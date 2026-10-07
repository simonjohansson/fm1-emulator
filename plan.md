# Generic FM-1 QEMU implementation plan

Resumed with explicit user authorization on 2026-10-07 after reviewing the
three-layer architecture against the POC. The user approved updating this
plan and working through it. The 2026-10-06 pause is superseded.

Main agent: **Astra high**. Every subagent: **Sol 6.1 Extra high**, configured
explicitly as described below. Target **macOS only** initially; Linux and
Windows are deferred. Keep the existing Cocoa frontend initially.

## Objective still to complete

Boot the pinned **unchanged** Felucca application in the fresh pi32v2 QEMU
implementation: its splash, continuously running home screen, actual audio
and timer ISR activity, 30 guest seconds through the bootguard period, a
reproducible physical key/encoder sequence with verified UI/audio effects,
and a native Cocoa viewer left **executing**, without checkpoint pause.
Splash success and first audio entry do not complete this objective.

## General-purpose FM-1 compatibility goal

The user clarified after pausing that the emulator must target all firmware
for the synth. Felucca is an acceptance workload; its successful boot is not
the final compatibility boundary. The generic boundary gate is complete; reached instruction bring-up has
resumed through the shared CPU/hardware paths.

Preserve the completed separation of fixture machinery from the production
CPU/board model during further integration. CPU execution and
peripheral availability must not depend on firmware names, hashes or guest
PCs. Keep firmware hashes, section poisoning, checkpoint PCs and guest symbol
observations in optional test/validation harnesses. The former profile-dependent
CPU conditional-completion handling and device maps now use common behavior;
fixture loading and observations remain optional.

Make hardware behavior register-driven: support evidenced audio clock/buffer
configurations and interrupt sources independently of the loaded application.
Retain explicit faults for unimplemented behavior and preserve existing tests.
Use Felucca, diagnostics and available stock/other firmware as a compatibility
matrix with documented artifact identities and supported boot modes. Inventory
inputs first; do not promise all-firmware support from one successful boot.
Preserve the required subagents, independent review and serialized signed
milestones described below. This clarification does not authorize changes to
the stable Rust implementation or physical flashing.

## Accepted architecture and scope

Three logical layers share QEMU's execution, memory and virtual-time runtime;
they are not separate processes:

1. **CPU:** pi32v2 architectural state, instructions, memory operations and
   exception/interrupt entry and return. No firmware names, hashes, symbols,
   checkpoint addresses or board wiring may select architectural behavior.
2. **Hardware:** separate reusable JieLi SoC controllers/composition from
   FM-1 board wiring and attached components. SoC composition owns address
   decoding, shared syscon/pinmux/clock words and interrupt routing. Controllers
   own registers, transfers and IRQ outputs. Board composition owns evidenced
   flash/LCD/control/audio connections. Do not infer unverified components.
3. **Host interface:** Cocoa display and physical controls, QEMU/CoreAudio
   playback, CoreMIDI adapters, console and session controls. Exchange actual
   pixels, samples and physical/transport events; never modify guest variables
   to fabricate behavior. Headless execution uses the same hardware.

Optional validation instrumentation is outside the production behavior
contract. Hashes, poisoned sections, fixed observation PCs and symbol reads
remain valid test tools. Separate architectural reset from an explicit,
documented application-entry loader. Full ROM/SPL/flash boot is a distinct
capability requiring the necessary inputs and hardware evidence.

Preserve the working overlay and pinned QEMU build during extraction; a
repository-wide fork/layout migration is not a prerequisite. Preserve the
separate-process Rust reference and licensing boundary. No firmware patches,
stable Rust API changes or physical flashing are authorized by this plan.

### Ordered stages and completion gates

- [x] **1. Establish contracts and baseline.** Inventory available firmware
  artifacts and supported entry modes. Record hardware assumptions and
  fixture dependencies. Re-run the full ISA baseline (last full run predates
  IRQ changes) before changing CPU behavior. Preserve existing boot evidence.
- [x] **2. Establish generic boundaries.** Isolate test observers and loader
  state. Make CPU semantics and hardware availability independent of fixture
  selection. Preserve conditional-call completion and IRQ admission before
  attempting larger translation blocks. Preserve explicit protection faults
  and cached-code invalidation; plain QEMU ROM mappings can discard writes
  rather than reproducing the current fault. Gate on ISA, startup, splash,
  diagnostic and interrupt regressions, plus differently laid-out test images
  executing through the same hardware/CPU configuration.
