# Booting FM-1 in QEMU

Worktree: `/Users/simonjohansson/src/fm1-qemu-poc`, branch `codex/qemu-poc`.
Maintained source is `qemu-poc/overlay/`; downloaded QEMU and build products
in `.cache/` are disposable. The authorized pin is QEMU **11.1.2**, release
commit `4fc49f46dc95d4a27de2509e7fceb2931e91faeb`; `build.py` checks its inputs.
Follow [plan.md](../plan.md) for authorization, review and licensing boundaries.

## Current verified state

Batch D adds 16 qualified memory-operation forms. The accepted QEMU binary is
SHA-256 `88e01320c617087e3f38760ca5f3fdc20342fdc4291bd057e2f7f26f6231607d`;
translator SHA-256 `d4567f2c6059a6e251d0d6e21892d531335b26b8f56890bd9361ab94da33e58a`.
Use `git log -1` and `git verify-commit HEAD` for the signed milestone.

The unchanged application ran **3,750,000,000 instructions / 30,000,000,008 ns**
from entry, stopping at the instruction budget (`0x0200049e`). It advanced
1,734 UI frames; IRQ11 returned 5,109/5,109 times and IRQ63 258,174/258,174.
There were no instruction, watchdog or guard faults. ALNK captured 2,618,368
sample words, all zero in this idle run. LCD was active at the arbitrary stop.
Bootguard failed=0/pending=1; firmware time was **29,999 ms**. Complete healthy
HOME, bootguard clearance, physical input and nonzero synthesis remain unproven.

Focused QEMU checks passed **1,071 cases (796 success / 275 modeled faults)**;
existing CPU/profile (98), machine-map (32) and IRQ (10) checks passed.
The short 200-million-instruction generic renamed/default-loader replay matched
captured state, LCD and audio; whole SRAM was not compared across loader modes.
Initial instruction research used the separate reference; no subsequent fresh
reference calls or full reference-heavy ISA regression sweep were run.
Raw results are in the [Batch D evidence](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/),
including `long-30s/run.json` and `observation-30s.json`.

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

For the next bootguard check, start with **31 guest seconds**: use the command
recorded in `long-30s/run.json`, a fresh state directory, instruction budget
`3875000000`, and a host timeout of at least 600 seconds. The 30-second run took
187 host seconds, exceeding the runner's fixed 180-second timeout; run the
recorded QEMU command directly with a larger external bound. This requires no
runner/API change. Inspect firmware time and bootguard rather than inferring
clearance from the instruction budget.

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

## Completion still required

- **HOME:** verify the complete visible frame, foreground loop, advancing UI
  frames and idle completed LCD transfers; an early HOME flag is insufficient.
- **30 seconds:** begin from healthy HOME, reach at least 30,001 guest ms,
  verify bootguard magic `0x42475244`, failed=0/pending=0, no watchdog/guard
  faults and continued audio/timer service, including IRQ coalescing facts.
  At functional 8 ns/instruction, 30 seconds takes about 3.75 billion
  instructions; the default 100 million covers only 0.8 seconds. Increase
  instruction and host-time bounds deliberately; a short run proves less.
- **Physical inputs:** after bootguard, inject genuine matrix closures and
  encoder phases; verify note/release, octave and BPM effects plus nonzero
  actual ALNK samples. Host callbacks must not modify guest variables.
- **Native viewer:** use the accepted same binary, existing Cocoa frontend
  and normal running policy; leave it executing without checkpoint hold.

Existing private [sustained-HOME design](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-sustained-home-2026-10-08/),
[physical-input design](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-physical-input-design-2026-10-08/)
and [native-launch design](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-felucca-native-launch-design-2026-10-08/)
retain the detailed scenarios. Their design/host checks do not prove guest acceptance.

Functional clock rates, completion-time buffer capture and current application
handoff are limited models. Hardware timing, active-buffer streaming, whole
reset, ROM boot, NOR persistence, UART/connected USB, host audio/MIDI and
full-workload performance remain incomplete. Successful Felucca does not prove
all-firmware compatibility or physical hardware validation.

History is retained in Git, [archived BOOTING](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/baseline/qemu-poc/BOOTING.md),
and existing [Batch D](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/)
and [Batch C](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-c-2026-10-08/) evidence.
