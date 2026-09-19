# Klipper on ESP32 (BTT Rodent V1.1) — motion study

> **HARDWARE CORRECTION (from the official BTT Rodent V1.x manual, p.4):** the drivers are
> **TMC2160**, not TMC5160. Older notes say 5160 because the stock FluidNC config declares
> them that way — FluidNC has no direct TMC2160 support. Klipper's `[tmc5160]` section is
> nonetheless correct: a TMC2160 is a TMC5160 minus the integrated motion controller, and
> the driver register map (CHOPCONF, IHOLD_IRUN, GLOBALSCALER, DRV_STATUS, MSCNT, MSLUT…)
> is shared. Every register-level result here, MSCNT verification included, is unaffected.
> The manual also confirms **RSENSE 75MR** on the V1.1 diagram, matching the measured
> 0.075 Ω, and its pin table matches the empirically derived SR map exactly. MCU on V1.1 is
> **ESP32-WROOM-32URA-N4**. Rated input is **24–56 V** — the 20 V phase below was under spec.

> **SUPERSEDED.** Every number below was taken at **250000 baud**, which turned out to be
> the binding constraint, not the firmware. At **1500000 baud** the CoreXY workload runs
> **60 -> 800 mm/s with no shutdown at all** and retransmits fall by up to 20x. The
> "firmware scheduling ceiling" this report's central section claims does not exist at
> the speeds described. See "Baud sweep" at the end; treat everything below as a
> lower bound produced by a bad serial rate.

Two graduated studies, 180 s per phase, on bare-metal Linux
(GPD P2 MAX, Core m3-8100Y, Ubuntu 24.04, bare metal, load < 0.5). Four NEMA17 42CM08 motors (2.5 A rated)
at **0.8 A**, unloaded, on a **20 V** bench supply.

Firmware: fermino's ESP32 port + 4 local patches, SR@40 MHz, edge optimisation on,
240 MHz CCOUNT/CCOMPARE timer, 250000 baud.

---

## Study 1 — single-axis speed ramp (120 mm square, XY only)

| vel mm/s | accel | steps/s | laps | dist | retx% | invalid | stalls | jitter | StallGuard |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 1 000 | 4 000 | 19 | 9.1 m | 0.0 | 0 | 0 | 143 µs | 22–23 |
| 100 | 2 000 | 8 000 | 37 | 17.8 m | 0.0 | 0 | 0 | 113 µs | 127–137 |
| 150 | 3 000 | 12 000 | 54 | 25.9 m | 0.0 | 0 | 0 | 120 µs | 75–80 |
| 200 | 5 000 | 16 000 | 72 | 34.6 m | 0.0 | 0 | 0 | 128 µs | 74–83 |
| 300 | 8 000 | 24 000 | 104 | 49.9 m | 0.0 | 0 | 0 | 134 µs | 176–180 |
| 400 | 12 000 | 32 000 | 137 | 65.8 m | 0.0 | 0 | 0 | 128 µs | 129–196 |
| 500 | 16 000 | 40 000 | 168 | 80.6 m | 0.0 | 0 | 0 | 127 µs | 74–82 |
| 600 | 20 000 | 48 000 | 198 | 95.0 m | 0.0 | 0 | 0 | 98 µs | **2–4** |
| 800 | 30 000 | 64 000 | 257 | 123.4 m | 0.0 | 0 | 0 | 130 µs | 76–210 |

**No degradation at any speed.** 471 m of travel, zero retransmits, zero invalid
bytes, zero host stalls, no shutdowns. Jitter flat at ~100–140 µs independent of load.

⚠ **StallGuard collapsed to 2–4 at 600 mm/s** (900 RPM). That is what a stalled or
barely-coping motor looks like. Not verified visually — see "What is not measured".

---

## Study 2 — CoreXY print simulation (real duty cycle)

Real `kinematics: corexy` so motor loading is authentic: axis-aligned perimeters
drive **both** motors, 45° infill drives **one**. Real `[extruder]` with pressure
advance 0.04, E coordinated in the same move queue. 0.3 mm z-hops on an 8 mm
leadscrew. 0.8 mm retractions at 45 mm/s.

