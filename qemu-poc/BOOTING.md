# Booting FM-1 diagnostics in QEMU

This experiment runs unchanged guest binaries using a native pi32v2 QEMU
target. The maintained source is `overlay/`; the downloaded QEMU tree and
build products in `.cache/` are disposable.

Worktree: `/Users/simonjohansson/src/fm1-qemu-poc`, branch `codex/qemu-poc`.

## QEMU 11.1.2 upgrade

The active build is pinned to official QEMU 11.1.2, release commit
`4fc49f46dc95d4a27de2509e7fceb2931e91faeb`. The archive SHA-256 and detached
signature were verified; [README.md](README.md) and `build.py` contain the pin.
A fresh build passed with warnings treated as errors. CPU/TCG/QOM and display
callbacks and moved headers were adapted; guest instructions and device
behavior were preserved. Build dependency pins were accepted unchanged.
The build rejects reuse of a cache configured for another QEMU release.

Upgrade acceptance passed foundation/probe/fault/QMP, full ISA, FF0C,
98 CPU-profile and 32 common-map runs, peripheral/diagnostic startup/flash/long
boot, three-frame display, 18 ALNK and ten IRQ cases, hold/resume, exact
Felucca startup/reference and splash/reference, and the reached boot below.
TCG diagnostics now print three instruction metadata words; the parsers check
PC/zero/zero while retaining the common one-instruction TB assertion.

At upgrade acceptance, unchanged Felucca and a renamed image through the
default generic loader stopped at ECDC, `0x020023be`, 43,278,871 instructions
and 346,230,976 ns. Within
each loader mode, every captured state field, whole SRAM, sample bytes and LCD
pixels match QEMU 10 exactly. The generic and fixture runs also match every
state field except the profile label; sample and LCD bytes match too. Their
differently initialized whole SRAM images are not compared across modes.

The Cocoa functional gate passed 418 frames, ten physical key cycles and 386
changing timer images without pausing: 9.676 guest seconds in 9.646 wall
seconds. This is functional display/pacing validation, not a throughput or
hardware clock measurement. Automated checks inspect guest pixels and running
status; visual inspection of the unbundled QEMU window was unavailable through
the native app inventory during this upgrade.

The QEMU 10 binary, pins, build configuration and old validation caches remain
in main repo `.deps/qemu-11.1.2-upgrade-2026-10-07/baseline-10.0.0/`, with the old
build retained as `.cache/build-qemu-10.0.0/`. New logs and exact comparisons
are in `.deps/qemu-11.1.2-upgrade-2026-10-07/`; reached boot caches use the label
`upgrade-qemu-11-1-2`. Later instruction milestones are recorded below;
this upgrade does not establish completed audio service or a home screen.

## Generic machine boundary

The 2026-10-07 architecture extraction uses one CPU semantic path and one
implemented hardware map for every image. Omit `-append` (or use
`-append application`) for raw application handoff without fixture hashes,
poisoning or implicit checkpoints. Historical fixture modes below now select
only explicit test inputs and observations. See [ARCHITECTURE.md](ARCHITECTURE.md)
for the three layers, input inventory, loader contract and remaining limits.

The unchanged startup/splash/diagnostic gates remain acceptance tests. New
`validate_cpu_profiles.py` and `validate_machine_profiles.py` test profile,
layout and filename independence. A common one-instruction translation-block
boundary preserves conditional completion; this is not a throughput claim.

## Shared clock and routing ownership

