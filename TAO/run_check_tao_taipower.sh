#!/bin/bash
source /home/phchi/innmax/venv/bin/activate
TZ='Asia/Taipei'; export TZ
DIR="/home/phchi/innmax/TAO"
cd $DIR
#python3 snmp_charging_monitor.py
#VAL=$(/usr/bin/python3 /opt/python/ev2/charging_stats.py)
/home/phchi/innmax/venv/bin/python3 taipower.py
# 使用 printf 輸出，這會去掉所有多餘的封裝
#printf "%s" "$VAL"
