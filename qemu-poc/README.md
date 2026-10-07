# FM-1 QEMU proof of concept

## Current boot progress

The active build uses QEMU **11.1.2**. Upgrade evidence and the retained
QEMU 10 baseline are documented in [BOOTING.md](BOOTING.md).

The unchanged foundation and bare display diagnostics now boot from their
application entries. Foundation covers guest RAM initialization and execution,
GPIO matrix scanning, TIMER4 and a complete TIMER5 interrupt. The display
fixture continues through three guest-rendered SPI/DMA frames with a simulated
key press and release. The unchanged FM-1_980 diagnostic also reaches its
real foreground loop: guest memory initialization, protection and watchdog
setup, RAM flash identification and reads, an exact status framebuffer,
disconnected USB startup, and timer-driven matrix/encoder scans are validated.
See [BOOTING.md](BOOTING.md) for current commands, evidence and limitations.

To watch the timer and key matrix continuously in a native macOS window:

```sh
cd /Users/simonjohansson/src/fm1-qemu-poc
mise exec python@3.13.15 -- python qemu-poc/build.py
mise exec python@3.13.15 -- python qemu-poc/run_display.py
```

The timer keeps changing and the OCT-minus key is automatically pressed and
released every half-second of guest time. Execution stays running until you
close the window. This viewer does not yet map keyboard or mouse controls.

## Original probe and timer milestone

The remainder of this document records the original proof of concept before
full foundation startup was implemented. Its missing-feature and uncommitted
work statements describe that historical milestone; current status is in
[BOOTING.md](BOOTING.md).

This worktree contains a working, deliberately small pi32v2 QEMU system target.
It executes the existing instruction probe and the TIMER5 interrupt portion of
the existing foundation firmware without changing either guest binary.

The probe matches all twelve saved physical FM-1 result words. The timer test
dispatches through a guest vector, runs the guest handler, acknowledges the
pending interrupt and executes `rti`, with the application stack restored.
This supports continuing a bounded QEMU investigation while keeping the Rust
emulator as the operational implementation and reference. Whole-firmware
compatibility and performance remain unproven.

## Worktree and boundaries

- Worktree: `/Users/simonjohansson/src/fm1-qemu-poc`
- Branch: `codex/qemu-poc`
- Base commit: `81b9ed96e33b28fc401fb3e0faf46c5f904bd843`
- Original checkout: `/Users/simonjohansson/src/fm1-emulator`
- Inspection and validation date: 2026-10-05

The worktree was created from the original checkout with:

```sh
git worktree add -b codex/qemu-poc /Users/simonjohansson/src/fm1-qemu-poc
```

All experimental source, build configuration, documentation and validation
results live in `qemu-poc/`. Existing tracked files and workflows are unchanged.
The original checkout is clean. No hardware was flashed or contacted for this
experiment. The changes are uncommitted; retain this worktree while reviewing
them. Its `/private/tmp` location is temporary storage.

## Build and validate

The tested host is macOS arm64, Darwin 27, Apple Clang 21.0.0
(`clang-2100.3.34.2`), SDK 27.0, with existing Homebrew GLib 2.88.2. A native C
compiler, `make`, GLib development headers/libraries and network access for the
pinned downloads are required. Linux and other hosts have not been tested.

Use mise for both language runtimes. If they are not already installed:

```sh
cd /Users/simonjohansson/src/fm1-qemu-poc
mise trust
mise install rust@1.91.1 python@3.13.15
```

Build and run the complete validation:

```sh
cd /Users/simonjohansson/src/fm1-qemu-poc
mise exec python@3.13.15 -- python qemu-poc/build.py
mise exec python@3.13.15 -- python qemu-poc/validate.py
```

Expected validation output:

```text
PASS probe: QEMU 75 / Rust 75 guest instructions
PASS timer: QEMU 12553 / Rust 19251 guest instructions
PASS explicit fault: unsupported-opcode
PASS explicit fault: unmapped-memory
PASS explicit fault: unaligned-memory
PASS explicit fault: read-only-xip
PASS QEMU infrastructure: pi32v2 target; up to 35 guest markers per block
```

