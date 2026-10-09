# Handover: booting stock FM-1 firmware

Goal: `./emulator ~/Downloads/FM-1.fwsc` boots the stock firmware to its home
screen. Felucca (`~/Downloads/felucca-1.1.5.1-app.bin`) must keep working.

## Where it stands (commit 3c0b724, 2026-10-09)

- Launches create both cores (`-smp 2`). Stock draws HOME and runs to a
  deliberate 4-billion-instruction headless limit (38.79 s guest time),
  with audio DMA, watchdog feeds and continuing LCD updates.
- Normal Cocoa/CoreAudio launch originally exposed TIMER4 IRQ62 at PC
  `0x0205b8dc`, after 1,529,927,489 instructions. `3c0b724` adds its route
  through the existing timer model. A repeat normal launch ran for two wall
  minutes without that fault. The same native display/audio configuration
  with evidence capture ran to a deliberate 6-billion-instruction limit
  (54.13 s guest time), still showing HOME, with 231868 watchdog feeds.
  No unsupported-operation fault occurred. Live pacing reports timing lag;
  real-time speed and audio fidelity are not established by this check.
- The native capture was inspected from the guest LCD output. Desktop
  automation could not attach to this unbundled executable, so the macOS
  window itself was not visually inspected through that tool.
- Completed discrete commits: `6d3aa58` LCD SRAM/XIP DMA, stock ST7789
  commands, 240x320 controller RAM and SPI1 IRQ16; `1e18fd5` FF4B signed
  register branch; `e61487e` measured USB_IO_CON1 sensing; `8c9fdae` byte
  post-decrement store, including the stock D646/079B parallel bundle.
- Further commits: `98d08b5` measured UART1 DMA/IRQ20; `5c5faf6` unscaled
  register-sum halfword store; `418f96a` signed register LE branch;
  `9d1adf0` byte load with r15 stride; `c91c47b` signed register GT branch;
  `f3e5b0d` signed register LT conditional arms; `4306cee` byte load with
  r13 stride; `bf08bc3` measured USB-mode DMOUT latch; `d3b0d5e` measured
  USB pull-up/GPIO mode flags; `3c0b724` TIMER4 IRQ62 delivery, including
  stock's core-1 configuration. Timer clocks, controls, periodic deadlines
  and ACK phase remain unchanged.
- LCD CASET/RASET endpoints are inclusive; out-of-GRAM pixels are ignored
  per the ST7789V datasheet. Stock's temporary rows 40..279 are overwritten
  by its clear, so no visible-origin offset is inferred from that window.
- `.deps/qemu-stock-home-2026-10-09/stock-home/lcd.png` is the current QEMU
  HOME capture. `.deps/firmware-trial/stock-home.png` is an older reference
  from the retired emulator, with a transient Volume overlay.
- Current evidence is in `.deps/qemu-stock-home-2026-10-09/`: `stock-native/`
  contains the final native state/SRAM/LCD/ALNK and launch output;
  `stock-headless/` contains the preceding 38.79-second run; `timer4/`
  contains final build/test logs; `felucca-final/` contains the exact
  50-second comparison. Vendor disassembly, physical UART/USB captures and
  original diagnostic restoration/readback are also retained there.

## Workflow

- Build `mise run build`; tests `mise run test` (221 total, 1 skipped).
  Codex's sandbox blocks the local QMP sockets used by input/stress tests;
  the complete suite passed with those sandbox restrictions lifted.
