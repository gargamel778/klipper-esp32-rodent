# Klipper on an ESP32: porting to the BTT Rodent V1.1

Klipper's MCU firmware running on a **BigTreeTech Rodent V1.1** — a 4-driver ESP32 CNC
board — driving four TMC2160 steppers, characterised on the bench with repeated runs
rather than single ones.

**Headline result:** a rule-legal SpeedBoatRace Benchy streams at **800 mm/s with
`SQUARE_CORNER_VELOCITY 30` and 100 000 mm/s² acceleration, 10 runs out of 10**, finishing
in 3:45. At 0.25 mm layers and 0.5 mm line width that is **100 mm³/s of volumetric flow** —
which is a high-end hotend's melt limit, not a controller limit. On this workload the ESP32
is not the bottleneck.

This is a **bench rig**: real gcode, real motion, all four motors turning. No hotend, no
filament. It measures the electronics.

## What's here

| path | what it is |
|---|---|
| `STUDY-REPORT.md` | the full engineering write-up — method, tables, calculations |
| `rodent-report.html` | the same study as a standalone illustrated page |
| `blog-post.md` | a shorter narrative version |
| `BRINGUP.md` | first-power-on notes and the verified pin map |
| `rodent-*.cfg`, `stress-*.cfg` | Klipper configs used for the tests |
| `firmware/` | built images, and `rodent-klipper-patches.patch` |
| `fw-variants/` | per-baud build artifacts kept as evidence |
| `*.py`, `*.sh` | the test harnesses (see below) |
| `m1-ticks*.log`, `m2-*.log` | raw benchmark captures |

## Findings worth the time of anyone doing this

- **Serial baud is the single biggest lever.** 1 500 000 works; 2 000 000 connects, reports
  ready, drives all four motors — and then corrupts 6.11 % of bytes under load and shuts
  down. Rates are individually good or bad, not monotonic. `CONFIG_SERIAL_BAUD` is
  compile-time and must match klippy's `baud:`.
- **`SQUARE_CORNER_VELOCITY` is the lever that matters, not acceleration.** In Klipper's
  junction planner, `v_junction = SCV·√(R_jd · 0.41421)` — acceleration algebraically
  cancels. Raising accel past ~100 k buys nothing; raising SCV from 5 to 30 buys everything.
- **Speed reliability here is stochastic.** A level either passes 10/10 or it doesn't. 1000
  mm/s is 3/8. 850 mm/s failed on run **10 of 10**. Any single-run speed claim — including
  the ones originally published in this repo's own history — is an upper bound with no
  reliability attached.
- **Don't "optimise" the shift-register hot path.** A change that benchmarked +54 % and
  passed a 40/40 pulse-integrity check shut the MCU down within 27–132 moves under real
  four-stepper load, where baseline ran 1716 moves six times over. Two independent causes:
  skipping the hardware handshake on a cycle-count deadline, and removing an implicit bus
  barrier that a read-modify-write was providing. Reverted; baseline shipped.
- **The drivers are TMC2160, not TMC5160.** FluidNC's config mislabels them. Klipper's
  `[tmc5160]` section is still the correct syntax — the register maps are identical.
- **`sense_resistor` on V1.1 is 0.075**, not the 0.022 in BTT's published `rodent.yaml`.
  Getting this wrong sets 3.4× the intended current.

## Building

Requires ESP-IDF v5.5.1 and `xtensa-esp32-elf-gcc`. `source ~/esp/esp-idf/export.sh` first,
then `./build-esp32.sh`.

Two build gotchas that cost real time: never `make V=1` (it propagates through `MAKEFLAGS`
into the CMake `env_for_cmake` shell-out and the parser dies), and never byte-compare the
bootloader after flashing (esptool patches header byte 3 and recomputes the trailing
SHA-256, so 33 bytes always differ).

## Running the harnesses

The scripts expect a Klipper host with `klippy-api.py` in `$HOME`. Both the host directory
and the board identity are environment variables — nothing is hardcoded:

```bash
H=/home/you RODENT_MAC=aa:bb:cc:dd:ee:ff ./rodent-flash.sh
KLIPPY_API=/path/to/klippy-api.py python3 full-print.py benchy.gcode 30 100000 0.0 2000
```

Flashing scripts refuse to run without `RODENT_MAC` set, and verify the connected chip
matches it before writing anything.

## Provenance and licensing

This repository is **GPL-3.0-or-later** (see `LICENSE`), matching Klipper and the ESP32 port
it builds on.

- The ESP32 port is [`fermino/klipper-esp32-port`](https://github.com/fermino/klipper-esp32-port).
  It is not vendored here — clone it yourself. Patches developed here are in
  `firmware/rodent-klipper-patches.patch` and are being offered upstream.
- Two other ESP32 Klipper efforts exist and are worth knowing about:
  [`nikhil-robinson/klipper_esp32`](https://github.com/nikhil-robinson/klipper_esp32) and
  [`kluoyun/klipper`](https://github.com/kluoyun/klipper/tree/dev/esp32) (branch
  `dev/esp32`), the latter being bare-metal, structured as a Klipper fork branch, and
  shipping TWAI/CAN.
- BigTreeTech's Rodent manual and schematic are **not** redistributed here; get them from
  BTT. The board's factory FluidNC image is likewise not included.

## Status

The proximate firmware cause of the residual `Timer too close` failures is still
unidentified. Roughly 40 % of shift-register bandwidth headroom is measured and unclaimed.
Klipper has no Modbus, so the Huanyang VFD that FluidNC drove has no equivalent — a real
functional regression for CNC use.
