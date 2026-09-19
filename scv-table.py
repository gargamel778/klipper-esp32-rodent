#!/usr/bin/env python3
"""Calculate what SCV / MCR / ICV can do, from the real file's own geometry.

Klipper's junction limit, straight from toolhead.py:

    junction_deviation = SCV^2 * (sqrt(2)-1) / max_accel
    move_jd_v2         = R_jd * junction_deviation * accel
    R_jd               = sin(t/2) / (1 - sin(t/2)),  sin(t/2) = cos(phi/2)

where phi is the deviation angle between consecutive moves (0 = straight on,
90 deg = square corner). Since accel appears in both junction_deviation and
move_jd_v2 it CANCELS:

    v_junction = SCV * sqrt(R_jd * 0.41421)          <- independent of accel

At phi = 90 deg this reduces to exactly SCV, which is the parameter's definition.
The competing centripetal limit does keep its accel dependence:

    v_centripetal = sqrt(0.5 * d * accel / tan(phi/2))

and the extruder adds, at any junction where the extrusion ratio changes:

    v_extruder = ICV / |delta(E per mm)|

This walks the actual sliced file, bins its real junction angles, and reports
which of the three limits binds - so the table reflects this Benchy rather than
an assumed radius.
"""
import math
import sys
from collections import Counter

SQRT2M1 = math.sqrt(2.) - 1.


def jd_velocity(phi_deg, scv):
    """Junction velocity from the SCV limit. Independent of acceleration."""
    phi = math.radians(phi_deg)
    s = math.cos(phi / 2.)                       # == sin(theta/2) in Klipper terms
    if s >= 1.0:
        return float("inf")
    r = s / (1.0 - s)
    return scv * math.sqrt(r * SQRT2M1)


def centripetal_velocity(phi_deg, d, accel):
    phi = math.radians(phi_deg)
    t = math.tan(phi / 2.)
    if t <= 0:
        return float("inf")
    return math.sqrt(0.5 * d * accel / t)


def load(path, frac):
    import os
    limit = os.path.getsize(path) * frac
    x = y = z = e = 0.0
    absolute, abs_e, feed = True, False, 100.0
    mv, read = [], 0
    with open(path, errors="replace") as fh:
        for line in fh:
            read += len(line)
            if read > limit:
                break
            s = line.split(";", 1)[0].strip().upper()
            if not s:
                continue
            if s.startswith("G90"): absolute = True; continue
            if s.startswith("G91"): absolute = False; continue
            if s.startswith("M82"): abs_e = True; continue
            if s.startswith("M83"): abs_e = False; continue
            if s.startswith("G92"):
                for w in s.split()[1:]:
                    if w[0] == "E": e = float(w[1:])
                continue
            if not (s.startswith("G0") or s.startswith("G1")):
                continue
            nx, ny, nz, ne = x, y, z, e
            for w in s.split()[1:]:
                c = w[0]
                try: v = float(w[1:])
                except ValueError: continue
                if c == "X": nx = v if absolute else x + v
                elif c == "Y": ny = v if absolute else y + v
                elif c == "Z": nz = v if absolute else z + v
                elif c == "E": ne = v if abs_e else e + v
                elif c == "F": feed = v / 60.0
            dx, dy, dz = nx - x, ny - y, nz - z
            de = ne - e
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            x, y, z, e = nx, ny, nz, ne
            if d > 0:
                mv.append((d, dx / d, dy / d, dz / d, de / d, feed))
    return mv


if __name__ == "__main__":
    path = sys.argv[1]
    frac = (float(sys.argv[2]) / 100.0) if len(sys.argv) > 2 else 0.08
    mv = load(path, frac)

    # real junction angles
    angs, dists, diffs = [], [], []
    for i in range(1, len(mv)):
        d, ux, uy, uz, er, _ = mv[i]
        _, px, py, pz, per, _ = mv[i - 1]
        dot = max(-1.0, min(1.0, ux * px + uy * py + uz * pz))
        angs.append(math.degrees(math.acos(dot)))
        dists.append(d)
        diffs.append(abs(er - per))
    angs_s = sorted(angs)
    n = len(angs_s)
    def p(q): return angs_s[min(n - 1, int(n * q / 100))]

    print(f"Benchy junction geometry ({n} junctions in the first {frac*100:.0f}%)\n")
    print("  deviation angle percentiles (0 deg = straight on):")
    print("    p10 %.1f   p25 %.1f   median %.1f   p75 %.1f   p90 %.1f   p99 %.1f deg"
          % (p(10), p(25), p(50), p(75), p(90), p(99)))
    print("  median segment %.2f mm" % sorted(dists)[len(dists) // 2])

    print("\n=== TABLE A: junction velocity (mm/s) by SCV - EXACT, accel-independent ===")
    cols = [5, 10, 15, 20, 30, 40]
    print("  %-22s" % "junction type" + "".join("%9s" % f"SCV {c}" for c in cols))
    rows = [("p10 angle  %.1f deg" % p(10), p(10)),
            ("median     %.1f deg" % p(50), p(50)),
            ("p75        %.1f deg" % p(75), p(75)),
            ("p90        %.1f deg" % p(90), p(90)),
            ("p99        %.1f deg" % p(99), p(99)),
            ("square corner 90 deg", 90.0)]
    for label, phi in rows:
        print("  %-22s" % label +
              "".join("%9.0f" % min(jd_velocity(phi, c), 9999) for c in cols))

    print("\n=== TABLE B: per-junction binding limit, over ALL real junctions ===")
    print("  For every junction the three caps are evaluated and the smallest wins.")
    print("  %-6s %-7s %10s %10s %10s   %s" %
          ("SCV", "accel", "p25 cap", "median", "p75 cap", "bound by (share of junctions)"))
    for accel in (5000, 20000):
        for scv in (5, 10, 15, 20, 30):
            caps, who = [], Counter()
            for a, d, dr in zip(angs, dists, diffs):
                vj = jd_velocity(a, scv)
                vc = centripetal_velocity(a, d, accel)
                ve = (1.0 / dr) if dr > 1e-9 else float("inf")
                c = min(vj, vc, ve)
                caps.append(c)
                who["SCV" if c == vj else ("centrip" if c == vc else "ICV")] += 1
            caps.sort()
            m = len(caps)
            tot = sum(who.values())
            share = "  ".join("%s %.0f%%" % (k, 100.0 * v / tot)
                              for k, v in who.most_common())
            print("  %-6d %-7d %10.0f %10.0f %10.0f   %s" %
                  (scv, accel, caps[m // 4], caps[m // 2], caps[3 * m // 4], share))

    print("\n=== TABLE C: extruder ICV - how often does it actually bind? ===")
    nz = sorted(d for d in diffs if d > 1e-9)
    print("  |delta E-per-mm| percentiles at junctions where it changes:")
    print("    p50 %.5f   p90 %.5f   p99 %.5f   max %.5f"
          % (nz[len(nz)//2], nz[int(len(nz)*.9)], nz[int(len(nz)*.99)], nz[-1]))
    print("  %-8s %14s %14s" % ("ICV", "cap at p99 delta", "cap at max delta"))
    for icv in (1.0, 2.0, 5.0):
        print("  %-8.1f %14.0f %14.0f"
              % (icv, icv / nz[int(len(nz)*.99)], icv / nz[-1]))
