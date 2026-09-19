#!/usr/bin/env python3
"""Exercise each axis in turn and report which ports actually have a motor.

Discriminator is OPEN LOAD (DRV_STATUS bits 30/29, olb/ola). A driver with no
motor on its outputs reports both set; a real coil clears them. Note the flags
are only meaningful AFTER the driver has tried to push current, which is why each
axis is moved before its registers are read.

(sg_result was tried first and is unreliable here - it reads 0 once motion has
stopped, so it only discriminates if you happen to sample mid-move.)

  ./test-axes.py            test all four
  ./test-axes.py sx sy      test only these
"""
import os
import re
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
LOG = "/tmp/klippy-stress.log"

AXES = {                       # name: (shift-register pins, chain_position)
    "sx": ("SR2/SR1/SR0",    1),
    "sy": ("SR5/SR4/SR7",    2),
    "sz": ("SR10/SR9/SR8",   3),
    "sa": ("SR13/SR12/SR15", 4),
}
REV = {"sz": 8}                # rotation_distance differs on Z


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def drv_since(pos):
    """First DRV_STATUS written to the log after byte offset `pos`.

    Reading only new content avoids picking up a stale dump from an earlier axis.
    """
    for _ in range(20):
        with open(LOG, errors="replace") as fh:
            fh.seek(pos)
            hits = re.findall(r"DRV_STATUS:\s+(\S+)", fh.read())
        if hits:
            v = int(hits[0], 16)
            return v, bool((v >> 29) & 1), bool((v >> 30) & 1)   # raw, ola, olb
        time.sleep(0.3)
    return None, None, None


def main(names):
    k = K.Klippy()
    st = k.call("info")["result"]["state"]
    print(f"klippy state: {st}")
    if st != "ready":
        print("not ready - aborting"); return 1
    print()
    results = []
    for n in names:
        pins, cpos = AXES[n]
        dist = REV.get(n, 40)          # one full revolution
        print(f"  --- {n}  ({pins}, chain_position {cpos})  "
              f"1 revolution each way ---", flush=True)
        k.gcode(f"MANUAL_STEPPER STEPPER={n} ENABLE=1 SET_POSITION=0")
        k.gcode(f"MANUAL_STEPPER STEPPER={n} MOVE={dist} SPEED=10 ACCEL=200", 120)
        k.gcode("M400", 120)
        pos = log_size()
        k.gcode(f"DUMP_TMC STEPPER={n}")
        raw, ola, olb = drv_since(pos)
        k.gcode(f"MANUAL_STEPPER STEPPER={n} MOVE=0 SPEED=10 ACCEL=200", 120)
        k.gcode("M400", 120)
        k.gcode(f"MANUAL_STEPPER STEPPER={n} ENABLE=0")
        motor = raw is not None and not (ola and olb)
        results.append((n, pins, cpos, raw, ola, olb, motor))
        print(f"      DRV_STATUS=0x{raw:08x}  ola={int(ola)} olb={int(olb)}   "
              f"{'>>> MOTOR PRESENT <<<' if motor else 'open load - no motor'}",
              flush=True)
        time.sleep(1)

    print()
    print(f"  {'axis':<5}{'SR pins':<18}{'chain':<7}{'DRV_STATUS':<13}verdict")
    print("  " + "-" * 62)
    for n, pins, cpos, raw, ola, olb, motor in results:
        print(f"  {n:<5}{pins:<18}{cpos:<7}0x{raw:08x}   "
              f"{'MOTOR' if motor else '-'}")
    k.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or list(AXES)))
