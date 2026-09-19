#!/usr/bin/env python3
"""Find the ceiling of a REAL sliced print by ramping speed and acceleration.

Each phase prints the SAME leading fraction of the file, so the geometry is
identical between phases and speed/accel is the only variable - the same control
the synthetic ramp had, but on real slicer output.

M220 scales feedrate; accel is scaled alongside it because on 0.5 mm segments
the toolhead is accel-limited and never reaches the commanded feedrate, so M220
alone would barely change the load. The slicer's own accel commands are
neutralised in the bench config for the same reason.

Stops at the first shutdown, and recovers the MCU afterwards so a failed phase
does not poison whatever runs next.

Usage: print-ramp.py <file.gcode> [target_pct] [out.json]
"""
import json
import os
import re
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
LOG = "/tmp/klippy-stress.log"

# (M220 %, accel). 100% is the sliced 200 mm/s at the sliced 5000 accel.
RAMP = [
    (100, 5000),
    (150, 7500),
    (200, 10000),
    (300, 20000),
    (400, 30000),
    (500, 40000),
]

FIELDS = ("bytes_write bytes_retransmit bytes_invalid print_stall buffer_time "
          "mcu_task_avg mcu_task_stddev").split()


def stats():
    for _ in range(10):
        try:
            ls = [l for l in open(LOG, errors="replace") if l.startswith("Stats ")]
        except FileNotFoundError:
            ls = []
        if ls:
            out = {}
            for f in FIELDS:
                m = re.search(rf"\b{f}=([\d.]+)", ls[-1])
                if m:
                    out[f] = float(m.group(1))
            return out
        time.sleep(1)
    return {}


def shutdowns():
    try:
        t = open(LOG, errors="replace").read()
    except FileNotFoundError:
        return 0, ""
    h = re.findall(r"(?:MCU '\w+' shutdown|Transition to shutdown state): ?(.*)", t)
    return len(h), (h[-1][:70] if h else "")


def phase(k, fn, scale, accel, target):
    k.gcode("CLEAR_PAUSE")
    k.gcode(f"SET_VELOCITY_LIMIT ACCEL={accel} ACCEL_TO_DECEL={accel // 2} "
            f"SQUARE_CORNER_VELOCITY=5")
    k.gcode(f"M220 S{scale}")
    sd0, _ = shutdowns()
    b = stats()
    t0 = time.time()
    r = k.gcode(f"SDCARD_PRINT_FILE FILENAME={fn}", 60)
    err = ""
    if r and "error" in r:
        err = str(r["error"].get("message", ""))[:90]
    buf = []
    prog = 0.0
    while not err and time.time() - t0 < 1800:
        time.sleep(5)
        try:
            q = k.status({"virtual_sdcard": None, "print_stats": None})
            vs = q["result"]["status"]["virtual_sdcard"]
            ps = q["result"]["status"]["print_stats"]
        except Exception as e:
            err = f"api: {e}"[:90]
            break
        prog = vs.get("progress", 0) or 0
        s = stats()
        if "buffer_time" in s:
            buf.append(s["buffer_time"])
        if ps.get("state") in ("error", "cancelled"):
            err = f"{ps.get('state')}: {ps.get('message', '')}"[:90]
            break
        if ps.get("state") == "complete" or prog >= target:
            break
    el = time.time() - t0
    a = stats()
    sd1, msg = shutdowns()
    wr = a.get("bytes_write", 0) - b.get("bytes_write", 0)
    rx = a.get("bytes_retransmit", 0) - b.get("bytes_retransmit", 0)
    if sd1 == sd0 and not err:
        k.gcode("CANCEL_PRINT", 60)
        time.sleep(1)
    return {
        "m220": scale, "accel": accel, "mm_s": scale * 2,
        "dur": round(el, 1), "progress_pct": round(prog * 100, 2),
        "wrote": int(wr), "retx": int(rx),
        "retx_pct": round(rx / wr * 100, 3) if wr > 0 else None,
        "kbps": round(wr / el / 1000, 2) if el else 0,
        "invalid": int(a.get("bytes_invalid", 0) - b.get("bytes_invalid", 0)),
        "stalls": int(a.get("print_stall", 0) - b.get("print_stall", 0)),
        "buf_min": round(min(buf), 3) if buf else None,
        "buf_mean": round(sum(buf) / len(buf), 3) if buf else None,
        "jit_us": round(a.get("mcu_task_stddev", 0) * 1e6, 1),
        "shutdowns": sd1 - sd0, "sd_msg": msg if sd1 > sd0 else "", "err": err,
    }


def main():
    fn = sys.argv[1]
    target = (float(sys.argv[2]) if len(sys.argv) > 2 else 8.0) / 100.0
    out_path = sys.argv[3] if len(sys.argv) > 3 else os.path.expanduser("~/print-ramp.json")

    k = K.Klippy()
    if k.call("info")["result"]["state"] != "ready":
        print("klippy not ready"); return 1
    print(f"REAL PRINT ramp - {fn}, each phase prints the first {target*100:.0f}% "
          f"of the file\n")
    hdr = (f"{'M220':>5}{'mm/s':>6}{'accel':>7}{'dur':>7}{'file%':>7}"
           f"{'kB/s':>7}{'retx%':>8}{'inval':>7}{'stall':>6}"
           f"{'buf_min':>9}{'jit':>7}  verdict")
    print(hdr); print("-" * len(hdr))
    out = []
    for scale, accel in RAMP:
        r = phase(k, fn, scale, accel, target)
        out.append(r)
        v = "OK"
        if r["shutdowns"]:
            v = f"SHUTDOWN: {r['sd_msg'][:32]}"
        elif r["err"]:
            v = f"ERR: {r['err'][:32]}"
        elif (r["retx_pct"] or 0) > 5 or r["stalls"]:
            v = "DEGRADED"
        print(f"{r['m220']:>5}{r['mm_s']:>6}{r['accel']:>7}{r['dur']:>7.0f}"
              f"{r['progress_pct']:>7.2f}{r['kbps']:>7.1f}"
              f"{(r['retx_pct'] if r['retx_pct'] is not None else -1):>8.3f}"
              f"{r['invalid']:>7}{r['stalls']:>6}"
              f"{(r['buf_min'] if r['buf_min'] is not None else -1):>9.3f}"
              f"{r['jit_us']:>6.0f}u  {v}", flush=True)
        json.dump(out, open(out_path, "w"), indent=1)
        if r["shutdowns"] or r["err"]:
            print("\n  ceiling reached - recovering MCU and stopping")
            k.gcode("FIRMWARE_RESTART", 120)
            time.sleep(8)
            break
        time.sleep(4)
    print(f"\nraw -> {out_path}")
    k.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
