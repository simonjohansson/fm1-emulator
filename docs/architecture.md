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

QEMU TCG supplies portable native code generation. A translation block runs
until a branch, a memory access or an interrupt-relevant state change; IF arms,
REP bodies and observer addresses keep one instruction per block. Icount ends
blocks at timer deadlines, so interrupts are admitted at the same instruction
boundaries as with single-instruction blocks.
Functional clocks use 8 ns per counted instruction; this is a model policy,
not a measurement of physical hardware timing.

The application loader maps raw offset zero at `0x02000120`, sets
r0=`0x01c7fe08`, and starts other registers and SRAM at zero. The guest establishes
its stacks and initializes memory. Erased 1 MiB NOR is seeded at physical
`0x4120`; XIP maps physical `0x4000` to `0x02000000`. Application handoff is
separate from architectural reset. The package loader validates and decrypts
uncompressed FWSC/UFW containers, retains flash and preset data, and supplies
the decoded flash header, chip key and validated application-area directory
through the existing SRAM handoff.
Other SPL parameters remain unknown; no firmware identity selects defaults.
ROM/SPL execution and ELF loading are unsupported.

Launches create both FM-1 cores (`-smp 2`, which requires
`-accel tcg,thread=single`); the second is experimental. Core 1 starts in reset and is
released through C1_CON, using the SRAM entry vector at `0x01c7fff8` and an
RTI startup handoff exercised on hardware. Its registers, interrupt
configuration, tick timer and stack guards are independent. Both cores share
SRAM, peripherals, XIP routing and the LOCKSET/LOCKCLR lock. Bank-0 software
requests 120-127 route through each core's IRQ configuration and acknowledge
through bank 0. Pause/resume commands suspend and continue instruction execution
without resetting registers; their status and self-clearing command bits were
measured on hardware. Bank-1 requests remain unsupported. The boot-ROM reset sequence,
retained register values and startup latency are not modeled. Icount
charges 8 ns per instruction while one CPU is runnable and 4 ns while both
are, so two busy cores each advance at 8 ns per instruction as if executing
simultaneously.

The Cocoa panel embeds the existing LCD surface. Mouse and keyboard inputs
close the same matrix contacts; encoders emit quadrature transitions. MASTER
supplies the board’s SAR ADC channel 4. Focus loss, pause, and shutdown release
contacts, while keyboard and mouse holds are combined.

The panel LEDs share the key matrix. An LED lights while its column is
selected on the 595 chain (output low) and the LED line of its matrix row is
driven high: PA9, PA10, PH6 and PH9 for rows 1 to 4. PLAY's extra green LED
sits at column 8, row 1, where no key does. Firmware dims an LED by shortening
that overlap, so `fm1-leds.c` integrates it in guest time over 16 ms windows
and publishes the fraction of the column's selected time the LED was on (0 to
255; fully lit reads about 250, Felucca's dim glow about 8). The panel draws
the levels (white, REC red, PLAY also green) and snapshots record them as
`leds`. The matrix positions come from the same table as the contacts; which
colour an LED has is only known for REC and PLAY's second LED.

## Hardware and limits

The model includes 512 KiB SRAM, NOR/SFC/SPI0, SPI1/LCD (DMA from SRAM or
XIP), SPI2 driving the panel's 74HC595 chain (transfers stretched tenfold so
stock's continuous scan fits the 8 ns instruction budget), GPIO/IOMAP, TIMER1/4/5 with
60 MHz and 24 MHz clock sources, the core tick timers, the LRC measurement
timer, protection/P33/watchdog, ALNK0 audio, SAR/WLA with the measured
internal channel, USB, UART1 transmit DMA (stock sends MIDI there) with an
idle receiver, an idle SRC, a fixed-seed random number generator, and the
single-precision FPU. Stock firmware's Wi-Fi/RF blocks are inert registers:
they store values and complete calibration and timer commands immediately,
with no radio behind them. Controllers own their registers, transfers, and IRQ
outputs; the board owns composition and wiring.
Unimplemented accesses or configurations fault explicitly. Values that were
assumed rather than measured are marked as such in the source.
The high-speed USB controller accepts only its disabled control value; its
active SIE, endpoints and DMA remain unsupported.
NOR page program and sector erase update SPI/XIP data after their modeled busy
interval. Writes last for the current session; the firmware file stays unchanged.

Felucca boot, input, guest audio generation, UI progress, and descriptor-driven
USB CDC enumeration/DTR/endpoint DMA are tested. CoreAudio consumes guest
samples; it does not synthesize substitute audio.
NOR persistence across runs, ROM boot, whole-machine reset, complete IRQ
nesting/arbitration, USB MIDI, external UART transfers, and wider firmware
compatibility remain incomplete or unverified.

Maintain the independent QEMU implementation and its
[licensing boundary](../LICENSES.md). Saved disassembly and observed behavior
are evidence; the retired emulator is not an architectural specification.
