#!/bin/zsh
# Read TMC5160 state from all four drivers on the Rodent. READ-ONLY.
#
# Registers chosen because they are actually readable on a TMC5160:
#   0x6C CHOPCONF   (RW) - TOFF in bits 3:0. TOFF==0 means the driver stage is
#                          OFF and no coil current flows at all.
#   0x6F DRV_STATUS (R)  - CS_ACTUAL in bits 20:16, the live current scale.
#   0x00 GCONF      (RW)
# IHOLD_IRUN (0x10) and GLOBAL_SCALER (0x0B) are WRITE-ONLY on this part, so the
# configured current cannot be read back directly - CS_ACTUAL is the observable.
PORT="${1:-/dev/cu.wchusbserial120}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/tmc-dump.log"

"$HERE/esptool-venv/bin/esptool" --port "$PORT" --baud 115200 --after hard-reset \
    --no-stub run >/dev/null 2>&1

cd "$HERE/fermino-esp32" || exit 1

# 4 slots x 5 bytes; same register from every device in one frame.
frame() { printf "%s" "$(printf "%s" "$1"'0000000000000000'"" | head -c0)"; }

{
  sleep 8
  echo "allocate_oids count=1"
  echo "config_spi oid=0 pin=GPIO5 cs_active_high=0"
  echo "spi_set_sw_bus oid=0 miso_pin=GPIO19 mosi_pin=GPIO23 sclk_pin=GPIO18 mode=3 pulse_ticks=240"
  echo "finalize_config crc=0"
  sleep 2
  for reg in 6c 6f 00; do
      f="${reg}00000000${reg}00000000${reg}00000000${reg}00000000"
      echo "spi_transfer oid=0 data=$f"   # latch
      sleep 1
      echo "spi_transfer oid=0 data=$f"   # fetch
      sleep 1
  done
  sleep 4
} | ../klippy-venv/bin/python ./klippy/console.py "$PORT" -b 250000 >"$OUT" 2>&1 &

PID=$!
sleep 28
kill $PID 2>/dev/null; wait $PID 2>/dev/null

python3 - "$OUT" <<'EOF'
import re, sys, ast
txt = open(sys.argv[1], errors='replace').read()
resps = [ast.literal_eval(m) for m in
         re.findall(r"spi_transfer_response oid=0 response=(b'.*?'|b\".*?\")", txt)]
# responses come in latch/fetch pairs per register, in order 6C, 6F, 00
regs = ['CHOPCONF(0x6C)', 'DRV_STATUS(0x6F)', 'GCONF(0x00)']
fetched = resps[1::2]
if len(fetched) < 3:
    print(f"only {len(resps)} responses - check {sys.argv[1]}"); raise SystemExit
for name, r in zip(regs, fetched):
    b = bytearray(r)
    print(f"\n{name}")
    for slot in range(4):
        s = b[slot*5:(slot+1)*5]
        val = int.from_bytes(s[1:], 'big')
        extra = ""
        if name.startswith('CHOPCONF'):
            toff = val & 0xf
            extra = f"  TOFF={toff} -> {'DRIVER OFF (no coil current)' if toff==0 else 'driver enabled'}"
        elif name.startswith('DRV_STATUS'):
            cs = (val >> 16) & 0x1f
            stst = (val >> 31) & 1
            ot  = (val >> 25) & 1
            otpw= (val >> 26) & 1
            extra = (f"  CS_ACTUAL={cs}/31  standstill={stst}"
                     f"{'  OVERTEMP' if ot else ''}{'  otpw' if otpw else ''}")
        print(f"  slot {slot}: 0x{val:08x}{extra}")

# Current implied by CS_ACTUAL, assuming GLOBAL_SCALER at its reset default (=256)
print("\nimplied RMS current per phase (R_sense=0.075, V_fs=0.325V, GLOBALSCALER=256):")
b = bytearray(fetched[1])
for slot in range(4):
    cs = (int.from_bytes(b[slot*5+1:slot*5+5], 'big') >> 16) & 0x1f
    irms = (256/256) * ((cs+1)/32) * (0.325/0.075) / (2**0.5)
    print(f"  slot {slot}: CS={cs:2d} -> {irms:.2f} A rms  (0 A if TOFF=0)")
EOF
