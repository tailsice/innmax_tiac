import sys
import time
import requests
import configparser
import os

# 取得目前腳本所在的目錄，確保能正確讀取設定檔
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')

def load_config():
    config = configparser.ConfigParser()
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: Config file not found at {CONFIG_FILE}")
        sys.exit(1)
    config.read(CONFIG_FILE)
    return config

def get_charging_total():
    try:
        # 1. 讀取設定
        config = load_config()
        base_url = config['API']['BaseUrl']
        static_params = config['API']['QueryParams']
        timeout = int(config['SYSTEM']['Timeout'])
        
        # 2. 建構動態 URL (加入當前時間戳記以避免 Cache)
        # Javascript 的 Date.now() 是毫秒，所以 Python time.time() * 1000
        current_timestamp = int(time.time() * 1000)
        full_url = f"{base_url}?{static_params}&reactQueryCache={current_timestamp}"

        # 3. 設定 Headers
        headers = {
            'User-Agent': config['AUTH']['UserAgent'],
            'Content-Type': 'application/json'
        }
        # 如果設定檔有 Auth Token 則加入
        if config['AUTH'].get('Authorization'):
            headers['Authorization'] = config['AUTH']['Authorization']

        # 4. 發送請求
        response = requests.get(full_url, headers=headers, timeout=timeout)
        response.raise_for_status() # 若狀態碼非 200 則拋出異常

        # 5. 解析 JSON 並取得 total
        data = response.json()
        
        # 根據您提供的 response 結構，直接提取 total
        total_count = data.get('total', -1) # 若找不到欄位回傳 -1

        return total_count

    except requests.exceptions.RequestException as e:
        # 網路連線相關錯誤
        # 為了 SNMP 除錯，可以 print 錯誤訊息到 stderr，但在 stdout 輸出一個錯誤代碼
        sys.stderr.write(f"Network Error: {str(e)}\n")
        return -1
    except ValueError as e:
        # JSON 解析錯誤
        sys.stderr.write(f"JSON Parsing Error: {str(e)}\n")
        return -1
    except Exception as e:
        sys.stderr.write(f"Unexpected Error: {str(e)}\n")
        return -1

if __name__ == "__main__":
    # 執行並輸出結果至 Standard Output (這是 SNMP extend 讀取的地方)
    result = get_charging_total()
    print(result)
