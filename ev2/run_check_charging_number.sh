source /home/phchi/innmax/venv/bin/activate
TZ='Asia/Taipei'; export TZ
DIR="/home/phchi/innmax/ev2"
cd $DIR
python3 snmp_charging_monitor.py
