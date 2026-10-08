# FM-1 Emulator

Run FM-1 application firmware on your Mac, with a display, keyboard controls,
audio, and a USB console. Built on QEMU with a pi32v2 CPU and FM-1 board model.

## Run

```sh
./emulator path/to/firmware.bin
```

Close the window or press **Ctrl-C** in the terminal to quit. The executable
needs no Python or third-party library installations to run.
Firmware is not included: use a raw application `.bin`, not an ELF or `.fwsc`
update package.

| Key | Action |
| --- | --- |
| Z / C | Play notes |
| X / V | Octave down / up |
| P / O | Navigate pages |
| H | Return HOME |

The firmware console appears in your terminal and accepts typed commands.
For Felucca, try `help` or `status`. Use `--no-audio` for silence or
`--headless` for a console-only session. See `./emulator --help` for options.

## Build

On Apple Silicon macOS, install Xcode Command Line Tools, [Homebrew](https://brew.sh),
and the build dependencies:

```sh
brew install mise glib pkgconf
mise install
make
```

The first build downloads pinned QEMU sources and Python build tools.
It produces `./emulator` and a distribution archive in `.cache/`.
Python is needed only to build and test.

## Status

Unchanged Felucca passes boot, note/release, page navigation, audio-generation,
and console checks. Emulation still runs below real time; audio may have gaps.
Other firmware compatibility is limited. Linux and Windows are deferred.

See [development](docs/development.md) for tests and
[architecture](docs/architecture.md) for the model. Licensing and source
provenance are described in [LICENSES.md](LICENSES.md).