| print | travel | accel | layers | moves | retx% | invalid | stalls | verdict |
|---:|---:|---:|---:|---:|---:|---:|---:|:--|
| 60 | 150 | 3 000 | 4 | 264 | 1.13 | 38 | 1 | degraded |
| 100 | 250 | 5 000 | 6 | 396 | 3.02 | 63 | 0 | ok |
| 150 | 350 | 10 000 | 9 | 594 | 4.45 | 62 | 0 | ok |
| 200 | 400 | 15 000 | 12 | 792 | 7.50 | 63 | 0 | marginal |
| **300** | 500 | 20 000 | 2 | 151 | **26.38** | 107 | 0 | **MCU SHUTDOWN: "Timer too close"** |

At shutdown: `bytes_retransmit=69191`, `rto` had climbed 0.025 → **0.800**,
`send_seq 23905 / receive_seq 23903` — the host was 2 messages ahead and the MCU
never caught up.

---

## The central finding: concurrency, not speed

```
ONE stepper  @ 64 000 steps/s  -> 0.0% retransmits, flawless
THREE-FOUR   @ ~54 000 steps/s -> MCU shutdown
```

At the 300 mm/s ceiling a CoreXY pure-X move commands:

| stepper | rate |
|---|---:|
| A motor | 24 000 steps/s |
| B motor | 24 000 steps/s |
| E (pressure advance active) | 5 610 steps/s |
| Z during hops | 12 000 steps/s (bursts) |
| **concurrent total** | **~54 000 steps/s** |

So a *lower* aggregate step rate spread across more steppers breaks the MCU, while a
*higher* rate on one stepper does not. `Timer too close` is a scheduling failure —
the ESP32 could not service a timer whose deadline had passed — not a throughput
failure. Klipper's own benchmark measured this port at 1.1–1.3 M steps/s, roughly
20× the rate at which it fails here, which confirms the limit is event scheduling.

Three factors compound it: number of concurrent steppers, acceleration (higher accel
means step intervals change faster, so more `queue_step` commands with `add` terms),
and pressure advance (extra finely-timed E events).

**This also explains every confusing measurement earlier in the project.** A
synthetic single-axis test says the board is flawless to 800 mm/s. A realistic
4-motor workload shuts down at 300. Both are true; only the second predicts machine
behaviour.

---

## Recommended operating envelope

| | print speed | accel | basis |
|---|---|---|---|
| **Safe** | ≤ 100 mm/s | ≤ 5 000 | 3.0% retx, no faults |
| **Usable** | ≤ 150 mm/s | ≤ 10 000 | 4.5% retx, recovered cleanly |
| **Marginal** | 200 mm/s | 15 000 | 7.5% retx — works, no margin |
| **Do not** | ≥ 300 mm/s | ≥ 20 000 | MCU shutdown mid-print |

For the CNC use this board is destined for, none of this binds: routers rapid at
~80 mm/s and cut far slower, comfortably inside the safe envelope. A fast CoreXY
printer would be constrained by it.

Note the persistent **38–63 invalid bytes per phase at every speed**, including the
gentlest. Low-level corruption is always present under this workload and Klipper's
retransmit layer absorbs it. It never escalated, but it is not zero.

---

## What is NOT measured

**Mechanical step loss.** There are no encoders. A motor that stops following still
"returns to zero" as far as Klipper is concerned, and the link stays clean because
the ESP32 keeps emitting pulses into a motor that is not obeying them. Every "OK"
above means *the electronics kept up*, not *the machine moved*.

StallGuard is the only proxy, and it flagged 600 mm/s. To settle it: mark a shaft,
run 400 and then 600 mm/s, and check whether it returns to the same orientation.

**The 20 V rail is a variable, not a constant.** 600 mm/s at 40 mm/rev is 900 RPM
from a NEMA17 at 0.8 A. Back-EMF at 20 V is very likely the real limit at the top of
Study 1 — at 24 V and especially 48 V those speeds should be far more attainable.
The **electronic** ceiling found in Study 2 will not move with supply voltage; it is
a firmware scheduling limit.

**No mechanical load.** Unloaded motors. Real gantry inertia will lower the usable
acceleration independently of anything here.


