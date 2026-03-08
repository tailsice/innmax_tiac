import csv
import time
import requests
import traceback
import os
import configparser
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

# =================================================
# 1. 讀取 Config 設定
# =================================================
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), 'config.ini')

if not os.path.exists(config_path):
    print("❌ 找不到 config.ini 檔案，請檢查路徑。")
    exit(1)

config.read(config_path, encoding='utf-8')

# 從 Config 讀取資訊
TG_BOT_TOKEN = config.get('TELEGRAM', 'BOT_TOKEN')
TG_CHAT_ID = config.get('TELEGRAM', 'CHAT_ID')
SLACK_TOKEN = config.get('Slack', 'BOT_TOKEN')
SLACK_CHANNEL = config.get('Slack', 'CHANNEL_ID')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "case_tnn.csv")
RESULT_PATH = os.path.join(BASE_DIR, "result.txt")

# 分類權重
STATUS_CONFIG = {
    "✅ 已送電": {"keywords": ["檢驗送電", "已送電"], "weight": 1},
    "⚠️ 配合改善事項": {"keywords": ["配合改善", "改善事項"], "weight": 2},
    "💰 費用已核算完成": {"keywords": ["費用已核算", "核算完成", "繳費"], "weight": 3},
    "📑 申報內線竣工": {"keywords": ["內線竣工"], "weight": 4},
    "🚧 外線施工中": {"keywords": ["施工中"], "weight": 5},
    "📐 設計外線中": {"keywords": ["設計外線"], "weight": 6},
    "🔍 複核中": {"keywords": ["複核"], "weight": 7},
    "🚫 取消申請": {"keywords": ["取消申請", "撤回"], "weight": 8},
    "⚙️ 其他": {"keywords": [], "weight": 9}
}

# =================================================
# 2. 通訊發送功能
# =================================================

def send_telegram(message):
    """發送 Telegram 訊息"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"Telegram 發送失敗: {e}")

def send_slack(message):
    """發送 Slack 訊息 (使用 Web API)"""
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_TOKEN}",
        "Content-Type": "application/json"
    }
    # Slack 的粗體是 *text*，Telegram 是 <b>text</b>
    # 這裡做簡單的格式轉換，讓 Slack 也能顯示粗體
    slack_msg = message.replace("<b>", "*").replace("</b>", "*")
    
    payload = {
        "channel": SLACK_CHANNEL,
        "text": slack_msg
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        res_data = resp.json()
        if not res_data.get("ok"):
            print(f"Slack 發送失敗: {res_data.get('error')}")
    except Exception as e:
        print(f"Slack 連線異常: {e}")

def broadcast(message):
    """同步發送到所有平台"""
    print(f"正在發送報告至各平台...")
    send_telegram(message)
    send_slack(message)

# =================================================
# 3. Selenium & 查詢功能 (保持原邏輯並優化)
# =================================================

def init_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0...")
    return webdriver.Chrome(options=options)

def get_category_info(status_text):
    for cat_name, cfg in STATUS_CONFIG.items():
        if any(kw in status_text for kw in cfg["keywords"]):
            return cat_name, cfg["weight"]
    return "⚙️ 其他", STATUS_CONFIG["⚙️ 其他"]["weight"]

def query_taipower(driver, park, dist, cpsno):
    url = "https://service.taipower.com.tw/wapp/newnas/nawp090.aspx"
    try:
        driver.get(url)
        wait = WebDriverWait(driver, 15)
        rb_cps = wait.until(EC.element_to_be_clickable((By.ID, "rb_cps")))
        rb_cps.click()
        driver.find_element(By.ID, "custname").clear()
        driver.find_element(By.ID, "custname").send_keys("台達電")
        Select(driver.find_element(By.ID, "dist")).select_by_value(dist)
        driver.find_element(By.ID, "cpsno").clear()
        driver.find_element(By.ID, "cpsno").send_keys(cpsno)
        driver.find_element(By.ID, "Button2").click()
        wait.until(EC.visibility_of_element_located((By.ID, "LL_item")))
        item = driver.find_element(By.ID, "LL_item").text.strip()
        status = driver.find_element(By.ID, "LL_status").text.strip()
        return True, item, status
    except Exception as e:
        return False, "查詢異常", str(e)

# =================================================
# 4. 主流程
# =================================================

if __name__ == "__main__":
    driver = None
    raw_results = []

    try:
        if not os.path.exists(CSV_PATH):
            raise FileNotFoundError(f"找不到 CSV 檔案: {CSV_PATH}")

        with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cases = [row for row in reader]

        driver = init_driver()

        for row in cases:
            p_name = row["park"].strip()
            p_dist = row["dist"].strip()
            p_cpsno = row["cpsno"].strip()
            success, item, status = query_taipower(driver, p_name, p_dist, p_cpsno)
            cat_name, weight = get_category_info(status)

            raw_results.append({
                "weight": weight, "cat_name": cat_name,
                "park": p_name, "item": item, "status": status
            })
            time.sleep(1.5)

        sorted_results = sorted(raw_results, key=lambda x: (x["weight"], x["park"]))

        # 產生訊息內容
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        msg_parts = [f"⚡ <b>台電請電進度週報</b>", f"📅 時間: {timestamp}\n"]

        current_cat = ""
        for res in sorted_results:
            if res["cat_name"] != current_cat:
                current_cat = res["cat_name"]
                msg_parts.append(f"\n<b>{current_cat}</b>")
            msg_parts.append(f"  • {res['park']} ({res['status']})")

        msg_parts.append(f"\n---\n📊 總計監控：{len(sorted_results)} 處案場")
        
        # 統一發送
        full_message = "\n".join(msg_parts)
        broadcast(full_message)

        # 寫入檔案
        with open(RESULT_PATH, "w", encoding="utf-8") as f:
            f.write("分類,案場,項目,狀態\n")
            for res in sorted_results:
                f.write(f"{res['cat_name']},{res['park']},{res['item']},{res['status']}\n")

    except Exception:
        err = traceback.format_exc()
        print(err)
        broadcast(f"⚠️ <b>台電查詢腳本出錯</b>\n{err[-500:]}")
    finally:
        if driver:
            driver.quit()
