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
baselines, comparisons and gates. The later ALNK lifecycle milestone below adds local reset; an evidenced clock
tree remains open.

## Local audio-device reset lifecycle

ALNK is now a private SysBus child, with the existing mapping/rates/IRQ wiring.
Resettable enter cancels its completion timer and clears local DMA/registers,
pending and capture history; hold lowers the ALNK IRQ through its connector.
Shared syscon words, SRAM, CPU state and other controllers are preserved.
Unrealize unregisters owned syscon callbacks; finalization frees the timer.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_alnk_reset.py
```

Eighteen cases pass: nine functional cases and nine schedule rejections. They
attest cold/configured/repeated reset, cancellation before completion, clearing
pending, passage beyond old deadlines, fresh phase after reconfiguration and
actual surviving TIMER5 IRQ63 delivery/ack/RTI. Opt-in test-only
`FM1_POC_ALNK_RESETS_NS` schedules up to 16 sorted positive absolute virtual
nanoseconds (equal times permitted) and requires `FM1_POC_STATE_DIR`.
Pre/post evidence goes to separate `alnk-reset.jsonl`; existing capture JSON
is unchanged. No firmware identity or guest PC triggers device reset.

Syscon, ALNK, maps, profiles, IRQ, peripherals, diagnostics and boot gates pass.
With CPU fixed at 0ba8792, unchanged fixture and renamed generic runs exactly
match all pre-lifecycle state fields, whole SRAM, sample and LCD bytes within
each mode. The EDD8 stop at that milestone was unchanged. Caches use `after-alnk-lifecycle`
and its `-generic` label; main repo `.deps/qemu-alnk-lifecycle-2026-10-07/`
retains baselines, sidecars and gates. Whole-machine/watchdog/physical reset
and runtime unrealize/re-realize remain unvalidated.

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

### Postincrement byte store checkpoint

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

After the postincrement milestone, unchanged Felucca stopped at `F1E0/1EB3` at `0x02002776`, after 43,279,574
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

### Packed multiply bundle checkpoint

The existing E1E0 packed multiply is now classified as writing its low-nibble
destination when used as a parallel head. Scalar arithmetic, packed literals
and incoming-register bundle execution are preserved for every image.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_parallel_packed_multiply.py
```

The gate passes 39 reference comparisons, one generic replay and eight faults.
Incoming source/store/address aliases, every destination, low-word products,
PSR, six/eight-byte conditional sizing and retirement are checked. Hardware
fault state and inherited unused packed-literal discrepancies remain unverified.
Full ISA, 98 profiles, ten IRQ cases and boot gates pass.

After this milestone, unchanged Felucca stopped at `ED54/63BC`, PC `0x02002782`, after 43,279,576
instructions and 346,236,616 virtual ns. The vendor disassembly shows
`r6 = h[r11+60] (s)`, followed by `ED55/52FC`, `r5 = h[r15+300] (s)`.
This advances two bundles beyond the preceding stop. IRQ11 still has no
acknowledgment/return; the five captured halves are zero and the splash is
intact. Renamed generic loading matches all captured state fields except the
profile label and all sample/LCD bytes. Whole SRAM is not compared across
loader modes. Caches use `after-parallel-packed-multiply` and its `-generic`
label; main repo `.deps/qemu-parallel-packed-multiply-2026-10-07/` preserves
focused gates, actual boot captures and reference probes. Home and synthesis
remain unverified.

### Signed halfword loads checkpoint

Exact ED54/55 now load signed little-endian halfwords using an even unsigned
offset of 0..510 and the incoming base. Destination/base aliases and PSR are
preserved. Operands with bit 0 set and ED56/57 remain explicitly unsupported;
the existing unsigned load and parallel paths are unchanged.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_halfword_load.py
```

The gate passes 68 reference comparisons, one generic replay and eight faults.
It covers sign/offset boundaries, all GPR fields and aliases, readable regions,
guards, PSR, conditional sizing and retirement. Fault state is a model check,
not hardware validation. Full ISA, 98 profiles, ten IRQ cases and boot pass.

After this milestone, unchanged Felucca stopped at DB01, PC `0x02002eba`, after 43,279,745 instructions
and 346,237,968 virtual ns: 169 instructions beyond ED54. The vendor bundle is
`r1 *= r0 #` paired with `[sp+64] = r3`; scalar multiply already exists, but
its parallel-head destination is not classified. IRQ11 still has no ack/return,
five captured halves are zero and the splash is intact. Renamed generic loading
matches captured state except the profile label and all sample/LCD bytes.
Whole SRAM is not compared across loader modes. Caches use
`after-signed-halfword-load` and its `-generic` label; main repo
`.deps/qemu-signed-halfword-load-2026-10-07/` retains raw probes and acceptance
logs, including the repaired test capture-profile failure. Home and synthesis
remain unverified.

### Compact multiply bundle checkpoint

The existing scalar 1B00 multiply now has a parallel-head destination mask.
This is one classifier entry; scalar arithmetic, incoming register reads,
bundle order and four/six-byte sizing remain unchanged for every image.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_parallel_register_multiply.py
```

The gate passes 41 reference cases, one generic replay and eight faults.
Nonzero products, incoming aliases, every register field, square/overflow
boundaries, PSR, conditional sizing and retirement are checked. Hardware fault
state remains unverified. Full ISA, 98 profiles, ten IRQ cases and boot pass.

After this milestone, unchanged Felucca stopped at EDD8/302A, PC `0x02001c58`, after 43,279,913
instructions and 346,239,312 virtual ns: 168 instructions beyond DB01.
Vendor disassembly shows `r3 = h[r2+r0<<1] (s)`, followed by the alias form
`r0 = h[r2+r0<<1] (s)`. IRQ11 still has no ack/return, five captured halves
are zero and the splash is intact. Renamed generic loading matches captured
state except the profile label and all sample/LCD bytes; whole SRAM is not
compared across loader modes. Caches use `after-parallel-register-multiply`
and its `-generic` label; main repo
`.deps/qemu-parallel-register-multiply-2026-10-07/` retains probes and gates.
Home and synthesis remain unverified. The subsequent ALNK lifecycle milestone preserves this checkpoint exactly.
The latest unchanged/generic labels are `after-alnk-lifecycle` and its
`-generic` counterpart.

### Signed indexed halfword load checkpoint

Exact EDD8 operand kind A loads a signed little-endian halfword at wrapping
incoming base+(index<<1), without writeback or PSR changes. Existing unsigned
kinds 8/9 and the parallel classifier are unchanged for every image.

Pinned Apache SLEIGH's comment and vendor disassembly describe index<<1,
but that constructor body omits the shift. Discriminating separate-reference
probes agree with vendor scaling; retain this discrepancy as evidence rather
than describing all sources as agreeing. Hardware fault state is unverified.

`validate_signed_indexed_halfword_load.py` passes 73 reference cases, one
generic replay and 17 faults: sign/index boundaries, wrapping sums, all GPR
fields/aliases, distinct scaled/unscaled memory, readable limits, PSR,
conditionals/retirement, guards and unsupported EDD8 kinds. Full ISA, 98
profiles, ten IRQ cases and boot pass. Unchanged Felucca advances eight
instructions to AF88; renamed generic state except profile and sample/LCD
bytes match. Whole SRAM is not compared across initialization modes.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_indexed_halfword_load.py
```

At this milestone: AF88 at `0x02001c70`, 43,279,921 instructions and 346,239,376 ns.
Vendor `r0 = r0 >>> 15` requires the signed right immediate shift. IRQ11 still
has no ack/return; captured halves are zero and the splash is intact. Caches
use `after-signed-indexed-halfword-load` and its `-generic` label; main repo
`.deps/qemu-signed-indexed-halfword-load-2026-10-07/` retains raw evidence,
acceptance and repaired test failures. Home and synthesis remain unverified.

### Immediate arithmetic right shift checkpoint

The compact signed-right family uses mask `0xe088`, value `0xa088`, low
three-bit source/destination fields and an unsigned five-bit count. Count
zero preserves input; 31 replicates its sign. PSR remains unchanged. Only
the two-line scalar path is added; logical shifts and parallel classification
retain their behavior for every image.

`validate_arithmetic_shift.py` passes 126 separate-reference cases, one
generic replay and five faults. All 32 counts, all 8x8 register pairs,
aliases/sign boundaries, full PSR, conditional two-byte sizing, retirement
and unchanged memory are checked. Deferred left/register/tail forms and
PC guards verify existing model policy; hardware fault state is unverified.
Full ISA, 98 profiles, ten IRQ cases and boot pass. Unchanged Felucca advances
five instructions to EE01; renamed generic state except profile and sample/
LCD bytes match. Whole SRAM is not compared across initialization modes.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_arithmetic_shift.py
```

At this milestone: EE01/0019 at `0x02002f8e`, 43,279,926 instructions and
346,239,416 ns. Vendor `ifs (r0 > r1) goto 50` targets `0x02002fc4`.
IRQ11 still has no ack/return; captured halves are zero and the splash intact.
Caches use `after-arithmetic-shift` and its `-generic` label; main repo
`.deps/qemu-arithmetic-shift-2026-10-08/` retains primary/reference evidence
and acceptance. Home and synthesis remain unverified.

### Signed register greater-than checkpoint

Exact `(op & 0xfff0) == 0xee00` compares signed32 second-word bits15:12
against opcode bits3:0, with signed9 word displacement from PC+4. Only
admission and the signed-GT condition are added. FF0C, common predicate
completion and neighboring memory encodings retain their behavior.

`validate_signed_register_branch.py` passes 70 separate-reference cases
(54 ordinary, 16 conditional), one generic replay and16 model faults at that
checkpoint (14 after EE80/ED80 admission retires their obsolete negatives).
Register fields, signed boundaries, displacement endpoints, aliases, PSR,
conditional sizing and retirement are checked. Second-word bits11:9 are
rejected as conservative decoder policy; primary evidence leaves them
unconstrained, and the reference accepts one pattern. True PC32 wrapping
and hardware fault state remain unverified.

Four taken exits beyond a conditional arm retain the model's predicate state
and fault at the following IF, after branch retirement; the reference completes
those exits. Source inspection shows retained state also blocks IRQ admission.
This inherited limitation is preserved explicitly, not claimed as valid ISA
behavior. Generic predicate completion and interrupt admission need a separate
evidenced milestone; broadening common advance/call behavior is not part of
this change. Full ISA, 98 profiles, ten IRQ cases and boot pass.

Unchanged Felucca advances ten instructions to F0E0; renamed default loading
matches captured state except profile and all sample/LCD bytes. Whole SRAM
is not compared across initialization modes.

At this milestone: F0E0/BC00 +3580 at `0x02002fde`, 43,279,936
instructions and 346,239,496 ns. Vendor pairs `r0 = r11 + 0x8000`
with `[sp+84] = incoming r0`. IRQ11 still has no ack/return; captured halves
are zero and the splash intact. Caches use `after-signed-register-branch`
and its `-generic` label; main repo `.deps/qemu-signed-register-branch-2026-10-08/`
retains primary/reference disagreements and acceptance. Home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_register_branch.py
```

