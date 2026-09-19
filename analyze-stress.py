#!/usr/bin/env python3
"""Analyse the counterbalanced baseline-vs-v7 stress runs.

Reports two metrics deliberately:

  CEILING      highest phase completed without shutdown. Intuitive, but measured
               here to be very noisy - so its spread is printed alongside it, and
               a difference smaller than the within-firmware spread means nothing.
  RETX%        retransmit rate per phase. Repeatable to a few tenths all day, so
               this is the metric with enough resolution to decide on.

The paired comparison at the end only counts phases BOTH firmwares completed in
the SAME round, so a run that died early cannot flatter its own average by
dropping the hardest phases out of the mean.
"""
import os
import glob
import json
import statistics as st

runs = {}
for f in sorted(glob.glob(os.path.expanduser("~/stress-r*.json"))):
    tag = f.split("stress-")[1][:-5]           # e.g. r1-baseline
    rnd, fw = tag.split("-", 1)
    runs.setdefault(fw, {})[rnd] = json.load(open(f))

SPEEDS = [400, 500, 600, 700, 800, 900, 1000]


def ok(p):
    return not p["shutdowns"] and not p["gerr"] and p["retx_pct"] is not None


print("=== CEILING: highest phase completed without shutdown ===")
ceil = {}
for fw in ("baseline", "v7"):
    c = []
    for rnd in sorted(runs[fw]):
        ph = [p for p in runs[fw][rnd] if not p["shutdowns"] and not p["gerr"]]
        c.append(ph[-1]["print_v"] if ph else 0)
    ceil[fw] = c
    print("  %-9s %s   min=%d max=%d spread=%d" % (fw, c, min(c), max(c), max(c) - min(c)))
print("  -> within-firmware spread is %d mm/s; any between-firmware difference"
      % max(max(ceil[f]) - min(ceil[f]) for f in ceil))
print("     smaller than that is indistinguishable from noise at n=3.")

print()
print("=== RETRANSMIT %% per phase (only phases that completed) ===")
print("  %5s | %-24s %6s | %-24s %6s | %7s" %
      ("mm/s", "baseline runs", "mean", "v7 runs", "mean", "delta"))
for s in SPEEDS:
    got = {}
    for fw in ("baseline", "v7"):
        got[fw] = [p["retx_pct"] for rnd in sorted(runs[fw])
                   for p in runs[fw][rnd] if p["print_v"] == s and ok(p)]
    b, v = got["baseline"], got["v7"]
    if not b or not v:
        print("  %5d | baseline n=%d, v7 n=%d - not compared" % (s, len(b), len(v)))
        continue
    mb, mv = st.mean(b), st.mean(v)
    print("  %5d | %-24s %6.2f | %-24s %6.2f | %+7.2f" %
          (s, ", ".join("%.2f" % x for x in b), mb,
              ", ".join("%.2f" % x for x in v), mv, mv - mb))

print()
print("=== PAIRED: same round, same phase, both completed ===")
wins = {"v7": 0, "baseline": 0, "tie": 0}
deltas = []
for rnd in sorted(set(runs["baseline"]) & set(runs["v7"])):
    for s in SPEEDS:
        pb = [p for p in runs["baseline"][rnd] if p["print_v"] == s and ok(p)]
        pv = [p for p in runs["v7"][rnd] if p["print_v"] == s and ok(p)]
        if pb and pv:
            d = pv[0]["retx_pct"] - pb[0]["retx_pct"]
            deltas.append(d)
            wins["v7" if d < -0.05 else "baseline" if d > 0.05 else "tie"] += 1
print("  paired phases: %d" % len(deltas))
print("  v7 lower: %d    baseline lower: %d    tie: %d"
      % (wins["v7"], wins["baseline"], wins["tie"]))
if deltas:
    print("  mean delta (v7 - baseline): %+.3f pp" % st.mean(deltas))
    if len(deltas) > 1:
        print("  stdev of delta:             %.3f pp" % st.stdev(deltas))
    print("  -> v7 is better only if the mean delta is negative AND large")
    print("     relative to that stdev. Otherwise the two are equivalent.")

print()
print("=== total invalid bytes and stalls across all completed phases ===")
for fw in ("baseline", "v7"):
    inval = sum(p["invalid"] for rnd in runs[fw] for p in runs[fw][rnd] if ok(p))
    stalls = sum(p["stalls"] for rnd in runs[fw] for p in runs[fw][rnd] if ok(p))
    moves = sum(p["total_moves"] for rnd in runs[fw] for p in runs[fw][rnd] if ok(p))
    print("  %-9s invalid=%-6d stalls=%-3d moves=%d" % (fw, inval, stalls, moves))