---

## Baud sweep — this is the real result

The `Timer too close` shutdown means a command arrived **after its scheduled time** — a
delivery failure, not an MCU throughput failure. The whole "concurrency ceiling" above is
an artefact of a bad serial rate. Identical firmware, identical workload, identical
hardware; only `CONFIG_SERIAL_BAUD` (and klippy's matching `baud:`) changed:

| print mm/s | 250000 | 921600 | **1500000** | 2000000 |
|---:|---:|---:|---:|---:|
| 60 | 1.13 | 1.07 | **0.05** | 6.11 → **SHUTDOWN** |
| 100 | 3.02 | 1.17 | **0.20** | — |
| 150 | 4.45 | 2.39 | **0.16** | — |
| 200 | 7.50 | 3.96 | **0.37** | — |
| 300 | 26.38 → **SHUTDOWN** | 10.09 | **0.95** | — |
| 400 | — | 9.29 → **SHUTDOWN** | **3.68** | — |
| 500 | — | — | **5.74** | — |
| 600 | — | — | **7.19** | — |
| 700 | — | — | **8.52** | — |
| 800 | — | — | **8.88** | — |

At 1500000 the ramp was extended twice and **never found an electronic ceiling**:
60 → 800 mm/s, 5.9 MB written, 32 layers in the top phase, `send_seq == receive_seq`,
`srtt` 0.001, `rto` pinned at its 0.025 floor, and **zero** `Timer too close` events in
the entire klippy log. Retransmits plateau near 9% rather than running away.

### The rates are good or bad individually — it is not a speed limit

| rate | result |
|---|---|
| 115200 | good |
| 250000 | worked initially, then failed at identify |
| 460800 | never worked (fails at identify) |
| **921600** | good |
| **1500000** | **best measured — use this** |
| 2000000 | connects, enumerates, drives all 4 motors correctly, then corrupts ~6% of bytes under load |

1500000 outperforming 2000000 by a wide margin is the proof: this is divisor/timing error
per rate, not bandwidth. Note the two **distinct** failure modes — 460800/250000 die at
identify and are obvious, while 2000000 passes every bench check and only falls apart
under sustained motion. The second kind will look healthy and then shut down mid-print.

### Revised envelope (36 V, 0.8 A, unloaded)

| | print speed | basis |
|---|---|---|
| **Safe** | ≤ 300 mm/s | <1% retx, no faults, 17 layers |
| **Good** | ≤ 500 mm/s | ≤5.7% retx, no faults |
| **Electrically OK** | ≤ 800 mm/s | ≤8.9% retx, no shutdown — but see caveat |

**Caveat on ≥500 mm/s:** these say *the electronics kept up*, not *the motors followed*.
There are no encoders, and StallGuard collapsed at 600 mm/s in Study 1. At 40 mm/rev,
600 mm/s is 900 RPM from a NEMA17 at 0.8 A. Treat the top of this table as an
electrical result, not a machine speed.

Firmware archived at `firmware/klipper-rodent-1500000baud.bin`
(md5 `df759535f15019869d64de39866fc45c`), flashed at 0x10000. The 2000000 build is kept
as `firmware/klipper-rodent-2000000baud-BAD.bin` so the trap is reproducible.

---

## Shift-register optimisation — benchmark gains that DO NOT SURVIVE REAL LOAD

> **Status: the +54% below is real on Klipper's benchmark and is NOT usable.** Every build
> that achieved it shut the MCU down at 400 mm/s on the CoreXY workload, at a speed the
> unoptimised firmware completes cleanly six times out of six. The one change that IS
> safe (v7) scores zero on the same benchmark. Read "What it actually cost" at the end
> before reusing any of this; the benchmark numbers are kept only because the GPIO-vs-SR
> comparison is still the useful measurement.

Klipper's own 3-stepper benchmark (`bench-sr.sh`), walked down a ladder of step
intervals until the MCU can no longer keep up. Both firmwares at 1500000 baud, same
board, so the only variable is the SR code path.

**First, the measurement that framed the work** — what does the 74HC595 chain actually
cost, versus putting the same three steppers on direct GPIO?

| step pins | max sustained |
|---|---:|
| direct GPIO | 1200K steps/s |
| shift register (original) | 720K steps/s |

So the SR cost **40%** of the step rate, and 1200K was the target.

The decisive clue was an apparent contradiction: raising the SR clock to 40 MHz had
changed nothing measurable, yet the SR cost 40%. Those only reconcile if the cost is
**CPU-side APB register traffic, not shift duration** - which is where all three fixes
landed. Each was measured separately:

| change | steps/s | delta |
|---|---:|---|
| original | 720K | |
| CCOUNT deadline replaces the busy-wait poll | 900K | +25% |
| single store to SPI_CMD instead of read-modify-write | 1028K | +14% |
| chain state held pre-reversed in one word | **1107K** | +8% |

**Benchmark result: 720K -> 1107K, +54%** — which turned out not to mean what it looked
like. See "What it actually cost" below.

What each change does:

1. **Deadline instead of polling.** `gpio_sr_shift_out()` busy-waited on SPI2's
   `cmd.usr`, an APB read that stalls the 240 MHz core against the 80 MHz bus, once per
   step edge. A shift-out takes a known time (SR_BIT_NO bits at the SR clock), so the
   peripheral only has to be asked when a CCOUNT deadline has not yet passed. The
   hardware poll remains as the fallback, so a FIFO write can never land mid-transfer.
2. **`hw->cmd.usr = 1` is a bitfield store to a volatile register**, which the compiler
   must implement as APB read + OR + APB write. Every other bit in SPI_CMD_REG is a
   one-shot trigger that must be zero here anyway, so a plain `cmd.val = SPI_USR` store
   is equivalent at half the bus traffic.
3. **The chain state is now kept in FIFO byte order**, so the hot path is a single store
   instead of CONFIG_SR_BYTE_NO volatile byte loads, shifts, and a `spi_ll_write_buffer()`
   call. (Applies for chains of <=4 bytes; longer chains keep the original path.)

Also fixed in passing: `spi_ll_write_buffer()` memcpys 4 bytes per `data_buf` slot out of
a `CONFIG_SR_BYTE_NO`-sized buffer, reading 2 bytes past the end of the stack buffer on
every step when that is 2.

### Correctness: verified, not assumed

A step-rate number says the firmware went faster, not that it went faster *correctly* -
a dropped or duplicated pulse looks identical on a benchmark and silently loses position
on a machine. `verify-steps.py` reads **MSCNT** (TMC5160 reg 0x6A), the driver's own
microstep-table counter, which sits on the far side of the shift register and is
therefore independent of anything the MCU believes. At microsteps=16 each STEP pulse
advances it by 16, so a commanded N pulses must produce exactly `(N * 16) mod 1024`.

**40/40 cases exact** on every optimised build: all four axes, both directions, from
8 pulses up to 12,037 pulses at 64,000 steps/s.

> Note on reading its output: the first run reported 24/32 "failures" that were the test
> assuming MSCNT counts *up* for a positive MOVE. It counts down on this DIR polarity;
> every magnitude was exact. The script now calibrates the direction per axis. Beware
> distances whose residue is +/-512 - that value is its own negation mod 1024, so such a
> case cannot distinguish a correct move from a perfectly reversed one.

### What it actually cost — bisected against a real workload

Cold single-phase CoreXY run at 400 mm/s, identical workload and baud, firmware the only
variable, runs alternated:

| firmware | change from stock | benchmark | moves | retx% | outcome |
|---|---|---:|---:|---|---|
| baseline | - | 720K | 1716 x6 | 4.91-5.89 | completed x6 |
| **v7** | **hoist ROM call out of the wait loop** | **720K** | **1716 x2** | **4.09 / 4.77** | **completed x2** |
| v6 | + plain store to SPI_CMD | 1028K | 1716 x2 | 11.5 / 12.0 | completed, 2x retx |
| v5 | + one-word chain state | 1028K | 623 / 87 / 159 | 20.7-24.6 | **shutdown x3** |
| v4 | + CCOUNT deadline | 1107K | 132 | 26.42 | **shutdown** |
| v2 | CCOUNT deadline alone | 900K | 27 | 7.26 | **shutdown** |

**Two changes are independently harmful, for different reasons:**

1. **The CCOUNT deadline (v2) is unsafe.** Skipping the hardware handshake because elapsed
   time "proves" the transfer finished fails under four concurrent steppers, where steps
   land sub-microsecond apart. 27 moves before shutdown.
2. **The plain store to SPI_CMD (v6) removes an implicit bus barrier.** `hw->cmd.usr = 1`
   compiles to a read-modify-write, and that READ forces the preceding posted `data_buf`
   write to land before the transfer starts. Replace it with a bare store and the SPI can
   begin shifting before the FIFO data has arrived. It does not shut down on its own, but
   it doubles retransmits - and combined with the word-state change it does shut down.

**The only safe change is v7**, and note what it is: strictly less work, no semantic
change, no timing assumption, and it shows **zero benchmark gain** because the ROM call
only runs when the wait loop actually spins. It is nonetheless consistently better on a
real workload (4.43% mean retx vs 5.28% baseline). Verified 40/40 on MSCNT.

Three lessons, all about the tests rather than the code:

1. **Klipper's step-rate benchmark does not predict machine behaviour, in either
   direction.** It endorsed every build that later shut down, and it scored the one
   genuinely good change at exactly zero. It drives three steppers at a fixed interval
   with nothing else running - no four-stepper contention, no coordinated moves, no
   pressure advance, no serial traffic.
2. **MSCNT pulse-integrity passed 40/40 on builds that died in 27 moves.** It drives one
   axis at a time, so steps are microseconds apart; it structurally cannot reach the
   sub-microsecond inter-stepper spacing where these changes break. A correctness test
   that cannot reach the failure mode is not evidence of correctness.
3. **Single runs are not evidence.** Baseline is repeatable to +/-0.5% retx and lands on
   exactly 1716 moves every time, which is what made the comparison trustworthy - but only
   after running it six times. The failing builds fail at wildly varying points
   (27 / 87 / 159 / 623 moves), so a single run could have been read either way.

The durable result is the GPIO-vs-SR measurement: the shift register costs 40% of the step
rate, and that cost is CPU-side APB traffic rather than shift duration (a 40 MHz SR clock
changed nothing). That headroom is real and remains unclaimed. Any future attempt must
keep both the hardware handshake and the read-modify-write barrier, and be gated on a
four-stepper load test rather than a benchmark.

---

## Final decision: baseline vs v7, 6 maximum-finding runs, counterbalanced

Three rounds, order swapped each round (baseline,v7 / v7,baseline / baseline,v7), every
run cold with a verify-flashed image and a klippy state=ready gate, ramping 400 -> 1000
mm/s at 180 s per phase. Only the firmware image differed.

### The ceiling is far too noisy to decide on

| firmware | ceilings (3 runs) | spread |
|---|---|---:|
| baseline | 700, 800, **1000** | 300 |
| v7 | 600, 800, **1000** | 400 |

**The same firmware varies by 300-400 mm/s run to run.** Both reach 1000 at best and both
have a bad round. This metric cannot resolve a difference between two firmwares at n=3,
and any conclusion drawn from one run of it - in either direction - would be luck.

This also retroactively validates the earlier v4/v5 rejection rather than undermining it:
those failed at **400 mm/s** three times out of three, while baseline completed 400 with
exactly 1716 moves six times out of six. A firmware whose ceiling wanders between 700 and
1000 never once stumbled at 400, so that separation was far outside this noise band.

### Retransmit rate does resolve it, and favours v7 consistently

Mean retx delta (v7 - baseline) over phases both completed in the SAME round. Rounds are
independent; phases within a round are not, so these three numbers are the real sample:

| round | phases paired | mean delta |
|---|---:|---:|
| r1 | 3 | **-0.193 pp** |
| r2 | 5 | **-0.576 pp** |
| r3 | 7 | **-0.396 pp** |
| **across rounds** | | **-0.388 pp** (stdev 0.191) |

All three rounds favour v7, and 11 of 15 paired phases. Invalid bytes per move are
equivalent (0.161 baseline vs 0.170 v7) and **neither firmware stalled once** in 68,000
moves of coordinated four-stepper motion.

### Decision: ship BASELINE; keep v7 as an upstream candidate

The two are functionally equivalent. v7's advantage is real and consistent but has **no
functional consequence**: identical ceiling, zero stalls either way, and Klipper's protocol
absorbs the retransmit difference entirely. It buys ~5% fewer retransmits and no capability.

Against that, baseline is zero divergence from the fork. The asymmetry decides it - shipping
baseline forgoes a benefit that does not translate into machine behaviour, while shipping a
local patch carries a small but non-zero risk of an interaction none of today's tests
covered. Today produced three separate changes that looked good on every test available and
were harmful; that is a strong prior for preferring unmodified code when the measured gain
is this thin.

v7 remains archived and validated (40/40 MSCNT, 6 clean runs at 400 mm/s, 3 full ramps) and
is a clean upstream contribution on its own merits - hoisting a non-const ROM call out of
the hottest loop in the firmware is obviously correct and costs nothing.

**A note on the decision rule.** Before seeing data I committed to "v7 ships only if its
ceiling is >= baseline in all three rounds". v7 fails that on r1 (600 vs 700). The rule was
badly specified - it rests on a metric later measured to have a 400 mm/s spread - and I said
so before the analysis rather than after. The outcome is the same either way, which is the
only reason it is safe to note; had the rule and the evidence disagreed, the rule would have
stood.

---

## Real prints — the synthetic protocol was WRONG about the envelope

Everything above was measured with a harness that issued g-code one API call at a
time. Real prints are streamed by klippy itself from `[virtual_sdcard]`, and the
difference is not cosmetic:

| workload | moves/s | mean segment | wire | retx% | outcome |
|---|---:|---:|---:|---:|---|
| synthetic protocol, 400-1000 mm/s | 9-15 | 43-69 mm | 4-8 kB/s | 5-26 | shutdowns |
| 3DBenchy 0.2 mm quality profile | ~115 | 1.37 mm | 6 kB/s | 0.1 | untouched |
| speed Benchy 500 mm/s, no hops | ~180 | 1.53 mm | 8 kB/s | 0.3-0.7 | untouched |
| **speed Benchy 900-1000 mm/s** | ~330 | 1.53 mm | **16 kB/s** | **6-7** | **completes, 3:30** |

**The synthetic protocol was far HARSHER than a real print, not gentler.** It
commanded 43-69 mm moves at 400-1000 mm/s - 32,000-80,000 steps/s per motor. Real
geometry on a 0.5 mm median segment is accel-limited to a fraction of that. Every
envelope figure derived from the synthetic tests is therefore pessimistic; the
comparisons between firmwares remain valid because they were internally
controlled, but the absolute speeds do not transfer.

### What actually loads this board

Not move count - **sustained velocity**. Three changes to the FILE mattered more
than every motion parameter combined:

1. **Remove z-hops.** Each hop is a Z move on a TR8x8 leadscrew: 8x the steps/mm
   of XY, capped at max_z_velocity 100 / max_z_accel 2000. They fragment fast
   motion into short bursts the board absorbs trivially.
2. **Remove retraction.** Each retract/unretract is an extruder-ratio change, so
   `instantaneous_corner_velocity` caps the junction hard.
3. **Slice fast.** With nothing interrupting, the toolhead sustains velocity, and
   sustained velocity is the only thing this board has ever been sensitive to.

Quality Benchy at SCV 150 / accel 1,000,000: **0.11% retx, zero stalls.** The same
board, on a speed-Benchy profile at 900 mm/s: 6.2%. The file is the variable.

### Motion parameters: SCV is the lever, accel is not

From toolhead.py, `junction_deviation = SCV^2*(sqrt(2)-1)/max_accel` and
`move_jd_v2 = R_jd * junction_deviation * accel` - **accel cancels**:

    v_junction = SCV * sqrt(R_jd * 0.41421)      (independent of acceleration)

Measured on the quality Benchy (identical 8% of file, only settings varying):

| SCV | accel | duration |
|---:|---:|---:|
| 5 | 20k | 175 s |
| 15 | 20k | 150 s |
| 30 | 40k | 135 s |
| 40 | 100k | 130 s |
| 60 | 500k | 130 s |
| 150 | 1,000,000 | 125 s |

Everything worth having arrives by **SCV 30 / accel 50k**. Beyond that the
centripetal term takes over and more SCV buys nothing. Raising accel alone (5k ->
40k) moved a speed ramp by 19% while commanding 5x the feedrate - because accel
cannot touch the junction limit that was binding.

### Result: a rule-legal speed Benchy in 3:45, RELIABLY (see correction below)

SpeedBoatRace-legal profile (0.25 mm layers, 2 walls, 10% infill, 3 top/bottom,
0.5 mm line width, PLA), 91.1 m path, 192 layers, 59,626 moves:

| commanded | result | retx% | elapsed |
|---:|---|---:|---|
| 900 mm/s | complete | 6.23 | **3:30** |
| 950 mm/s | complete | 6.72 | **3:30** |
| 975 mm/s | complete | 6.82 | **3:30** |
| 1000 mm/s | complete | 7.0 | **3:30** |
| 1200 mm/s | **shutdown at 11%** | 13.2 | 0:30 |

Identical 3:30 from 900 to 1000 mm/s: the file is geometry-limited, not
board-limited, across that whole range. Ceiling is between 1000 and 1200 mm/s.

**Two traps caught in this section, both nearly published as fact:**

1. A bisection seeded its upper bound from a DIFFERENT file and was about to
   report the ceiling as 975 mm/s. Three identical 3:30 runs cannot bracket a
   failure 25 mm/s away - the inconsistency is what exposed it.
2. The first 1000 mm/s "shutdown at 85.65%" was caused by that file's
   `travel_speed = 100000`, emitting `F6000000` travels. Attributing it to speed
   would have understated the ceiling by 20% and blamed the board for a slicer
   typo.

**Standing caveat:** every number here means *the electronics kept up*, never
*the machine moved*. The motors do not follow at these speeds - the pulleys spin
but stall under finger pressure. `Timer too close` is an MCU scheduling failure
and is independent of whether the rotor obeys, so the limit transfers to a
capable machine; the print times do not.

### Harness bug worth remembering

On an MCU shutdown Klipper leaves `print_stats` at **`paused`**, not `error`. A
completion loop polling `print_stats` alone reports a dead printer as healthy
indefinitely - it did so here for two minutes, printing `stalls 0` the whole
time. Check klippy's own `info` state instead.

---

## TO INVESTIGATE: CAN bus on the OLED header

**Status: not implemented, worth investigating. Blocked on an external transceiver.**

### What exists

| piece | state |
|---|---|
| Klipper generic CAN layer (`generic/canbus.c` + `canserial.c`, 398 lines) | done, arch-independent |
| Arch contract | only 3 functions: `canhw_send`, `canhw_set_filter`, `canhw_get_status` |
| Reference implementations | rp2040 **95 lines**, stm32 338 lines |
| ESP32 port | **nothing** — no CAN code, no `CANBUS` in Kconfig |
| ESP32 silicon | has TWAI (CAN 2.0B). esp-idf driver present, and the port's own sdkconfig already enables all four TWAI errata fixes |

Estimated effort: ~100-150 line `src/esp32/can.c` modelled on rp2040's, plus Kconfig.

### Hardware: the OLED header is the candidate

The ESP32 gives logic-level TX/RX only; a transceiver is mandatory. The Rodent has
**no CAN transceiver** — its differential interface is RS485, which CANNOT be reused:
CAN arbitration needs dominant/recessive wired-AND behaviour, while RS485 is push-pull
with explicit direction control.

The **OLED header** (I2C: SDA GPIO27, SCL GPIO26, plus 5V and GND) is the best candidate:
two full-capability GPIOs that are otherwise unused under Klipper, already broken out.
- Both 26 and 27 are input/output capable (unlike GPIO34-39, which are input-only and
  therefore unusable for CAN TX).
- ⚠ The header supplies **5V**; use a **3.3V** transceiver (SN65HVD230) to match ESP32
  logic levels, or level-shift. A 5V TJA1051 would need care on the RX line.
- Alternative pins: the RS485 UART (GPIO15/16), unused under Klipper since Klipper has
  no Modbus.

### CAN BRIDGE mode is IMPOSSIBLE on the classic ESP32

`generic/usb_canbus.c` emulates a Linux `gs_usb` adapter — it includes `usb_cdc_ep.h`
and builds USB device descriptors, so bridge mode requires the MCU to BE a USB device.
The classic ESP32 has no native USB peripheral (the Rodent reaches the host via a CH340)
and the port has no `usb*.c`; `SERIAL` is a plain UART, Kconfig noting
`# Todo add CDC when the time comes`. **The board can be a CAN node, never a CAN bridge.**
ESP32-S2/S3 have native USB OTG and could do bridge mode.

### Is it worth it?

For a second *controller* board, no — a host USB port, or a host UART on an SBC host
(Pi/CB1 `serial: /dev/ttyAMA0`, which is a normal Klipper configuration), is simpler and
needs no transceiver, termination or bitrate.

CAN becomes compelling only if this board is used as a cheap **toolhead** MCU, where one
cable carries power and data to the moving hotend. There the 4-driver limit stops
mattering — a toolhead needs one extruder driver, a thermistor and fans.

⚠ Before anyone tries it: this MCU is already at its scheduling limit under four-stepper
load. Adding a CAN peripheral and its interrupts to the same core must be validated with
the four-stepper real-print load test, never a benchmark.


---

## CORRECTION: the 1000 mm/s ceiling was n=1 and is wrong

Repeating it: **1000 mm/s completes 3/8, 950 completes 7/13.** Both take the same 3:30
when they finish, so the faster setting bought nothing and lost the job ~2/3 of the time.

### Staircase, 10 runs per level (1.4A X/Y/Z, 0.6A E, 100k accel)

| speed | SCV 50 | SCV 30 | mean retx | time |
|---:|---|---|---:|---:|
| 700 | 10/10 | - | 4.55% | 3:46 |
| 750 | **10/10** | - | 5.33% | 3:30 |
| **800** | 5/6 FAILED | **10/10** | 5.02% | **3:45** |
| 850 | - | 9/10 FAILED | 5.23% | 3:46 |
| 900 | 3/4 FAILED | - | 6.38% | 3:30 |
| 950 | 7/13 | - | - | 3:30 |
| 1000 | 3/8 | - | - | 3:30 |

**SCV 30 bought back exactly one level** (750 -> 800). Junction-velocity mechanism
confirmed: at the ~17 deg hotspot angle, SCV 50 permits 305 mm/s, SCV 30 permits 183.

**850 failed on run 10 of 10** - a five-run protocol would have certified it.

### 800 mm/s is the answer because it is the HOTEND's limit

0.25 x 0.5 mm = 0.125 mm^2 per mm, so 800 mm/s = **100 mm^3/s** exactly. The board's
reliable maximum lands on the melt-rate ceiling of a high-end hotend, at motion settings
a purpose-built machine could run (SCV 50 is not a real setting; tuned printers run 5-15).
**The controller is not the bottleneck.**

### Ruled out
- **Thermal**: cooldown (25 min, motors off) gave 2/5, identical to the hot batch's 2/5.
  Driver otpw/ot clear after an hour; heatsinks ~40C vs ~120C threshold; host idle.
- **Motor current**: 0.8A -> 1.4A changed nothing, as predicted (Timer too close is MCU
  scheduling, independent of torque). Motors 65-75C, drivers 40C, supply 1.1A at 36V under
  load / 0.73A holding (=> ~1.9 ohm per phase; PSU current is NOT motor current, the
  TMC2160 is a switching regulator).

### Failures cluster by GEOMETRY, not time
Positions across 16 runs: 4.7, 9.5, 10.0, 10.0, 10.8, 12.2, 20.9, 23.4, 25.9, 26.9,
86.0, 87.2, 89.3%. Buckets where failures occurred: mean junction angle 24.7 deg, mean
segment **1.92 mm**; all other buckets 29.8 deg / 1.41 mm. Failures land where segments
are 36% longer and corners shallower - i.e. where the toolhead can sustain velocity.