### Packed-add parallel classification checkpoint

Only the existing E0E0 scalar family's destination mask is added to the
parallel classifier. Packed literals, ALU flags, incoming-register snapshots
and tail-first bundle execution are unchanged for every image.

`validate_parallel_packed_add.py` passes 71 separate-reference cases, one
generic replay and nine model faults. Packed constants, signed/unsigned sum
boundaries, aliases, old store source/address, six/eight-byte conditional
sizing and one retirement are checked. A disjoint ALU tail produces different
flags from the head, confirming existing head-last flag order for that case.
Upper PSR bits are retained. Flag authority is existing scalar model policy
plus executable reference; primary SLEIGH specifies arithmetic result only.
Disputed repeated-byte literal modes remain outside this change.

Four precheck and five tail-access faults retain original GPR/PSR/memory
before add effects/retirement; hardware fault state remains unverified. Full
ISA, 98 profiles, ten IRQ cases and boot pass. Unchanged firmware advances
980 instructions to ED34; renamed default loading matches state except
profile and all sample/LCD bytes. Whole SRAM is not compared across modes.

At this milestone: ED34/4000 at `0x02002010`, 43,280,916 instructions
and 346,247,336 ns. Vendor `ifs (r4 >= 0)` selects two instructions.
IRQ11 still has no ack/return; captured halves are zero and the splash intact.
Caches use `after-parallel-packed-add` and its `-generic` label; main repo
`.deps/qemu-parallel-packed-add-2026-10-08/` retains primary/reference evidence
and acceptance. Home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_parallel_packed_add.py
```

### Signed greater-or-equal IF checkpoint

Exact ED30/FFF0 compares signed32 GPR with a signed12 literal through the
existing four-byte IF path. THEN count is bits14:15+1 and ELSE bits12:13;
arm scanning, predicate helpers, existing IF kinds and classifier are unchanged.
Pinned primary names an unsigned token; vendor ED31/0F00 means >= -256.
Discriminating negative-literal reference evidence agrees with signed12 and
retains the primary discrepancy.

`validate_signed_literal_if.py` passes 139 separate-reference cases, one
generic replay and 12 model faults at that milestone. EEB0 admission later
retires its obsolete rejection, so the current GE gate has 11 faults. Signed boundaries, all GPR fields,
THEN1..4/ELSE0..3, 2/4/6-byte scalar and 4/6/8-byte bundle arm widths,
PSR/RETS, selected stores and balanced nonnested follow-up IF are checked.
At that milestone faults covered two deferred families, four inherited predicate limits, two PC
guards and four selected-store accesses. Header/body/control-transfer fault
retirement is asserted separately; hardware fault state remains unverified.

Nested IF, final THEN CALL/FF0C with ELSE and taken-exit limitations remain.
A taken exit faults at a following IF after header/branch retirement, while
reference completion succeeds. Retained predicate IRQ blocking is source
inspection only, not interrupt validation. Full ISA, 98 profiles, ten IRQ
cases and boot pass. Unchanged firmware advances five instructions to EEB4;
renamed default loading matches state except profile and all sample/LCD
bytes. Whole SRAM is not compared across initialization modes.

At this milestone: EEB4/4000 at `0x02002020`, 43,280,921 instructions
and 346,247,376 ns. Vendor `ifs (r4 <= 0)` selects two instructions.
IRQ11 still has no ack/return; captured halves are zero and the splash intact.
Caches use `after-signed-literal-if` and its `-generic` label; main repo
`.deps/qemu-signed-literal-if-2026-10-08/` retains primary/reference disagreements
and acceptance. Home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_literal_if.py
```

### EEB0 signed-literal less-or-equal IF checkpoint

Exact EEB0/FFF0 compares signed32 GPR with a signed12 threshold using the
existing IF machinery. ED30 and common scanner/helpers/classifier are unchanged.
Primary packed-token labeling disagrees with vendor <= -1 and discriminating
reference probes: FFF means -1 rather than packed510; 100 means256 rather than
packed0. Both negative and positive discrepancies are retained.

`validate_signed_literal_le_if.py` passes 141 separate-reference cases, one
generic replay and 11 model faults. All fields, thresholds, THEN1..4/ELSE0..3,
mixed scalar/bundle widths, selected SP+12 stores, PSR/RETS and balanced
nonnested follow-up IF are checked. Inherited nested/call/FF0C/taken-exit
limits remain explicit; taken-exit IRQ blocking is source inspection only.
Hardware fault state remains unverified.

Admitting EEB0 retires the old GE gate's obsolete unsupported-EEB4 case.
Its 139 positive fixtures are unchanged; the current GE gate passes 139
reference cases, one generic replay and 11 faults. Full ISA, 98 profiles,
ten IRQ cases and boot pass. Unchanged firmware advances 121 instructions
to ED13; renamed loading matches state except profile and sample/LCD bytes.
Whole SRAM is not compared across initialization modes.

At this milestone: ED13/4100 at `0x0200367e`, 43,281,042
instructions and 346,248,344 ns. Vendor `ifs (r3 >= r1)` selects two THEN instructions. Establish exact ED10
signed-register fields and unused-low-byte admission policy independently.
Caches use `after-signed-literal-le-if` and its `-generic` label; main repo
`.deps/qemu-signed-literal-le-if-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 1/0; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_literal_le_if.py
```

### ED10 signed-register greater-or-equal IF checkpoint

Exact ED10/FFF0 compares signed32 GPR[opcode low nibble] against GPR in
second-word bits8..11. It admits the primary constructor's zero low byte;
the separate reference ignores tested nonzero bytes. Their rejection is a
conservative canonical-encoding policy, not verified hardware reserved-bit
behavior. Existing91/GEU, C1/GTU, literal families and common IF machinery
are unchanged.

`validate_signed_register_if.py` passes 158 separate-reference cases, one
generic replay and 20 model faults at this milestone (19 after E435 admission).
All fields, aliases, signed boundaries,
arm counts/mixed widths, PSR/RETS, stores and balanced follow-up IF are
checked. Nine nonzero-byte cases retain separately verified oracle completion
against model rejection. Before E435 admission, its reached signed-min body
fault occurred after the IF and preceding literal retired, preserving r0=32767.
That obsolete negative is now retired and actual-body positives belong to the
minimum gate. Four inherited
predicate limits, two PC guards and four store faults retain precise count,
full registers/specials and memory. Hardware fault state remains unverified;
retained predicate IRQ blocking is source inspection only.

Full ISA, 98 profiles, ten IRQ cases and boot pass. Unchanged firmware advances
two instructions to E435; renamed loading matches state except profile and
all sample/LCD bytes. Whole SRAM is not compared across initialization modes.

At this milestone: E435/0031 at `0x02003686`, 43,281,044
instructions and 346,248,360 ns. Vendor `r0 = smin(r3, r0)` is selected after the signed IF and literal.
Establish exact E435 mode1 signed minimum while preserving mode0 unsigned minimum.
Caches use `after-signed-register-if` and its `-generic` label; main repo
`.deps/qemu-signed-register-if-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 1/0; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_register_if.py
```

### E435 signed minimum checkpoint

Exact scalar E435 mode1 implements signed32 minimum with destination in
second-word bits12..15, left operand bits4..7 and right operand bits8..11.
The primary constructor and vendor disassembly agree. Mode0 unsigned minimum,
other scalar families, unsupported modes and parallel classification remain
unchanged. No firmware identity or guest PC selects CPU behavior.

At the scalar milestone, `validate_signed_minimum.py` passed140 separate-reference
cases, one generic replay and16 model faults. The later parallel-minimum
milestone retains all140 positives and retires only the F435+NOP deferred
negative, leaving15 current model faults. Signed boundaries, every operand field, aliases,
mode0 controls, PSR/count and five actual conditional-body sequences are checked.
The historical gate checked Modes2..15, deferred F435 bundle rejection
and the PC guard fault state. Exact mode1 bundles are now supported by the
later dedicated parallel gate; mode0 remains deferred. The old ED10 gate removes only its now-obsolete minimum body fault;
all 158 positive cases remain, with 19 current model faults. Hardware fault
state remains unverified.

Full ISA, 98 profiles, ten IRQ cases and boot pass. Unchanged firmware advances
8,404 instructions to F430; renamed loading matches all captured state except
profile and all sample/LCD bytes. Whole SRAM is not compared across different
initialization modes. IRQ11 has still not acknowledged or returned.

At this milestone: F430/1500 +6100 at `0x02003992`, 43,289,448
instructions and 346,315,592 ns. Vendor `r1 = abs(r5)` is paired with `r0 = [r0 + 4]`.
Scalar E430 absolute value is also absent: admitting only the bundle classifier
would execute its tail before the head fault. Establish exact scalar E430 and
canonical guarded bundle classification together before accepting this form.
Caches use `after-signed-minimum` and its `-generic` label; main repo
`.deps/qemu-signed-minimum-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 1/0; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_minimum.py
```

### E430 absolute value and F430 bundles checkpoint

Exact E430 requires a zero second-word low byte, reads source bits8..11
and writes destination bits12..15 using wrap32 absolute value. INT_MIN remains
0x80000000. The pinned Apache constructor and vendor disassembly agree.
The exact guarded classifier admits E430 as a paired head or extended tail;
malformed operands are rejected before any tail effects. Existing incoming
snapshots, bundle order/sizing/count, helpers and E434/EE80/F435 stay fixed.

`validate_parallel_absolute.py` passes 99 separate-reference cases, one generic
replay and 21 model faults. Tests cover all register fields, aliases, signed
boundaries, four/six/eight-byte sizing, actual head/following scalar, an extended
ABS tail, incoming loads/stores and distinct tail flags. Ten malformed operand
faults, three conflict/deferred-tail faults, six access faults and two PC guards
retain full registers/specials, memory and retirement. The reference rejects
sampled noncanonical operands without fault snapshots; hardware fault state and
reserved-bit behavior remain unverified. Separate research retains 543 canonical
reference successes, independently of the focused QEMU acceptance count.

