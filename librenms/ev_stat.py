import os
import subprocess
import schedule
import time
import requests
import configparser
from datetime import datetime

# --- 1. 配置讀取 ---
# 請確保您的 config.ini 內容正確
config = configparser.ConfigParser()
config.read('config.ini', encoding='utf-8')

try:
    T_TOKEN = config['telegram']['bot_token']
    T_CHAT_ID = config['telegram']['chat_id']
except KeyError as e:
    print(f"❌ 配置文件讀取失敗，缺少關鍵欄位: {e}")
    exit(1)

# Docker 與 RRD 配置
CONTAINER_NAME = "librenms"
# 請確認以下路徑與您的 RRD 檔名一致
RRD_AC = "/data/rrd/10.3.0.145/customoid-AC_Num.rrd"
RRD_DC = "/data/rrd/10.3.0.145/customoid-DC_Num.rrd"
TMP_IMG_INSIDE = "/tmp/combined_graph.png"
LOCAL_IMG = "./output_charging_combined.png"

# --- 2. 功能函式 ---

def fetch_graph_via_docker_rrd():
    """透過 Docker Exec 調用容器內的 rrdtool，進行多 OID 聚合繪圖"""
    now = int(time.time())
    start = now - (6 * 3600)  # 抓取過去 6 小時數據

    # 構造複雜的 RRDtool 指令
    # DEF: 定義數據源
    # CDEF: 計算總和 (ac + dc)
    # AREA/STACK: 繪製堆疊圖
    # LINE2: 繪製總量趨勢線
    rrd_cmd = (
        f"rrdtool graph {TMP_IMG_INSIDE} "
        f"--start {start} --end {now} "
        f"--width 800 --height 350 --imgformat PNG "
        f"--title 'EV Charging Station Status (AC + DC)' "
        f"--font LEGEND:8:DejaVuSansMono --font AXIS:7:DejaVuSansMono "
        f"-c BACK#EEEEEE -c CANVAS#FFFFFF -l 0 --slope-mode "
        f"DEF:ac={RRD_AC}:oid_value:AVERAGE "
        f"DEF:dc={RRD_DC}:oid_value:AVERAGE "
        f"CDEF:total=ac,dc,+ "
        f"AREA:ac#32CD32:'AC Charging  ' "
        f"GPRINT:ac:LAST:'Now\:%6.2lf ' GPRINT:ac:AVERAGE:'Ave\:%6.2lf ' GPRINT:ac:MAX:'Max\:%6.2lf\\n' "
        f"STACK:dc#FF8C00:'DC Charging  ' "
        f"GPRINT:dc:LAST:'Now\:%6.2lf ' GPRINT:dc:AVERAGE:'Ave\:%6.2lf ' GPRINT:dc:MAX:'Max\:%6.2lf\\n' "
        f"LINE2:total#FF0000:'Total Count  ' "
        f"GPRINT:total:LAST:'Now\:%6.2lf ' GPRINT:total:AVERAGE:'Ave\:%6.2lf ' GPRINT:total:MAX:'Max\:%6.2lf\\n' "
    )

    try:
        # 1. 在 Docker 容器內繪圖
        print(f"[{datetime.now()}] 🎨 正在容器 {CONTAINER_NAME} 內生成合併圖表...")
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
        f"📊 **EV 充電站定時狀態報表**\n"
        f"🔋 指標: AC (綠) / DC (橘) 監控\n"
        f"⏰ 報表生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"✅ 狀態: 數據正常採集中"
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
        # 發送完畢後刪除本地臨時圖片，保持環境乾淨
        if os.path.exists(image_path):
            os.remove(image_path)

def job():
    """排程執行的核心任務"""
    print(f"\n[{datetime.now()}] 🚀 開始執行定時報表任務...")
    path = fetch_graph_via_docker_rrd()
    if path:
        send_to_telegram(path)
    else:
        print(f"[{datetime.now()}] ⛔ 由於圖表生成失敗，取消發送任務")

# --- 3. 程式進入點 ---

if __name__ == "__main__":
    print("=" * 50)
    print(f"🌟 LibreNMS AC/DC 監控報表服務已啟動")
    print(f"🕒 預定執行時間: 00:00, 06:00, 12:00, 18:00")
    print(f"📦 容器目標: {CONTAINER_NAME}")
    print("=" * 50)

    # 設定排程時間點
    schedule.every().day.at("00:00").do(job)
    schedule.every().day.at("06:00").do(job)
    schedule.every().day.at("12:00").do(job)
    schedule.every().day.at("18:00").do(job)

    # 啟動時先執行一次，用於測試功能是否正常
    job()

    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        print(f"\n[{datetime.now()}] 🛑 服務已手動停止")
