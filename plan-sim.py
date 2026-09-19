#!/usr/bin/env python3
"""Predict what square_corner_velocity / minimum_cruise_ratio /
instantaneous_corner_velocity will actually do to a real print, before running it.

This reimplements Klipper's own junction and look-ahead math (toolhead.py
calc_junction + LookAheadQueue.flush, extruder.py calc_junction) against the real
sliced file, so the table is derived from the planner rather than from intuition.

The one result that makes this worth doing: junction velocity is INDEPENDENT of
acceleration. junction_deviation = SCV^2*(sqrt(2)-1)/max_accel and
move_jd_v2 = R_jd * junction_deviation * accel, so accel cancels. On geometry
with a 0.5 mm median segment almost every move is a junction, which is why the
measured ramp gained 19% of speed for 5x the commanded feedrate - accel was
never the limit, SCV was.

Validation: the simulator is first run at the settings that were actually
measured (SCV 5, MCR 0.5, ICV 1.0) and its predicted duration compared against
the six measured phases. A model that cannot reproduce what was measured is not
used to predict what has not been.
"""
import json
import math
import sys

SQRT2M1 = math.sqrt(2.) - 1.
STEPS_PER_MM = 80.0          # rotation_distance 40, 16 microsteps, 200 full steps
FILAMENT_AREA = math.pi * (1.75 / 2.) ** 2


def parse(path, max_frac=1.0):
    """Extract kinematic moves: distance, unit vector, E ratio, feedrate."""
    size = 0
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
    limit = size * max_frac
    x = y = z = e = 0.0
    absolute = True
    abs_e = False
    feed = 100.0
    moves = []
    read = 0
    with open(path, errors="replace") as fh:
        for line in fh:
            read += len(line)
            if read > limit:
                break
            s = line.split(";", 1)[0].strip()
            if not s:
                continue
            up = s.upper()
            if up.startswith("G90"):
                absolute = True; continue
            if up.startswith("G91"):
                absolute = False; continue
            if up.startswith("M82"):
                abs_e = True; continue
            if up.startswith("M83"):
                abs_e = False; continue
            if up.startswith("G92"):
                for w in up.split()[1:]:
                    if w[0] == "E":
                        e = float(w[1:])
                continue
            if not (up.startswith("G0") or up.startswith("G1")):
                continue
            nx, ny, nz, ne = x, y, z, e
            for w in up.split()[1:]:
                c, v = w[0], w[1:]
                try:
                    v = float(v)
                except ValueError:
                    continue
                if c == "X": nx = v if absolute else x + v
                elif c == "Y": ny = v if absolute else y + v
                elif c == "Z": nz = v if absolute else z + v
                elif c == "E": ne = v if abs_e else e + v
                elif c == "F": feed = v / 60.0
            dx, dy, dz = nx - x, ny - y, nz - z
            de = (ne - e) if abs_e else (ne - e)
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            x, y, z, e = nx, ny, nz, ne
            if d <= 0:
                continue
            inv = 1.0 / d
            moves.append((d, dx * inv, dy * inv, dz * inv, de * inv, feed))
    return moves


