#!/usr/bin/env python3
"""Print a file start to finish and report the real elapsed time plus link health.

This is the acceptance test the whole exercise has been building toward: not a
sampled window, not an extrapolation - the complete job, the way it would run on
a machine, with the question being whether it finishes cleanly.

Settings are the tuned optimum found by the ramp, at the file's own sliced speed.
M220 stays at 100% deliberately: raising it is equivalent to having sliced the
file faster, which is a different claim than "the machine ran the file".

Usage: full-print.py <file> <scv> <accel> <mcr> [out.json]
"""
import json
import re
import sys
import time
from importlib.machinery import SourceFileLoader

import os
K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
M220 = int(os.environ.get("M220", "100"))
LOG = "/tmp/klippy-stress.log"
F = ("bytes_write bytes_retransmit bytes_invalid print_stall buffer_time "
     "mcu_task_stddev freq").split()


def stats():
    try:
        ls = [l for l in open(LOG, errors="replace") if l.startswith("Stats ")]
    except FileNotFoundError:
        return {}
    if not ls:
        return {}
    return {f: float(m.group(1)) for f in F
            if (m := re.search(rf"\b{f}=([\d.]+)", ls[-1]))}


def shutdowns():
    try:
        t = open(LOG, errors="replace").read()
    except FileNotFoundError:
        return 0, ""
    h = re.findall(r"(?:MCU '\w+' shutdown|Transition to shutdown state): ?(.*)", t)
    return len(h), (h[-1][:70] if h else "")


def main():
    fn, scv, accel, mcr = sys.argv[1], float(sys.argv[2]), int(sys.argv[3]), float(sys.argv[4])
    vel = float(sys.argv[5]) if len(sys.argv) > 5 else 600.0
    out = sys.argv[6] if len(sys.argv) > 6 else os.path.expanduser("~/full-print.json")

    k = K.Klippy()
    if k.call("info")["result"]["state"] != "ready":
        print("klippy not ready"); return 1
    k.gcode("CLEAR_PAUSE")
    # NOTE: a file may set its own ACCEL/SCV via SET_VELOCITY_LIMIT after this
    # point, in which case the file wins - that is intended for an as-sliced run.
    # VELOCITY and MINIMUM_CRUISE_RATIO are not emitted by slicers, so they stay
    # as set here and must be chosen deliberately.
    k.gcode(f"SET_VELOCITY_LIMIT ACCEL={accel} VELOCITY={vel} "
            f"SQUARE_CORNER_VELOCITY={scv} MINIMUM_CRUISE_RATIO={mcr}", 30)
    k.gcode(f"M220 S{M220}")
    print(f"FULL PRINT  {fn}  SCV={scv} ACCEL={accel} MCR={mcr} VEL={vel} M220={M220}%\n")

    sd0, _ = shutdowns()
    b = stats()
    t0 = time.time()
    r = k.gcode(f"SDCARD_PRINT_FILE FILENAME={fn}", 60)
    if r and "error" in r:
        print("start failed:", r["error"].get("message", "")[:150]); return 1

    bufs, freqs, state, msg = [], [], "printing", ""
    print(f"{'elapsed':>9}{'file%':>8}{'kB/s':>8}{'retx%':>8}{'buffer':>9}{'stalls':>8}")
    last = 0
    while True:
        time.sleep(15)
        try:
            q = k.status({"virtual_sdcard": None, "print_stats": None})
            vs = q["result"]["status"]["virtual_sdcard"]
            ps = q["result"]["status"]["print_stats"]
        except Exception as e:
            state, msg = "api_error", str(e)[:90]; break
        s = stats()
        if "buffer_time" in s:
            bufs.append(s["buffer_time"])
        if "freq" in s:
            freqs.append(s["freq"])
        el = time.time() - t0
        wt = s.get("bytes_write", 0) - b.get("bytes_write", 0)
        rx = s.get("bytes_retransmit", 0) - b.get("bytes_retransmit", 0)
        if el - last >= 30:
            last = el
            print(f"{el:>9.0f}{(vs.get('progress',0)*100):>8.2f}{wt/el/1000:>8.1f}"
                  f"{(rx/wt*100 if wt else 0):>8.3f}{s.get('buffer_time',0):>9.3f}"
                  f"{int(s.get('print_stall',0)-b.get('print_stall',0)):>8}", flush=True)
        # An MCU shutdown leaves print_stats at "paused", NOT "error" - polling
        # print_stats alone reports a dead printer as healthy indefinitely.
        # klippy's own state is the authoritative check and must come first.
        try:
            ks = k.call("info")["result"]["state"]
        except Exception:
            ks = "unknown"
        if ks != "ready":
            sdn, sdm = shutdowns()
            state, msg = "shutdown", sdm or ks
            break
        st = ps.get("state")
        if st in ("complete", "error", "cancelled"):
            state, msg = st, ps.get("message", "") or ""
            break
        if st == "paused":
            state, msg = "paused", ps.get("message", "") or "unexpected pause"
            break
        if el > 7200:
            state = "timeout"; break

    el = time.time() - t0
    try:
        fin = k.status({"virtual_sdcard": None})["result"]["status"]["virtual_sdcard"]
        final_prog = round((fin.get("progress") or 0) * 100, 2)
    except Exception:
        final_prog = None
    a = stats()
    sd1, sdmsg = shutdowns()
    wt = a.get("bytes_write", 0) - b.get("bytes_write", 0)
    rx = a.get("bytes_retransmit", 0) - b.get("bytes_retransmit", 0)
    res = {
        "file": fn, "scv": scv, "accel": accel, "mcr": mcr, "velocity": vel, "m220": M220,
        "state": state, "message": msg,
        "elapsed_s": round(el, 1),
        "elapsed": time.strftime("%H:%M:%S", time.gmtime(el)),
        "retx_pct": round(rx / wt * 100, 3) if wt else None,
        "kbps": round(wt / el / 1000, 2) if el else 0,
        "invalid": int(a.get("bytes_invalid", 0) - b.get("bytes_invalid", 0)),
        "stalls": int(a.get("print_stall", 0) - b.get("print_stall", 0)),
        "buf_min": round(min(bufs), 3) if bufs else None,
        "buf_mean": round(sum(bufs) / len(bufs), 3) if bufs else None,
        "freq_first": int(freqs[0]) if freqs else None,
        "freq_last": int(freqs[-1]) if freqs else None,
        "freq_drift": int(max(freqs) - min(freqs)) if freqs else None,
        "progress_pct": final_prog,
        "shutdowns": sd1 - sd0, "sd_msg": sdmsg if sd1 > sd0 else "",
    }
    json.dump(res, open(out, "w"), indent=1)
    print()
    print(f"  RESULT      {state.upper()}  {msg[:80]}")
    print(f"  elapsed     {res['elapsed']}  ({res['elapsed_s']:.0f}s)   reached {final_prog}%")
    print(f"  retx        {res['retx_pct']}%   wire {res['kbps']} kB/s   "
          f"invalid {res['invalid']}")
    print(f"  stalls      {res['stalls']}   shutdowns {res['shutdowns']} "
          f"{res['sd_msg']}")
    print(f"  buffer min  {res['buf_min']}s")
    k.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
