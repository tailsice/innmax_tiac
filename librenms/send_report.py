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
    # Telegram 通訊配置
    T_TOKEN = config['telegram']['bot_token']
    CHAT_ID_EV = config['telegram']['chat_id']        # 原本的 EV 群組
    CHAT_ID_TIAC = config['telegram']['tiac_chat_id']  # TIAC 專屬群組
except KeyError as e:
    print(f"❌ 配置文件讀取失敗，缺少關鍵欄位: {e}")
    exit(1)

# Docker 與 RRD 通用配置
CONTAINER_NAME = "librenms"
RRD_AC = "/data/rrd/10.3.0.143/customoid-AC_Num.rrd"
RRD_DC = "/data/rrd/10.3.0.143/customoid-DC_Num.rrd"
RRD_TIAC = "/data/rrd/10.3.0.143/customoid-TIAC_Charging_Number.rrd"

# --- 2. 核心功能函式 ---

def generate_graph(mode):
    """
    通用繪圖函式
    mode: 'EV' (AC+DC) 或 'TIAC' (純DC)
    """
    now = int(time.time())
    start = now - (6 * 3600)
    
    if mode == 'EV':
        tmp_inside = "/tmp/ev_combined_graph.png"
        local_path = "./output_ev_combined.png"
        rrd_cmd = (
            f"rrdtool graph {tmp_inside} --start {start} --end {now} "
            f"--width 800 --height 350 --imgformat PNG --title 'EV Charging Station Status (AC + DC)' "
            f"--font LEGEND:8:DejaVuSansMono --font AXIS:7:DejaVuSansMono "
            f"-c BACK#EEEEEE -c CANVAS#FFFFFF -l 0 --slope-mode "
            f"DEF:ac={RRD_AC}:oid_value:AVERAGE DEF:dc={RRD_DC}:oid_value:AVERAGE "
            f"CDEF:total=ac,dc,+ AREA:ac#32CD32:'AC Charging  ' "
            f"GPRINT:ac:LAST:'Now\:%6.2lf ' GPRINT:ac:AVERAGE:'Ave\:%6.2lf ' GPRINT:ac:MAX:'Max\:%6.2lf\\n' "
            f"STACK:dc#FF8C00:'DC Charging  ' "
            f"GPRINT:dc:LAST:'Now\:%6.2lf ' GPRINT:dc:AVERAGE:'Ave\:%6.2lf ' GPRINT:dc:MAX:'Max\:%6.2lf\\n' "
            f"LINE2:total#FF0000:'Total Count  ' "
            f"GPRINT:total:LAST:'Now\:%6.2lf ' GPRINT:total:AVERAGE:'Ave\:%6.2lf ' GPRINT:total:MAX:'Max\:%6.2lf\\n' "
        )
    else: # TIAC 模式
        tmp_inside = "/tmp/tiac_dc_graph.png"
        local_path = "./output_tiac_dc.png"
        rrd_cmd = (
            f"rrdtool graph {tmp_inside} --start {start} --end {now} "
            f"--width 800 --height 350 --imgformat PNG --title 'TIAC Charging Station - DC Status' "
            f"--font LEGEND:8:DejaVuSansMono --font AXIS:7:DejaVuSansMono "
            f"-c BACK#EEEEEE -c CANVAS#FFFFFF -l 0 --slope-mode "
            f"DEF:dc={RRD_TIAC}:oid_value:AVERAGE AREA:dc#9999cc:'DC Charging Count ' LINE1.25:dc#0000cc "
            f"COMMENT:'\\n' COMMENT:'                 Now       Ave      Max\\n' "
            f"GPRINT:dc:LAST:'Current\:%6.2lf ' GPRINT:dc:AVERAGE:'Average\:%6.2lf ' GPRINT:dc:MAX:'Maximum\:%6.2lf\\n' "
        )

    try:
        # 1. Docker 內繪圖
        exec_cmd = ["docker", "exec", CONTAINER_NAME, "sh", "-c", rrd_cmd]
        subprocess.run(exec_cmd, capture_output=True, text=True, check=True)
        # 2. 複製出來
        subprocess.run(["docker", "cp", f"{CONTAINER_NAME}:{tmp_inside}", local_path], check=True)
        return local_path
    except Exception as e:
        print(f"[{datetime.now()}] ❌ {mode} 圖表生成失敗: {e}")
        return None

def send_to_telegram(image_path, chat_id, caption):
    """通用發送函式"""
    url = f"https://api.telegram.org/bot{T_TOKEN}/sendPhoto"
    try:
        with open(image_path, 'rb') as photo:
            payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'Markdown'}
            files = {'photo': photo}
            response = requests.post(url, data=payload, files=files)
            if response.status_code == 200:
                print(f"[{datetime.now()}] ✅ 報表已成功發送至 ChatID: {chat_id}")
            else:
                print(f"[{datetime.now()}] ❌ 發送失敗: {response.text}")
    except Exception as e:
        print(f"[{datetime.now()}] ❌ Telegram 發送錯誤: {e}")
    finally:
        if os.path.exists(image_path):
            os.remove(image_path)

# --- 3. 整合任務邏輯 ---

def main_job():
    print(f"\n[{datetime.now()}] 🚀 開始執行合併報表任務...")
    
    # 執行任務 A: EV AC+DC
    path_ev = generate_graph('EV')
    if path_ev:
        caption_ev = (f"📊 **EV 充電站總體狀態報表**\n"
                      f"🔋 指標: AC (綠) / DC (橘) 監控\n"
                      f"⏰ 時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        send_to_telegram(path_ev, CHAT_ID_EV, caption_ev)

    # 執行任務 B: TIAC DC
    path_tiac = generate_graph('TIAC')
    if path_tiac:
        caption_tiac = (f"📊 **TIAC 充電站 DC 專屬報表**\n"
                        f"🔋 指標: TIAC 設備監控\n"
                        f"⏰ 時間: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        send_to_telegram(path_tiac, CHAT_ID_TIAC, caption_tiac)

# --- 4. 程式入口 ---

if __name__ == "__main__":
    print("=" * 50)
    print(f"🌟 LibreNMS 綜合報表服務已啟動")
    print(f"🕒 監控目標: EV 總表 & TIAC 專表")
    print(f"🕒 排程時間: 00:00, 06:00, 12:00, 18:00")
    print("=" * 50)

    # 定時排程
    times = ["00:00", "06:00", "12:00", "18:00"]
    for t in times:
        schedule.every().day.at(t).do(main_job)

    # 啟動測試執行
    main_job()

    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        print(f"\n🛑 服務已手動停止")
