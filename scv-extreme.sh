#!/bin/bash
# Extension of the SCV matrix into speed-benchy territory.
#
# set_max_velocities() in toolhead.py assigns max_accel/max_velocity directly
# with no clamp against the config values, so SET_VELOCITY_LIMIT can go far above
# the configured 50000 / 800. VELOCITY is raised alongside ACCEL here and M220 is
# pushed to 1000% so that neither the configured ceiling nor the file's own
# 200 mm/s feedrate can be the binding limit - at these accelerations the planner
# would otherwise slam into the feedrate cap and the accel change would show
# nothing, which is exactly the trap the first ramp fell into.
#
# At 500k mm/s^2 a single 0.54 mm segment supports dv = sqrt(2*a*d) = 735 mm/s,
# so the toolhead can genuinely reach four-figure velocities between junctions.
# That is ~80,000 steps/s per motor - the regime where the synthetic tests DID
# find trouble, which is the point of going here.
#
# Unloaded motors, no filament, no heaters: these settings are for stressing the
# electronics, not for printing anything.
H="${H:-$HOME}"
OUT=$H/scv-extreme.json
TARGET=${TARGET:-8}
rm -f "$OUT"

# label:scv:accel:mcr:velocity:m220
ROWS=(
  "7  SCV40 a100k:40:100000:0.0:2000:1000"
  "8  SCV50 a200k:50:200000:0.0:2000:1000"
  "9  SCV60 a500k:60:500000:0.0:2000:1000"
  "10 SCV100 a500k:100:500000:0.0:2000:1000"
  "11 SCV150 a1M:150:1000000:0.0:3000:1500"
)

echo "EXTREME matrix - real Benchy, first ${TARGET}%, ICV 5, config caps overridden"
printf "%-22s %5s %8s %5s %7s %7s %7s %8s %7s %6s %8s  %s\n" \
   label SCV accel MCR dur file% kB/s retx% inval stall buf_min verdict
printf '%.0s-' {1..126}; echo

for row in "${ROWS[@]}"; do
    IFS=":" read -r label scv accel mcr vel m220 <<<"$row"
    VEL=$vel M220=$m220 $H/klippy-venv/bin/python $H/scv-row.py benchy.gcode \
        "$TARGET" "$scv" "$accel" "$mcr" "$label" "$OUT"
    sleep 3
done
echo
echo "raw -> $OUT"
