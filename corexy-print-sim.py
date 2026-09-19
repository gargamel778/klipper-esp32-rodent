#!/usr/bin/env python3
"""CoreXY 3D-printer duty-cycle simulation for the BTT Rodent running Klipper.

Reproduces what a real printer actually asks of the motion system, rather than a
synthetic square:

  PERIMETERS  axis-aligned moves -> in CoreXY BOTH motors step (same direction
              for X, opposite for Y). Slow, high-precision.
  INFILL      45-degree zigzag   -> in CoreXY exactly ONE motor steps per pass.
              Fast, and the direction reversal at each end is abrupt. The
              diagonal ALTERNATES every layer so the A and B motors take turns
              carrying it, instead of one motor doing all the infill for the
              whole run. The A:B column reports the resulting balance, computed
              from the emitted g-code (A = X+Y, B = X-Y); 1.000 is even.
  Z-HOPS      short 0.3mm lifts before every travel. On an 8mm leadscrew that is
              8x the steps-per-mm of XY, so a "small" hop is a burst of steps.
  TRAVELS     fast non-extruding moves between features.
  EXTRUDER    a REAL [extruder] - E rides on the same coordinated moves as XY,
              so it exercises pressure advance and the true step interleave.
  RETRACTIONS abrupt E reversal before each travel, then unretract after -
              the sudden direction changes a real slicer emits.

Each phase runs the same choreography at a higher print speed until something
breaks. Metrics are the same health signals used in the speed study, plus a
per-feature breakdown of how many moves of each type were issued.
"""
import json
import os
import re
import sys
import time
from importlib.machinery import SourceFileLoader

K = SourceFileLoader("kapi", os.environ.get("KLIPPY_API", os.path.expanduser("~/klippy-api.py"))).load_module()
LOG = "/tmp/klippy-stress.log"
PHASE_SECS = 180

# (print mm/s, travel mm/s, accel mm/s^2) - a realistic progression from a
# conservative machine up to a fast CoreXY.
RAMP = [
    (60,  150,  3000),
    (100, 250,  5000),
    (150, 350, 10000),
    (200, 400, 15000),
    (300, 500, 20000),
    (400, 600, 30000),
    (500, 700, 30000),
    (600, 800, 30000),
]

BED = 200.0          # working square
LAYER_H = 0.2
ZHOP = 0.3
RETRACT = 0.8        # mm of filament
RETRACT_SPEED = 45
E_PER_MM = 0.045     # ~0.4 nozzle, 0.2 layer

SF = ("bytes_write bytes_retransmit bytes_invalid print_stall "
      "mcu_task_avg mcu_task_stddev freq").split()


def stats():
    for _ in range(12):
        try:
            L = [l for l in open(LOG, errors="replace") if l.startswith("Stats ")]
            if L:
                return {f: float(m.group(1)) for f in SF
                        if (m := re.search(rf"\b{f}=([\d.]+)", L[-1]))}
        except FileNotFoundError:
            pass
        time.sleep(1)
    return {}


def shutdowns():
    try:
        t = open(LOG, errors="replace").read()
    except FileNotFoundError:
        return 0, ""
    h = re.findall(r"(?:MCU '\w+' shutdown|Transition to shutdown state): ?(.*)", t)
    return len(h), (h[-1][:70] if h else "")


