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
# 1. 通訊與輔助功能 (含重送機制)
# --------------------------

def send_to_slack(token, channel, message):
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    slack_text = message.replace("<b>", "*").replace("</b>", "*")
    payload = {"channel": channel, "text": slack_text}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=20)
        if not resp.json().get("ok"): print(f"⚠️ Slack 發送失敗: {resp.json().get('error')}")
    except Exception as e: print(f"❌ Slack 連線異常: {e}")

def send_to_telegram(token, chat_id, message):
    """具備 3 次重試機制的 Telegram 發送函式"""
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    MAX_LENGTH = 4000
    MAX_RETRIES = 3
    chunks = [message[i:i+MAX_LENGTH] for i in range(0, len(message), MAX_LENGTH)] if len(message) > MAX_LENGTH else [message]

    for i, msg in enumerate(chunks):
        text = f"{msg}\n\n(第 {i+1}/{len(chunks)} 頁)" if len(chunks) > 1 else msg
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
        
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.post(url, json=payload, timeout=30)
                resp.raise_for_status()
                if len(chunks) > 1: time.sleep(1)
                break 
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    wait = (attempt + 1) * 5
                    print(f"⚠️ Telegram 重試中 ({attempt+1}/{MAX_RETRIES})... 錯誤: {e}")
                    time.sleep(wait)
                else:
                    print(f"❌ Telegram 最終發送失敗: {e}")

def load_operating_stations(csv_filename):
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
# 2. 核心監控邏輯
# --------------------------

def check_region_status(region_name, api_url, csv_file, tg_config, slack_config):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 檢查 {region_name}...")
    station_dict = load_operating_stations(csv_file)
    if not station_dict: return

    try:
        resp = requests.get(api_url, timeout=30)
        resp.raise_for_status()
        ns = {'ns': 'https://traffic.transportdata.tw/standard/EVStation/schema/'}
        root = ET.fromstring(resp.text)
        
        counts = {"正常": 0, "離線": 0, "故障": 0}
        issues = []
        active_stations = set()
        total_connectors = 0

        for live_status in root.findall(".//ns:LiveStatus", ns):
            s_id = live_status.findtext("ns:StationID", namespaces=ns)
            if s_id in station_dict:
                active_stations.add(s_id)
                total_connectors += 1
                status_code = live_status.findtext("ns:ConnectorStatus", namespaces=ns)
                conn_id = live_status.findtext("ns:ConnectorID", namespaces=ns)
                conn_type = live_status.findtext("ns:ConnectorType", namespaces=ns)
                
                # DC/AC 判定修正：ID 含 DC 則優先判定為 DC
                id_str = str(conn_id).upper()
                match = re.search(r'([0-9]+[A-Z]*)$', id_str)
                simple_id = match.group(1) if match else id_str
                
                if "DC" in id_str:
                    type_label = "DC"
                elif "AC" in id_str:
                    type_label = "AC"
                elif conn_type in ['1', '2', '3']:
                    type_label = "AC"
                else:
                    type_label = "DC"

                if status_code in ["1", "2"]:
                    counts["正常"] += 1
                else:
                    label = "離線" if status_code == "3" else "故障"
                    counts[label] += 1
                    issues.append(f"• {station_dict[s_id]} {type_label} ({simple_id})")

        report = [
            f"<b>{region_name}回報</b>",
            f"已啟用：{len(active_stations)}站 {total_connectors}槍",
            f"狀態：正常:{counts['正常']} | 離線:{counts['離線']} | 故障:{counts['故障']}",
            "問題清單："
        ]
        report.extend(issues if issues else ["目前營運站點全部正常"])
        final_msg = "\n".join(report)

        send_to_telegram(tg_config['token'], tg_config['chat_id'], final_msg)
        send_to_slack(slack_config['token'], slack_config['channel'], final_msg)

    except Exception as e:
        error_msg = f"❌ <b>{region_name} 執行錯誤</b>: {str(e)[:200]}"
        send_to_telegram(tg_config['token'], tg_config['chat_id'], error_msg)

def job():
    cfg = configparser.ConfigParser()
    cfg.read('config.ini', encoding='utf-8')
    try:
        tg_conf = {'token': cfg.get('TELEGRAM', 'BOT_TOKEN'), 'chat_id': cfg.get('TELEGRAM', 'CHAT_ID')}
        slack_conf = {'token': cfg.get('Slack', 'BOT_TOKEN'), 'channel': cfg.get('Slack', 'CHANNEL_ID')}
        regions = {
            "桃園": {"api": cfg.get('API', 'URL_TAO'), "csv": cfg.get('FILES', 'STATION_TAO')},
            "台南": {"api": cfg.get('API', 'URL_TNN'), "csv": cfg.get('FILES', 'STATION_TNN')}
        }
        for name, data in regions.items():
            check_region_status(name, data['api'], data['csv'], tg_conf, slack_conf)
            time.sleep(2)
    except Exception as e: print(f"❌ 任務執行錯誤: {e}")

if __name__ == "__main__":
    job()
    schedule.every().day.at("09:00").do(job)
    while True:
        schedule.run_pending()
        time.sleep(10)
