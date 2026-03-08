#!/bin/bash
# 檔案位置: /home/phchi/innmax/TAO/run.sh

# 1. 設定環境變數
export TZ='Asia/Taipei'
WORKDIR="/home/phchi/innmax/TNN"
VENV_PYTHON="/home/phchi/innmax/venv/bin/python3"

# 2. 進入目錄 (雖然 Python 內用了絕對路徑，但進入目錄仍是好習慣)
cd "$WORKDIR" || exit 1

# 3. 執行 Python 並記錄 Log
# 使用 >> 追加模式，避免每次執行都覆蓋舊 Log
echo "=== Job Started at $(date) ===" >> run.log
$VENV_PYTHON taipower_new.py >> run.log 2>&1
echo "=== Job Finished ===" >> run.log
