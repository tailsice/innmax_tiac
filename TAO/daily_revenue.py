import sys
import time
import requests
import configparser
import os
import schedule
from datetime import datetime, timedelta
from collections import Counter

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')

# 用於存放已查詢過的 sessionId 名稱，避免重複請求 API
name_cache = {}

def load_config():
    config = configparser.ConfigParser()
    config.optionxform = str
    if not os.path.exists(CONFIG_FILE):
        sys.stderr.write(f"❌ 找不到設定檔: {CONFIG_FILE}\n")
        sys.exit(1)
    config.read(CONFIG_FILE, encoding='utf-8')
    return config

def send_telegram(message, chat_id, bot_token):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for i in range(3):
        try:
            resp = requests.post(url, json={
                'chat_id': chat_id, 
                'text': message, 
                'parse_mode': 'HTML'
            }, timeout=15)
            resp.raise_for_status()
            return True
        except Exception as e:
            sys.stderr.write(f"TG 重試 {i+1}/3: {e}\n")
            time.sleep(5)
    return False

def get_station_name_by_api(sid, config, headers):
    """根據 sessionId 呼叫 API 取得中文名稱"""
    # 如果快取中已有，直接回傳
    if sid in name_cache:
        return name_cache[sid]
    
    try:
        base_url = config['API']['BASE_URL']
        # 依照使用者提供之 API 路徑：/sessionInfo/parkingLot/{id}
        url = f"{base_url}/sessionInfo/parkingLot/{sid}"
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        
        name = resp.json().get('data', {}).get('name')
        if name:
            name_cache[sid] = name # 存入快取
            return name
    except Exception as e:
        sys.stderr.write(f"讀取站點名稱失敗 (ID: {sid}): {e}\n")
    
    return f"站點({sid})" # 若失敗則回傳 ID 代替

def get_yesterday_range():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    return int(yesterday.timestamp() * 1000), int(today.timestamp() * 1000) - 1, yesterday.strftime("%Y/%m/%d")

def fetch_and_report(sid_group, group_name, target_chat_id, config):
    """處理特定 sessionId 區域的任務"""
    print(f"[{datetime.now()}] >>> 開始執行 {group_name} 營運統計...")
    
    auth_headers = {
        'User-Agent': config['AUTH']['UserAgent'],
        'Content-Type': 'application/json',
        'Authorization': config['AUTH']['AUTH_TOKEN']
    }
    
    start_at, end_at, date_str = get_yesterday_range()
    base_url = config['API']['BASE_URL']
    query_params = config['API']['QueryParams'] + f"&sessionId={sid_group}"
    
    raw_orders = []
    page = 0
    size = 100
    
    try:
        while True:
            url = f"{base_url}/order/{page}/{size}"
            full_url = f"{url}?{query_params}&startAt={start_at}&endAt={end_at}&reactQueryCache={int(time.time()*1000)}"
            
            resp = requests.get(full_url, headers=auth_headers, timeout=int(config['SYSTEM']['Timeout']))
            resp.raise_for_status()
            res_json = resp.json()
            
            raw_orders.extend(res_json.get('data', []))
            if page >= res_json.get('totalPages', 1) - 1:
                break
            page += 1

        # 1. 訂單去重與金額過濾
        unique_orders = {}
        for o in raw_orders:
            oid = o.get('orderId') or o.get('id')
            if oid:
                unique_orders[oid] = o

        valid_orders = [o for o in unique_orders.values() if o.get('paymentAmount', 0) > 0]
        
        total_count = len(valid_orders)
        total_income = sum(o.get('paymentAmount', 0) for o in valid_orders)

        # 2. 統計 Top 5 熱點 (統計訂單內的 sessionId)
        # 這裡的 sessionId 是指具體停車場的細項 ID
        id_counter = Counter([o.get('sessionId') for o in valid_orders if o.get('sessionId')])
        top_5_raw = id_counter.most_common(5)

        # --- 格式組建 ---
        msg = [
            f"<b>📊 {group_name} 充電營運日報 ({date_str})</b>",
            f"━━━━━━━━━━━━━━",
            f"✅ 總充電訂單：<b>{total_count}</b> 筆 (已去重)",
            f"💰 總營收金額：<b>${total_income:,}</b> 元",
            f"\n🔥 <b>前五大熱門站點：</b>"
        ]
        
        for rank, (id_val, count_val) in enumerate(top_5_raw, 1):
            # 將 ID 轉換為中文名稱
            cn_name = get_station_name_by_api(id_val, config, auth_headers)
            msg.append(f"{rank}. {cn_name}: <b>{count_val}</b> 次")
        
        report = "\n".join(msg)
        print(report)
        send_telegram(report, target_chat_id, config['TELEGRAM']['BOT_TOKEN'])

    except Exception as e:
        err = f"❌ {group_name} 執行失敗: {str(e)}"
        sys.stderr.write(f"[{datetime.now()}] " + err + "\n")
        send_telegram(err, target_chat_id, config['TELEGRAM']['BOT_TOKEN'])

def main_job():
    global name_cache
    name_cache = {} # 每次大任務開始前清空快取，確保名稱更新
    
    config = load_config()
    station_list = config['STATIONS']['List'].split(',')
    
    for sid in station_list:
        sid = sid.strip()
        section_key = f"STATION_{sid}"
        if section_key in config:
            name = config[section_key]['Name']
            chat_id = config[section_key]['ChatId']
            fetch_and_report(sid, name, chat_id, config)
            time.sleep(2)

if __name__ == "__main__":
    main_job()
    schedule.every().day.at("06:00").do(main_job)
    print(f"[{datetime.now()}] 每日營收報表系統啟動，每日 06:00 執行。")
    
    while True:
        schedule.run_pending()
        time.sleep(30)
