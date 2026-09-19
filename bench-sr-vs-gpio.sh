#!/bin/bash
# Quantify what the shift register actually costs per step event.
#
# Runs Klipper's own step-rate benchmark (docs/Benchmarks.md) twice on the SAME
# board and firmware: once with step pins on the 74HC595 chain (SR2/SR5/SR10 -
# the real wiring) and once on direct GPIO. The tick difference is the SR
# overhead per step, which is the headroom available to any optimisation of
# gpio_sr_shift_out().
#
# Lower ticks = faster. steps/sec = N * 240e6 / ticks.
PORT=/dev/ttyUSB1
HOME_DIR="${HOME_DIR:-$HOME}"
cd "$HOME_DIR/klipper" || exit 1

run() {                       # run <label> <p0> <p1> <p2> <d0> <d1> <d2> <ticks>
    local label=$1 p0=$2 p1=$3 p2=$4 d0=$5 d1=$6 d2=$7 ticks=$8
    "$HOME_DIR/klippy-venv/bin/esptool" --port $PORT --baud 115200 \
        --after hard-reset --no-stub run >/dev/null 2>&1
    sleep 1
    {
      sleep 8
      echo "allocate_oids count=3"
      echo "config_stepper oid=0 step_pin=$p0 dir_pin=$d0 invert_step=-1 step_pulse_ticks=0"
      echo "config_stepper oid=1 step_pin=$p1 dir_pin=$d1 invert_step=-1 step_pulse_ticks=0"
      echo "config_stepper oid=2 step_pin=$p2 dir_pin=$d2 invert_step=-1 step_pulse_ticks=0"
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
    } | "$HOME_DIR/klippy-venv/bin/python" ./klippy/console.py $PORT -b 250000 \
        > /tmp/bench.log 2>&1 &
    local pid=$!
    sleep 26
    kill $pid 2>/dev/null; wait $pid 2>/dev/null
    local nq
    nq=$(grep -c "^Eval: queue_step" /tmp/bench.log)
    if grep -qiE "shutdown|Rescheduled timer|Stepper too far" /tmp/bench.log; then
        echo "  $label ticks=$ticks -> FAIL ($(grep -oiE 'Rescheduled timer in the past|Stepper too far in past|Timer too close' /tmp/bench.log | head -1))"
        return 1
    elif [ "$nq" -ne 3 ]; then
        echo "  $label ticks=$ticks -> INCONCLUSIVE ($nq/3 queue_step seen)"
        return 1
    else
        echo "  $label ticks=$ticks -> PASS  ($((3 * 240000000 / ticks / 1000))K steps/s)"
        return 0
    fi
}

echo "=== 3-stepper benchmark: SHIFT REGISTER pins (the real wiring) ==="
for t in 2000 1500 1200 1000 900 800; do
    run "SR " SR2 SR5 SR10 SR1 SR4 SR9 $t || break
done

echo
echo "=== 3-stepper benchmark: DIRECT GPIO pins (same board, same firmware) ==="
for t in 900 700 650 600 550; do
    run "GPIO" GPIO4 GPIO13 GPIO16 GPIO2 GPIO14 GPIO15 $t || break
done