- [ ] **3. Continue hardware bring-up through those boundaries.** Introduce
  resettable QEMU devices incrementally; centralize shared clock/routing words
  before overlapping controllers are added. Define reset, power cycle and
  storage erase separately. Test pending IRQ/DMA/timer cancellation, clock
  transitions and mid-transfer buffer mutation. Complete reached CPU/device
  gaps, including the current ED54/55 blocker, then real audio IRQ return,
  sustained home frames, 30 guest seconds and physical input acceptance.
- [ ] **4. Demonstrate compatibility.** Inventory and attempt unchanged
  Felucca plus available stock/other firmware using the same machine. Record
  identities, entry contracts and precise unsupported behavior. Different
  layouts/diagnostics are regression coverage, not proof of all-firmware
  support. Missing hardware behavior must not be replaced by image-specific
  runtime branches. Full hardware boot has its own acceptance gate.
- [ ] **5. Complete the macOS host.** Retain Cocoa; add actual controls,
  QEMU/CoreAudio output, CoreMIDI and the appropriate UART/USB transport.
  Confirm transport evidence before making serial a prerequisite. Define
  bounded queues, callback ownership, pause/rebase/late-event/overflow policy
  and release handling. Host callbacks must not mutate guest state. Separate
  host underruns from guest FIFO/DMA failures. Test signed-app TCG early and
  bundle non-system dependencies; release must not require Homebrew/mise.

Stage 3 evolves devices incrementally alongside reached behavior; it does not
require speculative implementation of the entire SoC before the next boot.
A small early packaging smoke test can proceed independently once scoped,
but Linux/Windows, MTTCG, live migration and speculative extra CPUs are deferred.

### Known feasibility limits

- Profile-dependent CPU behavior/device maps were removed in Stage 2. All
  images use one instruction per translation block for conditional correctness;
  batching/performance remain unvalidated.
- Current devices are mostly one-time initialized structs; watchdog reset
  terminates instead of resetting the machine.
- Syscon now canonically owns CLK_CON1/CLK_CON2/IOMAP_CON5. Future UART and
  other consumers must use that owner; extra words and a clock tree remain
  unevidenced. Current timer/SPI/audio rates are functional assumptions.
- ALNK captures a whole half and LCD a whole transfer at completion. This
  assumes stable source buffers; active-buffer mutation needs explicit tests
  and an evidenced consumption model before generic streaming claims.
- NOR supports reads but not persistent write/erase or full hardware boot.
  ADC, UART and connected USB MIDI/CDC remain missing. Current build captures
  audio samples but does not enable CoreAudio playback.
- Saved results prove unchanged splash and first audio IRQ entry, not a
  completed audio ISR, synthesis or real-time throughput. Keep controlled
  single-threaded tests separate from interactive timing. The proposal's
  2x offline synthesis and 30-minute live targets are unmeasured goals;
  benchmark fresh guest-produced frames, not merely a WAV sink or UI pacing.

### Current work ledger

- 2026-10-07: architecture reviewed with Sol 6.1 Extra high CPU, hardware and
  macOS reviewers. User authorized implementation. Updating the plan and
  establishing Stage 1 evidence precede the first Stage 2 extraction.
- 2026-10-07: Stage 1 complete. Full ISA baseline passed before CPU changes;
  `qemu-poc/ARCHITECTURE.md` records actual firmware/package identities and
  entry limitations. Approved plan saved as signed commit `b04f464`.
- 2026-10-07: Stage 2 first boundary extraction complete and independently
  reviewed. CPU fixture semantics removed; fixed hardware map and dedicated
  ALNK apply to all images; optional fixture loading/observations extracted to
  `fm1-test.c`. Generic application mode has no identity checks, poisoning,
  implicit stop/budget or observers. Existing test output formats preserved.
  Passed 98 CPU profile/default-observer-disabled runs, 32 common-map runs,
  full ISA, foundation/probe/QMP/fault checks, 10 IRQ cases, 18 ALNK cases,
  diagnostic long USB retry, display, and unchanged Felucca startup/reference
  and splash/reference. Renamed unchanged Felucca under the generic loader
  reached the same FF0C failure with matching instruction/time, device state
  and pixels. Evidence: `.deps/qemu-architecture-2026-10-07/` in the main repo.
  The common one-instruction TB boundary is intentional; performance and
  pending-IRQ-at-conditional-boundary/page-crossing coverage remain follow-up.
  Saved as signed commit `bb671f9`; signature verified.
