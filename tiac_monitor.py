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
import sys

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
    # 從 API_CONFIG 讀取連線資訊
    API_URL = config.get('API_CONFIG', 'API_URL')
    BEARER_TOKEN = config.get('API_CONFIG', 'AUTHORIZATION_TOKEN')
    # 從 TELEGRAM_CONFIG 讀取 Bot 資訊
    TELEGRAM_BOT_TOKEN = config.get('TELEGRAM_CONFIG', 'TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHAT_ID = config.get('TELEGRAM_CONFIG', 'TELEGRAM_CHAT_ID')
    # 從 SYSTEM_CONFIG 讀取 CSV 檔名
    BASE_CSV_NAME = config.get('SYSTEM_CONFIG', 'CSV_FILE')
except Exception as e:
    print(f"❌ 讀取設定檔發生錯誤: {e}")
    sys.exit()

# --- Request Session 設定 ---
session = requests.Session()
session.headers.update({
    'Authorization': f'Bearer {BEARER_TOKEN}',
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'
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

# --- 輔助函式 ---

def get_current_gmt8_time():
    return datetime.now(TIMEZONE)

def get_monthly_csv_path():
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

def send_hourly_summary():
    """整點及啟動時發送所有充電樁狀態摘要"""
    now = get_current_gmt8_time()
    if not last_known_status:
        return

    # 分類統計 (包含 Finishing)
    summary = {
        STATUS_MAP['Available']: [],
        STATUS_MAP['Charging']: [],
        STATUS_MAP['Finishing']: [],
        STATUS_MAP['Unavailable']: [],
        'Other': []
    }

    for cid, data in last_known_status.items():
        status = data['status']
        if status == STATUS_MAP['Available']:
            summary[STATUS_MAP['Available']].append(cid)
        elif status == STATUS_MAP['Charging']:
            summary[STATUS_MAP['Charging']].append(cid)
        elif status == STATUS_MAP['Finishing']:
            summary[STATUS_MAP['Finishing']].append(cid)
        elif status == STATUS_MAP['Unavailable']:
            summary[STATUS_MAP['Unavailable']].append(cid)
        else:
            summary['Other'].append(f"{cid}({status})")

    msg = f"📊 *充電樁狀態總結報表* \\({escape_markdown_v2(now.strftime('%H:%M'))}\\)\n\n"
    msg += f"🟢 可使用: `{len(summary[STATUS_MAP['Available']])}`\n"
    msg += f"🔋 充電中: `{len(summary[STATUS_MAP['Charging']])}`\n"
    msg += f"🏁 佔用中: `{len(summary[STATUS_MAP['Finishing']])}`\n"
    msg += f"⚫ 離線中: `{len(summary[STATUS_MAP['Unavailable']])}`\n"

    if summary[STATUS_MAP['Unavailable']]:
        msg += f"\n❌ *離線清單:* `{escape_markdown_v2(', '.join(summary[STATUS_MAP['Unavailable']]))}`"

    msg += f"\n\n✅ 系統運行中"
    asyncio.run(send_telegram(msg))

def check_and_report_status():
    global last_known_status, is_first_run

    now = get_current_gmt8_time()
    current_csv = get_monthly_csv_path()

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

            # 1. 寫入 CSV (不論狀態皆記錄變動)
            timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
            df = pd.DataFrame([{
                'Timestamp': timestamp_str, 'ChargerID': cid,
                'OldStatus': old_status, 'NewStatus': new_status, 'Duration': duration
            }])
            df.to_csv(current_csv, mode='a', header=not os.path.exists(current_csv), index=False, encoding='utf-8-sig')

            # 2. Telegram 通知過濾邏輯：僅限離線或離線復原
            is_to_offline = (new_status == STATUS_MAP['Unavailable'])
            was_offline = (old_status == STATUS_MAP['Unavailable'])

            if not is_first_run and (is_to_offline or was_offline):
                emoji = "⚠️ 設備離線" if is_to_offline else "✅ 設備恢復"
                msg = (
                    f"{emoji}\n"
                    f"🔌 ID: `{escape_markdown_v2(cid)}`\n"
                    f"⏱ 持續時間: `{escape_markdown_v2(duration)}` \n"
                    f"狀態: {escape_markdown_v2(old_status if old_status else 'N/A')} ➔ {escape_markdown_v2(new_status)}\n"
                    "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
                )
                alerts.append(msg)

            new_status_memo[cid] = {'status': new_status, 'time': now}
        else:
            new_status_memo[cid] = {'status': new_status, 'time': last_time}

    last_known_status = new_status_memo

    if alerts:
        header = f"🚨 *連線異常變動通知* \\({escape_markdown_v2(now.strftime('%H:%M'))}\\)\n\n"
        for i in range(0, len(alerts), BATCH_SIZE):
            batch_msg = header + "".join(alerts[i:i+BATCH_SIZE])
            asyncio.run(send_telegram(batch_msg))
            time.sleep(1)

    is_first_run = False

def initialize():
    global last_known_status, is_first_run
    print("--- 系統初始化中 ---")

    directory = os.path.dirname(BASE_CSV_NAME) or '.'
    if not os.path.exists(directory):
        os.makedirs(directory)

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
                print(f"ℹ️ 已從舊紀錄載入 {len(last_known_status)} 筆狀態")
        except Exception as e:
            print(f"⚠️ 載入舊紀錄失敗: {e}")

    # 執行第一次 API 獲取
    check_and_report_status()

if __name__ == "__main__":
    # 執行初始化與第一次數據抓取
    initialize()

    # --- 啟動立即發送一次報表 ---
    print(f"[{get_current_gmt8_time().strftime('%H:%M:%S')}] 📋 發送啟動首發狀態報表...")
    send_hourly_summary()

    # 排程任務
    # 每 3 分鐘檢查是否有設備斷線或復原
    schedule.every(3).minutes.do(check_and_report_status)

    # 每整點發送彙整統計
    schedule.every().hour.at(":00").do(send_hourly_summary)

    print(f"🚀 監控服務已就緒 (每3分監控離線 / 每小時發送報表)")

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        print(f"\n🛑 監控服務已手動停止")
