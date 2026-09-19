#!/bin/bash
# Controlled maximum-finding stress test: baseline vs v7, 3 rounds each.
#
# Every design choice here exists to stop a single lucky or unlucky run from
# deciding anything:
#
#   COUNTERBALANCED ORDER   round 1 A,B  round 2 B,A  round 3 A,B. Plain
#                           alternation controls for drift over time; swapping
#                           the order each round additionally controls for any
#                           "first run after the rig has been idle" effect.
#   COLD EVERY RUN          each run reflashes (which resets the MCU) and starts
#                           a fresh klippy. No run inherits another's state.
#   FLASH IS VERIFIED       the write must report a matching digest AND the
#                           image is verify-flash'd against the file before the
#                           run counts. A silently bad flash would otherwise be
#                           indistinguishable from a firmware regression.
#   IDENTICAL WORKLOAD      same stress-max.py, same config, same baud. The
#                           firmware image is the only thing that differs.
#   RAW KEPT                per-run log and JSON are preserved for re-analysis.
#
# Result per run = the highest phase completed without shutdown (the ceiling),
# plus per-phase retransmit rates for comparison at the phases both reach.
H="${H:-$HOME}"
E=$H/klippy-venv/bin/esptool
PORT=/dev/ttyUSB1
ROUNDS=${ROUNDS:-3}
declare -A FW=( [baseline]=/tmp/klipper-1500000.bin [v7]=/tmp/klipper-sropt7.bin )

flash_verified() {          # flash_verified <image>   -> 0 only if truly on the board
    local img=$1
    $H/vm-klippy.sh stop >/dev/null 2>&1
    $E --port $PORT --baud 460800 --chip esp32 write-flash \
        --flash_mode dio --flash_freq 40m --flash_size detect \
        0x10000 "$img" 2>&1 | grep -q "Hash of data verified" || return 1
    $E --port $PORT --baud 460800 verify-flash 0x10000 "$img" 2>&1 \
        | grep -q "Verification successful" || return 1
    return 0
}

echo "=== baseline vs v7, maximum-finding, $ROUNDS rounds, counterbalanced ==="
echo "=== each run: cold flash (verified) + fresh klippy + ramp 400->1000 mm/s ==="
echo

for r in $(seq 1 $ROUNDS); do
    # counterbalance the order every other round
    if [ $((r % 2)) -eq 1 ]; then ORDER="baseline v7"; else ORDER="v7 baseline"; fi
    for name in $ORDER; do
        tag="r${r}-${name}"
        if ! flash_verified "${FW[$name]}"; then
            echo "[$tag] FLASH/VERIFY FAILED - aborting"; exit 1
        fi
        $H/vm-klippy.sh start $H/rodent-corexy.cfg >/dev/null 2>&1
        # Ask klippy directly rather than grepping its log for a phrase - the
        # log never contains a literal "ready" line, so a grep-based gate fails
        # even on a perfectly healthy startup.
        ready=0
        for _ in $(seq 1 20); do
            if $H/klippy-venv/bin/python - <<PY >/dev/null 2>&1
import sys
from importlib.machinery import SourceFileLoader
K = SourceFileLoader("kapi", "$H/klippy-api.py").load_module()
k = K.Klippy()
s = k.call("info")["result"]["state"]
k.close()
sys.exit(0 if s == "ready" else 1)
PY
            then ready=1; break; fi
            sleep 2
        done
        if [ $ready -ne 1 ]; then
            echo "[$tag] klippy did not reach state=ready - aborting"; exit 1
        fi
        OUT=$H/stress-$tag.json $H/klippy-venv/bin/python $H/stress-max.py \
            > /tmp/stress-$tag.log 2>&1
        # ceiling = fastest phase that completed; failed phase reported separately
        ceil=$(awk '$1 ~ /^[0-9]+$/ && $0 !~ /SHUTDOWN|GCODE ERR/ {c=$1} END{print (c==""?"none":c)}' \
               /tmp/stress-$tag.log)
        fail=$(awk '/SHUTDOWN|GCODE ERR/ {print $1; exit}' /tmp/stress-$tag.log)
        echo "[$tag] ceiling=${ceil} mm/s   failed_at=${fail:-none-in-range}"
        awk '$1 ~ /^[0-9]+$/ {
                v=""; for (i=12; i<=NF; i++) v = v (i>12 ? " " : "") $i;
                printf "        %5s mm/s  lay=%-3s moves=%-5s retx=%-6s inval=%-5s A:B=%-6s %s\n",
                       $1,$4,$5,$7,$8,$11,v }' /tmp/stress-$tag.log
        echo
    done
done
echo "=== all runs complete ==="
