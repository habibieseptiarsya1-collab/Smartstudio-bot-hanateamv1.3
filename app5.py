import streamlit as st
import sqlite3
import pandas as pd
import hashlib
import datetime
from datetime import timedelta
import time
import re
import os
import urllib.parse

# ==========================================
# 0. CONFIG & CSS
# ==========================================
st.set_page_config(page_title="SmartStudio Ultimate", layout="wide", page_icon="🎹")

# KONFIGURASI NOMOR ADMIN UNTUK LINK WA
ADMIN_WA = "628123456789" 

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stChatMessage { background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; }
    div[data-testid="stMetric"] { background-color: #1e293b; padding: 20px; border-radius: 10px; border-left: 4px solid #3b82f6; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
    h1, h2, h3 { color: #f8fafc !important; }
    .stButton button { background-color: #3b82f6; color: white; border-radius: 8px; font-weight: 600; }
    [data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
    
    /* Styling khusus tombol Approval */
    .element-container button:contains("✅ Terima") { background-color: #22c55e !important; border-color: #22c55e; }
    .element-container button:contains("❌ Tolak") { background-color: #ef4444 !important; border-color: #ef4444; }
</style>
""", unsafe_allow_html=True)

# Nama Database
DB_FILE = 'smartstudio_v18_integrated.db'

# ==========================================
# 1. DATABASE SYSTEM
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT, no_hp TEXT, date TEXT, 
        start_hour INTEGER, duration INTEGER, instruments TEXT, price REAL, status TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT UNIQUE)''')
    
    # Update: Status default Pending untuk approval system
    c.execute('''CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, student_name TEXT, instrument TEXT, 
        schedule_day TEXT, schedule_time TEXT, duration INTEGER, status TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, details TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    # Seed Admin
    try: c.execute("INSERT INTO users VALUES (?, ?)", ('admin', hashlib.sha256("Hanateam123".encode()).hexdigest()))
    except: pass

    # Seed Inventory
    c.execute("SELECT count(*) FROM inventory")
    if c.fetchone()[0] == 0:
        items = [('gitar elektrik',), ('bass',), ('drum set',), ('keyboard',), ('mic wireless',)]
        c.executemany("INSERT INTO inventory (item_name) VALUES (?)", items)
        conn.commit()
        
    conn.commit()
    return conn

def log_action(conn, action, details):
    wib = datetime.timezone(datetime.timedelta(hours=7))
    now_wib = datetime.datetime.now(wib).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO audit_logs (action, details, timestamp) VALUES (?, ?, ?)", 
                 (action, details, now_wib))
    conn.commit()

# ==========================================
# 2. LOGIC SYSTEM
# ==========================================
def calculate_price(start, duration):
    base = 50000
    peak_hours = {18, 19, 20, 21, 22}
    rental_hours = set(range(start, start + duration))
    is_peak = not rental_hours.isdisjoint(peak_hours)
    total = (base * duration) * (1.2 if is_peak else 1.0)
    return total, is_peak

def check_conflict(conn, date_str, start, duration, exclude_id=None, exclude_type='booking'):
    """
    Mengecek bentrok jadwal baik dengan Booking lain maupun Kursus.
    """
    c = conn.cursor()
    
    # 1. Cek Tabel Bookings
    query_bk = "SELECT id, start_hour, duration FROM bookings WHERE date = ?"
    params_bk = [date_str]
    if exclude_type == 'booking' and exclude_id:
        query_bk += " AND id != ?"
        params_bk.append(exclude_id)
        
    c.execute(query_bk, tuple(params_bk))
    for _, b_start, b_dur in c.fetchall():
        if (start < b_start + b_dur) and (start + duration > b_start): return True

    # 2. Cek Tabel Courses (Abaikan yang Rejected)
    # Asumsi: Kursus diinput per tanggal spesifik (untuk memudahkan bot sederhana)
    query_crs = "SELECT id, schedule_time, duration FROM courses WHERE schedule_day = ? AND status != 'Rejected'"
    params_crs = [date_str]
    if exclude_type == 'course' and exclude_id:
        query_crs += " AND id != ?"
        params_crs.append(exclude_id)

    c.execute(query_crs, tuple(params_crs))
    for _, c_time_str, c_dur in c.fetchall():
        try:
            # Handle format jam kursus (bisa string "16:00:00" atau int)
            c_start = int(str(c_time_str).split(':')[0])
            if (start < c_start + c_dur) and (start + duration > c_start): return True
        except: pass
        
    return False

def get_customer_stats(conn, no_hp):
    c = conn.cursor()
    try:
        c.execute("SELECT SUM(duration) FROM bookings WHERE no_hp = ?", (no_hp,))
        result = c.fetchone()[0]
        return result if result else 0
    except:
        return 0

def get_level_info(total_jam):
    if total_jam >= 50:
        return "🎸 Rockstar", "Diskon 15% booking selanjutnya!", 1.0, "gold"
    elif total_jam >= 20:
        return "🎹 Pro Musician", "Diskon 10% booking selanjutnya!", 0.7, "orange"
    elif total_jam >= 5:
        return "🥁 Garage Band", "Diskon 5% (Member setia)", 0.4, "blue"
    else:
        return "🎤 Newcomer", "Main 5 jam lagi untuk dapat diskon!", 0.1, "gray"

def parse_intent(user_input, inventory_list):
    txt = user_input.lower()
    res = {'intent': 'unknown', 'date': None, 'time': None, 'dur': None, 'found_items': []}
    
    # Intent Detection
    if 'batal' in txt or 'cancel' in txt or 'gak jadi' in txt: res['intent'] = 'cancel'
    elif 'ulang' in txt or 'reset' in txt or 'salah' in txt: res['intent'] = 'reset'
    elif 'reschedule' in txt or 'ganti' in txt or 'ubah' in txt: res['intent'] = 'reschedule'
    # Intent Baru: Kursus
    elif any(x in txt for x in ['kursus', 'les', 'sekolah', 'privat']): res['intent'] = 'course_register'
    elif any(x in txt for x in ['booking', 'sewa', 'pesan']): res['intent'] = 'booking'
    
    clean_txt = txt 
    wib = datetime.timezone(datetime.timedelta(hours=7))
    today = datetime.datetime.now(wib).date()

    # Cek Tanggal
    if 'hari ini' in txt: 
        res['date'] = today.strftime("%Y-%m-%d")
    elif 'besok' in txt: 
        res['date'] = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    elif 'lusa' in txt: 
        res['date'] = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    else:
        date_match = re.search(r'(tanggal|tgl)\s*(\d{1,2})', clean_txt)
        if date_match:
            try: 
                target_day = int(date_match.group(2))
                res['date'] = today.replace(day=target_day).strftime("%Y-%m-%d")
                clean_txt = clean_txt.replace(date_match.group(0), "")
            except: pass

    # Cek Durasi
    d_match = re.search(r'(\d+)\s*(jam|hour)', clean_txt)
    if d_match: 
        res['dur'] = int(d_match.group(1))
        clean_txt = clean_txt.replace(d_match.group(0), "")

    # Cek Jam
    time_match = re.search(r'(jam|pukul)?\s*(\d{1,2})[:.]?(\d{2})?\s*(pagi|siang|sore|malam)?', clean_txt)
    if time_match:
        h = int(time_match.group(2))
        modifier = time_match.group(4)
        if modifier:
            if modifier in ['sore', 'malam'] and h < 12: h += 12
            elif modifier == 'siang':
                if h < 11: h += 12
        if 8 <= h <= 23:
            res['time'] = h

    # Cek Inventory
    for item in inventory_list:
        if item in txt or (item.split()[0] in txt): 
             res['found_items'].append(item)
            
    return res

def finalize_booking(conn, bs):
    # Cek Validasi Final dengan sistem Conflict Baru
    conflict = check_conflict(conn, bs['date'], bs['time'], bs['dur'], exclude_type='booking')
    
    if conflict:
        msg = f"❌ Maaf Kak {bs['name']}, jam {bs['time']}:00 di tanggal {bs['date']} sudah penuh (ada Booking/Kursus)."
        return msg, False
    else:
        price, is_peak = calculate_price(bs['time'], bs['dur'])
        items_str = ", ".join(set(bs['items'])).title() if bs['items'] else "Standard Room"
        
        conn.execute('''INSERT INTO bookings (customer_name, no_hp, date, start_hour, duration, instruments, price, status) 
                        VALUES (?,?,?,?,?,?,?,?)''', 
                        (bs['name'], bs['phone'], bs['date'], bs['time'], bs['dur'], items_str, price, "Confirmed"))
        
        log_action(conn, "NEW_BOOKING", f"{bs['name']} ({bs['phone']}) - {bs['date']}")
        conn.commit()
        
        # --- GENERATE LINK WA ---
        wa_text = (
            f"*BOOKING CONFIRMED - SMART STUDIO*\n"
            f"--------------------------------\n"
            f"Nama: {bs['name']}\n"
            f"Tanggal: {bs['date']}\n"
            f"Jam: {bs['time']}:00 WIB\n"
            f"Durasi: {bs['dur']} Jam\n"
            f"Total: Rp {price:,.0f}\n"
            f"--------------------------------"
        )
        wa_encoded = urllib.parse.quote(wa_text)
        wa_link = f"https://wa.me/{ADMIN_WA}?text={wa_encoded}"

        ticket_html = f"""
<div style="font-family: 'Courier New', Courier, monospace; background-color: #fffcf5; color: #333; padding: 25px; max-width: 400px; margin: 10px auto; border: 2px solid #333; border-radius: 10px; box-shadow: 8px 8px 0px rgba(0,0,0,0.2); position: relative;">
<div style="text-align: center; border-bottom: 2px dashed #333; padding-bottom: 15px; margin-bottom: 15px;">
<p style="margin: 0; font-weight: 900; letter-spacing: 2px; color: #000000 !important;">🎹 SMART STUDIO</h2>
<p style="margin: 5px 0 0; font-size: 12px; color: #000000;">DIGITAL RECEIPT TICKET</p>
</div>
<div style="font-size: 14px; line-height: 1.6;">
<div style="display: flex; justify-content: space-between;"><span>👤 Nama:</span><strong>{bs['name']}</strong></div>
<div style="display: flex; justify-content: space-between;"><span>📅 Tgl:</span><strong>{bs['date']}</strong></div>
<div style="display: flex; justify-content: space-between;"><span>⏰ Jam:</span><strong>{bs['time']}:00 WIB</strong></div>
<div style="display: flex; justify-content: space-between;"><span>⏳ Durasi:</span><strong>{bs['dur']} Jam</strong></div>
<hr style="border: none; border-top: 1px dashed #bbb; margin: 10px 0;">
<div style="margin-bottom: 5px;"><span>🎸 Alat:</span><br><strong>{items_str}</strong></div>
</div>
<div style="margin-top: 20px; border-top: 2px solid #333; padding-top: 10px; text-align: right;">
<p style="margin: 0; font-size: 12px;">Total Paid</p>
<p style="margin: 0; font-size: 28px; color: #000000;">Rp {price:,.0f}</h1>
</div>
<div style="margin-top: 15px; text-align: center;">
    <a href="{wa_link}" target="_blank" style="display: block; width: 100%; background-color: #25D366; color: white; text-decoration: none; padding: 10px 0; border-radius: 5px; font-weight: bold; font-family: sans-serif;">
        📩 Kirim Tiket ke WhatsApp Admin
    </a>
</div>
</div>
"""
        msg = ticket_html
        return msg, True

def finalize_course_registration(conn, bs):
    # Cek Conflict Kursus
    conflict = check_conflict(conn, bs['course_date'], bs['course_time'], 1, exclude_type='course')
    if conflict:
        return f"❌ Maaf, jadwal {bs['course_date']} jam {bs['course_time']}:00 sudah terisi.", False
    
    # Insert dengan Status PENDING (Menunggu Approval Admin)
    conn.execute("INSERT INTO courses (student_name, instrument, schedule_day, schedule_time, duration, status) VALUES (?,?,?,?,?,?)", 
                 (bs['name'], bs['course_instrument'], bs['course_date'], str(bs['course_time']), 1, "Pending"))
    log_action(conn, "NEW_COURSE_REQ", f"{bs['name']} - {bs['course_instrument']}")
    conn.commit()

    wa_text = f"DAFTAR KURSUS\nNama: {bs['name']}\nInstrumen: {bs['course_instrument']}\nTgl Mulai: {bs['course_date']}\nJam: {bs['course_time']}:00\nMohon Approval."
    wa_link = f"https://wa.me/{ADMIN_WA}?text={urllib.parse.quote(wa_text)}"

    ticket_html = f"""
    <div style='background:#f0fdf4; padding:15px; border-radius:10px; border:2px solid #166534; color:#000; font-family: monospace;'>
    <b>🎓 PENDAFTARAN DITERIMA</b><br><br>
    👤 {bs['name']}<br>
    🎸 Kelas: {bs['course_instrument']}<br>
    📅 {bs['course_date']} | ⏰ {bs['course_time']}:00<br>
    <hr style='border-top: 1px dashed #166534;'>
    <i>Status: ⏳ <b>PENDING</b> (Menunggu Admin)</i><br>
    <a href="{wa_link}" target="_blank" style="background:#166534; color:white; padding:8px; border-radius:5px; text-decoration:none; display:block; text-align:center; margin-top:10px; font-weight:bold;">
    📩 Konfirmasi ke Admin (WA)</a>
    </div>"""
    return ticket_html, True

def process_reschedule(conn, type_res, target_id, new_date, new_time):
    c = conn.cursor()
    
    if type_res == 'booking':
        c.execute("SELECT customer_name, duration FROM bookings WHERE id=?", (target_id,))
        exclude_type = 'booking'
    else: # course
        c.execute("SELECT student_name, duration FROM courses WHERE id=?", (target_id,))
        exclude_type = 'course'
        
    row = c.fetchone()
    if not row: return "❌ Data tidak ditemukan.", False
    
    name, duration = row
    
    # Cek Conflict (exclude ID sendiri)
    if check_conflict(conn, new_date, new_time, duration, exclude_id=target_id, exclude_type=exclude_type):
        return f"❌ Gagal. Jam {new_time}:00 di tanggal {new_date} bentrok.", False
    
    if type_res == 'booking':
        new_price, _ = calculate_price(new_time, duration)
        conn.execute("UPDATE bookings SET date=?, start_hour=?, price=? WHERE id=?", (new_date, new_time, new_price, target_id))
    else:
        conn.execute("UPDATE courses SET schedule_day=?, schedule_time=? WHERE id=?", (new_date, str(new_time), target_id))
        
    log_action(conn, f"RESCHEDULE_{type_res.upper()}", f"ID {target_id} moved to {new_date}")
    conn.commit()
    
    return f"✅ **Reschedule Berhasil!** Jadwal baru Kak **{name}**: {new_date} jam {new_time}:00.", True

# ==========================================
# 3. UI LAYER
# ==========================================
def main():
    conn = init_db()
    
    # --- Sidebar ---
    st.sidebar.title("🎹 SmartStudio Bot")
    st.sidebar.caption("By Hanateam")
    
    # --- STATUS MEMBER ---
    st.sidebar.markdown("---")
    st.sidebar.header("🏆 Status Member Kamu")
    st.sidebar.write("Masukkan No HP untuk cek level & diskon!")
    
    cek_hp = st.sidebar.text_input("No. WhatsApp:", placeholder="0812xxx")
    
    if cek_hp:
        jam_terbang = get_customer_stats(conn, cek_hp)
        level_name, benefit, progress, lvl_color = get_level_info(jam_terbang)
        st.sidebar.info(f"**Level: {level_name}**")
        st.sidebar.metric("Jam Terbang", f"{jam_terbang} Jam")
        st.sidebar.progress(progress)
        st.sidebar.success(f"🎁 {benefit}")
    else:
        st.sidebar.caption("Data level bersifat personal. Masukkan nomor HP untuk melihat progress Anda.")
    
    st.sidebar.markdown("---")
    
    if "admin_logged_in" not in st.session_state: st.session_state.admin_logged_in = False
    
    if "chat_history" not in st.session_state: st.session_state.chat_history = []
    if "bot_state" not in st.session_state: 
        st.session_state.bot_state = {
            "mode": "idle", "step": 0, 
            "name": None, "phone": None, 
            "date": None, "time": None, "dur": None, 
            "items": [], "target_id": None, "res_type": None,
            "course_instrument": None, "course_date": None, "course_time": None
        }

    # Admin Auth
    with st.sidebar.expander("🔐 Admin Area (Klik untuk buka)", expanded=False):
        if not st.session_state.admin_logged_in:
            pwd = st.text_input("Password Admin", type="password")
            if st.button("Login"):
                if hashlib.sha256(pwd.encode()).hexdigest() == hashlib.sha256("Hanateam123".encode()).hexdigest():
                    st.session_state.admin_logged_in = True; st.rerun()
                else: st.error("Salah password")
        else:
            if st.button("Logout"): st.session_state.admin_logged_in = False; st.rerun()

    # ==========================================
    # VIEW A: ADMIN DASHBOARD
    # ==========================================
    if st.session_state.admin_logged_in:
        st.title("🎛️ Studio Command Center")
        
        # --- FITUR BACKUP & RESTORE KEMBALI ---
        with st.expander("💾 Database Backup & Restore", expanded=True):
            st.info("Gunakan fitur ini untuk menyimpan data agar tidak hilang saat server Cloud restart.")
            c_bk1, c_bk2 = st.columns(2)
            with c_bk1:
                conn.commit()
                if os.path.exists(DB_FILE):
                    with open(DB_FILE, "rb") as f:
                        bytes_data = f.read()
                        st.download_button("⬇️ Download Full Backup (.db)", bytes_data, f"smartstudio_backup.db")
            with c_bk2:
                uploaded_db = st.file_uploader("⬆️ Restore Backup (Upload .db)", type="db")
                if uploaded_db and st.button("⚠️ Timpa Database & Restore"):
                    conn.close()
                    try:
                        with open(DB_FILE, "wb") as f: f.write(uploaded_db.getbuffer())
                        st.success("Restore Berhasil! Restarting..."); time.sleep(3); st.rerun()
                    except: st.error("Gagal restore")
                            
        with st.expander("💀 DANGER ZONE", expanded=False):
            if st.checkbox("Saya yakin ingin menghapus seluruh database") and st.button("💣 Hapus Total"):
                conn.close()
                if os.path.exists(DB_FILE): os.remove(DB_FILE)
                st.success("Database dihapus. Restarting..."); time.sleep(3); st.rerun()

        df_bk = pd.read_sql("SELECT * FROM bookings", conn)
        df_crs = pd.read_sql("SELECT * FROM courses", conn)
        
        # --- STATISTIK KEMBALI ---
        c1, c2, c3 = st.columns(3)
        c1.metric("Revenue", f"Rp {df_bk['price'].sum() if not df_bk.empty else 0:,.0f}")
        c2.metric("Bookings", f"{len(df_bk)}")
        c3.metric("Students", f"{len(df_crs)}")
        
        if not df_bk.empty:
            st.markdown("### 📊 Statistik")
            chart_data = df_bk.groupby('date')['price'].sum().reset_index()
            st.bar_chart(chart_data, x='date', y='price', color='#3b82f6')

        t1, t2, t3, t4 = st.tabs(["📅 Bookings", "🛠️ Inventory", "🎓 Courses (Approval)", "🛡️ Logs"])
        
        with t1:
            st.dataframe(df_bk, use_container_width=True, hide_index=True)
            if not df_bk.empty:
                del_ops = df_bk.apply(lambda x: f"{x['id']} - {x['customer_name']} ({x['date']})", axis=1)
                sel_del = st.selectbox("Hapus Booking", del_ops)
                if st.button("❌ Hapus Permanen"):
                    conn.execute("DELETE FROM bookings WHERE id=?", (int(sel_del.split(' - ')[0]),))
                    conn.commit(); st.success("Dihapus"); st.rerun()

            st.markdown("---")
            if not df_bk.empty:
                c_r1, c_r2, c_r3, c_r4 = st.columns(4)
                with c_r1: tid = st.selectbox("ID Booking", df_bk['id'])
                with c_r2: ndate = st.date_input("Tanggal Baru")
                with c_r3: ntime = st.number_input("Jam Baru", 8, 23, 17)
                with c_r4: 
                    st.write("")
                    if st.button("Pindah Jadwal"):
                        m, s = process_reschedule(conn, 'booking', tid, str(ndate), int(ntime))
                        if s: st.success(m); time.sleep(1); st.rerun()
                        else: st.error(m)

        with t2:
            c_a, c_b = st.columns([2, 1])
            with c_a: st.dataframe(pd.read_sql("SELECT * FROM inventory", conn), use_container_width=True)
            with c_b: 
                with st.form("add_inv"):
                    new_item = st.text_input("Tambah Alat Baru")
                    if st.form_submit_button("Simpan"):
                        try:
                            conn.execute("INSERT INTO inventory (item_name) VALUES (?)", (new_item.lower(),))
                            conn.commit(); st.rerun()
                        except: st.error("Item sudah ada!")

        with t3:
            # === APPROVAL SYSTEM ===
            st.info("ℹ️ Input manual siswa telah dinonaktifkan. Silakan gunakan Chatbot untuk pendaftaran, lalu Approve di sini.")
            
            col_pending, col_active = st.columns(2)
            
            with col_pending:
                st.markdown("### ⏳ Approval Antrian (Pending)")
                df_pending = pd.read_sql("SELECT * FROM courses WHERE status='Pending'", conn)
                
                if df_pending.empty:
                    st.write("Belum ada pendaftaran baru.")
                else:
                    for idx, row in df_pending.iterrows():
                        with st.container(border=True):
                            st.subheader(f"{row['student_name']}")
                            st.text(f"Kelas: {row['instrument']}")
                            st.text(f"Jadwal: {row['schedule_day']} @ {row['schedule_time']}:00")
                            
                            b1, b2 = st.columns(2)
                            if b1.button("✅ Terima", key=f"acc_{row['id']}"):
                                conn.execute("UPDATE courses SET status='Active' WHERE id=?", (row['id'],))
                                log_action(conn, "COURSE_APPROVED", f"Approved {row['student_name']}")
                                conn.commit(); st.rerun()
                            
                            if b2.button("❌ Tolak", key=f"rej_{row['id']}"):
                                conn.execute("UPDATE courses SET status='Rejected' WHERE id=?", (row['id'],))
                                log_action(conn, "COURSE_REJECTED", f"Rejected {row['student_name']}")
                                conn.commit(); st.rerun()

            with col_active:
                st.markdown("### ✅ Siswa Aktif")
                st.dataframe(pd.read_sql("SELECT id, student_name, instrument, schedule_day, schedule_time FROM courses WHERE status='Active'", conn), use_container_width=True)
                
                st.markdown("### 🚫 History Rejected")
                st.dataframe(pd.read_sql("SELECT id, student_name, instrument FROM courses WHERE status='Rejected'", conn), use_container_width=True)

        with t4: st.dataframe(pd.read_sql("SELECT * FROM audit_logs ORDER BY id DESC", conn), use_container_width=True)

    # ==========================================
    # VIEW B: CHATBOT (USER)
    # ==========================================
    else:
        st.title("🤖 Assistant Studio")
        
        # --- FITUR HEATMAP KETERSEDIAAN KEMBALI ---
        with st.expander("📊 Cek Ketersediaan & Jam Rame (Klik di sini)", expanded=False):
            col_date, col_ket = st.columns([1, 2])
            with col_date: tgl_pilih = st.date_input("Pilih Tanggal:", datetime.date.today())
            with col_ket: st.write(""); st.caption(f"Menampilkan kepadatan: **{tgl_pilih.strftime('%d %B %Y')}**")

            # Ambil data Booking DAN Kursus untuk visualisasi
            bookings_today = conn.execute("SELECT start_hour, duration FROM bookings WHERE date = ?", (str(tgl_pilih),)).fetchall()
            courses_today = conn.execute("SELECT schedule_time, duration FROM courses WHERE schedule_day = ? AND status='Active'", (str(tgl_pilih),)).fetchall()
            
            hours_map = {h: 0 for h in range(8, 24)}
            
            # Petakan Booking
            for start, dur in bookings_today:
                for h in range(start, start + dur):
                    if h in hours_map: hours_map[h] += 1
            
            # Petakan Kursus
            for start_str, dur in courses_today:
                try:
                    start = int(str(start_str).split(':')[0])
                    for h in range(start, start + dur):
                        if h in hours_map: hours_map[h] += 1
                except: pass
            
            df_heat = pd.DataFrame({"Jam": [f"{h}:00" for h in hours_map], "Value": list(hours_map.values())})
            st.bar_chart(df_heat.set_index("Jam")['Value'], color="#F63366")
            
            jam_penuh = [k for k, v in hours_map.items() if v > 0]
            if jam_penuh: st.warning(f"Jam terisi: {', '.join([str(x)+':00' for x in jam_penuh])}")
            else: st.success("Jadwal kosong melompong!")

        with st.expander("ℹ️  Panduan / Cara Pakai", expanded=True):
            st.markdown("""
            **1. Mau Sewa Studio?** Ketik: *"Booking"* atau *"Booking besok jam 2 siang"*
            **2. Mau Daftar Les?** Ketik: *"Daftar Kursus"* atau *"Les Gitar"*
            **3. Mau Ganti Jadwal?** Ketik: *"Reschedule"*
            """)
        
        if not st.session_state.chat_history:
            st.session_state.chat_history.append(("assistant", "Halo! 👋 Ketik **'Booking'** untuk sewa atau **'Kursus'** untuk daftar les."))

        inv_rows = conn.execute("SELECT item_name FROM inventory").fetchall()
        inv_list = [x[0] for x in inv_rows]
        
        for role, txt in st.session_state.chat_history:
            with st.chat_message(role): 
                if "<div" in txt: st.markdown(txt, unsafe_allow_html=True)
                else: st.markdown(txt)
            
        if prompt := st.chat_input("Ketik pesan Anda..."):
            st.session_state.chat_history.append(("user", prompt))
            with st.chat_message("user"): st.markdown(prompt)

            res = parse_intent(prompt, inv_list)
            bs = st.session_state.bot_state
            
            # --- FIX: INITIALIZE REPLY TO AVOID CRASH ---
            reply = "Maaf, saya tidak mengerti maksud Anda. Coba ketik 'Booking' atau 'Kursus'."

            # --- GLOBAL CANCEL / RESET ---
            if res['intent'] == 'cancel':
                reply = "⚠️ **Pembatalan?** Hubungi Admin WA jika darurat."
                st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'; bs['items']=[]
            
            elif res['intent'] == 'reset':
                reply = "🔄 Oke, diulang. Silakan ketik perintah lagi."
                st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'; bs['items']=[]
            
            # --- FLOW 1: BOOKING STUDIO ---
            elif res['intent'] == 'booking' or bs['mode'] == 'booking':
                bs['mode'] = 'booking'
                
                # Update State Paramaters
                if res['date']: bs['date'] = res['date']
                if bs['step'] != 'ASK_PHONE': # Jangan update jam jika sedang input HP
                    if res['time']: bs['time'] = res['time']
                if res['dur']: bs['dur'] = res['dur']
                if res['found_items']: bs['items'].extend(res['found_items'])

                # Step-by-Step Logic
                if not bs['date']:
                    bs['step'] = 'ASK_DATE'; reply = f"📅 Siap Booking. Tanggal berapa? (Misal: Besok / Tgl 25)"
                elif not bs['time']:
                    bs['step'] = 'ASK_TIME'; reply = f"⏰ Oke tanggal {bs['date']}. Jam berapa mulainya?"
                elif not bs['dur']:
                    bs['step'] = 'ASK_DUR'; reply = "⏳ Mau sewa berapa jam?"
                elif not bs['items'] and bs['step'] == 'ASK_DUR': # Optional items check
                     bs['step'] = 'ASK_ITEMS'; reply = "🎸 Butuh tambahan alat? (Ketik nama alat atau 'Standar')"
                elif not bs['name']:
                    bs['step'] = 'ASK_NAME'; reply = "👤 Atas nama siapa?"
                elif not bs['phone']:
                    bs['name'] = prompt.title() # Capture name from previous input
                    bs['step'] = 'ASK_PHONE'; reply = "📱 Nomor WA? (Untuk tiket & level)"
                else:
                    bs['phone'] = prompt # Capture phone
                    msg, _ = finalize_booking(conn, bs)
                    reply = msg
                    st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'; bs['items']=[]

            # --- FLOW 2: DAFTAR KURSUS (PENGGANTI INPUT MANUAL ADMIN) ---
            elif res['intent'] == 'course_register' or bs['mode'] == 'course_register':
                bs['mode'] = 'course_register'
                
                if bs['step'] == 0:
                    bs['step'] = 'C_NAME'; reply = "🎓 **Pendaftaran Kursus Baru**\nSiapa nama calon siswanya?"
                elif bs['step'] == 'C_NAME':
                    bs['name'] = prompt.title(); bs['step'] = 'C_INS'; reply = "🎸 Halo! Mau ambil kelas instrumen apa? (Gitar/Piano/Drum/Vokal)"
                elif bs['step'] == 'C_INS':
                    bs['course_instrument'] = prompt.title(); bs['step'] = 'C_DATE'; reply = "📅 Mau mulai tanggal berapa?"
                elif bs['step'] == 'C_DATE':
                    if res['date']:
                        bs['course_date'] = res['date']; bs['step'] = 'C_TIME'; reply = f"⏰ Tanggal {bs['course_date']}. Jam berapa bisanya?"
                    else: reply = "Mohon sebutkan tanggal yang jelas (Contoh: Besok / Tgl 25)."
                elif bs['step'] == 'C_TIME':
                    if res['time']:
                        bs['course_time'] = res['time']
                        msg, stat = finalize_course_registration(conn, bs)
                        reply = msg
                        st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'; bs['items']=[]
                    else: reply = "Jam berapa? (Masukkan angka, misal 16)"

            # --- FLOW 3: RESCHEDULE ---
            elif res['intent'] == 'reschedule' or bs['mode'] == 'reschedule':
                bs['mode'] = 'reschedule'
                if bs['step'] == 0:
                    bs['step'] = 'RES_TYPE'; reply = "🔄 **Reschedule Jadwal**\nMau ganti jadwal **Booking** atau **Kursus**?"
                elif bs['step'] == 'RES_TYPE':
                    if 'kursus' in prompt.lower(): bs['res_type'] = 'course'
                    else: bs['res_type'] = 'booking'
                    bs['step'] = 'RES_NAME'; reply = f"👤 Oke, ganti jadwal {bs['res_type']}. Atas nama siapa?"
                elif bs['step'] == 'RES_NAME':
                    table = "bookings" if bs['res_type'] == 'booking' else "courses"
                    col = "customer_name" if bs['res_type'] == 'booking' else "student_name"
                    row = conn.execute(f"SELECT id FROM {table} WHERE {col} LIKE ? ORDER BY id DESC", (f"%{prompt}%",)).fetchone()
                    if row:
                        bs['target_id'] = row[0]; bs['step'] = 'RES_NEW_DATE'; reply = "📅 Data ditemukan. Mau pindah ke tanggal berapa?"
                    else: reply = "❌ Nama tidak ditemukan. Coba lagi?"
                elif bs['step'] == 'RES_NEW_DATE':
                    if res['date']:
                        bs['date'] = res['date']; bs['step'] = 'RES_NEW_TIME'; reply = "⏰ Jam berapa?"
                    else: reply = "Tanggal berapa? (Contoh: Besok)"
                elif bs['step'] == 'RES_NEW_TIME':
                    if res['time']:
                        msg, _ = process_reschedule(conn, bs['res_type'], bs['target_id'], bs['date'], res['time'])
                        reply = msg
                        st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'; bs['items']=[]
                    else: reply = "Jam berapa? (Angka 0-23)"

            else:
                reply = "Halo! Ketik **'Booking'** untuk sewa, **'Kursus'** untuk daftar les, atau **'Reschedule'**."
            
            time.sleep(0.5)
            st.session_state.chat_history.append(("assistant", reply))
            with st.chat_message("assistant"): 
                if "<div" in reply:
                    st.markdown(reply, unsafe_allow_html=True)
                else:
                    st.markdown(reply)
            st.rerun()

if __name__ == "__main__":
    main()
