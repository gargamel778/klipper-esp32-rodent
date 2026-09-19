#!/usr/bin/env python3
"""Stability stress suite for the BTT Rodent running Klipper.

Drives a live klippy over its unix-socket API and, for each phase, records the
deltas in klippy's own MCU link counters. The numbers that matter:

  bytes_retransmit  serial link errors - MUST NOT grow under load
  bytes_invalid     framing/corruption - MUST stay 0
  send_seq/receive_seq   divergence means the mcu is falling behind
  print_stall       host failed to keep the mcu buffer fed
  freq              clock-sync estimate; large drift means timing trouble
  mcu_task_stddev   scheduler jitter

Every stepper move here goes through the 74HC595 chain, so stepper phases are
simultaneously a shift-register/SPI2 stress test.
"""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, ""
                   "dc30c6a2-844e-4768-ad4a-f58f9380b411/scratchpad")
from importlib.machinery import SourceFileLoader
K = SourceFileLoader("kapi", ""
                     "dc30c6a2-844e-4768-ad4a-f58f9380b411/scratchpad/"
                     "klippy-api.py").load_module()

LOG = "/tmp/klippy-stress.log"
STEPPERS = ["sx", "sy", "sz", "sa"]
FIELDS = ("bytes_retransmit bytes_invalid send_seq receive_seq retransmit_seq "
          "print_stall freq srtt mcu_task_avg mcu_task_stddev mcu_awake").split()


def last_stats():
    """Parse the most recent Stats line out of klippy.log."""
    try:
        with open(LOG, errors="replace") as fh:
            lines = [l for l in fh if l.startswith("Stats ")]
    except FileNotFoundError:
        return None
    if not lines:
        return None
    out = {}
    for f in FIELDS:
        m = re.search(rf"\b{f}=([\d.]+)", lines[-1])
        if m:
            out[f] = float(m.group(1))
    return out


def shutdowns():
    try:
        with open(LOG, errors="replace") as fh:
            t = fh.read()
    except FileNotFoundError:
        return 0
    return len(re.findall(r"Transition to shutdown state|MCU shutdown|"
                          r"Lost communication with MCU", t))


def phase(name, fn, k):
    before, sd0 = last_stats(), shutdowns()
    t0 = time.time()
    err = None
    try:
        fn(k)
    except Exception as e:
        err = str(e)[:120]
    dur = time.time() - t0
    time.sleep(2.5)          # let one more Stats line land
    after, sd1 = last_stats(), shutdowns()
    if not before or not after:
        print(f"  {name:<26} NO STATS"); return
    d = {f: after.get(f, 0) - before.get(f, 0) for f in FIELDS}
    seq_gap = after.get("send_seq", 0) - after.get("receive_seq", 0)
    flag = "OK "
    if d["bytes_retransmit"] > 0 or d["bytes_invalid"] > 0 or sd1 > sd0:
        flag = "!! "
    print(f"  {flag}{name:<26} {dur:5.1f}s  "
          f"retx+{int(d['bytes_retransmit']):<4} inval+{int(d['bytes_invalid']):<3} "
          f"stall+{int(d['print_stall']):<3} seqgap={seq_gap:<3} "
          f"freq={after.get('freq',0):.0f} "
          f"jit={after.get('mcu_task_stddev',0)*1e6:6.1f}us "
          f"shutdowns={sd1-sd0}")
    if err:
        print(f"      error: {err}")


# ---------------- phases ----------------

def p_idle(k):
    time.sleep(45)


def p_one_stepper(k):
    k.gcode("MANUAL_STEPPER STEPPER=sx ENABLE=1 SET_POSITION=0")
    end = time.time() + 45
    d = 100
    while time.time() < end:
        k.gcode(f"MANUAL_STEPPER STEPPER=sx MOVE={d} SPEED=200 ACCEL=3000", 120)
        d = 0 if d else 100


def p_all_steppers(k):
    for s in STEPPERS:
        k.gcode(f"MANUAL_STEPPER STEPPER={s} ENABLE=1 SET_POSITION=0")
    end = time.time() + 45
    d = 100
    while time.time() < end:
        for s in STEPPERS:
            spd = 100 if s == "sz" else 200
            k.gcode(f"MANUAL_STEPPER STEPPER={s} MOVE={d} SPEED={spd} "
                    f"ACCEL=3000 SYNC=0", 120)
        k.gcode("M400", 120)
        d = 0 if d else 100


def p_spi_hammer(k):
    end = time.time() + 45
    i = 0
    while time.time() < end:
        k.gcode(f"DUMP_TMC STEPPER={STEPPERS[i % 4]}", 60)
        i += 1


def p_combined(k):
    for s in STEPPERS:
        k.gcode(f"MANUAL_STEPPER STEPPER={s} ENABLE=1 SET_POSITION=0")
    end = time.time() + 45
    d, i = 100, 0
    while time.time() < end:
        for s in STEPPERS:
            k.gcode(f"MANUAL_STEPPER STEPPER={s} MOVE={d} SPEED=200 "
                    f"ACCEL=3000 SYNC=0", 120)
        k.gcode(f"DUMP_TMC STEPPER={STEPPERS[i % 4]}", 60)
        k.gcode("M400", 120)
        d = 0 if d else 100
        i += 1


def p_cmd_flood(k):
    end = time.time() + 30
    n = 0
    while time.time() < end:
        k.gcode("GET_POSITION", 30)
        n += 1
    print(f"      ({n} commands, {n/30:.0f}/s)")


def p_enable_cycle(k):
    """Rapid enable/disable - hammers the shift register latch path."""
    end = time.time() + 30
    while time.time() < end:
        for s in STEPPERS:
            k.gcode(f"MANUAL_STEPPER STEPPER={s} ENABLE=1", 30)
        for s in STEPPERS:
            k.gcode(f"MANUAL_STEPPER STEPPER={s} ENABLE=0", 30)


if __name__ == "__main__":
    k = K.Klippy()
    info = k.call("info")
    st = (info or {}).get("result", {}).get("state")
    print(f"klippy state: {st}\n")
    if st != "ready":
        print("not ready - aborting"); sys.exit(1)

    print("phase                        dur    link errors                       health")
    print("-" * 108)
    for name, fn in [
        ("1 idle baseline", p_idle),
        ("2 one stepper (SR)", p_one_stepper),
        ("3 four steppers (SR)", p_all_steppers),
        ("4 TMC SPI hammer", p_spi_hammer),
        ("5 steppers + SPI combined", p_combined),
        ("6 gcode command flood", p_cmd_flood),
        ("7 enable/disable cycling", p_enable_cycle),
    ]:
        phase(name, fn, k)
    k.close()
    print("-" * 108)