The minimum regression still passes 140 reference cases, one generic replay and
16 faults, including deferred F435. Full ISA, 98 profiles, ten IRQ cases and boot
pass. Unchanged firmware advances two instructions to E434; renamed generic
loading matches captured state except profile and all sample/LCD bytes. Whole
SRAM is not compared across different initialization modes.

At this milestone: E434/1131 at `0x0200399c`, 43,289,450
instructions and 346,315,608 ns. Vendor `r1 = smax(r3, r1)` follows the accepted absolute-value sequence.
The pinned Apache SLEIGH has no exact maximum constructor. Establish exact
mode1 signed maximum from vendor operand witnesses and independent executable
probes; mode0 unsigned witnesses also exist. Keep F434 bundle admission deferred.
Caches use `after-parallel-absolute` and its `-generic` label; main repo
`.deps/qemu-parallel-absolute-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 1/0; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_parallel_absolute.py
```

### E434 signed and unsigned maximum checkpoint

Exact scalar E434 supports mode0 unsigned and mode1 signed maximum with
destination bits12..15, left bits4..7 and right bits8..11. Direct vendor
E434/0050,0100,1131,7741 witnesses and independent executable probes establish
these fields and modes. No exact maximum constructor exists in the pinned
Apache SLEIGH; the minimum constructor is only analogous. Native TCG umax/smax
preserve aliases. Shared multiply/divide/min dispatch, helpers and all parallel
classifiers remain fixed; F434 and F435 stay deferred.

`validate_maximum.py` passes 191 separate-reference cases, one generic replay
and 18 model faults. Both modes, every register field/alias, signed boundaries,
PSR/RETS, balanced selected/skipped arms followed by IF, four vendor witnesses
and the actual preceding ABS pair/scalar/max sequence are checked. ABS(INT_MIN)
meaningfully discriminates signed maximum. Fourteen unsupported modes, two
F434 bundles with memory-changing tails and two PC guards fault before effects
and retirement. The reference rejects modes2..15 without fault snapshots;
hardware validity/fault behavior remain unverified. Separate research retains
714 canonical reference successes, independently of focused QEMU acceptance.

The minimum regression still passes 140 reference cases, one generic replay
and 16 faults. Full ISA, 98 profiles, ten IRQ cases and boot pass. Unchanged
firmware advances one instruction to EE80; renamed generic loading matches
captured state except profile and all sample/LCD bytes. Whole SRAM is not
compared across different initialization modes.

At this milestone: EE80/1004 at `0x020039a0`, 43,289,451
instructions and 346,315,616 ns. Vendor `ifs (r1 <= r0) goto 0x020039ac` uses a signed9 word displacement
from PC+4. The exact Apache progflow constructor286..290 supports signed LE.
Extend only this canonical register-branch family; preserve inherited predicate
exit/completion limits and qualify conservative unused-bit admission.
Caches use `after-maximum` and its `-generic` label; main repo
`.deps/qemu-maximum-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 1/0; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_maximum.py
```

### EE80 signed register less-or-equal branch checkpoint

Exact EE80/FFF0 compares signed32 second-word bits12..15 against opcode
bits0..3 with LE and signed9 word displacement from PC+4. Apache progflow
constructor286..290 and slaspec364 directly agree with vendor EE80/1004.
Only admission and condition change; count/branch recording, common predicate
advance, helpers and classifiers remain fixed.

`validate_signed_register_le_branch.py` passes 70 reference cases (54 ordinary,
16 balanced conditional), one generic replay and 15 model faults at the EE80
milestone (14 after ED80 admission): seven unused
bit policy faults, two deferred families, two PC guards and four inherited
outgoing-arm faults. Fields/aliases, signed boundaries/equality, displacement
endpoints, full PSR/RETS, memory and retirement are checked. Taken exits retain
the predicate and fault at the following IF after the branch retires; the
reference completes that IF. Retained predicate IRQ blocking is source
inspection only. Hardware fault state and true PC32 wrap remain unverified.

Bits11..9 are unconstrained by primary evidence; rejecting them preserves
conservative canonical admission. The reference accepts exactly one sampled
pattern 4 / bit 11 and rejects six others without fault snapshots. This is not
hardware validity evidence. The old EE00 gate removes only its obsolete EE8E
negative/counts and still passes all 70 positives, generic replay and 15 faults.
Full ISA, 98 profiles, ten IRQ cases and boot pass. Unchanged firmware advances
two instructions to ED81; renamed generic state except profile and sample/LCD
bytes match. Whole SRAM is not compared across initialization modes.

At this milestone: ED81/0019 at `0x020039b0`, 43,289,453
instructions and 346,315,632 ns. Vendor `ifs (r0 < r1) goto 0x020039e6` uses signed9 word displacement
from PC+4. Extend only exact ED80/FFF0 with signed LT, preserving the common
pipeline and conservative unused-bit admission. Both current branch gates
still carry the obsolete ED8E deferred negative until this form is admitted.
Caches use `after-signed-register-le-branch` and its `-generic` label; main repo
`.deps/qemu-signed-register-le-branch-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 1/0; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_register_le_branch.py
```

### ED80 signed register less-than branch checkpoint

Exact ED80/FFF0 compares signed32 second-word bits12..15 against opcode
bits0..3 using LT and signed9 word displacement from PC+4. Apache progflow
constructor266..270 and vendor ED81/0019 agree. Only admission and condition
change; canonical operand policy, count/recording, predicate advance, helpers
and classifiers remain fixed.

The new LT gate and both updated GT/LE gates each pass 70 reference cases,
one generic replay and 14 model faults. Each retains 54 ordinary and 16 balanced
conditional cases, seven unused-bit policy faults, one deferred family, two PC
guards and four inherited outgoing-arm faults. Both older gates remove only
ED8E and its counts, preserving all functions and positive generation.
Signed equality distinguishes LT from LE. Fields/aliases, signed boundaries,
displacement endpoints, PSR/RETS, memory and retirement remain checked.

Taken exits retain the predicate and fault at the following IF after branch
retirement; separate reference completion disagreements remain recorded.
IRQ blocking is source inspection only. Primary leaves unused bits unconstrained;
canonical rejection remains model policy, with one reference-accepted pattern4
and six reference errors. Hardware validity/fault state and PC32 wrap remain
unverified. Full ISA, 98 profiles, ten IRQ cases and boot pass. Unchanged firmware
advances seven instructions to EDDC; renamed state except profile and sample/LCD
bytes match. Whole SRAM is not compared across initialization modes.

At this milestone: EDDC/3312 at `0x020039fc`, 43,289,460
instructions and 346,315,688 ns. Vendor `r3 = h[++r1=r3] (s)` is reached in the table lookup.
Establish exact halfword indexing, sign extension and writeback from pinned
load/store facts plus independent probes. Preserve existing word/indexed forms,
explicit alias limitations and fault ordering until evidence supports changes.
Caches use `after-signed-register-lt-branch` and its `-generic` label; main repo
`.deps/qemu-signed-register-lt-branch-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 1/0; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_register_lt_branch.py
```

### EDDC signed pre-indexed halfword load checkpoint

Exact scalar EDDC kind2 uses an unscaled incoming base+index sum modulo32,
writes the effective address to base, then loads a signed aligned halfword.
Destination bits12..15, index bits8..11 and base bits4..7 follow direct vendor
EDDC/3312 and independent probes. No exact constructor exists in the pinned
Apache SLEIGH; analogous word/byte rules are not direct opcode authority.
An address temporary preserves every alias: when destination=base, the loaded
value wins. Helpers, classifiers, neighboring forms and old validators stay fixed.

The focused gate passes 117 reference cases, one generic replay and 22 model
faults. All register fields and aliases, sign/scale discrimination, wrapped and
negative indices, odd operands with aligned sum, XIP/protected reads, PSR/RETS,
count, arms and the actual EDDC→ED54 pair are checked. Six data faults retain
modeled partial base writeback without a loaded result or retirement; a PC guard
and 15 deferred kinds preserve incoming registers. Reference faults establish
only PC/address/width/read and expose no CPU state. Hardware fault ordering
remains unverified. Deferred kind0 is reference-valid unsigned and its full
completion is retained; rejection is current model scope, not hardware invalidity.

Separate research retains 327 canonical reference probes. The unchanged word
pre-index gate passes 14 reference cases, one generic replay and 15 faults.
Full ISA, 98 profiles, ten IRQ cases and boot pass. Unchanged firmware advances
2,712 instructions to E86C; renamed captured state except profile and sample/LCD
bytes match. Whole SRAM is not compared across initialization modes.

At this milestone: E86C/3704 at `0x02003aac`, 43,292,172
instructions and 346,337,384 ns. Vendor `[r3+4] <<= 7` is reached while scaling interleaved audio samples.
Establish exact memory word-shift fields, supported modes, flags and read/write
fault phases independently. Preserve existing RMW families and I/O fencing.
Caches use `after-signed-preindexed-halfword-load` and its `-generic` label; main repo
`.deps/qemu-signed-preindexed-halfword-load-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 1/0; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_preindexed_halfword_load.py
```

### E86C word memory left-shift checkpoint

Exact scalar E86C low2 mode0 reads an aligned little-endian word at incoming
base+(x&252), shifts it left by count nibble0..15 and writes it once. Address
arithmetic wraps modulo32; all GPRs and PSR are preserved, with one retirement
only after a successful store. Count zero still performs both accesses. The
local I/O fence prevents repeated MMIO reads during replay; common helpers,
classifiers, older RMW families and device code remain fixed.

The exact constructor is absent from the verified pinned Apache slaspec and
included instruction sources. Direct vendor E86C/3704 and separate executable
probes supply encoding/semantics authority. Research retains 473 canonical left
successes and 32 full right-mode completions: logical-right mode2 and arithmetic-
right mode3 are valid in the reference and remain deferred in this left-only
milestone. Low2 bits are modes, not high count bits. Reference access failures
establish metadata but expose no CPU fault snapshot. Read-before-write/no-
retirement fault handling is model policy; MMIO read effects are not rolled
back. Hardware fault ordering and atomicity remain unverified.

