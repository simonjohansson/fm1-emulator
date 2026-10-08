# FM-1 QEMU implementation plan

The user authorized resumed implementation on 2026-10-07 and a shorter,
boot-first workflow on 2026-10-08. The earlier pause is superseded.
Main agent: **Sol 6.1 High**. Every subagent, including nested agents, must
explicitly use `model="gpt-6.1-sol"`, `reasoning_effort="high"` and a supported
partial-history fork or `fork_turns="none"`; report an unavailable setting.
Use subagents throughout bring-up with one file owner and independent source review; keep routine delegation in this chat.
Target **macOS** with the existing Cocoa frontend; Linux/Windows are deferred.

## Objective

Run the pinned, unchanged Felucca application through splash and complete HOME,
real audio/timer service, 30 guest seconds through bootguard, and physical
key/encoder input with verified UI and nonzero audio effects. Leave the native
Cocoa viewer executing without checkpoint pause. Splash or an initial HOME
flag alone is insufficient. Felucca is an acceptance workload; the emulator
must remain general-purpose and support other firmware as evidence permits.

## Current state

Batch D implements 16 qualified memory-operation forms. The unchanged firmware
now runs for **30 guest seconds / 3.75 billion instructions**, advancing 1,734
UI frames with matching audio/timer interrupt returns and no instruction,
watchdog or guard fault. Bootguard remains pending, the firmware millisecond
counter is 29,999, and idle audio samples are zero. This is sustained idle
execution, not complete healthy-HOME/input/audio acceptance.

Focused checks passed 1,071 cases; existing QEMU-only CPU/profile, machine-map
and IRQ checks passed. The 200-million-instruction renamed/default-loader
replay matched captured state and LCD/audio output. No further reference calls
or reference-heavy ISA sweep were required for acceptance.

**Next:** boot past 30,001 firmware milliseconds (start with 31 guest seconds)
and inspect bootguard. Then test physical key/encoder events and nonzero guest
audio, fixing the first actual failure. See BOOTING for the binary/evidence pin.

## Architecture and boundaries

1. **CPU:** pi32v2 registers, instructions, memory and exceptions/interrupts.
   Firmware names, hashes, symbols and PCs never select execution semantics.
2. **Hardware:** reusable JieLi SoC controllers own registers, transfers and
   IRQ outputs; composition owns shared syscon/address/IRQ routing. The FM-1
   board owns evidenced attached flash, LCD, controls and audio wiring.
3. **Host:** Cocoa pixels, physical events, actual samples, MIDI/transport and
   session controls. Host events must not fabricate guest variable changes.

Headless and interactive execution use the same machine. Optional observers,
artifact hashes, poisoned sections and checkpoint PCs belong to validation.
Architectural reset and explicit application-entry loading remain distinct;
ROM/SPL/flash boot is a separate, currently unsupported capability.
Preserve established helper/API, capture schema, persisted formats and other
public contracts. Explain any necessary change and obtain explicit user
confirmation before implementation; ordinary private fixes need no new gate.
No Rust or firmware implementation source reads, copies, changes or rebuilds;
no firmware patches or physical flashing. The existing GPL-3.0-only emulator
is a separate executable reference, never copied or linked into QEMU.
Fresh QEMU work follows [licensing boundaries](qemu-poc/LICENSES.md): QEMU's
file licenses, GPL-2.0-or-later overlay/scripts, Apache-2.0 encoding evidence.

## Working loop: boot → diagnose → fix → retest

1. Boot the unchanged image on the current accepted binary with a bounded run.
   Retain the stop, exact bytes, state, memory/access facts and relevant output.
2. Diagnose the reached blocker using QEMU C, pinned ISA evidence and matching
   saved disassembly. Separate architectural facts from model policy and
   source disagreements. Do not infer correctness from admission counts.
3. Fix the smallest generic CPU/device issue. Group nearby forms only when
   evidence and simple shared semantics make that work worthwhile. Preserve
   unaffected decoding, predicate/IRQ behavior and explicit unsupported faults.
4. Get one independent source review, then build once after owners freeze
   their edits. Run focused meaningful checks plus existing required gates
   affected by the change, and boot again. Repair failures; do not repeat
   passed checks without a changed risk or new failure.

Firmware boot and existing QEMU checks are the default. Use the old emulator
only for a specific ambiguity where it adds useful evidence; its output is
not architectural truth or a required whole-batch gate. Avoid exhaustive
Cartesian matrices, repeated reviews and duplicated evidence reports.
Add a standalone validator only for a meaningful repeatable risk that the
existing checks cannot cover. Keep the current summary and next action here;
keep historical details in Git and the already saved evidence.

## Remaining work and limits

- Continue reached CPU/device fixes until complete HOME has advancing frames,
  finished LCD transfers and verified pixels in the foreground loop.
- Sustain healthy HOME for 30 guest seconds, clear bootguard pending without
  failure, and verify timer/audio service and absence of guard/watchdog faults.
- Exercise genuine board key closures and encoder phases after bootguard;
  verify note/release, octave and BPM effects and actual nonzero ALNK samples.
- Promote the existing native launch/input design after those gates; retain
  Cocoa and leave execution running. CoreAudio/CoreMIDI and missing transports
  require their own evidenced implementation and checks.
- Attempt available unchanged stock/other images through documented entry
  modes. One successful image does not establish all-firmware compatibility.

Functional virtual clocks are not hardware timing measurements. ALNK/LCD
capture buffers at completion; active-buffer streaming remains unproven.
NOR write/erase persistence, ROM boot, connected USB MIDI/CDC and UART are
missing. Whole-machine reset, nested IRQ details and full-workload performance
are not established; one-instruction translation blocks preserve correctness.

## Commands and handover

Run from `/Users/simonjohansson/src/fm1-qemu-poc` on `codex/qemu-poc`:

```sh
git status --short
mise exec python@3.13.15 -- python qemu-poc/build.py
mise exec python@3.13.15 -- python qemu-poc/run_felucca.py --label after-next-fix --max-instructions 200000000
mise exec python@3.13.15 -- python qemu-poc/validate_cpu_profiles.py
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_irq.py
mise exec python@3.13.15 -- python qemu-poc/validate_felucca_devices.py
git verify-commit HEAD
```

Serialize shared builds, acceptance runs and commits. Retain unique run labels,
command/input/executable identities and raw captures in cache and durable
`.deps`. Sign milestone commits, verify signatures, follow Tim Pope's message
style, include the relevant user prompt in the body and omit emojis. Reuse
this worktree; use `git worktree add` if another is needed, never `git -C`.
Manage runtimes with mise; preserve the main repository's separate uv setup.

See [BOOTING](qemu-poc/BOOTING.md) for acceptance details and commands. Earlier
history remains in Git and the [saved full plan](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/baseline/plan.md).
Existing [Batch D evidence](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/)
and [Batch C evidence](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-c-2026-10-08/)
remain the detailed records; do not reproduce their ledgers here.
