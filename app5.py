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
# Gunakan format 628xxx (tanpa + atau 0 di depan)
ADMIN_WA = "628123456789" 
# ------------------------------------------

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

DB_FILE = 'smartstudio_v18.db'

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
    
    c.execute('''CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, student_name TEXT, instrument TEXT, 
        schedule_day TEXT, schedule_time TEXT, duration INTEGER, status TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, details TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    try: c.execute("INSERT INTO users VALUES (?, ?)", ('admin', hashlib.sha256("Hanateam123".encode()).hexdigest()))
    except: pass

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
    Mengecek bentrok jadwal baik di tabel Bookings maupun Courses.
    """
    c = conn.cursor()
    
    # 1. Cek Tabel Bookings
    if exclude_type == 'booking' and exclude_id:
        c.execute("SELECT start_hour, duration FROM bookings WHERE date = ? AND id != ?", (date_str, exclude_id))
    else:
        c.execute("SELECT start_hour, duration FROM bookings WHERE date = ?", (date_str,))
    
    for b_start, b_dur in c.fetchall():
        if (start < b_start + b_dur) and (start + duration > b_start): return True

    # 2. Cek Tabel Courses (Asumsi schedule_day menyimpan tanggal spesifik atau hari)
    # Untuk simplifikasi di bot ini, kita anggap course menyimpan Tanggal Spesifik juga untuk sesi pertemuannya
    if exclude_type == 'course' and exclude_id:
        c.execute("SELECT schedule_time, duration FROM courses WHERE schedule_day = ? AND id != ?", (date_str, exclude_id))
    else:
        c.execute("SELECT schedule_time, duration FROM courses WHERE schedule_day = ?", (date_str,))
    
    for c_start_str, c_dur in c.fetchall():
        # Parsing jam course (misal "16:00:00" atau "16")
        try:
            c_start = int(c_start_str.split(':')[0])
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
    if total_jam >= 50: return "🎸 Rockstar", "Diskon 15%", 1.0, "gold"
    elif total_jam >= 20: return "🎹 Pro Musician", "Diskon 10%", 0.7, "orange"
    elif total_jam >= 5: return "🥁 Garage Band", "Diskon 5%", 0.4, "blue"
    else: return "🎤 Newcomer", "Main 5 jam lagi dpt diskon!", 0.1, "gray"

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

    # Parse Date
    if 'hari ini' in txt: res['date'] = today.strftime("%Y-%m-%d")
    elif 'besok' in txt: res['date'] = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    elif 'lusa' in txt: res['date'] = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    else:
        date_match = re.search(r'(tanggal|tgl)\s*(\d{1,2})', clean_txt)
        if date_match:
            try: 
                target_day = int(date_match.group(2))
                # Logic sederhana ganti hari di bulan ini
                res['date'] = today.replace(day=target_day).strftime("%Y-%m-%d")
                clean_txt = clean_txt.replace(date_match.group(0), "")
            except: pass

    # Parse Duration
    d_match = re.search(r'(\d+)\s*(jam|hour)', clean_txt)
    if d_match: 
        res['dur'] = int(d_match.group(1))
        clean_txt = clean_txt.replace(d_match.group(0), "")

    # Parse Time
    time_match = re.search(r'(jam|pukul)?\s*(\d{1,2})[:.]?(\d{2})?\s*(pagi|siang|sore|malam)?', clean_txt)
    if time_match:
        h = int(time_match.group(2))
        modifier = time_match.group(4)
        if modifier:
            if modifier in ['sore', 'malam'] and h < 12: h += 12
            elif modifier == 'siang' and h < 11: h += 12
        if 8 <= h <= 23: res['time'] = h

    # Parse Items
    for item in inventory_list:
        if item in txt or (item.split()[0] in txt): res['found_items'].append(item)
            
    return res

def finalize_booking(conn, bs):
    # Cek Validasi Conflict (Booking & Course)
    conflict = check_conflict(conn, bs['date'], bs['time'], bs['dur'])
    
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
        
        # --- LINK WA KE ADMIN ---
        wa_text = (
            f"*KONFIRMASI BOOKING STUDIO*\n"
            f"--------------------------------\n"
            f"Nama: {bs['name']}\n"
            f"No HP: {bs['phone']}\n"
            f"Tanggal: {bs['date']}\n"
            f"Jam: {bs['time']}:00 WIB\n"
            f"Durasi: {bs['dur']} Jam\n"
            f"Alat: {items_str}\n"
            f"Estimasi Total: Rp {price:,.0f}\n"
            f"--------------------------------\n"
            f"Mohon diproses min!"
        )
        wa_encoded = urllib.parse.quote(wa_text)
        wa_link = f"https://wa.me/{ADMIN_WA}?text={wa_encoded}"

        ticket_html = f"""
<div style="font-family: monospace; background-color: #fffcf5; padding: 20px; border: 2px solid #333; border-radius: 10px; box-shadow: 6px 6px 0px rgba(0,0,0,0.2);">
<div style="text-align: center; border-bottom: 2px dashed #333; padding-bottom: 10px; margin-bottom: 10px;">
<b>🎹 SMART STUDIO RECEIPT</b>
</div>
<div>👤: {bs['name']}</div>
<div>📅: {bs['date']} | ⏰ {bs['time']}:00</div>
<div>⏳: {bs['dur']} Jam | 🎸: {items_str}</div>
<div style="text-align: right; font-weight: bold; margin-top: 10px;">Rp {price:,.0f}</div>
<div style="margin-top: 15px; text-align: center;">
    <a href="{wa_link}" target="_blank" style="display: block; width: 100%; background-color: #25D366; color: white; text-decoration: none; padding: 10px 0; border-radius: 5px; font-weight: bold;">
        📩 Kirim ke Admin (WA)
    </a>
</div>
</div>
"""
        return ticket_html, True

def finalize_course_registration(conn, bs):
    # Cek Validasi Conflict (Booking & Course)
    # Default durasi kursus 1 jam
    conflict = check_conflict(conn, bs['course_date'], bs['course_time'], 1)

    if conflict:
        return f"❌ Maaf Kak {bs['name']}, jadwal {bs['course_date']} jam {bs['course_time']}:00 sudah terisi. Mohon pilih waktu lain.", False
    
    # Simpan
    conn.execute("INSERT INTO courses (student_name, instrument, schedule_day, schedule_time, duration, status) VALUES (?,?,?,?,?,?)", 
                 (bs['name'], bs['course_instrument'], bs['course_date'], str(bs['course_time']), 1, "Pending"))
    log_action(conn, "NEW_COURSE", f"{bs['name']} - {bs['course_instrument']}")
    conn.commit()

    # Link WA ke Admin
    wa_text = (
        f"*PENDAFTARAN KURSUS BARU*\n"
        f"--------------------------------\n"
        f"Nama Siswa: {bs['name']}\n"
        f"Instrumen: {bs['course_instrument']}\n"
        f"Mulai Tanggal: {bs['course_date']}\n"
        f"Jam: {bs['course_time']}:00\n"
        f"--------------------------------\n"
        f"Mohon info biaya pendaftaran min."
    )
    wa_encoded = urllib.parse.quote(wa_text)
    wa_link = f"https://wa.me/{ADMIN_WA}?text={wa_encoded}"

    ticket_html = f"""
<div style="font-family: monospace; background-color: #f0fdf4; padding: 20px; border: 2px solid #166534; border-radius: 10px; box-shadow: 6px 6px 0px rgba(0,0,0,0.1);">
<div style="text-align: center; border-bottom: 2px dashed #166534; padding-bottom: 10px; margin-bottom: 10px;">
<b>🎓 FORMULIR KURSUS</b>
</div>
<div>👤: {bs['name']}</div>
<div>🎸: Kelas {bs['course_instrument']}</div>
<div>📅: {bs['course_date']}</div>
<div>⏰: {bs['course_time']}:00 WIB</div>
<div style="margin-top: 15px; text-align: center;">
    <a href="{wa_link}" target="_blank" style="display: block; width: 100%; background-color: #166534; color: white; text-decoration: none; padding: 10px 0; border-radius: 5px; font-weight: bold;">
        📩 Kirim ke Admin (WA)
    </a>
</div>
</div>
"""
    return ticket_html, True

def process_reschedule(conn, type_res, target_id, new_date, new_time):
    c = conn.cursor()
    
    if type_res == 'booking':
        c.execute("SELECT customer_name, duration FROM bookings WHERE id=?", (target_id,))
        row = c.fetchone()
        if not row: return "❌ Booking tidak ditemukan.", False
        name, duration = row
        table = "bookings"
        exclude_type = 'booking'
    else:
        c.execute("SELECT student_name, duration FROM courses WHERE id=?", (target_id,))
        row = c.fetchone()
        if not row: return "❌ Data Kursus tidak ditemukan.", False
        name, duration = row
        table = "courses"
        exclude_type = 'course'

    # Cek Bentrok
    if check_conflict(conn, new_date, new_time, duration, exclude_id=target_id, exclude_type=exclude_type):
        return f"❌ Gagal. Jam {new_time}:00 di tanggal {new_date} sudah penuh.", False
    
    # Update
    if type_res == 'booking':
        new_price, _ = calculate_price(new_time, duration)
        conn.execute("UPDATE bookings SET date=?, start_hour=?, price=? WHERE id=?", (new_date, new_time, new_price, target_id))
    else:
        # Update course schedule (schedule_day disini kita pakai format YYYY-MM-DD agar seragam)
        conn.execute("UPDATE courses SET schedule_day=?, schedule_time=? WHERE id=?", (new_date, str(new_time), target_id))

    log_action(conn, f"RESCHEDULE_{type_res.upper()}", f"ID {target_id} moved to {new_date}")
    conn.commit()
    
    return f"✅ **Reschedule {type_res.title()} Berhasil!**\nJadwal baru Kak **{name}**: {new_date} jam {new_time}:00.", True

# ==========================================
# 3. UI LAYER
# ==========================================
def main():
    conn = init_db()
    
    # --- Sidebar ---
    st.sidebar.title("🎹 SmartStudio Bot")
    st.sidebar.caption("By Hanateam")
    st.sidebar.markdown("---")
    st.sidebar.header("🏆 Member Area")
    cek_hp = st.sidebar.text_input("Cek Level (No HP):", placeholder="0812xxx")
    
    if cek_hp:
        jam_terbang = get_customer_stats(conn, cek_hp)
        level_name, benefit, progress, lvl_color = get_level_info(jam_terbang)
        st.sidebar.info(f"**Level: {level_name}**")
        st.sidebar.metric("Jam Terbang", f"{jam_terbang} Jam")
        st.sidebar.progress(progress)
        st.sidebar.success(f"🎁 {benefit}")
    
    st.sidebar.markdown("---")
    
    if "admin_logged_in" not in st.session_state: st.session_state.admin_logged_in = False
    if "chat_history" not in st.session_state: st.session_state.chat_history = []
    
    # Init Bot State
    if "bot_state" not in st.session_state: 
        st.session_state.bot_state = {
            "mode": "idle", "step": 0, 
            "name": None, "phone": None, 
            "date": None, "time": None, "dur": None, 
            "items": [], "target_id": None, "res_type": None,
            "course_instrument": None, "course_date": None, "course_time": None
        }

    # Admin Login
    with st.sidebar.expander("🔐 Admin Login", expanded=False):
        if not st.session_state.admin_logged_in:
            pwd = st.text_input("Password", type="password")
            if st.button("Masuk"):
                if hashlib.sha256(pwd.encode()).hexdigest() == hashlib.sha256("Hanateam123".encode()).hexdigest():
                    st.session_state.admin_logged_in = True; st.rerun()
                else: st.error("Salah password")
        else:
            if st.button("Keluar"): st.session_state.admin_logged_in = False; st.rerun()

    # ==========================================
    # VIEW A: ADMIN DASHBOARD
    # ==========================================
    if st.session_state.admin_logged_in:
        st.title("🎛️ Studio Command Center")
        
        with st.expander("💾 Database Tools", expanded=True):
            if st.button("💣 Hapus Database (Reset Total)"):
                conn.close()
                if os.path.exists(DB_FILE): os.remove(DB_FILE)
                st.success("Reset Berhasil. Reloading..."); time.sleep(2); st.rerun()

        df_bk = pd.read_sql("SELECT * FROM bookings", conn)
        df_crs = pd.read_sql("SELECT * FROM courses", conn)
        
        c1, c2 = st.columns(2)
        c1.metric("Total Bookings", f"{len(df_bk)}")
        c2.metric("Total Siswa", f"{len(df_crs)}")
        
        t1, t2, t3, t4 = st.tabs(["📅 Bookings", "🛠️ Inventory", "🎓 Courses", "🛡️ Logs"])
        
        with t1:
            st.dataframe(df_bk, use_container_width=True, hide_index=True)
            if not df_bk.empty:
                del_ops = df_bk.apply(lambda x: f"{x['id']} - {x['customer_name']}", axis=1)
                sel_del = st.selectbox("Hapus Booking", del_ops)
                if st.button("❌ Hapus"):
                    conn.execute("DELETE FROM bookings WHERE id=?", (int(sel_del.split(' - ')[0]),))
                    conn.commit(); st.rerun()

        with t2:
            st.dataframe(pd.read_sql("SELECT * FROM inventory", conn), use_container_width=True)
            new_item = st.text_input("Tambah Alat")
            if st.button("Simpan Alat"):
                try:
                    conn.execute("INSERT INTO inventory (item_name) VALUES (?)", (new_item.lower(),))
                    conn.commit(); st.rerun()
                except: pass

        with t3:
            st.info("ℹ️ Pendaftaran Siswa baru dilakukan melalui Chatbot agar tervalidasi jadwalnya.")
            st.dataframe(df_crs, use_container_width=True)
            if not df_crs.empty:
                sel_c_del = st.selectbox("Hapus Siswa", df_crs.apply(lambda x: f"{x['id']} - {x['student_name']}", axis=1))
                if st.button("❌ Hapus Siswa"):
                    conn.execute("DELETE FROM courses WHERE id=?", (int(sel_c_del.split(' - ')[0]),))
                    conn.commit(); st.rerun()

        with t4: st.dataframe(pd.read_sql("SELECT * FROM audit_logs ORDER BY id DESC", conn), use_container_width=True)

    # ==========================================
    # VIEW B: CHATBOT (USER)
    # ==========================================
    else:
        st.title("🤖 Assistant Studio")
        st.caption(f"Nomor Admin: {ADMIN_WA}")

        # Heatmap Ketersediaan
        with st.expander("📊 Cek Jadwal Kosong", expanded=False):
            tgl_pilih = st.date_input("Cek Tanggal:", datetime.date.today())
            bookings_today = conn.execute("SELECT start_hour, duration FROM bookings WHERE date = ?", (str(tgl_pilih),)).fetchall()
            courses_today = conn.execute("SELECT schedule_time, duration FROM courses WHERE schedule_day = ?", (str(tgl_pilih),)).fetchall()
            
            hours_map = {h: 0 for h in range(8, 24)}
            
            # Hitung load Booking
            for start, dur in bookings_today:
                for h in range(start, start + dur):
                    if h in hours_map: hours_map[h] += 1
            
            # Hitung load Kursus
            for t_str, dur in courses_today:
                try:
                    start = int(str(t_str).split(':')[0])
                    for h in range(start, start + dur):
                        if h in hours_map: hours_map[h] += 1
                except: pass
            
            df_heat = pd.DataFrame({"Jam": [f"{h}:00" for h in hours_map], "Status": ["Penuh" if v > 0 else "Kosong" for v in hours_map.values()], "Value": list(hours_map.values())})
            st.bar_chart(df_heat.set_index("Jam")['Value'], color="#F63366")
            
            penuh = [k for k, v in hours_map.items() if v > 0]
            if penuh: st.warning(f"Jam Sibuk: {', '.join([str(x)+':00' for x in penuh])}")
            else: st.success("Jadwal Kosong!")

        # Chat Interface
        if not st.session_state.chat_history:
            st.session_state.chat_history.append(("assistant", "Halo! 👋 Ketik **'Booking'** untuk sewa studio atau **'Daftar Kursus'** untuk les."))

        inv_rows = conn.execute("SELECT item_name FROM inventory").fetchall()
        inv_list = [x[0] for x in inv_rows]
        
        for role, txt in st.session_state.chat_history:
            with st.chat_message(role): 
                if "<div" in txt: st.markdown(txt, unsafe_allow_html=True)
                else: st.markdown(txt)
            
        if prompt := st.chat_input("Ketik pesan..."):
            st.session_state.chat_history.append(("user", prompt))
            with st.chat_message("user"): st.markdown(prompt)

            res = parse_intent(prompt, inv_list)
            bs = st.session_state.bot_state
            
            # ----------------------------------------
            # GLOBAL COMMANDS
            # ----------------------------------------
            if res['intent'] == 'cancel':
                reply = "⚠️ **Dibatalkan.**"
                st.session_state.bot_state = {k:None for k in bs} # Reset All
                bs = st.session_state.bot_state; bs['mode'] = 'idle'; bs['step'] = 0; bs['items'] = []
            
            elif res['intent'] == 'reset':
                reply = "🔄 Reset. Silakan mulai lagi."
                st.session_state.bot_state = {k:None for k in bs} # Reset All
                bs = st.session_state.bot_state; bs['mode'] = 'idle'; bs['step'] = 0; bs['items'] = []
            
            # ----------------------------------------
            # INTENT: BOOKING
            # ----------------------------------------
            elif res['intent'] == 'booking' or bs['mode'] == 'booking':
                bs['mode'] = 'booking'
                
                # Parsing Contextual
                if res['date']: bs['date'] = res['date']
                if res['dur']: bs['dur'] = res['dur']
                if bs['step'] != 'ASK_PHONE' and res['time']: bs['time'] = res['time']
                if res['found_items']: bs['items'].extend(res['found_items'])

                # Flow
                if not bs['date']:
                    bs['step'] = 'ASK_DATE'
                    reply = "Siap Booking Studio. **Untuk tanggal berapa?** (Contoh: Besok, atau Tgl 25)"
                
                elif not bs['time']:
                    bs['step'] = 'ASK_TIME'
                    reply = f"Oke tanggal {bs['date']}. **Jam berapa mulainya?**"
                
                elif bs['dur'] is None:
                    # Cek Availability Awal sebelum tanya durasi
                    if check_conflict(conn, bs['date'], bs['time'], 1):
                        reply = f"⛔ Jam {bs['time']}:00 penuh. Pilih jam lain ya."
                        bs['time'] = None
                    else:
                        bs['step'] = 'ASK_DURATION'
                        reply = "Jam tersedia. **Mau sewa berapa jam?**"

                elif not bs['items'] and bs['step'] == 'ASK_DURATION':
                    # Cek Availability Full dengan durasi
                    if check_conflict(conn, bs['date'], bs['time'], bs['dur']):
                        reply = f"⛔ Maaf, slot waktu tidak cukup untuk {bs['dur']} jam. Coba durasi lebih pendek atau jam lain."
                        bs['dur'] = None
                    else:
                        bs['step'] = 'ASK_GEAR'
                        reply = "Oke. **Ada tambahan alat khusus?** (Ketik 'Standar' jika tidak)."
                
                elif not bs['name']:
                    if bs['step'] == 'ASK_GEAR' and ("standar" in prompt.lower() or "tidak" in prompt.lower()): pass
                    bs['step'] = 'ASK_NAME'
                    reply = "Siap. **Atas nama siapa?**"
                
                elif not bs['phone']:
                    bs['name'] = prompt.title()
                    bs['step'] = 'ASK_PHONE'
                    reply = "Terakhir, **Nomor WA kakak?** (Untuk konfirmasi admin)."
                
                elif bs['step'] == 'ASK_PHONE':
                    if len(prompt) > 8:
                        bs['phone'] = prompt
                        msg, status = finalize_booking(conn, bs)
                        reply = msg
                        # Reset
                        st.session_state.bot_state = {k:None for k in bs}; bs=st.session_state.bot_state; bs['mode']='idle'; bs['items'] = []
                    else:
                        reply = "Nomor tidak valid. Masukkan angka saja."
                else:
                    reply = "Lanjut..."

            # ----------------------------------------
            # INTENT: KURSUS (UPDATED)
            # ----------------------------------------
            elif res['intent'] == 'course_register' or bs['mode'] == 'course_register':
                bs['mode'] = 'course_register'
                
                if bs['step'] == 0:
                    bs['step'] = 'C_NAME'
                    reply = "🎓 **Pendaftaran Kursus Musik**\nSiapa nama calon siswanya?"
                
                elif bs['step'] == 'C_NAME':
                    bs['name'] = prompt.title()
                    bs['step'] = 'C_INSTRUMENT'
                    reply = f"Halo {bs['name']}. **Mau belajar alat apa?** (Gitar/Piano/Drum/Vokal)"
                
                elif bs['step'] == 'C_INSTRUMENT':
                    bs['course_instrument'] = prompt.title()
                    bs['step'] = 'C_DATE'
                    reply = "Oke. **Mau mulai tanggal berapa?** (Sebutkan Tanggal, misal: Tgl 25)"
                
                elif bs['step'] == 'C_DATE':
                    # Parsing Tanggal Manual jika regex global gagal menangkap konteks spesifik ini
                    detected_date = res['date']
                    if detected_date:
                        bs['course_date'] = detected_date
                        bs['step'] = 'C_TIME'
                        reply = f"Siap tanggal {bs['course_date']}. **Jam berapa bisanya?** (Contoh: 16.00)"
                    else:
                        reply = "Mohon sebutkan tanggal yang jelas (contoh: 'Besok' atau 'Tgl 25')."

                elif bs['step'] == 'C_TIME':
                    # Parsing Jam Manual
                    tm = re.search(r'(\d{1,2})', prompt)
                    if tm:
                        val_time = int(tm.group(1))
                        if 8 <= val_time <= 22:
                            bs['course_time'] = val_time
                            msg, status = finalize_course_registration(conn, bs)
                            if status:
                                reply = msg
                                # Reset
                                st.session_state.bot_state = {k:None for k in bs}; bs=st.session_state.bot_state; bs['mode']='idle'; bs['items'] = []
                            else:
                                reply = msg + "\n\nSilakan pilih **Jam** lain:"
                        else: reply = "Studio buka jam 08:00 - 23:00."
                    else: reply = "Jam berapa? (Masukkan angka, misal '15')"

            # ----------------------------------------
            # INTENT: RESCHEDULE (UPDATED)
            # ----------------------------------------
            elif res['intent'] == 'reschedule' or bs['mode'] == 'reschedule':
                bs['mode'] = 'reschedule'
                
                if bs['step'] == 0:
                    bs['step'] = 'RES_TYPE'
                    reply = "🔄 **Reschedule Jadwal**\nMau ganti jadwal **Booking** Studio atau **Kursus**?"
                
                elif bs['step'] == 'RES_TYPE':
                    if 'kursus' in prompt.lower(): bs['res_type'] = 'course'
                    else: bs['res_type'] = 'booking'
                    
                    bs['step'] = 'RES_NAME'
                    reply = f"Oke Reschedule {bs['res_type'].title()}. **Atas nama siapa data lamanya?**"
                
                elif bs['step'] == 'RES_NAME':
                    table = "bookings" if bs['res_type'] == 'booking' else "courses"
                    col_name = "customer_name" if bs['res_type'] == 'booking' else "student_name"
                    col_date = "date" if bs['res_type'] == 'booking' else "schedule_day"
                    col_time = "start_hour" if bs['res_type'] == 'booking' else "schedule_time"

                    rows = conn.execute(f"SELECT id, {col_date}, {col_time} FROM {table} WHERE {col_name} LIKE ? ORDER BY id DESC", (f"%{prompt}%",)).fetchall()
                    
                    if rows:
                        row = rows[0]
                        bs['target_id'] = row[0]
                        bs['step'] = 'RES_NEW_DATE'
                        reply = f"Ketemu! Jadwal lama: {row[1]} jam {row[2]}. **Pindah ke Tanggal berapa?**"
                    else:
                        reply = "Data tidak ditemukan. Coba nama lain?"

                elif bs['step'] == 'RES_NEW_DATE':
                    if res['date']:
                        bs['date'] = res['date'] # Simpan sementara di var booking biar hemat memori
                        bs['step'] = 'RES_NEW_TIME'
                        reply = f"Oke ke tanggal {bs['date']}. **Jam berapa?**"
                    else: reply = "Tanggal berapa? (Contoh: Besok)"

                elif bs['step'] == 'RES_NEW_TIME':
                    if res['time']:
                        msg, status = process_reschedule(conn, bs['res_type'], bs['target_id'], bs['date'], res['time'])
                        reply = msg
                        st.session_state.bot_state = {k:None for k in bs}; bs=st.session_state.bot_state; bs['mode']='idle'; bs['items'] = []
                    else:
                        reply = "Jam berapa? (Contoh: 15)"

            else:
                reply = "Saya tidak mengerti. Ketik **Booking**, **Daftar Kursus**, atau **Reschedule**."

            # Output Reply
            time.sleep(0.5)
            st.session_state.chat_history.append(("assistant", reply))
            with st.chat_message("assistant"): 
                if "<div" in reply: st.markdown(reply, unsafe_allow_html=True)
                else: st.markdown(reply)
            
            st.rerun()

if __name__ == "__main__":
    main()
