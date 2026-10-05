# Booting the small FM-1 diagnostics in QEMU

This experiment runs unchanged guest binaries using a native pi32v2 QEMU
target. The maintained source is `overlay/`; the downloaded QEMU tree and
build products in `.cache/` are disposable.

Worktree: `/private/tmp/fm1-qemu-poc`, branch `codex/qemu-poc`.

## Foundation: full application startup

The existing 592-byte `build/foundation/firmware.bin` starts at `0x02000120`
and reaches its guest completion loop at `0x020002ba`. It is no longer entered
partway through its timer test. SRAM starts filled with `0xa5`, so the guest
must perform its own data copying and BSS clearing.

Run from the worktree root:

```sh
mise exec python@3.13.15 -- python qemu-poc/build.py
mise exec python@3.13.15 -- python qemu-poc/validate_boot.py
```

The validator includes the original probe, timer subsection, explicit-fault
and QMP/TCG checks. All new run records go to `.cache/boot-validation/`,
leaving the original tracked validation records unchanged.

To run the full foundation directly:

```sh
qemu-poc/.cache/build/qemu-system-pi32v2 \
  -M fm1-poc -accel tcg,thread=single \
  -icount shift=3,align=off,sleep=off \
  -display none -serial none -monitor none -nodefaults \
  -kernel build/foundation/firmware.bin -append foundation
```

`foundation` holds matrix input column 0, row 4. Use `foundation-released`
for a separate boot with every key released. Input is supplied through the
modeled GPIO/shift-register wiring; guest RAM results are never synthesized.

### Verified results

Both input states complete after 14,773 guest instructions. Validation checks:

- Guest data copy produces `c001cafe`, and poisoned BSS becomes zero.
- Guest startup copies the RAM function; TCG translates its RAM addresses,
  and the function writes `5a17` through a register-indirect call.
- All twelve embedded instruction-probe results match the saved hardware
  observations and the separate Rust reference process.
- All eleven matrix samples match the reference. Pressing column 0, row 4
  changes the first sample from `1e1` to `e1`.
- The guest generates 176 shift edges and eleven latch edges.
- TIMER4 advances. QEMU samples 0 then 4; Rust samples 0 then 3 under its
  different functional instruction clock.
- TIMER5 expires once, enters the guest vector and handler, is acknowledged,
  and returns through `rti` with no remaining pending interrupt.
- Application SP is restored to `01c7a000` and SSP to `01c7c000`.
- The guest writes its final completion marker `0050f00d`.

The unchanged input SHA-256 is
`d22ba9de32a8ba3cfee7aec804468d7dea2db8a7c83d0c0707f996cbcfff8ac7`.
The validator enforces the binary and disassembly hashes before running.

## Bare display: startup, three frames, key press and release

The unchanged 2,700-byte `tests/fixtures/display/firmware.bin` includes the
foundation startup and then executes its display renderer. It starts at
`0x02000120`; each completed frame is observed at `0x020004fa`.

Run directly from the worktree root after building:

```sh
mkdir -p qemu-poc/.cache/display-frames
FM1_POC_FRAME_DIR="$PWD/qemu-poc/.cache/display-frames" \
  qemu-poc/.cache/build/qemu-system-pi32v2 \
  -M fm1-poc -accel tcg,thread=single \
  -icount shift=3,align=off,sleep=off \
  -display none -serial none -monitor none -nodefaults \
  -kernel tests/fixtures/display/firmware.bin -append display
```

The process writes `frame-1.ppm` through `frame-3.ppm`, per-frame JSON records
and a final JSON summary. The output directory must already exist. The
checkpoint harness starts with released keys, presses column 0/row 4 after
the first frame, releases it after the second, and stops at the third complete
frame. Between checkpoints the guest executes its real loop, reads the GPIO
matrix and timer, composes pixels in SRAM and sends them through SPI/DMA.
The harness does not write guest result memory or draw the screen.

On macOS, the captured image can be converted for preview without additional
dependencies:

```sh
sips -s format png qemu-poc/.cache/display-frames/frame-2.ppm \
  --out qemu-poc/.cache/display-frames/frame-2.png
```

The observed run completes three frames after 15,581,968 guest instructions,
with 91,232 pixel writes and 2,723 DMA transfers. The actual framebuffer shows
the FM1 title, timer and key grid. The selected key tile changes from gray to
yellow and back; the timer readout advances. The foundation completion marker
and its completed startup interrupt remain present.

The panel consumes the guest's command/data stream, including RGB565 pixels
and address windows. SPI completion uses QEMU virtual timers at a fixed
functional 12 MHz for this fixture's configured mode. The initial background
alone takes about 76.8 ms of virtual transfer time. Sources are read from guest
SRAM at DMA completion; this fixture waits before modifying them. This is not
a general streaming DMA or calibrated peripheral-clock model.

The unchanged input SHA-256 is
`3bc59ff7d09de123174b49ea786582a1e137b2e1609074b5189c9fb024f19ba2`.

### Display validation

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_display.py
```

The validator runs all three input states in QEMU and a separate Rust process.
All pixels outside the timer-digit band (rows 80 through 109) match exactly,
including the title, background and all 44 key tiles. Matrix RAM, all general
registers and special registers 1 through 15 match at each frame. RETI is
checked as a valid polling-loop return address because timer delivery can land
on a different instruction under the two functional clocks. Frame counters
advance and the pressed tile returns to its original color on release.
Records are in `.cache/display-validation/` and images in
`.cache/display-frames/`.

QEMU executes 15,581,968 guest instructions for the three frames; the Rust
reference executes 176,292. This is not a speed comparison: QEMU's modeled
SPI duration causes substantially more guest polling. No claim of equivalent
peripheral timing or superior host throughput follows from these counts.

## Scope and next target

These are application-entry diagnostics, not a ROM/SPL or encrypted package
boot. Foundation and the bare display fixture explicitly target emulator
integration and must not be flashed as updates. No connected hardware was
accessed or flashed.

Virtual time uses QEMU icount at eight nanoseconds per supported guest
instruction. It is functional timing, not a hardware cycle model. Different
polling counts and timer samples from the Rust emulator are expected; the
validator compares the behavior relevant to each fixture rather than claiming
identical clocks or a performance advantage.

Only the implemented CPU and peripheral subset is supported. Unsupported
instructions and device operations fail explicitly. Full Felucca, stock
firmware, USB, audio, dual-core execution and arbitrary firmware loading remain
outside the verified boot paths. The Rust emulator remains the operational
implementation and a separate behavioral reference; it is not linked into
QEMU or called to execute guest instructions.

The next larger target is the existing `build/fm1-diag.bin` FM-1_980 diagnostic
(14,804 bytes), which was installed on hardware earlier in development. Its
real startup needs more instruction forms plus P33/watchdog, protection/IRQ
configuration, NOR/SFC and USB behavior. Booting it is not established by the
two smaller fixtures. It is a more bounded next step than full Felucca.

See [LICENSES.md](LICENSES.md) for implementation provenance and
[README.md](README.md) for the pinned build and original prototype details.

## Commits and preserved evidence

The milestones are separate signed commits:

- `33b8b9a`: preserve the existing instruction/timer proof of concept.
- `e095683`: boot the complete unchanged foundation diagnostic.
- `2847f0b`: boot the bare display diagnostic and validate three SPI/DMA frames.

All three signatures were verified. The original tracked fixture binaries and
recorded results remain unchanged. Current validation records and frame images
also have a durable ignored copy outside the temporary worktree:

`/Users/simonjohansson/src/fm1-emulator/.deps/qemu-boot-2026-10-05/`