class Sim:
    """Issues the g-code for one simulated layer."""

    def __init__(self, k, print_v, travel_v):
        self.k = k
        self.pv, self.tv = print_v, travel_v
        self.e = 0.0
        self.z = 0.0
        self.counts = {"perimeter": 0, "infill": 0, "travel": 0,
                       "zhop": 0, "retract": 0}
        self.gerr = 0
        self.err = ""
        self.n_layers = 0
        # CoreXY motor-load accounting, computed from the g-code actually
        # emitted: A = X + Y and B = X - Y, so summing |dA| and |dB| over every
        # XY move gives exactly how far each physical motor was driven. This is
        # what proves the per-layer infill flip really does share the work
        # rather than just looking like it should.
        self.pos = (0.0, 0.0)
        self.a_mm = 0.0
        self.b_mm = 0.0

    def g(self, cmd, to=240):
        r = self.k.gcode(cmd, to)
        if r and "error" in r:
            self.gerr += 1
            self.err = str(r["error"].get("message", ""))[:90]
            return False
        return True

    def xy(self, x, y):
        """Record an XY destination against the CoreXY motors and return it."""
        dx, dy = x - self.pos[0], y - self.pos[1]
        self.a_mm += abs(dx + dy)
        self.b_mm += abs(dx - dy)
        self.pos = (x, y)
        return x, y

    def e_for(self, mm_xy):
        """Advance the extruder in step with an XY move and return the E word.

        E is a real [extruder] now, so extrusion is part of the same coordinated
        move as XY - which is what exercises pressure advance and produces a
        printer's true step pattern, rather than a second independent queue.
        """
        self.e += mm_xy * E_PER_MM
        return f" E{self.e:.4f}"

    def retract(self):
        """Abrupt E-only reversal - the sudden direction change a slicer emits."""
        self.e -= RETRACT
        self.counts["retract"] += 1
        return self.g(f"G1 E{self.e:.4f} F{int(RETRACT_SPEED*60)}", 120)

    def unretract(self):
        self.e += RETRACT
        return self.g(f"G1 E{self.e:.4f} F{int(RETRACT_SPEED*60)}", 120)

    def travel_to(self, x, y):
        """Retract, z-hop, travel, drop, unretract - the standard slicer sequence."""
        if not self.retract():
            return False
        self.counts["zhop"] += 1
        if not self.g(f"G1 Z{self.z + ZHOP:.3f} F{int(30*60)}"):
            return False
        self.counts["travel"] += 1
        self.xy(x, y)
        if not self.g(f"G1 X{x:.2f} Y{y:.2f} F{int(self.tv*60)}"):
            return False
        if not self.g(f"G1 Z{self.z:.3f} F{int(30*60)}"):
            return False
        return self.unretract()

    def perimeter(self, x0, y0, size, loops=2):
        """Axis-aligned rectangle: in CoreXY both motors step on every side."""
        for i in range(loops):
            inset = i * 1.0
            pts = [(x0+inset, y0+inset), (x0+size-inset, y0+inset),
                   (x0+size-inset, y0+size-inset), (x0+inset, y0+size-inset),
                   (x0+inset, y0+inset)]
            for px, py in pts[1:]:
                self.counts["perimeter"] += 1
                self.xy(px, py)
                e = self.e_for(size)
                if not self.g(f"G1 X{px:.2f} Y{py:.2f}{e} F{int(self.pv*60)}"):
                    return False
        return True

    def infill(self, x0, y0, size, spacing=6.0, flip=False):
        """45-degree zigzag: in CoreXY exactly ONE motor steps per diagonal.

        CoreXY drives A = X + Y and B = X - Y, so a chord with dX = -dY moves
        ONLY the B motor and a chord with dX = +dY moves ONLY the A motor. The
        original pattern used the anti-diagonal on every layer, which meant one
        motor did all of the infill for the whole run while the other only ever
        saw perimeters. `flip` mirrors the pattern about the square's vertical
        centre line to switch to the main diagonal, and the caller alternates it
        per layer - which is also what a real slicer does (45 deg / 135 deg).

        The two moves per chord are ordered by parity so consecutive chords run
        in opposite directions: the first move is the short connecting hop along
        an edge, the second is the 45-degree chord itself. That is a genuine
        zigzag with the abrupt reversals a real print produces.
        """
        n = int(size / spacing)
        for i in range(n):
            off = i * spacing
            if flip:
                a = (x0 + size - off, y0)
                b = (x0 + size, y0 + off)
            else:
                a = (x0 + off, y0)
                b = (x0, y0 + off)
            p, q = (a, b) if i % 2 == 0 else (b, a)
            self.counts["infill"] += 2
            d = max(1.0, off * 1.414)
            self.xy(*p)
            if not self.g(f"G1 X{p[0]:.2f} Y{p[1]:.2f}{self.e_for(d)} "
                          f"F{int(self.pv*60)}"):
                return False
            self.xy(*q)
            if not self.g(f"G1 X{q[0]:.2f} Y{q[1]:.2f}{self.e_for(d)} "
                          f"F{int(self.pv*60)}"):
                return False
        return True

    def layer(self):
        """One layer: two separate objects, each perimeter + infill, with a
        travel and z-hop between them. Mirrors a real multi-object print.

        The infill diagonal alternates direction every layer so the A and B
        motors take turns carrying it. Perimeters are axis-aligned and already
        drive both motors equally, so infill was the only asymmetric part.
        """
        self.z += LAYER_H
        flip = self.n_layers % 2 == 1
        # The two objects also swap between a main-diagonal and an anti-diagonal
        # layout. Travels between them are long diagonal moves, which in CoreXY
        # drive a single motor - and reversing the direction of travel does not
        # change which one. Alternating the layout is what shares them; with a
        # fixed diagonal layout travel balance is 0.21 and drags the run to 0.88
        # even when the infill itself is perfectly even.
        objects = (((20.0, 20.0), (110.0, 110.0)) if not flip
                   else ((20.0, 110.0), (110.0, 20.0)))
        self.n_layers += 1
        if not self.g(f"G1 Z{self.z:.3f} F{int(30*60)}"):
            return False
        for (x0, y0) in objects:
            if not self.travel_to(x0, y0):
                return False
            if not self.perimeter(x0, y0, 70.0):
                return False
            if not self.infill(x0, y0, 70.0, flip=flip):
                return False
        return True


