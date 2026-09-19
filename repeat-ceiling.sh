#!/bin/bash
# Is 1000 mm/s actually reliable, or did it complete once by luck?
#
# The headline "1000 mm/s completes" rests on a single run. Everything learned in
# this project says that is not evidence: baseline needed six runs before its
# 1716-move result could be trusted, and the failing builds died anywhere between
# 27 and 623 moves. The number that matters operationally is not the fastest
# speed that CAN finish but the fastest that finishes EVERY time - a job that
# dies at 86% has cost time and material.
#
# 3 runs at each of 1000 and 950 mm/s, ALTERNATED so any drift in the rig spreads
# across both arms rather than loading one. Firmware is fixed (shipped baseline)
# so nothing is reflashed; each run gets a fresh klippy and a recovered MCU.
#
# The file is sliced at 900 mm/s and sets its own ACCEL=100000 / SCV=50, which is
# the measured optimum, so speed via M220 is the only variable.
H="${H:-$HOME}"
FILE=sb900.gcode
BASE=900
OUT=$H/repeat-ceiling.tsv
: > "$OUT"

recover() {
    $H/klippy-venv/bin/python - <<PY >/dev/null 2>&1
import time
from importlib.machinery import SourceFileLoader
K = SourceFileLoader("kapi", "$H/klippy-api.py").load_module()
try:
    k = K.Klippy()
    if k.call("info")["result"]["state"] != "ready":
        k.gcode("FIRMWARE_RESTART", 180)
    else:
        k.gcode("CANCEL_PRINT", 120); k.gcode("CLEAR_PAUSE", 30)
    k.close()
except Exception:
    pass
time.sleep(12)
PY
}

ready() {
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

echo "Reliability of the ceiling - 3 runs each at 1000 and 950 mm/s, alternated"
echo "shipped baseline firmware, 1500000 baud, ACCEL/SCV from the file"
printf "%-5s %-7s %-7s %-11s %-9s %-8s %-9s %s\n" run mm/s M220 result reached retx% buf_min elapsed
printf '%.0s-' {1..82}; echo

for i in 1 2 3; do
    for v in 1000 950; do
        m=$(( v * 100 / BASE ))
        recover
        if ! ready; then echo "run $i @ $v: klippy not ready - aborting"; exit 1; fi
        M220=$m $H/klippy-venv/bin/python $H/full-print.py "$FILE" 50 100000 0.0 2000 \
            "$H/rc-$v-$i.json" > /tmp/rc-$v-$i.log 2>&1
        read -r st reach retx buf el <<<"$($H/klippy-venv/bin/python -c "
import json;d=json.load(open('$H/rc-$v-$i.json'))
print(d['state'], d['progress_pct'], d['retx_pct'], d['buf_min'], d['elapsed'])" 2>/dev/null)"
        printf "%-5s %-7s %-7s %-11s %-9s %-8s %-9s %s\n" \
               "$i" "$v" "${m}%" "$st" "${reach}%" "$retx" "$buf" "$el"
        printf "%s\t%s\t%s\t%s\t%s\t%s\n" "$i" "$v" "$st" "$reach" "$retx" "$el" >> "$OUT"
    done
done

echo
echo "=== verdict ==="
for v in 1000 950; do
    ok=$(awk -F'\t' -v s="$v" '$2==s && $3=="complete"' "$OUT" | wc -l | tr -d ' ')
    n=$(awk -F'\t' -v s="$v" '$2==s' "$OUT" | wc -l | tr -d ' ')
    echo "  ${v} mm/s: ${ok}/${n} completed"
done
