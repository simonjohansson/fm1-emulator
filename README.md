# FM-1 Emulator

Run FM-1 application firmware on your Mac, with an FM-1 panel, mouse and keyboard controls,
audio, and a USB console. Built on QEMU with a pi32v2 CPU and FM-1 board model.

## Run

```sh
./emulator path/to/firmware.bin
```

Close the window or press **Ctrl-C** in the terminal to quit. The executable
needs no Python or third-party library installations to run.
Firmware is not included. Pass a raw application `.bin` or an uncompressed
`.fwsc` / `.ufw` update package; ELF files are unsupported.

Click and hold the piano keys or function buttons. Drag a knob up/down or
scroll over it to turn it. MASTER controls the board’s volume input.

| Key | Action |
| --- | --- |
| Z / C | Play notes |
| X / V | Octave down / up |
| P / O | ENV / LFO |
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
mise run build
```

The first build downloads pinned QEMU sources and Python build tools.
It produces `./emulator` and a distribution archive in `.cache/`.
Python is needed only to build and test.

## Status

Unchanged Felucca passes boot, note/release, page navigation, audio-generation,
and console checks. Emulation still runs below real time; audio may have gaps.
Package decoding is tested; stock FM-1 startup currently stops at an
unimplemented CPU repeat instruction.
Other firmware compatibility is limited. Linux and Windows are deferred.

See [development](docs/development.md) for tests and
[architecture](docs/architecture.md) for the model. Licensing and source
provenance are described in [LICENSES.md](LICENSES.md).
