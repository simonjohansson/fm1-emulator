# FM-1 QEMU proof of concept

A fresh generic pi32v2 CPU and FM-1 machine on QEMU **11.1.2**.
macOS is the current target; Linux and Windows are deferred.

- [plan.md](../plan.md): current state, next action and working boundaries.
- [BOOTING.md](BOOTING.md): verified firmware progress and boot commands.
- [ARCHITECTURE.md](ARCHITECTURE.md): CPU, hardware and host ownership.
- [OPCODE_COVERAGE.md](OPCODE_COVERAGE.md): saved-corpus instruction gaps.
- [LICENSES.md](LICENSES.md): implementation provenance and licensing.

## Native macOS executable

Build the standalone program from the repository root:

```sh
make emulator
./emulator path/to/firmware.bin
```

The generated `emulator` is one native executable with GLib, gettext and PCRE2
linked in. Running it requires only macOS's built-in libraries and frameworks;
no Python, mise, Homebrew or external QEMU installation is used at runtime.
It opens the Cocoa display with CoreAudio output and runs until the window closes.
Use `./emulator --help` for controls, `--no-audio` for silent display or
`--headless` for execution without a window. `--qemu` exposes the original QEMU
arguments for developer diagnostics.

The firmware argument is a raw application-entry image accepted by the current
FM-1 loader. ROM boot and packed/encrypted firmware packages remain unsupported.
Firmware is supplied separately and is never identified by name or hash to select
CPU or hardware behavior.

Compilation still uses QEMU's Python/Meson/Ninja toolchain, managed by mise, and
macOS compiler/GLib development files. `make emulator` uses an isolated standalone
build and checks that the output loads only macOS system libraries. Static
archives for GLib, gettext and PCRE2 must be available to the compiler. The normal
developer build and bounded validation scripts remain available below.

The current verified artifact is Apple Silicon (arm64), with minimum macOS 27
selected by the local compiler. Other deployment targets/platforms are unvalidated.
The build also produces `qemu-poc/.cache/fm1-emulator-macos-arm64.tar.gz` containing
the executable and notices, with no Python scripts or external library files.

The release directory is `qemu-poc/.cache/release/`; only `emulator` is required
for execution. Keep the accompanying license notices and corresponding source/
build inputs when redistributing. The generated root executable is ignored by Git.

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
The firmware runner checks unchanged saved artifacts and supports bounded
headless runs or a continuous `--display cocoa` window; see BOOTING for controls.
A bounded successful run does not establish complete firmware compatibility.

The small timer/key-matrix demonstration has a continuous Cocoa viewer:

```sh
mise exec python@3.13.15 -- python qemu-poc/run_display.py
```

It runs until the window closes and animates physical key closures. It is a
separate display fixture; Felucca has its own native launcher and behavior gate.
Its `icount shift=8` pacing differs from headless correctness runs at `shift=3`.
Neither setting is a hardware-cycle or throughput calibration.

Historical probe/upgrade/bring-up reports remain in Git and
[archived documentation](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/baseline/qemu-poc/).
