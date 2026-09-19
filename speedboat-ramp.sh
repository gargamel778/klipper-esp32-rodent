#!/bin/bash
# Stage 1 for the speedboat file: find the motion settings that actually help it.
#
# Row 1 is the as-sliced control (SCV 5 = Klipper default, accel 10000 = what the
# slicer set, M220 100% = the sliced 500 mm/s). Everything is measured against
# that, so any claimed improvement is against the file as the slicer produced it
# rather than against an arbitrarily hobbled starting point.
#
# The last rows raise M220. That is equivalent to having sliced faster and is
# reported separately from the as-sliced result, not folded into it.
H="${H:-$HOME}"
OUT=$H/speedboat-ramp.json
TARGET=${TARGET:-8}
rm -f "$OUT"

# label:scv:accel:mcr:velocity:m220
ROWS=(
  "1 as-sliced:5:10000:0.5:600:100"
  "2 SCV15:15:10000:0.5:600:100"
  "3 SCV30 a50k:30:50000:0.0:600:100"
  "4 SCV50 a100k:50:100000:0.0:600:100"
  "5 SCV50 a100k M220x2:50:100000:0.0:1200:200"
  "6 SCV80 a200k M220x3:80:200000:0.0:2000:300"
)

echo "SPEEDBOAT ramp - first ${TARGET}% of speedboat.gcode (91.9 m path, 192 layers)"
printf "%-22s %5s %8s %5s %7s %7s %7s %8s %7s %6s %8s  %s\n" \
   label SCV accel MCR dur file% kB/s retx% inval stall buf_min verdict
printf '%.0s-' {1..126}; echo

for row in "${ROWS[@]}"; do
    IFS=":" read -r label scv accel mcr vel m220 <<<"$row"
    VEL=$vel M220=$m220 $H/klippy-venv/bin/python $H/scv-row.py speedboat.gcode \
        "$TARGET" "$scv" "$accel" "$mcr" "$label" "$OUT"
    sleep 3
done
echo
echo "raw -> $OUT"
