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

# --- 設定時區常數 ---
# 台灣/台北的時區就是 GMT+8
TIMEZONE = pytz.timezone('Asia/Taipei') 
# Telegram 訊息分批數量
BATCH_SIZE = 10 

# --- 獲取當前 GMT+8 時間的輔助函式 ---
def get_current_gmt8_time():
    """獲取當前 GMT+8 的 datetime 物件。"""
    return datetime.now(TIMEZONE)

# --- 讀取設定檔 ---
config = configparser.ConfigParser()
CONFIG_FILE = 'config.ini'

if not os.path.exists(CONFIG_FILE):
    raise FileNotFoundError(f"❌ 找不到設定檔: {CONFIG_FILE}。請創建此檔案並填入您的 Token 和 ID。")

try:
    config.read(CONFIG_FILE)
    
    API_URL = config.get('API_CONFIG', 'API_URL')
    BEARER_TOKEN = config.get('API_CONFIG', 'AUTHORIZATION_TOKEN')
    
    TELEGRAM_BOT_TOKEN = config.get('TELEGRAM_CONFIG', 'TELEGRAM_BOT_TOKEN')
    TELEGRAM_CHAT_ID = config.get('TELEGRAM_CONFIG', 'TELEGRAM_CHAT_ID')
    
    CSV_FILE = config.get('SYSTEM_CONFIG', 'CSV_FILE')

except configparser.Error as e:
    print(f"❌ 讀取設定檔發生錯誤: {e}")
    exit()

# --- API Headers ---
HEADERS = {
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip, deflate, br, zstd',
    'Accept-Language': 'en-US,en;q=0.9',
    'Authorization': f'Bearer {BEARER_TOKEN}',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    'Content-Type': 'application/json',
    'Host': 'tyap.ev2.com.tw',
    'Pragma': 'no-cache',
    'Referer': 'https://tyap.ev2.com.tw/device/chargingpoint-management',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'sec-ch-ua': '"Chromium";v="142", "Google Chrome";v="142", "Not_A Brand";v="99"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"'
}

# 全域變數
last_known_status = {}
is_first_run = True 

STATUS_MAP = {
    'Available': '上線',
    'Unavailable': '離線',
}

# --- 輔助函式: 嚴格轉義 ---
def escape_markdown_v2(text):
    """將 MarkdownV2 的保留符號進行轉義。"""
    # 這裡我們只轉義會導致解析錯誤的符號 (不包含 * 和 _，允許簡單粗體或斜體)
    reserved_chars = ['[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in reserved_chars:
        text = text.replace(char, '\\' + char)
    return text

# --- 函式: 實際 API 狀態檢查 ---

def get_charger_status():
    """實際呼叫 API，並解析複雜的 JSON 結構，抓取 connectors 中的 detailedStatus。"""
    current_statuses = {}
    
    try:
        response = requests.get(API_URL, headers=HEADERS, timeout=15)
        response.raise_for_status() 
        data = response.json()
        
        charger_points = data.get('data', []) 
        
        if not charger_points:
            print("⚠️ API 回傳數據中 'data' 欄位為空或不存在。")
            return current_statuses
        
        for cp in charger_points:
            connectors = cp.get('connectors', []) 
            
            for connector in connectors:
                connector_id = str(connector.get('deviceId')) 
                detailed_status = connector.get('detailedStatus')
                
                if connector_id and detailed_status:
                    mapped_status = STATUS_MAP.get(detailed_status, detailed_status)
                    current_statuses[connector_id] = mapped_status
                else:
                    print(f"⚠️ 略過無效的充電槍數據 (Connector ID 或 detailedStatus 缺失): {connector}")
                        
    except requests.exceptions.RequestException as e:
        print(f"❌ API 請求失敗: {e}")
    except Exception as e:
        print(f"❌ 處理 API 資料失敗: {e}")
        
    return current_statuses


# --- 函式: 紀錄與回報 ---

def log_status_change(charger_id, old_status, new_status):
    """將異動資料寫入 CSV 檔案。時間戳記使用 GMT+8。"""
    try:
        timestamp = get_current_gmt8_time().strftime("%Y-%m-%d %H:%M:%S")
        old_status_log = old_status if old_status is not None else 'INITIAL'
        
        new_data = pd.DataFrame([{
            'Timestamp': timestamp,
            'ChargerID': charger_id,
            'OldStatus': old_status_log,
            'NewStatus': new_status
        }])
        
        new_data.to_csv(CSV_FILE, mode='a', header=False, index=False, encoding='utf-8')
        print(f"✅ CSV 紀錄成功 [{timestamp}]: {charger_id} 從 {old_status_log} 變為 {new_status}")
        
    except Exception as e:
        print(f"❌ 寫入 CSV 失敗: {e}")

async def send_telegram_notification(message):
    """發送 Telegram 訊息。"""
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN) 
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='MarkdownV2') 
    except Exception as e:
        # 在這裡不再打印失敗，因為在主邏輯中已經打印了
        # 這裡主要是為了確保異步調用能夠捕獲到錯誤
        raise e 


# --- 主排程任務 ---

