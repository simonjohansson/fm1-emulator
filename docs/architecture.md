# Architecture

The emulator separates CPU execution, SoC/board hardware, and the host interface.
Headless and interactive sessions use the same CPU and hardware model.

| Layer | Owns |
| --- | --- |
| CPU | pi32v2 registers, instructions, memory accesses, exceptions, IRQ entry/return |
| Hardware | Register-driven JieLi controllers, shared routing, FM-1 wiring, flash/LCD/controls |
| Host | Cocoa pixels, physical key events, CoreAudio samples, USB CDC terminal, session controls |

Firmware names, hashes, symbols, and PCs never select CPU or hardware behavior.
Host input closes board contacts or uses real USB endpoint transfers; it does
not edit guest variables. Optional validation observers in `fm1-test.c` inspect
state and stop bounded runs without changing instruction semantics.

## Execution and loading

QEMU TCG supplies portable native code generation. The current target translates
one architectural instruction per block, with guarded fallthrough/static-branch
chaining. Predicate completion and interrupt boundaries remain explicit.
Functional clocks use 8 ns per counted instruction; this is a model policy,
not a measurement of physical hardware timing.

The application loader maps raw offset zero at `0x02000120`, sets
r0=`0x01c7fe08`, and starts other registers and SRAM at zero. The guest establishes
its stacks and initializes memory. Erased 1 MiB NOR is seeded at physical
`0x4120`; XIP maps physical `0x4000` to `0x02000000`. Application handoff is
separate from architectural reset. ROM/SPL boot, ELF loading, and encrypted
update packages are unsupported.

## Hardware and limits

The model includes 512 KiB SRAM, NOR/SFC/SPI0, SPI1/LCD, GPIO/IOMAP, TIMER4/5,
protection/P33/watchdog, ALNK0 audio, SAR/WLA, and USB. Controllers own their
registers, transfers, and IRQ outputs; the board owns composition and wiring.
Unimplemented accesses or configurations fault explicitly.

Felucca boot, input, guest audio generation, UI progress, and descriptor-driven
USB CDC enumeration/DTR/endpoint DMA are tested. CoreAudio consumes guest
samples; it does not synthesize substitute audio. Emulation is below real time.
NOR program/erase persistence, ROM boot, whole-machine reset, complete IRQ
nesting/arbitration, USB MIDI, UART, and wider firmware compatibility remain
incomplete or unverified.

Maintain the independent QEMU implementation and its
[licensing boundary](../LICENSES.md). Saved disassembly and observed behavior
are evidence; the retired emulator is not an architectural specification.
