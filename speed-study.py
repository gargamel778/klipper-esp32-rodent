#!/usr/bin/env python3
"""Graduated speed/acceleration study for Klipper on the BTT Rodent (ESP32).

Each phase runs a fixed 180s of continuous coordinated XY motion at a set
velocity/acceleration, sampling health metrics throughout. The ramp continues
until something actually breaks, then stops and reports where.

WHAT IS AND IS NOT MEASURABLE HERE
  Measurable: serial link integrity, MCU scheduler health, host keep-up,
  TMC driver fault flags, thermal pre-warning, StallGuard load.
  NOT measurable: mechanical step loss. There are no encoders, so a motor that
  skips still "returns to 0" as far as Klipper is concerned. StallGuard collapse
  and audible/visual checks are the only proxies - flagged, never assumed.

Motion pattern: a 120mm square. Sides are single-axis moves so each axis reaches
commanded velocity; 120mm is long enough to reach it even at the top of the ramp
(v^2/2a = 90mm at 600mm/s & 20000mm/s^2).
"""
import os
import json
import re
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
LOG = "/tmp/klippy-stress.log"
STEPS_PER_MM = 80                       # 16 microsteps, rotation_distance 40
SIDE = 120.0
PHASE_SECS = 180

# (velocity mm/s, accel mm/s^2). Accel scales with velocity to keep the
# accelerate/cruise ratio roughly constant across phases.
RAMP = [
    (50,   1000),
    (100,  2000),
    (150,  3000),
    (200,  5000),
    (300,  8000),
    (400, 12000),
    (500, 16000),
    (600, 20000),
    (800, 30000),
]
AXES = ["stepper_x", "stepper_y", "stepper_z"]
SFIELDS = ("bytes_write bytes_retransmit bytes_invalid print_stall "
           "mcu_task_avg mcu_task_stddev mcu_awake freq srtt").split()


def stats():
    for _ in range(12):
        try:
            L = [l for l in open(LOG, errors="replace") if l.startswith("Stats ")]
            if L:
                return {f: float(m.group(1)) for f in SFIELDS
                        if (m := re.search(rf"\b{f}=([\d.]+)", L[-1]))}
        except FileNotFoundError:
            pass
        time.sleep(1)
    return {}


def log_bytes():
    try:
        import os
        return os.path.getsize(LOG)
    except OSError:
        return 0


def drv(k, axis):
    """DUMP_TMC one axis and decode the fault-relevant bits."""
    import os
    pos = log_bytes()
    k.gcode(f"DUMP_TMC STEPPER={axis}")
    for _ in range(20):
        with open(LOG, errors="replace") as fh:
            fh.seek(pos)
            m = re.search(r"DRV_STATUS:\s+(\S+)", fh.read())
        if m:
            v = int(m.group(1), 16)
            return {
                "raw": v,
                "stst": (v >> 31) & 1, "olb": (v >> 30) & 1, "ola": (v >> 29) & 1,
                "s2gb": (v >> 28) & 1, "s2ga": (v >> 27) & 1,
                "otpw": (v >> 26) & 1, "ot": (v >> 25) & 1,
                "cs": (v >> 16) & 0x1f, "sg": v & 0x3ff,
            }
        time.sleep(0.3)
    return None


def shutdown_count():
    try:
        t = open(LOG, errors="replace").read()
    except FileNotFoundError:
        return 0, ""
    hits = re.findall(r"(?:MCU '\w+' shutdown|Transition to shutdown state): ?(.*)", t)
    return len(hits), (hits[-1][:70] if hits else "")


