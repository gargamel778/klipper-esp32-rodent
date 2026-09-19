#!/bin/zsh
# VERIFY #2: probe the TMC5160 SPI daisy chain on the Rodent.
#
# Read-only. Reads register 0x04 (IOIN) from all four drivers in one 20-byte
# chain frame. On a TMC5160 the top byte of IOIN is VERSION = 0x30, so a
# working chain returns 0x30 in every 5-byte slot.
#
# TMC needs SPI mode 3. Software SPI on the board's SPI pins:
#   sclk gpio.18   mosi gpio.23   miso gpio.19   cs gpio.5 (shared by all four)
# pulse_ticks=240 at 240MHz => ~500kHz, comfortably slow.
#
# TMC SPI is pipelined: a read command returns the PREVIOUS command's data, so
# the frame is sent twice and the second response is the real one.
PORT="${1:-/dev/cu.wchusbserial120}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/verify2-tmc.log"

# allocate_oids/finalize_config are once-per-boot: a previous attempt leaves the
# mcu configured and the next run shuts down with "oids already allocated".
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
  echo "spi_transfer oid=0 data=0400000000040000000004000000000400000000"
  sleep 1
  echo "spi_transfer oid=0 data=0400000000040000000004000000000400000000"
  sleep 6
} | ../klippy-venv/bin/python ./klippy/console.py "$PORT" -b 250000 >"$OUT" 2>&1 &

PID=$!
sleep 26
kill $PID 2>/dev/null; wait $PID 2>/dev/null

echo "=== transcript ==="
grep -vE "^  |^$|debugging console|artificial commands|DELAY|FLOOD|SUPPRESS|SET   |DUMP|FILEDUMP|STATS|LIST|HELP|All commands|For example|to user|clock :|freq  :" "$OUT" | tail -20

echo
echo "=== decoding the last chain response ==="
python3 - "$OUT" <<'EOF'
import re, sys, ast
txt = open(sys.argv[1], errors='replace').read()
resps = re.findall(r"spi_transfer_response oid=0 response=(\S+)", txt)
if not resps:
    print("  no spi_transfer_response seen - check the transcript above")
    raise SystemExit
raw = resps[-1]
try:
    data = ast.literal_eval(raw) if raw.startswith(("b'", 'b"', '[')) else None
except Exception:
    data = None
if data is None:
    print("  raw:", raw); raise SystemExit
b = bytearray(data)
print(f"  {len(b)} bytes: {b.hex()}")
if len(b) != 20:
    print("  !! expected 20 bytes for a 4-deep chain"); raise SystemExit
for slot in range(4):
    s = b[slot*5:(slot+1)*5]
    status, ver = s[0], s[1]
    val = int.from_bytes(s[1:], 'big')
    ok = "TMC5160 (VERSION 0x30)" if ver == 0x30 else f"unexpected VERSION 0x{ver:02x}"
    print(f"  slot {slot}: status=0x{status:02x} data=0x{val:08x}  {ok}")
EOF