The private SoC syscon canonically owns CLK_CON1 (`0x10010`, mask `0x3`),
CLK_CON2 (`0x10014`, mask `0xf00`) and IOMAP_CON5 (`0x51030`, mask `0xc0`).
The same three word-only regions, names, faults and functional rates are
preserved. ALNK checks active changes before canonical assignment and reads
shared getters; USB/ALNK no longer hold duplicate shared values. Existing
capture JSON keys remain unchanged.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_syscon.py
```

All 28 focused cases pass, including independent field readbacks, masks/widths,
active precommit faults and preserved ALNK phase, pending state and samples.
Existing ALNK, common-map, observer-off, IRQ, peripheral/diagnostic USB and boot
gates pass. At the fixed EED2 CPU revision, unchanged Felucca and renamed
generic runs exactly match their own pre-extraction state, all SRAM, sample
and LCD bytes. The F1E0 stop below remains unchanged. Labels are `after-syscon`
and `after-syscon-generic`; main repo `.deps/qemu-syscon-2026-10-07/` retains
baselines, comparisons and gates. Resettable ALNK and an evidenced clock tree
remain open.

## Watch the timer and key matrix in a macOS window

```sh
cd /Users/simonjohansson/src/fm1-qemu-poc
mise exec python@3.13.15 -- python qemu-poc/build.py
mise exec python@3.13.15 -- python qemu-poc/run_display.py
```

The launcher runs the unchanged 2,700-byte display example continuously.
Its timer readout keeps changing, and the OCT-minus matrix tile alternates
between pressed and released every half-second of guest time. The firmware
reads the modeled GPIO matrix and draws every pixel through SPI/DMA.
Execution does not pause after boot or after three frames. Close the window
or quit QEMU to exit; resize the window to enlarge the display. Keyboard and
mouse controls are not mapped; the key cycle is automatic.

The build enables QEMU's built-in Cocoa display on macOS and automatically
reconfigures an older headless build. No additional package installation is
needed on the validated machine. The live launcher uses
`-icount shift=8,align=on,sleep=on` to pace guest virtual time against host
elapsed time. Live mode uses 256 ns per guest instruction, while the exact
headless regressions retain 8 ns (`shift=3`). This reduces busy-loop polling
between device events; modeled TIMER4 and SPI transfer rates are unchanged.
A busy host may fall behind. This is functional emulation, not
a claim about the real FM-1 CPU's clock accuracy. The readout shows the guest
TIMER4 counter in hexadecimal, not elapsed decimal seconds.

The private `FM1_POC_DISPLAY_LIVE=1` switch applies only to `-append display`.
Without it, the existing three-frame regression still exits normally. Live
mode saves no frame captures by default. For analysis, explicitly setting
`FM1_POC_FRAME_DIR` to an existing directory overwrites the fixed files
`frame-live.ppm` and `frame-live.json` at each complete frame. These contain
the guest LCD pixels and observed guest state; the viewer draws no substitute
screen and does not write guest RAM.

Run the continuous-mode and unchanged three-frame checks with:

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_live_display.py --cocoa --cycles 6
mise exec python@3.13.15 -- python qemu-poc/validate_display.py
```

The live check observes execution past frame three, changing timer pixels,
repeated press/release cycles, and QMP's running status without pausing the
guest. The validated native-window run completed 252 frames and six key
cycles without a pause; 5.838 seconds of guest time elapsed in 5.849 seconds
of host time. It stores
its evidence in `.cache/live-display-validation/`. Omit `--cocoa` for a
headless check; the local QMP socket may need permission outside a restricted
sandbox. The unchanged three-frame
regression still passed with 15,581,968 guest instructions and matching
reference pixels outside its timer band. The minimal
build omits Pixman, so QMP `screendump` is unavailable; automated pixel checks
inspect guest LCD captures. The native Cocoa window was previously confirmed
visually by the user.

### Diagnostic checkpoint inspection

The separate `-append diag` fixture still supports `FM1_POC_KEEP_OPEN=1` to
pause at its validated completion checkpoint for inspection. That option is
not used by the live display launcher. `validate_window.py` checks this
older diagnostic hold mode, including unchanged guest state, exact LCD
capture, pause/resume behavior and clean QMP shutdown. Its local Unix socket
may require permission outside a restricted sandbox. Prior native diagnostic
state, SRAM and LCD capture matched the headless USB-retry run exactly.

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

## FM-1_980: unchanged diagnostic boot

