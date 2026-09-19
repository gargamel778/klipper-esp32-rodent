# Klipper: ESP32 as a secondary (GPIO-only) MCU — feasibility & effort report

Reviewed against Klipper master @ `cab2f00` (2026-08-06).

---

## 1. Verdict

**Technically possible, and already partly done by two community forks — but the effort is a
real MCU port (~1,500–2,500 lines of new C, 4–8 weeks FTE for someone fluent in *both*
Klipper internals and ESP-IDF low-level), and "GPIO-only" saves you much less than you'd
expect.**

The critical finding: restricting scope to "no motion system" removes the *throughput*
requirement but **not the determinism requirement**. Klipper's MCU has no concept of a
low-priority peripheral — every heater PWM cycle, every endstop sample, every ADC read and
the host clock-sync itself run off the same single hardware-timer dispatch loop, and that
loop calls `try_shutdown("Rescheduled timer in the past")` if it ever runs >1000µs late
(`src/generic/timer_irq.c:56`). So you still have to solve the hard ESP32 problem —
guaranteeing IRQ latency — you just don't have to solve it at 100 kHz.

Second critical finding: **the only compelling reason to want ESP32 here is WiFi, and WiFi
is precisely what is off the table.** More on that in §6.

---

## 2. What Klipper actually requires from a port

Klipper's MCU layer is unusually clean. `src/generic/` holds arch-neutral code; the build
symlinks `out/board -> src/<arch>/`, so `#include "board/gpio.h"` resolves to your port
first and falls back to `src/generic/`. Only three headers form the real contract:

| Header | Symbols a port must supply |
|---|---|
| `board/misc.h` | `timer_read_time`, `timer_kick`, `timer_from_us`, `timer_is_before`, `console_sendf`, `console_receive_buffer`, `dynmem_start/end`, `bootloader_request` |
| `board/irq.h` | `irq_disable/enable/save/restore/wait/poll` |
| `board/gpio.h` | `gpio_out_*`, `gpio_in_*`, `gpio_pwm_*`, `gpio_adc_*`, `spi_*`, `i2c_*` |

Everything else — `sched.c`, `command.c`, `basecmd.c`, `gpiocmds.c`, `adccmds.c`,
`pwmcmds.c`, `trsync.c`, `endstop.c`, all the sensor drivers — is arch-independent and
compiles unchanged.

Reusable arch-neutral helpers in `src/generic/`: `timer_irq.c`, `serial_irq.c`,
`crc16_ccitt.c`, `alloc.c`, `canserial.c`, `usb_cdc.c`.

**Not reusable on ESP32:** `armcm_boot.c`, `armcm_irq.c`, `armcm_timer.c`, `armcm_reset.c`,
`armcm_link.lds.S`. These are Cortex-M specific and every ARM port leans on them heavily.
This is the single largest structural difference between an ESP32 port and every port added
in the last eight years.

### Reference sizes (hand-written port code, excluding vendor headers in `lib/`)

| Port | Lines |
|---|---|
| `src/hc32f460` (minimal, serial+GPIO+ADC+PWM) | 865 |
| `src/lpc176x` | 1,627 |
| `src/linux` | 1,795 |
| `src/rp2040` (incl. USB + CAN) | 2,076 |
| `src/atsamd` | 2,649 |

The official porting guide is `docs/Code_Overview.md` § "Porting to a new micro-controller"
(9 steps). Its own reference example — the LPC176x initial bring-up commit `970831ee` — was
**250 lines** of Klipper code (`main.c` 31, `serial.c` 79, `timer.c` 59, `gpio.c` 28,
Kconfig 34, Makefile 42) plus 6,300 lines of vendored CMSIS/device headers. GPIO support
(`c78b9076`) was another **120 lines**. That is the realistic scale *when CMSIS gives you
boot, vectors and a systick for free*. ESP32 gives you none of those three.

### Host side: essentially free

