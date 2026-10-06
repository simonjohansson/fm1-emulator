# Rust FM-1 emulator

Rust 1.91.1 is managed by the root `mise.toml`. The interpreter executes vendor-built pi32v2 machine code without external
Rust dependencies. The optional `gui` feature uses eframe/egui 0.31.1 for a
native OpenGL window; the version and transitive dependencies are locked.

The [full Felucca boot investigation](FELUCCA.md) records the verified firmware
inputs, resolved startup failures, decoder fixes, full boot and note checks. It also
documents the bounded `diagnose` runner for symbol and peripheral reports.

On a fresh checkout, fetch the locked dependency metadata before offline tests:

```sh
mise exec -- cargo fetch --manifest-path rust-emulator/Cargo.toml --locked
mise run rust-test
mise run rust-probe
mise run build-foundation
mise run rust-foundation
```

## Native device window

From the project root:

```sh
./emulator build/display/firmware.elf
./emulator "$HOME/Downloads/FM-1.fwsc"
mise run build-display       # optional: rebuild guest with the vendor compiler
mise run rust-gui-test       # core and GUI input integration tests
```

The launcher compiles a release `fm1-ui` executable using mise. On macOS it
creates a local application bundle under `target/release`; on Linux it runs the
binary directly (the host needs the normal eframe OpenGL/windowing development
libraries). macOS is visually verified; Linux is not tested here. The existing
`fm1-emu probe|boot` CLI is unchanged and does not require a display server.

To boot a `.fwsc` package over a device's saved data, pass a raw 1 MiB flash
dump (`jl-uboot-tool read 0 0x100000`, FM-1-transporter `fm1t.py dump`):
`fm1-emu boot|probe PACKAGE.fwsc --flash DUMP.bin` or
`cargo run --release --example diagnose -- --flash DUMP.bin PACKAGE.fwsc`.
The package is installed over the image as a firmware install would; every
other byte (projects, settings, presets) comes from the dump. Changes the guest
makes are not written back to the file.

