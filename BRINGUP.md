# Klipper-on-ESP32 bring-up — M0 → M2

Everything below runs on a **bare WROOM-32 devkit**, not the Rodent. The point is that
only one thing can be wrong at a time: no shift register, no daisy-chained TMCs.

Build artifacts are already made (`./build-esp32.sh`):

| file | size | flash offset |
|---|---|---|
| `fermino-esp32/out/bootloader/bootloader.bin` | 25,888 | `0x1000` |
| `fermino-esp32/out/partition_table/partition-table.bin` | 3,072 | `0x8000` |
| `fermino-esp32/out/klipper.bin` | 190,032 | `0x10000` |
| `fermino-esp32/out/klipper.dict` | 8,996 | (host side — klippy reads this) |

## Pin choices for the devkit

Safe on a WROOM-32: output-capable, broken out, not strapping pins, not UART0.

| use | pins | why not others |
|---|---|---|
| step | GPIO18, GPIO19, GPIO21 | — |
| dir | GPIO22, GPIO23, GPIO25 | — |
| **avoid** | GPIO1/GPIO3 | UART0 — this is the Klipper serial link |
| **avoid** | GPIO6–GPIO11 | wired to the SPI flash |
| **avoid** | GPIO34–GPIO39 | input-only, no output driver |
| **avoid** | GPIO0/2/5/12/15 | strapping pins; GPIO12 sets flash voltage at boot |

## M0 — does it boot and talk?

```bash
./flash-devkit.sh /dev/cu.usbserial-XXXX     # backs up existing flash first, then verifies
cd fermino-esp32
./klippy/console.py /dev/cu.usbserial-XXXX -b 250000
```

Success = console.py prints the `MCU config` line and the connection stays up. That proves
serial framing, `timer_read_time()` and clock sync — the porting guide's hardest step.

Confirm in that banner: `CLOCK_FREQ=240000000` and `STEPPER_STEP_BOTH_EDGE=1`.

## M1 — step rate benchmark

Per `docs/Benchmarks.md`. Because this build advertises `STEPPER_STEP_BOTH_EDGE=1`, use
the dedge parameters (`invert_step=-1`, `step_pulse_ticks=0`) — one timer event per step.

Config, pasted into console.py:

```
allocate_oids count=3
config_stepper oid=0 step_pin=GPIO18 dir_pin=GPIO22 invert_step=-1 step_pulse_ticks=0
config_stepper oid=1 step_pin=GPIO19 dir_pin=GPIO23 invert_step=-1 step_pulse_ticks=0
config_stepper oid=2 step_pin=GPIO21 dir_pin=GPIO25 invert_step=-1 step_pulse_ticks=0
finalize_config crc=0
```

Test block — bisect `ticks` down until it stops completing:

```
SET start_clock {clock+freq}
SET ticks 1000

reset_step_clock oid=0 clock={start_clock}
set_next_step_dir oid=0 dir=0
queue_step oid=0 interval={ticks} count=60000 add=0
set_next_step_dir oid=0 dir=1
queue_step oid=0 interval=3000 count=1 add=0

reset_step_clock oid=1 clock={start_clock}
set_next_step_dir oid=1 dir=0
queue_step oid=1 interval={ticks} count=60000 add=0
set_next_step_dir oid=1 dir=1
queue_step oid=1 interval=3000 count=1 add=0

reset_step_clock oid=2 clock={start_clock}
set_next_step_dir oid=2 dir=0
queue_step oid=2 interval={ticks} count=60000 add=0
set_next_step_dir oid=2 dir=1
queue_step oid=2 interval=3000 count=1 add=0
```

After a failure (`Rescheduled timer in the past` / `Stepper too far in past`):

```
clear_shutdown
```

Score it:

```
ECHO Test result is: {"%.0fK" % (3. * freq / ticks / 1000.)}
```

**Reference points** (from `docs/Benchmarks.md`, 3-stepper):

| MCU | ticks | ≈ steps/sec |
|---|---|---|
| stm32f446 @168 MHz | 205 | 2,458K |
| sam4e8e @120 MHz | 215 | — |
| stm32f103 @72 MHz | 264 | 818K |
| avr @16 MHz | 486 | 99K |
| **esp32 @240 MHz** | **?** | **? ← M1 produces this** |

A caveat worth remembering when reading the result: `CONFIG_HAVE_STEPPER_OPTIMIZED_BOTH_EDGE`
is **0** in this build, so `stepper.c` uses the generic `stepper_event` rather than the
inlined ARM fast path. Some headroom is likely being left on the table; whether it's worth
chasing depends on what number M1 returns.

## M2 — jitter soak

The result that actually decides whether any of this is trustworthy. Square wave on one
GPIO, scope on it, left running for an hour.

Watch for: cycle-to-cycle jitter, and any `Rescheduled timer in the past` shutdown.
The dispatch loop tolerates lateness up to 1000 µs before shutting down
(`src/generic/timer_irq.c`), so a clean scope trace over a long window is the bar — the
theory being tested is that `noflash.lf` really did put every hot path in IRAM.

## Only after M0–M2 pass

- **M3** — real hardware on the devkit: stepper driver breakout, 100k thermistor + 4.7k
  divider on ADC1, microswitch endstop.
- **M4** — Rodent. Needs `SR_PIN_DATA=21`, `SR_PIN_CLK=22`, `SR_PIN_LATCH=17`,
  `SR_BYTE_NO=2`, and the `I2SO.n → SRn` bit order verified **with a meter** — FluidNC
  drives those 595s over I2S, fermino drives them over SPI2, and the bit ordering is not
  guaranteed to agree.
- **M5** — TMC5160 SPI daisy-chain support for klippy (~200–300 lines of Python; Klipper
  has no such concept today, `klippy/extras/bus.py:129` requires a per-driver `cs_pin`).

## Local patches carried on top of fermino's tree

| file | fix |
|---|---|
| `src/esp32/gpio/gpio_pwm.h` | `ledc_ll_set_duty_start()` gained a 4th `bool` arg in IDF ≥5.5.1 |
| `scripts/ctr/extract_sections` | `mktemp --suffix` is GNU-only; absent on macOS |
| `src/esp32/gpio/gpio_pwm.c` | declare `PWM_MAX`; klippy hard-errors on `hardware_pwm: True` without it |

All three are upstreamable to fermino. The `mktemp` one implies nobody has built this
port on a Mac before.

## Build gotcha

**Never `make V=1`.** It propagates through `MAKEFLAGS` into the `make env_for_cmake` that
CMake shells out to, so recipe lines get echoed; `echo "__ENV_END__"` has no `=` and the
CMake parser dies with `list index: 1 out of range`. Looks like a bug in the port; isn't.
