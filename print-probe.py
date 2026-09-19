#!/usr/bin/env python3
"""Short validation run of a real sliced print, then cancel.

Two jobs:

  1. Prove the pipeline works end to end - virtual_sdcard, the neutralising
     macros, exclude_object, the slicer start/end hooks - before committing
     hours to full prints.
  2. Measure what the synthetic protocol could not: the actual command rate and
     wire rate of real geometry, and whether klippy keeps its look-ahead buffer
     full. buffer_time is the number that matters. The synthetic harness fed
     moves one API call at a time; if that starved the queue, every ceiling
     measured so far is suspect. virtual_sdcard removes that path entirely, so
     comparing buffer_time here against the synthetic runs settles it.

Usage: print-probe.py <file.gcode> [seconds] [M220_scale] [accel]
"""
import os
import re
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
LOG = "/tmp/klippy-stress.log"

FIELDS = ("bytes_write bytes_retransmit bytes_invalid print_stall buffer_time "
          "print_time mcu_task_avg mcu_task_stddev").split()


def stats_line():
    try:
        ls = [l for l in open(LOG, errors="replace") if l.startswith("Stats ")]
    except FileNotFoundError:
        return {}
    if not ls:
        return {}
    out = {}
    for f in FIELDS:
        m = re.search(rf"\b{f}=([\d.]+)", ls[-1])
        if m:
            out[f] = float(m.group(1))
    return out


def main():
    fn = sys.argv[1]
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    accel = int(sys.argv[4]) if len(sys.argv) > 4 else 5000

    k = K.Klippy()
    st = k.call("info")["result"]["state"]
    print(f"klippy state: {st}")
    if st != "ready":
        print("not ready"); return 1

    k.gcode("CLEAR_PAUSE")
    k.gcode(f"SET_VELOCITY_LIMIT ACCEL={accel} ACCEL_TO_DECEL={accel//2} "
            f"SQUARE_CORNER_VELOCITY=5")
    k.gcode(f"M220 S{scale}")
    print(f"starting {fn}  M220={scale}%  ACCEL={accel}\n")
    r = k.gcode(f"SDCARD_PRINT_FILE FILENAME={fn}", 60)
    if r and "error" in r:
        print("start failed:", r["error"].get("message", "")[:200]); k.close(); return 1

    b = stats_line()
    t0 = time.time()
    prev_pos, prev_t = 0, t0
    print(f"{'t':>5}{'file%':>7}{'kB/s':>8}{'retx%':>7}{'buffer_s':>10}"
          f"{'stalls':>7}{'jit_us':>8}")
    while time.time() - t0 < secs:
        time.sleep(10)
        q = k.status({"virtual_sdcard": None, "print_stats": None})
        vs = q["result"]["status"]["virtual_sdcard"]
        ps = q["result"]["status"]["print_stats"]
        s = stats_line()
        el = time.time() - t0
        wr = (s.get("bytes_write", 0) - b.get("bytes_write", 0)) / max(el, 1)
        rx = s.get("bytes_retransmit", 0) - b.get("bytes_retransmit", 0)
        wt = s.get("bytes_write", 0) - b.get("bytes_write", 0)
        print(f"{el:>5.0f}{vs.get('progress', 0) * 100:>7.2f}{wr / 1000:>8.1f}"
              f"{(rx / wt * 100 if wt else 0):>7.2f}"
              f"{s.get('buffer_time', 0):>10.3f}"
              f"{int(s.get('print_stall', 0) - b.get('print_stall', 0)):>7}"
              f"{s.get('mcu_task_stddev', 0) * 1e6:>8.1f}", flush=True)
        if ps.get("state") in ("complete", "error", "cancelled"):
            print("  print ended early:", ps.get("state"), ps.get("message", ""))
            break

    q = k.status({"virtual_sdcard": None, "print_stats": None})
    ps = q["result"]["status"]["print_stats"]
    vs = q["result"]["status"]["virtual_sdcard"]
    s = stats_line()
    el = time.time() - t0
    print()
    print(f"  state           {ps.get('state')}  {ps.get('message','')}")
    print(f"  file position   {vs.get('file_position')} / {vs.get('file_size')} "
          f"({vs.get('progress', 0) * 100:.2f}%)")
    print(f"  print_duration  {ps.get('print_duration', 0):.1f}s")
    wt = s.get("bytes_write", 0) - b.get("bytes_write", 0)
    rx = s.get("bytes_retransmit", 0) - b.get("bytes_retransmit", 0)
    print(f"  wire            {wt / el / 1000:.1f} kB/s   retx {rx / wt * 100 if wt else 0:.2f}%")
    print(f"  buffer_time     {s.get('buffer_time', 0):.3f}s "
          f"(healthy look-ahead is ~2s; near 0 means the queue is starving)")

    k.gcode("CANCEL_PRINT", 60)
    time.sleep(1)
    k.gcode("M400", 120)
    k.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
