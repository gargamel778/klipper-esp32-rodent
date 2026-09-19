#!/usr/bin/env python3
"""Coordinated cartesian motion stress - the real Klipper motion path.

Streams G1 moves continuously (no M400 barriers, no independent per-stepper
streams), which is what an actual machine does. All three steppers move from one
coordinated move queue.

Reports bytes_write so a silent no-op can't masquerade as a clean result.
"""
import os
import re
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
LOG = "/tmp/klippy-stress.log"
F = "bytes_write bytes_retransmit bytes_invalid print_stall mcu_task_stddev".split()


def stats(retries=10):
    for _ in range(retries):
        try:
            lines = [l for l in open(LOG, errors="replace") if l.startswith("Stats ")]
            if lines:
                return {f: float(m.group(1)) for f in F
                        if (m := re.search(rf"\b{f}=([\d.]+)", lines[-1]))}
        except FileNotFoundError:
            pass
        time.sleep(1)
    raise RuntimeError("no Stats")


def run(k, feed, secs=30, span=60):
    k.gcode("SET_KINEMATIC_POSITION X=0 Y=0 Z=0")
    k.gcode("G90")
    errs = 0
    b = stats()
    end = time.time() + secs
    n = 0
    while time.time() < end:
        # a small square in XY with Z motion - all three steppers coordinated,
        # streamed without waiting so klippy keeps the mcu queue full
        for x, y, z in ((span, 0, 1), (span, span, 2), (0, span, 1), (0, 0, 0)):
            r = k.gcode(f"G1 X{x} Y{y} Z{z} F{feed}", 120)
            if r and "error" in r:
                errs += 1
            n += 1
    k.gcode("M400", 300)
    time.sleep(2.5)
    a = stats()
    wr = a["bytes_write"] - b["bytes_write"]
    rx = a["bytes_retransmit"] - b["bytes_retransmit"]
    iv = a["bytes_invalid"] - b["bytes_invalid"]
    pct = (rx / wr * 100) if wr > 0 else 0.0
    verdict = "NO WORK DONE" if wr < 1000 else f"retx {pct:5.1f}% of traffic"
    print(f"  G1 F{feed:<6} ({feed//60:>3} mm/s)  moves={n:>5}  wrote={int(wr):>7}  "
          f"retx={int(rx):>7}  inval={int(iv):>5}  gerr={errs:<3} "
          f"jit={a['mcu_task_stddev']*1e6:5.1f}us  {verdict}")


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "?"
    k = K.Klippy()
    st = k.call("info")["result"]["state"]
    print(f"=== {label}   klippy state: {st} ===")
    if st != "ready":
        print("   NOT READY"); sys.exit(1)
    run(k, 3000)                     # warmup
    print("  --- warmup above discarded ---")
    for f in (3000, 6000, 12000, 18000):
        run(k, f)
    k.close()
