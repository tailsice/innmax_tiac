import requests
import xml.etree.ElementTree as ET
import json
import time
from datetime import datetime
import schedule
import csv
import re
import configparser
import os

# --------------------------
# 1. 讀取 Config 設定
# --------------------------
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')

try:
    BOT_TOKEN = config.get('TELEGRAM', 'BOT_TOKEN')
    CHAT_ID = config.get('TELEGRAM', 'CHAT_ID')
    API_URL = config.get('API', 'URL')
    STATION_CSV = config.get('FILES', 'STATION_CSV')
except Exception as e:
    print(f"❌ 設定檔讀取失敗: {e}")
    exit()

# --------------------------
# 2. 輔助處理函數
# --------------------------
def simplify_connector_id(connector_id):
    """ 提取槍號 ID (如 320167AC) """
    match = re.search(r'([0-9]+[A-Z]*)$', str(connector_id))
    return match.group(1) if match else connector_id

def get_connector_type_label(connector_id, connector_type):
    """ 根據 ID 結尾判斷 AC/DC (修正誤判邏輯) """
    id_str = str(connector_id).upper()
    if id_str.endswith('AC'):
        return "AC"
    elif id_str.endswith('DC'):
        return "DC"
    t = str(connector_type)
    if t in ['1', '2', '3'] or 'AC' in t.upper():
        return "AC"
    return "DC"

def load_operating_stations(filename):
    """ 只讀取 Operation 為 1 的站點 """
    station_dict = {}
    if not os.path.exists(filename):
        return station_dict
    with open(filename, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items()}
            s_id = row.get('StationID')
            s_name = row.get('StationName', '未知站點')
            operation = row.get('Operation', '0')
            if s_id and operation == '1':
                station_dict[s_id] = s_name
    return station_dict

def send_telegram_report(message):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    payload = {'chat_id': CHAT_ID, 'text': message}
    try:
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Telegram 發送失敗: {e}")

# --------------------------
# 3. 核心邏輯
# --------------------------
def job():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 執行每日定時報表檢查...")
    station_dict = load_operating_stations(STATION_CSV)
    if not station_dict:
        print("⚠️ 無營運中站點。")
        return

    try:
        resp = requests.get(API_URL, timeout=20)
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
                conn_type = live_status.findtext("ns:ConnectorType", namespaces=ns)
                
                type_label = get_connector_type_label(conn_id, conn_type)
                s_name = station_dict[s_id]
                simple_conn_id = simplify_connector_id(conn_id)

                if status_code in ["1", "2"]:
                    counts["正常"] += 1
                elif status_code == "3":
                    counts["離線"] += 1
                    issues.append(f"{s_name} 1{type_label} ({simple_conn_id})")
                else:
                    counts["故障"] += 1
                    issues.append(f"{s_name} 1{type_label} ({simple_conn_id})")

        report_lines = [
            "桃園回報",
            f"已啟用站點數：{len(active_stations)}站{total_connectors}槍",
            f"充電椿情況：{counts['正常']}正常 {counts['離線']}離線 {counts['故障']}故障",
            "問題回報："
        ]
        if issues:
            report_lines.extend(issues)
        else:
            report_lines.append("目前營運站點全部正常")

        final_message = "\n".join(report_lines)
        print("-" * 30)
        print(final_message)
        send_telegram_report(final_message)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 報表發送完成。")

    except Exception as e:
        print(f"❌ 執行錯誤: {e}")

# --------------------------
# 4. 啟動排程 (啟動執行 + 每日 09:00)
# --------------------------
if __name__ == "__main__":
    print(f"🔹 桃園充電槍監控啟動 (設定每日 09:00 執行報表)")
    
    # 第一步：啟動時立即執行一次
    job()
    
    # 第二步：設定每天 09:00 執行
    #schedule.every().day.at("09:00").do(job)
    #schedule.every().hour.do(job)
    schedule.every().hour.at(":00").do(job)

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"排程系統異常: {e}")
        time.sleep(1)
