#!/bin/bash
# Start/stop klippy on the Ubuntu VM, detached so it survives the ssh session.
#   ./vm-klippy.sh start <config>   ./vm-klippy.sh stop   ./vm-klippy.sh status
set -u
CFG="${2:-/home/parallels/stress-rodent-notmc.cfg}"
LOG=/tmp/klippy-stress.log
BOOT=/tmp/klippy-boot.log
UDS=/tmp/klippy_uds

case "${1:-status}" in
  start)
    pkill -f klippy.py 2>/dev/null; sleep 1
    rm -f "$LOG" "$BOOT" "$UDS"
    cd /home/parallels/klipper || exit 1
    setsid nohup /home/parallels/klippy-venv/bin/python ./klippy/klippy.py \
        "$CFG" -a "$UDS" -l "$LOG" >"$BOOT" 2>&1 < /dev/null &
    disown
    # wait for readiness rather than a fixed sleep
    for i in $(seq 1 60); do
        grep -q "Klipper state: Ready\|Printer is ready" "$LOG" 2>/dev/null && break
        grep -qE "Config error|Traceback|Unable to open" "$LOG" "$BOOT" 2>/dev/null && break
        sleep 1
    done
    echo "=== startup (config: $CFG) ==="
    grep -vE "^Stats " "$LOG" 2>/dev/null | grep -E \
      "Loaded MCU|MCU 'mcu' config|Configured MCU|Config error|Traceback|error|shutdown" | head -8
    tail -4 "$BOOT" 2>/dev/null
    ;;
  stop)
    pkill -f klippy.py 2>/dev/null && echo "stopped" || echo "not running"
    ;;
  status)
    pgrep -f klippy.py >/dev/null && echo "running" || echo "not running"
    grep "^Stats " "$LOG" 2>/dev/null | tail -1
    ;;
esac
