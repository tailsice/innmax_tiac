import schedule
import time
import pandas as pd
from datetime import datetime, timedelta
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
    CSV_FILE = config.get('SYSTEM_CONFIG', 'CSV_FILE')
except Exception as e:
    print(f"❌ 讀取設定檔發生錯誤: {e}")
    exit()

# --- Request 優化方案: 使用 Session ---
session = requests.Session()
session.headers.update({
    'Authorization': f'Bearer {BEARER_TOKEN}',
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
})

# --- 全域變數 ---
last_known_status = {} 
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

# --- 新增：定義需要發送 Telegram 的狀態清單 ---
# 只有當新狀態是這些時，才會發出通知
NOTIFY_STATUSES = [STATUS_MAP['Available'], STATUS_MAP['Unavailable']]

# --- 輔助函式 ---

def get_current_gmt8_time():
    return datetime.now(TIMEZONE)

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
    return "".join(res)

async def send_telegram(message):
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='MarkdownV2')
    except Exception as e:
        print(f"❌ Telegram 發送失敗: {e}")

# --- API 檢查邏輯 ---

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
        if consecutive_failures == MAX_FAIL_THRESHOLD:
            fail_alert = f"⚠️ *系統警報：API 請求持續失敗*\n\n`{escape_markdown_v2(str(e))}`"
            asyncio.run(send_telegram(fail_alert))
        return None

def check_and_report_status():
    global last_known_status, is_first_run

    now = get_current_gmt8_time()
    print(f"[{now.strftime('%H:%M:%S')}] 開始檢查...")

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

            # 【邏輯 1】無論是什麼狀態變動，一律寫入 CSV
            timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
            df = pd.DataFrame([{'Timestamp': timestamp_str, 'ChargerID': cid, 'OldStatus': old_status, 'NewStatus': new_status, 'Duration': duration}])
            df.to_csv(CSV_FILE, mode='a', header=not os.path.exists(CSV_FILE), index=False, encoding='utf-8')

            # 【邏輯 2】篩選發送 Telegram 的條件
            # 1. 不是第一次執行 (避免重啟時洗版)
            # 2. 新狀態必須是「上線」或「離線」
            if not is_first_run and new_status in NOTIFY_STATUSES:
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
        header = f"📊 *重要狀態變更* \\({escape_markdown_v2(now.strftime('%H:%M'))}\\)\n\n"
        for i in range(0, len(alerts), BATCH_SIZE):
            batch_msg = header + "".join(alerts[i:i+BATCH_SIZE])
            asyncio.run(send_telegram(batch_msg))
            time.sleep(1)

    is_first_run = False
    print("✅ 檢查完成。")

def initialize():
    global last_known_status, is_first_run
    print("--- 系統初始化中 ---")
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE)
            if not df.empty:
                latest = df.sort_values('Timestamp').drop_duplicates(subset=['ChargerID'], keep='last')
                for _, row in latest.iterrows():
                    l_time = datetime.strptime(row['Timestamp'], "%Y-%m-%d %H:%M:%S")
                    last_known_status[str(row['ChargerID'])] = {
                        'status': row['NewStatus'],
                        'time': TIMEZONE.localize(l_time)
                    }
                is_first_run = False
        except: pass
    check_and_report_status()

if __name__ == "__main__":
    initialize()
    schedule.every(3).minutes.do(check_and_report_status)
    while True:
        schedule.run_pending()
        time.sleep(1)
