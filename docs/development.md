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

It boots the firmware unpaced on both cores for 20 guest seconds, then sends a
seeded random sequence of taps (every button and piano key) and encoder detents
in either direction for 60 **guest** seconds: 5 ms holds, gaps and quadrature
phases by default (`--hold-ms`, `--gap-ms`, `--phase-ms`, `--seconds`,
`--boot-seconds`). It passes if the emulator does not fault, the display stays
on and keeps updating, audio DMA keeps completing and a key held afterwards
still sounds. Captures in `.cache/tests/stress/` hold the seed, every action
(`inputs.jsonl`), snapshots and any fault capture; the same seed replays the
same run.

The panel smoke test steps through every preset with a note and pages through
every button, checking each step on screen, in the audio and in the panel
LEDs (a button passes if the screen or a lit LED changes):

```sh
mise exec python@3.13.15 -- python tools/panel_smoke.py /path/to/firmware.fwsc
```

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
With it, `FM1_POC_CAPTURE_NS=<guest ns>` together with `FM1_POC_STATE_DIR`
ends the run with a full capture (state, SRAM, LCD image) at that guest
time, for example after panel input sent over QMP. `FM1_POC_INPUT` presses
and releases host keys at guest times (`NS:QCODE:1,NS:QCODE:0,...`) through
QEMU's input layer, so scripted panel scenarios run unpaced.

With stock update packages, the scenarios in `tests/test_stock_scenarios.py`
boot each package and check HOME, the ENV page, PRESETS, a held note (audio
and MIDI note-on) and OCT+:

```sh
cd tests && FM1_STOCK_PACKAGES=/path/FM-1.fwsc:/path/FM-1_093.fwsc \
  mise exec python@3.13.15 -- python -m unittest test_stock_scenarios
```

`FM1_POC_TRACE_PCS=HEX,HEX,...` prints the core, its instruction count and
r0-r3/RETS whenever either core reaches one of the listed PCs; it costs nothing
when unset. `FM1_POC_SNAPSHOT_NS` and `tools/panel_smoke.py` are described in
the panel smoke test's docstring.

`FM1_POC_UART1_LOG=/path/to/file` appends bytes the firmware sends on UART1 to
that file; stock firmware sends MIDI messages there. Normal launches clear
`FM1_POC_*` variables.
Serialize builds and guest runs that share captures or caches.

## Build inputs

QEMU is pinned to **11.1.2**, release commit
`4fc49f46dc95d4a27de2509e7fceb2931e91faeb`; its archive SHA-256 and Python
build-tool versions are checked in `tools/build.py`. Downloaded sources,
private Python environment, build products, and logs stay in `.cache/`.
Use `mise run build` after changing sources; it integrates changed files before Ninja.
Use `mise run build -- --reconfigure` when changing configuration.

The native build (macOS or Linux) uses SDL for the window and audio, and LTO.
mise provides the compiler (clang) and conda-forge's GLib, SDL2 and pkg-config;
the build reads their pkg-config files from the conda environments on PATH.
Publishing copies the shared libraries the executable loads from those
environments into `lib/` next to it (found through its rpath), checks that it
starts with them alone, ad-hoc signs it on macOS, and packages it with
notices. On macOS the Xcode Command Line Tools supply the SDK. It does not
bundle firmware or Python. CI targets Apple Silicon macOS 26; Windows is
deferred.

For release distribution, retain notices and provide the corresponding QEMU
and overlay sources/build inputs and LGPL relinking materials. See
[LICENSES.md](../LICENSES.md).