The unchanged 14,804-byte `build/fm1-diag.bin` now runs from its actual
application entry, `0x02000120`, through memory initialization and protection
setup. The explicit clean loader handoff supplies `r0 = 0x01c7fe08`; persistent
SRAM starts zero. RAM text, initialized data, BSS and the mailbox start filled
with `0xa5`, so their final contents demonstrate the guest's own copying and
clearing.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_isa.py
mise exec python@3.13.15 -- python qemu-poc/validate_diag_startup.py
mise exec python@3.13.15 -- python qemu-poc/validate_peripherals.py system
```

The memory-copy checkpoint at `0x02001f3c` takes 9,117 QEMU instructions.
The protection checkpoint at `0x0200200a` takes 9,232. All general and special
registers match the separate Rust reference at both checkpoints. Different
P33 transfer clocks produce different polling counts; these are behavioral
checks, not a throughput comparison.

Validation checks the exact copied RAM text and data, cleared BSS, restored
reset reason at `0x01c09684`, and the mailbox's transition from poisoned to
zero. Eighteen timed P33 bytes form six transactions; the guest arms and feeds
the watchdog once without expiry. SP is `0x01c79ef0` after its 272-byte frame
allocation and SSP is `0x01c7c000`. The configured stack, write and PC guards
are checked at runtime, including cached execution. At this startup checkpoint
the write-window mask is 3; the later main-loop top-memory lock is separate.

Focused instruction checks cover signed literal boundaries, stack adjustments,
parallel instructions using incoming register values and conditional blocks.
Diagnostic mode currently limits each translation block to one guest
instruction to preserve conditional-block and peripheral timing. No speedup
claim follows from this implementation.

Guard violations and watchdog expiry terminate explicitly; delivery of the
corresponding hardware exception or reset is not implemented. The audio model
currently supports only reading its cold disabled state and writing zero to
ALNK0 control. Audio generation is not implemented.

The input SHA-256 remains
`781005cfcc4e0b562291fa747a0fa956204ee97c396c39214df04feaf86cc7e2`.
Startup records and SRAM snapshots are under `.cache/diag-validation/`.

### RAM flash driver and LCD initialization

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_diag_flash.py
mise exec python@3.13.15 -- python qemu-poc/validate_peripherals.py
```

The NOR model starts with 1 MiB of erased `0xff` bytes and the unchanged raw
application at physical offset `0x4120`. The application is plaintext; this
does not model boot ROM, SPL, encrypted packages or a complete flash dump.
The guest disables SFC, executes its copied RAM routines, operates SPI0 and
chip select, and restores SFC afterward. Runtime checks reject flash fetches
and data accesses while SFC is disabled, including a previously translated
target and a warmed data mapping.

The guest receives JEDEC ID `0x856014` and sets `flash_ok` itself. It then
reads four and eight erased header bytes and skips the flash-modification
path. The observed totals are three transactions, one JEDEC command, two
read commands, 26 completed and acknowledged byte transfers, and three SFC
disable/restore pairs. Status-register reads are exercised by a separate
protocol test; the clean firmware boot does not issue them. Flash programming
and erase commands remain unsupported and fail explicitly.

LCD initialization returns at `0x0200218e` after 26,374,949 instructions and
210,999,600 ns of virtual time. It completes six commands and two DMA
transfers; it has not drawn pixels at this checkpoint. The JEDEC and erased
header checkpoints match all general and special registers in the Rust
reference. At LCD initialization, the timed-SPI polling counter in `r1` is
16 in QEMU and 0 in Rust; all other registers match. Validation records this
specific timing difference and checks completed transfers and bounded waits.

The 13 peripheral tests cover positive and negative stack/write guard cases,
cached flash access, timed NOR transactions, and the disconnected USB
controller's retained requests and guest polling. They do not establish
normal USB enumeration or CDC operation. Flash/LCD initialization evidence is
under `.cache/diag-flash-validation/`.

### Exact status screen and disconnected USB startup

The firmware proceeds through its renderer and USB startup to `0x0200225e`
after 51,582,487 instructions. At that checkpoint the last LCD transfer has
completed naturally: the panel is visible and idle, with 124,480 pixel writes,
1,216 commands, 1,700 DMAs and 2,916 total completed transfers.

The entire 240 by 240 RGB framebuffer matches the independently derived
status image: a green top bar, `D1A60001`, and a zero run counter. Its pixel
SHA-256 is
`cc00d897e166126d034a174776615fcdac85e473892ea3a3a2eef33db25de6b0`.
It contains 2,880 green, 3,744 white and 50,976 black pixels. These pixels come
from guest SPI/DMA writes; the harness does not draw the screen or alter guest
status memory.

The selected USB state is cold power-on with no cable and an unavailable SIE
clock, a condition explicitly handled by the tracked firmware. Six guest
requests remain pending without DONE. Each receives 20,000 guest polls, for
120,000 in total, after which the guest records its own timeouts and continues
with USB down. This establishes the disconnected startup path, not USB host
traffic, endpoint packet DMA, enumeration, MIDI or CDC.