The focused gate passes 189 separate-reference comparisons, one default-loader
replay, one model-only USB check and 13 faults. Data/count boundaries, all base
fields and aligned offsets, PSR, selected/skipped conditionals with following IF,
incoming base outside SRAM with mapped effective address, last SRAM word and
the actual adjacent store are checked. Count0 unaligned, XIP and write-guard
faults prevent eliminating either access. Captured fault metadata distinguishes
four failed reads and four failed writes; PC and mode prechecks precede access.
The existing diagnostic USB counters observe exactly one poll read and one
identity repost, without adding state fields or claiming hardware equivalence.
Adjacent signed pre-index halfword and full ISA/profile/IRQ/boot gates pass.

The initial 100-million-instruction replay completes 79 audio IRQ11 returns
and 3,944 timer IRQ63 returns. Extending the bound to 200 million reaches the
first missing SAR control write after 116,552,281 instructions and
932,418,256 ns, with 101 audio returns/acknowledgments and 5,104 timer returns.
Audio samples remain zero and home frames remain absent. Renamed unchanged generic-image captured
state except profile and all captured sample/LCD bytes match; whole SRAM is not
compared across different initialization modes.

At this milestone: F123/2800 +60A1 at `0x020059a8`, 116,552,281
instructions and 932,418,256 ns. The first ADC control write 0 to `0x13100` is reached in the compact store tail
at `0x020059ac`, inside the parallel bundle whose reported PC is `0x020059a8`.
CPU encoding is supported; the register mapping is missing. The head would
form WLA address `0x11900`, whose following read at `0x020059ae` has not yet
executed. Implement reviewed generic SAR/analog ownership and board inputs.
Caches use `after-memory-left-shift` and its `-generic` label; main repo
`.deps/qemu-memory-left-shift-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 101/101; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_memory_left_shift.py
```

### SAR ADC and canonical analog ownership checkpoint

A private resettable SysBus SAR controller now owns CON/RES at `0x13100`;
a separate analog component canonically owns WLA_CON0 at `0x11900`. Board
callbacks bind channels 3/PB1 and 4/PB6 to raw32 inputs 600/512 unchanged. These
fixed maps and topology are shared by every image. No CPU, Rust, firmware or
existing capture schema changes were needed.

Primary SDK facts establish RW32/RO32 registers, divider6=96, startupF=120 ADC
clocks, EN/IE/kick/pending fields and channel wiring. Source clock, bit3 meaning,
resolution and precise command/reset behavior remain unproven. Functional
choices are explicit: analog application handoff zero, local cold zero, 10 us virtual
completion, payload bit6 reads zero/clears pending and starts only when enabled;
bit7 writes are ignored. Enable alone starts nothing. Busy kick latches a new
sample and deadline after full validation; identical no-kick writes preserve
phase, busy reconfiguration without kick faults, supported idle changes retain
pending/result. Disable cancels work while retaining result/pending unless a
kick clears it. IRQ24 and unsupported channels/configurations remain explicit
faults. WLA bit14 changes require canonical ownership and reject while busy;
other initial fields survive. SAR reset cancels and clears only local state,
preserving analog/provider/wiring; unrealize unregisters its validator.

The reviewed gate passes 76 model probes: seven positives, 28 faults, seven local
reset, 21 profile/layout/name/observer-off and 13 parser cases. Existing SRAM
readbacks verify driver sequencing, repeated/RMW kick and pending, functional
deadlines, cancellation/restart, idle changes, shared-field/provider preservation
and unrelated ALNK/TIMER5 service. Fatal ADC poststate, provider failure and
deadline overflow remain source-reviewed only. Repeated/max reset schedules
check visible final state, without per-event callback-count attestation. An
initial isolation fixture used unsupported TIMER5 control 1; only that fixture
was corrected to 9/8009, and all 76 cases then passed. Production remained frozen.
Full ISA, 98 profiles, 10 IRQ, boot, syscon, ALNK reset and audio-device gates pass.

Unchanged firmware advances 2,571 instructions through ADC initialization to
F1F4 at `0x0200e2fc`, with 101 audio and 5,105 timer returns. Renamed generic
captured state except profile and all captured sample/LCD bytes match; whole SRAM
is not compared across initialization modes. Samples remain zero and home
remains unverified. No hardware calibration or all-firmware claim is made.

At this milestone: F1F4/0011 +2B81 at `0x0200e2fc`, 116,554,852
instructions and 932,438,824 ns. Vendor `r0 = r1 / r0 (s)` is paired with `[sp+44] = incoming r1` in the
foreground master-input smoothing path. Scalar signed division already works;
research exact parallel admission, incoming snapshots, modes, flags and fault
phases before changing the shared classifier. Preserve existing helpers.
Caches use `after-adc` and its `-generic` label; main repo
`.deps/qemu-adc-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 101/101; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_adc.py
```

### F1F4 parallel signed division checkpoint

The sole production change is an exact E1F4 signed-mode1 destination mask in
parallel classification. Scalar division, helpers, incoming GPR snapshots,
tail-first bundle execution, instruction sizing and all other classifiers remain
unchanged. The same rule admits signed division as an extended eight-byte tail.
Pinned primary constructor and vendor operands establish B numerator / C
denominator; the nearby primary prose reverses B/C and that discrepancy is retained.

Separate standalone research passed 424 full-state completions in 450 probes,
covering all numerator/denominator fields, aliases and signed boundaries. The
reviewed focused gate passes 134 reference positives, one default-loader replay
and 35 modeled faults, including balanced six/eight-byte conditional arms followed
by a new IF, the reached stack-store/square/shift sequence and both extended-tail
roles. Full ISA, 98 profiles, IRQ, boot, parallel ABS and signed minimum pass.

Prechecks reject unsupported modes and conflicting destinations before tail
effects. Tail access faults precede division. Zero/overflow in a division head
retains successful tail stores/GPR/PSR effects without a quotient or retirement;
a faulting division tail prevents head effects and retirement. Reference fatal
exits expose no CPU snapshot, so this fault ordering remains model policy.
Unsigned parallel mode0 and overlapping destinations are reference-valid but
remain deferred; malformed modes2..15 failed the reference without establishing
hardware invalidity. Generic replay uses configured observers and compares
captured state/sample/LCD bytes rather than whole SRAM.

Unchanged boot advances 54 instructions to FF41 at `0x0200db62`, with audio
entries/returns 101/101 and timer 5,105/5,105. HOME frames and nonzero samples
remain absent; no firmware patch or hardware compatibility claim is made.

At this milestone: FF41/0100/FCF0 at `0x0200db62`, 116,554,906
instructions and 932,439,256 ns. Vendor `if (r0 != r1) goto -1568` targets `0x0200d548`.
The current scanner reports a four-byte access for this six-byte instruction.
Establish exact primary fields, displacement base, predicate boundaries and
independent reference behavior before changing the shared CPU paths.
Caches use `after-parallel-signed-divide` and its `-generic` label; main repo
`.deps/qemu-parallel-signed-divide-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 101/101; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_parallel_signed_divide.py
```

### FF41 long register-inequality branch checkpoint

Exact canonical FF41 now compares the full 32-bit GPRs in operand bits12:15
and 8:11, preserves all registers/PSR/RETS and branches by twice the signed 16-bit displacement
in its third word, relative to PC+6. Its primary constructor explicitly requires a zero low byte.
Exact scanner sizing is six bytes. A dedicated internal family guard rejects a
final selected THEN with ELSE before retirement/branch effects; existing FF0C,
CALL and common predicate helpers and all classifiers remain unchanged.

Pinned primary and vendor fields agree. Separate research retained 328 probes,
305 expected full-state completions, scanner/conditional disagreements and all
nine reference-valid nonzero low bytes. The reviewed focused gate passes 73
reference positives, 13 independently expected six-byte model positives, one
default-loader replay and 22 modeled faults. Full ISA, 98 profiles, IRQ, boot,
long signed-literal and signed-register-LE regressions pass.

The independent executable scans FF41 as four bytes in conditional arms,
executing a skipped zero displacement as an extra NOP or faulting on third word 4.
Primary six-byte expectations check markers, counts and following IF separately,
with raw contradictions retained. A guarded final THEN exit faults at FF41
before count; other taken exits preserve the inherited following-IF fault after
branch retirement. Retained predicates can block IRQ admission by inspection;
no IRQ validation of those exits is claimed. Canonical low-byte and neighboring
FF40/42/43/48/49 rejection are model admission/deferred policies despite valid
reference completions. True 32-bit PC-wrap execution, hardware conditional and
fault behavior remain unverified. No persisted capture schema changes.

Unchanged boot advances 44 instructions to F194 at `0x020086c4` in btn_hold.
HOME=1 and ui_frames=1, but stage=1 and the LCD remains busy; drawing has not
completed. Audio/timer entries and returns remain 101/101 and 5,105/5,105, with
zero nonzero samples. Renamed generic captured state and all sample/LCD bytes
match, without whole-SRAM comparison across initialization modes.

At this milestone: F194/1152 +6004 at `0x020086c4`, 116,554,950
instructions and 932,439,608 ns. Vendor `r1 = r5 & (1 << r1)` is paired with `r4 = [incoming r0]`
in `btn_hold`, reached through HOME input processing. Scalar E194 exists;
research exact mode2 parallel admission, index and incoming/tail semantics
before changing the classifier.
Caches use `after-long-register-ne-branch` and its `-generic` label; main repo
`.deps/qemu-long-register-ne-branch-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 101/101; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_long_register_ne_branch.py
```

### F194 parallel indexed-bit AND checkpoint

The sole production change is exact E194 indexed-bit AND mode 2 admission in
parallel classification. Scalar bit operations, the index<32 guard, helpers,
incoming GPR snapshots, tail-first order, predicates, sizing and all other
classifiers remain unchanged. Both six-byte heads and eight-byte extended
indexed-bit tails use the shared machinery.

Pinned logicops constructor and vendor operands agree. Separate research has
546 probes, 525 verified completions and no remaining expectation failures.
Thirty-six out-of-range reference completions wrap indices modulo 32, while
the inherited model explicitly rejects unsigned indices at least 32. That is
qualified unsupported behavior, not a hardware-invalid claim or new scalar
semantics. An initial extended-tail research fixture encoded the wrong source
and index. Its original binary/snapshot and correction are retained; the valid
fixture proves that the head uses its old source after the tail replaces it.

