#!/bin/zsh
# M2 (software half): sustained stepping load, so the mcu stats windows measure
# steady-state scheduler behaviour instead of a 0.19s burst diluted across 5s.
#
#   ./m2-sustained.sh [ticks] [nsteppers] [nchunks] [port]
#
# queue_step's count field is %hu -> 65535 steps max per command, so continuous
# load requires chaining commands. At ticks=1000 (4.17us/step) each chunk is
# ~0.27s, so 40 chunks ~= 11s of unbroken stepping, spanning >2 stats windows.
#
# This is still a software proxy. It does not replace a scope on a pin --
# it measures main-loop task duration, not timer ISR edge placement.
setopt PIPE_FAIL 2>/dev/null || set -o pipefail

TICKS="${1:-1000}"
NSTEP="${2:-3}"
CHUNKS="${3:-40}"
PORT="${4:-/dev/cu.usbserial-0001}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/m2-sustained-t${TICKS}-n${NSTEP}.log"

STEP_PINS=(GPIO18 GPIO19 GPIO21)
DIR_PINS=(GPIO22 GPIO23 GPIO25)

SECS=$(( CHUNKS * 65535 * TICKS / 240000000 ))
echo "ticks=$TICKS steppers=$NSTEP chunks=$CHUNKS -> ~${SECS}s of continuous stepping"

"$HERE/esptool-venv/bin/esptool" --port "$PORT" --baud 115200 --after hard-reset \
    --no-stub run >/dev/null 2>&1

cd "$HERE/fermino-esp32" || exit 1

RUNTIME=$((SECS + 25))
{
  sleep 8
  echo "allocate_oids count=${NSTEP}"
  for i in $(seq 0 $((NSTEP-1))); do
      echo "config_stepper oid=$i step_pin=${STEP_PINS[$((i+1))]} dir_pin=${DIR_PINS[$((i+1))]} invert_step=-1 step_pulse_ticks=0"
  done
  echo "finalize_config crc=0"
  echo "SET start_clock {clock+freq}"
  for i in $(seq 0 $((NSTEP-1))); do
      echo "reset_step_clock oid=$i clock={start_clock}"
      echo "set_next_step_dir oid=$i dir=0"
      for _ in $(seq 1 $CHUNKS); do
          echo "queue_step oid=$i interval=${TICKS} count=65535 add=0"
      done
  done
  sleep $RUNTIME
} | ../klippy-venv/bin/python ./klippy/console.py "$PORT" -b 250000 >"$OUT" 2>&1 &

PID=$!
sleep $((RUNTIME + 12))
kill $PID 2>/dev/null; wait $PID 2>/dev/null

echo
NQ=$(grep -c "queue_step" "$OUT")
if grep -qiE "Rescheduled timer in the past|Stepper too far in past|shutdown" "$OUT"; then
    echo "SHUTDOWN: $(grep -ioE 'Rescheduled timer in the past|Stepper too far in past|shutdown[^ ]*' "$OUT" | head -1)"
elif grep -qE "^Error:" "$OUT"; then
    echo "ERROR: $(grep -E '^Error:' "$OUT" | head -1)"
elif ! grep -q connected "$OUT"; then
    echo "never connected"
else
    echo "clean run, no shutdown"
fi
echo
python3 "$HERE/stats-analyze.py" "$OUT"
