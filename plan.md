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
  gaps, then real audio IRQ return,
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
- ALNK is now a private resettable SysBus device. Other controllers remain
  mostly one-time initialized structs; watchdog reset still terminates.
  Local ALNK reset is tested; whole-machine and physical reset remain open.
- Syscon now canonically owns CLK_CON1/CLK_CON2/IOMAP_CON5. Future UART and
  other consumers must use that owner; extra words and a clock tree remain
  unevidenced. Current timer/SPI/audio rates are functional assumptions.
- ALNK captures a whole half and LCD a whole transfer at completion. This
  assumes stable source buffers; active-buffer mutation needs explicit tests
  and an evidenced consumption model before generic streaming claims.
- NOR supports reads but not persistent write/erase or full hardware boot.
  UART and connected USB MIDI/CDC remain missing. SAR supports reviewed polling
  inputs/configuration with uncalibrated timing. Current build captures
  audio samples but does not enable CoreAudio playback.
- Saved results now prove unchanged splash and completed audio/timer IRQ
  service. Synthesis, home and real-time throughput remain unverified. Keep
  controlled single-threaded tests separate from interactive timing. The proposal's
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

- 2026-10-07: Stage 3 exact ED54/55 signed halfword loads complete and
  independently reviewed. Focused gate passes 68 reference comparisons, one
  generic replay and eight faults; full ISA, 98 profiles, ten IRQ cases and
  boot gates pass. Unchanged Felucca advances 169 instructions to DB01 at
  `0x02002eba`; renamed generic state, samples and LCD match. Evidence:
  `.deps/qemu-signed-halfword-load-2026-10-07/`. Audio IRQ acknowledgment and
  return remain open.

- 2026-10-07: Stage 3 compact 1B00 multiply parallel classification complete
  and independently reviewed. Only one destination-mask entry changes.
  Focused gate passes 41 reference cases, one generic replay and eight faults;
  full ISA, 98 profiles, ten IRQ cases and boot gates pass. Unchanged Felucca
  advances 168 instructions to EDD8 at `0x02001c58`; renamed generic state,
  samples and LCD match. Evidence:
  `.deps/qemu-parallel-register-multiply-2026-10-07/`. CPU changes are held
  during the next resettable ALNK acceptance milestone.

- 2026-10-07: Stage 3 ALNK lifecycle conversion complete and independently
  reviewed. Private SysBus child retains the same MMIO/rates/IRQ connector;
  Resettable enter cancels timer/local DMA/capture state, hold lowers only
  ALNK's IRQ. Syscon words, SRAM, CPU and other controllers are preserved.
  Eighteen reset/schedule cases pass, including cancellation, pending clearing,
  repeated reset, fresh phase and surviving real TIMER5 IRQ63 ack/RTI.
  Syscon, ALNK, maps, profiles, IRQ, peripherals, diagnostics and boot pass.
  At fixed CPU revision 0ba8792, fixture and renamed generic captures match
  all state fields, whole SRAM, sample and LCD bytes before/after. Evidence:
  `.deps/qemu-alnk-lifecycle-2026-10-07/`. Whole-machine/watchdog/hardware
  reset and runtime unrealize/re-realize remain unvalidated.

- 2026-10-07: Stage 3 EDD8 kind-A signed indexed load complete and independently
  reviewed. Shared scaled address path reused; unsigned/classifier behavior
  retained. Focused gate passes 73 reference cases, one generic replay and
  17 faults; full ISA/profile/IRQ/boot pass. Unchanged firmware advances eight
  instructions to AF88 at `0x02001c70`; renamed generic state/sample/LCD match.
  SLEIGH body/comment shift discrepancy and rejected speculative FDD8 test
  are preserved in `.deps/qemu-signed-indexed-halfword-load-2026-10-07/`.
  Existing FDD8 branch behavior is retained. Audio service remains open.

