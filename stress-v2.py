#!/usr/bin/env python3
"""Stress sweep that PROVES work happened before reporting a result.

The previous harness reported only retransmit deltas. If the moves silently
failed (gcode error, mcu shutdown, wrong stepper name) nothing was sent and it
printed a beautiful "retx+0" that meant "nothing happened", not "clean". That
is almost certainly why the devkit looked perfect on macOS and terrible on
Linux at the same step rate.

So every row now reports:
  wrote     bytes klippy actually sent   -> ~0 means the test DID NOT RUN
  gcode_err count of rejected commands
  retx/inval as before, plus retx as a % of bytes written
"""
import re
import sys
import time
from importlib.machinery import SourceFileLoader

BASE = "/home/parallels/"
K = SourceFileLoader("kapi", BASE + "klippy-api.py").load_module()
LOG = "/tmp/klippy-stress.log"
ALL = ["sx", "sy", "sz", "sa"]
FIELDS = ("bytes_write bytes_retransmit bytes_invalid print_stall "
          "mcu_task_stddev srtt").split()


def stats(retries=10):
    for _ in range(retries):
        try:
            with open(LOG, errors="replace") as fh:
                lines = [l for l in fh if l.startswith("Stats ")]
            if lines:
                return {f: float(m.group(1))
                        for f in FIELDS
                        if (m := re.search(rf"\b{f}=([\d.]+)", lines[-1]))}
        except FileNotFoundError:
            pass
        time.sleep(1)
    raise RuntimeError("no Stats lines in " + LOG)


def run(k, steppers, speed, secs=30, dist=60):
    errs = 0
    for s in steppers:
        r = k.gcode(f"MANUAL_STEPPER STEPPER={s} ENABLE=1 SET_POSITION=0")
        if r and "error" in r:
            errs += 1
    b = stats()
    end = time.time() + secs
    d = dist
    while time.time() < end:
        for s in steppers:
            r = k.gcode(f"MANUAL_STEPPER STEPPER={s} MOVE={d} SPEED={speed} "
                        f"ACCEL=3000 SYNC=0", 120)
            if r and "error" in r:
                errs += 1
        r = k.gcode("M400", 120)
        if r and "error" in r:
            errs += 1
        d = 0 if d else dist
    time.sleep(2.5)
    a = stats()
    wr = a["bytes_write"] - b["bytes_write"]
    rx = a["bytes_retransmit"] - b["bytes_retransmit"]
    iv = a["bytes_invalid"] - b["bytes_invalid"]
    rate = speed * 80 * len(steppers)
    pct = (rx / wr * 100) if wr > 0 else 0.0
    verdict = "NO WORK DONE" if wr < 1000 else f"retx {pct:5.1f}% of traffic"
    print(f"  {len(steppers)}x @{speed:>4}mm/s ({rate:>6} st/s)  "
          f"wrote={int(wr):>7}  retx={int(rx):>7}  inval={int(iv):>5}  "
          f"gerr={errs:<3} jit={a['mcu_task_stddev']*1e6:5.1f}us  {verdict}")


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "?"
    k = K.Klippy()
    st = k.call("info")["result"]["state"]
    print(f"=== {label}   klippy state: {st} ===")
    if st != "ready":
        print("   NOT READY - results meaningless"); sys.exit(1)
    run(k, ALL, 50)                      # warmup, discarded
    print("  --- warmup above discarded ---")
    for sp in (50, 100, 200):
        run(k, ALL, sp)
    run(k, ["sx"], 200)
    k.close()
