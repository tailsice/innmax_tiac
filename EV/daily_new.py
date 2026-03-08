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
# 1. 通訊與輔助功能 (保持邏輯一致)
# --------------------------

def send_to_slack(token, channel, message):
    url = "https://slack.com/api/chat.postMessage"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    slack_text = message.replace("<b>", "*").replace("</b>", "*")
    payload = {"channel": channel, "text": slack_text}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if not resp.json().get("ok"): print(f"⚠️ Slack 發送失敗: {resp.json().get('error')}")
    except Exception as e: print(f"❌ Slack 連線異常: {e}")

def send_to_telegram(token, chat_id, message):
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    MAX_LENGTH = 4000
    chunks = [message[i:i+MAX_LENGTH] for i in range(0, len(message), MAX_LENGTH)] if len(message) > MAX_LENGTH else [message]

    for i, msg in enumerate(chunks):
        text = f"{msg}\n\n(第 {i+1}/{len(chunks)} 頁)" if len(chunks) > 1 else msg
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
        try:
            requests.post(url, json=payload, timeout=15).raise_for_status()
            if len(chunks) > 1: time.sleep(0.5) 
        except Exception as e: print(f"❌ Telegram 發送失敗: {e}")

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
# 2. 核心監控邏輯 (增加 config 傳入參數)
# --------------------------

def check_region_status(region_name, api_url, csv_file, tg_config, slack_config):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 檢查 {region_name}...")
    station_dict = load_operating_stations(csv_file)
    if not station_dict: return

    try:
        resp = requests.get(api_url, timeout=25)
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
                
                # 判斷槍號與類型
                match = re.search(r'([0-9]+[A-Z]*)$', str(conn_id))
                simple_conn_id = match.group(1) if match else conn_id
                
                id_str = str(conn_id).upper()
                type_label = "AC" if id_str.endswith('AC') or live_status.findtext("ns:ConnectorType", namespaces=ns) in ['1','2','3'] else "DC"

                if status_code in ["1", "2"]:
                    counts["正常"] += 1
                else:
                    label = "離線" if status_code == "3" else "故障"
                    counts[label] += 1
                    issues.append(f"• {station_dict[s_id]} 1{type_label} ({simple_conn_id})")

        report = [
            f"<b>{region_name}回報</b>",
            f"已啟用：{len(active_stations)}站 {total_connectors}槍",
            f"狀態：正常:{counts['正常']} | 離線:{counts['離線']} | 故障:{counts['故障']}",
            "問題清單："
        ]
        report.extend(issues if issues else ["目前營運站點全部正常"])
        final_msg = "\n".join(report)

        # 執行發送
        send_to_telegram(tg_config['token'], tg_config['chat_id'], final_msg)
        send_to_slack(slack_config['token'], slack_config['channel'], final_msg)

    except Exception as e:
        error_msg = f"❌ <b>{region_name} 執行錯誤</b>: {str(e)[:200]}"
        send_to_telegram(tg_config['token'], tg_config['chat_id'], error_msg)

# --------------------------
# 3. 每日任務封裝 (每次執行都會重讀 config)
# --------------------------

def job():
    print(f"\n🔔 開始執行每日定時報表檢查...")
    
    # 每次執行都重新讀取 Config，確保手動修改設定後不需重啟程式
    cfg = configparser.ConfigParser()
    cfg.read('config.ini', encoding='utf-8')

    try:
        # 讀取共用通訊設定
        tg_conf = {'token': cfg.get('TELEGRAM', 'BOT_TOKEN'), 'chat_id': cfg.get('TELEGRAM', 'CHAT_ID')}
        slack_conf = {'token': cfg.get('Slack', 'BOT_TOKEN'), 'channel': cfg.get('Slack', 'CHANNEL_ID')}
        
        # 區域清單
        regions = {
            "桃園": {"api": cfg.get('API', 'URL_TAO'), "csv": cfg.get('FILES', 'STATION_TAO')},
            "台南": {"api": cfg.get('API', 'URL_TNN'), "csv": cfg.get('FILES', 'STATION_TNN')}
        }

        for name, data in regions.items():
            check_region_status(name, data['api'], data['csv'], tg_conf, slack_conf)
            time.sleep(2) # 避開 API 頻率限制
            
        print(f"✅ 每日報表任務處理完畢。")
    except Exception as e:
        print(f"❌ 讀取設定檔或執行任務時出錯: {e}")

# --------------------------
# 4. 啟動與排程
# --------------------------

if __name__ == "__main__":
    print(f"🚀 充電槍監控服務已啟動...")
    print(f"📅 排程設定：每日 09:00 執行")
    
    # 1. 啟動時立即測試一次
    job()
    
    # 2. 設定每天早上 09:00 執行
    schedule.every().day.at("09:00").do(job)

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(f"排程循環異常: {e}")
        time.sleep(10) # 檢查頻率調降為 10 秒，節省 CPU