- 2026-10-07: Stage 3 narrow FF0C signed-literal greater-than branch implemented
  and independently reviewed. Full ISA, 98 CPU profile runs and 10 IRQ cases
  pass. Focused validation passes 27 separate-reference comparisons, two
  independent six-byte conditional skips and six explicit faults. The reference
  has a conditional scanning/completion discrepancy; final selected THEN with
  ELSE fails explicitly pending evidence. Unchanged Felucca now reaches ECDC
  at `0x020023be`, 31 instructions later. The renamed image under the default
  generic loader matches every captured state field except the profile label;
  LCD pixels match exactly. Whole SRAM is not compared because fixture poisoning
  differs. Resettable controllers/shared syscon and DMA contracts remain open.

- 2026-10-07: user-authorized QEMU 11.1.2 upgrade complete. Fresh warning-clean
  build and every existing acceptance gate pass, including full ISA, profiles,
  common maps, peripherals, diagnostic startup/flash/long boot, display,
  ALNK/IRQ, QMP hold/resume and exact Felucca startup/splash reference checks.
  Reached unchanged Felucca and renamed generic replay match QEMU 10 state,
  whole SRAM, sample and LCD bytes exactly within each loader mode. The ECDC
  checkpoint remains unchanged. Cocoa functional gate passes 418 frames and
  ten key cycles without pausing; native window visual inspection was unavailable
  through the app inventory. Review found no new guest semantics. Old build
  and evidence are retained; the build rejects reuse of another release's cache.

- 2026-10-07: Stage 3 ECDC preindex word store implemented and independently
  reviewed. Full ISA, 98 CPU-profile runs, ten IRQ cases and boot/fault/QMP
  regressions pass. Focused gate passes 14 separate-reference comparisons,
  one generic replay and 15 faults. Disputed source/base aliases stay explicit
  faults; access-fault writeback is model policy, not hardware validation.
  Unchanged Felucca advances 307 instructions to EED2 at `0x0200249e`.
  Renamed default-loader replay matches captured state, sample and LCD bytes.
  Evidence and raw oracle disagreements are saved durably in the main repo's
  `.deps/qemu-indexed-store-2026-10-07/`. Audio acknowledgment/return and the
  home screen remain open.

- 2026-10-07: Stage 3 exact EED2 postincrement byte store implemented and
  independently reviewed. Focused gate passes 49 reference comparisons, one
  generic replay and nine faults; full ISA, ECDC, 98 profiles, ten IRQ cases
  and boot gates pass. Unchanged Felucca advances to F1E0 at `0x02002776`;
  renamed default-loader replay matches captured state, sample and LCD bytes.
  Evidence: `.deps/qemu-postincrement-store-2026-10-07/` in the main repo.
  The current audio IRQ has not acknowledged or returned.

- 2026-10-07: Stage 3 shared-word ownership extraction complete. A private
  syscon owns exactly CLK_CON1/CLK_CON2/IOMAP_CON5 with existing addresses,
  masks, widths, faults and names; ALNK validates active writes before canonical
  assignment and reads getters. Duplicate USB/ALNK fields and mappings removed.
  Existing capture keys and rates preserved. Independent source/validator review
  found no blockers. New 28-case gate passes; ALNK, common maps, observer-off,
  IRQ, peripheral/diagnostic USB and boot regressions pass. At the fixed EED2
  CPU revision, unchanged and renamed generic captures match all state fields,
  whole SRAM, samples and LCD bytes before/after extraction within each mode.
  Evidence: `.deps/qemu-syscon-2026-10-07/`. Clock tree, resettable ALNK and
  hardware/whole-machine reset remain open.

- 2026-10-07: Stage 3 packed-multiply parallel classification complete and
  independently reviewed. Only the existing E1E0 destination mask is added;
  arithmetic/literal and bundle execution semantics are preserved. Focused
  gate passes 39 reference comparisons, one generic replay and eight faults;
  full ISA, 98 profiles, ten IRQ cases and boot gates pass. Unchanged Felucca
  advances to ED54 at `0x02002782`; renamed generic capture matches state,
  samples and LCD bytes. Evidence:
  `.deps/qemu-parallel-packed-multiply-2026-10-07/` in the main repo.