- 2026-10-08: Stage 3 compact signed-right immediate shift complete and
  independently reviewed. Only two scalar decoder lines change; logical
  shifts/classifier retained. Focused gate passes 126 reference cases, one
  generic replay and five faults; full ISA/profile/IRQ/boot pass. Unchanged
  firmware advances five instructions to EE01 at `0x02002f8e`; renamed
  generic state/sample/LCD match. Evidence:
  `.deps/qemu-arithmetic-shift-2026-10-08/`. Audio service remains open.

- 2026-10-08: Stage 3 exact EE00 signed register greater-than complete and
  independently reviewed. Only admission and signed condition change. Focused
  gate passes 70 reference cases, one generic replay and 16 model faults;
  full ISA/profile/IRQ/boot pass. Unchanged firmware advances ten instructions
  to F0E0 at `0x02002fde`; renamed generic state/sample/LCD match. Inherited
  taken-exit predicate completion/IRQ limitation and conservative unused-bit
  policy are recorded explicitly. Evidence:
  `.deps/qemu-signed-register-branch-2026-10-08/`. Audio service remains open.

- 2026-10-08: Stage 3 E0E0 packed-add parallel classification complete and
  independently reviewed. Sole destination-mask entry preserves scalar/bundle
  behavior. Focused gate passes 71 reference cases, one generic replay and
  nine faults; full ISA/profile/IRQ/boot pass. Unchanged firmware advances
  980 instructions to ED34 at `0x02002010`; renamed generic state/sample/LCD
  match. Evidence: `.deps/qemu-parallel-packed-add-2026-10-08/`. ADC research
  now records command-style start and separate shared analog ownership.
  Audio service remains open.

- 2026-10-08: Stage 3 ED30 signed12 greater-or-equal IF complete and
  independently reviewed. Exact admission/condition preserve common predicate
  machinery. Focused gate passes 139 reference cases, one generic replay and
  12 model faults; full ISA/profile/IRQ/boot pass. Unchanged firmware advances
  five instructions to EEB4 at `0x02002020`; renamed generic state/sample/LCD
  match. Primary unsigned-token discrepancy and inherited predicate limits
  are retained. Evidence: `.deps/qemu-signed-literal-if-2026-10-08/`.
  Audio service remains open.

- 2026-10-08: Stage 3 EEB0 signed12 less-or-equal IF complete and independently
  reviewed. Focused gate passes 141 reference cases, one generic replay and 11
  faults; updated GE positives and full ISA/profile/IRQ/boot pass. Unchanged
  firmware advances 121 instructions to ED13 at `0x0200367e`; renamed generic
  state/sample/LCD match. Only the obsolete GE EEB4 rejection is retired.
  Primary packed-token discrepancy and inherited predicate limits are retained.
  Evidence: `.deps/qemu-signed-literal-le-if-2026-10-08/`. Audio remains open.

- 2026-10-08: Stage 3 ED10 signed-register greater-or-equal IF complete and
  independently reviewed. Focused gate passes 158 reference cases, one generic
  replay and 20 faults; full ISA/profile/IRQ/boot pass. Unchanged firmware
  advances two instructions to E435 at `0x02003686`; renamed generic state/
  sample/LCD match. Canonical low-byte/reference disagreement and precise
  reached-body retirement are retained. Evidence:
  `.deps/qemu-signed-register-if-2026-10-08/`. Audio service remains open.

- 2026-10-08: Stage 3 E435 signed minimum complete and independently reviewed.
  Focused gate passes 140 reference cases, one generic replay and 16 faults;
  updated ED10 gate passes unchanged 158 positives and 19 faults. Full ISA/
  profile/IRQ/boot pass. Unchanged firmware advances 8,404 instructions to
  F430 at `0x02003992`; renamed state/sample/LCD match. Evidence:
  `.deps/qemu-signed-minimum-2026-10-08/`. Audio service remains open.