### Running foreground, timer interrupts and input scans

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_diag_boot.py
```

For a direct bounded boot from the worktree root:

```sh
mkdir -p qemu-poc/.cache/diag-run
FM1_POC_STATE_DIR="$PWD/qemu-poc/.cache/diag-run" \
FM1_POC_LOOP_IRQS=640 FM1_POC_MAX_INSTRUCTIONS=100000000 \
  qemu-poc/.cache/build/qemu-system-pi32v2 \
  -M fm1-poc -accel tcg,thread=single \
  -icount shift=3,align=off,sleep=off \
  -display none -serial none -monitor none -nodefaults \
  -kernel build/fm1-diag.bin -append diag
```

This prints the final JSON record and saves an SRAM snapshot and LCD PPM.
The observer stops at a foreground checkpoint after at least 640 completed
interrupt returns and three foreground visits; it does not change guest state.

The bounded main-loop gate reaches `0x02002632` after 59,582,859 instructions.
It observes 640 TIMER5 expirations, acknowledgments, interrupt entries and
`rti` returns, with no pending interrupt and execution outside the handler.
The firmware has visited its foreground loop 16,685 times and fed the
watchdog 16,685 times. Its millisecond counter is 476; both stacks are restored,
LCD/P33 timeout counters remain zero, and the final write-window mask is 7.

These 640 ticks cover 58 complete eleven-column matrix scans and the
forty-frame encoder-rest learning interval. The validator checks released
matrix inputs, debounce and encoder state, the clean boot-guard tuple
`(0x42475244, 0, 1)`, and the unchanged complete status image. This is a real
foreground checkpoint after completed interrupts, not a stop at first entry
to the main loop.

Evidence is under `.cache/diag-boot-validation/`: each checkpoint includes
`state.json`, raw SRAM, the actual LCD PPM and stderr. The startup, flash and
small-fixture validators remain separate regression gates.

### Continued execution through the scheduled USB retry

A longer bounded run passes one second of guest time and completes the
firmware's scheduled disconnected-USB retry; `validate_diag_boot.py` includes
this stage. For a direct run, use
`FM1_POC_LOOP_IRQS=6000` and `FM1_POC_MAX_INSTRUCTIONS=200000000` in the command
above. It returns to the foreground
checkpoint after 128,755,894 instructions and 1,030,047,160 ns of virtual time.
All 6,173 timer expirations, interrupt entries, acknowledgments and returns
balance. The guest records 1,029 milliseconds, 153,163 foreground visits and
watchdog feeds, and one USB retry. USB has issued twelve requests and performed
240,000 polls; it remains down with six consecutive timeouts after the retry.
The LCD remains unchanged, both stacks are restored, and LCD/P33 timeout and
watchdog-expiry counters remain zero.

This run exposed and fixed a CPU lifetime error: the final call in a selected
conditional arm must finish that arm before the callee executes its own
conditional instructions. Direct, short and register calls without an ELSE,
and final calls in a selected ELSE arm, match the separate reference. A final
THEN call followed by ELSE has unresolved
reference semantics and is rejected explicitly; it is not used by this boot.
General nested conditional blocks remain unsupported.

## Scope and next target

### Bounded Felucca application startup

The private `-append felucca` profile now boots the unchanged application with
SHA-256 `12a4b4ea47248467f566ec3b6984b08f2f89d6ef5a8e9e494cab5184e8fadb36`.
It has separate observers and hash-bound SRAM section poisoning; diagnostic
checkpoint addresses and result-memory fields are not used. The existing
application-entry handoff supplies `r0 = 0x01c7fe08`, one CPU and erased NOR
with the raw application at physical offset `0x4120`. Its selected ELF load
segments match the raw binary; this establishes artifact identity, not a
rebuild from the currently checked-out Felucca source.

```sh
mise exec python@3.13.15 -- python qemu-poc/run_felucca.py
mise exec python@3.13.15 -- python qemu-poc/run_felucca.py \
  --label startup-copies --stop-pc 0x0200cc64 \
  --expect-reason 'checkpoint reached'
