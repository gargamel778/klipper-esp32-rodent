#!/usr/bin/env python3
"""Turn console.py 'stats' lines into Klipper's mcu_task_avg / mcu_task_stddev.

Same arithmetic as klippy/mcu.py:_handle_mcu_stats. src/basecmd.c:stats_update
times each pass of the mcu main loop, so avg is the mean task-pass duration and
stddev is its jitter.

NOTE: this is a *software* proxy for scheduler health, not a scope trace of timer
ISR jitter. It is the number Klipper itself reports and users judge boards by,
but it does not on its own prove the IRAM-placement claim -- M2 still wants a scope.
"""
import math
import re
import sys

FREQ = 240_000_000.0
SUMSQ_BASE = 256.0
PAT = re.compile(r"([\d.]+): stats count=(\d+) sum=(\d+) sumsq=(\d+)")


def main(paths):
    rows = []
    for p in paths:
        with open(p) as fh:
            for line in fh:
                m = PAT.search(line)
                if not m:
                    continue
                t, count, s, sq = (float(m.group(1)), int(m.group(2)),
                                   int(m.group(3)), int(m.group(4)))
                if not count:
                    continue
                c = 1.0 / (count * FREQ)
                avg = s * c
                sumsq = sq * SUMSQ_BASE
                diff = count * sumsq - s ** 2
                stddev = c * math.sqrt(max(0.0, diff))
                awake = s / FREQ
                rows.append((t, count, avg, stddev, awake))

    if not rows:
        print("no stats lines found")
        return 1

    print(f"{'t(s)':>8} {'count':>6} {'task_avg':>11} {'task_stddev':>12} {'awake':>10}")
    for t, count, avg, stddev, awake in rows:
        print(f"{t:8.3f} {count:6d} {avg*1e6:9.3f}us {stddev*1e6:10.3f}us "
              f"{awake*1e6:8.1f}us")

    # Skip the first sample: it covers boot and skews everything.
    body = rows[1:] or rows
    avgs = [r[2] for r in body]
    sds = [r[3] for r in body]
    print()
    print(f"samples (excl. first): {len(body)}")
    print(f"task_avg    mean {sum(avgs)/len(avgs)*1e6:7.3f}us   "
          f"max {max(avgs)*1e6:7.3f}us")
    print(f"task_stddev mean {sum(sds)/len(sds)*1e6:7.3f}us   "
          f"max {max(sds)*1e6:7.3f}us")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["m0-console.log"]))