- Stock probe: `scratchpad/stock.sh` in
  `/private/tmp/claude-501/-Users-simonjohansson-src-fm1-emulator/8c4c9482-9375-4909-ad77-ee724b8a717e/`
  (two cores, writes `stock/state.json`; `INSNS=4000000000` for longer runs).
  state.json now includes `peer` (the other core's PC etc.).
- Felucca A/B before each commit: `./ab.sh NAME` in the older scratchpad
  `/private/tmp/claude-501/-Users-simonjohansson-src-fm1-emulator/3be97cb6-3bea-4413-bd4c-6d7c7799bcab/scratchpad`.
  The oldest baseline differs in `alnk clock_control 0->6`.
  This wrapper clears TMPDIR and can fail QEMU shared-memory allocation
  under Codex. Run its QEMU command directly while retaining TMPDIR, then
  compare with the preceding capture. Each new commit passed 50 guest
  seconds; full state JSON, SRAM, LCD and captured ALNK bytes matched the
  preceding build exactly after the earlier clock-control correction.
- Normal launch uses `shift=3,align=on,sleep=on`, Cocoa and CoreAudio.
  Exact headless comparisons use `shift=3,align=off,sleep=off`. Live pacing
  warnings are not throughput measurements.
- Stock disassembly: that scratchpad's `stockapp/app.dis` (base 0x2000120).
  It is out of step around FPU code; `scratchpad/hw.py ADDR N` (newer
  scratchpad) prints raw halfwords. RAM code: dump `stock/state.sram`, run
  `jl-dis.sh ram.bin 0x1c00000 out.dis`.
- Discovery trick (never commit): make `transaction_failed` in
  `src/target/pi32v2/cpu.c` log and return to list unmapped regions in one run.
- Vendor assembler gives encodings: clang `-target pi32v2 -mfprev1` in Docker
  (see `scratchpad/fa/run2.sh`); objdump cannot decode FPU ops.

## Hardware probing

- Diagnostic firmware worktree: `/private/tmp/fm1-core1-probe` (Felucca-based,
  GPL-3, kept outside this repo). Probes: `pclp`, `softp`, `lrcp`, `adcp`,
  `tmrp`, `uidp`, `regr ADDR N`. Evidence in its `evidence/` (v14-v22).
- Build with the env from `build-felucca.sh` but `cd` to the worktree; use
  mise-selected Python with the existing MIDI dependencies for
  `tools/fm1_install.py build/felucca.fwsc --yes`; commands via `hw_cmd.py`.
  Device currently runs the original probe build again after restoration.
- A read-only full XIP readback on 2026-10-09 verified that the installed
  diagnostic exactly matches `/private/tmp/fm1-core1-probe/build/felucca.bin`:
  SHA-256 `48c2539e389691e271b8d03951549066800aceed307120d573114c1e99478212`.
  Its saved restoration package is
  `.deps/qemu-stock-home-2026-10-09/original-diagnostic.fwsc`, SHA-256
  `8c0f315395d9c95b16aec4db734ecf6da8738d063394fef1fe33bc64a7d1fd27`.
- UART TX probe is built separately at `/private/tmp/fm1-uart-tx-probe`.
  `evidence/README.md` and `evidence/uart-tx-probe.diff` describe `uarttp`:
  counts 1/3/8, PLL48M, BAUD383, /4 and /3 divisors, TIMER4 completion ticks,
  TPND persistence, IRQ20 and ACK. Loader remains byte-identical. Final
  package SHA-256:
  `3e0667730ac28edb09e1f4a9939860b0ce8cc1ddf6037eac01f8e2c68491729a`.
  The user authorized flashing on 2026-10-09. This probe and the separate
  BAUD95/191/383/767 sweep at `/private/tmp/fm1-uart-baud-probe` were flashed
  and measured. Both loaders matched the original byte-for-byte.
  The original diagnostic was restored; a full 454084-byte XIP readback
  matched the original application hash above. Capture and restoration
  evidence is in `.deps/qemu-stock-home-2026-10-09/uart-tx/`.
- Primary vendor UART evidence: TX pending CON0 bit15, TX IRQ enable bit2,
  ACK bit13; source20. CLK_CON1 bits11:10 select OSC/PLL48M/LSB; CON0 bit4
  chooses /4 or /3 and BAUD uses the register plus one. The measured DMA
  completion period is empirically `((10*divisor+1)*(BAUD+1)+6)` PLL48M
  ticks per byte; nominal ten-bit frame duration alone does not fit.
  The model omits the probe's 3..7 OSC24M ticks of launch/poll latency.
  Qualified TX modes: PLL48M, CON1 zero; /4 BAUD95/191/383/767, /3 BAUD383.
  Completed TXCNT reads zero. TPND and IRQ20 remain set until ACK13;
  active count reads, aborts, restarts and reconfiguration remain faults.
- Separate USB probes measured DMOUT and the exact stock mode sequence
  `6636 -> 6f36 -> 6f3e -> 663e -> 164c` twice. SDK names: DMOUT bit1,
  IO_PU_MODE bit8, IO_MODE bit11; input direction is bit3 for DM. The model
  stores these measured flags. Live CON1 samples varied with attached USB
  traffic, so the prior attached-idle snapshot 2 remains; no general GPIO
  electrical waveform or sensing dependency is inferred. DPOUT bit0 is
  still rejected. Captures are in `usb-output/` and `usb-mode/` beneath
  `.deps/qemu-stock-home-2026-10-09/`.
- After the final USB probe, the original diagnostic was restored. Full
  454084-byte XIP readback took 38.61 s and matched SHA-256
  `48c2539e389691e271b8d03951549066800aceed307120d573114c1e99478212`
  byte-for-byte. Evidence: `usb-mode/restore.txt`, `restore-readback.txt`
  and `restored-diagnostic.bin`. No transient probe remains installed.
- TIMER4 IRQ62 routing is primary-source qualified by SDK `hwi.h:108`,
  `timer-test1.c` and stock setup/ISR (`timer4-vendor.dis`). Existing `tmrp`
  measured timer clocks with TIMER4 as a reference; it did not physically
  validate TIMER4 interrupt delivery or acknowledgment phase.

## Assumptions (not measured), flagged in code

- Equal IRQ priority: lower source number first.
- P33 write bursts auto-increment the address (from stock's own sequence).
- Inert radio: WLA_CON30 calibration returns code 0x80; RF timer reads
  virtual microseconds.
- SPI2 CON 0x2000 is the IRQ enable.
- RNG is a fixed-seed xorshift (determinism).

## Rules from the user

- Tim Pope commit messages with the prompt, no emojis, Co-Authored-By line;
  commit on main in discrete steps; never `git -C`.
- Tests only where meaningful. Measure on hardware when behavior is unknown;
  otherwise fault explicitly rather than guess.
- The retired GPL-3 Rust emulator (git history) is evidence only; never copy
  its code. Probe captures in `.deps/firmware-trial/*-probe` are usable.
