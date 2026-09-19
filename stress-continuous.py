#!/usr/bin/env python3
"""Continuous-motion stress, without the M400 stalls of the earlier harness.

Why: the previous test did
    <4 moves with SYNC=0> ; M400   (wait for all to finish)
At low speeds M400 blocks for over a second, leaving the serial link idle. Klipper
sizes its retransmit timeout from a live RTT estimate (srtt/rttvar/rto), so long
idle gaps can produce SPURIOUS retransmits when traffic resumes - which is the most
likely reason the devkit showed MORE errors at LOW rates, an inverted curve that no
real link fault produces.

Here moves are queued continuously so the link never goes idle, and the run is timed
by wall clock rather than by move completion. Any retransmits left are load-related.

  usage: stress-continuous.py <label>
"""
import os
import re
import sys
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
    for f in ("bytes_retransmit", "bytes_invalid", "bytes_write",
              "srtt", "rto", "mcu_task_stddev", "print_stall"):
        m = re.search(rf"\b{f}=([\d.]+)", lines[-1])
        d[f] = float(m.group(1)) if m else 0.0
    return d


def run(k, steppers, speed, secs=30, dist=20):
    for s in steppers:
        k.gcode(f"MANUAL_STEPPER STEPPER={s} ENABLE=1 SET_POSITION=0")
    # prime the queue, then keep it fed without ever waiting on M400
    b = stats()
    end = time.time() + secs
    d = dist
    while time.time() < end:
        for s in steppers:
            k.gcode(f"MANUAL_STEPPER STEPPER={s} MOVE={d} SPEED={speed} "
                    f"ACCEL=3000 SYNC=0", 120)
        d = 0 if d else dist
    k.gcode("M400", 180)
    time.sleep(2.5)
    a = stats()
    rate = speed * 80 * len(steppers)
    wr = a["bytes_write"] - b["bytes_write"]
    rx = a["bytes_retransmit"] - b["bytes_retransmit"]
    pct = (rx / wr * 100) if wr else 0
    print(f"  {len(steppers)}x @ {speed:>3}mm/s ({rate:>6} steps/s)  "
          f"wrote={int(wr):>8}  retx={int(rx):>7} ({pct:5.2f}%)  "
          f"inval={int(a['bytes_invalid']-b['bytes_invalid']):>5}  "
          f"stall+{int(a['print_stall']-b['print_stall'])}  "
          f"srtt={a['srtt']:.4f} rto={a['rto']:.3f}")


if __name__ == "__main__":
    label = sys.argv[1] if len(sys.argv) > 1 else "?"
    k = K.Klippy()
    print(f"=== {label} : continuous motion, no idle gaps ===")
    for sp in (50, 100, 200):
        run(k, ALL, sp)
    run(k, ["sx"], 200)
    k.close()