### QEMU version upgrade

The user explicitly authorized QEMU **11.1.2** on 2026-10-07. The inherited
10.0.0 binary, source/build pins and validation caches are preserved in
main repo `.deps/qemu-11.1.2-upgrade-2026-10-07/baseline-10.0.0/`; its old build
also remains in `.cache/build-qemu-10.0.0/`. The active source is
`.cache/qemu-11.1.2/`, selected by `integrate.py`; build provenance is in
`build.py`. No instruction/device expansion is part of this upgrade.

The official release archive's detached signature verified against QEMU's
published fingerprint `CEACC9E15534EBABB82D3FA03353C9CEF108B584`.
Release commit: `4fc49f46dc95d4a27de2509e7fceb2931e91faeb`.
Archive SHA-256: `731b5681e4bb18be313231579b8efd0296c5b015fa36dc533874b639ba838016`.
Only required CPU/TCG/QOM/display interfaces and moved headers are adapted;
three-word TCG marker parsing preserves the existing validation assertions.
Meson 1.5.0 and other build dependencies remain within this release's accepted
requirements; no dependency pin change was necessary. Full acceptance results
are recorded in the work ledger above and `qemu-poc/BOOTING.md`.

## Permanent workspace and checkpoint

- Worktree: `/Users/simonjohansson/src/fm1-qemu-poc`
- Branch: `codex/qemu-poc`
- Pre-architecture firmware checkpoint: `74f2ce6` — first real audio IRQ entry.
- Current architecture milestones and verification are recorded in the work ledger.
- Architecture review baseline: `268e562`; later milestones are recorded in
  the work ledger and Git history.
- Main repository: `/Users/simonjohansson/src/fm1-emulator`
- Felucca repository: `/Users/simonjohansson/src/Felucca`
- Durable evidence: `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-felucca-2026-10-06/`

The worktree was moved using Git's worktree relocation from
`/private/tmp/fm1-qemu-poc`. All 193,091 files/symlinks matched the before/after
SHA-256/link manifest, and the uncommitted diff and branch were preserved.
Do not recreate or work in the old temporary path. Absolute-path build/Python
caches were retained as `.cache/build-before-relocation` and
`.cache/python-before-relocation`; fresh active caches were built successfully.
Relocation evidence is in the durable directory above.

Read `qemu-poc/BOOTING.md`, `qemu-poc/LICENSES.md`, and
`qemu-poc/HANDOVER_FELUCCA.md` before making changes. The handover describes the
original baseline; BOOTING and this plan contain the later actual progress.
Recheck status and applicable instructions. Never use `git -C`.

## Required subagents

**Use subagents throughout the resumed bring-up. Every subagent, including
any nested agent, must explicitly use `model="gpt-6.1-sol"` and
`reasoning_effort="xhigh"`.** Use `fork_turns="none"` with a self-contained
assignment and absolute handover/plan paths, or a supported positive history
count. Full-history forks do not accept these overrides. If the requested
configuration is unavailable, report it; do not silently substitute.

Use the parent and three worker slots in the current session, rather than
creating separate user-facing chats:

1. CPU worker: reached decoder/translation/IRQ semantics and focused ISA/IRQ
   regressions. Own `overlay/target/pi32v2/*`, `validate_isa.py`, and
   `validate_felucca_irq.py` when assigned.
2. Device worker: only reached ALNK/ADC/NOR/USB behavior and focused device
   regressions. Own dedicated device files and `validate_felucca_devices.py`.
3. Review/validation worker: independent source/disassembly and evidence
   review; separate Rust comparisons; acceptance validators. Assign exact
   files before any edits.
4. Parent: owns `overlay/hw/pi32v2/fm1-poc.c`, wiring/build integration,
   runners, documentation and durable evidence; runs shared builds/tests
   and creates discrete signed commits.

Give each shared file one owner at a time. **Serialize shared builds,
acceptance runs and commits.** Workers must signal stable code before the
parent builds. Retain independent review before committing a milestone.
Existing agent identities may not survive a new session; recreate the roles
with the required explicit settings if needed.

## Pinned inputs and provenance

Use existing `/Users/simonjohansson/src/Felucca/build/` artifacts:

