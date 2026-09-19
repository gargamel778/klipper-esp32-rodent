#!/bin/bash
# Alternate two firmwares through the SAME cold single-phase workload, N times
# each, so the comparison rests on a distribution rather than one run apiece.
#
# Alternating (A B A B) rather than blocking (A A B B) keeps any drift in the
# rig - board temperature, host load - from loading onto one arm.
H="${H:-$HOME}"
E=$H/klippy-venv/bin/esptool
N=${N:-2}
declare -A FW=( [baseline]=/tmp/klipper-1500000.bin [v6]=/tmp/klipper-sropt6.bin )

flash() {
    $H/vm-klippy.sh stop >/dev/null 2>&1
    $E --port /dev/ttyUSB1 --baud 460800 --chip esp32 write-flash \
        --flash_mode dio --flash_freq 40m --flash_size detect \
        0x10000 "$1" 2>&1 | grep -q "Hash of data verified" || { echo "FLASH FAILED"; exit 1; }
    $H/vm-klippy.sh start $H/rodent-corexy.cfg >/dev/null 2>&1
    sleep 2
}

echo "cold 400mm/s, $N runs per firmware, alternating"
printf "%-10s %-5s %6s %7s %8s %s\n" firmware run layers moves retx% outcome
for i in $(seq 1 $N); do
    for name in v6 baseline; do
        flash "${FW[$name]}"
        $H/klippy-venv/bin/python $H/corexy-ab.py > /tmp/rep-$name-$i.log 2>&1
        line=$(grep -E "^ +400" /tmp/rep-$name-$i.log)
        lay=$(awk '{print $4}' <<<"$line"); mv=$(awk '{print $5}' <<<"$line")
        rx=$(awk '{print $7}' <<<"$line")
        if grep -q SHUTDOWN <<<"$line"; then out="SHUTDOWN"; else out="completed"; fi
        printf "%-10s %-5s %6s %7s %8s %s\n" "$name" "$i" "$lay" "$mv" "$rx" "$out"
    done
done
