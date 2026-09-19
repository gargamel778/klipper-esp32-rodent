#!/usr/bin/env python3
"""Reproduce the 'Timer too close' shutdown and capture what caused it.

sched.c:94 fires when a NEWLY ADDED timer already has a waketime in the past.
That is not a step-rate limit - during continuous stepping the stepper timer is
rescheduled from stepper_event's return value and never goes through
sched_add_timer at all. So something is *starting* a timer late.

Prime suspect: command_reset_step_clock (stepper.c:285), which klippy sends when
an axis restarts after being idle. The CoreXY workload stops and restarts
individual axes constantly (Z only moves during hops, E reverses on retractions)
whereas the single-axis square never stops one - which would explain why the
square ran clean to 800mm/s and this dies at 300.

klippy dumps its send queue on shutdown, so the command immediately preceding
the fault is recoverable.
"""
import os
import re
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
LOG = "/tmp/klippy-stress.log"


def run():
    k = K.Klippy()
    if k.call("info")["result"]["state"] != "ready":
        print("not ready"); return 1
    # Go straight to the phase that failed before.
    k.gcode("SET_VELOCITY_LIMIT VELOCITY=500 ACCEL=20000 ACCEL_TO_DECEL=10000 "
            "SQUARE_CORNER_VELOCITY=5")
    k.gcode("SET_KINEMATIC_POSITION X=0 Y=0 Z=0")
    k.gcode("G90"); k.gcode("G92 E0")
    e = 0.0
    z = 0.0
    t0 = time.time()
    n = 0
    while time.time() - t0 < 240:
        z += 0.2
        # layer change
        if not ok(k, f"G1 Z{z:.3f} F1800"): break
        for x0, y0 in ((20.0, 20.0), (110.0, 110.0)):
            # retract / z-hop / travel / unretract  == lots of axis restarts
            e -= 0.8
            if not ok(k, f"G1 E{e:.4f} F2700"): break
            if not ok(k, f"G1 Z{z+0.3:.3f} F1800"): break
            if not ok(k, f"G1 X{x0:.2f} Y{y0:.2f} F30000"): break
            if not ok(k, f"G1 Z{z:.3f} F1800"): break
            e += 0.8
            if not ok(k, f"G1 E{e:.4f} F2700"): break
            # perimeter (both corexy motors) then diagonal infill (one motor)
            for px, py in ((x0+70, y0), (x0+70, y0+70), (x0, y0+70), (x0, y0)):
                e += 70 * 0.045
                if not ok(k, f"G1 X{px:.2f} Y{py:.2f} E{e:.4f} F18000"): break
            for i in range(12):
                off = i * 6.0
                e += off * 1.414 * 0.045
                if not ok(k, f"G1 X{x0+off:.2f} Y{y0:.2f} E{e:.4f} F18000"): break
                e += off * 1.414 * 0.045
                if not ok(k, f"G1 X{x0:.2f} Y{y0+off:.2f} E{e:.4f} F18000"): break
            n += 1
        if failed[0]:
            break
    print(f"  stopped after {n} object-passes, {time.time()-t0:.0f}s")
    print(f"  reason: {failed[0] or 'time limit reached (NO shutdown)'}")
    k.close()
    return 0


failed = [None]


def ok(k, cmd):
    r = k.gcode(cmd, 240)
    if r and "error" in r:
        failed[0] = str(r["error"].get("message", ""))[:120]
        return False
    return True


if __name__ == "__main__":
    sys.exit(run())