| File | SHA-256 |
| --- | --- |
| felucca.bin | 12a4b4ea47248467f566ec3b6984b08f2f89d6ef5a8e9e494cab5184e8fadb36 |
| felucca.elf | 9404c41dcd5c7516243e99cdbae1a4e731ea0c69e13dea397a8db343244b5deb |
| felucca.dis | 0d992c3113a1765f21a10bfb233bef81ee908073f26a16ea1f9d13c6fe897618 |

Raw length is `0x65f84`; ELF file-backed load segments match the raw image.
These hashes establish artifact identity, not production from the inspected
source HEAD `1e838e17e170b20ff09b9660c9a7171aadfc5dca`.
**Do not rebuild, modify, patch or replace the firmware for acceptance.**

Application entry is `0x02000120`, boot parameter r0 is `0x01c7fe08`, guest SP
is `0x01c7a000`, SSP is `0x01c7c000`. Use one CPU. Keep the explicit cold
noinit/loader state and hash-bound poisoned startup sections; the guest must
initialize memory itself. Erased NOR is seeded with unchanged application
bytes at physical offset `0x4120`.

Edit `qemu-poc/overlay/`, never generated `.cache/qemu-11.1.2` files. Existing
build regeneration is authorized. Fresh QEMU C/Python stays GPL-2.0-or-later;
Rust and firmware stay GPL-3.0-only. Use register/encoding/interface facts,
write fresh implementation, and keep Rust as a **separate executable using
public interfaces**. Never copy/link its implementation into QEMU.

## What is verified now

- Startup copies: all 512 KiB SRAM, 16 GPRs, 16 SPRs and pre-LCD pixels match
  the separate Rust process at the selected checkpoint.
- Unchanged splash at `0x0200d0c2`: 38,257,079 instructions, 306,056,640 ns;
  actual LCD SPI/DMA generates all 240x240 pixels. Exact Rust pixel match;
  RGB SHA-256 `93f011c5d3a19e513c75ee4f7142e81513579b25255d157a73c81fe34db676c2`.
  Guards, stacks, RAM initialization, mailbox/vectors and watchdog are valid.
- Dedicated ALNK model: exact reached widths/configuration, rational 44.1 kHz
  deadlines, 256 stereo frames per 512-word half, level pending/W1 ack,
  coalescence and bounded capture of actual SRAM samples. Six positive and
  twelve negative probes pass. Skipped-callback accounting is inspected but
  not exercised by normal deterministic probes.
- IRQ11/63: dynamic vectors, highest enabled pending priority, mask/global
  gates, independent device acknowledgments and per-source counters. Eight
  positive and two negative focused cases pass, using actual wrapper opcodes
  with disposable instrumented callees. Nonnested restoration is retained;
  simultaneous equal-priority arbitration fails explicitly.
- Original foundation/probe/QMP regressions pass after IRQ changes.
- Unchanged FM-1_980 diagnostic passes: 640 balanced timer IRQs and the long
  disconnected-USB retry run with 6,173 balanced IRQ/RTIs, 153,163 foreground
  loops, 1,029 guest ms, 12 SIE requests and 240,000 guest polls.
- Full ISA gate passed again on 2026-10-07 before and after the first
  architecture extraction. The IRQ, startup and splash gates also passed;
  the subsequent FF0C milestone also passes full ISA/profile/IRQ gates. The
  latest firmware blocker is ED54, recorded below.

Recent signed commits, all signatures verified:

- `d38dafc`: exact unchanged splash validation.
- `b50ed3d`: standalone pop-PC return.
- `502244b`: reached ALNK DMA configuration/model/tests.
- `d38ad8d`: compact halfword-store tail classification in parallel bundles.
- `74f2ce6`: reached audio IRQ source selection and focused gates.

## Current firmware blocker: signed halfword load ED54/55

Latest unchanged boot: `.cache/felucca-validation/after-parallel-packed-multiply/`.
The packed multiply bundle and following parallel addition now execute through
existing arithmetic and incoming-value machinery. Execution fails explicitly:

- PC: `0x02002782`; opcode words `ED54/63BC`.
- Vendor disassembly: `r6 = h[r11+60] (s)`; the next instruction is
  `ED55/52FC`, `r5 = h[r15+300] (s)`. Establish valid bits and offset fields
  from independent evidence before extending the existing ED50/51 unsigned path.
