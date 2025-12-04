# app.py
from flask import Flask, render_template, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
from requests.exceptions import RequestException
import requests
import json
import time

# --- การตั้งค่า Flask และ Database ---
app = Flask(__name__)

# กำหนดค่า SQLAlchemy ให้ใช้ SQLite และสร้างไฟล์ database.db
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ระบุ Universe ID ของเกม Roblox ที่คุณต้องการวิเคราะห์
# ตัวอย่างนี้ใช้ Universe ID ของ "Adopt Me!" (ID: 920587237)
ROBLOX_UNIVERSE_ID = 920587237
ROBLOX_API_URL = f"https://games.roblox.com/v1/games?universeIds={ROBLOX_UNIVERSE_ID}"
GAME_NAME = "Adopt Me! (ตัวอย่าง)" # ใช้ชื่อชั่วคราวเพื่อแสดงผล

# --- Model สำหรับ Database ---
class PlayerCountEntry(db.Model):
    """โมเดลสำหรับบันทึกจำนวนผู้เล่น ณ เวลาหนึ่ง"""
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    player_count = db.Column(db.Integer, nullable=False)
    
    def __repr__(self):
        return f'<Entry {self.player_count} at {self.timestamp}>'

# --- ฟังก์ชันหลักในการดึงข้อมูลและบันทึก ---
def fetch_and_save_data():
    """ดึงจำนวนผู้เล่นจาก Roblox API และบันทึกลงฐานข้อมูล"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Attempting to fetch data...")
    try:
        # ใช้ Exponential Backoff ในกรณีที่เกิดปัญหา Rate Limit
        max_retries = 3
        initial_delay = 1
        
        for attempt in range(max_retries):
            response = requests.get(ROBLOX_API_URL, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                player_count = 0
                
                if data.get('data') and len(data['data']) > 0:
                    game_data = data['data'][0]
                    player_count = game_data.get('playing', 0)
                    
                    # บันทึกข้อมูลลงใน Database
                    new_entry = PlayerCountEntry(player_count=player_count)
                    db.session.add(new_entry)
                    db.session.commit()
                    
                    print(f"SUCCESS: Saved Player Count: {player_count}")
                    return player_count
                
                print("WARNING: Data structure is not as expected. Skipping save.")
                return None
            
            elif response.status_code == 429:
                # HTTP 429: Too Many Requests (Rate Limit)
                delay = initial_delay * (2 ** attempt)
                print(f"RATE LIMIT hit (Attempt {attempt+1}/{max_retries}). Retrying in {delay}s.")
                time.sleep(delay)
                
            else:
                response.raise_for_status() # สำหรับ Error อื่นๆ ที่ไม่ใช่ 429
                
    except RequestException as e:
        print(f"ERROR: Failed to fetch data from Roblox API: {e}")
    except Exception as e:
        print(f"AN UNEXPECTED ERROR OCCURRED: {e}")

# --- การตั้งค่า Scheduler ---
def start_scheduler():
    """เริ่มต้น Scheduler เพื่อรันฟังก์ชันดึงข้อมูลอัตโนมัติ"""
    from apscheduler.schedulers.background import BackgroundScheduler
    
    # ใช้วิธี Context เพื่อให้ Scheduler สามารถเข้าถึง App Context ของ Flask
    def job_wrapper():
        with app.app_context():
            fetch_and_save_data()
            
    scheduler = BackgroundScheduler()
    # กำหนดให้รันฟังก์ชัน job_wrapper ทุกๆ 5 นาที (300 วินาที)
    scheduler.add_job(job_wrapper, 'interval', minutes=5, id='roblox_fetch_job')
    scheduler.start()
    print("Scheduler started: Fetching data every 5 minutes.")

# --- Routes (API Endpoints) ---

@app.route('/')
def index():
    """Route หลักสำหรับแสดงหน้า HTML"""
    # Flask จะค้นหาไฟล์ 'index.html' ในโฟลเดอร์ 'templates'
    return render_template('index.html')

@app.route('/api/summary')
def get_summary():
    """API ดึงข้อมูลสรุป (จำนวนผู้เล่นปัจจุบันและสูงสุด 24 ชั่วโมง)"""
    
    # 1. ดึงข้อมูลผู้เล่นปัจจุบัน (ล่าสุด)
    latest_entry = PlayerCountEntry.query.order_by(PlayerCountEntry.timestamp.desc()).first()
    current_players = latest_entry.player_count if latest_entry else 0

    # 2. ดึงข้อมูลสูงสุดใน 24 ชั่วโมง
    time_24_hours_ago = datetime.utcnow() - timedelta(hours=24)
    max_entry_24h = db.session.query(db.func.max(PlayerCountEntry.player_count)).filter(
        PlayerCountEntry.timestamp >= time_24_hours_ago
    ).scalar() or 0
    
    return jsonify({
        'game_name': GAME_NAME,
        'current_players': current_players,
        'max_players_24h': max_entry_24h
    })

@app.route('/api/analytics/historical')
def get_historical_data():
    """API ดึงข้อมูลย้อนหลังสำหรับแผนภูมิ (24 ชั่วโมงล่าสุด)"""
    
    time_24_hours_ago = datetime.utcnow() - timedelta(hours=24)
    
    # ดึงข้อมูล 24 ชั่วโมงล่าสุด
    entries = PlayerCountEntry.query.filter(
        PlayerCountEntry.timestamp >= time_24_hours_ago
    ).order_by(PlayerCountEntry.timestamp.asc()).all()
    
    # แปลงข้อมูลให้อยู่ในรูปแบบที่ Chart.js ต้องการ
    labels = []
    data = []
    
    for entry in entries:
        # ใช้รูปแบบเวลาที่กระชับขึ้นสำหรับแกน X
        labels.append(entry.timestamp.strftime('%H:%M')) 
        data.append(entry.player_count)
        
    return jsonify({
        'labels': labels,
        'data': data
    })

# --- การตั้งค่าเริ่มต้น ---
with app.app_context():
    # สร้างตารางใน Database หากยังไม่มี
    db.create_all()

# หากรันโดยตรง (เช่น python app.py) ให้เริ่มต้น Scheduler
if __name__ == '__main__':
    start_scheduler()
    app.run(debug=True, use_reloader=False) # ต้องตั้งค่า use_reloader=False เมื่อใช้ APScheduler
