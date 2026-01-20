import streamlit as st
import sqlite3
import pandas as pd
import hashlib
import datetime
from datetime import timedelta
import time
import re
import os
import urllib.parse  # Tambahan library untuk Link WhatsApp

# ==========================================
# 0. CONFIG & CSS
# ==========================================
st.set_page_config(page_title="SmartStudio Ultimate", layout="wide", page_icon="🎹")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stChatMessage { background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; }
    div[data-testid="stMetric"] { background-color: #1e293b; padding: 20px; border-radius: 10px; border-left: 4px solid #3b82f6; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
    h1, h2, h3 { color: #f8fafc !important; }
    .stButton button { background-color: #3b82f6; color: white; border-radius: 8px; font-weight: 600; }
    [data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

# Nama Database
DB_FILE = 'smartstudio_v17.db'

# ==========================================
# 1. DATABASE SYSTEM
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Tables
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT)''')
    
    # Tabel Bookings
    c.execute('''CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT, no_hp TEXT, date TEXT, 
        start_hour INTEGER, duration INTEGER, instruments TEXT, price REAL, status TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT UNIQUE)''')
    
    # Tabel Courses (Tetap sama, tidak diubah strukturnya agar database lama aman)
    c.execute('''CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, student_name TEXT, instrument TEXT, 
        schedule_day TEXT, schedule_time TEXT, duration INTEGER, status TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, details TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    # Seed Admin (Pass: Hanateam123)
    try: c.execute("INSERT INTO users VALUES (?, ?)", ('admin', hashlib.sha256("Hanateam123".encode()).hexdigest()))
    except: pass

    # Seed Inventory Default
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

def check_conflict(conn, date_str, start, duration, exclude_id=None):
    c = conn.cursor()
    if exclude_id:
        c.execute("SELECT id, start_hour, duration FROM bookings WHERE date = ? AND id != ?", (date_str, exclude_id))
    else:
        c.execute("SELECT id, start_hour, duration FROM bookings WHERE date = ?", (date_str,))
    
    for _, b_start, b_dur in c.fetchall():
        if (start < b_start + b_dur) and (start + duration > b_start): return True
    return False

def get_customer_stats(conn, no_hp):
    """Menghitung total jam terbang berdasarkan Nomor HP"""
    c = conn.cursor()
    try:
        c.execute("SELECT SUM(duration) FROM bookings WHERE no_hp = ?", (no_hp,))
        result = c.fetchone()[0]
        return result if result else 0
    except:
        return 0

def get_level_info(total_jam):
    """Menentukan Level dan Diskon berdasarkan Total Jam"""
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
    
    # 1. Cek Intent Dasar
    if 'batal' in txt or 'cancel' in txt or 'gak jadi' in txt: res['intent'] = 'cancel'
    elif 'ulang' in txt or 'reset' in txt or 'salah' in txt: res['intent'] = 'reset'
    elif 'reschedule' in txt or 'ganti' in txt or 'ubah' in txt: res['intent'] = 'reschedule'
    # --- [BARU] Intent Kursus ---
    elif any(x in txt for x in ['kursus', 'les', 'sekolah', 'privat', 'belajar']): res['intent'] = 'course_register'
    elif any(x in txt for x in ['booking', 'sewa', 'pesan']): res['intent'] = 'booking'
    
    clean_txt = txt 
    wib = datetime.timezone(datetime.timedelta(hours=7))
    today = datetime.datetime.now(wib).date()

    # 2. Cek Tanggal
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

    # 3. Cek Durasi
    d_match = re.search(r'(\d+)\s*(jam|hour)', clean_txt)
    if d_match: 
        res['dur'] = int(d_match.group(1))
        clean_txt = clean_txt.replace(d_match.group(0), "")

    # 4. Cek Jam (Logika Siang/Sore/Malam)
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

    # 5. Cek Inventory
    for item in inventory_list:
        if item in txt or (item.split()[0] in txt): 
             res['found_items'].append(item)
            
    return res

def finalize_booking(conn, bs):
    # Cek Validasi Final
    conflict = check_conflict(conn, bs['date'], bs['time'], bs['dur'])
    
    if conflict:
        msg = f"❌ Maaf Kak {bs['name']}, jam {bs['time']}:00 di tanggal {bs['date']} sudah penuh."
        return msg, False
    else:
        price, is_peak = calculate_price(bs['time'], bs['dur'])
        items_str = ", ".join(set(bs['items'])).title() if bs['items'] else "Standard Room"
        
        conn.execute('''INSERT INTO bookings (customer_name, no_hp, date, start_hour, duration, instruments, price, status) 
                        VALUES (?,?,?,?,?,?,?,?)''', 
                        (bs['name'], bs['phone'], bs['date'], bs['time'], bs['dur'], items_str, price, "Confirmed"))
        
        log_action(conn, "NEW_BOOKING", f"{bs['name']} ({bs['phone']}) - {bs['date']}")
        conn.commit()
        
        # --- LOGIKA LINK WHATSAPP ---
        wa_text = (
            f"*BOOKING CONFIRMED - SMART STUDIO*\n"
            f"--------------------------------\n"
            f"Nama: {bs['name']}\n"
            f"Tanggal: {bs['date']}\n"
            f"Jam: {bs['time']}:00 WIB\n"
            f"Durasi: {bs['dur']} Jam\n"
            f"Alat: {items_str}\n"
            f"Total: Rp {price:,.0f}\n"
            f"--------------------------------\n"
            f"Terima kasih sudah booking!"
        )
        wa_encoded = urllib.parse.quote(wa_text)
        hp_fmt = bs['phone']
        if hp_fmt.startswith("0"): hp_fmt = "62" + hp_fmt[1:]
        wa_link = f"https://wa.me/{hp_fmt}?text={wa_encoded}"

        # --- TIKET HTML ---
        ticket_html = f"""
<div style="font-family: 'Courier New', Courier, monospace; background-color: #fffcf5; color: #333; padding: 25px; max-width: 400px; margin: 10px auto; border: 2px solid #333; border-radius: 10px; box-shadow: 8px 8px 0px rgba(0,0,0,0.2); position: relative;">
<div style="text-align: center; border-bottom: 2px dashed #333; padding-bottom: 15px; margin-bottom: 15px;">
<p style="margin: 0; font-weight: 900; letter-spacing: 2px; color: #000000 !important;">🎹 SMART STUDIO</h2>
<p style="margin: 5px 0 0; font-size: 12px; color: #000000;">BOOKING RECEIPT</p>
<p style="margin: 0; font-size: 10px; color: #777;">ID: #{int(time.time())}</p>
</div>

<div style="font-size: 14px; line-height: 1.6;">
<div style="display: flex; justify-content: space-between;"><span>👤 Nama:</span><strong>{bs['name']}</strong></div>
<div style="display: flex; justify-content: space-between;"><span>📱 No HP:</span><strong>{bs['phone']}</strong></div>
<div style="display: flex; justify-content: space-between;"><span>📅 Tgl:</span><strong>{bs['date']}</strong></div>
<div style="display: flex; justify-content: space-between;"><span>⏰ Jam:</span><strong>{bs['time']}:00 WIB</strong></div>
<div style="display: flex; justify-content: space-between;"><span>⏳ Durasi:</span><strong>{bs['dur']} Jam</strong></div>
<hr style="border: none; border-top: 1px dashed #bbb; margin: 10px 0;">
<div style="margin-bottom: 5px;">
<span>🎸 Alat:</span><br>
<span style="display: inline-block; background: #eee; padding: 2px 6px; border-radius: 4px; font-size: 12px;">{items_str}</span>
</div>
</div>

<div style="margin-top: 20px; border-top: 2px solid #333; padding-top: 10px; text-align: right;">
<p style="margin: 0; font-size: 12px;">Total Paid</p>
<p style="margin: 0; font-size: 28px; color: #000000;">Rp {price:,.0f}</h1>
</div>

<div style="margin-top: 15px; text-align: center;">
    <a href="{wa_link}" target="_blank" style="display: block; width: 100%; background-color: #25D366; color: white; text-decoration: none; padding: 10px 0; border-radius: 5px; font-weight: bold; font-family: sans-serif;">
        📩 Kirim Tiket ke WhatsApp
    </a>
</div>
</div>
"""
        msg = ticket_html
        return msg, True

# --- [BARU] FUNGSI FINALISASI KURSUS ---
def finalize_course_registration(conn, bs):
    # Insert ke DB
    conn.execute("INSERT INTO courses (student_name, instrument, schedule_day, schedule_time, duration, status) VALUES (?,?,?,?,?,?)", 
                 (bs['name'], bs['course_instrument'], bs['course_day'], bs['course_time'], 1, "Pending"))
    log_action(conn, "NEW_COURSE", f"{bs['name']} - {bs['course_instrument']}")
    conn.commit()

    # Link WA untuk Kursus
    wa_text = (
        f"*PENDAFTARAN KURSUS BARU*\n"
        f"--------------------------------\n"
        f"Nama Siswa: {bs['name']}\n"
        f"Instrumen: {bs['course_instrument']}\n"
        f"Jadwal: {bs['course_day']}, Jam {bs['course_time']}\n"
        f"--------------------------------\n"
        f"Mohon konfirmasi pendaftaran saya."
    )
    wa_encoded = urllib.parse.quote(wa_text)
    wa_link = f"https://wa.me/6281234567890?text={wa_encoded}" # Ganti No HP Admin

    ticket_html = f"""
<div style="font-family: 'Courier New', Courier, monospace; background-color: #f0fdf4; color: #333; padding: 25px; max-width: 400px; margin: 10px auto; border: 2px solid #166534; border-radius: 10px; box-shadow: 8px 8px 0px rgba(0,0,0,0.1); position: relative;">
<div style="text-align: center; border-bottom: 2px dashed #166534; padding-bottom: 15px; margin-bottom: 15px;">
<p style="margin: 0; font-weight: 900; letter-spacing: 2px; color: #166534 !important;">🎓 SMART COURSE</h2>
<p style="margin: 5px 0 0; font-size: 12px; color: #000000;">REGISTRATION TICKET</p>
</div>

<div style="font-size: 14px; line-height: 1.6;">
<div style="display: flex; justify-content: space-between;"><span>👤 Siswa:</span><strong>{bs['name']}</strong></div>
<div style="display: flex; justify-content: space-between;"><span>🎸 Kelas:</span><strong>{bs['course_instrument']}</strong></div>
<div style="display: flex; justify-content: space-between;"><span>📅 Hari:</span><strong>{bs['course_day']}</strong></div>
<div style="display: flex; justify-content: space-between;"><span>⏰ Jam:</span><strong>{bs['course_time']}</strong></div>
<div style="margin-top: 10px; background: #dcfce7; padding: 5px; border-radius: 4px; text-align: center; font-size: 12px; color: #14532d;">
Status: <strong>Menunggu Konfirmasi Admin</strong>
</div>
</div>

<div style="margin-top: 15px; text-align: center;">
    <a href="{wa_link}" target="_blank" style="display: block; width: 100%; background-color: #166534; color: white; text-decoration: none; padding: 10px 0; border-radius: 5px; font-weight: bold; font-family: sans-serif;">
        📩 Kirim ke Admin (WA)
    </a>
</div>
</div>
"""
    return ticket_html

def process_reschedule(conn, booking_id, new_date, new_time):
    c = conn.cursor()
    c.execute("SELECT customer_name, duration FROM bookings WHERE id=?", (booking_id,))
    row = c.fetchone()
    if not row: return "❌ Data tidak ditemukan.", False
    
    name, duration = row
    if check_conflict(conn, new_date, new_time, duration, exclude_id=booking_id):
        return f"❌ Gagal. Jam {new_time}:00 di tanggal {new_date} bentrok.", False
    
    new_price, _ = calculate_price(new_time, duration)
    conn.execute("UPDATE bookings SET date=?, start_hour=?, price=? WHERE id=?", (new_date, new_time, new_price, booking_id))
    log_action(conn, "RESCHEDULE", f"ID {booking_id} moved to {new_date}")
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
            "items": [], "target_id": None,
            # [BARU] State untuk Kursus
            "course_instrument": None, "course_day": None, "course_time": None
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
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Revenue", f"Rp {df_bk['price'].sum() if not df_bk.empty else 0:,.0f}")
        c2.metric("Bookings", f"{len(df_bk)}")
        c3.metric("Students", f"{len(df_crs)}")
        
        if not df_bk.empty:
            st.markdown("### 📊 Statistik")
            chart_data = df_bk.groupby('date')['price'].sum().reset_index()
            st.bar_chart(chart_data, x='date', y='price', color='#3b82f6')

        t1, t2, t3, t4 = st.tabs(["📅 Bookings", "🛠️ Inventory", "🎓 Courses", "🛡️ Logs"])
        
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
                        m, s = process_reschedule(conn, tid, str(ndate), int(ntime))
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
            st.dataframe(df_crs, use_container_width=True)
            if not df_crs.empty:
                sel_c_del = st.selectbox("Hapus Siswa", df_crs.apply(lambda x: f"{x['id']} - {x['student_name']}", axis=1))
                if st.button("❌ Hapus Siswa"):
                    conn.execute("DELETE FROM courses WHERE id=?", (int(sel_c_del.split(' - ')[0]),))
                    conn.commit(); st.rerun()
            
            st.markdown("---")
            with st.form("new_student"):
                c_s1, c_s2 = st.columns(2)
                with c_s1: 
                    n = st.text_input("Nama Siswa")
                    i = st.selectbox("Alat", ["Gitar", "Piano", "Drum", "Vokal", "Bass"])
                    day = st.selectbox("Hari", ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"])
                with c_s2: 
                    t_val = st.time_input("Jam Mulai", datetime.time(16, 0))
                    dur = st.number_input("Durasi", min_value=1, value=1)
                
                if st.form_submit_button("Daftar"):
                    conn.execute("INSERT INTO courses (student_name, instrument, schedule_day, schedule_time, duration, status) VALUES (?,?,?,?,?,?)", (n, i, day, str(t_val), dur, "Active"))
                    conn.commit(); st.rerun()

        with t4: st.dataframe(pd.read_sql("SELECT * FROM audit_logs ORDER BY id DESC", conn), use_container_width=True)

    # ==========================================
    # VIEW B: CHATBOT (USER)
    # ==========================================
    else:
        st.title("🤖 Assistant Studio")
        
        with st.expander("📊 Cek Ketersediaan & Jam Rame (Klik di sini)", expanded=False):
            col_date, col_ket = st.columns([1, 2])
            with col_date: tgl_pilih = st.date_input("Pilih Tanggal:", datetime.date.today())
            with col_ket: st.write(""); st.caption(f"Menampilkan kepadatan: **{tgl_pilih.strftime('%d %B %Y')}**")

            bookings_today = conn.execute("SELECT start_hour, duration FROM bookings WHERE date = ?", (str(tgl_pilih),)).fetchall()
            hours_map = {h: 0 for h in range(8, 24)}
            for start, dur in bookings_today:
                for h in range(start, start + dur):
                    if h in hours_map: hours_map[h] += 1
            
            df_heat = pd.DataFrame({"Jam": [f"{h}:00" for h in hours_map], "Value": list(hours_map.values())})
            st.bar_chart(df_heat.set_index("Jam")['Value'], color="#F63366")
            
            jam_penuh = [k for k, v in hours_map.items() if v > 0]
            if jam_penuh: st.warning(f"Jam terisi: {', '.join([str(x)+':00' for x in jam_penuh])}")
            else: st.success("Jadwal kosong melompong!")

        with st.expander("ℹ️  Panduan / Cara Pakai", expanded=True):
            st.markdown("""
            **1. Mau Booking?** Ketik: *"Booking"* atau *"Booking besok jam 2 siang"*
            **2. Mau Kursus?** Ketik: *"Daftar Kursus"* atau *"Mau Les"*
            **3. Mau Ganti Jadwal?** Ketik: *"Reschedule"*
            """)
        
        if not st.session_state.chat_history:
            st.session_state.chat_history.append(("assistant", "Halo! 👋 Ketik **'Booking'** atau **'Daftar Kursus'** untuk mulai."))

        inv_rows = conn.execute("SELECT item_name FROM inventory").fetchall()
        inv_list = [x[0] for x in inv_rows]
        
        for role, txt in st.session_state.chat_history:
            with st.chat_message(role): 
                if "<div" in txt: st.markdown(txt, unsafe_allow_html=True)
                else: st.markdown(txt)
            
        if prompt := st.chat_input("Ketik di sini..."):
            st.session_state.chat_history.append(("user", prompt))
            with st.chat_message("user"): st.markdown(prompt)

            res = parse_intent(prompt, inv_list)
            bs = st.session_state.bot_state
            
            # --- RESET / CANCEL GLOBAL ---
            if res['intent'] == 'cancel':
                reply = "⚠️ **Dibatalkan.** Silakan ketik perintah baru."
                st.session_state.bot_state = {
                    "mode": "idle", "step": 0, "name": None, "phone": None, 
                    "date": None, "time": None, "dur": 1, "items": [], "target_id": None,
                    "course_instrument": None, "course_day": None, "course_time": None
                }
            
            elif res['intent'] == 'reset':
                reply = "🔄 Oke, diulang. Silakan mulai lagi."
                st.session_state.bot_state = {
                    "mode": "idle", "step": 0, "name": None, "phone": None, 
                    "date": None, "time": None, "dur": 1, "items": [], "target_id": None,
                    "course_instrument": None, "course_day": None, "course_time": None
                }
            
            # --- LOGIKA INPUT DATA BOOKING (PARSING) ---
            else:
                if res['date']: bs['date'] = res['date']
                
                if bs['step'] != 'ASK_PHONE':
                    if res['time']: bs['time'] = res['time']
                
                if res['dur']: bs['dur'] = res['dur']
                if res['found_items']: bs['items'].extend(res['found_items'])

                # Early warning booking penuh
                if bs['mode'] == 'booking' and bs['date'] and bs['time']:
                    durasi_cek = bs['dur'] if bs['dur'] else 1 
                    conflict = check_conflict(conn, bs['date'], bs['time'], durasi_cek)
                    if conflict:
                        reply = f"⛔ **Waduh, Penuh!**\n\nTanggal {bs['date']} jam {bs['time']}:00 sudah ada yang booking.\n\nSilakan pilih jam lain ya."
                        bs['time'] = None 
                        bs['step'] = 'ASK_TIME'
                        st.session_state.chat_history.append(("assistant", reply))
                        st.rerun()

                reply = ""
                
                # ==========================================
                # [BARU] LOGIKA CHAT KURSUS
                # ==========================================
                if res['intent'] == 'course_register':
                    bs['mode'] = 'course_register'
                    bs['step'] = 'COURSE_ASK_NAME'
                    reply = "🎓 **Pendaftaran Kursus Musik**\n\nSiap! Boleh tau **Siapa Nama Kamu?**"

                elif bs['mode'] == 'course_register':
                    # STEP 1: NAMA
                    if bs['step'] == 'COURSE_ASK_NAME':
                        bs['name'] = prompt.title()
                        bs['step'] = 'COURSE_ASK_INSTRUMENT'
                        reply = f"Halo Kak {bs['name']}. **Mau belajar alat apa?**\n(Pilihan: Gitar, Piano, Drum, Vokal, Bass)"
                    
                    # STEP 2: INSTRUMEN
                    elif bs['step'] == 'COURSE_ASK_INSTRUMENT':
                        bs['course_instrument'] = prompt.title()
                        bs['step'] = 'COURSE_ASK_DAY'
                        reply = f"Oke, kelas {bs['course_instrument']}. **Bisanya hari apa?** (Contoh: Senin, Sabtu)"
                    
                    # STEP 3: HARI
                    elif bs['step'] == 'COURSE_ASK_DAY':
                        bs['course_day'] = prompt.title()
                        bs['step'] = 'COURSE_ASK_TIME'
                        reply = f"Baik hari {bs['course_day']}. **Jam berapa bisanya?** (Contoh: 16.00 atau jam 4 sore)"
                    
                    # STEP 4: JAM & FINALISASI
                    elif bs['step'] == 'COURSE_ASK_TIME':
                        bs['course_time'] = prompt
                        reply = finalize_course_registration(conn, bs)
                        # Reset State
                        st.session_state.bot_state = {
                            "mode": "idle", "step": 0, "name": None, "phone": None, 
                            "date": None, "time": None, "dur": None, "items": [], "target_id": None,
                            "course_instrument": None, "course_day": None, "course_time": None
                        }

                # ==========================================
                # LOGIKA CHAT BOOKING (LAMA)
                # ==========================================
                elif bs['step'] == 'ASK_PHONE':
                    if len(prompt) > 8 and any(char.isdigit() for char in prompt):
                        bs['phone'] = prompt
                        if not bs['dur']: bs['dur'] = 1 
                        msg, _ = finalize_booking(conn, bs)
                        reply = msg
                        st.session_state.bot_state = {
                            "mode": "idle", "step": 0, "name": None, "phone": None, 
                            "date": None, "time": None, "dur": None, "items": [], "target_id": None,
                            "course_instrument": None, "course_day": None, "course_time": None
                        }
                    else:
                        reply = "Nomor HP sepertinya kurang valid. Mohon masukkan nomor yang benar."

                elif bs['step'] == 'ASK_NAME':
                    bs['name'] = prompt.title()
                    bs['step'] = 'ASK_PHONE'
                    reply = f"Halo Kak {bs['name']}. Terakhir, **berapa Nomor WhatsApp kamu?** (Untuk update level member & kirim tiket)."

                elif bs['step'] == 'ASK_GEAR':
                    if "standar" in prompt.lower() or "tidak" in prompt.lower(): pass 
                    bs['step'] = 'ASK_NAME'
                    reply = f"Oke, alat: {', '.join(bs['items']) if bs['items'] else 'Standar'}. **Atas nama siapa?**"

                elif bs['step'] == 'ASK_DURATION':
                    num_match = re.search(r'\d+', prompt)
                    if num_match:
                        bs['dur'] = int(num_match.group(0))
                        bs['step'] = 'ASK_GEAR'
                        reply = f"Siap {bs['dur']} jam. **Ada tambahan alat?** (Ketik 'Standar' jika tidak ada)."
                    else:
                        reply = "Mohon masukkan angka durasi (contoh: '2')."

                elif bs['step'] == 'ASK_TIME':
                    if bs['time']:
                        if bs['dur'] is None:
                            bs['step'] = 'ASK_DURATION'
                            reply = "Jam aman. **Mau main berapa jam?**"
                        else:
                            bs['step'] = 'ASK_GEAR'
                            reply = f"Oke {bs['dur']} jam. **Ada tambahan alat?** (Ketik 'Standar' jika tidak ada)."
                    else:
                        reply = "Maaf, jam berapa mulainya? (Contoh: '16' atau 'jam 4 sore')"

                elif res['intent'] == 'reschedule':
                    bs['mode'] = 'reschedule'
                    bs['step'] = 'RES_NAME'
                    reply = "Siap reschedule. **Atas nama siapa** booking lamanya?"
                
                elif bs['mode'] == 'reschedule':
                    if bs['step'] == 'RES_NAME':
                        row = conn.execute("SELECT id, date, start_hour FROM bookings WHERE customer_name LIKE ? ORDER BY id DESC", (f"%{prompt}%",)).fetchone()
                        if row:
                            bs['target_id'] = row[0]; bs['step'] = 'RES_TIME'
                            reply = f"Ketemu! Kak {prompt} tgl {row[1]} jam {row[2]}. **Pindah ke Hari & Jam berapa?**"
                        else: reply = "Nama tidak ditemukan."
                    elif bs['step'] == 'RES_TIME':
                        if bs['date'] and bs['time']:
                            msg, _ = process_reschedule(conn, bs['target_id'], bs['date'], bs['time'])
                            reply = msg
                            st.session_state.bot_state = {
                                "mode": "idle", "step": 0, "name": None, "phone": None, 
                                "date": None, "time": None, "dur": None, "items": [], "target_id": None,
                                "course_instrument": None, "course_day": None, "course_time": None
                            }
                        else: reply = "Mohon sebutkan **Hari dan Jam** baru."

                elif res['intent'] == 'booking' or bs['mode'] == 'booking':
                    bs['mode'] = 'booking'
                    if not bs['date']: 
                        wib = datetime.timezone(datetime.timedelta(hours=7))
                        bs['date'] = datetime.datetime.now(wib).strftime("%Y-%m-%d")
                    
                    if not bs['time']:
                        bs['step'] = 'ASK_TIME'
                        reply = f"Siap booking tgl **{bs['date']}**. Jam berapa mainnya?"
                    
                    elif bs['dur'] is None:
                        bs['step'] = 'ASK_DURATION'
                        reply = "Jam oke. **Mau sewa berapa jam?**"

                    elif not bs['items'] and bs['step'] not in ['ASK_NAME', 'ASK_PHONE']:
                        bs['step'] = 'ASK_GEAR'
                        reply = "Sip. **Butuh alat apa saja?**"
                    elif not bs['name']:
                        bs['step'] = 'ASK_NAME'
                        reply = "Siap. **Atas nama siapa**?"
                    elif not bs['phone']:
                        bs['step'] = 'ASK_PHONE'
                        reply = "Terakhir, **Berapa nomor WA kamu?**"
                    else:
                        msg, _ = finalize_booking(conn, bs)
                        reply = msg
                        st.session_state.bot_state = {
                            "mode": "idle", "step": 0, "name": None, "phone": None, 
                            "date": None, "time": None, "dur": None, "items": [], "target_id": None,
                            "course_instrument": None, "course_day": None, "course_time": None
                        }
                
                else:
                    reply = "Halo! Ketik **'Booking'** untuk sewa studio, atau **'Daftar Kursus'** untuk sekolah musik."
            
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
