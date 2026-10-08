# Booting FM-1 in QEMU

Worktree: `/Users/simonjohansson/src/fm1-qemu-poc`, branch `codex/qemu-poc`.
Maintained source is `qemu-poc/overlay/`; downloaded QEMU and build products
in `.cache/` are disposable. The authorized pin is QEMU **11.1.2**, release
commit `4fc49f46dc95d4a27de2509e7fceb2931e91faeb`; `build.py` checks its inputs.
Follow [plan.md](../plan.md) for authorization, review and licensing boundaries.

## Current verified state

The unchanged application passed **4,625,000,000 instructions / 37 guest
seconds**, reaching **36,999 firmware milliseconds** with bootguard magic
`0x42475244`, failed=0/pending=0. UI frames advanced to 2,140; IRQ11 returned
6,315/6,315 and IRQ63 318,744/318,744. No instruction, watchdog, guard, guest
crash or LCD/P33 timeout was observed. A separate stage9 HOME capture at 1,017
firmware milliseconds has an idle visible LCD and complete pixels. The final
37-second budget stops during a transfer; it is not an idle-frame endpoint.

Three note presses generate nonzero PCM; all releases clear the note bitmap
and reduce sample energy below 1.4% of held levels after 500 guest ms. ALNK
captured 3,235,840 sample words, including 237,780 nonzero words. Octave minus/
plus, BPM 120→121, ENV decay 55→56→57 and repeated page/HOME navigation pass
through standard KEY events and the guest's GPIO scans. Input duplicate,
simultaneous, pause/rearming, overflow recovery and shutdown checks passed.
Earlier three-frame display captures matched the saved QEMU output.

The accepted binary SHA-256 is
`131a688d6c8189852a1d5728678740b751e482ee84cfc10029d581c8cc38b896`.
The CPU forms remain unchanged. Ordinary fallthroughs and static branches
chain between one-instruction blocks when no predicate is active. Active
predicates, calls, mask changes and RTI retain dispatcher exits. Typed CPU
callbacks avoid redundant QOM checks. Contained hardware reads reuse QEMU's
existing subpage section table, retaining register validation and dispatch;
complex accesses and writes use the original path. Register ranges are unchanged.

CPU/profile and IRQ gates pass, including taken/fallthrough branches completing
both selected IF arms with a pending timer IRQ. Tiny-region read width, alignment,
unsupported-register and hole faults pass. At 200 million instructions, full
CPU/device state, SRAM, ALNK and LCD bytes match the previous binary exactly;
the unchanged 1,071 opcode cases were not repeated.

The latest matched 37-second behavior run improves from **86.9 to 69.3 host
seconds (1.25×)** by enabling macOS link-time optimization. Both builds pass
boot/input/audio checks. The normal rebuilt executable retains exact CPU/device
state and full SRAM/audio/LCD bytes at 200 million instructions; 98 CPU/profile
and 16 IRQ checks pass. CPU/device source and 8 ns/instruction clocks are unchanged.
`build.py` automatically reconfigures older macOS caches to enable LTO; the first
build recompiles the target and needs an LTO-capable compiler/linker.

The native Cocoa run typically advances about **30 frames/s** and **0.50× real
time** beyond bootguard, with repeated early/late note/page/HOME effects within
90 host ms. These measure guest-state responses rather than pixel presentation
latency. Real-time execution remains unestablished.

CPU Profiler attributes about 30% of accepted-build sampled cycles to address,
NOR and stack checks. A separate plugin build finds LCD status/timer-counter
reads account for about 92% of hardware accesses in the short interaction run.
The small PC-window early-return experiment showed no clear speedup and was
removed; wider blocks remain deferred. Diagnostic plugins remain separate from
the normal build. Current traces, counts and matched captures are in
`/Users/simonjohansson/src/fm1-emulator/.deps/qemu-profile-2026-10-08/`, including
`accepted-cpu/`, `guest-pattern/`, `guest-source/`, `accepted-37s/`, `lto-37s/`,
`default-lto-exact/`, `default-lto-37s/` and `lto-native/`. Earlier performance captures remain in
`.deps/qemu-performance-2026-10-08/`.
Earlier bring-up
evidence remains in `.deps/qemu-behavior-2026-10-08/`, especially
`accepted-37s/`, `lifecycle/` and
`display-compat/`. The earlier 31-second bootguard run is in
`.deps/qemu-bootguard-2026-10-08/`. Use `git log -1` and `git verify-commit HEAD`
for the signed milestone. Raw DMA snapshots are observations, not continuous
host audio.

