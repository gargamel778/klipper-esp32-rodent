#!/bin/zsh
# M0: connect console.py to the ESP32, hold the link open for a few seconds,
# then stop. console.py is an interactive REPL and does not exit on stdin EOF,
# so it needs to be killed explicitly.
SECS="${2:-12}"
PORT="${1:-/dev/cu.usbserial-0001}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/m0-console.log"

cd "$HERE/fermino-esp32" || exit 1
rm -f "$OUT"

( printf 'get_uptime\nget_clock\n'; sleep "$SECS" ) \
    | ../klippy-venv/bin/python ./klippy/console.py "$PORT" -b 250000 >"$OUT" 2>&1 &
PID=$!
sleep $((SECS + 4))
kill $PID 2>/dev/null
wait $PID 2>/dev/null

echo "=== console.py output ==="
cat "$OUT"
