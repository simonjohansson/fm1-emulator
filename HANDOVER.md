# Handover: booting stock FM-1 firmware

Goal: `./emulator ~/Downloads/FM-1.fwsc` boots the stock firmware to its home
screen. Felucca (`~/Downloads/felucca-1.1.5.1-app.bin`) must keep working.

## Where it stands (commit 8c9fdae, 2026-10-09)

- Launches create both cores (`-smp 2`). Stock reaches ~160M core-0
  instructions (3.30 s guest time): audio DMA runs, watchdog is fed, and
  LCD initialization and its initial clear complete. HOME is not drawn yet.
- Current stop: **"UART1 transmission is unimplemented" at PC 0x02029a20**.
  Stock writes TXADR `0x12114 = 0x01c0f970`, then TXCNT `0x12118 = 3`.
  UART1 completion timing still needs hardware measurement before modeling.
- Completed discrete commits: `6d3aa58` LCD SRAM/XIP DMA, stock ST7789
  commands, 240x320 controller RAM and SPI1 IRQ16; `1e18fd5` FF4B signed
  register branch; `e61487e` measured USB_IO_CON1 sensing; `8c9fdae` byte
  post-decrement store, including the stock D646/079B parallel bundle.
- LCD CASET/RASET endpoints are inclusive; out-of-GRAM pixels are ignored
  per the ST7789V datasheet. Stock's temporary rows 40..279 are overwritten
  by its clear, so no visible-origin offset is inferred from that window.
- `.deps/firmware-trial/stock-home.png` shows what the home screen should look
  like (from the retired emulator, which reached it).
- Current evidence is in `.deps/qemu-stock-home-2026-10-09/`: test logs,
  stock stop, 50-second Felucca state/SRAM/LCD, vendor FF4B disassembly,
  fresh USB pad read, installed diagnostic readback and restoration package.

## Workflow

- Build `mise run build`; tests `mise run test` (182 total, 1 skipped).
  Codex's sandbox blocks the local QMP sockets used by input/stress tests;
  the complete suite passed with those sandbox restrictions lifted.
- Stock probe: `scratchpad/stock.sh` in
  `/private/tmp/claude-501/-Users-simonjohansson-src-fm1-emulator/8c4c9482-9375-4909-ad77-ee724b8a717e/`
  (two cores, writes `stock/state.json`; `INSNS=4000000000` for longer runs).
  state.json now includes `peer` (the other core's PC etc.).
- Felucca A/B before each commit: `./ab.sh NAME` in the older scratchpad
  `/private/tmp/claude-501/-Users-simonjohansson-src-fm1-emulator/3be97cb6-3bea-4413-bd4c-6d7c7799bcab/scratchpad`.
  Expect only `alnk clock_control 0->6` and `sram identical: True`.
  This wrapper clears TMPDIR and can fail QEMU shared-memory allocation
  under Codex. Run its QEMU command directly while retaining TMPDIR, then
  compare with its existing baseline. Each new commit passed 50 guest
  seconds; SRAM stayed identical and LCD output matched the preceding build.
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
- Build with the env from `build-felucca.sh` but `cd` to the worktree; install
  `felenv/bin/python tools/fm1_install.py build/felucca.fwsc --yes`; commands
  via `hw_cmd.py`. Device currently runs this probe build; no restore needed.
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
  **Not flashed: explicit permission to flash, measure and restore is pending.**
  Do not treat preparation or prior-session flashing permission as approval.
- Primary vendor UART evidence: TX pending CON0 bit15, TX IRQ enable bit2,
  ACK bit13; source20. CLK_CON1 bits11:10 select OSC/PLL48M/LSB; CON0 bit4
  chooses /4 or /3 and BAUD uses the register plus one. Do not assume that
  DMA pending occurs at the final stop bit without the prepared measurement.

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