The panel is an original vector illustration drawn in Rust, using the device's
[front-panel photograph](https://m.media-amazon.com/images/I/71fgwUHLJKL._AC_SL1500_.jpg)
as a layout reference. No vendor product photo is bundled. Button identities
follow the [pinned Felucca panel defaults](https://github.com/hugelton/Felucca/blob/1e838e17e170b20ff09b9660c9a7171aadfc5dca/firmware/src/panel.c)
and the local `fm1_input.h` wiring. All fourteen buttons and twenty-seven note
keys feed matrix contacts; rotary controls are currently decorative. Short
clicks/keystrokes are held for at least 100 ms of both host and guest time so a
slow guest scan can observe and debounce them.
Losing window focus releases contacts. Pause stops guest execution; Restart
reloads the selected image and resets CPU, RAM, peripherals, and input state.

The UI reads only the panel's 240×240 framebuffer, gated by display enable,
sleep, and active-low PA2 backlight. There are no symbol-specific drawing hooks
or substituted application functions. SPI1 commands and SRAM DMA implement
software reset, sleep/display enable, RGB565 format, unrotated RGB/BGR, column/
row windows, and pixel writes. Unsupported commands, rotations, and invalid DMA
addresses fault visibly. Completion is synchronous, not cycle-accurate; INVON
is treated as the FM-1 panel's normal electrical drive mode, not an RGB invert.
The CPU runs continuously on a worker thread. The window sends matrix contacts
and receives the latest LCD snapshot; unchanged pixels do not require texture
uploads. Guest time still follows the emulated clock, so execution speed depends
on the host and is not yet calibrated to real time.

Instruction dispatch uses a shared first-word decode table and a bounded cache
of wide instruction words. A bounded basic-block cache also prepares common
register, arithmetic, shift, memory and short branch operations, including their
operands. Each core retains its own position in the block. Current instruction
words are still checked through the bus, so SRAM changes, flash remapping and
disabled XIP cannot execute stale code. Interrupts, device timing and core
interleaving retain their per-instruction boundaries.

Hot blocks compile common register operations to ARM64 or x86-64 machine code.
Each native entry currently executes one guest instruction before returning to
the device scheduler; memory accesses and complex instructions use the existing
handlers. Cheap uncached operations bypass block lookup. Native code uses owned
pages that become read/execute after emission, and automatically falls back to
prepared interpretation if allocation is denied. Broader translation and native
block batching remain performance work; this initial backend does not yet provide
a substantial whole-firmware speedup.

The [performance TODOs](PERFORMANCE.md) record the current measurements and the
remaining batching, validation and profiling work.

`build/display/firmware.elf` is the 17,056-byte FM-1_981 hardware application.
It uses Felucca-derived startup, watchdog, recovery, input scanning, USB CDC and
MIDI updater code. `firmware/display.S` draws labels, a buffered TIMER4 readout
and key tiles, called by the hardware application's main loop. There is no
emulator-specific application path in this image. Package integrity tests
compare its bytes against the decrypted `build/display/firmware.fwsc` payload.
The same package was flashed successfully to the FM-1; its USB serial status
reports advancing display frames, and all twelve CPU probe words match.

Button transitions pass through guest GPIO scanning and debounce, the CDC
ring, USB endpoint DMA and the emulator host before stdout receives
`KEY <id> down` or `KEY <id> up`. The host performs GET_DESCRIPTOR,
SET_ADDRESS, SET_CONFIGURATION and CDC SET_CONTROL_LINE_STATE requests.
It discovers the CDC interface and IN endpoint from the configuration descriptor.
Terminal stdin reaches the descriptor-selected CDC OUT endpoint through bounded
host queues, SRAM DMA, RX packet/count registers and the receive interrupt latch.
An unread packet stays intact until the guest acknowledges it. Type commands and
press Enter; EOF stops the input reader while guest execution continues.
The unchanged local and published Felucca builds answer `help` through this path.
USB MIDI host transport is not implemented.
The hardware firmware retains both its serial console and MIDI updater.

P33 accesses model watchdog arming/feeding and stop with an expiry fault if
feeding ceases. NOR supports JEDEC/status/read transactions used at startup;
erase/program and persistent flash images are not implemented. CPU write guards
reject protected RAM writes; full guard exception dispatch and stack/PC limit
hardware remain incomplete. Reset requests stop rather than emulate ROM boot.
Full Felucca boots into its UI and renders note samples through ALNK DMA.
FX rendering and continued execution now pass with both the published package
and local source ELF. Support remains partial: the local presets path can stop
on an unsupported instruction. See the [Felucca investigation](FELUCCA.md) for
the verified scope and remaining failure.
Additional engines, CPU forms and peripheral behavior remain incomplete;
host audio playback is absent. See [the measured full-firmware checks](FELUCCA.md).

The loader accepts an FM-1 `.fwsc` package, an application `.bin` mapped at
`0x02000120`, or an executable ELF32-pi32v2. Package loading checks the outer
header/table, complete `flash.bin`, its flash directory, chip key and decrypted
application CRCs. It retains the original flash bytes and supplies the SDK's
SPL boot-device parameter block; encrypted SFC reads include the package's
directory and embedded configuration. Separate outer auxiliary payloads and
the update process are not emulated. ELF flash load addresses reconstruct the exact application `.bin`,
including the initializers for RAM code and data. Startup performs the RAM
copies; the loader does not move them early. Package support does not imply
that every stock CPU or peripheral path is implemented; see the
[stock firmware boot trials](STOCK-FIRMWARE.md).

`rust-probe` calls the embedded probe directly from the unchanged full
`build/fm1-diag.elf`. Its twelve results match the saved physical FM-1 capture.
The existing Python interpreter remains an independent reference. The Rust
probe takes 70 instructions; the Python test image includes five additional
startup/call instructions, for 75.

For a raw application, specify the probe entry from that build's symbol table:

```sh
mise exec -- cargo run --manifest-path rust-emulator/Cargo.toml --offline -- \
  probe build/fm1-diag.bin --entry 0x02002bc2
```

The address above belongs to the current FM-1_980 build, not arbitrary firmware.
Both commands accept `--limit COUNT` and `--trace PATH` (JSONL).

The separate 592-byte foundation firmware boots from `_start`, copies `.data` and
`.ram_text`, clears `.bss`, runs the hardware-verified CPU probe, executes RAM
code, observes TIMER4 progressing, and services a TIMER5 interrupt through a
guest vector and handler. The handler runs on SSP, saves/restores registers,
acknowledges the timer, and returns with `rti` to the application stack. Firmware
also clocks the two 74HC595 registers on PA4/PA3, latches on PA1, and scans all
eleven active-low matrix columns. GPIO direction, digital enable, pull-ups,
outputs, and the physical PA/PB row wiring determine the observed key states.

```sh
mise run build-foundation
mise exec -- cargo run --manifest-path rust-emulator/Cargo.toml --offline -- \
  boot build/foundation/firmware.elf --until foundation_done \
  --inspect foundation_results:10 --press 0:4
```

`--press COLUMN:ROW` injects a physical matrix closure; repeat it for multiple
keys. Columns are 0..10 and packed rows 0..5. `0:4` is OCT-minus; `3:4` is the F3
note key. Debouncing and encoder decoding belong to firmware, not the GPIO
model. The hardware display application runs the inherited full input routine.
Scheduled input events and UI encoder contacts remain future work.

`--until` accepts a breakpoint symbol or numeric address, and `--inspect` accepts
`SYMBOL_OR_ADDRESS:WORDS`. Raw `.bin` boot works with numeric addresses. Successful
boot output reports the reached PC, executed instructions, IRQ entries, and
inspected guest RAM. `--trace PATH` saves executed instructions as JSONL.

This ELF/raw application is for emulator tests. It has no updater or recovery
and must not be installed on the FM-1. It is a historical unit-test fixture;
use the shared hardware display build for device comparisons.

Timers use a deterministic virtual clock of one 24 MHz oscillator tick per guest
instruction bundle, not measured CPU cycle timing. TIMER4 supports
OSC /1 and TIMER5 supports OSC /4; other clock modes fail explicitly. Interrupt
delivery currently covers non-nested TIMER5/IRQ63 with global and per-source
masking. SPL initial state and the interrupt stack handoff are functional
approximations that still need independent physical validation.

The hardware display image runs from its application entry without host
substitutions for firmware functions. Unknown MMIO and instructions still
fault. This models the application after the SPL handoff, not ROM or SPL.
Parallel instruction pairs execute the following slot first. Conditional-block
state is preserved across IRQ63 entry/return. Compiler-derived encodings have
regression tests and boot coverage; only the probe's forms have individual
physical comparison evidence.

Probe encodings come from the Python reference, vendor disassembly, and the
physical comparison. Startup stack arithmetic, immediate masks, and special
register mappings were checked against the Apache-2.0
[Quarkslab pi32v2 reference](https://github.com/quarkslab/ghidra-jieli/tree/e1bd0707874b77b759401555d24839ad43af1267/data/languages).
New CPU/peripheral behavior needs separate hardware validation.

## Agreed foundation checklist

The completion criterion is five of these ten foundations exercised by booted
guest firmware. Each foundation has equal weight for this milestone. This is
not a percentage of complete instruction-set or musical-feature coverage.

| Foundation | Milestone evidence | Remaining scope |
| --- | --- | --- |
| CPU | Guest executes real vendor machine code; twelve probe words match hardware | Further ISA forms, flags and independent instruction probes |
| Memory/startup | ELF equals raw flash image; guest copies data and RAM code, clears dirty BSS, executes RAM code | ROM/SPL, reset retention, boot parameters |
| Timers | Guest sees TIMER4 progress; TIMER5 produces a periodic event | Other sources/dividers and measured cycle timing |
| Interrupts | IRQ63/ALNK11 vectors, masking, SSP handler frame, acknowledgment, `rti`, priority selection | Nested priorities, other IRQs, physical entry-state validation |
| Controls | Guest scans eleven columns; released/pressed and multiple-key cases agree | Scheduled events and UI encoder input |
| Flash | Startup JEDEC/status/NOR reads; plain XIP shares physical NOR storage | Erase/program, persistence, XIP busy behavior |
| LCD | Display guest initializes SPI/DMA, draws RGB565 pixels and live timer/key data | Other controller modes, SPI timing, pixel-exact physical comparison |
| USB serial | Hardware guest enumerates and sends CDC debug bytes through DMA | Host OUT packets, broader controller/USB behavior |
| USB MIDI | Not implemented | USB transport and MIDI packet handling |
| Audio/DMA | Unchanged Felucca renders stereo SRAM, alternates ALNK halves, services audio IRQs; note samples are nonzero | Host playback, other clocks/formats, codec analog behavior, cycle timing |

Original foundation evidence: seventeen Rust integration tests passed; a native boot executes
2,371 instructions, services one guest interrupt, and reaches `foundation_done`.
The guest's last result is `0x0050F00D`. See `build/foundation/verification.txt`.

Current suite: 65 ordinary Rust tests and an opt-in full Felucca test pass.
See `build/display/verification.txt` for the display milestone and limitations.