- Instructions: 43,279,576; virtual time: 346,236,616 ns.
- IRQ11 entries: 1; IRQ11 returns: 0; IRQ63 entries/returns: 0.
- ALNK: five completions, four coalesced, zero skipped, 2,560 zero sample words;
  pending `0x80`, no acknowledgment. Splash intact, guard messages and watchdog
  expiry zero. This is not completed audio service, synthesis or a home screen.
- QEMU SHA-256:
  `29c781a06380345c660aa1e5ccb371b78b326c739470dabd64216e08a9f5ed35`.
- Generic replay:
  `.cache/felucca-validation/after-parallel-packed-multiply-generic/`.
- Durable evidence: main repo `.deps/qemu-parallel-packed-multiply-2026-10-07/`;
  preceding CPU, syscon, upgrade and architecture checkpoints are retained.

### Resolved F1E0 parallel multiply checkpoint

`F1E0/1EB3 + 3381` at `0x02002776` pairs `r0 = r1 * 0x598` with
`[sp+76] = r1`. The change only classifies the existing E1E0 destination;
scalar decoding, packed literals and the incoming-register bundle machinery
remain unchanged. It applies to every image through the shared CPU path.

`validate_parallel_packed_multiply.py` passes 39 reference comparisons, one
generic-loader replay and eight explicit faults. It checks incoming source,
store value/address aliases, all destinations, product truncation, PSR,
six/eight-byte conditional sizing and one-bundle retirement. Tail faults occur
before multiply effects under the existing model policy. Hardware fault state
and inherited unused packed-literal discrepancies remain unverified. Full ISA,
98 profiles, ten IRQ cases and boot gates pass. Unchanged Felucca advances two
bundles to ED54; the renamed default-loader replay matches captured state
except the profile label, sample bytes and LCD pixels.

### Resolved EED2 checkpoint

`EED2/2510` at `0x0200249e` stores the incoming source's low byte at the old
base, then advances the base by the unsigned eight-bit immediate stride.
Source==base stores the old pointer byte; all register fields use common CPU
behavior. The exact opcode was added without changing neighboring operations
or the parallel classifier.

`validate_postincrement_store.py` passes 49 separate-reference comparisons,
one generic-loader replay and nine explicit faults. It verifies stride
boundaries, byte truncation/neighbor preservation, all GPR fields/aliases,
PSR, conditional sizing, retirement, guards and fault-before-writeback model
state. Hardware fault state is unverified; a successful 32-bit wrapping
writeback cannot be observed in the available memory map. Full ISA, ECDC,
98 CPU-profile runs, ten IRQ cases and boot/fault/QMP gates pass. Unchanged
Felucca advances another 396 instructions to F1E0; its renamed default-loader
replay matches all captured state fields except the profile label and all
sample/LCD bytes. Whole SRAM is not compared across loader initialization modes.

### Resolved ECDC checkpoint and alias limitation

`ECDC/5013` at `0x020023be` is an unscaled wrapping preindex word store:
base receives the incoming base+index, then the source word is stored there.
The opcode applies to every image through the common CPU path. Incoming
source==index and base==index aliases are supported. Source==base (including
all-equal) remains explicitly unsupported before effects: the pinned Apache
SLEIGH and separate public reference disagree on the value to store.

`validate_indexed_store.py` passes 14 separate-reference comparisons, one
generic-loader replay and 15 explicit faults. Tests cover wraparound,
register selection, supported aliases, conditional skip/selection, PSR,
word width, neighboring memory, guards, access faults and retirement. Access
fault writeback tests verify the existing model policy; the reference exposes
no fault state, so hardware fault-state ordering and priority are unverified.
Full ISA, 98 CPU-profile runs, ten IRQ cases and foundation/probe/QMP/fault
checks pass. Renamed unchanged Felucca under the default generic loader
matches all captured state fields except the profile label and all sample/LCD
bytes at the new EED2 stop. Whole SRAM is not compared across differently
initialized loader modes. No MMIO behavior or firmware bytes changed.

No Felucca live viewer has been launched.

### Resolved FF0C checkpoint and conditional limitation

The previous stop at `0x020022aa` (`FF0C/1FFF/0C48`) was a six-byte signed
literal branch: r1 > -1, displacement relative to PC+6. The narrow
implementation sign-extends the 12-bit literal and 16-bit word displacement,
preserves PSR/RETS and retires once. Existing unsigned forms retain their
semantics. `validate_long_signed_branch.py` tests boundaries, register
selection, conditional sizing and unsupported neighboring opcodes.

