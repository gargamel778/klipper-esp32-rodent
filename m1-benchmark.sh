#!/bin/zsh
# M1: Klipper step-rate benchmark per docs/Benchmarks.md.
#
#   ./m1-benchmark.sh <ticks> [nsteppers] [port]
#
# finalize_config may only run once per MCU boot, so the board is hard-reset
# before every trial. A trial PASSES if the queue drains with no shutdown.
#
# This build advertises STEPPER_STEP_BOTH_EDGE=1, so dedge parameters are used
# (invert_step=-1, step_pulse_ticks=0) => one timer event per step.
setopt PIPE_FAIL 2>/dev/null || set -o pipefail

TICKS="${1:-1000}"
NSTEP="${2:-3}"
PORT="${3:-/dev/cu.usbserial-0001}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/m1-ticks${TICKS}-n${NSTEP}.log"

STEP_PINS=(GPIO18 GPIO19 GPIO21)
DIR_PINS=(GPIO22 GPIO23 GPIO25)

# Reset the MCU so finalize_config is valid again
"$HERE/esptool-venv/bin/esptool" --port "$PORT" --baud 115200 --after hard-reset \
    --no-stub run >/dev/null 2>&1

cd "$HERE/fermino-esp32" || exit 1

{
  # CRITICAL: console.py only knows the mcu's commands after the data dictionary
  # loads. Anything sent before "connected" comes back as "Unknown command" and
  # the benchmark silently measures nothing.
  sleep 8
  echo "allocate_oids count=${NSTEP}"
  for i in $(seq 0 $((NSTEP-1))); do
      echo "config_stepper oid=$i step_pin=${STEP_PINS[$((i+1))]} dir_pin=${DIR_PINS[$((i+1))]} invert_step=-1 step_pulse_ticks=0"
  done
  echo "finalize_config crc=0"
  echo "SET start_clock {clock+freq}"
  echo "SET ticks ${TICKS}"
  for i in $(seq 0 $((NSTEP-1))); do
      echo "reset_step_clock oid=$i clock={start_clock}"
      echo "set_next_step_dir oid=$i dir=0"
      echo "queue_step oid=$i interval={ticks} count=60000 add=0"
      echo "set_next_step_dir oid=$i dir=1"
      echo "queue_step oid=$i interval=3000 count=1 add=0"
  done
  sleep 12
} | ../klippy-venv/bin/python ./klippy/console.py "$PORT" -b 250000 >"$OUT" 2>&1 &

PID=$!
sleep 32
kill $PID 2>/dev/null; wait $PID 2>/dev/null

FREQ=240000000
RESULT=$(( NSTEP * FREQ / TICKS / 1000 ))

echo "--- ticks=${TICKS} steppers=${NSTEP} ---"
NQUEUE=$(grep -c "^Eval: queue_step" "$OUT")
EXPECTED=$NSTEP   # only the {ticks} lines produce an "Eval:" line
if ! grep -q "connected" "$OUT"; then
    echo "INCONCLUSIVE: never connected - see $OUT"; tail -5 "$OUT"
elif grep -q "Unknown command" "$OUT"; then
    echo "INCONCLUSIVE: commands sent before dictionary loaded - see $OUT"
elif [ "$NQUEUE" -ne "$EXPECTED" ]; then
    echo "INCONCLUSIVE: saw $NQUEUE queue_step evals, expected $EXPECTED - see $OUT"
elif grep -qiE "Rescheduled timer in the past|Stepper too far in past|shutdown" "$OUT"; then
    echo "FAIL: $(grep -ioE 'Rescheduled timer in the past|Stepper too far in past|shutdown[^ ]*' "$OUT" | head -1)"
elif grep -qE "^Error:" "$OUT"; then
    echo "INCONCLUSIVE: mcu returned an error - $(grep -E '^Error:' "$OUT" | head -1)"
else
    echo "PASS  => ${RESULT}K steps/sec"
fi
