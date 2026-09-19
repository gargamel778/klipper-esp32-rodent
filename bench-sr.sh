#!/bin/bash
# Klipper's own 3-stepper step-rate benchmark (docs/Benchmarks.md), walked down a
# ladder of step intervals until the MCU can no longer keep up. The lowest tick
# count that still passes is the board's maximum sustained step rate.
#
# Both firmwares under test are built at the SAME baud so the only variable is
# the shift-register code path. Step pins are the real SR wiring.
#
#   steps/sec = 3 * 240e6 / ticks
BAUD=${BAUD:-1500000}
PORT=/dev/ttyUSB1
H="${H:-$HOME}"
LABEL="${1:-run}"
# PINS=sr (real wiring, default) or PINS=gpio (direct GPIO, no shift register)
if [ "${PINS:-sr}" = "gpio" ]; then
    P0=GPIO4; P1=GPIO13; P2=GPIO16; D0=GPIO2; D1=GPIO14; D2=GPIO15
else
    P0=SR2; P1=SR5; P2=SR10; D0=SR1; D1=SR4; D2=SR9
fi
cd "$H/klipper" || exit 1

run() {
    local ticks=$1
    "$H/klippy-venv/bin/esptool" --port $PORT --baud 115200 \
        --after hard-reset --no-stub run >/dev/null 2>&1
    sleep 1
    {
      sleep 8
      echo "allocate_oids count=3"
      echo "config_stepper oid=0 step_pin=$P0 dir_pin=$D0 invert_step=-1 step_pulse_ticks=0"
      echo "config_stepper oid=1 step_pin=$P1 dir_pin=$D1 invert_step=-1 step_pulse_ticks=0"
      echo "config_stepper oid=2 step_pin=$P2 dir_pin=$D2 invert_step=-1 step_pulse_ticks=0"
      echo "finalize_config crc=0"
      sleep 2
      echo "SET start_clock {clock+freq}"
      echo "SET ticks $ticks"
      for o in 0 1 2; do
        echo "reset_step_clock oid=$o clock={start_clock}"
        echo "set_next_step_dir oid=$o dir=0"
        echo "queue_step oid=$o interval={ticks} count=60000 add=0"
        echo "set_next_step_dir oid=$o dir=1"
        echo "queue_step oid=$o interval=3000 count=1 add=0"
      done
      sleep 10
    } | "$H/klippy-venv/bin/python" ./klippy/console.py $PORT -b "$BAUD" \
        > /tmp/bench-$LABEL-$ticks.log 2>&1 &
    local pid=$!
    sleep 26
    kill $pid 2>/dev/null; wait $pid 2>/dev/null

    local rate=$((3 * 240000000 / ticks / 1000))
    local nq
    nq=$(grep -c "^Eval: queue_step" /tmp/bench-$LABEL-$ticks.log)
    if grep -qiE "shutdown|Rescheduled timer|Stepper too far|took too long" /tmp/bench-$LABEL-$ticks.log; then
        local why
        why=$(grep -oiE "Rescheduled timer in the past|Stepper too far in past|Timer too close|SPI transaction took too long" /tmp/bench-$LABEL-$ticks.log | head -1)
        printf "  ticks=%-5s %5sK steps/s  FAIL (%s)\n" "$ticks" "$rate" "$why"
        return 1
    elif [ "$nq" -ne 3 ]; then
        printf "  ticks=%-5s %5sK steps/s  INCONCLUSIVE (%s/3 queue_step)\n" "$ticks" "$rate" "$nq"
        return 1
    else
        printf "  ticks=%-5s %5sK steps/s  PASS\n" "$ticks" "$rate"
        return 0
    fi
}

echo "=== $LABEL : 3 steppers on ${PINS:-sr} pins ($P0/$P1/$P2), baud $BAUD ==="
BEST=""
for t in 1400 1200 1000 900 800 700 650 600 550 500 450; do
    if run $t; then BEST=$t; else break; fi
done
if [ -n "$BEST" ]; then
    echo "  --> $LABEL best: ticks=$BEST = $((3 * 240000000 / BEST / 1000))K steps/s"
else
    echo "  --> $LABEL: failed even the slowest rung"
fi