Black-box Rust reference runs disagree on conditional scanning/completion for
FF0C: skipped arms land on its displacement word, while a final selected THEN
with ELSE executes ELSE unexpectedly. Independent six-byte skip expectations
are tested; final selected THEN with ELSE is explicitly unsupported before
retirement/branch effects. Do not copy the reference implementation or claim
hardware validation for this unresolved combination. Disagreement evidence is
preserved separately. The old `after-irq-selection/` and
`after-generic-boundaries/` checkpoints remain historical evidence.

Next implementation sequence:

1. Preserve the QEMU 11.1.2 upgrade gate and source pin above while continuing
   reached instruction and device bring-up.
2. Assign the reached ED54/55 signed halfword loads to the CPU worker. Inspect
   existing unsigned ED50/51 addressing before extending the shared path.
   Resolve valid fields independently; test sign extension, offsets, aliases,
   PSR/retirement, conditional sizing and explicit guard/access faults.
3. Obtain independent review; build and run focused/full ISA/profile/IRQ gates;
   repeat unchanged bounded boot under a new evidence label. Commit only
   validated changes. Repeat for each actual subsequent CPU/MMIO failure.
4. Hardware path: shared CLK_CON1/CLK_CON2/IOMAP_CON5 ownership is complete.
   Convert ALNK to the first resettable QEMU device, retaining canonical shared
   words and rates. Verify timer cancellation, pending IRQ clearing,
   repeated reset and reconfiguration without resetting unrelated controllers.
   Whole-machine reset remains unsupported until all components participate.

## Next milestones after the blocker

### Complete real audio and timer service

Observe actual IRQ11 acknowledgment/RTI, rendered-half counters, TIMER5 IRQ63
activity and foreground progress. Source11 vector is `0x01c7fe2c`, priority3;
source63 vector is `0x01c7fefc`, priority1. Both device levels remain pending
until their own acknowledgment; never clear another source on selection.

The current CPU deliberately blocks nesting as before. Only introduce the
nesting actually required by captured firmware behavior, with explicit
higher-priority admission, supervisor/foreground stack handling, prior IRQ
context/RETI/ICFG restoration and independent validation. Wrapper source alone
cannot establish undocumented hardware shadow behavior. Do not claim nesting
support from current balanced nonnested probes.

### Implement ADC only when reached

No ADC model is implemented yet. Prior inspection predicts first foreground
ADC control write at `0x020059ac` to `0x13100`, then WLA read at
`0x020059ae` from `0x11900` (confirm actual execution first).
Planned private registers: CON `0x13100`, RES `0x13104`, WLA `0x11900`, exact
aligned words. Guest uses channels3 battery and4 master, start/clear bit6,
read-only done bit7, enable bit4, with conversion interrupts disabled.
Proposed deterministic raw inputs: battery600, master512; functional 10 us
latency, explicitly not hardware calibration. Cover conversion, ack,
cancel/restart, read-only result, width/configuration faults and stale timer
cancellation. Preserve existing device maps and public Rust boundaries.

### Reach a complete home frame

Pinned observation candidates (verify bytes and actual captured state):

- `felucca_dbg` at `0x01c7c040`, 19 words: magic, halves, max_us, nested,
  in_audio, late, timer_irqs, ui_frames, last_us, cpu_q8, boots, stage, page,
  home, prev_stage, prev_page, prev_home, prev_rst, prev_frames.
- Main `ui_draw` call `0x0200dcc0`, stage9 store `0x0200dccc`.
- Foreground loop top `0x0200e25c`, watchdog feed call `0x0200e266`.
- `fm1_ms` `0x01c11710`; `ui` `0x01c12720`; `song` `0x01c11a1c`.

The first loop-top visit precedes UI drawing. ui_draw does not finish with an
explicit lcd_sync; do not mistake a busy final transfer for a complete frame.
Observe a later loop boundary with home=1/stage=9, progressing ui_frames and
visible idle LCD. Bind observers to unchanged input hash and pinned bytes.
Never synthesize guest counters or write guest RAM to satisfy a checkpoint.

### Validate 30 guest seconds and physical input