def simulate(moves, scale, accel, scv, mcr, icv, max_v=800.0):
    """Faithful-enough port of Klipper's junction + look-ahead planning."""
    jd = scv * scv * SQRT2M1 / accel
    mcr_accel = accel * (1.0 - mcr)
    n = len(moves)
    max_cruise_v2 = [0.0] * n
    delta_v2 = [0.0] * n
    mcr_delta_v2 = [0.0] * n
    max_start_v2 = [0.0] * n

    for i, (d, ux, uy, uz, er, feed) in enumerate(moves):
        # M220 scales the file's feedrate; the configured max_velocity still caps it
        v = min(feed * scale, max_v)
        max_cruise_v2[i] = v * v
        delta_v2[i] = 2.0 * d * accel
        mcr_delta_v2[i] = min(2.0 * d * mcr_accel, delta_v2[i])

    for i in range(1, n):
        d, ux, uy, uz, er, _ = moves[i]
        pd, pux, puy, puz, per, _ = moves[i - 1]
        jct = -(ux * pux + uy * puy + uz * puz)
        ms = min(max_cruise_v2[i], max_cruise_v2[i - 1])
        # extruder instantaneous corner velocity
        diff_r = er - per
        if diff_r:
            ms = min(ms, (icv / abs(diff_r)) ** 2)
        sin_h = math.sqrt(max(0.5 * (1.0 - jct), 0.))
        cos_h = math.sqrt(max(0.5 * (1.0 + jct), 0.))
        if 1.0 - sin_h > 0. and cos_h > 0.:
            r_jd = sin_h / (1.0 - sin_h)
            qt = 0.25 * sin_h / cos_h
            ms = min(ms, r_jd * jd * accel,
                     delta_v2[i] * qt, delta_v2[i - 1] * qt)
        max_start_v2[i] = ms

    # backward pass: start velocities, plus the mcr-smoothed pass that caps cruise
    start_v2 = [0.0] * n
    smoothed = [0.0] * n
    next_end = 0.0
    next_sm = 0.0
    for i in range(n - 1, -1, -1):
        sv = min(max_start_v2[i], next_end + delta_v2[i])
        sm = min(max_start_v2[i], next_sm + mcr_delta_v2[i])
        start_v2[i] = sv
        smoothed[i] = sm
        next_end = sv
        next_sm = sm

    # forward pass + trapezoid timing
    total_t = 0.0
    total_d = 0.0
    vsum = 0.0
    rates = []
    cur = 0.0
    for i in range(n):
        d, ux, uy, uz, er, _ = moves[i]
        sv2 = min(start_v2[i], cur)
        ev2 = start_v2[i + 1] if i + 1 < n else 0.0
        ev2 = min(ev2, sv2 + delta_v2[i])
        peak = 0.5 * (sv2 + ev2) + 0.5 * delta_v2[i]
        # minimum_cruise_ratio caps the peak of short accel/decel-only moves
        peak_mcr = 0.5 * (smoothed[i] + (smoothed[i + 1] if i + 1 < n else 0.0)) \
            + 0.5 * mcr_delta_v2[i]
        cv2 = min(max_cruise_v2[i], peak, max(peak_mcr, sv2, ev2))
        sv, ev, cv = math.sqrt(sv2), math.sqrt(ev2), math.sqrt(max(cv2, 0.))
        ta = max(0.0, (cv - sv) / accel)
        td = max(0.0, (cv - ev) / accel)
        da = 0.5 * (sv + cv) * ta
        dd = 0.5 * (cv + ev) * td
        dc = d - da - dd
        if dc < 0:                      # no cruise phase: pure triangle
            cv = math.sqrt(max(0.5 * (sv2 + ev2) + 0.5 * delta_v2[i], 0.))
            ta = max(0.0, (cv - sv) / accel)
            td = max(0.0, (cv - ev) / accel)
            dc = 0.0
        tc = dc / cv if cv > 0 else 0.0
        t = ta + tc + td
        if t <= 0:
            continue
        total_t += t
        total_d += d
        vsum += cv * t
        # CoreXY: A = X+Y, B = X-Y
        rates.append(cv * max(abs(ux + uy), abs(ux - uy)) * STEPS_PER_MM)
        cur = ev2
    rates.sort()
    return {
        "dur": total_t, "dist": total_d,
        "eff_v": total_d / total_t if total_t else 0,
        "mean_cruise": vsum / total_t if total_t else 0,
        "rate_med": rates[len(rates) // 2] if rates else 0,
        "rate_p99": rates[int(len(rates) * 0.99)] if rates else 0,
        "rate_max": rates[-1] if rates else 0,
    }


if __name__ == "__main__":
    path = sys.argv[1]
    frac = float(sys.argv[2]) / 100.0 if len(sys.argv) > 2 else 0.08
    moves = parse(path, frac)
    print(f"parsed {len(moves)} moves from the first {frac*100:.0f}% of the file\n")

    print("=== VALIDATION: simulator vs the six MEASURED phases ===")
    print("  (SCV 5, MCR 0.5, ICV 1.0 - the settings those runs actually used)")
    # (M220 scale, accel, measured duration for this same file fraction)
    measured = [(1.0, 5000, 200), (1.5, 7500, 185), (2.0, 10000, 180),
                (3.0, 20000, 175), (4.0, 30000, 170), (5.0, 40000, 170)]
    print("  %7s %8s %10s %10s %9s" % ("M220", "accel", "meas s", "pred s", "err"))
    for sc, ac, ms in measured:
        r = simulate(moves, sc, ac, 5.0, 0.5, 1.0)
        print("  %6.0f%% %8d %10d %10.0f %8.0f%%" %
              (sc * 100, ac, ms, r["dur"], (r["dur"] - ms) / ms * 100))
    print("\n  path distance in this fraction: %.2f m" %
          (simulate(moves, 1.0, 5000, 5.0, 0.5, 1.0)["dist"] / 1000))
