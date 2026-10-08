# Development

Build and run from the repository root:

```sh
mise install
mise run build
./emulator path/to/firmware.bin
mise run test
```

`mise run test` runs compact synthetic CPU, panel input, package decoding, USB,
and native CLI checks without external firmware. Build first. Firmware behavior checks are optional and use
saved, unchanged `felucca.bin`, `felucca.elf`, and `felucca.dis` artifacts:

```sh
mise exec python@3.13.15 -- python tests/behavior.py \
  --firmware-dir /path/to/artifacts --label behavior-check
mise exec python@3.13.15 -- python tests/console.py \
  --firmware-dir /path/to/artifacts --label console-check --long
```

These check bootguard, UI progress, note/release audio, parameters/pages, IRQ
returns, and USB console responses. They verify artifact identities and refuse
existing capture labels. Captures live in `.cache/tests/`. Do not rebuild or
patch firmware to make an emulator check pass.

Random button testing accepts any application image:

```sh
mise run stress /path/to/firmware.fwsc --seed 123
```

It waits for Felucca's bootguard to clear, then shuffles all 41 panel buttons
and piano keys for 30 **host** seconds or until the first failure. Holds default
to 80 ms and released gaps to at least 20 ms, enforced in both host and guest
time. The final hold finishes before releasing the button. For other firmware,
set `--ready-memory ADDRESS:WORD[,WORD...]` or `--ready-console REGEX` to its
actual boot-ready signal; all supplied conditions must match before clicking.
The console greeting alone may precede complete startup. Use `--seconds` to
change duration. Captures in `.cache/tests/stress/` include the seed, every
attempted input, serial output, failure PC/reason and existing emulator fault
captures. This checks faults and CPU progress; it does not prove UI correctness.

To also check a local update package against an independently saved raw image:

```sh
FM1_PACKAGE=/path/to/firmware.fwsc \
FM1_RAW_REFERENCE=/path/to/app.bin mise run test
```

## Source layout

| Path | Purpose |
| --- | --- |
| `src/target/pi32v2/` | CPU and TCG translation |
| `src/hw/pi32v2/` | SoC devices, FM-1 board, host input/audio/USB adapters |
| `src/system/`, `src/ui/` | Native launcher and small upstream integration hooks |
| `tools/` | Pinned QEMU download, integration, build, packaging |
| `tests/` | Synthetic regressions and optional firmware behavior checks |

Edit `src/`, never generated sources under `.cache/`. Its directory structure
matches QEMU so `tools/integrate.py` can apply it to the pinned release. Device
and test-hook names retaining `poc` are internal compatibility names.
`./emulator --qemu ...` retains the underlying QEMU command line for development.
Serialize builds and guest runs that share captures or caches.

## Build inputs

QEMU is pinned to **11.1.2**, release commit
`4fc49f46dc95d4a27de2509e7fceb2931e91faeb`; its archive SHA-256 and Python
build-tool versions are checked in `tools/build.py`. Downloaded sources,
private Python environment, build products, and logs stay in `.cache/`.
Use `mise run build` after changing sources; it integrates changed files before Ninja.
Use `mise exec python@3.13.15 -- python tools/build.py --standalone --reconfigure`
when changing configuration.

The macOS build uses Cocoa, CoreAudio, LTO, and static third-party archives.
It rejects non-system dynamic dependencies, ad-hoc signs the executable,
and packages it with notices. The build needs Xcode Command Line Tools,
Homebrew GLib/pkgconf, and mise Python. It does not bundle firmware or Python.
The deployment target follows the SDK and dependency archives: the locally
validated Apple Silicon artifact requires macOS 27. Older macOS compatibility
has not been established. CI targets Apple Silicon macOS 26; Linux/Windows
build and packaging work is deferred.

For release distribution, retain notices and provide the corresponding QEMU
and overlay sources/build inputs and LGPL relinking materials. See
[LICENSES.md](../LICENSES.md).
