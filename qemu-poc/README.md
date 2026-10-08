# FM-1 QEMU proof of concept

A fresh generic pi32v2 CPU and FM-1 machine on QEMU **11.1.2**.
macOS is the current target; Linux and Windows are deferred.

- [plan.md](../plan.md): current state, next action and working boundaries.
- [BOOTING.md](BOOTING.md): verified firmware progress and boot commands.
- [ARCHITECTURE.md](ARCHITECTURE.md): CPU, hardware and host ownership.
- [OPCODE_COVERAGE.md](OPCODE_COVERAGE.md): saved-corpus instruction gaps.
- [LICENSES.md](LICENSES.md): implementation provenance and licensing.

## Build and run

From `/Users/simonjohansson/src/fm1-qemu-poc` (`codex/qemu-poc`):

```sh
mise exec python@3.13.15 -- python qemu-poc/build.py
mise exec python@3.13.15 -- python qemu-poc/run_felucca.py --label next-boot --max-instructions 200000000
```

The tested build needs macOS's native compiler/make and GLib development files;
network access is needed only for missing pinned downloads. Python is managed
with mise. Edit `overlay/`, not generated `.cache/qemu-*` copies.
Caches are disposable; preserve useful captures under the main repo's `.deps`.
The firmware runner checks unchanged saved artifacts and executes headlessly.
A bounded successful run does not establish complete firmware compatibility.

The small timer/key-matrix demonstration has a continuous Cocoa viewer:

```sh
mise exec python@3.13.15 -- python qemu-poc/run_display.py
```

It runs until the window closes and animates physical key closures. It is a
separate display fixture, not Felucca, and does not map keyboard/mouse controls.
Its `icount shift=8` pacing differs from headless correctness runs at `shift=3`.
Neither setting is a hardware-cycle or throughput calibration.

Historical probe/upgrade/bring-up reports remain in Git and
[archived documentation](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/baseline/qemu-poc/).