mise exec python@3.13.15 -- python qemu-poc/validate_felucca.py --capture
mise exec python@3.13.15 -- python qemu-poc/validate_felucca.py --capture --reference
```

The runner checks the binary, ELF and disassembly hashes and saves state,
registers, last access, whole SRAM, actual LCD pixels, nearby disassembly,
command, functional clock and executable hash under
`.cache/felucca-validation/<label>/`. Unsupported behavior and instruction
budget exhaustion exit explicitly; a host timeout retains partial logs.
Default runs stop after at most 100 million instructions, using 8 ns per
functional instruction, without diagnostic pause/hold mode.

The first captured failure is the compact stack store `[sp+132] = r0` at
`0x0200cd2a` (bytes `a0 21`), after 157,370 guest instructions. Before it,
the copies checkpoint at `0x0200cc64` takes 157,321 instructions and verifies
the guest's exact RAM-code/data copies, zero BSS/pool/mailbox, application
handoff, installed fatal vectors and cold bootguard. Watchdog setup completes
without expiry and the guest subsequently enables its protection guards.
This startup checkpoint precedes LCD initialization; the completed splash
milestone is recorded below.

The compact stack offset decoder now includes its sixth unsigned word-offset
bit. Boundary tests at 124, 128, 132 and 252 bytes, neighboring memory and
incoming-source bundle behavior match the separate Rust process. The full
focused ISA gate passes. The unchanged application advances through JEDEC
identification to the sample header scan, then explicitly stops at the
pre-increment word load `r1 = [++r6=r1]` (`dc ec 62 11`) at `0x020049fa`
after 269,535 instructions. Evidence is in `after-stack-offset/`.

The optional `--reference` gate runs the existing Rust emulator as a separate
process through a private wrapper using only its public interfaces. At the
startup-copy checkpoint all 16 general registers, all 16 special registers,
the complete 512 KiB SRAM and the pre-LCD pixels match exactly. QEMU retires
157,321 instructions and Rust 155,089 under their different functional clocks.
The caller verifies all input hashes and binds both executable hashes to the
comparison evidence. The Rust wrapper keeps its existing physical NOR erased
while mapping the raw application into XIP; QEMU also seeds the application
bytes into physical NOR. These are equivalent for the selected startup
checkpoint, before any NOR transaction, rather than a full flash comparison.
No Rust implementation is linked or copied into QEMU.

### Unchanged Felucca splash

The application completes its own splash at `0x0200d0c2`, after both text
boxes have synchronized their LCD transfers. Run the dedicated gate with:

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_splash.py
```

The selected binary remains byte-for-byte unchanged. QEMU completes the
checkpoint after 38,257,079 instructions and 306,056,640 ns under the
8 ns functional clock. The real SPI/DMA command stream produces 69,120
pixel writes: the full background and both splash text boxes. The LCD is
visible and idle, with 16 commands, 250 DMA transfers and 266 completed
transfers. All 240 by 240 RGB pixels match the separate Rust process exactly
(RGB SHA-256 `93f011c5d3a19e513c75ee4f7142e81513579b25255d157a73c81fe34db676c2`).

The gate verifies guest PC, stack and write protection windows with no error
bits, application SP `0x01c79eac` and supervisor SP `0x01c7c000`, intact RAM
code/guard bands/mailbox/vectors and cold crash/debug state. The bootguard is
`(0x42475244, 0, 1)`. NOR reads and watchdog/protection setup have occurred;
LCD/P33 timeout and watchdog-expiry counters are zero. This checkpoint
precedes interrupt startup. Different reference clocks leave polling
registers and temporary stack values different; exact pixels and meaningful
initialized state are compared without requiring equal instruction counts.

Evidence is in `.cache/felucca-validation/splash/` and `splash-reference/`,
with a durable copy in
`/Users/simonjohansson/src/fm1-emulator/.deps/qemu-felucca-2026-10-06/`.
The first post-splash fault was the compact byte store `b[r3++=1] = r4`
in `memcpy` at `0x02000ade` (38,257,636 instructions). Subsequent focused
CPU extensions advance the unchanged application to audio initialization.

### Reached ALNK audio configuration

