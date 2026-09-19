#!/usr/bin/env python3
"""Analyse and prepare a sliced g-code file for the Rodent bench.

ANALYSE is the important half. The synthetic protocol issues 9-15 moves/s with
43-69 mm average segments; a sliced model is an order of magnitude denser, and
since the binding constraint on this board is per-command latency rather than
bandwidth, segment density is the property that actually decides whether a file
is a harder test than what we have already run. This prints that number up
front so a file can be judged before an hour is spent printing it.

PREPARE is deliberately minimal - the bench config neutralises heaters, fans and
homing with macros, so files do not need editing for those. This only:
  * prepends a kinematic-position declaration (no endstops are wired)
  * drops motor-disable commands that would drop the axes mid-run
  * reports whether the model fits the configured travel

Usage:  gcode-prep.py <in.gcode> [out.gcode]
"""
import math
import os
import re
import sys
from collections import Counter

# Bench limits from rodent-print.cfg
POS_MIN, POS_MAX = -1000.0, 1000.0

WORD = re.compile(r"([XYZEF])(-?\d*\.?\d+)")


def analyse(path):
    x = y = z = 0.0
    have_pos = False
    absolute = True
    segs = []              # XY segment lengths of extruding+travel moves
    extruding = 0
    travel = 0
    zmoves = 0
    feeds = []
    layers = set()
    minx = miny = 1e9
    maxx = maxy = -1e9
    nlines = 0

    with open(path, errors="replace") as fh:
        for line in fh:
            nlines += 1
            s = line.split(";", 1)[0].strip()
            if not s:
                continue
            up = s.upper()
            if up.startswith("G90"):
                absolute = True
                continue
            if up.startswith("G91"):
                absolute = False
                continue
            if not (up.startswith("G0 ") or up.startswith("G1 ")
                    or up in ("G0", "G1")):
                continue
            d = dict((k, float(v)) for k, v in WORD.findall(up))
            nx = d.get("X", x if absolute else 0.0)
            ny = d.get("Y", y if absolute else 0.0)
            nz = d.get("Z", z if absolute else 0.0)
            if absolute:
                dx, dy = nx - x, ny - y
                dz = nz - z
                x, y, z = nx, ny, nz
            else:
                dx, dy, dz = nx, ny, nz
                x, y, z = x + dx, y + dy, z + dz
            if "F" in d:
                feeds.append(d["F"] / 60.0)
            if dz:
                zmoves += 1
                layers.add(round(z, 3))
            L = math.hypot(dx, dy)
            if L > 0:
                segs.append(L)
                if "E" in d:
                    extruding += 1
                else:
                    travel += 1
                have_pos = True
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)

    return dict(nlines=nlines, segs=segs, extruding=extruding, travel=travel,
                zmoves=zmoves, layers=len(layers), feeds=feeds,
                bbox=(minx, miny, maxx, maxy) if have_pos else None)


def report(a):
    segs = a["segs"]
    if not segs:
        print("  no XY moves found - is this really a sliced file?")
        return
    segs_sorted = sorted(segs)
    n = len(segs)

    def pct(p):
        return segs_sorted[min(n - 1, int(n * p / 100))]

    total = sum(segs)
    fmean = (sum(a["feeds"]) / len(a["feeds"])) if a["feeds"] else 0.0
    fmax = max(a["feeds"]) if a["feeds"] else 0.0

    print("  lines            %d" % a["nlines"])
    print("  XY moves         %d  (extruding %d, travel %d)"
          % (n, a["extruding"], a["travel"]))
    print("  Z moves / layers %d / %d" % (a["zmoves"], a["layers"]))
    print("  path length      %.1f m" % (total / 1000.0))
    print("  segment length   mean %.2f mm | median %.2f | p10 %.2f | p90 %.2f | max %.1f"
          % (total / n, pct(50), pct(10), pct(90), segs_sorted[-1]))
    print("  feedrate in file mean %.0f mm/s | max %.0f mm/s" % (fmean, fmax))
    if a["bbox"]:
        mnx, mny, mxx, mxy = a["bbox"]
        print("  bounding box     X %.1f..%.1f   Y %.1f..%.1f  (%.1f x %.1f mm)"
              % (mnx, mxx, mny, mxy, mxx - mnx, mxy - mny))
        if mnx < POS_MIN or mny < POS_MIN or mxx > POS_MAX or mxy > POS_MAX:
            print("  !! outside configured travel %.0f..%.0f" % (POS_MIN, POS_MAX))

    print()
    print("  Command rate vs the synthetic protocol (which ran 9-15 moves/s,")
    print("  43-69 mm segments, 3.9-7.5 kB/s on the wire):")
    print("    %8s %10s %12s" % ("M220", "moves/s", "vs synthetic"))
    for scale in (100, 150, 200, 300, 400):
        v = fmean * scale / 100.0
        if v <= 0:
            continue
        rate = v / (total / n)          # mm/s divided by mm/move
        print("    %7d%% %10.0f %11.0fx" % (scale, rate, rate / 12.0))
    print("  (upper bound: ignores acceleration, so real rates are somewhat lower)")


def prepare(src, dst):
    dropped = Counter()
    with open(src, errors="replace") as fi, open(dst, "w") as fo:
        fo.write("; prepared for the Rodent bench - no endstops, no heaters\n")
        fo.write("SET_KINEMATIC_POSITION X=0 Y=0 Z=0\n")
        fo.write("G90\n")
        for line in fi:
            up = line.split(";", 1)[0].strip().upper()
            # M84/M18 would drop the steppers partway through a run
            if up.startswith("M84") or up.startswith("M18"):
                dropped["M84/M18"] += 1
                continue
            fo.write(line)
    return dropped


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    src = sys.argv[1]
    print("=== %s (%.1f MB) ===" % (os.path.basename(src),
                                    os.path.getsize(src) / 1e6))
    report(analyse(src))
    if len(sys.argv) > 2:
        d = prepare(src, sys.argv[2])
        print()
        print("  prepared -> %s   dropped: %s"
              % (sys.argv[2], dict(d) or "nothing"))
