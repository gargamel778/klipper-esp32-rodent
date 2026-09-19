#!/usr/bin/env python3
"""Isolate WHY four steppers corrupt the serial link but one does not.

Two candidate causes:
  (a) aggregate step rate  - total shift-register transactions/sec
  (b) stepper concurrency  - collisions between steppers wanting the SR at once

Config is 80 steps/mm (rotation_distance 40, 16 microsteps), so
    steps/sec = mm/s * 80 * n_steppers

Comparing 4 steppers @ 50mm/s against 1 stepper @ 200mm/s gives the SAME
aggregate rate (16k steps/s) with different concurrency, which separates them.
"""
import os
import re
import time
from importlib.machinery import SourceFileLoader

B = (""
     "dc30c6a2-844e-4768-ad4a-f58f9380b411/scratchpad/")
K = SourceFileLoader("kapi", B + "klippy-api.py").load_module()
LOG = "/tmp/klippy-stress.log"
ALL = ["sx", "sy", "sz", "sa"]


def stats():
    with open(LOG, errors="replace") as fh:
        lines = [l for l in fh if l.startswith("Stats ")]
    d = {}
    for f in ("bytes_retransmit", "bytes_invalid", "mcu_task_stddev"):
        m = re.search(rf"\b{f}=([\d.]+)", lines[-1])
        d[f] = float(m.group(1)) if m else 0.0
    return d


def run(k, steppers, speed, secs=30):
    for s in steppers:
        k.gcode(f"MANUAL_STEPPER STEPPER={s} ENABLE=1 SET_POSITION=0")
    b = stats()
    end = time.time() + secs
    d = 60
    while time.time() < end:
        for s in steppers:
            k.gcode(f"MANUAL_STEPPER STEPPER={s} MOVE={d} SPEED={speed} "
                    f"ACCEL=3000 SYNC=0", 120)
        k.gcode("M400", 120)
        d = 0 if d else 60
    time.sleep(2.5)
    a = stats()
    rate = speed * 80 * len(steppers)
    print(f"  {len(steppers)} stepper(s) @ {speed:>3} mm/s "
          f"= {rate:>6} steps/s aggregate -> "
          f"retx+{int(a['bytes_retransmit']-b['bytes_retransmit']):<7} "
          f"inval+{int(a['bytes_invalid']-b['bytes_invalid']):<5} "
          f"jit={a['mcu_task_stddev']*1e6:6.1f}us")


if __name__ == "__main__":
    k = K.Klippy()
    print("aggregate-rate sweep (4 steppers):")
    for sp in (25, 50, 100, 200):
        run(k, ALL, sp)
    print("\nsame aggregate rate, different concurrency:")
    run(k, ALL, 50)          # 4 x 50  = 16000 steps/s
    run(k, ["sx"], 200)      # 1 x 200 = 16000 steps/s
    print("\nsingle stepper pushed higher:")
    for sp in (200, 300):
        run(k, ["sx"], sp)
    k.close()
