#!/usr/bin/env python3
"""Identify which chain_position physically has the motor, electrically.

A TMC5160 sets OLA/OLB (open load) on a coil with nothing connected. So after a
DUMP_TMC of all four, the driver whose open-load flags are CLEAR is the one with
a motor plugged in - which settles the chain_position -> physical port mapping
without moving anything.
"""
import re
import sys

LOG = "/tmp/klippy-stress.log"
NAMES = ["sx (chain_position 1)", "sy (chain_position 2)",
         "sz (chain_position 3)", "sa (chain_position 4)"]

t = open(LOG, errors="replace").read()
blocks = re.findall(r"=+ Queried registers =+(.*?)(?:=====|\Z)", t, re.S)
tail = blocks[-4:]
print(f"{len(blocks)} dump blocks in log; using the last {len(tail)}\n")

found = []
for n, b in zip(NAMES, tail):
    m = re.search(r"DRV_STATUS:\s+(\S+)\s+(.*)", b)
    if not m:
        print(f"  {n:<26} (no DRV_STATUS)")
        continue
    raw, fields = m.group(1), m.group(2)
    ola = "ola=1" in fields
    olb = "olb=1" in fields
    sg = re.search(r"sg_result=(\d+)", fields)
    cs = re.search(r"cs_actual=(\d+)", fields)
    open_load = ola and olb
    if not open_load:
        found.append(n)
    print(f"  {n:<26} {raw}  ola={int(ola)} olb={int(olb)} "
          f"sg={sg.group(1) if sg else '?':>4} cs={cs.group(1) if cs else '?':>2}   "
          f"{'open load - NO motor' if open_load else '>>> MOTOR PRESENT <<<'}")

print()
if len(found) == 1:
    print(f"RESULT: motor is on {found[0]}")
elif not found:
    print("RESULT: all four read open-load - motor not detected electrically.")
    print("        (open-load detection needs current flowing; a stationary")
    print("         high-impedance coil at low current can read as open)")
else:
    print(f"RESULT: ambiguous, {len(found)} drivers show a load: {found}")
