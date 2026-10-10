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

Firmware confirmed to work. CI boots each one on every push, drives the
panel with random buttons and encoders for 60 guest seconds (`mise run stress`),
then steps through 128 presets and every function button (`mise run panel_smoke`).

| Firmware | Version | Source | Headless, idle | Headless, input every 10 ms | Windowed with audio |
| --- | --- | --- | --- | --- | --- |
| Stock FM-1 | `FM-1_015` (string in the image) | [FM-1.fwsc](https://yms-file-store.oss-cn-hongkong.aliyuncs.com/software/firmware/FM-1.fwsc) | 2.7x | 1.1x | real time, 53% of a core |
| Felucca | 1.5 | [felucca-1.5.fwsc](https://github.com/hugelton/Felucca/releases/download/v1.5/felucca-1.5.fwsc) | 1.5x | 1.0x | real time, 68% of a core |

Speeds are guest time divided by host time, measured on an Apple M3 Pro
(macOS 27, both firmware cores emulated). Headless is unpaced, so it shows the
most the emulator can do: "idle" is 50 guest seconds sitting at HOME, and
"input every 10 ms" is the stress run, about 83 guest seconds of constant button
presses and knob turns. Windowed sessions are paced to real time, so they
cannot run faster than 1x; the last column is the host CPU the session used
over 40 seconds, and no "guest is late" warnings appeared. A slower Mac has
less headroom, and below 1x a windowed session falls behind.

The panel draws the button and key LEDs as the firmware drives them,
including the dim glow, REC's red and PLAY's green. The stock FM-1 Wi-Fi radio is modeled as inert hardware. Other firmware
compatibility is limited. Linux and Windows are deferred.

## Browser (experimental)

A headless build runs in the browser: choose a firmware file, press Boot, and the
page shows the display and the console, with its speed against real time. It is
an interpreter: on an Apple M3 Pro it runs at roughly 0.05x real time, so Felucca
reaches its home screen after about 30 seconds. There are no buttons, LEDs or
sound yet. It runs in current Chrome, Firefox and Safari; the firmware never
leaves your computer.

```sh
mise run build_wasm    # emsdk and clang come from mise
mise run serve_web     # http://localhost:8080
```

See [development](docs/development.md) for tests and
[architecture](docs/architecture.md) for the model. Licensing and source
provenance are described in [LICENSES.md](LICENSES.md).
