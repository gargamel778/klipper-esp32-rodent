#!/bin/bash
# Disable all four TMC5160 output stages on the Rodent so a motor can be
# connected/disconnected safely.
#
# Sets CHOPCONF.TOFF = 0. That switches the power stage OFF entirely (coils go
# high-Z) and is stronger than de-asserting an enable pin. Klipper's last value
# was 0x34410153 (toff=3); we write 0x34410150, preserving every other field.
#
# Raw SPI over the shared-CS daisy chain, so it does not depend on any
# printer.cfg being correct.
set -u
PORT="${1:-/dev/ttyUSB1}"
HOME_DIR="${HOME_DIR:-$HOME}"
OUT=/tmp/tmc-off.log

W_CHOPCONF_OFF="ec34410150ec34410150ec34410150ec34410150"   # 0x6C|0x80, toff=0
R_CHOPCONF="6c000000006c000000006c000000006c00000000"
R_DRVSTATUS="6f000000006f000000006f000000006f00000000"

pkill -f klippy.py 2>/dev/null; sleep 2
"$HOME_DIR/klippy-venv/bin/esptool" --port "$PORT" --baud 115200 \
    --after hard-reset --no-stub run >/dev/null 2>&1
sleep 1

cd "$HOME_DIR/klipper" || exit 1
{
  sleep 8
  echo "allocate_oids count=1"
  echo "config_spi oid=0 pin=GPIO5 cs_active_high=0"
  echo "spi_set_sw_bus oid=0 miso_pin=GPIO19 mosi_pin=GPIO23 sclk_pin=GPIO18 mode=3 pulse_ticks=240"
  echo "finalize_config crc=0"
  sleep 2
  echo "spi_send oid=0 data=$W_CHOPCONF_OFF"
  sleep 1
  echo "spi_transfer oid=0 data=$R_CHOPCONF"
  sleep 1
  echo "spi_transfer oid=0 data=$R_CHOPCONF"
  sleep 1
  echo "spi_transfer oid=0 data=$R_DRVSTATUS"
  sleep 1
  echo "spi_transfer oid=0 data=$R_DRVSTATUS"
  sleep 3
} | "$HOME_DIR/klippy-venv/bin/python" ./klippy/console.py "$PORT" -b 250000 >"$OUT" 2>&1 &
PID=$!
sleep 26
kill $PID 2>/dev/null; wait $PID 2>/dev/null

python3 - "$OUT" <<'EOF'
import re, sys, ast
txt = open(sys.argv[1], errors='replace').read()
r = [ast.literal_eval(m) for m in
     re.findall(r"spi_transfer_response oid=0 response=(b'.*?'|b\".*?\")", txt)]
if len(r) < 4:
    print(f"  only {len(r)} responses - check {sys.argv[1]}"); raise SystemExit(1)
chop, drv = r[1], r[3]
allsafe = True
print()
for slot in range(4):
    c = int.from_bytes(bytearray(chop)[slot*5+1:slot*5+5], 'big')
    d = int.from_bytes(bytearray(drv)[slot*5+1:slot*5+5], 'big')
    toff, cs = c & 0xf, (d >> 16) & 0x1f
    ok = toff == 0
    allsafe &= ok
    print(f"  slot {slot}: CHOPCONF=0x{c:08x} TOFF={toff}  CS_ACTUAL={cs}  "
          f"{'OUTPUT STAGE OFF - safe' if ok else '*** STILL ENABLED ***'}")
print()
print("ALL FOUR DRIVERS DISABLED - safe to connect motors" if allsafe
      else "!!! NOT ALL DISABLED - do not connect motors")
EOF
