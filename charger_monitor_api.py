import schedule
import time
import pandas as pd
from datetime import datetime
from telegram import Bot
import asyncio
import requests
import configparser
import os
import pytz
import re

# --- 設定時區常數 ---
TIMEZONE = pytz.timezone('Asia/Taipei')
BATCH_SIZE = 10

# --- 讀取設定檔 ---
config = configparser.ConfigParser()
CONFIG_FILE = 'config.ini'

if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError(f"❌ 找不到設定檔: {CONFIG_FILE}")

try:
    config.read(CONFIG_FILE)
    API_URL = config.get('API_CONFIG', 'API_URL')
    BEARER_TOKEN = config.get('API_CONFIG', 'AUTHORIZATION_TOKEN')
    TELEGRAM_BOT_TOKEN = config.get('TELEGRAM_CONFIG', 'TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHAT_ID = config.get('TELEGRAM_CONFIG', 'TELEGRAM_CHAT_ID')
    BASE_CSV_NAME = config.get('SYSTEM_CONFIG', 'CSV_FILE')
except Exception as e:
    print(f"❌ 讀取設定檔發生錯誤: {e}")
    exit()

# --- Request Session 設定 ---
session = requests.Session()
session.headers.update({
    'Authorization': f'Bearer {BEARER_TOKEN}',
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
})

# --- 全域變數 ---
last_known_status = {} # 格式: {'ID': {'status': '🟢 上線', 'time': datetime}}
is_first_run = True
consecutive_failures = 0
MAX_FAIL_THRESHOLD = 3

# 狀態對照表
STATUS_MAP = {
    'Available': '🟢 上線',
    'Preparing': '⚡ 準備充電',
    'Charging': '🔋 充電中',
    'SuspendedEVSE': '🚫 充電樁暫停',
    'SuspendedEV': '🚗 車端暫停',
    'Finishing': '🏁 完成充電',
    'Reserved': '🅿️ 佔用',
    'Unavailable': '⚫ 離線',
    'Faulted': '🔧 故障'
}

# --- 輔助函式 ---

def get_current_gmt8_time():
    return datetime.now(TIMEZONE)

def get_monthly_csv_path():
    """產生當月檔案路徑，例如：2025-12_charger_log.csv"""
    now = get_current_gmt8_time()
    month_prefix = now.strftime("%Y-%m")
    directory, filename = os.path.split(BASE_CSV_NAME)
    new_filename = f"{month_prefix}_{filename}"
    return os.path.join(directory, new_filename)

def escape_markdown_v2(text):
    if text is None: return ""
    return re.sub(r"([_\*\[\]\(\)~`>#\+\-=|\{\}\.!])", r"\\\1", str(text))

def format_duration(start_time, end_time):
    if not start_time: return "N/A"
    if start_time.tzinfo is None: start_time = TIMEZONE.localize(start_time)
    diff = end_time - start_time
    total_sec = int(diff.total_seconds())
    h, m = divmod(total_sec // 60, 60)
    d, h = divmod(h, 24)
    res = []
    if d > 0: res.append(f"{d}天")
    if h > 0: res.append(f"{h}時")
    res.append(f"{m}分")
    return "".join(res) if res else "0分"

async def send_telegram(message):
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='MarkdownV2')
    except Exception as e:
        print(f"❌ Telegram 發送失敗: {e}")

# --- 核心邏輯 ---

def get_charger_status():
    global consecutive_failures
    current_statuses = {}
    try:
        response = session.get(API_URL, timeout=15)
        response.raise_for_status()
        consecutive_failures = 0
        data = response.json()
        charger_points = data.get('data', [])
        for cp in charger_points:
            for connector in cp.get('connectors', []):
                cid = str(connector.get('deviceId'))
                status_raw = connector.get('detailedStatus')
                if cid and status_raw:
                    current_statuses[cid] = STATUS_MAP.get(status_raw, f"❓ {status_raw}")
        return current_statuses
    except Exception as e:
        consecutive_failures += 1
        print(f"❌ API 請求失敗 ({consecutive_failures}/{MAX_FAIL_THRESHOLD})")
        if consecutive_failures == MAX_FAIL_THRESHOLD:
            fail_alert = f"⚠️ *系統警報：API 請求持續失敗*\n\n`{escape_markdown_v2(str(e))}`"
            asyncio.run(send_telegram(fail_alert))
        return None

def check_and_report_status():
    global last_known_status, is_first_run

    now = get_current_gmt8_time()
    current_csv = get_monthly_csv_path()
    print(f"[{now.strftime('%H:%M:%S')}] 檢查中... (紀錄至: {os.path.basename(current_csv)})")

    current_statuses = get_charger_status()
    if current_statuses is None: return

    alerts = []
    new_status_memo = {}

    for cid, new_status in current_statuses.items():
        old_data = last_known_status.get(cid)
        old_status = old_data['status'] if old_data else None
        last_time = old_data['time'] if old_data else now

        if old_status != new_status:
            duration = format_duration(last_time, now)

            # 1. 寫入 CSV (所有變動都寫入)
            timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
            df = pd.DataFrame([{
                'Timestamp': timestamp_str, 
                'ChargerID': cid, 
                'OldStatus': old_status, 
                'NewStatus': new_status, 
                'Duration': duration
            }])
            # 使用 utf-8-sig 確保 Excel 開啟中文不亂碼
            df.to_csv(current_csv, mode='a', header=not os.path.exists(current_csv), index=False, encoding='utf-8-sig')

            # 2. Telegram 通知過濾邏輯
            # A. 變更為離線
            is_to_offline = (new_status == STATUS_MAP['Unavailable'])
            # B. 從離線恢復為上線
            is_back_online = (old_status == STATUS_MAP['Unavailable'] and new_status == STATUS_MAP['Available'])

            if not is_first_run and (is_to_offline or is_back_online):
                msg = (
                    f"🔌 ID: `{escape_markdown_v2(cid)}`\n"
                    f"⏱ 持續: `{escape_markdown_v2(duration)}` 後變動\n"
                    f"從 {escape_markdown_v2(old_status if old_status else 'N/A')}\n"
                    f"變更為 ➔ {escape_markdown_v2(new_status)}\n"
                    "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
                )
                alerts.append(msg)

            new_status_memo[cid] = {'status': new_status, 'time': now}
        else:
            new_status_memo[cid] = {'status': new_status, 'time': last_time}

    last_known_status = new_status_memo

    if alerts:
        header = f"📊 *設備狀態重要提醒* \\({escape_markdown_v2(now.strftime('%H:%M'))}\\)\n\n"
        for i in range(0, len(alerts), BATCH_SIZE):
            batch_msg = header + "".join(alerts[i:i+BATCH_SIZE])
            asyncio.run(send_telegram(batch_msg))
            time.sleep(1)

    is_first_run = False

def initialize():
    global last_known_status, is_first_run
    print("--- 系統初始化中 ---")
    
    # 尋找最新的 CSV 檔案來載入狀態 (跨月接續)
    directory = os.path.dirname(BASE_CSV_NAME) or '.'
    all_logs = sorted([f for f in os.listdir(directory) if f.endswith(os.path.basename(BASE_CSV_NAME))])
    
    if all_logs:
        latest_csv = os.path.join(directory, all_logs[-1])
        try:
            df = pd.read_csv(latest_csv)
            if not df.empty:
                latest_rows = df.sort_values('Timestamp').drop_duplicates(subset=['ChargerID'], keep='last')
                for _, row in latest_rows.iterrows():
                    l_time = datetime.strptime(row['Timestamp'], "%Y-%m-%d %H:%M:%S")
                    last_known_status[str(row['ChargerID'])] = {
                        'status': row['NewStatus'],
                        'time': TIMEZONE.localize(l_time)
                    }
                is_first_run = False
                print(f"ℹ️ 已從 {latest_csv} 載入 {len(last_known_status)} 筆狀態")
        except Exception as e:
            print(f"⚠️ 載入舊紀錄失敗: {e}")
            
    check_and_report_status()

if __name__ == "__main__":
    initialize()
    # 每 3 分鐘檢查一次
    schedule.every(3).minutes.do(check_and_report_status)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