Require repeated complete home frames, progressing milliseconds/foreground,
per-source entry/return/ack balance, sound stacks/guards, no crash/watchdog
expiry or LCD/P33 timeouts. TIMER5 expirations can coalesce during audio;
compare actual service rather than requiring one IRQ per expiration.

At `fm1_ms >= 30001`, guest bootguard at `0x01c7c08c` must have magic
`0x42475244`, failed=0, pending=0. The clear stores precede PC `0x0200e2c4`.
Default 100M instructions is only 0.8 guest sec at 8 ns; explicitly extend
bounded instruction/host timeout settings for sustained acceptance. About
3.75B instructions are needed for 30 seconds at that functional clock.

Use only physical matrix closures after the guard period, with actual guest
scans/debounce, baseline snapshots and resulting UI/audio changes:

- Note: column3/packed row4 -> key14 -> notes bit0.
- OCT-minus: column0/row4 -> button0; verify octave decreases one.
- Encoder0 SELECT: A column0/row0, B column1/row0; positive detent sequence
  AB `00 -> 01 -> 11 -> 10 -> 00` (close B, close A, open B, open A).
- Keys need eight complete scan frames per press/release. Encoder states need
  at least two frames; learn rest for 40 frames, keep detent phases under 40
  frames, and wait >60 ms before the first detent to avoid acceleration.
- Default BPM is 120 by pinned data; verify captured baseline then +1 rather
  than assuming defaults. Matrix frame has 11 TIMER5 services.
- Candidate `fm1_in` `0x01c11e40`: notes+0, buttons+4, enc_steps+0x6c,
  frames+0x7c. Song BPM halfword+0, selected byte+0x39, octave signed byte+0x3a.

Verify note-on activity reaches actual nonzero ALNK samples. Release can
have an ADSR tail; do not require instantaneous silence.

### Compare reference and expose the running viewer

Startup/splash reference comparisons are established. The current Rust public
USB API automatically attaches a host when guest registers enable it; it has
no public disconnected-host switch or step mode excluding only USB advance.
Do not modify stable Rust implementation or force guest MMIO state to align
it. Disclose this environment difference in any later home comparison;
compare only meaningful supported milestones/pixels/device effects.

After sustained acceptance passes, add the native Felucca launcher. Measure
guest/audio/watchdog progress before choosing its functional clock. The small
fixture's shift8 clock must not be carried into Felucca correctness claims.
Use QMP running status and progressing state to verify execution remains
active; never use diagnostic KEEP_OPEN/checkpoint hold. No Pixman screendump
is available, so use actual modeled LCD captures for pixel evidence. Keep
firmware unchanged, document command/hash/input scenario/limits, and leave
execution running for the user. Host playback and interactive keyboard/mouse
controls are subsequent work, not prerequisites for this verified boot.

## Commands, evidence and working rules

Run from `/Users/simonjohansson/src/fm1-qemu-poc`:

```sh
git status --short
git verify-commit 74f2ce6
mise exec python@3.13.15 -- python qemu-poc/build.py
mise exec python@3.13.15 -- python qemu-poc/run_felucca.py --label after-next-fix
mise exec python@3.13.15 -- python qemu-poc/validate_isa.py
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_irq.py
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_devices.py
```

Use relevant established gates before declaring support: validate_boot,
validate_diag_boot, validate_display, validate_felucca startup/reference and
validate_felucca_splash. Shared runs/builds remain serialized. Preserve each
new boot label, nearby disassembly, whole SRAM, LCD/sample bytes, registers,
clock/command/settings and executable/input hashes in cache **and** durable
`.deps`; `milestones.json` maps observed failures to commits. Keep unsupported
operations explicit. Never update tracked snapshots simply to pass a test.

Create discrete signed commits; verify every signature with `git verify-commit`.
Follow Tim Pope's message conventions, include the relevant user prompt in the
body, no emojis. Reuse this feature worktree; if another is necessary, use
`git worktree add`, not `git checkout -b`. Runtimes use mise according to this
experiment's pins; keep the main repository's separate uv ownership intact.
No hardware access/flashing is needed or authorized by this plan.

The permanent path may be outside a future session's default writable roots.
Use a properly scoped escalation citing the original attached bring-up request
and the relocation authorization if required; do not bypass sandbox review.
On resume, inspect worker state and assign explicit ownership before edits.
Record completed checks and the next concrete action in the work ledger.
