# Handover

Goal: `./emulator FIRMWARE.fwsc` runs stock FM-1 firmware (`~/Downloads/FM-1.fwsc`,
`~/Downloads/FM-1_093.fwsc`) and Felucca (`~/Downloads/felucca-1.1.5.1.fwsc`)
generally: panel input, presets, sound, real time. The user wants the
emulator general-purpose, not tuned to one image; commit in discrete steps
(Tim Pope style, include the prompt, no emoji); the attached FM-1 may be
flashed freely (no restore needed).

## Tools

- `tools/panel_smoke.py FIRMWARE [--presets N] [--keep]`: one unpaced run
  stepping PRESETS through N presets with a note each, then every button
  six times from HOME, then a note. Checks LCD/audio/MIDI per step via
  `FM1_POC_SNAPSHOT_NS`; failing screens in `.cache/panel-smoke/`.
- `tests/test_stock_scenarios.py` (opt-in, `FM1_STOCK_PACKAGES=a.fwsc:b.fwsc`).
- `FM1_POC_INPUT="NS:qcode:1,NS:qcode:0,..."` scripted keys at guest times;
  `FM1_POC_CAPTURE_NS` ends a run with state.json/lcd.ppm/state.sram;
  `FM1_POC_UART1_LOG` captures stock's MIDI out.
- `tools/decode_sweep.py` (needs Docker + JIELI_TOOLCHAIN): rejected
  instruction forms per image. FM-1 and FM-1_093 have none statically
  reachable (last run 2026-10-10).
- Hardware probe: Felucca checkout with diagnostics at
  `/private/tmp/fm1-core1-probe` (console commands spi2p, ifp, trigp, denp,
  flr, memr...). Build/flash commands: see git log of this session or
  `evidence/build-v26.log`/`install-v26.log` there; console via
  `hw_cmd.py` in the older scratchpad. The FM-1 currently runs that probe.

## State (2026-10-10)

Works on FM-1 and FM-1_093: boot to HOME, panel scan (SPI2, stretched 10x
because the 8 ns model starved the scan ISR), ENV/LFO/FX/EDIT/GLO/ARP pages,
PRESETS/ALGORITHM (labels swapped vs Felucca's guess, verified from stock),
KNOB 1-4, notes with audio and MIDI, OCT+-. Subnormal floats, IFF blocks,
TRIGGER and the 093 resource key were measured or derived generally.

Timing model: 8 ns/instruction with one runnable vCPU, 4 ns with two
(`icount_set_parallel` hook in tools/integrate.py). Round-robin switch
points no longer follow host kicks (mostly reproducible). Idle loops that
call and store are fast-forwarded (helper_pi32v2_idle_loop, TB flag
PI32V2_TB_WATCH); stock runs ~4x real time headless.

## Open problem being debugged

Stock UI task dies after repeated preset changes: LCD commands and MIDI
stop, buttons do nothing, audio keeps rendering. Deterministic per
configuration:

- `tools/panel_smoke.py ~/Downloads/FM-1.fwsc --presets 40`
- both parallel time and idle fast-forward on: dies around preset 5-6;
- parallel off (`FM1_DBG_NO_PAR=1`, uncommitted getenv in the
  icount_set_parallel hook): dies at preset 34 (DS GUITAR1);
- parallel and idle fast-forward off: 40 presets fine, dies later.
- Uncommitted experiment: idle fast-forward stops two iterations before the
  slice end (so stock's TIMER5 reset really happens); then dies at preset 17.

So the bug is timing/interleaving sensitive, probably a cross-core race in
the model (LOCKSET/TESTSET, software IRQs 120-127, IRQ masking) or a
timing assumption, not the preset data. Core 0 ends up idle with only ISRs
running (tick ISR at 0x0205b6d8, TIMER1 ISR walking the RTOS timer list at
0x02002254); core 1 renders. Next: find which task blocks and on what.

## Not yet done (user requests)

- Make `tools/panel_smoke.py` pass for all three firmwares.
- Extend the random button stress script (docs/development.md, captures in
  .cache/tests/stress/) to turn encoders too, 60 s per run, ~5 ms between
  actions, firmware passed as an argument.
- Unverified: SAVE, SEQ, SEL, PLAY/STOP, REC contacts; stock with no USB
  host (no serial backend) plays no notes; stock audio level is low.
