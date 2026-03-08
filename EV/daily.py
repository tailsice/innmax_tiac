import requests
import xml.etree.ElementTree as ET
import time
import re
import configparser
import os
import schedule
import csv
from datetime import datetime

# --------------------------
# 1. 讀取 Config 設定
# --------------------------
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')

try:
    TG_TOKEN = config.get('TELEGRAM', 'BOT_TOKEN')
    TG_CHAT_ID = config.get('TELEGRAM', 'CHAT_ID')
    SLACK_TOKEN = config.get('Slack', 'BOT_TOKEN')
    SLACK_CHANNEL = config.get('Slack', 'CHANNEL_ID')
    
    # 建立地區配置地圖 (包含 API 與對應的 CSV 檔名)
    REGION_MAP = {
        "桃園": {
            "api": config.get('API', 'URL_TAO'),
            "csv": config.get('FILES', 'STATION_TAO')
        },
        "台南": {
            "api": config.get('API', 'URL_TNN'),
            "csv": config.get('FILES', 'STATION_TNN')
        }
    }
except Exception as e:
    print(f"❌ 設定檔讀取失敗: {e}")
    exit()

# --------------------------
# 2. 通訊發送函式 (保持不變)
# --------------------------

def send_to_slack(message):
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json"}
    slack_text = message.replace("<b>", "*").replace("</b>", "*")
    payload = {"channel": SLACK_CHANNEL, "text": slack_text}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if not resp.json().get("ok"): print(f"⚠️ Slack 發送失敗: {resp.json().get('error')}")
    except Exception as e: print(f"❌ Slack 連線異常: {e}")

def send_to_telegram(message):
    url = f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage'
    MAX_LENGTH = 4000
    chunks = [message[i:i+MAX_LENGTH] for i in range(0, len(message), MAX_LENGTH)] if len(message) > MAX_LENGTH else [message]

    for i, msg in enumerate(chunks):
        text = f"{msg}\n\n(第 {i+1}/{len(chunks)} 頁)" if len(chunks) > 1 else msg
        payload = {'chat_id': TG_CHAT_ID, 'text': text, 'parse_mode': 'HTML'}
        try:
            requests.post(url, json=payload, timeout=15).raise_for_status()
            if len(chunks) > 1: time.sleep(0.5) 
        except Exception as e: print(f"❌ Telegram 發送失敗: {e}")

def broadcast(message):
    send_to_telegram(message)
    send_to_slack(message)

# --------------------------
# 3. 數據處理輔助 (新增參數化路徑)
# --------------------------

def simplify_connector_id(connector_id):
    match = re.search(r'([0-9]+[A-Z]*)$', str(connector_id))
    return match.group(1) if match else connector_id

def get_connector_type_label(connector_id, connector_type):
    id_str = str(connector_id).upper()
    if id_str.endswith('AC'): return "AC"
    if id_str.endswith('DC'): return "DC"
    return "AC" if str(connector_type) in ['1', '2', '3'] or 'AC' in str(connector_type).upper() else "DC"

def load_operating_stations(csv_filename):
    """ 動態載入指定地區的 CSV """
    station_dict = {}
    if not os.path.exists(csv_filename):
        print(f"⚠️ 找不到檔案: {csv_filename}")
        return station_dict
    with open(csv_filename, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items()}
            if row.get('StationID') and row.get('Operation') == '1':
                station_dict[row['StationID']] = row.get('StationName', '未知站點')
    return station_dict

# --------------------------
# 4. 核心監控邏輯
# --------------------------

def check_region_status(region_name, config_data):
    api_url = config_data['api']
    csv_file = config_data['csv']
    
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 檢查 {region_name} (使用 {csv_file})...")
    
    station_dict = load_operating_stations(csv_file)
    if not station_dict:
        print(f"⚠️ {region_name} 無營運中站點。")
        return

    try:
        resp = requests.get(api_url, timeout=25)
        resp.raise_for_status()
        ns = {'ns': 'https://traffic.transportdata.tw/standard/EVStation/schema/'}
        root = ET.fromstring(resp.text)
        
        active_stations = set()
        total_connectors = 0
        counts = {"正常": 0, "離線": 0, "故障": 0}
        issues = []

        for live_status in root.findall(".//ns:LiveStatus", ns):
            s_id = live_status.findtext("ns:StationID", namespaces=ns)
            if s_id in station_dict:
                active_stations.add(s_id)
                total_connectors += 1
                status_code = live_status.findtext("ns:ConnectorStatus", namespaces=ns)
                conn_id = live_status.findtext("ns:ConnectorID", namespaces=ns)
                
                type_label = get_connector_type_label(conn_id, live_status.findtext("ns:ConnectorType", namespaces=ns))
                s_name = station_dict[s_id]
                simple_conn_id = simplify_connector_id(conn_id)

                if status_code in ["1", "2"]:
                    counts["正常"] += 1
                elif status_code == "3":
                    counts["離線"] += 1
                    issues.append(f"• {s_name} 1{type_label} ({simple_conn_id})")
                else:
                    counts["故障"] += 1
                    issues.append(f"• {s_name} 1{type_label} ({simple_conn_id})")

        report = [
            f"<b>{region_name}回報</b>",
            f"已啟用：{len(active_stations)}站 {total_connectors}槍",
            f"狀態：正常:{counts['正常']} | 離線:{counts['離線']} | 故障:{counts['故障']}",
            "問題清單："
        ]
        report.extend(issues if issues else ["目前營運站點全部正常"])
        
        broadcast("\n".join(report))

    except Exception as e:
        broadcast(f"❌ <b>{region_name} 執行錯誤</b>: {str(e)[:200]}")

def job():
    for region, data in REGION_MAP.items():
        check_region_status(region, data)
        time.sleep(2)

# --------------------------
# 5. 啟動排程
# --------------------------
if __name__ == "__main__":
    print(f"🔹 雙地區監控啟動 (多檔案模式)")
    job()
    schedule.every().hour.at(":00").do(job)

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"排程系統異常: {e}")
        time.sleep(1)