The build script downloads and verifies the QEMU release archive, applies the
source overlay, and builds only `qemu-system-pi32v2`. It keeps the source, Python
environment, tools and logs under `qemu-poc/.cache/`. It installs no global
packages. It creates a local pkgconf if `pkg-config` is absent. On the tested
Apple Silicon host it discovers existing `/opt/homebrew/opt/*/lib/pkgconfig`
directories. Hosts with another library prefix should set `PKG_CONFIG_PATH`.

A fresh QEMU source extraction and fresh build directory were tested using this
script with warnings treated as errors. Build logs are `.cache/configure.log`
and `.cache/build.log`. Existing local dependency installations were reused in
that final check. The one unused EDK2 symlink to `/opt/X11/include` is omitted
during safe archive extraction; EDK2 firmware is not built by this target.
Use `build.py --reconfigure` after changing configure options. When changing
QEMU releases, preserve the old `.cache/build/` under another name first; the
script rejects a cache tied to another release. Deleting only
`qemu-poc/.cache/build/` requests a fresh compilation. Downloaded sources and
build outputs are disposable; the overlay is the maintained source.

Run either fixture directly from the worktree root:

```sh
qemu-poc/.cache/build/qemu-system-pi32v2 \
  -M fm1-poc -accel tcg,thread=single \
  -icount shift=3,align=off,sleep=off \
  -display none -serial none -monitor none -nodefaults \
  -kernel build/probe.bin -append probe

qemu-poc/.cache/build/qemu-system-pi32v2 \
  -M fm1-poc -accel tcg,thread=single \
  -icount shift=3,align=off,sleep=off \
  -display none -serial none -monitor none -nodefaults \
  -kernel build/foundation/firmware.bin -append timer
```

Each process stops at its fixture completion PC and prints one JSON object.
This host stop point is test instrumentation, not guest hardware or a replaced
firmware routine. `validate.py probe` and `validate.py timer` select individual
tests. The complete validation also checks QMP's architecture reporting and
records TCG instruction markers from `.cache/probe-tcg.log`.

The unchanged reference tests can be run separately:

```sh
mise exec -- cargo test --manifest-path rust-emulator/Cargo.toml \
  --locked --offline --test core --test timers
```

These 20 tests passed. The reference executable in `reference/` uses the existing
public Rust interfaces in a separate process. Its Cargo build is offline; the
headless core does not require the vendor compiler or ignored firmware packages.

## Revision and integration

