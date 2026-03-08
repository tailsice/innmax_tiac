import requests
import xml.etree.ElementTree as ET
import json
import time
from datetime import datetime
import schedule
import csv
import re
import html
import configparser
import os

# --------------------------
# 讀取設定檔
# --------------------------
config = configparser.ConfigParser()
config.read('config_tnn.ini', encoding='utf-8')

BOT_TOKEN = config.get('TELEGRAM', 'BOT_TOKEN')
CHAT_ID = config.get('TELEGRAM', 'CHAT_ID')
API_URL = config.get('API', 'URL')
STATION_CSV = config.get('FILES', 'STATION_CSV')
STATE_FILE = config.get('FILES', 'STATE_JSON')

def send_telegram_messages_in_batches(conns, batch_size=10, msg_type="新增離線"):
    """
    將連線狀態列表拆成一批發送
    """
    for i in range(0, len(conns), batch_size):
        batch = conns[i:i+batch_size]
        msg_text = f"{'⚠️' if msg_type=='新增離線' else '✅'} {msg_type} ({len(batch)} 支)\n"
        for conn in batch:
            station_simple = simplify_station_id(conn["StationID"])
            connector_simple = simplify_connector_id(conn["ConnectorID"])
            last_update = conn.get("LastUpdateTime", "")
            msg_text += f"- {conn['StationName']} ({station_simple}), 槍號: {connector_simple}, 最後更新: {last_update}\n"

        safe_text = html.escape(msg_text)
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        payload = {
            'chat_id': CHAT_ID,
            'text': safe_text,
            'parse_mode': 'HTML'
        }
        try:
            resp = requests.post(url, data=payload, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            print(f"❌ Telegram 發送失敗: {e}")

# --------------------------
# CSV & JSON 處理
# --------------------------
def load_previous_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {}

def save_current_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

def save_message_to_csv(entries):
    if not entries:
        return

    # 優化項目：每月自動產生一份 CSV
    now = datetime.now()
    month_str = now.strftime("%Y-%m")
    filename = f"ev_status_{month_str}_tnn.csv"

    fieldnames = ["Timestamp", "Type", "StationID", "StationName", "ConnectorID", "ConnectorType", "LastUpdateTime", "Message"]

    file_exists = os.path.isfile(filename)
    with open(filename, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for entry in entries:
            writer.writerow(entry)

# --------------------------
# 讀取站點資料 (移除 Operation 判斷)
# --------------------------
def load_station_csv(filename):
    station_dict = {}
    if not os.path.exists(filename):
        print(f"❌ 找不到站點檔: {filename}")
        return station_dict

    with open(filename, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items()}
            station_id = row['StationID']
            station_name = row.get('StationName', '')
            # 不再判斷 operation，只要 ID 在檔案內就記錄
            station_dict[station_id] = station_name
    return station_dict

# --------------------------
# 解析與 ID 處理
# --------------------------
def simplify_station_id(station_id):
    match = re.search(r'([0-9]+)$', station_id)
    return match.group(1) if match else station_id

def simplify_connector_id(connector_id):
    match = re.search(r'([0-9]+[A-Z]*)$', connector_id)
    return match.group(1) if match else connector_id

def parse_offline_connectors(xml_text, station_dict):
    ns = {'ns': 'https://traffic.transportdata.tw/standard/EVStation/schema/'}
    root = ET.fromstring(xml_text)
    offline_list = []

    for live_status in root.findall(".//ns:LiveStatus", ns):
        connector_status = live_status.findtext("ns:ConnectorStatus", namespaces=ns)
        if connector_status != "3":  # 只抓離線
            continue

        station_id = live_status.findtext("ns:StationID", namespaces=ns)
        station_name = station_dict.get(station_id)
        
        if station_name:
            offline_list.append({
                "StationID": station_id,
                "StationName": station_name,
                "ConnectorID": live_status.findtext("ns:ConnectorID", namespaces=ns),
                "ConnectorType": live_status.findtext("ns:ConnectorType", namespaces=ns),
                "LastUpdateTime": live_status.findtext("ns:LastUpdateTime", namespaces=ns),
            })
    return offline_list

# --------------------------
# 比對狀態 & 通知
# --------------------------
def compare_and_notify(current_list):
    previous_state = load_previous_state()
    current_state = {f"{c['StationID']}_{c['ConnectorID']}": c for c in current_list}

    new_offline = [conn for key, conn in current_state.items() if key not in previous_state]
    restored = [conn for key, conn in previous_state.items() if key not in current_state]

    csv_entries = []

    if new_offline:
        send_telegram_messages_in_batches(new_offline, batch_size=10, msg_type="新增離線")
        for conn in new_offline:
            station_simple = simplify_station_id(conn["StationID"])
            connector_simple = simplify_connector_id(conn["ConnectorID"])
            csv_entries.append({
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Type": "新增離線",
                "StationID": station_simple,
                "StationName": conn["StationName"],
                "ConnectorID": connector_simple,
                "ConnectorType": conn.get("ConnectorType", ""),
                "LastUpdateTime": conn.get("LastUpdateTime", ""),
                "Message": f"- {conn['StationName']} ({station_simple}), 槍號: {connector_simple}"
            })

    if restored:
        send_telegram_messages_in_batches(restored, batch_size=10, msg_type="已恢復")
        for conn in restored:
            station_simple = simplify_station_id(conn["StationID"])
            connector_simple = simplify_connector_id(conn["ConnectorID"])
            csv_entries.append({
                "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Type": "已恢復",
                "StationID": station_simple,
                "StationName": conn["StationName"],
                "ConnectorID": connector_simple,
                "ConnectorType": conn.get("ConnectorType", ""),
                "LastUpdateTime": conn.get("LastUpdateTime", ""),
                "Message": f"- {conn['StationName']} ({station_simple}), 槍號: {connector_simple}"
            })

    save_message_to_csv(csv_entries)
    save_current_state(current_state)

# --------------------------
# 主任務
# --------------------------
def job():
    station_dict = load_station_csv(STATION_CSV)
    try:
        resp = requests.get(API_URL, timeout=15)
        resp.raise_for_status()
        xml_text = resp.text
        
        offline_connectors = parse_offline_connectors(xml_text, station_dict)
        compare_and_notify(offline_connectors)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 台南監控檢查完成，目前離線: {len(offline_connectors)}")
        
    except Exception as e:
        print(f"❌ API 執行錯誤：{e}")

# --------------------------
# 啟動排程
# --------------------------
schedule.every(3).minutes.do(job)

print("🔹 台南 EV 充電槍監控啟動 (每 3 分鐘檢查一次)...")
job() # 啟動時先跑一次

while True:
    try:
        schedule.run_pending()
    except Exception as e:
        print(f"排程崩潰: {e}")
    time.sleep(1)
