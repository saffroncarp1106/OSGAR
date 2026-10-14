# OSGAR HID — build and measurement guide

An Arduino Pro Micro types into the game as a USB keyboard and watches the screen
with a phototransistor, timestamping both on its own clock. That gives an
end-to-end input latency measurement with microsecond resolution and no camera,
no frame counting and no human in the loop.

This document is half wiring and half experimental method. The wiring is the easy
part.

---

## 1. What this measures, and what it does not

Every number the bench produces is **press-queued → screen-changed**. That
interval contains, in order:

1. USB polling phase (≤1 ms, or a consistent offset with SOF locking on)
2. the OS input stack
3. **the game** ← the only part under test
4. the display's own response and its scanout position

Three of those four are not the game. They are also **identical between two
clients**, provided nothing about the rig changes between runs. That is what makes
a *comparison* trustworthy and a single absolute number close to meaningless.

> Report the difference between two conditions. Do not report "osu! has X ms of
> latency" — most of X is your monitor.

---

## 2. Bill of materials

| Qty | Part | Notes |
|----:|------|-------|
| 1 | Arduino Pro Micro (ATmega32U4) | 3.3 V/8 MHz or 5 V/16 MHz, either is fine |
| 1 | Phototransistor | TEPT4400, or any NPN type. A photodiode + op-amp is better but not needed |
| 1 | 10 kΩ resistor | external pull-up, sharper edge than the internal one |
| 1 | LED + 470 Ω | marker for the camera cross-check (optional) |
| 1 | momentary push button | play / panic stop (optional) |
| — | opaque tube or tape | to shade the sensor — **not optional**, see §4 |

No MOSFETs, no 12 V supply, no flyback diodes. The board is powered from USB.

---

## 3. Pin assignments

| Pin | Goes to | Notes |
|-----|---------|-------|
| **D3** | phototransistor collector | INT0. External 10 kΩ to VCC |
| **D9** | marker LED | via 470 Ω, pulsed with each bench press |
| **D7** | printer sync in (optional) | INT6, only if driving a printer too |
| **D4** | play button → GND | internal pull-up |
| **D5** | status LED (optional) | via 470 Ω |
| **GND** | phototransistor emitter, LED cathodes | |

---

## 4. The light sensor

```
            VCC (3.3 V or 5 V)
              │
             ┌┴┐
             │ │ 10 kΩ
             └┬┘
              ├──────────────► D3
              │
         ────┴──── collector
         ▼▼  phototransistor
         ────┬──── emitter
              │
             GND
```

Brighter screen → the phototransistor conducts → D3 falls. The firmware triggers
on **any** edge, so the polarity does not matter.

**Shade it.** This is the step people skip and then get nonsense. Ambient room
light will saturate the phototransistor so it never sees the screen change at all.
Put it in a short opaque tube — a bit of heatshrink or a black straw — and press
that against the monitor so only the screen's light reaches it. Tape it down.

**Aim it at something that actually changes on the keypress.** Options, worst to
best:

- The key overlay highlight. Deterministic and always in the same place, but
  stable and lazer may draw it at different points in their pipelines — which is a
  confound, not a measurement.
- The hit burst on a note. Closest to what a player perceives.
- A deliberately maximal target: a dark background skin with a large bright hit
  effect, so the transition is unmistakable.

Measuring **both** the overlay and the hit burst and reporting them separately is
worth the extra run. If they disagree between clients, that difference is itself
the interesting result.

If the sensor triggers unreliably, the screen transition is too subtle for a bare
phototransistor. The fix is a comparator (LM393) with a trim pot to set a
threshold, not more gain on the phototransistor.

---

## 5. Uploading

Arduino IDE, `firmware/osgar_hidbot/osgar_hidbot.ino`:

- Board: **SparkFun Pro Micro** (3.3 V/8 MHz or 16 MHz), or **Arduino Leonardo**
- Needs the **Keyboard** library (`arduino-cli lib install Keyboard`)

```bash
arduino-cli compile --fqbn SparkFun:avr:promicro:cpu=8MHzatmega32U4 firmware/osgar_hidbot
```

Once flashed the board is a keyboard **and** a serial port at the same time, so
OSGAR can keep talking to it while it types.

### On the polling rate

Arduino AVR core 1.8.8 declares the HID endpoint with `bInterval = 1`, i.e. 1 ms /
1000 Hz. Nothing needs patching. Worth confirming once with a polling-rate checker
after flashing — if a future core regresses this to 8 ms, every measurement gains
8 ms of quantisation and the bench silently becomes worse than a phone camera.

---

## 6. Bring-up

1. **Connect.** OSGAR's *HID Key Bot* panel → Connect. It reports the protocol
   version and key count.
2. **Check the binding.** Click into a text editor, then *Tap K1*. A `z` should
   appear. Presses go to whatever window has focus — there is no targeting.