QEMU is pinned to **v11.1.2**, commit
`4fc49f46dc95d4a27de2509e7fceb2931e91faeb`. Its release archive is
[`qemu-11.1.2.tar.xz`](https://download.qemu.org/qemu-11.1.2.tar.xz), SHA-256:

```text
731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016
```

Build dependencies are Python 3.13.15, Rust 1.91.1, Meson 1.5.0, Ninja
1.11.1.4, distlib 0.3.9 and pycotap 1.3.1. The optional local pkgconf is 2.3.0,
archive SHA-256
`3a9080ac51d03615e7c1910a0a2a8df08424892b5f13b0628a204d3fcce0ea8b`.
Python package versions are pinned; their wheel hashes and native host library
versions are not locked across platforms. This is a reproducible source/build
procedure on the tested host, not a claim of identical executable bytes on
different hosts.

No public reusable pi32v2 QEMU target was found. The pinned release lacks one,
and the complete upstream master tree at
`d7a65d1793d691d356a56833620f7d1e6f5d653b` contained no pi32v2/JieLi target
paths when checked. Public repository searches for QEMU with pi32v2 or JieLi
also found no target repository. However, a
[vendor build note](https://gist.github.com/elfplz/2a4b840214f4ba50c4754d3df4e7e5c5)
explicitly configures `pi32v2-softmmu`. This is evidence of a vendor fork;
its source was not available in that owner's public repositories. It would be
useful to obtain that fork before undertaking a larger port. The conclusion is
limited to accessible public source, not that a target has never existed.

[TCG plugins](https://www.qemu.org/docs/master/devel/tcg-plugins.html) provide
instrumentation hooks. They do not define a new guest CPU's decoder and
translation backend. QOM/device modules likewise do not remove the need to
compile a new target against QEMU's internal interfaces. This prototype uses a
source overlay rather than a plugin:

| File/directory | Responsibility |
| --- | --- |
| `overlay/target/pi32v2/` | CPU state, translation, MMU access hooks, interrupt entry and return |
| `overlay/hw/pi32v2/fm1-poc.c` | One-CPU machine, SRAM/XIP mapping, TIMER5 and IRQ63 MMIO |
| `integrate.py` | Target/device build hooks, architecture identifier and QMP enum |
| `build.py` | Pinned downloads and isolated build |
| `reference/` | Separate unchanged-emulator reference runner |
| `fixtures.json`, `validate.py` | Immutable fixture identity, expectations and comparisons |
| `results/` | Recorded JSON validation outputs |

Integration changes only the downloaded QEMU tree: new CPU/machine files and
target configs, additions to `target/` and `hw/` Meson/Kconfig entry points,
`QEMU_ARCH_PI32V2`, and one `SysEmuTarget` enum member in `qapi/machine.json`.
The user explicitly approved that isolated QEMU schema addition. QMP reports
`{"arch":"pi32v2"}`. The CPU is an independent pi32v2 target; it does not use
ARM or RISC-V execution semantics.

The licensing boundary and implementation sources are recorded in
[LICENSES.md](LICENSES.md). No GPL-3.0-only interpreter, decoder or peripheral
implementation is copied or linked into QEMU.

## What QEMU supplies

[QEMU TCG](https://www.qemu.org/docs/master/devel/tcg.html) translates the new
pi32v2 decoder's operations into host code, manages translated blocks and
chains them. The probe generated a block with 35 guest instruction markers,
demonstrating execution beyond one guest instruction per native invocation.
Most moves, shifts, logic, memory and stack operations use direct TCG
operations; arithmetic flags and interrupt return use small C helpers.
There is no callback into the Rust interpreter and no new ARM64/x86-64 emitter.

[QEMU MemoryRegions](https://www.qemu.org/docs/master/devel/memory.html) provide
512 KiB SRAM at `0x01c00000`, 1 MiB read-only XIP at `0x02000000`, and MMIO
callbacks. Raw application bytes load at `0x02000120`. CPU memory accesses
use QEMU's translated load/store and TLB infrastructure with an identity map.
Word accesses enforce alignment; unmapped accesses and XIP writes terminate
with explicit diagnostics. The prototype's page permission hook supplements
the ROM mapping so XIP writes cannot silently disappear.

`QEMUTimer` on `QEMU_CLOCK_VIRTUAL` schedules TIMER5 expiration. A qdev GPIO
connection asserts `CPU_INTERRUPT_HARD`. The timer and CPU then implement the
guest-visible behavior themselves: enable/source/divider bits, the pending
latch, write-to-acknowledge, IRQ63 enable/priority, global ICFG masking, the
per-core priority threshold, vector lookup, stack handoff, RETI and `rti`.
Acknowledging the timer clears the latch without restarting its phase.
QEMU's IRQ connection does not supply JieLi interrupt-controller semantics.

The machine uses a small JieLi-specific timer model. A generic ARM or other
timer/controller model would be reusable only if its guest-visible register
and interrupt behavior matched the WL82. No such compatibility is assumed.
One guest CPU runs with single-threaded TCG. QEMU's
[MTTCG facilities](https://www.qemu.org/docs/master/devel/multi-thread-tcg.html)
are not enabled or evaluated here.

## Fixture provenance and expected results

The selected raw binaries, ELF files and original probe capture exist locally
and are tracked at the base commit. The tests never regenerate them. The vendor
compiler, SDK, extra firmware packages and several later hardware captures are
ignored or unredistributed and are not needed to build or run this prototype.
Every selected artifact's SHA-256 is enforced by [fixtures.json](fixtures.json).

### Instruction probe

`build/probe.bin` is the 142-byte standalone image built from
`firmware/crt0.S` and `firmware/probe.S`. Its saved manifest, `build/probe.json`,
records JieLi Clang 4.0.1 and Felucca commit
`1e838e17e170b20ff09b9660c9a7171aadfc5dca`.
`build/fm1-diag.json` records compiler SHA-256
`42b94f9e11140b0fcab8f807b2872ad245b8eeca03a2d792f8706c5a3a35d34c`.

The 114-byte `fm1_probe` function has SHA-256
`05c1cbfe173ff55b89b6fbc6b26c9aaa28784e2d23933d7871c868ccb9626ea7`.
Validation verifies these identical bytes in all three guest images:

| Image | Function address | Raw image offset |
| --- | --- | --- |
| `build/probe.bin` | `0x0200013c` | `0x1c` |
| `build/foundation/firmware.bin` | `0x020002f0` | `0x1d0` |
| `build/fm1-diag.bin` | `0x02002bc2` | `0x2aa2` |

`build/hardware-verification.txt` records a physical FM-1_980 diagnostic
capture on 2026-10-04 at 17:00 UTC. `build/hardware-initial.txt` contains the
function hash and these twelve words. QEMU and the current Rust emulator match
each word:

| Index | Observation | Expected hexadecimal word |
| ---: | --- | --- |
| 0 | Constant | `12345678` |
| 1 | Wrapping addition | `00000001` |
| 2 | Subtraction | `fffffffe` |
| 3 | XOR | `b7910c22` |
| 4 | AND | `00245258` |
| 5 | OR | `b7b55e7a` |
| 6 | NOT | `edcba987` |
| 7 | Left shift | `23456780` |
| 8 | Right shift | `01234567` |
| 9 | Load and add | `1234567f` |
| 10 | Loop sum | `00000037` |
| 11 | Stack | `13579bdf` |

The standalone fixture starts at `0x02000120`, stops before the completion
loop at `0x0200013a`, and writes results at `0x01c08000`. QEMU and Rust each
retire 75 guest instructions. The unchanged diagnostic ELF's direct probe
runs separately in Rust and retires 70; it excludes the standalone startup.
Only the probe function bytes, not the two startup paths, are equivalent.

Both runners seed general registers with `0x10203040 + register * 0x01010101`.
The standalone guest initializes SP/SSP. Validation checks every final general
and special register against Rust, and explicitly verifies preservation of
`r1`–`r15` and SP. This register check is reference evidence; the saved hardware
capture only observes the twelve result words, not all registers or flags.

### TIMER5 interrupt

`build/foundation/firmware.bin` is the existing 592-byte application built by
`tools/build_foundation.py` from `firmware/foundation.S` plus the same probe.
The prototype loads the complete unchanged application but enters its timer
subsection at `0x02000238`. It seeds zeroed SRAM, SP `0x01c7a000` and SSP
`0x01c7c000`. The Rust runner uses the same entry, seed and completion PC.

This intentionally skips foundation startup, RAM code copying/execution,
the probe, GPIO and TIMER4. QEMU does not boot the full foundation fixture.
The first six result words remain host-initialized zeroes and are not evidence
that those earlier milestones ran.

The guest itself installs handler `0x020002bc` in vector `0x01c7fefc`, enables
IRQ63 at priority 1, selects TIMER5 OSC/4 with period 600, enables interrupts,
and polls. The handler saves PSR/RETS/RETI and `r0`–`r3`, acknowledges through
TIMER5 MMIO, increments its SRAM counter, records the interrupt stack, restores
the saved state and executes `rti`. The foreground guest disables the timer
and stores the completion marker before stopping at `0x020002ba`.

Expected ten words at `0x01c08010`:

```text
00000000 00000000 00000000 00000000 00000000 00000000
00000001 01c7bfe4 01c7a000 0050f00d
```

QEMU observes exactly one expiration, one IRQ entry, one acknowledgment and
one `rti`; both the pending latch and interrupt-context flag are clear at
completion. Handler SP is `0x01c7bfe4` (SSP minus 28 bytes), final application
SP is `0x01c7a000`, and SSP is restored to `0x01c7c000`.
Entry ICFG is `0x013f0302`; immediately after `rti` it is `0x013f0700`, and
the foreground `cli` leaves `0x013f0500`.

All final general registers and special registers match Rust except RETI's
allowed polling-slot difference: QEMU interrupts at `0x02000282`, Rust at
`0x02000280`. Both addresses are in the same two-instruction wait loop. The
validators require either valid slot and the exact remaining register state.

There is no saved physical TIMER5 capture for this fixture. The existing
foundation record explicitly calls its timer/interrupt evidence emulation
only. A later ignored capture exists in the original checkout at
`.deps/firmware-trial/irq-context/irq-context.txt`, SHA-256
`f926b6bcf4a204efa79f86c760783b8b90ac55d8266e64c21ff30e249b4a3bc4`.
It exercises software source 120 and priorities 0–7, supporting the source,
priority and active-bit interpretation of ICFG. Its priority-1 entry value is
`0x01780302`. Applying those fields to TIMER5 source 63 remains a model
assumption backed by the Rust implementation, not a physical timer validation.
That private capture is not copied into this worktree or required by validation.

## Validation record and timing

[results/validation.json](results/validation.json) and the per-run JSON files
record the fixture comparisons. [results/negative-checks.json](results/negative-checks.json)
records four temporary modified-image checks: unsupported opcode, unmapped
memory, unaligned word access and attempted XIP write. Each fails explicitly.
Only temporary cache images are patched; the selected fixture artifacts remain
unchanged. [results/qmp-target.json](results/qmp-target.json) and
[results/tcg-translation.json](results/tcg-translation.json) record target
identity and translation evidence.

QEMU uses `-icount shift=3,align=off,sleep=off`: one supported guest instruction
encoding advances functional virtual time by 8 ns. TIMER5 uses a 24 MHz source,
OSC/4 in this fixture. QEMU shortens translated execution at virtual-timer
deadlines through its existing icount machinery. This is not cycle-accurate
pi32v2 execution, and it does not model pipeline, cache or bus latencies.
The host completion marker consumes one extra QEMU icount slot without
incrementing the guest retirement counter. Thus the probe ends at 608 ns
(`(75 + 1) * 8`), and the timer test at 100,432 ns (`(12553 + 1) * 8`).

The current Rust clock differs. `Clock::instruction_ticks` accumulates nominal
CPU issue time into fractional 24 MHz oscillator ticks. Raw-image defaults
select 192 MHz, giving approximately one oscillator tick per eight outer
scheduler calls; the packaged SPL handoff selects 360 MHz. Shared device time
advances once per outer scheduler round even when both guest CPU contexts run.
A parallel instruction bundle or idle scheduler call further complicates
equating the Rust `steps` field with individual guest instruction slots.
The selected fixtures have one guest CPU, no parallel bundles and no idle
instructions, so their reported counts are guest instruction retirements.

The timer subsection takes 12,553 QEMU instructions and 19,251 Rust instructions
because the functional clocks cause different polling durations. This is not a
throughput comparison. QEMU startup/translation and sustained host execution
were not timed. The twelve-word probe is too small to establish whole-firmware
performance; TCG's larger blocks demonstrate a capability, not a speedup.

Some existing documentation predates the current code:

- `rust-emulator/README.md` says one full OSC tick per instruction and describes
  a narrower timer/interrupt implementation. `FELUCCA.md` also says one OSC tick
  per completed bundle. Current clock advancement uses the fractional issue
  calculation above.
- `build/foundation/verification.txt` records 24 OSC ticks per instruction and
  2,371 instructions. A fresh current-Rust full-foundation run reaches the same
  completion PC after 21,467 instructions and one IRQ, with TIMER4 samples 0
  then 3. The full run's other results are `c001cafe`, `00000000`, `00005a17`,
  GPIO `000000e1`, and the same four timer/stack/completion words listed above.
- Current Rust interrupt code covers timers 0–5, per-core tick, software
  sources 120–127 and other peripheral sources, including UART, ADC, audio,
  display/SPI and wireless paths. Source enable/priority, per-core thresholds
  and deterministic tie selection are modeled. The QEMU prototype covers only
  TIMER5/IRQ63 and nonnested entry/return.
- `PERFORMANCE.md`'s existing 400-million-call measurements count outer
  `Cpu::step()` calls, potentially involving two guest contexts, and are
  historical single-run results. They are not fresh measurements or directly
  comparable to this prototype's guest retirement count.

Existing documents and historical generated records are left intact; this
README records the reconciliation for the experiment.

The current full-foundation observation is saved in
[results/rust-full-foundation.json](results/rust-full-foundation.json). Reproduce
that separate Rust run with:

```sh
mise exec -- cargo run --manifest-path rust-emulator/Cargo.toml --locked --offline \
  -- boot build/foundation/firmware.elf --until foundation_done \
  --inspect foundation_results:10 --press 0:4
```

## Missing behavior and next decision

The implemented instruction forms are restricted to those required by the two
fixtures: selected 16/32/48-bit immediates, special-register moves, add/subtract,
logic and logical shifts, aligned word memory, stack saves/restores, relative
call/jump, zero/nonzero branch, `rts`, `cli`/`sti`, `csync`, `nop` and `rti`.
The ALU helper implements observed low PSR bits, but this probe does not validate
the complete flags semantics or all operands of these forms. Unknown forms
fail instead of falling back to another ISA or the Rust interpreter.

Remaining work includes broad ISA/flag coverage, parallel and predicate bundles,
hardware repeats, atomics/locking, floating point/DSP, nested exceptions,
other IRQ sources and arbitration, ROM/SPL boot state, address guards,
encrypted/remapped XIP, peripheral clock changes and complete device models.
TIMER5 accepts only the implemented OSC/1 and OSC/4 count modes; full prescaler
and peripheral-clock modes are absent. CPU faults terminate the host process
with diagnostics rather than implementing hardware exception dispatch.

SRAM uses QEMU's translated memory machinery, but RAM code execution and
self-modifying-code invalidation have not been validated here. Full machine
reset, migration/snapshot state and debugger integration are also unvalidated.
The test addresses and stop points are tied to the pinned fixtures; this is not
a general firmware loader. MTTCG, second-core execution, full firmware boot,
audio, USB, DMA, the graphical panel and broad peripheral coverage are outside
this experiment.

There is no blocker to the requested prototype: both scoped fixtures pass.
For a larger migration, the concrete gaps are missing pi32v2/exception coverage,
license-safe peripheral implementation, absent physical timer confirmation,
and no equivalent whole-firmware performance measurements.

**Recommendation: continue a bounded QEMU experiment and retain the current
engine.** QEMU has demonstrated replacement of memory mapping, translated
memory access, MMIO dispatch, virtual timer scheduling, IRQ connections and
host code generation/block management. Instruction and exception semantics,
JieLi interrupt behavior and most peripherals remain project responsibilities.
These results justify further investigation, not a wholesale migration.

A useful next gate is an unchanged single-CPU RAM-code/startup fixture with
code mutation and broader interrupt-mask/priority cases, followed by a bounded
firmware path. Compare equivalent guest inputs, instruction coverage, clocks
and device settings; count retired instructions separately from scheduler
calls; measure startup/translation separately from sustained execution; and
report multiple runs. Seek the vendor pi32v2 fork in parallel with that work.
No alternative framework was prototyped here, so this evidence does not justify
switching frameworks now.
