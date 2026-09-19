# I put Klipper on a $6 ESP32 and it kept up with a high-end hotend

Klipper's architecture makes the microcontroller almost boring on purpose. The host does the planning and the step generation; the MCU is a timed I/O engine sitting behind a small HAL. So the question that started this: could an ESP32 be that engine?

Not as a GPIO expander. As the *primary* MCU, motion and all.

The target was a **BTT Rodent V1.1** — a classic Xtensa ESP32 with four TMC2160 drivers, where every step, direction and enable line runs through a single 74HC595 shift register. That last detail matters more than anything else in this post.

(A note on the drivers, because it trips people up: they're **TMC2160**, not TMC5160. The stock FluidNC config declares them as 5160 because FluidNC has no direct TMC2160 support. The substitution is fine in Klipper too — a TMC2160 is a TMC5160 without the integrated motion controller, and the driver half of the register map is identical. Klipper runs these in step/dir mode and touches only those registers, so a `[tmc5160]` section is correct for this board.)

The short version: it works. The board completes a rule-legal SpeedBoatRace Benchy in **3 minutes 45 seconds, ten times out of ten**, at 800 mm/s with 100k acceleration and a square-corner velocity of 30.

That 800 isn't arbitrary. At 0.25 mm layers and 0.5 mm line width the profile extrudes 0.125 mm² per mm of path, so **800 mm/s is exactly 100 mm³/s** — the melt rate of a high-end hotend. The board's reliable maximum lands precisely where the *hotend* gives out. **The controller isn't the bottleneck.**

The longer version is more useful, because almost every measurement I took along the way pointed the wrong way first — including the one I originally published as the headline.

---

## The hardware bit

Base is `fermino/klipper-esp32-port`, which needed four patches before it would build and run on current tooling: an IDF 5.5.1 signature change, a GNU-only `mktemp --suffix` (nobody had built this on a Mac), a missing `PWM_MAX` constant that killed any `hardware_pwm: True` at startup, and enabling `HAVE_STEPPER_OPTIMIZED_BOTH_EDGE` — one timer event per step instead of two, worth **+18%** for free.

Three board-level traps, all of which cost real time:

**The sense resistors are 0.075 Ω, not 0.022.** BTT's published config says 0.022. Use it and you command **3.4× the intended motor current**. Check the silkscreen — mine reads `R075`, and the official manual's V1.1 pin diagram labels the part `RSENSE 75MR`.

**USB-C has no CC pull-downs.** A C-to-C cable will never enumerate. You need legacy A-to-C.

**It wants 24 V minimum.** The board is rated DC24–56 V. My first study ran at 20 V, below spec — which probably explains a StallGuard collapse I saw at 600 mm/s and initially read as an electrical limit.

**It browns out on USB power.** 3 KB flash reads worked. A 4 MB backup read dropped the port entirely. Connect the supply before you do anything involving the flash.

Rotation alone is weak proof that stepping works, so I verified pulses at the driver instead. The TMC2160 exposes **MSCNT** (reg 0x6A), a microstep counter that advances on every STEP pulse it receives — on the far side of the shift register, independent of anything the MCU believes. At 16 microsteps each pulse advances it by exactly 16, so N pulses must produce `(N × 16) mod 1024`. Forty of forty cases exact, both directions, up to 12,037 pulses at 64,000 steps/s.

Remember that test. It comes back to bite me.

---

## The one setting that mattered most

My first synthetic CoreXY workload hit a hard wall at 300 mm/s with an MCU shutdown, and I wrote a confident analysis attributing it to firmware scheduling — concurrency between four steppers, not raw throughput.

It was the baud rate.

`CONFIG_SERIAL_BAUD` is compile-time and has to match klippy's `baud:`. Changing only that value:

| print mm/s | 250000 | 921600 | **1500000** |
|---:|---:|---:|---:|
| 100 | 3.02% | 1.17% | **0.20%** |
| 200 | 7.50% | 3.96% | **0.37%** |
| 300 | **shutdown** | 10.09% | **0.95%** |
| 400 | — | **shutdown** | **3.68%** |
| 800 | — | — | **8.88%** |

And here's the part worth carrying away: **the rates are individually good or bad, not a speed limit.** 1500000 comfortably beats 2000000. Working: 115200, 921600, 1500000. Broken: 250000, 460800, 2000000.

The broken ones fail in two very different ways. 460800 and 250000 die at identify — you never connect, and it's obvious. **2000000 connects, reports ready, drives all four motors correctly in both directions, passes every bench check — and then corrupts ~6% of bytes under sustained load.** That's the one that would eat a print.

One correction to my own reasoning here: I first said this was bandwidth. It isn't. At 1.5 Mbps the link carries ~150 kB/s and the workload used 4–8. It's **latency** — a 64-byte message takes 2.6 ms at 250000 and 0.43 ms at 1500000, and `Timer too close` is by definition a late-arrival failure.

---

## The firmware optimisation that lied to me

Every step pulse on this board goes through the 74HC595. First useful measurement: run Klipper's own three-stepper benchmark on SR pins, then on direct GPIO, same board and firmware.

- Direct GPIO: **1,200 K steps/s**
- Shift register: **720 K steps/s**

The SR costs **40%**. And since raising the SR clock to 40 MHz had changed nothing measurable, that cost had to be **CPU-side APB register access, not shift duration**. Good diagnosis. Three changes followed:

| build | change | steps/s |
|---|---|---:|
| baseline | — | 720 K |
| v2 | CCOUNT deadline replaces the busy-wait poll | 900 K |
| v3 | single store to SPI_CMD instead of read-modify-write | 1,028 K |
| v4 | chain state kept pre-reversed in one word | **1,107 K** |

**+54% on the benchmark. 40/40 on MSCNT pulse integrity.** Both green.

Then I ran the same builds against a real four-stepper CoreXY load at 400 mm/s:

| build | benchmark | moves before failure | outcome |
|---|---:|---:|---|
| baseline | 720 K | 1716 ×6 | completed every time |
| v4 | 1,107 K | 132 | **shutdown** |
| v2 | 900 K | **27** | **shutdown** |

The bisect found two independently harmful changes, for two different reasons.

**The CCOUNT deadline is unsafe.** Skipping the hardware handshake because elapsed time "proves" the transfer finished falls apart under four concurrent steppers landing sub-microsecond apart.

**The plain store to SPI_CMD removes an implicit bus barrier.** `spi_ll_user_start()` is `hw->cmd.usr = 1` — a bitfield write to a volatile register, which the compiler must implement as an APB *read*, an OR, and an APB write. That read is what forces the preceding posted `data_buf` write to land before the transfer begins. Take it away and the SPI can start shifting before the data arrives.

The only safe change was hoisting a ROM call out of the wait loop. It shows **zero benchmark gain** — the call only runs when the loop actually spins — and is consistently better under real load. I shipped stock firmware anyway: functionally equivalent, and zero divergence from the fork beats a 0.39-point retransmit improvement that buys no capability.

**Why MSCNT passed builds that died in 27 moves:** it drives one axis at a time. Even at 64,000 steps/s, steps on a single axis are ~15 µs apart, so the deadline had always expired and the broken fast path was never exercised. A correctness test that can't reach the failure mode isn't evidence of correctness — and I'd presented it as though it were.

---

## Synthetic tests were harder than real printing

I assumed a real print would be roughly 10× harder than my synthetic protocol. Real geometry has 0.5 mm segments and hundreds of moves per second, against my harness's long moves at 9–15/s.

Exactly backwards.

My synthetic protocol commanded **43–69 mm moves at 400–1000 mm/s: 32,000–80,000 steps/s per motor.** A real Benchy on 0.5 mm segments is acceleration-limited to about **6,000**. Many tiny slow moves are far cheaper than a few enormous fast ones — Klipper's step compression means command volume tracks step rate, not move count.

A 0.2 mm quality Benchy turned out to be *impossible* to stress:

| commanded | **effective** | steps/s/motor | retx% |
|---:|---:|---:|---:|
| 200 mm/s | 64.3 | 5,146 | 0.094 |
| 600 mm/s | 75.5 | 6,037 | 0.084 |
| 1000 mm/s | **76.3** | 6,102 | 0.116 |

**Commanding 5× the speed produced 19% more motion.** The toolhead is geometry-bound. Which also means "printing a Benchy at 200 mm/s" at 0.2 mm layers is largely fiction — the real average is ~64 mm/s, and no controller changes that.

---

## Square corner velocity, not acceleration

Here's the bit of Klipper maths I wish I'd read on day one. From `toolhead.py`:

```
junction_deviation = SCV² × (√2 − 1) / max_accel
move_jd_v2         = R_jd × junction_deviation × accel
```

The acceleration **cancels**:

```
v_junction = SCV × √(R_jd × 0.41421)
```

On geometry with a sub-millimetre median segment — every real print — almost every move is a junction, so **`square_corner_velocity` sets your speed and acceleration cannot touch it.** That's why my acceleration ramp moved 19% while commanding 5× the feedrate.

The Benchy's junction angles are bimodal: p10 is 1.8° (smooth hull curves), but p75 is 48° and p90 is 105°. At the default SCV 5, **a quarter of all junctions are capped at ≤10 mm/s.**

Measured, same 8% of file, settings the only variable:

| SCV | accel | duration |
|---:|---:|---:|
| 5 | 20k | 175 s |
| 15 | 20k | 150 s |
| 30 | 40k | **135 s** |
| 40 | 100k | 130 s |
| 60 | 500k | 130 s |
| 150 | **1,000,000** | 125 s |

**SCV 150 and a million mm/s²: 0.112% retransmits, zero stalls.** Roughly twenty times the acceleration of the fastest machines on the SpeedBoatRace leaderboard, and the board was quieter than at SCV 5. Everything worth having arrives by **SCV 30 / accel 50k** — past that the centripetal term takes over and more SCV buys nothing.

Two API details, both of which cost me measurements: `SET_VELOCITY_LIMIT` does **not** clamp to your configured maxima, and `ACCEL_TO_DECEL` doesn't exist in current Klipper — passing it is silently ignored.

---

## What actually loads the board

Not motion settings. The *file*.

Three changes to the slicer profile mattered more than every parameter sweep combined:

1. **Remove z-hops.** Z is a TR8x8 leadscrew — 8× the steps/mm of XY, capped at `max_z_velocity 100`. Every hop is a slow, step-dense interruption that fragments fast motion into bursts the board absorbs trivially.
2. **Remove retraction.** Each retract changes the extrusion ratio, so `instantaneous_corner_velocity` caps that junction hard.
3. **Slice fast.** With nothing interrupting, the toolhead finally sustains velocity — and sustained velocity is the only thing this board has ever been sensitive to.

Same board, same firmware:

| workload | wire | retx% | result |
|---|---:|---:|---|
| Benchy, SCV 150 / accel 1M | 6 kB/s | **0.11%** | untouched |
| speed profile, 900 mm/s | **16 kB/s** | **6.2%** | completes |

---

## The result — and the number I got wrong first

My first answer was "1000 mm/s, 3:30." I found it by bisecting: 1000 completed, 1050 failed, done. I published it.

Then I repeated it. **1000 mm/s completes 3 times in 8.** 950 completes 7 in 13. Both finish in the same 3:30 *when they finish*, so the faster setting bought nothing and lost the job about two-thirds of the time. A ceiling from a single run isn't a ceiling, it's a coin toss that landed well.

So I ran a proper staircase — ten runs per speed, drop 100 on any failure, step up 50 on a clean sweep:

| speed | SCV 50 | SCV 30 | mean retx | time |
|---:|---|---|---:|---:|
| 700 | 10/10 | — | 4.55% | 3:46 |
| 750 | **10/10** | — | 5.33% | 3:30 |
| **800** | 5/6 → failed | **10/10** | 5.02% | **3:45** |
| 850 | — | 9/10 → failed | 5.23% | 3:46 |
| 900 | 3/4 → failed | — | 6.38% | 3:30 |
| 950 | 7/13 | — | — | 3:30 |
| 1000 | 3/8 | — | — | 3:30 |

**Square corner velocity bought back exactly one level.** 800 mm/s fails at SCV 50 and runs 10/10 at SCV 30 — the junction-velocity mechanism confirmed on hardware. In the failure hotspots (~17° mean junction angle) SCV 50 permits 305 mm/s through a corner; SCV 30 permits 183. A 40% cut, precisely where the failures live.

And **850 failed on run 10 of 10.** Nine passes, then a failure. A five-run protocol would have certified it. That single data point justifies the whole ten-run design.

### Where the failures actually happen

They're not spread randomly through the print. Across 16 runs they landed at 4.7, 9.5, 10.0, 10.0, 10.8, 12.2, 20.9, 23.4, 25.9, 26.9, 86.0, 87.2 and 89.3% of the file — three tight clusters. Binning the file into 2% slices:

| | mean junction angle | mean segment |
|---|---:|---:|
| buckets where failures occurred | 24.7° | **1.92 mm** |
| everywhere else | 29.8° | 1.41 mm |

Failures cluster where segments are 36% longer and corners shallower — exactly where the toolhead can reach and hold speed. Same mechanism as everything else: **sustained velocity is what loads this board.**

### Things that turned out not to matter

**Temperature.** Success rate seemed to decay across a session, so I tested it: motors de-energised, 25 minutes idle, then five runs. Result 2/5 — identical to the hot batch. Driver over-temp flags clear after an hour of load, heatsinks at 40 °C against a 120 °C threshold, host idle. The apparent decay was clustering in a small sample.

**Motor current.** I predicted before running that raising X/Y/Z from 0.8 A to 1.4 A would change nothing, because `Timer too close` is an MCU scheduling failure and torque has no bearing on it. It changed nothing. What it did buy was mechanical — motor cases at 65–75 °C, drivers at 40 °C.

One incidental that surprises people: with four motors at 1.4 A the bench supply draws about **1.1 A at 36 V**, not 5.6. The TMC2160 is a switching regulator — it PWMs the rail to force current through a coil that needs only a few volts. Working backwards from the 0.73 A holding figure gives ~1.9 Ω per phase, which is normal.

## What I'd tell you if you're doing this

**Klipper's step-rate benchmark predicts nothing, in either direction.** It endorsed every build that later shut down, and scored the one genuinely good change at zero. Three steppers at a fixed interval with nothing else running can't see contention, coordinated moves, pressure advance, or serial traffic.

**Single runs are not evidence — and knowing that isn't the same as applying it.** It took six runs to establish that baseline lands on exactly 1716 moves every time. The failing builds died at 27 / 87 / 159 / 623 moves. "Ceiling" as a metric has a **300–400 mm/s spread on identical firmware**.

I applied that discipline rigorously to the firmware comparison — six runs, counterbalanced ordering, paired statistics — and then abandoned it completely the moment the question changed from *which firmware* to *how fast*, and published a ceiling from n=1. The lesson wasn't internalised; it was applied where I happened to be suspicious.

**Watch for tests that can't reach the bug.** MSCNT was the right instrument pointed at the wrong axis count.

**And check your harness before you blame the hardware.** One bisection inherited its upper bound from a *different file* and was about to report the ceiling 20% low. A slicer's `travel_speed = 100000` typo emitted `F6000000` travels and produced a shutdown that looked exactly like a speed limit. On MCU shutdown Klipper leaves `print_stats` at `paused`, not `error` — my completion loop reported a dead printer as healthy for two minutes.

Every one of those was caught because numbers stopped cohering, not because I was careful up front.

---

## Still open

The 40% shift-register headroom is real and unclaimed. It's CPU-side APB traffic, and any future attempt has to keep both the hardware handshake and that read-modify-write barrier — and be gated on a four-stepper load test, not a benchmark. Hardware SPI on SPI3 is still an empty stub, though it'd speed the MCU's side of TMC transfers without reducing the traffic they generate, so I'd expect less from it than it looks like. And Klipper has no Modbus, so the VFD this board used to drive under FluidNC has no equivalent.

But the headline stands, in its corrected form: **a $6 microcontroller, running mainline Klipper's motion planner, driving four TMC2160s through a shift register, finishing a legal speed Benchy in 3:45 — ten times out of ten, at exactly the flow rate a high-end hotend can melt.**

Push it harder and the hotend gives out before the ESP32 does.