The first missing peripheral access after the splash was the halfword write
to ALNK0 CON1 at `0x00012e04`, PC `0x0200d336`, after 38,928,409 instructions.
The dedicated audio model now covers the reached control-register widths,
clock/routing words and the selected configuration: two 512-word halves,
256 stereo frames per half, with a functional 44.1 kHz completion clock.
Cumulative rational deadlines avoid rounding drift. Each completion reads
the actual guest SRAM half into a bounded sample sink; pending IRQ11 is a
level latch until the guest writes its acknowledgment. Further completions
coalesce while pending. Delayed callbacks count skipped captures instead of
fabricating earlier sample contents; normal deterministic probes have not
exercised that skipped-history path.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_devices.py
```

Six positive and twelve explicit-fault probes pass, including twelve
alternating halves, exact sample bytes after guest buffer mutation,
acknowledgments, phase retention, cancellation and invalid widths/configs.
They use a separate private `alnk-probe` guest with interrupts disabled;
the unchanged Felucca profile still enforces its binary hash. Probe evidence
binds both fixture and QEMU executable hashes and saves the bounded latest
half as `state.alnk`. The established diagnostic peripheral gate also passes.

The unchanged boot then reached the parallel ADD/store bundle at
`0x0200d380` (`01 f1 20 30 b9 60`), after 38,928,433 instructions; see
`after-alnk-device/`. That observation precedes the IRQ11 enable and DMA
start. The subsequent halfword-store bundle classification fix advances to
the real IRQ11 enable at `0x0200d3ee` (38,928,471 instructions).

### First real Felucca audio interrupt

Source selection now dispatches enabled pending ALNK11 and TIMER5 63 through
their own guest vectors, selecting the higher priority and preserving each
device latch until its acknowledgment. Existing global-enable, conditional
block and nonnested interrupt gates remain; simultaneous equal priorities
fail explicitly. Nesting has not been generalized or validated.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_irq.py
```

All ten focused cases pass: actual wrapper save/restore opcodes with
instrumented disposable callees, both sources, simultaneous audio-first
priority, mask/source-enable hold and release, global STI gating and two
explicit faults. They verify all general registers, special return/stack
state, canaries, independent acknowledgments and balanced per-source entry
and return counts. Established foundation/probe/QMP checks also pass, as does
the unchanged diagnostic through 6,173 balanced timer interrupts and its
scheduled disconnected-USB retry.

At the IRQ-selection milestone, unchanged Felucca entered its own audio
wrapper and C handler. It stopped explicitly at the signed literal branch `FF0C/1FFF/0C48` at
`0x020022aa`, after 43,278,840 instructions. The captured ICFG is `0x030b0308`
and SP is `0x01c7be24`, exactly 28 wrapper + 52 callee-save + 396 local bytes
below SSP. ALNK has completed five halves, coalesced four pending completions,
and captured 2,560 zero sample words without skipped captures. Guard/error
and watchdog-expiry fields are zero; the splash pixels remain unchanged.
This establishes first real audio entry, not a completed audio ISR or home
screen. ADC, sustained operation, physical inputs and the Felucca viewer
remain unvalidated. See `after-irq-selection/` and the focused/regression
records preserved in the worktree cache and durable `.deps` directory.

Work resumed with user authorization on 2026-10-07. The generic boundary
extraction and signed-literal branch milestone below supersede that checkpoint;
[../plan.md](../plan.md) contains the current sequence and subagent workflow.

### Signed-literal branch checkpoint

FF0C now implements signed greater-than against a signed 12-bit literal with
a signed 16-bit word displacement from PC+6. All images share this CPU path;
existing unsigned branch forms are unchanged. Validate with:

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_long_signed_branch.py
```

The focused gate passes 27 comparisons against the separate reference, two
independent six-byte conditional-skip checks and six explicit faults. The
reference's conditional scanner/completion disagrees for this opcode. Final
selected THEN with ELSE is conservatively rejected before retirement; this
combination needs independent hardware/ISA evidence. These checks are host
validation only. Full ISA, 98 CPU-profile runs and ten IRQ cases also pass.

After this branch milestone, unchanged Felucca stopped at unsupported
`ECDC/5013`, PC `0x020023be`, after
43,278,871 instructions (346,230,976 virtual ns): 31 instructions beyond the
previous branch. The vendor disassembly calls this `[++r1=r0] = r5`; exact
addressing/writeback semantics were established in the next milestone below.
Audio still has one
IRQ entry and no return or acknowledgment, with five captured zero halves.
The splash is intact. Generic default loading of a renamed identical image
matches all captured state fields apart from the profile label; LCD pixels
match exactly. The differently initialized whole SRAM images are not compared.
Evidence is under `after-signed-literal-branch/` in both the Felucca and generic
application caches, copied to the main repo's
`.deps/qemu-architecture-2026-10-07/`. This does not establish completed audio
service, synthesis or a home screen.

### Preindex word store checkpoint

ECDC kind 3 now stores a word at the wrapping, unscaled sum of the incoming
base and index and writes that sum back to the base. This common CPU behavior
has no firmware identity checks. Source==base stores remain explicit faults:
the pinned Apache SLEIGH and the separate reference disagree about the stored
value. Other evidenced aliases are covered by the focused gate:

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_indexed_store.py
```

