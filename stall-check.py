#!/usr/bin/env python3
"""Find where the motors actually stop following, using StallGuard sampled
DURING motion.

Why this works: TMC StallGuard (SG_RESULT) measures back-EMF and therefore the
motor's mechanical load. On a healthy spinning motor it reads a substantial,
fairly stable value. When the rotor stops following the field - a stall - the
back-EMF signature collapses and SG_RESULT falls toward 0.

Why the earlier reading was useless: DUMP_TMC was issued after M400, i.e. at
standstill, so it reported a stopped motor every time. The fix is to drive the
axis with manual_stepper SYNC=0, which puts the move in the stepper's own queue
and leaves the gcode queue free, so DRV_STATUS can be polled while it spins.

Interpretation is comparative, not absolute: SG_RESULT has no calibrated units,
so what matters is the trend and where it collapses relative to lower speeds.
"""
import json
import os
import re
import statistics
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
LOG = "/tmp/klippy-stress.log"
AXIS = "sx"
ROT = 40.0                       # mm per revolution
SPEEDS = [50, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600, 700, 800]
SECS_PER_SPEED = 6.0             # spin time per data point


def drv_now():
    """Poll DRV_STATUS without waiting on the motion queue."""
    pos = os.path.getsize(LOG)
    k.gcode(f"DUMP_TMC STEPPER={AXIS}", 20)
    for _ in range(25):
        with open(LOG, errors="replace") as fh:
            fh.seek(pos)
            m = re.search(r"DRV_STATUS:\s+(\S+)", fh.read())
        if m:
            v = int(m.group(1), 16)
            return {"sg": v & 0x3ff, "stst": (v >> 31) & 1,
                    "ola": (v >> 29) & 1, "olb": (v >> 30) & 1,
                    "otpw": (v >> 26) & 1, "cs": (v >> 16) & 0x1f}
        time.sleep(0.05)
    return None


def spin(speed):
    """Spin the axis at `speed` for SECS_PER_SPEED, sampling StallGuard live."""
    dist = speed * SECS_PER_SPEED          # enough travel to run the whole window
    accel = min(20000, max(1000, speed * 40))
    k.gcode(f"MANUAL_STEPPER STEPPER={AXIS} ENABLE=1 SET_POSITION=0")
    k.gcode(f"MANUAL_STEPPER STEPPER={AXIS} MOVE={dist:.1f} SPEED={speed} "
            f"ACCEL={accel} SYNC=0", 60)
    time.sleep(0.6)                         # let it get up to speed
    samples, flags = [], {"otpw": 0, "ola": 0, "olb": 0}
    t0 = time.time()
    while time.time() - t0 < SECS_PER_SPEED - 1.2:
        d = drv_now()
        if d:
            samples.append(d["sg"])
            for f in flags:
                flags[f] |= d[f]
        time.sleep(0.15)
    k.gcode(f"MANUAL_STEPPER STEPPER={AXIS} MOVE={dist:.1f} SPEED={speed} "
            f"ACCEL={accel}", 120)          # sync: wait for completion
    k.gcode(f"MANUAL_STEPPER STEPPER={AXIS} MOVE=0 SPEED=100 ACCEL=2000", 300)
    k.gcode("M400", 300)
    return samples, flags


if __name__ == "__main__":
    k = K.Klippy()
    st = k.call("info")["result"]["state"]
    print(f"klippy state: {st}")
    if st != "ready":
        print("not ready"); sys.exit(1)
    print(f"\naxis {AXIS}, {ROT}mm/rev, StallGuard sampled DURING motion "
          f"({SECS_PER_SPEED}s per speed)\n")
    hdr = (f"{'mm/s':>6}{'rpm':>7}{'steps/s':>9}{'n':>4}"
           f"{'sg_min':>8}{'sg_mean':>9}{'sg_max':>8}{'flags':>8}  note")
    print(hdr); print("-" * len(hdr))
    out = []
    base = None
    for sp in SPEEDS:
        s, fl = spin(sp)
        if not s:
            print(f"{sp:>6}  no samples"); continue
        mn, mx = min(s), max(s)
        mean = statistics.mean(s)
        if base is None and mean > 0:
            base = mean
        rpm = sp / ROT * 60
        note = ""
        if base:
            frac = mean / base
            if mean < 8:
                note = "*** COLLAPSED - stall signature ***"
            elif frac < 0.25:
                note = "** heavy load / near stall **"
            elif frac < 0.5:
                note = "* loaded *"
        f = ",".join(x for x, v in fl.items() if v) or "-"
        print(f"{sp:>6}{rpm:>7.0f}{int(sp*80):>9}{len(s):>4}"
              f"{mn:>8}{mean:>9.1f}{mx:>8}{f:>8}  {note}", flush=True)
        out.append({"mm_s": sp, "rpm": round(rpm), "steps_s": int(sp*80),
                    "n": len(s), "sg_min": mn, "sg_mean": round(mean, 1),
                    "sg_max": mx, "flags": fl})
        time.sleep(1)
    json.dump(out, open(os.path.expanduser("~/stall-check.json"), "w"), indent=1)
    print("\nraw -> ~/stall-check.json")
    k.close()