## Reproduce a bounded boot

Run from the worktree above. Use a new lowercase label for each run:

```sh
mise exec python@3.13.15 -- python qemu-poc/build.py
mise exec python@3.13.15 -- python qemu-poc/run_felucca.py --label after-next-fix --max-instructions 200000000
```

The runner checks unchanged `felucca.bin`, `.elf` and `.dis` identities in
`/Users/simonjohansson/src/Felucca/build`. It uses one CPU, headless TCG and
`-icount shift=3,align=off,sleep=off`, with a 180-second host timeout. It saves
`run.json`, stdout/stderr, state/SRAM, samples, LCD pixels and nearby disassembly
under `qemu-poc/.cache/felucca-validation/<label>/`; inspect the recorded stop.
Preserve useful captures in durable `.deps`; a timeout retains partial output
and cannot establish completion. Optional `--stop-pc` is an observation tool.

The runner's `felucca` append mode enables explicit validation observations.
For generic application handoff, omit `-append` or use `-append application`;
names/hashes/PCs must not alter CPU semantics or the implemented hardware map.
Compare fixture/default-loader runs through existing profile/map checks;
different loader initialization means whole SRAM need not match across modes.

The compact full behavior regression has a 600-second host bound and checks
actual scan cadence, bootguard, repeated contacts and guest audio:

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_behavior.py --label next-behavior
```

It refuses existing capture labels. The current 37 guest seconds took about
69 host seconds. Keep the quick bounded runner for first-failure feedback.

## Continuous Cocoa display

```sh
mise exec python@3.13.15 -- python qemu-poc/run_felucca.py --display cocoa
```

This uses the same CPU/board model in ordinary application mode, with no
instruction budget, checkpoint observer or hold. Close the window to quit.
Felucca requires `shift=3`; the display demo's `shift=8` advances time too fast
for its startup and expires the watchdog. Cocoa uses align=off/sleep=on.
A running VM holds a macOS user-initiated
activity token to prevent background App Nap; pause and cleanup release it.
Normal idle system sleep remains allowed. Guest/headless clocks are unchanged.

Z/C play two notes, X/V lower/raise octave, P opens ENV, O opens the second page
family, and H returns HOME on a short release. A/S expose BPM encoder phases;
D/F expose encoder3 (ENV DEC). A positive detent is S down, A down, S up, A up
(or F down, D down, F up, D up), with stable phases and rest between detents.
These are physical contacts; the firmware owns their meaning. Keyboard bindings
are shared by Cocoa and standard QMP input. Host speaker output is not connected.

## Next-fix checks

Boot → diagnose the reached failure → fix its generic cause → review once →
build and retest. Use the smallest meaningful focused checks and affected
existing gates, then repeat the unchanged boot. These are common CPU/device
gates; other established startup/splash/display/profile checks remain relevant
when their behavior changes:

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_cpu_profiles.py
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_irq.py
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_devices.py
```

No routine old-emulator sweep is required. Consult that separate executable
only to resolve a specific ambiguity; preserve source/reference disagreements.
Workers freeze assigned files before the parent serializes builds and runs.
Do not read/change/rebuild Rust or firmware implementations, flash hardware,
or change public APIs/capture contracts without explicit user authorization.

## Remaining limits

Cocoa runs continuously; input transport and guest samples share the headless
hardware model. The sustained run, complete HOME pixels and compact behavior
suite establish this pinned workload, not all-firmware or hardware compatibility.
System-reset input registration is implemented, but whole-machine reset and
its controller behavior remain unverified. ROM/SPL/flash boot is separate from
application-entry loading.

Functional clock rates, completion-time buffer capture and current application
handoff are limited models. Hardware timing, active-buffer streaming, whole
reset, ROM boot, NOR persistence, UART/connected USB, host audio/MIDI and
real-time performance remain incomplete. Successful Felucca does not prove
all-firmware compatibility or physical hardware validation.

History is retained in Git, [archived BOOTING](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/baseline/qemu-poc/BOOTING.md),
and existing [Batch D](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/)
and [Batch C](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-c-2026-10-08/) evidence.