Mode/conflict prechecks run before the tail; tail access faults precede the
head index check. A bad head index preserves completed tail store/GPR/PSR
effects before a normalized E194 fault, without result or bundle retirement.
A bad extended-tail index prevents the head and retirement. Modes 0/1/3 and
overlapping destinations remain reference-valid but parallel-deferred or
conservatively rejected. Modes 4..15 failed the reference without fault-state
snapshots. Hardware fault behavior and inherited predicate/IRQ limits remain
unverified. Default-loader replay uses configured observers; existing capture
schemas are preserved.

The focused validator contains 187 reference-positive comparisons, one
default-loader replay and 38 modeled faults. Sixteen model-policy fault cases
also retain separate successful reference states. All 203 independent private
reference expectations were checked before parent QEMU acceptance.

Focused acceptance and full ISA/profile/IRQ/boot plus adjacent signed-division
gates pass. Unchanged boot advances 241 instructions through ui_input to the
SP-relative byte memory failure. The renamed default-loader replay matches
every captured state field except profile and all LCD/latest-half sample bytes;
whole SRAM is not compared across differently initialized loader modes.
HOME flag/frame1/stage2 does not establish a completed HOME draw.

At this milestone: E9DE/814A at `0x0200db94`, 116,555,191
instructions and 932,441,536 ns. Vendor `b[sp+330] = r8` is reached in `fm1_cstart` after HOME input
processing. Establish exact SP-relative byte fields and canonical policies
from pinned primary and independent reference before implementing it.
The HOME flag and first frame count are set, but stage2 precedes completed drawing.
Caches use `after-parallel-indexed-bit-and` and its `-generic` label; main repo
`.deps/qemu-parallel-indexed-bit-and-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 101/101; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_parallel_indexed_bit_and.py
```

### E9DE SP-relative byte store checkpoint

The sole production change is a separate exact E9DE scalar path: take the
source GPR from operand bits 12..15 and the unsigned byte offset from all
twelve low bits, add it to the special SP with wrapping 32-bit arithmetic,
and store the low byte. Offset bit 0 is data, not a load/store selector.
There is no GPR/SP/PSR writeback; sizing is four bytes and retirement is once.
Existing E9D0/E9D4 word/dword paths, helpers, parallel classification and
common conditional machinery are unchanged.

Pinned Apache stack constructor lines 421–424 and vendor E9DE/814A agree.
Neighbor E9DC/E9DD byte loads and E9D8 halfword memory remain outside this
exact scope. Fault-state expectations describe the existing model; the
reference provides no fault-state snapshots and hardware behavior is unknown.

Standalone research has 89 probes, 82 verified completions and no expectation
mismatches. A precise byte-guard discriminator permits an adjacent byte
while the intersecting byte fails. Reference access failures establish computed
address, one-byte width and write category only. Its PC-guard fixture completes;
the QEMU fetch-policy gate remains qualified independently. Successful wrapping
into mapped memory is unavailable in this address map.

The focused validator has 78 reference positives, one generic replay and
11 modeled faults. Four separate full reference completions document the
three deferred valid neighbors and the PC-guard policy disagreement. Seven
reference fatal calls supply access/rejection categories without fault state.
All 89 private fixture expectations were checked before parent acceptance.

Focused acceptance and full ISA/profile/IRQ/boot plus adjacent indexed-bit
gates pass. Unchanged firmware executes the store and advances one instruction
to E9D8. The renamed default-loader replay matches every captured state field
except profile and all LCD/latest-half sample bytes. Whole SRAM is not compared
across different initialization modes. HOME flag/frame1/stage2 remains
incomplete drawing; audio/timer service returns successfully but samples are zero.

At this milestone: E9D8/8149 at `0x0200db98`, 116,555,192
instructions and 932,441,544 ns. Vendor `h[sp+328] = r8` is reached immediately after the accepted byte
store in `fm1_cstart`. Establish exact E9D8 load/store constructors, unsigned
offset masking, halfword semantics and model fault phases before implementing.
HOME flag/frame1/stage2 still precedes a completed drawing.
Caches use `after-sp-relative-byte-store` and its `-generic` label; main repo
`.deps/qemu-sp-relative-byte-store-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 101/101; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_sp_relative_byte_store.py
```

### E9D8 SP-relative halfword memory checkpoint

The exact E9D8 scalar family uses operand bit 0 to select store or unsigned
load. Operand bits 12..15 select an ordinary GPR; the unsigned byte offset
is the low twelve bits with bit 0 cleared. A temporary wrapping SP+offset
address passes a two-byte aligned access through existing helpers. Stores
write only the low sixteen bits; loads zero-extend. SP/PSR/RETS are preserved,
with only the load destination GPR updated. Sizing is four bytes and
retirement is once. Existing E9D0/E9D4/E9DE paths, parallel classification,
helpers and common predicate machinery remain unchanged.

Pinned Apache stack store and unsigned-load constructors agree with vendor
E9D8/8149. Other neighboring scalar opcodes remain outside this scope.
The earlier byte-store gate retires only its E9D8 deferred negative and
related counts/claims/dead expected-value branch; its 78 successful cases
and other fault checks are preserved. Original frozen byte-store validator
and its 11-fault milestone remain retained as historical acceptance evidence.

Initial research recorded 130 probes and 115 verified expected completions,
with no mismatches; the sampled E9D9 completion was initially unverified. A reference-valid E9D9 example is retained but its
semantics lack pinned primary support; no signed-halfword claim is made.
Two PC-guard fixtures complete in the reference, so their QEMU rejection
remains qualified model policy. Fatal access records establish address,
two-byte width and read/write category without fault-state snapshots.
Successful wrapping into mapped memory and hardware fault state are unknown.

The focused validator contains 113 reference positives, one default-loader
replay and 17 modeled faults. Three separate complete reference states
document two PC-guard differences and one primary-unverified E9D9 sample.
Fourteen reference fatal calls provide access/rejection categories only.
All 130 final private calls are checked (116 sampled complete states plus
14 fatal categories), distinct from the preserved initial 115 expected
research completions and initially unverified E9D9 snapshot. Checking that
single sample does not establish general E9D9 semantics or signedness.

Focused acceptance and full ISA/profile/IRQ/boot plus adjusted adjacent
byte-store gates pass. Unchanged boot advances 1,015,870 instructions into
ui_draw and stops at EA13. Audio IRQ entries/returns are 103/103 and timer
5172/5172; LCD pixels/transfers have progressed, but HOME frame1/stage3 is
incomplete. Renamed default-loader replay matches every captured state field
except profile and all LCD/latest-half sample bytes; whole SRAM is not
compared across different initialization modes. Samples remain zero.

At this milestone: EA13/0180 at `0x0200a842`, 117,571,062
instructions and 940,568,504 ns. Vendor `if ((r3 & r1) != 0) { r9 = 2; }` is reached inside `ui_draw`.
Establish the exact register-bitwise-AND conditional constructor, selector
fields and THEN/ELSE sizing before implementing through existing predicate
machinery. HOME flag/frame1/stage3 and increased LCD transfers show drawing
in progress, without a completed frame.
Caches use `after-sp-relative-halfword` and its `-generic` label; main repo
`.deps/qemu-sp-relative-halfword-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 103/103; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_sp_relative_halfword.py
```

### EA13 register-bitwise-AND IF checkpoint

The production change is limited to kind A1 register-bitwise-AND IF:
accept canonical low bytes 00 and 80, compute the existing masked condition,
and select equality or inequality with zero from bit 7. The inherited
zero-form behavior, operand fields, latched condition, arm scanner/helper,
sizing/count, common predicate state and IRQ rules remain unchanged.
THEN count is 1..4; ELSE count is 0..3. No zero-length THEN is encoded.

Pinned primary constructors and vendor EA13/0180 agree on the nonzero form.
Standalone research has 570 probes and 566 verified expected completions
without mismatches. Eighteen noncanonical low-byte reference completions
are retained separately; primary constrains the full low byte to 00 or 80,
and QEMU preserves explicit rejection of other patterns. These are model
canonical policies, not a hardware-invalid claim. Four inherited nested,
final CALL, final FF0C and taken-exit outcomes retain raw reference
completions, without changing existing modeled restrictions.

The primary nonzero constructor has an erroneous duplicated equality
comment; its explicit inequality form/body and the vendor agree. Eight
supplemental calls retain the actual captured values, precise inherited
FF41 outcome, two reference-valid PC-guard completions and four fatal
access categories. These are separate from the original 570-probe research.

A private inherited CALL discriminator initially masked an even callee
address with 1 and therefore skipped the intended selected call. Its raw
exploratory record is retained; the validator corrects the compared right
operand to all ones so the same call target keeps THEN selected. This is
a fixture correction, not a production or helper change. The original
skipped result is not used as selected-CALL fault-policy evidence.
The corrected call target is 0x020001d6 with right operand 0xffffffff.
The reference completes 36 instructions and updates RETS; the existing
model-policy expectation faults at CALL 0x020001c8, count 31, fetch span
two bytes, before CALL effects. Hardware fault state remains unverified.

The private final gate check retained 289 oracle calls: 281 full-state
comparisons, four fatal-access categories and four raw inherited outcomes.
The first parent QEMU run passed all 260 positives plus generic replay,
then exposed a fault-test expectation mixing poisoned reference SRAM
with the alnk-probe cold-zero profile. Only the QEMU fault memory
expectation is corrected; failed evidence and reference initialization
are retained. Production decoding and fault phase remain fixed.

At this milestone: ECD0/684E at `0x0200996c`, 117,588,663
instructions and 940,709,312 ns. Vendor `r6 = [++r4=140]` is reached in `cv_rect` during HOME drawing.
The preceding literal sets r4 to `0x01c116f0`. Establish exact primary
constructor, byte displacement, direction, pre-update and alias semantics.
HOME frame1/stage3 and idle LCD are an intermediate draw snapshot,
not a completed HOME endpoint.
Caches use `after-register-and-if` and its `-generic` label; main repo
`.deps/qemu-register-and-if-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 103/103; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_register_and_if.py
```

### ECD0..7 signed immediate pre-indexed word load checkpoint

The dedicated scalar branch admits the single pinned addldw constructor:
ECD0..7 with operand low two bits equal 2, signed eleven-bit aligned byte
displacements from -1024 through +1020, and distinct destination/base.
It captures incoming base plus displacement in a temporary address,
updates the base, then loads one aligned little-endian word and advances
four bytes. Existing shared immediate word/doubleword kinds0/1 and all
pre-index stores remain unchanged, as do helpers/classification/predicates.

