# Booting FM-1 diagnostics in QEMU

This experiment runs unchanged guest binaries using a native pi32v2 QEMU
target. The maintained source is `overlay/`; the downloaded QEMU tree and
build products in `.cache/` are disposable.

Worktree: `/Users/simonjohansson/src/fm1-qemu-poc`, branch `codex/qemu-poc`.

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
This milestone has no splash or running-home-screen claim.

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
also have durable ignored copies outside the temporary worktree:

- Small fixtures:
  `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-boot-2026-10-05/`
- FM-1_980, with separate records for each milestone:
  `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-diag-boot-2026-10-05/`
- Native window and pause checks:
  `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-window-2026-10-06/`
