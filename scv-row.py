#!/usr/bin/env python3
"""Run one row of the SCV/MCR matrix: print a fixed fraction of the file and
report what it cost.

M220 is pinned at 300% for every row so the file's own feedrate (200 mm/s) can
never be the binding limit - that leaves SCV, accel and MCR as the only things
varying. Row 1 duplicates settings already measured in the speed ramp
(SCV 5, accel 20000 -> 175 s) so the matrix is anchored to existing data rather
than floating free.

Usage: scv-row.py <file> <target_pct> <scv> <accel> <mcr> <label> <out.json>
"""
import json
import re
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
import os
LOG = "/tmp/klippy-stress.log"
# Overridable so the extreme matrix can lift the feedrate and velocity ceilings
# out of the way; at very high accel the planner would otherwise be capped by
# the file feedrate and the accel change would measure nothing.
M220 = int(os.environ.get("M220", "300"))
VEL = os.environ.get("VEL")

F = ("bytes_write bytes_retransmit bytes_invalid print_stall buffer_time "
     "mcu_task_avg mcu_task_stddev").split()


def stats():
    for _ in range(10):
        try:
            ls = [l for l in open(LOG, errors="replace") if l.startswith("Stats ")]
        except FileNotFoundError:
            ls = []
        if ls:
            return {f: float(m.group(1)) for f in F
                    if (m := re.search(rf"\b{f}=([\d.]+)", ls[-1]))}
        time.sleep(1)
    return {}


def shutdowns():
    try:
        t = open(LOG, errors="replace").read()
    except FileNotFoundError:
        return 0, ""
    h = re.findall(r"(?:MCU '\w+' shutdown|Transition to shutdown state): ?(.*)", t)
    return len(h), (h[-1][:70] if h else "")


def main():
    fn, target, scv, accel, mcr, label, out = (
        sys.argv[1], float(sys.argv[2]) / 100.0, float(sys.argv[3]),
        int(sys.argv[4]), float(sys.argv[5]), sys.argv[6], sys.argv[7])

    k = K.Klippy()
    if k.call("info")["result"]["state"] != "ready":
        print(f"{label}: klippy not ready"); return 1
    k.gcode("CLEAR_PAUSE")
    vel = f" VELOCITY={VEL}" if VEL else ""
    r = k.gcode(f"SET_VELOCITY_LIMIT ACCEL={accel}{vel} "
                f"SQUARE_CORNER_VELOCITY={scv} MINIMUM_CRUISE_RATIO={mcr}", 30)
    if r and "error" in r:
        print(f"{label}: limit rejected: {r['error'].get('message','')[:80]}")
        return 1
    k.gcode(f"M220 S{M220}")

    sd0, _ = shutdowns()
    b = stats()
    t0 = time.time()
    r = k.gcode(f"SDCARD_PRINT_FILE FILENAME={fn}", 60)
    err = "" if not (r and "error" in r) else str(r["error"].get("message", ""))[:80]
    bufs, prog = [], 0.0
    while not err and time.time() - t0 < 1200:
        time.sleep(5)
        try:
            q = k.status({"virtual_sdcard": None, "print_stats": None})
            vs = q["result"]["status"]["virtual_sdcard"]
            ps = q["result"]["status"]["print_stats"]
        except Exception as e:
            err = f"api {e}"[:80]; break
        prog = vs.get("progress", 0) or 0
        s = stats()
        if "buffer_time" in s:
            bufs.append(s["buffer_time"])
        if ps.get("state") in ("error", "cancelled"):
            err = f"{ps.get('state')}: {ps.get('message','')}"[:80]; break
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
    res = {
        "label": label, "scv": scv, "accel": accel, "mcr": mcr, "m220": M220,
        "dur": round(el, 1), "progress_pct": round(prog * 100, 2),
        "wrote": int(wr), "retx_pct": round(rx / wr * 100, 3) if wr > 0 else None,
        "kbps": round(wr / el / 1000, 2) if el else 0,
        "invalid": int(a.get("bytes_invalid", 0) - b.get("bytes_invalid", 0)),
        "stalls": int(a.get("print_stall", 0) - b.get("print_stall", 0)),
        "buf_min": round(min(bufs), 3) if bufs else None,
        "buf_mean": round(sum(bufs) / len(bufs), 3) if bufs else None,
        "jit_us": round(a.get("mcu_task_stddev", 0) * 1e6, 1),
        "shutdowns": sd1 - sd0, "sd_msg": msg if sd1 > sd0 else "", "err": err,
    }
    try:
        prev = json.load(open(out))
    except Exception:
        prev = []
    prev.append(res)
    json.dump(prev, open(out, "w"), indent=1)

    v = "OK"
    if res["shutdowns"]:
        v = f"SHUTDOWN: {res['sd_msg'][:30]}"
    elif res["err"]:
        v = f"ERR: {res['err'][:30]}"
    elif (res["retx_pct"] or 0) > 5 or res["stalls"]:
        v = "DEGRADED"
    print("%-22s %5.0f %7d %5.2f %7.0f %7.2f %7.1f %8.3f %7d %6d %8.3f  %s" %
          (label, scv, accel, mcr, res["dur"], res["progress_pct"], res["kbps"],
           res["retx_pct"] if res["retx_pct"] is not None else -1,
           res["invalid"], res["stalls"],
           res["buf_min"] if res["buf_min"] is not None else -1, v), flush=True)
    if res["shutdowns"] or res["err"]:
        k.gcode("FIRMWARE_RESTART", 120)
        time.sleep(8)
    k.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
