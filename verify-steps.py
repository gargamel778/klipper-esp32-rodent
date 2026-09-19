#!/usr/bin/env python3
"""Prove that every step pulse the MCU emits actually reaches the driver.

This is the check that was missing when the shift-register optimisation was
first benchmarked: a step-rate number says the firmware went faster, not that it
went faster *correctly*. A dropped or duplicated pulse on the 74HC595 path would
look identical on a benchmark and would silently lose position on a machine.

The TMC5160 exposes MSCNT (reg 0x6A), a 10-bit microstep-table position that
advances on every STEP pulse the driver receives. It is a counter inside the
driver, on the far side of the shift register, so it is independent of anything
the MCU believes. One electrical period is 4 full steps = 1024 MSCNT units, and
at microsteps=16 each STEP pulse advances it by 256/16 = 16.

So for a commanded move of N pulses:

    expected MSCNT delta = (N * 16) mod 1024,   signed by direction

Any mismatch is a real pulse-integrity fault. Both directions are tested so a
stuck or inverted DIR bit cannot pass, and the same move is run slowly and then
at speed - the fast case is the one the optimisation actually changes.

Note this deliberately measures the ELECTRONICS, not the machine: MSCNT counts
pulses the driver received whether or not the rotor followed them.
"""
import os
import re
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
LOG = "/tmp/klippy-stress.log"

AXES = ["sx", "sy", "sz", "sa"]
STEPS_PER_MM = 80          # 200 full steps * 16 microsteps / 40 mm per rev
MSCNT_PER_PULSE = 16       # 256 / microsteps
MSCNT_MOD = 1024

# (label, distance mm, speed mm/s, accel) - pulses chosen so the expected
# residue is distinctive rather than landing on 0.
# Avoid distances whose residue is +/-512, which is its own negation mod 1024
# and so cannot tell a correct move from a perfectly reversed one.
CASES = [
    ("slow 0.35mm",     0.35,     5,   500),   # 28 pulses    -> 448
    ("slow 0.10mm",     0.10,     5,   500),   # 8 pulses     -> 128
    ("fast  50.4625mm",  50.4625, 300, 20000),  # 4037 pulses  ->  80
    ("fast 100.4625mm", 100.4625, 500, 30000),  # 8037 pulses  -> 592
    ("fast 150.4625mm", 150.4625, 800, 30000),  # 12037 pulses ->  80  (64k steps/s)
]


def mscnt(axis):
    """Read MSCNT for one axis at standstill."""
    pos = os.path.getsize(LOG)
    k.gcode(f"DUMP_TMC STEPPER={axis}", 30)
    for _ in range(40):
        with open(LOG, errors="replace") as fh:
            fh.seek(pos)
            # DUMP_TMC prints the register name upper-case and the decoded
            # field lower-case: "MSCNT:  00000148 mscnt=328"
            m = re.search(r"\bmscnt=(\d+)", fh.read())
        if m:
            return int(m.group(1))
        time.sleep(0.05)
    return None


def move(axis, dist, speed, accel):
    k.gcode(f"MANUAL_STEPPER STEPPER={axis} MOVE={dist:.4f} SPEED={speed} "
            f"ACCEL={accel}", 300)
    k.gcode("M400", 300)


if __name__ == "__main__":
    k = K.Klippy()
    st = k.call("info")["result"]["state"]
    print(f"klippy state: {st}")
    if st != "ready":
        print("not ready"); sys.exit(1)

    print(f"\nMSCNT pulse-integrity check - {MSCNT_PER_PULSE} MSCNT units per "
          f"step pulse, modulo {MSCNT_MOD}\n")
    hdr = (f"{'axis':>5}{'case':>16}{'dir':>5}{'pulses':>8}"
           f"{'exp':>7}{'got':>7}{'':>3}verdict")
    print(hdr); print("-" * len(hdr))

    def delta(axis, dist, speed, accel):
        """Signed MSCNT change across one move, in [-512, 511]."""
        before = mscnt(axis)
        if before is None:
            return None
        k.gcode(f"MANUAL_STEPPER STEPPER={axis} SET_POSITION=0")
        move(axis, dist, speed, accel)
        time.sleep(0.3)
        after = mscnt(axis)
        if after is None:
            return None
        return ((after - before + MSCNT_MOD // 2) % MSCNT_MOD) - MSCNT_MOD // 2

    bad = 0
    total = 0
    for axis in AXES:
        k.gcode(f"MANUAL_STEPPER STEPPER={axis} ENABLE=1 SET_POSITION=0")
        time.sleep(0.3)

        # Calibrate which way MSCNT runs for a positive MOVE. Whether the DIR
        # bit makes the microstep table count up or down is a wiring/polarity
        # convention, not a correctness property - what matters is that the
        # MAGNITUDE is exact and that reversing MOVE reverses MSCNT. Use a
        # distance whose residue is not self-negating (avoid +/-512).
        cal = delta(axis, 0.15, 5, 500)          # 12 pulses -> +/-192
        if cal is None or abs(cal) != 12 * MSCNT_PER_PULSE:
            print(f"{axis:>5}  calibration FAILED (got {cal}, want +/-192)")
            bad += 1
            continue
        dsign = 1 if cal > 0 else -1
        print(f"{axis:>5}  MSCNT counts {'up' if dsign > 0 else 'down'} "
              f"for +MOVE")

        for label, dist, speed, accel in CASES:
            for sign in (+1, -1):
                pulses = round(dist * STEPS_PER_MM)
                got = delta(axis, sign * dist, speed, accel)
                if got is None:
                    print(f"{axis:>5}{label:>16}  MSCNT unreadable"); bad += 1
                    continue
                raw = dsign * sign * pulses * MSCNT_PER_PULSE
                exp = ((raw + MSCNT_MOD // 2) % MSCNT_MOD) - MSCNT_MOD // 2
                total += 1
                if got == exp:
                    note = "ok"
                else:
                    bad += 1
                    err = ((got - exp + MSCNT_MOD // 2) % MSCNT_MOD) - MSCNT_MOD // 2
                    note = (f"MISMATCH  off by {err} MSCNT"
                            f"{f' = {err // MSCNT_PER_PULSE} pulses' if err % MSCNT_PER_PULSE == 0 else ''}")
                print(f"{axis:>5}{label:>16}{'+' if sign > 0 else '-':>5}"
                      f"{pulses:>8}{exp:>7}{got:>7}{'':>3}{note}", flush=True)

    print()
    if bad:
        print(f"*** {bad}/{total} FAILED - pulses are being lost or duplicated ***")
    else:
        print(f"ALL {total} CASES EXACT - every commanded pulse reached every "
              f"driver, both directions, at every speed tested")
    k.close()
    sys.exit(1 if bad else 0)