def phase(k, vel, acc):
    """One 180s phase. Returns a result dict."""
    k.gcode(f"SET_VELOCITY_LIMIT VELOCITY={vel} ACCEL={acc} "
            f"ACCEL_TO_DECEL={acc//2} SQUARE_CORNER_VELOCITY=5")
    k.gcode("SET_KINEMATIC_POSITION X=0 Y=0 Z=0")
    k.gcode("G90")
    sd0, _ = shutdown_count()
    b = stats()
    t0 = time.time()
    laps = 0
    gerr = 0
    samples = []
    err_msg = ""
    while time.time() - t0 < PHASE_SECS:
        for x, y in ((SIDE, 0), (SIDE, SIDE), (0, SIDE), (0, 0)):
            r = k.gcode(f"G1 X{x} Y{y} F{int(vel*60)}", 240)
            if r and "error" in r:
                gerr += 1
                err_msg = str(r["error"].get("message", ""))[:90]
                break
        if gerr:
            break
        laps += 1
        # sample driver state every ~8 laps without stalling the queue
        if laps % 8 == 0:
            d = drv(k, "stepper_x")
            if d:
                samples.append(d)
    k.gcode("M400", 300)
    time.sleep(2.5)
    a = stats()
    sd1, sd_msg = shutdown_count()

    wr = a.get("bytes_write", 0) - b.get("bytes_write", 0)
    rx = a.get("bytes_retransmit", 0) - b.get("bytes_retransmit", 0)
    iv = a.get("bytes_invalid", 0) - b.get("bytes_invalid", 0)
    st = a.get("print_stall", 0) - b.get("print_stall", 0)
    dur = time.time() - t0
    dist = laps * SIDE * 4
    # peak single-axis step rate actually commanded
    rate = vel * STEPS_PER_MM
    return {
        "vel": vel, "accel": acc, "dur": round(dur, 1), "laps": laps,
        "mm": round(dist), "step_rate": int(rate),
        "wrote": int(wr), "retx": int(rx),
        "retx_pct": round(rx / wr * 100, 2) if wr > 0 else None,
        "invalid": int(iv), "stalls": int(st), "gerr": gerr, "err": err_msg,
        "jit_us": round(a.get("mcu_task_stddev", 0) * 1e6, 1),
        "task_us": round(a.get("mcu_task_avg", 0) * 1e6, 2),
        "awake": round(a.get("mcu_awake", 0), 3),
        "freq": int(a.get("freq", 0)), "srtt": a.get("srtt", 0),
        "shutdowns": sd1 - sd0, "sd_msg": sd_msg if sd1 > sd0 else "",
        "otpw": max((s["otpw"] for s in samples), default=0),
        "ot": max((s["ot"] for s in samples), default=0),
        "s2g": max((s["s2ga"] | s["s2gb"] for s in samples), default=0),
        "sg_min": min((s["sg"] for s in samples), default=None),
        "sg_max": max((s["sg"] for s in samples), default=None),
        "cs": samples[-1]["cs"] if samples else None,
    }


def main():
    k = K.Klippy()
    st = k.call("info")["result"]["state"]
    print(f"klippy state: {st}")
    if st != "ready":
        print("not ready - aborting"); return 1
    print(f"\n{len(RAMP)} phases x {PHASE_SECS}s, 120mm square, "
          f"{STEPS_PER_MM} steps/mm\n")
    hdr = (f"{'vel':>5}{'accel':>7}{'steps/s':>9}{'laps':>6}{'mm':>8}"
           f"{'wrote':>8}{'retx%':>7}{'inval':>7}{'stall':>6}"
           f"{'jit':>7}{'otpw':>5}{'sg':>10}  verdict")
    print(hdr); print("-" * len(hdr))
    out = []
    for vel, acc in RAMP:
        r = phase(k, vel, acc)
        out.append(r)
        sg = (f"{r['sg_min']}-{r['sg_max']}"
              if r["sg_min"] is not None else "-")
        bad = (r["shutdowns"] or r["gerr"] or (r["retx_pct"] or 0) > 25
               or r["invalid"] > 100 or r["stalls"])
        verdict = "OK"
        if r["shutdowns"]:
            verdict = f"SHUTDOWN: {r['sd_msg'][:38]}"
        elif r["gerr"]:
            verdict = f"GCODE ERR: {r['err'][:38]}"
        elif bad:
            verdict = "DEGRADED"
        print(f"{r['vel']:>5}{r['accel']:>7}{r['step_rate']:>9}{r['laps']:>6}"
              f"{r['mm']:>8}{r['wrote']:>8}"
              f"{(r['retx_pct'] if r['retx_pct'] is not None else -1):>7}"
              f"{r['invalid']:>7}{r['stalls']:>6}{r['jit_us']:>6}u"
              f"{r['otpw']:>5}{sg:>10}  {verdict}", flush=True)
        if r["shutdowns"] or r["gerr"]:
            print("\n  ceiling reached - stopping ramp")
            break
        time.sleep(3)
    json.dump(out, open(os.path.expanduser("~/speed-study.json"), "w"), indent=1)
    print(f"\nraw results -> ~/speed-study.json")
    k.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