Original standalone research retains 187 calls: 134 verified exploratory
completions (127 new non-alias loads, four unchanged controls and three
separate deferred-store probes), five fatal-access categories and 48 destination/base alias
contradictions at offsets 0/+140/-4 across all sixteen GPRs. Primary
loads last for an alias; the standalone reference writes the address last.
All original mismatches remain explicit. The production branch rejects
that unresolved alias before effects/access/count rather than choosing
an unverified ordering. This is a model limit, not hardware invalidity.

Base writeback before a failed data access is the existing pre-index
model policy consistent with the primary constructor. The reference
fatal records contain no CPU fault snapshot and establish only access
category/address/width/direction. Hardware fault state remains unverified.
Mapped small-offset wrapping success is unavailable on this board;
wrap-to-zero/top unmapped failures do not prove a successful wrap.

Separate checks of 48 saved alias snapshots characterize reference
writeback-wins without additional oracle calls or relabeling originals.
Eight supplemental calls add six full completions and two wrap-access
fatal categories: 195 cumulative exploratory calls. Selected/skipped
aliases and reference-valid PC-guard completions stay separate from
accepted successful non-alias instruction behavior.

One separate EC50 kind2 reference probe is explicitly unsupported,
bringing exploratory calls to 196. Final private gate checking is
separate: 109 calls with 81 positives, 21 policy completions, six fatal
access categories and one unsupported EC50 outcome. Review strengthened
only the six fatal category assertions; all saved outcomes agree,
with fixtures/counts/source unchanged and original validator retained.

At this milestone: ED00/101B at `0x02009986`, 117,588,670
instructions and 940,709,368 ns. Vendor `ifs (r1 >= r0) goto 54` targets `0x020099c0` from
`0x02009986` in cv_rect. Establish the exact signed comparison, operand
fields and displacement relative to the four-byte instruction end.
The successful new load advances seven instructions; HOME frame1/stage3
still represents unfinished drawing.
Caches use `after-preindexed-word-immediate` and its `-generic` label; main repo
`.deps/qemu-preindexed-word-immediate-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 103/103; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_preindexed_word_immediate.py
```

### ED00 signed register greater-or-equal branch checkpoint

The source adds only exact ED00/FFF0 admission and D0 signed GE selection
through the existing four-byte compare-branch path. It compares signed32
GPR[x>>12] >= GPR[op&15], with signed9 word displacement from PC+4
(-512 through +510 bytes). PSR, RETS and special stack-pointer behavior
are unchanged; GPR14 is checked against separately seeded special SP.

Pinned primary and vendor ED00/101B agree on the constructor. Captured
r1=5, r0=13 falls through
from 0x02009986 to 0x0200998a; the encoded taken target is 0x020099c0.
The current x&0E00 canonical restriction remains model policy because
primary does not constrain those unused bits. Private 83 reference calls
have 70 canonical successes (54 ordinary and 16 balanced conditional),
77 total full-state completions and six rejected unused-bit outcomes
without CPU fault snapshots. The extra completed cases are unused pattern4,
two reference-valid PC guards and four taken-exit/following-IF outcomes.
These remain separately qualified model/reference disagreements.

The three earlier signed GT/LE/LT gates retire only their exact ED0E
deferred negative and update deferred1->0/total14->13 counts. Each retains
all 70 successful fixture bytes and expected states, positive generators
and other helper functions. Original validators are retained as historical
evidence. Common helpers, predicate/IRQ rules, scanner, classifier and
other opcode paths remain fixed. True32-bit PC wrap and hardware fault
state remain unverified; mapped displacement boundaries are exercised.
Taken exits may retain model predicates and fault the following IF after
branch retirement; IRQ blocking is source-inspected, without IRQ proof.

At this milestone: F435/2621 + 2603 at `0x0200998a`, 117,588,671
instructions and 940,709,376 ns. Vendor bundle `r2 = smin(r2, r6)` with tail `r3 = [sp+24]` is reached
at `0x0200998a`, the untaken ED00 fallthrough in cv_rect. The scalar E435
mode1 signed minimum is already implemented; establish exact shared
parallel destination admission, incoming operands, tail roles and fault
order before adding a narrow classifier entry. HOME frame1/stage3
remains an unfinished draw snapshot.
Caches use `after-signed-register-ge-branch` and its `-generic` label; main repo
`.deps/qemu-signed-register-ge-branch-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 103/103; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_register_ge_branch.py
```

### F435 parallel signed minimum checkpoint

The production delta adds only exact E435 mode1 destination classification
for the existing scalar signed minimum. Both head and extended-tail roles use
the unchanged incoming-GPR snapshots and tail-first execution, with one
bundle retirement. The minimum leaves PSR/RETS unchanged; flags written by a
disjoint tail survive. No helper, scanner, predicate, IRQ or scalar decoder
changes, and no firmware identity or PC selects this behavior.

Pinned Apache constructor and vendor F435/2621 +2603 agree: signed32
r2=min(incoming r2,incoming r6), paired with r3=[special SP+24]. Research
retains154 historical standalone-reference calls; the final153 fixtures have
126 canonical successes,
seven separately checked full-state model-policy completions and 20 fatal
categories without reference CPU fault snapshots. Supported coverage checks
all16 destination fields and source aliases, signed extrema/equality,
incoming store values/addresses, overwritten sources, flag-writing tails,
two extended-minimum tail roles and16 balanced selected/skipped conditional
contexts. Independent review replaced one nondiscriminating tail-source
fixture with a decisive old7 versus updated8 test; both original and new raw
outcomes are retained. Mode0, conflicting destinations and deferred unsigned minimum /
signed maximum tails are reference-valid but remain explicitly unsupported
by current parallel scope. PC guard admission is model policy.

The focused gate checks126 successes, one generic default-loader replay and
27 modeled faults:15 mode prechecks, four conflicts/deferred tails, six
width4 tail accesses and two full-bundle PC guards. Fault phase/order and
no-retirement assertions are POC policy, without hardware rollback proof.
The old scalar minimum gate retires only its newly obsolete F435+NOP
negative/count16->15; all140 positive bytes/expected states and non-main
functions remain unchanged. Original gate/evidence are retained.

At this milestone: ED58/3E44 at `0x0200aa44`, 117,599,142
instructions and 940,793,144 ns. Vendor `r3 = h[++r4=228] (u)` is reached at `0x0200aa44` after
text rendering in the first HOME draw. Establish exact unsigned-halfword
pre-indexed load fields, signed/aligned displacement, nonalias policy and
writeback/access fault phases from vendor and separate reference. The pinned
load/store primary lacks this exact constructor; adjacent ED50/54 and ECD0
forms are analogues only, so broader displacement/alias behavior requires
discriminating evidence.
Preserve the old halfword paths and deferred aliases/stores pending evidence.
HOME frame1/stage3 remains an unfinished draw snapshot.
Caches use `after-parallel-signed-minimum` and its `-generic` label; main repo
`.deps/qemu-parallel-signed-minimum-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 103/103; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_parallel_signed_minimum.py
```

### ED58..5B unsigned halfword pre-indexed immediate load checkpoint

A separate ED58..5B decoder branch admits only unsigned-halfword loads
with distinct destination/base GPRs and operand bit0 clear. It uses signed2
first-opcode lowbits times256 plus x8..11 times16 plus x&14, giving signed
even-byte offsets -512 through +510. A temporary incoming-base EA is copied
to the base before the modeled aligned LE16 read; result is zero extended,
PC advances4 and the instruction retires once. Old memory paths, helpers,
classifier, scanner, predicate, IRQ and state schemas remain unchanged.

The exact pre-indexed halfword constructor is absent from pinned Apache
SLEIGH; ED50 plain-load and ECD0 word pre-index forms are analogues only.
Vendor ED58/3E44 and discriminatory standalone-reference probes establish
the accepted unsigned-load fields and signed offsets. The actual reached
base01c116f0 plus228 selects01c117d4, containing33808. All16 destination /
base fields, ordinaryGPR14 versus specialSP, unsigned boundaries, signed
extrema/offset bits, last SRAM halfword, XIP, permitted guard reads and
balanced selected/skipped mixed2/4/6/8-byte contexts are checked.

Research retains151 historical calls:10 initial neighbor probes,140
original matrix fixtures and one final selected-alias fixture, with145
historical sampled full-state completions and six
fatal access categories without CPU fault snapshots. All48 aliases leave
the address in the reference; with no exact primary/hardware alias contract,
pre-effect alias rejection is conservative model admission policy. Stores
remain deferred: negative-load-offset store probes instead use unsigned
highbits in the reference. The original wrong store expectation and seed
metadata annotation are retained with accurate separate characterizations;
outside-inspection store target data is not directly observed.

Final109 private fixtures have103 full sampled reference completions and
six categorized fatal outcomes;108 fixtures reuse saved records, with one
new selected-alias check. The final focused gate checks74 supported cases,
one generic default-loader
replay and35 modeled faults:18 aliases, five read2 faults after modeled
writeback, two full4-byte PC guards, five deferred stores, four signed
neighbors and one genuine six-byte C000+ED58 parallel classifier deferral.
FD58 is an existing scalar branch and is not mislabeled parallel. Hardware
alias/fault order, rollback and successful32-bit wrap remain unverified;
reference fatals establish category only. No old tracked negative retires.

At this milestone: F040/0165 + 624A at `0x0200aa52`, 117,601,373
instructions and 940,810,992 ns. Vendor `r0 = 357` paired with `r2 = h[r4+4] (u)` is reached at
`0x0200aa52`, after the accepted ED58 load and icon call. Scalar literal and
compact halfword load already exist. Establish exact shared destination
classification from pinned compact-load constructor and reference probes,
with incoming addresses, disjoint destinations and tail fault phases.
HOME frame1/stage3 remains an unfinished draw snapshot.
Caches use `after-preindexed-unsigned-halfword-immediate` and its `-generic` label; main repo
`.deps/qemu-preindexed-unsigned-halfword-immediate-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 103/103; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_preindexed_unsigned_halfword_immediate.py
```

### compact unsigned halfword load tails in parallel bundles checkpoint

The shared parallel destination classifier admits compact unsigned
halfword loads matching `(op & 0xe088) == 0x6008`, with destination mask
`1u << (op & 7)`. This is the sole CPU source addition. The scalar decoder
remains unchanged: aligned little-endian 16-bit memory is read through the
incoming low-bank base in bits 4 through 6, with signed five-bit displacement
times two (-32 through +30 bytes), and the result is zero extended. There is
no base writeback or PSR update. Ordinary base/destination aliases therefore
remain supported.