def check_and_report_status():
    """主要的檢查任務，每 3 分鐘執行一次。時間戳記使用 GMT+8。"""
    global last_known_status, is_first_run
    
    current_gmt8 = get_current_gmt8_time()
    current_time_str = current_gmt8.strftime('%H:%M:%S')
    
    print(f"\n[{current_time_str}] 執行檢查 (GMT+8)...")
    
    current_statuses = get_charger_status()
    
    if not current_statuses:
        print("❌ 無法取得當前狀態，跳過本次檢查。")
        return

    alerts_to_send = []
    newly_updated_status = current_statuses.copy()

    for charger_id, new_status in current_statuses.items():
        old_status = last_known_status.get(charger_id)
        
        is_change_detected = (old_status != new_status) or is_first_run and (old_status is None)

        if is_change_detected:
            
            is_alert = False
            alert_type = ""
            
            if is_first_run:
                is_alert = True
                alert_type = f"⭐️ 初始狀態 ({new_status})" 
            elif new_status == '離線':
                is_alert = True
                alert_type = "🚨 離線警報"
            elif old_status == '離線' and new_status == '上線':
                is_alert = True
                alert_type = "✅ 狀態恢復"
            
            if is_alert:
                # 觸發 CSV 記錄
                log_status_change(charger_id, old_status, new_status)
                
                # 準備 Telegram 訊息
                old_status_display = old_status if old_status is not None else 'N/A'
                
                # 轉義內容
                safe_alert_type = escape_markdown_v2(alert_type)
                safe_old_status = escape_markdown_v2(old_status_display)
                safe_new_status = escape_markdown_v2(new_status)
                
                # 組裝單條異動訊息：緊湊格式
                single_alert_message = (
                    f"{safe_alert_type} 🔌 充電槍 ID: `{charger_id}`\n"
                    f"  \\- 舊狀態: `{safe_old_status}`\n"
                    f"  \\- 新狀態: `{safe_new_status}`\n"
                    "\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\-\\n"
                )
                alerts_to_send.append(single_alert_message)

    # 4. 更新全域狀態
    last_known_status = newly_updated_status
    
    # 5. 分批發送 Telegram 通知
    if alerts_to_send:
        
        # 修正重點：精簡標題
        time_part = current_gmt8.strftime('%H\\:%M')
        
        if is_first_run:
            # 📢 系統啟動報告 (18:15)
            telegram_title = f"📢 **系統啟動報告** \\({time_part}\\)" 
        else:
            # 🚨 狀態異動報告 (18:15)
            telegram_title = f"🚨 **狀態異動報告** \\({time_part}\\)" 

        # 分批發送
        print(f"ℹ️ 偵測到 {len(alerts_to_send)} 筆異動，將分批 ({BATCH_SIZE} 筆/批) 發送。")
        
        for i in range(0, len(alerts_to_send), BATCH_SIZE):
            batch = alerts_to_send[i:i + BATCH_SIZE]
            
            # 計算批次編號並轉義括號
            batch_index = int(i/BATCH_SIZE) + 1
            total_batches = int((len(alerts_to_send) + BATCH_SIZE - 1) / BATCH_SIZE)
            batch_info_escaped = f"\\(批次 {batch_index}/{total_batches}\\)"
            
            # 建立單批訊息：緊湊格式，一個換行符
            batch_message = (
                f"{telegram_title} {batch_info_escaped}\n" 
                f"{''.join(batch)}"
            )
            
            try:
                asyncio.run(send_telegram_notification(batch_message))
                time.sleep(1) # 為了避免 API 限制，每發送一批休息 1 秒鐘 
            except Exception as e:
                 print(f"❌ Telegram 發送失敗。請檢查 Token 和 Chat ID。錯誤: {e}")
                 break # 如果發送失敗，停止後續批次發送
            
    else:
        print("ℹ️ 未偵測到需要回報的狀態異動。")
        
    is_first_run = False


# --- 啟動函式 ---

def initialize():
    """程式啟動時的初始化設置。"""
    global last_known_status, is_first_run
    
    print("--- 充電樁監控程式啟動 ---")
    print(f"ℹ️ 讀取設定檔 {CONFIG_FILE} 成功。")
    
    # 檢查並載入歷史狀態
    try:
        df = pd.read_csv(CSV_FILE, encoding='utf-8')
        if not df.empty:
            latest_records = df.sort_values('Timestamp').drop_duplicates(subset=['ChargerID'], keep='last')
            last_known_status = latest_records.set_index('ChargerID')['NewStatus'].to_dict()
            print(f"ℹ️ 載入上次狀態成功: {last_known_status}")
            is_first_run = False
        else:
            print("ℹ️ CSV 檔案為空，將視為首次偵測。")
            is_first_run = True

    except FileNotFoundError:
        print(f"ℹ️ 未找到 CSV 檔案 '{CSV_FILE}'，將創建新檔案並視為首次偵測。")
        initial_df = pd.DataFrame(columns=['Timestamp', 'ChargerID', 'OldStatus', 'NewStatus'])
        initial_df.to_csv(CSV_FILE, index=False, encoding='utf-8')
        is_first_run = True
        
    except Exception as e:
        print(f"❌ 讀取 CSV 發生錯誤: {e}。將視為首次偵測。")
        is_first_run = True
    
    # 立即執行第一次檢查。
    check_and_report_status()


# --- 主程式區塊 ---

if __name__ == "__main__":
    
    initialize()
    
    # 設定排程：每隔 3 分鐘執行一次 check_and_report_status
    schedule.every(3).minutes.do(check_and_report_status)
    print("--- 排程器已啟動，每 3 分鐘檢查一次 ---")
    
    # 主迴圈
    while True:
        schedule.run_pending()
        time.sleep(1)