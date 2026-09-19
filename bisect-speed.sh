#!/bin/bash
# Find the fastest speed at which the board COMPLETES a legal speed Benchy.
#
# A finished print at 850 mm/s is worth more than a failed one at 1000 - a job
# that dies at 86% has cost time and material, which is the whole point of
# finding this number rather than the headline speed.
#
# The file is sliced at 900 mm/s and emits its own ACCEL=100000 / SCV=50 once at
# the start, which are already the measured optimum, so those are left alone.
# Speed is scaled with M220, making velocity the single search variable.
#
# Known boundary going in: 1000 mm/s shut down at 85.65% (Timer too close).
# Bisection is on absolute mm/s; each result narrows [lo, hi] until they are
# within TOL, and every failure recovers the MCU before the next run.
H="${H:-$HOME}"
FILE=sb900.gcode
BASE=900                # the speed the file is sliced at; M220 100% == this
OUT=$H/bisect-speed.json
LOG=/tmp/bisect-speed.log
TOL=${TOL:-25}          # mm/s
MAXRUNS=${MAXRUNS:-6}

lo=0                    # highest speed known to COMPLETE
hi=1000                 # lowest speed known to FAIL (measured earlier)
next=$BASE              # first probe

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

echo "Bisecting the completing speed. file sliced $BASE mm/s, ACCEL/SCV from file."
echo "known: 1000 mm/s -> shutdown at 85.65%"
printf "%-6s %-7s %-9s %-11s %-9s %-8s %s\n" run mm/s M220 result reached retx% elapsed
printf '%.0s-' {1..80}; echo

for i in $(seq 1 $MAXRUNS); do
    m220=$(( next * 100 / BASE ))
    recover
    M220=$m220 $H/klippy-venv/bin/python $H/full-print.py "$FILE" 50 100000 0.0 2000 \
        "$H/bisect-run-$next.json" > /tmp/bisect-run-$next.log 2>&1
    state=$($H/klippy-venv/bin/python -c "import json;print(json.load(open('$H/bisect-run-$next.json'))['state'])" 2>/dev/null)
    reach=$($H/klippy-venv/bin/python -c "import json;print(json.load(open('$H/bisect-run-$next.json'))['progress_pct'])" 2>/dev/null)
    retx=$($H/klippy-venv/bin/python -c "import json;print(json.load(open('$H/bisect-run-$next.json'))['retx_pct'])" 2>/dev/null)
    elap=$($H/klippy-venv/bin/python -c "import json;print(json.load(open('$H/bisect-run-$next.json'))['elapsed'])" 2>/dev/null)
    printf "%-6s %-7s %-9s %-11s %-9s %-8s %s\n" "$i" "$next" "${m220}%" "$state" "${reach}%" "$retx" "$elap"

    if [ "$state" = "complete" ]; then lo=$next; else hi=$next; fi
    span=$(( hi - lo ))
    if [ "$span" -le "$TOL" ]; then break; fi
    next=$(( (lo + hi) / 2 ))
done

echo
echo "fastest COMPLETING speed: ${lo} mm/s      lowest FAILING: ${hi} mm/s"
echo "(bracket ${lo}-${hi} mm/s, tolerance ${TOL})"