Admission is confined to tails under existing two-byte and four-byte heads,
forming four-byte and six-byte bundles. Compact group 3 cannot be a head
under the existing and primary prefix rules. No new eight-byte role is
admitted. Prefix normalization, scanning, scalar execution, predicate, IRQ,
device and state schema paths remain unchanged. Existing bundle machinery
captures the incoming tail address before head effects and requires disjoint
head/tail destinations.

The pinned compact-load constructor directly supports the exact load-only
mask, fields and unsigned halfword semantics; unlike the preceding ED58
milestone, this constructor is present. The reached F040/0165 + 624A pair
sets r0 to 357 and loads r2 from h[r4+4] without writeback. Its captured
incoming r4 is 0x01c117d4, selecting 0x01c117d8 with sampled halfword 59164.
These test inputs do not select CPU behavior by firmware identity or PC.

Saved separate-reference research contains 86 calls: 74 canonical supported
cases (56 ordinary, 16 balanced conditional cases and two skipped bad-EA
cases), two destination-conflict policy completions, two PC-span policy
completions and eight categorized fatal two-byte reads. There are 78 full
sampled state completions and no canonical mismatch. Coverage includes all
eight destination, base and ordinary alias fields; unsigned boundaries;
signed offset extrema and intermediate fields; incoming-address/result
ordering; head flag preservation; scalar controls; last SRAM halfword, XIP
and guarded reads; and final/nonfinal THEN/ELSE four-byte and six-byte bundles
followed by another IF. Skipped invalid addresses are not read. Seeded full
words are checked; unowned reference words retain poison while cold QEMU
fixture/default-loader neighbors are zero, so no whole-memory equality is
claimed.

The focused gate covers 74 supported cases, one generic default-loader
replay and 12 modeled faults: two destination conflicts, eight tail reads
and two complete four-byte/six-byte PC-span guards. The reference completes
conflicts with head-wins behavior and completes the configured PC guards;
existing QEMU pre-effect rejection is conservative model policy, not proof
of ISA invalidity. Tail data reads precede head execution and bundle
retirement in the model. A flag-writing head therefore leaves GPRs, PSR,
memory and count unchanged after a modeled tail-read fault. Reference fatal
outcomes establish read category, address and width only; they expose no CPU
poststate and do not validate hardware ordering, rollback or fault priority.
The outer model bundle PC and inner reference tail PC are recorded separately.
Only the obsolete ISA deferred-halfword-load tuple F101/3020 + 603A retires;
all other fixture definitions and unrelated functions remain unchanged.

The parent build and seven serialized acceptance gates pass on the approved
freeze: full ISA, CPU profiles, Felucca IRQ, boot, ED58 unsigned pre-indexed
halfword, signed halfword and the new focused parallel gate. The latter
passes 74 supported cases, one generic replay and 12 modeled faults with
a per-fixture MAX of 100. The initial runner naming error is preserved
separately; its corrected CPU-profile invocation passed.
Unchanged firmware under a 200,000,000-instruction bound advances 4,949
instructions from F040 to 0111 TBH at `0x02009bbc`, after 117,606,322 retired
instructions and 940,850,584 ns. Renamed generic loading matches all captured
JSON state except the profile label, exact LCD PPM bytes and latest ALNK-half
bytes. Whole SRAM is not compared across poisoned fixture and cold generic
initialization. Audio entries/returns are 103/103 and TIMER entries/returns
are 5,175/5,175, with guard messages and watchdog expirations zero. The LCD
is busy at debug stage4/home1/frame1; bootguard is still pending at 940 guest
ms. All 55,296 captured sample words are zero. Completed HOME, synthesis,
sustained 30 seconds, physical-input acceptance and native execution remain
unverified.

At this milestone: 0111 (TBH) at `0x02009bbc`, 117,606,322
instructions and 940,850,584 ns. After the F040/0165 + 624A compact unsigned halfword tail is admitted,
unchanged firmware advances 4,949 instructions to the compact 0111 TBH
instruction at `0x02009bbc` during HOME drawing. Exact compact table-branch
semantics remain the next read-only research scope. Captured debug stage is
4 with home=1 and ui_frames=1, but the LCD is busy; this is an unfinished
HOME draw, not a completed frame. Guest ms=940, scan frames=470, bootguard
failed=0/pending=1 and all captured audio sample words remain zero.
Caches use `after-parallel-compact-unsigned-halfword` and its `-generic` label; main repo
`.deps/qemu-parallel-compact-unsigned-halfword-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 103/103; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_parallel_compact_unsigned_halfword.py
```

### compact table halfword branch checkpoint

The scalar TBH family uses exact mask `(op & 0xfff0) == 0x0110`.
Low four bits select any of 16 ordinary GPRs as an unscaled byte index.
An unsigned LE16 entry is read at wrapping PC+2+incoming index; the target
is wrapping PC+2+(entry << 1). GPRs, PSR and RETS are preserved.

Pinned primary evidence supports these fields and effects. Prior 0111/r1=8
selects entry 0056 at `0x02009bc6` and target `0x02009c6a`. Seven scalar lines
read before retirement, then use existing count/dynamic-jump handling.
Helpers, prefix/scanner, parallel classifier, predicate, IRQ, devices,
schemas and all old validators remain unchanged.

Saved research has 69 calls: 55 canonical matches plus four taken-exit,
two TBB and two PC-policy completions give 63 original expected matches.
Five fatal read categories expose no CPU fault state. C111 originally
retired 53 versus predicted 38; one separate full-state characterization
reuses that record, giving 64 full completions. The retained mismatch and
extra 15 NOPs fit a sequential walk, without path or hardware proof.

Independent source/validator review and all seven parent gates pass.
The focused gate passes 55 supported cases, one generic replay and 14 faults:
five reads, two PC stages, four inherited exit/following-IF limits, two TBB
neighbors and one C111 deferral. Taken-exit references complete; model IF
faults follow TBH retirement under unchanged predicate policy. Alignment is
sampled MO_ALIGN model policy; true top-PC wrap execution is unverified.
Every fixture uses MAX 100; positive QEMU/reference processes each allow
60 seconds, faults/generic and copied research 15 seconds per process.
The validator has no internal aggregate deadline. Full detail is in evidence.

Unchanged boot under MAX 200,000,000 advances 804,786 instructions to
E9DC/7034 at `0x0200a104`, 118,411,108 instructions and 947,288,872 ns.
Debug stage 4/home 1/frame 1 and visible idle LCD do not prove completed HOME.
Renamed generic captured state except profile and exact PPM/latest ALNK
agree; whole SRAM is excluded. All 55,808 captured sample words are zero.
Completed HOME, 30 guest seconds, physical input, synthesis and native
execution still require separate acceptance. No hardware proof is claimed.

At this milestone: E9DC/7034 at `0x0200a104`, 118,411,108
instructions and 947,288,872 ns. The unchanged bounded run advances 804,786 instructions beyond 0111 TBH to E9DC/7034, an unsigned SP-relative byte load reached during unfinished HOME bring-up. Debug stage 4/home 1/frame 1, 947 guest milliseconds and 475 scans do not establish completed HOME. The LCD is visible and idle; all 55,808 captured audio sample words are zero. Renamed generic replay agrees on captured state except profile and exact PPM/latest ALNK bytes; whole SRAM is excluded.
Caches use `after-table-halfword-branch` and its `-generic` label; main repo
`.deps/qemu-table-halfword-branch-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 104/104; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_table_halfword_branch.py
```

### unsigned SP-relative byte load checkpoint

Exact scalar E9DC loads an unsigned byte relative to special SP.
Extension high four bits select any of 16 ordinary GPR destinations; low
12 bits are an unsigned, unscaled offset from 0 through 4,095. The address
wraps incoming special SP plus offset; the byte is zero extended.
Special SP, PSR, RETS and memory have no primary writes.

Pinned primary evidence directly supplies the constructor and fields.
Prior E9DC/7034 selects r7, offset 52, special SP `0x01c79cec` and byte 67
at `0x01c79d20`. Six scalar lines implement the exact load. All other CPU
paths, helpers, scanner, classifier, predicate, IRQ and schemas stay fixed.

Saved research has 70 calls: 61 canonical plus three policy/deferred full
matches and six categorized fatal read-one results, with no mismatch.
Coverage includes all destinations, low-12 bits, byte positions, unsigned
data, SP/PSR boundaries, read permissions, XIP and conditional paths.
Fatal reference results expose no CPU fault state or hardware ordering.
Six modeled reads fault before effects; one full-fetch-four PC span rejects
before data/count. Signed E9DD and genuine C000/E9DC tails stay deferred
before effects despite full reference completions.

Only obsolete E9DC rejection/wording/count metadata in the byte-store gate
retires. Its 78 positives and one generic replay stay; faults fall ten to
nine, deferred neighbors two to one, policy completions three to two.
Preservation review fixes E9DD, other fixtures and unrelated functions.
Independent exact three-file review, build and all seven parent gates pass.
The new focused gate passes 61 supported, one generic replay and nine faults.
Each fixture MAX 100; positive QEMU/reference processes each allow 60 seconds,
fault/generic/research processes 15 seconds. No internal suite deadline.

Unchanged MAX 200,000,000 boot advances 1,906,331 instructions to EE94/5500
at `0x0200bbfc`, 120,317,439 instructions and 962,539,520 ns. Debug stage 5,
home 1/frame 1 and visible idle LCD do not prove completed HOME. Renamed
generic captured state except profile and exact PPM/latest ALNK bytes agree;
whole SRAM is excluded. All 57,344 captured sample words are zero. HOME,
30 guest seconds, physical input, synthesis and native running need acceptance.

At this milestone: EE94/5500 at `0x0200bbfc`, 120,317,439
instructions and 962,539,520 ns. The unchanged MAX 200,000,000 capture advances 1,906,331 instructions beyond E9DC to EE94/5500, a signed register <= IF reached during unfinished HOME bring-up. Debug stage 5/home 1/frame 1, 962 guest milliseconds and 487 scans do not establish completed HOME. The LCD is visible and idle; all 57,344 captured sample words are zero. Renamed generic state except profile and exact PPM/latest ALNK agree; whole SRAM is excluded.
Caches use `after-sp-relative-byte-load` and its `-generic` label; main repo
`.deps/qemu-sp-relative-byte-load-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 107/107; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_sp_relative_byte_load.py
```

