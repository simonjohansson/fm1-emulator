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

## Fixed: stock UI froze after preset changes (f1239a6)

Lost FreeRTOS wakeup: core 0's switch IRQ (soft 127, level 0) was
overtaken by TIMER1/ALNK (levels 1/3) that piled up while round-robin ran
core 1's slice inside core 0's critical section. The rr loop now keeps a
vCPU (up to 16 passes x 1024 instructions, timers still running) while its
IRQs are masked or its own software IRQ is pending
(tcg_rr_hold_slice, src/target/pi32v2/cpu.c hold_slice).
FM1_POC_TRACE_PCS=HEX,... prints registers at chosen PCs (how it was found).

Hardware facts measured this session (probe firmware console):
prip (IRQ priority: higher level first, ties lower source first,
higher level nests into a lower handler - nesting NOT modelled yet),
spi0p (SPI0 0.69 us/byte, as modelled), sdivp/smacp (signed 64-bit divide
and MAC, now implemented), trigp, denp (subnormals), ifp (IFF blocks).

## Not yet done (user requests)

- Done: `tools/panel_smoke.py FIRMWARE` passes on all three firmwares
  (HOME pressed from HOME shows no change; harmless).
- Done: `tests/stress.py FIRMWARE` scripted random buttons+encoders,
  60 guest s, 5 ms; passes on all three (seeds 1-3).
- Next: run more stress seeds; model IRQ nesting (measured); check
  SAVE/SEQ/SEL/REC behaviour; interleaving still not fully deterministic.
- Unverified: SAVE, SEQ, SEL, PLAY/STOP, REC contacts; stock with no USB
  host (no serial backend) plays no notes; stock audio level is low.
