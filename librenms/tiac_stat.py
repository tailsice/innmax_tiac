import os
import subprocess
import schedule
import time
import requests
import configparser
from datetime import datetime

# --- 1. 配置讀取 ---
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')

try:
    T_TOKEN = config['telegram']['bot_token']
    T_CHAT_ID = config['telegram']['tiac_chat_id']
except KeyError as e:
    print(f"❌ 配置文件讀取失敗，缺少關鍵欄位: {e}")
    exit(1)

# Docker 與 RRD 配置
CONTAINER_NAME = "librenms"
# 更新為您指定的 TIAC RRD 路徑
RRD_TIAC = "/data/rrd/10.3.0.145/customoid-TIAC_Charging_Number.rrd"
TMP_IMG_INSIDE = "/tmp/tiac_charging_graph.png"
LOCAL_IMG = "./output_tiac_charging.png"

# --- 2. 功能函式 ---

def fetch_graph_via_docker_rrd():
    """透過 Docker Exec 調用容器內的 rrdtool，進行單一 OID (DC) 繪圖"""
    now = int(time.time())
    start = now - (6 * 3600)  # 抓取過去 6 小時數據

    # 構造簡化後的 RRDtool 指令 (僅針對 DC/TIAC)
    rrd_cmd = (
        f"rrdtool graph {TMP_IMG_INSIDE} "
        f"--start {start} --end {now} "
        f"--width 800 --height 350 --imgformat PNG "
        f"--title 'EV Charging Station - DC Status' "
        f"--font LEGEND:8:DejaVuSansMono --font AXIS:7:DejaVuSansMono "
        f"-c BACK#EEEEEE -c CANVAS#FFFFFF -l 0 --slope-mode "
        f"DEF:dc={RRD_TIAC}:oid_value:AVERAGE "
        f"AREA:dc#9999cc:'DC Charging Count ' "
        f"LINE1.25:dc#0000cc "
        f"COMMENT:'\\n' "
        f"COMMENT:'                 Now       Ave      Max\\n' "
        f"GPRINT:dc:LAST:'Current\:%6.2lf ' "
        f"GPRINT:dc:AVERAGE:'Average\:%6.2lf ' "
        f"GPRINT:dc:MAX:'Maximum\:%6.2lf\\n' "
    )

    try:
        print(f"[{datetime.now()}] 🎨 正在容器 {CONTAINER_NAME} 內生成 DC 報表圖表...")
        exec_cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", rrd_cmd]
        result = subprocess.run(exec_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"[{datetime.now()}] ❌ RRDtool 繪圖失敗: {result.stderr}")
            return None

        # 2. 將圖片從容器複製到本地宿主機
        cp_cmd = ["docker", "cp", f"{CONTAINER_NAME}:{TMP_IMG_INSIDE}", LOCAL_IMG]
        subprocess.run(cp_cmd, check=True)

        if os.path.exists(LOCAL_IMG):
            return LOCAL_IMG
        return None

    except Exception as e:
        print(f"[{datetime.now()}] ⚠️ 執行過程中發生異常: {e}")
        return None

def send_to_telegram(image_path):
    """將生成的圖片發送至 Telegram Bot"""
    url = f"https://api.telegram.org/bot{T_TOKEN}/sendPhoto"
    caption = (
        f"📊 **TIAC 充電站定時狀態報表 (DC)**\n"
        f"🔋 指標: TIAC Charging Number 監控\n"
        f"⏰ 報表生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"✅ 狀態: 數據採集正常"
    )

    try:
        with open(image_path, 'rb') as photo:
            payload = {
                'chat_id': T_CHAT_ID,
                'caption': caption,
                'parse_mode': 'Markdown'
            }
            files = {'photo': photo}
            response = requests.post(url, data=payload, files=files)

            if response.status_code == 200:
                print(f"[{datetime.now()}] ✅ 已成功發送至 Telegram")
            else:
                print(f"[{datetime.now()}] ❌ 發送失敗，API 回傳: {response.text}")
    except Exception as e:
        print(f"[{datetime.now()}] ❌ Telegram 發送過程出錯: {e}")
    finally:
        if os.path.exists(image_path):
            os.remove(image_path)

def job():
    """排程執行的核心任務"""
    print(f"\n[{datetime.now()}] 🚀 開始執行 DC 定時報表任務...")
    path = fetch_graph_via_docker_rrd()
    if path:
        send_to_telegram(path)
    else:
        print(f"[{datetime.now()}] ⛔ 圖表生成失敗")

# --- 3. 程式進入點 ---

if __name__ == "__main__":
    print("=" * 50)
    print(f"🌟 LibreNMS DC 監控報表服務已啟動")
    print(f"🕒 執行時間: 00:00, 06:00, 12:00, 18:00")
    print("=" * 50)

    schedule.every().day.at("00:00").do(job)
    schedule.every().day.at("06:00").do(job)
    schedule.every().day.at("12:00").do(job)
    schedule.every().day.at("18:00").do(job)

    # 測試執行
    job()

    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        print(f"\n🛑 服務已停止")