def phase(k, pv, tv, acc):
    k.gcode(f"SET_VELOCITY_LIMIT VELOCITY={max(pv, tv)} ACCEL={acc} "
            f"ACCEL_TO_DECEL={acc//2} SQUARE_CORNER_VELOCITY=5")
    k.gcode("SET_KINEMATIC_POSITION X=0 Y=0 Z=0")
    k.gcode("G90")
    k.gcode("G92 E0")          # real extruder: reset the E origin
    sd0, _ = shutdowns()
    b = stats()
    t0 = time.time()
    sim = Sim(k, pv, tv)
    layers = 0
    while time.time() - t0 < PHASE_SECS:
        if not sim.layer():
            break
        layers += 1
    k.gcode("M400", 300)
    time.sleep(2.5)
    a = stats()
    sd1, msg = shutdowns()
    wr = a.get("bytes_write", 0) - b.get("bytes_write", 0)
    rx = a.get("bytes_retransmit", 0) - b.get("bytes_retransmit", 0)
    return {
        "print_v": pv, "travel_v": tv, "accel": acc,
        "dur": round(time.time() - t0, 1), "layers": layers,
        "moves": dict(sim.counts),
        "total_moves": sum(sim.counts.values()),
        "wrote": int(wr), "retx": int(rx),
        "retx_pct": round(rx / wr * 100, 2) if wr > 0 else None,
        "invalid": int(a.get("bytes_invalid", 0) - b.get("bytes_invalid", 0)),
        "stalls": int(a.get("print_stall", 0) - b.get("print_stall", 0)),
        "jit_us": round(a.get("mcu_task_stddev", 0) * 1e6, 1),
        "task_us": round(a.get("mcu_task_avg", 0) * 1e6, 2),
        "freq": int(a.get("freq", 0)),
        "shutdowns": sd1 - sd0, "sd_msg": msg if sd1 > sd0 else "",
        "gerr": sim.gerr, "err": sim.err,
        "a_mm": round(sim.a_mm, 1), "b_mm": round(sim.b_mm, 1),
        # 1.00 = the two CoreXY motors were driven exactly the same distance
        "ab_balance": (round(min(sim.a_mm, sim.b_mm) / max(sim.a_mm, sim.b_mm), 3)
                       if max(sim.a_mm, sim.b_mm) > 0 else None),
    }


def main():
    k = K.Klippy()
    st = k.call("info")["result"]["state"]
    print(f"klippy state: {st}")
    if st != "ready":
        print("not ready - aborting"); return 1
    print(f"\nCoreXY print simulation - {len(RAMP)} phases x {PHASE_SECS}s")
    print("perimeters(both motors) + 45deg infill(one motor) + z-hops + "
          "retractions + continuous E\n")
    hdr = (f"{'print':>6}{'trav':>6}{'accel':>7}{'lay':>5}{'moves':>7}"
           f"{'wrote':>8}{'retx%':>7}{'inval':>7}{'stall':>6}{'jit':>7}"
           f"{'A:B':>7}  verdict")
    print(hdr); print("-" * len(hdr))
    out = []
    for pv, tv, acc in RAMP:
        r = phase(k, pv, tv, acc)
        out.append(r)
        v = "OK"
        if r["shutdowns"]:
            v = f"SHUTDOWN: {r['sd_msg'][:34]}"
        elif r["gerr"]:
            v = f"GCODE ERR: {r['err'][:34]}"
        elif (r["retx_pct"] or 0) > 25 or r["invalid"] > 100 or r["stalls"]:
            v = "DEGRADED"
        print(f"{r['print_v']:>6}{r['travel_v']:>6}{r['accel']:>7}{r['layers']:>5}"
              f"{r['total_moves']:>7}{r['wrote']:>8}"
              f"{(r['retx_pct'] if r['retx_pct'] is not None else -1):>7}"
              f"{r['invalid']:>7}{r['stalls']:>6}{r['jit_us']:>6}u"
              f"{(r['ab_balance'] if r['ab_balance'] is not None else -1):>7}"
              f"  {v}", flush=True)
        if r["shutdowns"] or r["gerr"]:
            print("\n  ceiling reached - stopping ramp"); break
        time.sleep(3)
    json.dump(out, open(os.path.expanduser("~/corexy-sim.json"), "w"), indent=1)
    print("\nraw -> ~/corexy-sim.json")
    print("\nmove mix in the last completed phase:")
    if out:
        for kk, vv in out[-1]["moves"].items():
            print(f"    {kk:<11}{vv:>7}")
    k.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