- 2026-10-08: Stage 3 exact E430 ABS and guarded F430 bundle support complete
  and independently reviewed. Focused gate passes 99 reference cases, one generic
  replay and 21 faults; adjacent minimum and full ISA/profile/IRQ/boot pass.
  Unchanged firmware advances two instructions to E434 at `0x0200399c`; renamed
  state/sample/LCD match. Source snapshots, extended-tail role, INT_MIN and
  pre-effect malformed-head rejection are covered. Evidence:
  `.deps/qemu-parallel-absolute-2026-10-08/`. Audio service remains open.

- 2026-10-08: Stage 3 exact E434 scalar signed/unsigned maximum complete and
  independently reviewed. Focused gate passes 191 reference cases, one generic
  replay and 18 faults; minimum and full ISA/profile/IRQ/boot pass. Unchanged
  firmware advances one instruction to EE80 at `0x020039a0`; renamed state/
  sample/LCD match. Direct vendor/reference authority and absent exact pinned
  maximum constructor are explicit. Evidence:
  `.deps/qemu-maximum-2026-10-08/`. Audio service remains open.

- 2026-10-08: Stage 3 exact EE80 signed register less-or-equal branch complete
  and independently reviewed. New and updated EE00 gates each pass 70 reference
  cases, one generic replay and 15 faults; full ISA/profile/IRQ/boot pass.
  Unchanged firmware advances two instructions to ED81 at `0x020039b0`; renamed
  state/sample/LCD match. Canonical policy and inherited outgoing-arm/follow-up
  IF discrepancy are retained. Evidence:
  `.deps/qemu-signed-register-le-branch-2026-10-08/`. Audio service remains open.

- 2026-10-08: Stage 3 exact ED80 signed register less-than branch complete and
  independently reviewed. New LT and updated GT/LE gates each pass 70 reference
  cases, one generic replay and 14 faults; full ISA/profile/IRQ/boot pass.
  Unchanged firmware advances seven instructions to EDDC at `0x020039fc`;
  renamed state/sample/LCD match. Canonical policy and inherited outgoing-arm
  completion discrepancies remain explicit. Evidence:
  `.deps/qemu-signed-register-lt-branch-2026-10-08/`. Audio service remains open.

- 2026-10-08: Stage 3 exact EDDC signed pre-indexed halfword load complete and
  independently reviewed. Focused gate passes 117 reference cases, one generic
  replay and 22 faults; adjacent word-preindex and full ISA/profile/IRQ/boot pass.
  Unchanged firmware advances 2,712 instructions to E86C at `0x02003aac`;
  renamed state/sample/LCD match. Load-wins aliases, partial modeled fault
  writeback, deferred unsigned kind0 and absent exact primary are explicit.
  Evidence: `.deps/qemu-signed-preindexed-halfword-load-2026-10-08/`.
  Audio service remains open.

- 2026-10-08: Stage 3 exact E86C word memory left-shift complete and independently
  reviewed. Gate: 189 reference cases, one generic replay, one modeled USB case
  and 13 faults; adjacent signed-load and full ISA/profile/IRQ/boot pass. Actual audio/timer service now completes:
  extended unchanged replay has 101 IRQ11 and 5,104 IRQ63 returns; renamed captured
  state/sample/LCD match. Next missing mapping is SAR CON `0x13100`, parent
  bundle PC `0x020059a8`; no home/nonzero synthesis yet. Evidence:
  `.deps/qemu-memory-left-shift-2026-10-08/`.

- 2026-10-08: Stage 3 reusable SAR ADC and separate canonical WLA owner complete,
  independently reviewed. Gate: 76 model probes passes; full ISA/profile/IRQ/boot
  and syscon/ALNK lifecycle/audio-device regressions pass. Unchanged firmware
  advances 2,571 instructions to parallel signed division at `0x0200e2fc`;
  renamed captured state/sample/LCD match. Raw inputs, 10 us/command/reset policy
  and fatal-state/source-review limits remain explicit. Evidence:
  `.deps/qemu-adc-2026-10-08/`. Home/synthesis still open.