### signed register <= IF checkpoint

Exact EE90/FFF0 compares ordinary GPRs with signed <=. Head low four bits
select left; extension bits 8..11 select right, with primary low byte zero.
Extension bits 14..15 plus one give THEN 1..4; bits 12..13 give ELSE 0..3.
The IF has no primary PSR writes; selected body instructions may set flags.

Pinned primary evidence supports the fields. Prior EE94/5500 compares
r4=0 with r5=1500 and selects NEG/SMAX over an ELSE move. NEG flags and
SMAX preservation are body behavior. A dedicated E9 path admits canonical
EE90; ED10 sibling, helpers, scanner, classifier and predicate/IRQ stay fixed.

Saved 137-call research retains 129 original expected matches: 117 canonical,
ten ignored-low-byte and two PC-policy completions. Four inherited full
completions are separately characterized without fresh calls; four fatal
write-four categories expose no CPU snapshot or hardware fault ordering.
Primary-backed zero-byte admission differs from ten reference completions.
Nested/final CALL/final FF0C/taken-exit limits remain explicit. Common NOR/
unmapped diagnostics use controlled EA labels, without distinct-text claims.

Independent exact source/validator review, build and all seven gates pass.
The focused gate passes 117 supported, one generic replay and 20 model faults:
ten low-byte, four inherited, two PC-guard and four selected-write faults.
All old validators and shared boundaries remain unchanged. Each fixture
uses MAX 100; positive processes each allow 60 seconds and fault/generic/
copied-research processes 15 seconds. Parent runner deadlines are separate.

Unchanged MAX 200,000,000 boot advances 5,993 instructions to F070/4600+6247
at `0x0200a2d0`, 120,323,432 instructions and captured 962,587,464 ns.
Vendor disassembly describes a byte-reversal head plus compact word-load tail.
Stage 5/home 1/frame 1 and visible idle LCD do not prove completed HOME.
Renamed captured JSON except profile and exact PPM/latest ALNK data agree;
whole SRAM is excluded. All 57,344 captured sample words are zero. Completed
HOME, 30 guest seconds, physical input, synthesis and native running stay open.

Next work inventories all known source firmware encodings/counts offline and
groups generic implementation candidates into independently reviewed batches.
Full model-policy and host artifact-recording qualifications remain in evidence.

At this milestone: F070/4600 + 6247 at `0x0200a2d0`, 120,323,432
instructions and 962,587,464 ns. The unchanged MAX 200,000,000 capture advances 5,993 instructions beyond EE94 to F070/4600 + 6247 at the current unsupported bundle stop. Vendor disassembly describes r4=rev8(r6) in the extended head with r7=[r4+8] in a compact word-load tail; exact primary/role classification belongs to the ongoing offline inventory. Stage 5/home 1/frame 1 and visible idle LCD do not prove completed HOME. Renamed captured JSON state except profile and exact PPM/latest ALNK data agree; whole SRAM is excluded and all 57,344 captured sample words are zero.
Caches use `after-signed-register-le-if` and its `-generic` label; main repo
`.deps/qemu-signed-register-le-if-2026-10-08/` retains primary/reference evidence and acceptance.
Audio IRQ11 entries/returns 107/107; home remains unverified.

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_signed_register_le_if.py
```

### Grouped Batch A checkpoint (historical)

Batch A admits 12 of its 13 reviewed forms: five scalar forms (packed
SUB, compact register ASR, ADC, SBC and special SP ADD), plus seven missing
parallel placements. The E0F0 scalar form also admits its reviewed parallel
roles. REV8 now executes in the reached F070/4600 + 6247 bundle. All paths are
generic; no firmware name, hash or PC selects CPU behavior.

Exact compact RETS push 04C8 stays an explicit fault. It lacks an exact primary
constructor, and the separate CLI rejects all 27 executed cases; six skipped
completions do not validate it. Retain this research gap rather than infer
semantics from the neighboring 0410 encoding.

Pinned primary evidence and 916 serialized copied-reference research calls
(including five SP low-bit discriminators) qualify the admissions. Carry
bodies in the primary omit carry; the reference supports widened ADC/SBC
result/flags. ASR counts at least 32 sign-fill. E8F0 admits x & E003 == 0,
adds signed13 bytes and preserves the existing configured EMU stack-window
check after updating SP. This is not mapped-memory or alignment SP validation.
Inherited disputed packed repeats and fatal-reference limits remain explicit.

Independent Sol 6.1 High source/test review precedes one production build.
483 scalar cases plus five generic replays and 439 parallel cases pass,
including complete CPU/inspection expectations, aliases, flags, predicates,
one-retirement bundles and model faults. One additional QEMU call exposed a
test capture naming error; its reviewed transport repair reused the same
binary. No CPU repair or second build was needed. Ten required/affected gates
pass: ISA, CPU profiles, Felucca IRQ, boot, parallel signed divide/minimum,
maximum, scalar signed minimum, signed register <= IF and parallel packed ADD.
Only obsolete newly admitted negative expectations were replaced by positives.

Fresh exact production-C admission over the original 21 executable payloads
has 117,441 admitted / 3,395 rejected sites, 353 new admissions and no
regressions. There are 64 remaining forms / 69 role rows. The original 75/81
baseline accidentally merged one unsigned maximum placement into signed
maximum: instrumented F434/0100 + 2E01 at 0x0200DBA4. Correct baseline is
76/82; 12 forms / 13 role rows are resolved. Signed maximum is fully admitted;
unsigned parallel mode0 remains deferred. Keep frozen evidence intact.
All 496 rejected width gaps and 71 opaque E53F regions / 12 raw patterns
remain; no admitted width mismatch occurs. Static admission is not runtime
reachability, frequency, throughput or all-firmware correctness.

Unchanged MAX 200,000,000 boot advances 11 instructions to E99C/8D00 at
0x0200a2fa: 120,323,443 instructions and 962,587,552 ns. Vendor disassembly
describes if (r12 < r13); this belongs to conditional batch C. Debug
stage 5/home 1/frame 1 and visible idle LCD do not establish completed HOME.
IRQ11 entries/returns are 107/107, IRQ63 5362/5362. All 57,344 captured sample
words are zero; bootguard remains pending, with no guard/watchdog failure.
Renamed default-loader replay matches captured JSON except profile, exact
PPM and latest ALNK bytes. Whole SRAM is excluded. HOME, 30 guest seconds,
physical input, synthesis and native execution remain open.

Durable source/review/research/build/gate/firmware evidence is in main repo
.deps/qemu-batch-a-2026-10-08/. Hash-identical copied reference CLIs and
standalone focused validators are retained outside temporary directories.
QEMU SHA-256:
02e0815bc737e49b47ad15441462e6118475237a4e7d149bf86e208b70e7a56c.

Focused validators reuse the pinned cached reference records; they do not
rebuild the Rust implementation. Their matrix/evidence inputs and exact
reproduction commands are in main repo `.deps/qemu-batch-a-2026-10-08/README.md`.

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

### Grouped Batch B and latest checkpoint (2026-10-08)

All 15 exact six-byte scalar forms are accepted together: FF0B/FF0D signed
literal < / <=; FF20/21/23/28/29/2A/2B/2D packed EQ/NE/unsigned LT/GT/LE
and signed GE/LT/LE; FF40/42/43/48 register EQ/unsigned GE/LT/GT and FF4A
signed GE. Register extension low byte zero remains conservative canonical
admission. Every form uses signed16 word displacement from PC+6, preserves
PSR/RETS and retires once. Exact scanner widths track the same 15 opcodes.
Shared helpers, predicates, IRQ policy and parallel classification are preserved.

Pinned primary contradictions are retained: FF0B has an unsigned literal
token, FF0D names packed rather than signed12, FF4A names a different register
field, and FF2D lacks a constructor. Saved vendor bytes and independently
expected full-state reference probes resolve these four forms. The existing
packed-repeat policy remains separate from disputed primary constructors.
Reference IF scanning/completion, branch-exit predicate clearing and canonical
admission differences remain raw evidence, not hardware validation.

Sol 6.1 High workers prepared private slices and independently reviewed the
finite matrix, combined source and focused tests before parent integration.
One production build passes all 1,754 focused cases: 1,594 successes and 160
precise model faults, covering fields/aliases, signed boundaries, displacement
endpoints, PSR/RETS preservation, IF selection/skipping/completion/following IF,
owned memory and six-byte/target fetch guards. The 1,738 initial copied-reference
research calls had zero timeouts; focused QEMU acceptance launches no reference.
Eleven required/affected ISA/profile/IRQ/boot/branch gates pass; their 896 copied
reference calls include 12 independently reviewed replacements for obsolete
negative-neighbor tests. Every other assertion and neighboring admission stays
within its prior scope.

Fresh actual-production C extraction passes 51 static sanity and 45 unpadded
short-context checks. On the same 21 executable payloads, Batch B adds 496
admitted sites with zero regressions; totals are 117,937 admitted / 2,899 rejected.
All 496 known width gaps are resolved. There remain 49 forms / 54 role rows;
unchanged Felucca has 346 rejected static sites. The 71 opaque E53F regions /
12 raw patterns and deferred 04C8/unsigned parallel maximum remain separate.
Static admission establishes no reachability, frequency, performance or complete
firmware compatibility.

Unchanged MAX 200,000,000 boot remains E99C/8D00 at 0x0200a2fa after
120,323,443 instructions and 962,587,552 ns: no reached boot advance. IRQ11
entries/returns remain 107/107; IRQ63 5362/5362. All 57,344 captured sample words
are zero; bootguard is pending. Stage 5/home 1/frame 1 does not establish
completed HOME. Renamed default-loader JSON except profile, exact LCD PPM and
latest ALNK bytes match; cross-loader whole SRAM is excluded. A/B captures
also match all JSON, whole SRAM, samples and pixels within each loader mode.
HOME, synthesis, 30 guest seconds, physical input and native execution remain open.

Durable evidence and reproduction commands: main repo
.deps/qemu-batch-b-2026-10-08/. Translator SHA-256:
ea56ba039c93c1b941eb34c4e867e1cc48a05a51fa94187d0c2ea1d598ea2ae5.
QEMU SHA-256: 8c26d479eb3fca46783b4729d36762712bad43267c34694a287a2a2a485f6560.
Next is grouped Batch C, including the reached E99C conditional form.