- Secondary MCUs are already a first-class concept — `[mcu my_extra_mcu]` in
  `docs/Config_Reference.md:73`, no code needed.
- Pin names are **declared by the firmware**, not the host:
  `DECL_ENUMERATION_RANGE("pin", "gpio0", 0, NUM_GPIO)` in `src/rp2040/gpio.c:24` is the
  whole mechanism. They travel to klippy in the MCU data dictionary.
- So a working ESP32 port needs **zero or near-zero changes under `klippy/`**. All the work
  is in `src/`.

---

## 3. What ESP32 specifically breaks

### 3.1 FreeRTOS / ESP-IDF (the headline problem)
ESP-IDF boots FreeRTOS before `app_main()`. Klipper cannot run under a preemptive RTOS
scheduler and expect its timer guarantees — FreeRTOS critical sections mask interrupts up
to level 3 for unbounded-ish durations you don't control. This is the objection raised
repeatedly on the Klipper Discourse thread ("It is the intermediate layer. So, we can't
really control anything or guarantee anything.").

Two ways out, both proven by the existing forks:
- Use ESP-IDF only as a register/HAL header source and take over boot yourself (fermino's
  approach: *"only the HAL/Low-Level layers are used for anything time critical"*).
- Keep IDF but pin Klipper to a core with FreeRTOS effectively neutered on it.

Either is doable. Neither is a weekend.

### 3.2 Boot, image format, flashing
No CMSIS equivalent. You must supply:
- a startup path and linker script from scratch (replacing `armcm_boot.c` + `armcm_link.lds.S`),
- interrupt-matrix setup and vector installation (replacing `armcm_irq.c`),
- a hardware-timer dispatch driver (replacing `armcm_timer.c`) — TIMG or SYSTIMER,
- an ESP image header + partition table so the ROM/2nd-stage bootloader will accept it
  (`esptool.py elf2image`),
- a new flashing path in `scripts/flash_usb.py` (esptool over UART with DTR/RTS strapping).

### 3.3 IRAM and the flash cache
All timer/IRQ code must live in IRAM (`IRAM_ATTR`) with its data in DRAM, or the first
SPI-flash cache miss inside the ISR blows your latency budget. This is routine ESP32
practice but it means auditing what LTO inlines where — and Klipper builds with
`-flto=auto -fwhole-program` plus `INLINE_STEPPER_HACK`, which deliberately inlines across
translation units. Expect to fight the linker.

### 3.4 Toolchain distribution
- **Xtensa (ESP32 / S2 / S3):** requires Espressif's *patched* GCC built from their
  crosstool-NG fork. Not in Debian/Ubuntu. Klipper today needs only `avr-gcc` and
  `arm-none-eabi-gcc`, both apt-installable. This is a genuine upstream-acceptance obstacle,
  not just an inconvenience.
- **RISC-V (ESP32-C3 / C6 / H2 / P4):** RV32IMC. Buildable with `riscv32-esp-elf` or
  Debian's stock `gcc-riscv64-unknown-elf` with `-march=rv32imc`. **Much** lower barrier.

### 3.5 ADC — matters more than you'd think for a GPIO expander
The obvious use for a secondary MCU is thermistors + heaters + fans + endstops. ESP32's ADC
is the weak link:
- **ADC2 is unusable whenever the WiFi driver is running** — hardware arbitration, ADC2
  reads simply fail between `esp_wifi_start()` and `esp_wifi_stop()`.
- **ADC1 is materially non-linear**, worst at 11 dB attenuation and above ~2.6 V, and
  factory two-point calibration does not remove the non-linearity. Poor resolution below
  0.2 V and above 3.0 V.

For thermistor work that's a real accuracy hit versus the 12-bit ADCs on RP2040/STM32 that
Klipper users are calibrated against. Workable, but you'd want a per-chip calibration curve
in `gpio_adc_read()` or an external ADS1x1x — and Klipper already supports the latter.

### 3.6 Licensing (a formal requirement of the porting guide)
ESP-IDF is Apache-2.0, which **is** compatible with GPLv3 one-way — Apache-2.0 source can
be incorporated into a GPLv3 work. So vendoring IDF HAL headers into `lib/` is legally
clean, and `lib/README` would need the provenance note the guide demands. (The proprietary
blobs are the WiFi/BT libs — irrelevant if you don't use the radio.)

---

## 4. Prior art — don't start from zero

| Project | Approach | State |
|---|---|---|
| [fermino/klipper-esp32-port](https://github.com/fermino/klipper-esp32-port) | True port. ESP-IDF used for HAL/low-level only; actively stripping FreeRTOS out of the timing path. Explicitly **no WiFi/BT**, ever. Stated goal: upstream it. | Active, reports "predictable latencies" |
| [nikhil-robinson/klipper_esp32](https://github.com/nikhil-robinson/klipper_esp32) | Wraps upstream Klipper as an ESP-IDF *component* (klipper as a git submodule); CMake replicates the `.compile_time_request` objcopy → `buildcommands.py` step. | Experimental, explicitly unstable, ~73 commits; author reports ESP32 / S3 / H2 running |
| [Klipper#199](https://github.com/Klipper3d/klipper/issues/199) | Original 2018 feature request (wireless toolhead) | **Closed** |
| [Discourse: Klipper ESP32 support](https://klipper.discourse.group/t/klipper-esp32-support/25503) | Maintainer/dev discussion | Consensus: feasible, non-trivial, FreeRTOS is the blocker, community-driven only |

Neither fork is officially endorsed. Note that the Klipper build's ctr mechanism
(`objcopy -j .compile_time_request` → `scripts/buildcommands.py`, top-level `Makefile`) is
fully architecture-neutral — nikhil-robinson's fork proves it works outside the stock
Makefile, so that part is a solved problem you can copy.

---

## 5. Effort breakdown

Assumes target = **ESP32-C3 or C6** (see §7), scope = serial link + timer + GPIO in/out +
ADC + hardware PWM. No steppers, no CAN, no USB, no WiFi.

| Phase | Work | New/ported LOC | Effort (expert) |
|---|---|---|---|
| 0 | `src/esp32/{Kconfig,Makefile}`, linker script, startup, image gen, `flash_usb.py` path, vendor IDF headers into `lib/` | 400–600 | 1–2 wk |
| 1 | UART serial + `timer_read_time()`; verify with `console.py` (guide steps 3–4 — *"the most difficult step"*) | ~200 | 1 wk |
| 2 | Hardware-timer IRQ dispatch, IRAM placement, `irq_*` primitives, FreeRTOS neutralisation | ~150 | 1–2 wk ⚠️ **highest risk** |
| 3 | GPIO in/out + pin enumeration | ~150 | 2–3 days |
| 4 | ADC (+ linearity handling), LEDC hard PWM, watchdog, chipid | 300–500 | 1 wk |
| 5 | Sample `config/`, `test/configs/*.config`, docs, `docs/Benchmarks.md` numbers, soak testing | ~100 | 1 wk |
| 6 | *(optional)* SPI + I²C for sensors | 300–400 | 1 wk |

**Totals**

| Scenario | Calendar |
|---|---|
| Fluent in Klipper internals **and** ESP-IDF low-level; bench-working GPIO/ADC/PWM secondary MCU | **4–6 weeks FTE** |
| …plus hardening to "I'd run it on a printer that heats unattended" | **+3–4 weeks** |
| Strong embedded dev, learning one of the two codebases | **3–4 months** |
| Starting from fermino's fork instead of scratch | plausibly **halves** phases 0–2 |
| To upstream-mergeable quality (Kevin's review bar, CI build tests, toolchain story, benchmarks) | **6+ months part-time, and acceptance is unlikely** — the Xtensa toolchain problem alone is probably disqualifying; RISC-V-only is a better pitch |

---

## 6. The thing worth saying plainly

For pure GPIO expansion, ESP32 is the wrong chip and the economics are brutal:

- An **RP2040-Zero is ~$2**, has 26 usable GPIO, a 12-bit ADC, is a **first-class Klipper
  MCU today**, flashes by drag-and-drop UF2, and needs **zero** firmware development.
- An **STM32F103 "Blue Pill" is ~$2** and likewise already supported.
- The `[mcu extra]` plumbing you'd use is identical either way.

So the ESP32 case only makes sense if you want the radio — a wireless toolhead or a remote
sensor/heater node. And that's exactly what you can't have:

- `klippy/serialhdl.py` supports **UART** (`connect_uart`), **pipe** (`connect_pipe`), and
  **CANbus** (`connect_canbus`). **There is no TCP/UDP transport**, on either side.
- Adding one is not just a socket — Klipper's protocol assumes a low-jitter, low-loss link;
  over WiFi you'd need a much deeper retransmit/buffering story on both host and MCU, and
  any association hiccup becomes a printer shutdown mid-print.
- Both existing ESP32 forks **explicitly disclaim WiFi**. fermino's README: *"This project
  DOES NOT aim for wifi/bt support, and will likely never support it."* That is not an
  oversight; it's the informed conclusion of the people who went furthest.
- And if you *did* enable the radio, §3.5 says half your ADC pins stop working.

If you want a wireless toolhead today, the answer that exists is **CAN over a single
twisted pair** (`docs/CANBUS.md`) — which Klipper supports properly, including on the $2
RP2040 via software CAN (`lib/can2040`).

---

## 7. If you do it anyway — recommendations

1. **Target ESP32-C3 or C6, not the classic Xtensa ESP32.** RISC-V, stock-ish toolchain,
   simpler interrupt model, ~$1.50. Removes the single biggest upstreaming objection.
2. **Fork fermino's port rather than starting clean.** It already made the correct
   architectural call (IDF as headers, not as an RTOS).
3. **Follow `docs/Code_Overview.md` steps 3→4→5 literally.** Get `console.py` talking over
   UART *before* touching GPIO. The guide is right that comms bring-up is the hard part.
4. **Prove determinism before writing a single peripheral driver.** Free-run the MCU with a
   1 kHz repeating timer toggling a pin, scope the jitter for an hour. If you can't hold
   ±few µs with everything else running, stop — nothing downstream will be trustworthy.
5. **Never use ADC2**, and characterise ADC1 against a known reference before trusting a
   thermistor reading to it.
6. **Bench-only until it survives a multi-day soak.** A `Rescheduled timer in the past`
   shutdown mid-print is the *good* failure mode; a stuck heater PWM is the bad one.

---

## Sources

- Klipper source @ `cab2f00`: [`docs/Code_Overview.md`](https://github.com/Klipper3d/klipper/blob/master/docs/Code_Overview.md),
  `src/Kconfig`, `src/Makefile`, `src/generic/`, `src/rp2040/`, `src/hc32f460/`,
  `src/simulator/`, `klippy/serialhdl.py`, `docs/Config_Reference.md`
- [fermino/klipper-esp32-port](https://github.com/fermino/klipper-esp32-port)
- [nikhil-robinson/klipper_esp32](https://github.com/nikhil-robinson/klipper_esp32)
- [Klipper3d/klipper issue #199 — MCU support for ESP32](https://github.com/KevinOConnor/klipper/issues/199)
- [Klipper Discourse — Klipper ESP32 support](https://klipper.discourse.group/t/klipper-esp32-support/25503)
- [ESP-IDF: ADC2 unavailable with WiFi](https://github.com/espressif/arduino-esp32/issues/440),
  [ESP32 forum — ADC non-linearity](https://esp32.com/viewtopic.php?t=2881&start=30)
- [Espressif Xtensa toolchain setup](https://docs.espressif.com/projects/esp-idf/en/release-v3.0/get-started/linux-setup.html)