- 2026-10-08: Stage 3 exact F1F4 parallel signed division complete, independently
  reviewed. Four classifier lines preserve scalar/helpers/common bundle paths.
  Research 450 probes / 424 full-state completions; focused 134 reference,
  one generic and 35 modeled faults pass, plus full/adjacent CPU gates.
  Unchanged boot advances 54 instructions to FF41 at `0x0200db62`; renamed
  captured state/sample/LCD match. Tail fault phases and reference-valid deferred
  mode0/conflicts remain qualified model policies. Evidence:
  `.deps/qemu-parallel-signed-divide-2026-10-08/`. HOME/synthesis still open.

- 2026-10-08: Stage 3 exact FF41 register-inequality branch and six-byte sizing
  complete, independently reviewed. Dedicated final THEN+ELSE guard preserves
  existing helpers and predicate policy. Focused 73 reference positives,
  13 independent primary model positives, one generic and 22 modeled faults
  pass, with full/adjacent branch gates. Unchanged boot advances 44 instructions
  to F194 at `0x020086c4` in HOME input processing; renamed captures match.
  First HOME flag/frame count is set, but no completed frame/synthesis claim.
  Reference scanner/conditional and canonical-policy differences are retained.
  Evidence: `.deps/qemu-long-register-ne-branch-2026-10-08/`.

- 2026-10-08: Stage 3 exact E194 mode 2 parallel indexed-bit AND admission
  complete, independently reviewed. Scalar index<32 policy, helpers and
  common bundle behavior are unchanged. Focused 187 reference positives,
  one generic and 38 modeled faults pass, with full/adjacent CPU gates.
  Unchanged boot advances 241 instructions through HOME input to E9DE
  at `0x0200db94`; renamed captures match. HOME frame1/stage2 remains
  incomplete drawing. Reference modulo32 disagreement, deferred modes,
  conservative conflicts and original fixture correction are retained.
  Evidence: `.deps/qemu-parallel-indexed-bit-and-2026-10-08/`.

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
  latest firmware blocker is E9DE SP-relative byte memory, recorded below.

Recent signed commits, all signatures verified:

- `d38dafc`: exact unchanged splash validation.
- `b50ed3d`: standalone pop-PC return.
- `502244b`: reached ALNK DMA configuration/model/tests.
- `d38ad8d`: compact halfword-store tail classification in parallel bundles.
- `74f2ce6`: reached audio IRQ source selection and focused gates.

## Current firmware blocker: E9DE SP-relative byte memory

Latest unchanged boot: `.cache/felucca-validation/after-parallel-indexed-bit-and/`.
Vendor `b[sp+330] = r8` is reached in `fm1_cstart` after HOME input
processing. Establish exact SP-relative byte fields and canonical policies
from pinned primary and independent reference before implementing it.
The HOME flag and first frame count are set, but stage2 precedes completed drawing.

- PC `0x0200db94`, words `E9DE/814A`.
- Instructions 116,555,191; virtual time 932,441,536 ns.
- IRQ11 entries/returns 101/101;
  IRQ63 entries/returns 5105/5105.
- ALNK completions 106, acknowledgments 101,
  coalesced 5, pending `0x0`;
  54,272 captured sample words, 0 nonzero.
- LCD visible=True, busy=True; guard debug message
  `0x0`, watchdog expirations 0.
  Audio and timer service return successfully; HOME flag/frame1/stage2 is set, but drawing and synthesis remain incomplete.
- QEMU SHA-256: `f423a51d0e915ce68680c90a24006c4712bc194d31620845a0f652e7a38fa8fa`.
- Generic replay: `after-parallel-indexed-bit-and-generic`.
- Durable evidence: main repo `.deps/qemu-parallel-indexed-bit-and-2026-10-08/`.

### Resolved F194 parallel indexed-bit AND

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

### Resolved FF41 long register-inequality branch

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

### Resolved F1F4 parallel signed division

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

### Resolved SAR ADC and canonical analog ownership

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

### Resolved E86C word memory left-shift

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

