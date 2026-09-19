#!/bin/zsh
# Set all four TMC5160s to 0.8 A rms via raw SPI, using the register values
# Klipper's own tmc5160.py _calc_current() produces for
# run_current=0.8 hold_current=0.8 sense_resistor=0.075:
#
#   GLOBALSCALER (0x0B) = 67      IHOLD_IRUN (0x10) = ihold 31, irun 31
#   -> 0.802 A rms
#
# Writes are addressed with the register's MSB set (reg|0x80), same value to
# every slot in the 20-byte chain frame.
#
# NOTE: this lives in driver RAM only. It is lost when the drivers lose VS, and
# klippy will overwrite it from printer.cfg once it runs.
PORT="${1:-/dev/cu.wchusbserial120}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/tmc-set-current.log"

W_GSCALER="8b000000438b000000438b000000438b00000043"
W_IHOLDRUN="9000001f1f9000001f1f9000001f1f9000001f1f"
R_DRVSTATUS="6f000000006f000000006f000000006f00000000"
R_CHOPCONF="6c000000006c000000006c000000006c00000000"

"$HERE/esptool-venv/bin/esptool" --port "$PORT" --baud 115200 --after hard-reset \
    --no-stub run >/dev/null 2>&1

cd "$HERE/fermino-esp32" || exit 1

{
  sleep 8
  echo "allocate_oids count=1"
  echo "config_spi oid=0 pin=GPIO5 cs_active_high=0"
  echo "spi_set_sw_bus oid=0 miso_pin=GPIO19 mosi_pin=GPIO23 sclk_pin=GPIO18 mode=3 pulse_ticks=240"
  echo "finalize_config crc=0"
  sleep 2
  echo "spi_send oid=0 data=$W_GSCALER"
  sleep 1
  echo "spi_send oid=0 data=$W_IHOLDRUN"
  sleep 1
  # read back: latch then fetch
  echo "spi_transfer oid=0 data=$R_DRVSTATUS"
  sleep 1
  echo "spi_transfer oid=0 data=$R_DRVSTATUS"
  sleep 1
  echo "spi_transfer oid=0 data=$R_CHOPCONF"
  sleep 1
  echo "spi_transfer oid=0 data=$R_CHOPCONF"
  sleep 4
} | ../klippy-venv/bin/python ./klippy/console.py "$PORT" -b 250000 >"$OUT" 2>&1 &

PID=$!
sleep 30
kill $PID 2>/dev/null; wait $PID 2>/dev/null

grep -E "^Error|shutdown|is_shutdown" "$OUT" | head -3

python3 - "$OUT" <<'EOF'
import re, sys, ast, math
txt = open(sys.argv[1], errors='replace').read()
resps = [ast.literal_eval(m) for m in
         re.findall(r"spi_transfer_response oid=0 response=(b'.*?'|b\".*?\")", txt)]
if len(resps) < 4:
    print(f"only {len(resps)} transfer responses - see {sys.argv[1]}"); raise SystemExit
drv, chop = resps[1], resps[3]
VREF, RS, GS = 0.325, 0.075, 67
print("\n=== after write ===")
for slot in range(4):
    d = int.from_bytes(bytearray(drv)[slot*5+1:slot*5+5], 'big')
    c = int.from_bytes(bytearray(chop)[slot*5+1:slot*5+5], 'big')
    cs, toff = (d >> 16) & 0x1f, c & 0xf
    amps = GS*(cs+1)*VREF/(256.*32.*math.sqrt(2.)*RS)
    print(f"  slot {slot}: CS_ACTUAL={cs:2d}/31  TOFF={toff}  -> {amps:.3f} A rms")
EOF