It passes 14 reference comparisons, one generic replay and 15 fault cases.
Fault-state writeback follows the existing model policy; hardware fault-state
ordering is unverified. Full ISA, 98 CPU-profile cases, ten IRQ cases and
foundation/probe/QMP/fault regressions also pass after this change.

After the preindex milestone, unchanged Felucca reached `EED2/2510`, PC `0x0200249e`, after 43,279,178
instructions and 346,233,432 virtual ns. The vendor disassembly calls it
`b[r1++=80] = r2`; its semantics are established in the next milestone below.
It advances 307 instructions beyond ECDC. Audio still has one IRQ entry,
no acknowledgment/return and five captured zero halves; the splash is intact.
A renamed unchanged image under the default generic loader matches all
captured state fields apart from the profile label and all sample/LCD bytes.
Whole SRAM is not compared across the differently initialized loader modes.
Evidence labels are `after-indexed-store` and `after-indexed-store-generic`,
saved to main repo `.deps/qemu-indexed-store-2026-10-07/` with focused gates
and raw oracle disagreement evidence. This is not completed audio service,
synthesis or a home screen.

### Postincrement byte store and latest checkpoint

Exact EED2 now stores the incoming source low byte at the old base, then adds
the unsigned eight-bit immediate stride to that base. Source==base uses the
incoming pointer byte, matching the pinned SLEIGH and separate reference.
The behavior is shared by every image; neighboring opcodes are unchanged.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_postincrement_store.py
```

The focused gate passes 49 reference comparisons, one generic replay and nine
fault cases. It verifies stride/byte boundaries, every GPR field and alias,
PSR, conditional selection/skip, retirement and fault-before-writeback model
state. Hardware fault state and successful 32-bit wrapping writeback remain
unverified. Full ISA, ECDC, 98 profiles, ten IRQ cases and boot gates pass.

Unchanged Felucca now stops at `F1E0/1EB3` at `0x02002776`, after 43,279,574
instructions and 346,236,600 virtual ns. The existing packed multiply needs
parallel-head destination classification: the vendor bundle is
`r0 = r1 * 0x598` with `[sp+76] = r1`. The EED2 milestone advances another
396 instructions; audio still has one IRQ entry without acknowledgment/return,
five captured zero halves and an intact splash. The renamed default-loader
replay matches captured state except the profile label, sample bytes and LCD
pixels. Whole SRAM is not compared across different initialization modes.
Caches use `after-postincrement-store` and `after-postincrement-store-generic`;
durable logs, focused gates and raw reference probes are in the main repo's
`.deps/qemu-postincrement-store-2026-10-07/`. Home and synthesis remain unverified.

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
firmware, connected USB, audio generation, dual-core execution and arbitrary
firmware loading remain outside the verified boot paths. The Rust emulator
remains the operational
implementation and a separate behavioral reference; it is not linked into
QEMU or called to execute guest instructions.

FM-1_980 was installed on hardware earlier in development. Its unchanged
application now initializes, identifies flash, renders its status screen and
runs timer-driven input scans and the foreground loop. Full Felucca and stock
firmware still require separate bring-up and compatibility validation.

See [LICENSES.md](LICENSES.md) for implementation provenance and
[README.md](README.md) for the pinned build and original prototype details.

## Commits and preserved evidence

The milestones are separate signed commits:

- `33b8b9a`: preserve the existing instruction/timer proof of concept.
- `e095683`: boot the complete unchanged foundation diagnostic.
- `2847f0b`: boot the bare display diagnostic and validate three SPI/DMA frames.
- `9d996b7`: run FM-1_980 from its true entry to the first P33 transaction.
- `68812d5`: validate startup memory, P33/watchdog and protection setup.
- `caaa5cc`: execute its RAM flash driver and initialize the LCD.
- `a531f78`: validate exact status pixels and 640 completed TIMER5 cycles.
- `0e4149d`: fix conditional-call completion and validate the scheduled USB
  retry after one second of guest time.
- `57ea938`: add the native Cocoa LCD window, launcher and held checkpoint.

All listed signatures were verified. The original tracked fixture binaries and
recorded results remain unchanged. Current validation records and frame images
also have durable ignored copies alongside the persistent worktree:

- Small fixtures:
  `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-boot-2026-10-05/`
- FM-1_980, with separate records for each milestone:
  `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-diag-boot-2026-10-05/`
- Native window and pause checks:
  `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-window-2026-10-06/`