3. **Check the sensor.** *Check sensor* reads the phototransistor. Cover and
   uncover it; the value must change. If it does not, it is not wired, not
   powered, or drowned in ambient light.
4. **Aim it.** Park it over the target region with the game running and confirm it
   still changes when a note is hit.
5. **A short run.** 50 trials. Look at the distribution before committing to 2000.

---

## 7. Running a comparison that will survive scrutiny

This is the part that decides whether the result is worth publishing.

**Change exactly one thing.** Between the two runs, change the game client and
nothing else. Not resolution, not refresh rate, not vsync, not the frame limiter,
not the window mode, not the sensor position, not the skin, not the monitor's
picture mode. Anything else that moves invalidates the comparison, because you can
no longer attribute the difference.

**Interleave the runs.** Do not run 2000 stable then 2000 lazer. Machine state
drifts — thermals, background tasks, driver state — and a single A-then-B ordering
cannot tell drift apart from a real difference. Run A-B-A-B in blocks of a few
hundred. If the two A blocks disagree with each other by as much as A disagrees
with B, you have measured your computer's mood, not the client.

**Use enough trials.** The panel reports the standard error of the mean. A
difference smaller than about two standard errors is not a finding. With a typical
3 ms spread, ~400 trials puts the SEM near 0.15 ms, so differences above roughly
0.5 ms become resolvable — which is well inside the range being argued about.

**Sweep the settings that are actually in dispute.** ppy's own
[latency and unlimited frame rates](https://github.com/ppy/osu/wiki/Latency-and-unlimited-frame-rates)
page documents lazer's frame pacing, and the community
[discussion thread](https://github.com/ppy/osu/discussions/26971) suggests results
swing hard on frame-limiter mode. One number per client is a much weaker result
than a curve across frame-limiter settings for both.

**Report the distribution, not the mean.** Input latency is not symmetric: it has
a floor set by the pipeline and a tail set by frame pacing. Two clients can share
a mean and feel completely different because one has a fat tail. The panel draws a
histogram; publish it.

**State your rig.** Monitor model, refresh rate, resolution, GPU, driver version,
OS, in-game settings, and where on the screen the sensor sat. Without those, the
absolute numbers cannot be compared with anyone else's.

### What to publish

- n, mean, median, standard deviation, p95, p99, for each condition
- the difference and its standard error
- the histograms
- the raw CSVs — *Save CSV...* keeps every trial, so anyone can redo the analysis
- the miss count, and what you did about it

---

## 8. The camera cross-check

The marker LED on D9 pulses in step with each press. Film the LED and the monitor
in one frame at high speed and you can measure the same interval the old way, on
the same events. If the electronic and video methods agree, the electronic result
is credible to an audience that only trusts the video method.

Worth doing once, on one configuration, and showing alongside the main result.

---

## 9. SOF locking

The host polls the keyboard endpoint once per 1 ms USB frame. Queue a report at an
arbitrary moment and it waits somewhere between 0 and 1 ms — uniform noise on
every trial.

With `soflock` on (the default), the firmware waits for a Start-of-Frame before
queuing, so the report always goes out at the same point in the frame. The delay
does not disappear; it stops varying. Since it is then identical in both runs, it
cancels in the difference.

Turning it off is a legitimate experiment: the two runs should differ by about
0.5 ms in mean and visibly in spread. If they do not, SOF locking is not doing
what it claims on your machine and the results deserve a second look.

---

## 10. Replay playback

The panel can also drive presses from a real `.osr`, in step with the printer
drawing the cursor path — the whole OSGAR pipeline, with HID instead of solenoids.

Useful for realism, but **not** a precision timing source: osu! records replay
frames at roughly 16 ms and writes many presses with zero duration (press and
release on two frames sharing a timestamp — 1689 of them in one test replay).
Those get widened to 1 ms because no input system can send a zero-length press.

Use randomised synthetic taps for measurement, and replay playback for
demonstrating the rig under realistic load.

---

## 11. Troubleshooting

| Symptom | Cause |
|---|---|
| Board found but "running the SOLENOID firmware" | Upload `firmware/osgar_hidbot` |
| Tap types nothing | Wrong window focused, or a usage ID outside 0x04–0x77 / 0xE0–0xE7 |
| Sensor value never changes | Not shaded — ambient light saturating it; or unwired |
| Every trial a miss | Sensor not aimed at anything that changes on a press |
| Trials under 1 ms, discarded | Sensor seeing the marker LED, or a flickering backlight |
| Huge spread (sd > 8 ms) | Vsync or a frame limiter, or watching an animation that does not start on the press |
| Occasional 200 ms+ outliers | A stutter, not latency. Reported separately — worth investigating on its own |
| Two runs of the same client disagree | Machine state drifting. Interleave the blocks (§7) |
