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

# --- [PENTING] GANTI NOMOR ADMIN DISINI ---
ADMIN_WA = "628123456789" 
# ------------------------------------------

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stChatMessage { background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; }
    div[data-testid="stMetric"] { background-color: #1e293b; padding: 20px; border-radius: 10px; border-left: 4px solid #3b82f6; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
    h1, h2, h3 { color: #f8fafc !important; }
    .stButton button { border-radius: 8px; font-weight: 600; }
    div[data-testid="stExpander"] { background-color: #1e293b; border-radius: 10px; }
    .approve-btn { background-color: #22c55e !important; color: white !important; }
    .reject-btn { background-color: #ef4444 !important; color: white !important; }
</style>
""", unsafe_allow_html=True)

DB_FILE = 'smartstudio_v20.db'

# ==========================================
# 1. DATABASE SYSTEM
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Tabel User Admin
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT)''')
    
    # Tabel Booking Studio
    c.execute('''CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT, no_hp TEXT, date TEXT, 
        start_hour INTEGER, duration INTEGER, instruments TEXT, price REAL, status TEXT)''')
    
    # Tabel Inventory
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT UNIQUE)''')
    
    # Tabel Courses (Ada kolom status: Pending/Active/Rejected)
    c.execute('''CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, student_name TEXT, instrument TEXT, 
        schedule_day TEXT, schedule_time TEXT, duration INTEGER, status TEXT)''')
    
    # Tabel Logs
    c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, details TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    # Buat Akun Admin Default (Password: Hanateam123)
    try: c.execute("INSERT INTO users VALUES (?, ?)", ('admin', hashlib.sha256("Hanateam123".encode()).hexdigest()))
    except: pass

    # Isi Inventory Default
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
# 2. LOGIC SYSTEM (Conflict & Price)
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
    Mengecek bentrok jadwal.
    - Booking vs Booking: Cek tabrakan jam.
    - Booking vs Course: Cek tabrakan. Course status 'Pending' & 'Active' dianggap MEMBLOKIR. 
      Course 'Rejected' tidak memblokir.
    """
    c = conn.cursor()
    
    # 1. Cek Tabel Bookings
    query_bk = "SELECT start_hour, duration FROM bookings WHERE date = ?"
    params_bk = [date_str]
    
    if exclude_type == 'booking' and exclude_id:
        query_bk += " AND id != ?"
        params_bk.append(exclude_id)
        
    c.execute(query_bk, tuple(params_bk))
    for b_start, b_dur in c.fetchall():
        if (start < b_start + b_dur) and (start + duration > b_start): return True

    # 2. Cek Tabel Courses (Abaikan yang status Rejected)
    query_course = "SELECT schedule_time, duration FROM courses WHERE schedule_day = ? AND status != 'Rejected'"
    params_course = [date_str]
    
    if exclude_type == 'course' and exclude_id:
        query_course += " AND id != ?"
        params_course.append(exclude_id)
        
    c.execute(query_course, tuple(params_course))
    
    for c_start_str, c_dur in c.fetchall():
        try:
            c_start = int(c_start_str.split(':')[0]) # Asumsi format jam "16" atau "16:00"
            if (start < c_start + c_dur) and (start + duration > c_start): return True
        except: pass
        
    return False

def get_customer_stats(conn, no_hp):
    c = conn.cursor()
    try:
        c.execute("SELECT SUM(duration) FROM bookings WHERE no_hp = ?", (no_hp,))
        result = c.fetchone()[0]
        return result if result else 0
    except: return 0

def get_level_info(total_jam):
    if total_jam >= 50: return "🎸 Rockstar", "Diskon 15%", 1.0
    elif total_jam >= 20: return "🎹 Pro Musician", "Diskon 10%", 0.7
    elif total_jam >= 5: return "🥁 Garage Band", "Diskon 5%", 0.4
    else: return "🎤 Newcomer", "Main 5 jam lagi dpt diskon!", 0.1

def parse_intent(user_input, inventory_list):
    txt = user_input.lower()
    res = {'intent': 'unknown', 'date': None, 'time': None, 'dur': None, 'found_items': []}
    
    if 'batal' in txt or 'cancel' in txt: res['intent'] = 'cancel'
    elif 'ulang' in txt or 'reset' in txt: res['intent'] = 'reset'
    elif 'reschedule' in txt or 'ganti' in txt: res['intent'] = 'reschedule'
    elif any(x in txt for x in ['kursus', 'les', 'sekolah', 'privat']): res['intent'] = 'course_register'
    elif any(x in txt for x in ['booking', 'sewa', 'pesan']): res['intent'] = 'booking'
    
    clean_txt = txt 
    wib = datetime.timezone(datetime.timedelta(hours=7))
    today = datetime.datetime.now(wib).date()

    # Parsing Tanggal
    if 'hari ini' in txt: res['date'] = today.strftime("%Y-%m-%d")
    elif 'besok' in txt: res['date'] = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    elif 'lusa' in txt: res['date'] = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    else:
        date_match = re.search(r'(tanggal|tgl)\s*(\d{1,2})', clean_txt)
        if date_match:
            try: 
                target_day = int(date_match.group(2))
                # Logic sederhana: jika tgl < hari ini, berarti bulan depan, otherwise bulan ini (simplified)
                res['date'] = today.replace(day=target_day).strftime("%Y-%m-%d")
                clean_txt = clean_txt.replace(date_match.group(0), "")
            except: pass

    # Parsing Durasi
    d_match = re.search(r'(\d+)\s*(jam|hour)', clean_txt)
    if d_match: 
        res['dur'] = int(d_match.group(1))
        clean_txt = clean_txt.replace(d_match.group(0), "")

    # Parsing Jam
    time_match = re.search(r'(jam|pukul)?\s*(\d{1,2})[:.]?(\d{2})?\s*(pagi|siang|sore|malam)?', clean_txt)
    if time_match:
        h = int(time_match.group(2))
        modifier = time_match.group(4)
        if modifier:
            if modifier in ['sore', 'malam'] and h < 12: h += 12
            elif modifier == 'siang' and h < 11: h += 12
        if 8 <= h <= 23: res['time'] = h

    # Parsing Alat
    for item in inventory_list:
        if item in txt or (item.split()[0] in txt): res['found_items'].append(item)
            
    return res

# ==========================================
# 3. TRANSACTION FUNCTIONS
# ==========================================
def finalize_booking(conn, bs):
    # Cek Conflict
    conflict = check_conflict(conn, bs['date'], bs['time'], bs['dur'])
    if conflict:
        return f"❌ Maaf Kak {bs['name']}, jam {bs['time']}:00 di tanggal {bs['date']} sudah penuh (ada Booking/Kursus).", False
    
    # Hitung Harga & Insert
    price, _ = calculate_price(bs['time'], bs['dur'])
    items_str = ", ".join(set(bs['items'])).title() if bs['items'] else "Standard Room"
    
    conn.execute('''INSERT INTO bookings (customer_name, no_hp, date, start_hour, duration, instruments, price, status) 
                    VALUES (?,?,?,?,?,?,?,?)''', 
                    (bs['name'], bs['phone'], bs['date'], bs['time'], bs['dur'], items_str, price, "Confirmed"))
    log_action(conn, "NEW_BOOKING", f"{bs['name']} ({bs['phone']}) - {bs['date']}")
    conn.commit()
    
    # Generate Link WA Admin
    wa_text = f"BOOKING STUDIO\nNama: {bs['name']}\nTgl: {bs['date']}\nJam: {bs['time']}:00\nDurasi: {bs['dur']} Jam\nTotal: Rp {price:,.0f}"
    wa_link = f"https://wa.me/{ADMIN_WA}?text={urllib.parse.quote(wa_text)}"

    ticket_html = f"""
    <div style='background:#fffcf5; padding:15px; border-radius:10px; border:2px solid #333; color:#000;'>
    <b>🎹 TIKET BOOKING</b><br>
    👤 {bs['name']} ({bs['phone']})<br>
    📅 {bs['date']} | ⏰ {bs['time']}:00<br>
    ⏳ {bs['dur']} Jam | 💰 Rp {price:,.0f}<br>
    <a href="{wa_link}" target="_blank" style="background:#25D366; color:white; padding:8px; border-radius:5px; text-decoration:none; display:block; text-align:center; margin-top:10px; font-weight:bold;">
    📩 Kirim ke Admin (WA)</a>
    </div>"""
    return ticket_html, True

def finalize_course_registration(conn, bs):
    # Cek Conflict (Pending tetap blokir slot)
    conflict = check_conflict(conn, bs['course_date'], bs['course_time'], 1)
    if conflict:
        return f"❌ Maaf, jadwal {bs['course_date']} jam {bs['course_time']}:00 sudah terisi.", False
    
    # Insert dengan Status PENDING
    conn.execute("INSERT INTO courses (student_name, instrument, schedule_day, schedule_time, duration, status) VALUES (?,?,?,?,?,?)", 
                 (bs['name'], bs['course_instrument'], bs['course_date'], str(bs['course_time']), 1, "Pending"))
    log_action(conn, "NEW_COURSE_REQ", f"{bs['name']} - {bs['course_instrument']}")
    conn.commit()

    # Generate Link WA Admin
    wa_text = f"DAFTAR KURSUS\nNama: {bs['name']}\nInstrumen: {bs['course_instrument']}\nTgl Mulai: {bs['course_date']}\nJam: {bs['course_time']}:00\nMohon Approval."
    wa_link = f"https://wa.me/{ADMIN_WA}?text={urllib.parse.quote(wa_text)}"

    ticket_html = f"""
    <div style='background:#f0fdf4; padding:15px; border-radius:10px; border:2px solid #166534; color:#000;'>
    <b>🎓 PENDAFTARAN DITERIMA</b><br>
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
    # Identifikasi tabel dan ambil durasi
    if type_res == 'booking':
        c.execute("SELECT customer_name, duration FROM bookings WHERE id=?", (target_id,))
        exclude_type = 'booking'
    else:
        c.execute("SELECT student_name, duration FROM courses WHERE id=?", (target_id,))
        exclude_type = 'course'
    
    row = c.fetchone()
    if not row: return "❌ Data tidak ditemukan.", False
    name, duration = row

    # Cek Conflict dengan jadwal baru (exclude ID sendiri)
    if check_conflict(conn, new_date, new_time, duration, exclude_id=target_id, exclude_type=exclude_type):
        return f"❌ Gagal. Jam {new_time}:00 di tanggal {new_date} sudah penuh.", False
    
    # Update DB
    if type_res == 'booking':
        new_price, _ = calculate_price(new_time, duration)
        conn.execute("UPDATE bookings SET date=?, start_hour=?, price=? WHERE id=?", (new_date, new_time, new_price, target_id))
    else:
        conn.execute("UPDATE courses SET schedule_day=?, schedule_time=? WHERE id=?", (new_date, str(new_time), target_id))

    log_action(conn, f"RESCHEDULE_{type_res.upper()}", f"ID {target_id} moved to {new_date}")
    conn.commit()
    return f"✅ **Reschedule Berhasil!**\nJadwal baru Kak **{name}**: {new_date} jam {new_time}:00.", True

# ==========================================
# 4. MAIN APPLICATION (UI)
# ==========================================
def main():
    conn = init_db()
    
    st.sidebar.title("🎹 SmartStudio Bot")
    st.sidebar.caption("By Hanateam")
    
    # --- Sidebar: Admin Login ---
    if "admin_logged_in" not in st.session_state: st.session_state.admin_logged_in = False
    
    with st.sidebar.expander("🔐 Admin Area", expanded=not st.session_state.admin_logged_in):
        if not st.session_state.admin_logged_in:
            pwd = st.text_input("Password", type="password")
            if st.button("Masuk"):
                if hashlib.sha256(pwd.encode()).hexdigest() == hashlib.sha256("Hanateam123".encode()).hexdigest():
                    st.session_state.admin_logged_in = True; st.rerun()
                else: st.error("Salah password")
        else:
            if st.button("Keluar"): st.session_state.admin_logged_in = False; st.rerun()

    # --- Sidebar: Cek Member ---
    st.sidebar.markdown("---")
    st.sidebar.header("🏆 Cek Member")
    cek_hp = st.sidebar.text_input("No HP:", placeholder="0812xxx")
    if cek_hp:
        jam = get_customer_stats(conn, cek_hp)
        lvl, ben, prog, _ = get_level_info(jam)
        st.sidebar.info(f"Level: {lvl}\nTotal Main: {jam} Jam")
        st.sidebar.progress(prog)
        st.sidebar.success(ben)

    # ==========================================
    # VIEW A: ADMIN DASHBOARD
    # ==========================================
    if st.session_state.admin_logged_in:
        st.title("🎛️ Studio Command Center")
        
        t1, t2, t3, t4 = st.tabs(["📅 Bookings", "🛠️ Inventory", "🎓 Courses (Approval)", "🛡️ Logs"])
        
        # --- TAB 1: BOOKINGS ---
        with t1:
            df_bk = pd.read_sql("SELECT * FROM bookings ORDER BY id DESC", conn)
            st.dataframe(df_bk, use_container_width=True, hide_index=True)
            if not df_bk.empty:
                del_id = st.number_input("Hapus ID Booking", min_value=0, step=1)
                if st.button("Hapus Booking"):
                    conn.execute("DELETE FROM bookings WHERE id=?", (del_id,))
                    conn.commit(); st.rerun()

        # --- TAB 2: INVENTORY ---
        with t2:
            st.dataframe(pd.read_sql("SELECT * FROM inventory", conn), use_container_width=True)
            new_item = st.text_input("Tambah Alat Baru")
            if st.button("Simpan Alat"):
                try:
                    conn.execute("INSERT INTO inventory (item_name) VALUES (?)", (new_item.lower(),))
                    conn.commit(); st.rerun()
                except: pass

        # --- TAB 3: COURSES (APPROVAL SYSTEM) ---
        with t3:
            st.info("ℹ️ Input manual siswa dihapus. Semua pendaftaran via Chatbot dan masuk ke antrian 'Pending' di sini.")
            
            c_col1, c_col2 = st.columns(2)
            
            with c_col1:
                st.markdown("### ⏳ Menunggu Approval")
                df_pending = pd.read_sql("SELECT * FROM courses WHERE status='Pending'", conn)
                
                if df_pending.empty:
                    st.success("Tidak ada antrian pending.")
                else:
                    for idx, row in df_pending.iterrows():
                        with st.container(border=True):
                            st.markdown(f"**{row['student_name']}** - {row['instrument']}")
                            st.text(f"Jadwal: {row['schedule_day']} | {row['schedule_time']}:00")
                            
                            b1, b2 = st.columns(2)
                            if b1.button("✅ Terima", key=f"acc_{row['id']}"):
                                conn.execute("UPDATE courses SET status='Active' WHERE id=?", (row['id'],))
                                log_action(conn, "COURSE_APPROVED", f"Approved {row['student_name']}")
                                conn.commit(); st.rerun()
                            
                            if b2.button("❌ Tolak", key=f"rej_{row['id']}"):
                                conn.execute("UPDATE courses SET status='Rejected' WHERE id=?", (row['id'],))
                                log_action(conn, "COURSE_REJECTED", f"Rejected {row['student_name']}")
                                conn.commit(); st.rerun()

            with c_col2:
                st.markdown("### ✅ Siswa Aktif")
                df_active = pd.read_sql("SELECT id, student_name, instrument, schedule_day, schedule_time FROM courses WHERE status='Active'", conn)
                st.dataframe(df_active, hide_index=True, use_container_width=True)
                
                with st.expander("Lihat Data Rejected / Histori"):
                    df_rej = pd.read_sql("SELECT * FROM courses WHERE status='Rejected'", conn)
                    st.dataframe(df_rej, hide_index=True)

        # --- TAB 4: LOGS ---
        with t4:
            if st.button("Clear Logs"):
                conn.execute("DELETE FROM audit_logs"); conn.commit(); st.rerun()
            st.dataframe(pd.read_sql("SELECT * FROM audit_logs ORDER BY id DESC", conn), use_container_width=True)

    # ==========================================
    # VIEW B: CHATBOT INTERFACE
    # ==========================================
    else:
        if "chat_history" not in st.session_state: 
            st.session_state.chat_history = [("assistant", "Halo! 👋 Ketik **'Booking'** untuk sewa atau **'Daftar Kursus'** untuk les.")]
        
        if "bot_state" not in st.session_state: 
            st.session_state.bot_state = {
                "mode": "idle", "step": 0, "name": None, "phone": None, 
                "date": None, "time": None, "dur": None, "items": [], 
                "target_id": None, "res_type": None,
                "course_instrument": None, "course_date": None, "course_time": None
            }

        st.title("🤖 Assistant Studio")
        st.caption(f"Nomor Admin: {ADMIN_WA}")

        # --- Chat UI ---
        for role, txt in st.session_state.chat_history:
            with st.chat_message(role): 
                if "<div" in txt: st.markdown(txt, unsafe_allow_html=True)
                else: st.markdown(txt)

        # --- Logic Engine ---
        if prompt := st.chat_input("Ketik pesan..."):
            st.session_state.chat_history.append(("user", prompt))
            with st.chat_message("user"): st.markdown(prompt)

            bs = st.session_state.bot_state
            inv_rows = conn.execute("SELECT item_name FROM inventory").fetchall()
            res = parse_intent(prompt, [x[0] for x in inv_rows])

            # GLOBAL COMMANDS
            if res['intent'] == 'cancel':
                reply = "⚠️ Transaksi Dibatalkan."
                st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'
            
            elif res['intent'] == 'reset':
                reply = "🔄 Reset Bot."
                st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'

            # BOOKING FLOW
            elif res['intent'] == 'booking' or bs['mode'] == 'booking':
                bs['mode'] = 'booking'
                if res['date']: bs['date'] = res['date']
                if res['time']: bs['time'] = res['time']
                if res['dur']: bs['dur'] = res['dur']
                if res['found_items']: bs['items'].extend(res['found_items'])

                if not bs['date']:
                    bs['step'] = 'ASK_DATE'; reply = "📅 Siap Booking. Tanggal berapa? (Misal: Besok)"
                elif not bs['time']:
                    bs['step'] = 'ASK_TIME'; reply = f"⏰ Oke tanggal {bs['date']}. Jam berapa mulainya?"
                elif not bs['dur']:
                    bs['step'] = 'ASK_DUR'; reply = "⏳ Mau sewa berapa jam?"
                elif not bs['name']:
                    bs['step'] = 'ASK_NAME'; reply = "👤 Atas nama siapa?"
                elif not bs['phone']:
                    bs['name'] = prompt.title(); bs['step'] = 'ASK_PHONE'; reply = "📱 Nomor WA? (Untuk konfirmasi)"
                else:
                    bs['phone'] = prompt
                    msg, _ = finalize_booking(conn, bs)
                    reply = msg
                    st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'

            # COURSE FLOW (UPDATED for Approval)
            elif res['intent'] == 'course_register' or bs['mode'] == 'course_register':
                bs['mode'] = 'course_register'
                if bs['step'] == 0:
                    bs['step'] = 'C_NAME'; reply = "🎓 **Pendaftaran Kursus**\nSiapa nama calon siswanya?"
                elif bs['step'] == 'C_NAME':
                    bs['name'] = prompt.title(); bs['step'] = 'C_INS'; reply = "🎸 Halo! Mau ambil kelas alat apa?"
                elif bs['step'] == 'C_INS':
                    bs['course_instrument'] = prompt.title(); bs['step'] = 'C_DATE'; reply = "📅 Mau mulai tanggal berapa?"
                elif bs['step'] == 'C_DATE':
                    if res['date']:
                        bs['course_date'] = res['date']; bs['step'] = 'C_TIME'; reply = f"⏰ Tanggal {bs['course_date']}. Jam berapa bisanya?"
                    else: reply = "Mohon sebutkan tanggal (Contoh: Besok / Tgl 25)."
                elif bs['step'] == 'C_TIME':
                    if res['time']:
                        bs['course_time'] = res['time']
                        msg, stat = finalize_course_registration(conn, bs)
                        reply = msg
                        st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'
                    else: reply = "Jam berapa? (Masukkan angka, misal 16)"

            # RESCHEDULE FLOW
            elif res['intent'] == 'reschedule' or bs['mode'] == 'reschedule':
                bs['mode'] = 'reschedule'
                if bs['step'] == 0:
                    bs['step'] = 'RES_TYPE'; reply = "🔄 **Reschedule Jadwal**\nMau ganti jadwal **Booking** atau **Kursus**?"
                elif bs['step'] == 'RES_TYPE':
                    bs['res_type'] = 'course' if 'kursus' in prompt.lower() else 'booking'
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
                        st.session_state.bot_state = {k:None for k in bs}; bs['mode']='idle'
                    else: reply = "Jam berapa? (Angka 0-23)"
            
            else:
                reply = "Maaf saya belum mengerti. Ketik **Booking** untuk sewa, **Kursus** untuk daftar les, atau **Reschedule**."

            # Output Response
            time.sleep(0.5)
            st.session_state.chat_history.append(("assistant", reply))
            with st.chat_message("assistant"): 
                if "<div" in reply: st.markdown(reply, unsafe_allow_html=True)
                else: st.markdown(reply)
            
            st.rerun()

if __name__ == "__main__":
    main()