### Resolved EDDC signed pre-indexed halfword load

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

### Resolved ED80 signed register less-than branch

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

### Resolved EE80 signed register less-or-equal branch

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

### Resolved E434 signed and unsigned maximum

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

### Resolved E430 absolute value and F430 bundles

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

### Resolved E435 signed minimum

Exact scalar E435 mode1 implements signed32 minimum with destination in
second-word bits12..15, left operand bits4..7 and right operand bits8..11.
The primary constructor and vendor disassembly agree. Mode0 unsigned minimum,
other scalar families, unsupported modes and parallel classification remain
unchanged. No firmware identity or guest PC selects CPU behavior.

`validate_signed_minimum.py` passes 140 separate-reference cases, one generic
replay and 16 model faults. Signed boundaries, every operand field, aliases,
mode0 controls, PSR/count and five actual conditional-body sequences are checked.
Modes2..15, deferred F435 bundle rejection and the PC guard retain precise
fault state. The old ED10 gate removes only its now-obsolete minimum body fault;
all 158 positive cases remain, with 19 current model faults. Hardware fault
state remains unverified.

Full ISA, 98 profiles, ten IRQ cases and boot pass. Unchanged firmware advances
8,404 instructions to F430; renamed loading matches all captured state except
profile and all sample/LCD bytes. Whole SRAM is not compared across different
initialization modes. IRQ11 has still not acknowledged or returned.

### Resolved ED10 signed-register greater-or-equal IF

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

### Resolved EEB0 signed-literal less-or-equal IF

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

### Resolved ED30 signed-literal greater-or-equal IF

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

### Resolved E0E0 packed-add parallel classification

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

### Resolved EE00 signed register greater-than branch

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

### Resolved immediate arithmetic right shift

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

### Resolved EDD8 signed indexed halfword load

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

### Resolved DB01 compact multiply parallel head

Only the existing scalar 1B00 family's destination mask is added to the
parallel classifier. Product arithmetic, incoming-register snapshots, tail
execution and four/six-byte bundle sizing remain unchanged for all images.

`validate_parallel_register_multiply.py` passes 41 separate-reference cases,
one generic replay and eight faults. It verifies nonzero products, old
destination/source/address aliases, all register fields, squares, modulo-32
products, full PSR, conditional sizing and one retirement. Hardware fault
state remains unverified. Full ISA, 98 profiles, ten IRQ cases and boot gates
pass. Unchanged Felucca advances 168 instructions to EDD8. Renamed default
loading matches state except the profile label and all sample/LCD bytes;
whole SRAM is not compared across loader modes.

### Resolved ED54/55 signed halfword loads

Exact ED54/55 load a little-endian halfword and sign-extend it to 32 bits.
The even unsigned offset spans 0..510, addresses use the incoming base and
PSR is preserved. Operands with bit 0 set and ED56/57 remain explicitly unsupported
because independent encoding evidence is insufficient for those forms.
Existing unsigned loads and the parallel classifier are unchanged.

`validate_signed_halfword_load.py` passes 68 separate-reference comparisons,
one generic replay and eight explicit faults. Offset/sign boundaries, all
GPR fields and aliases, readable flash/SRAM limits, read-through write guards,
PSR, conditional sizing and retirement are covered. Faults check model state;
hardware fault ordering remains unverified. Full ISA, 98 profiles, ten IRQ
cases and boot gates pass. Unchanged Felucca advances 169 instructions to
DB01. Renamed default loading matches captured state except the profile label
and all sample/LCD bytes; whole SRAM is not compared across loader modes.

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

1. Preserve the QEMU 11.1.2 upgrade pin and generic boundaries while continuing
   reached instruction and device bring-up. Shared syscon ownership and local
   resettable ALNK are now complete; whole-machine reset remains open.
