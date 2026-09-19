#!/bin/bash
# SCV / MCR / ICV matrix on the real Benchy.
#
# instantaneous_corner_velocity is a config-only option - Klipper exposes no SET
# command for it - so any row that changes it edits rodent-print.cfg and restarts
# klippy. SCV, accel and MCR are all settable at runtime via SET_VELOCITY_LIMIT.
#
# Row 1 duplicates settings already measured in the speed ramp (SCV 5,
# accel 20000, M220 300% -> 175 s). If it does not reproduce that, the harness is
# wrong and nothing below it means anything.
H="${H:-$HOME}"
CFG=$H/rodent-print.cfg
OUT=$H/scv-matrix.json
TARGET=${TARGET:-8}
rm -f "$OUT"

# label:scv:accel:mcr:icv
ROWS=(
  "1 control:5:20000:0.5:1"
  "2 SCV10:10:20000:0.5:1"
  "3 SCV15+ICV5:15:20000:0.5:5"
  "4 SCV20 MCR0 ICV5:20:20000:0.0:5"
  "5 SCV30 a40k MCR0:30:40000:0.0:5"
  "6 SCV40 a50k MCR0:40:50000:0.0:5"
)

set_icv() {                     # set_icv <value>; returns 0 if a restart happened
    local want=$1
    local cur
    cur=$(grep -oP '^instantaneous_corner_velocity:\s*\K[0-9.]+' "$CFG" 2>/dev/null)
    if [ "$cur" = "$want" ]; then return 1; fi
    if grep -q '^instantaneous_corner_velocity:' "$CFG"; then
        sed -i "s/^instantaneous_corner_velocity:.*/instantaneous_corner_velocity: $want/" "$CFG"
    else
        # insert into the [extruder] section, right after pressure_advance
        sed -i "0,/^pressure_advance_smooth_time:/s//instantaneous_corner_velocity: $want\npressure_advance_smooth_time:/" "$CFG"
    fi
    $H/vm-klippy.sh start "$CFG" >/dev/null 2>&1
    return 0
}

wait_ready() {
    for _ in $(seq 1 25); do
        if $H/klippy-venv/bin/python - <<PY >/dev/null 2>&1
import sys
from importlib.machinery import SourceFileLoader
K = SourceFileLoader("kapi", "$H/klippy-api.py").load_module()
k = K.Klippy(); s = k.call("info")["result"]["state"]; k.close()
sys.exit(0 if s == "ready" else 1)
PY
        then return 0; fi
        sleep 2
    done
    return 1
}

echo "SCV / MCR / ICV matrix - real Benchy, first ${TARGET}% of file, M220 pinned 300%"
printf "%-22s %5s %7s %5s %7s %7s %7s %8s %7s %6s %8s  %s\n" \
   label SCV accel MCR dur file% kB/s retx% inval stall buf_min verdict
printf '%.0s-' {1..125}; echo

for row in "${ROWS[@]}"; do
    IFS=":" read -r label scv accel mcr icv <<<"$row"
    if set_icv "$icv"; then
        if ! wait_ready; then echo "$label: klippy not ready after ICV change"; exit 1; fi
    fi
    $H/klippy-venv/bin/python $H/scv-row.py benchy.gcode "$TARGET" \
        "$scv" "$accel" "$mcr" "$label" "$OUT"
    sleep 3
done
echo
echo "raw -> $OUT"