2. Assign reached E9DE/814A SP-relative byte memory to the CPU worker.
   Establish pinned primary/vendor facts and independent field, offset,
   load/store, truncation and neighboring-memory expectations. Include
   SP wrapping, supported/canonical fields, guards/access faults, conditional
   sizing and retirement. Keep exact E9DE initial scope and preserve existing
   SP-relative word/dword paths, scalar helpers and parallel classifier.
   Review the proposed delta and validator before implementation.
3. Obtain independent review; build and run focused/full ISA/profile/IRQ gates;
   repeat unchanged bounded boot under a new label and renamed generic replay.
   Commit only validated changes. Repeat for each subsequent CPU/MMIO failure.
4. Continue controller lifecycle extraction when reached behavior needs it,
   preserving canonical shared words, register-driven configurations and
   unaffected controllers. Whole-machine reset requires all components.

## Next milestones after the blocker

### Preserve completed audio and timer service

The E86C milestone completes actual IRQ11 acknowledgment/RTI and TIMER5
IRQ63 service. At the first ADC stop, audio entries/returns/acknowledgments are
101/101/101 and timer entries/returns are 5,104/5,104. Samples remain zero;
verify synthesis using physical virtual inputs after reaching home.

Source11 vector is `0x01c7fe2c`, priority3; source63 vector is `0x01c7fefc`,
priority1. Both device levels remain pending until their own acknowledgment;
never clear another source on selection. Preserve nonnested service. Introduce
nesting only if captured firmware behavior requires it, with independently
reviewed admission, stack/context restoration and focused validation. Current
captures do not prove undocumented hardware shadow or nesting behavior.

### Preserve the accepted SAR/analog contract

SAR polling and local reset are now accepted through the generic controller
and separate canonical WLA owner. Preserve the qualified command/pending,
busy restart and idle reconfiguration policies recorded above. IRQ24, other
board inputs and timing configurations remain explicit unsupported behavior.
The 10 us latency, raw 600/512 inputs and handoff/reset values are functional
assumptions. New configurations need primary evidence and independent review;
do not substitute firmware identities for register-driven behavior.

Primary SDK/file identities remain in `.deps/qemu-adc-research-2026-10-08/`;
actual reached input, reviewed source, 76 focused probes and unchanged/generic
replays are in `.deps/qemu-adc-2026-10-08/`. Fatal ADC poststate remains source-
reviewed; repeated schedules do not attest every callback separately. Whole-
machine reset remains open despite accepted local ADC and ALNK reset.

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

### Reviewed physical input ownership before implementation

Read-only design and independent Sol 6.1 Extra high review are retained in
main `.deps/qemu-physical-input-design-2026-10-08/`. Implement after verified
HOME through one private DeviceState child and QEMU's standard KEY handler,
shared by Cocoa and headless input-send-event. Keep all queue state under BQL;
at most one async CPU drain applies physical levels between guest instructions.
Own host closures separately and OR them with existing fixture/display closures.
Release-all clears only the child's contribution.

Pause/reset invalidates pending presses, requests release-all outside the
64-entry FIFO and quarantines held bindings. A later keyup rearms; a press/release
cycle only rearms if the prior release was lost while paused. Check runstate in
the drain. Overflow flushes stale work, releases owned closures and latches a
host error invalidating acceptance. Handler deactivate does not disable ingress.

Unrealize must run on the main/I/O thread before CPU teardown: mark inactive,
invalidate events, unregister input/runstate handlers, then synchronously
run_on_cpu to fence queued work and release closures before clearing storage.
Calling that fence on the target CPU provides no queue barrier. Timer-driven
teardown must first marshal to the main AioContext. Verify callback lifetimes,
duplicate/autorepeat suppression, simultaneous keys, pause/lost releases,
repeated reset, overflow and queued-work teardown; preserve fixture closures.

Optional virtual timers emit standard qemu_input_event_send plus sync through
the same handler. Keep firmware identities, HOME/bootguard observations and
scan-cadence calibration in test support. Preserve existing state/SRAM/ALNK
capture schemas; no guest-variable writes, custom QMP or upstream Cocoa changes.
Then complete the note/release, octave and encoder acceptance described above.

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
