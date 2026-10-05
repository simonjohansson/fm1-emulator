![FM-1 emulator running the Felucca firmware](docs/felucca.jpg)

# FM-1 emulator

A Rust emulator for the M-VAVE FM-1. Load firmware and interact with its own
screen and buttons. The firmware's USB serial console uses your terminal; type
commands such as Felucca's `help` and press Enter.

## Download and run

Download and extract the [release](https://github.com/simonjohansson/fm1-emulator/releases)
for your OS and architecture, then run it with a firmware path:

```sh
./emulator /path/to/firmware.fwsc
```

Use `emulator.exe` on Windows. Firmware is supplied separately; `.fwsc`, `.elf`
and `.bin` are supported.

## Build from source

Install [mise](https://mise.jdx.dev/installing-mise.html) and a native C/C++
toolchain: Xcode Command Line Tools on macOS, Visual Studio Build Tools with
**Desktop development with C++** on Windows, or the following on Ubuntu:

```sh
sudo apt-get install build-essential pkg-config libx11-dev libxi-dev libgl1-mesa-dev libxkbcommon-dev libwayland-dev
```

From this checkout:

```sh
mise trust
mise install rust
mise exec -- cargo build --manifest-path rust-emulator/Cargo.toml --locked --release --features gui --bin fm1-ui
mise exec -- cargo test --manifest-path rust-emulator/Cargo.toml --locked --release --features gui
```

On Linux/macOS, run `./emulator /path/to/firmware.fwsc`; the launcher builds and
opens the emulator. On Windows, run
`.\rust-emulator\target\release\fm1-ui.exe C:\path\to\firmware.fwsc`.
Building the emulator does not require the vendor firmware compiler or Docker.

## Firmware compatibility

Updated on 2026-10-05. **Partial** means boot and some controls work, but
other firmware paths can stop emulation.

| Firmware | Status | Verified behavior / blocker |
| --- | --- | --- |
| Felucca 0.9-beta (`FM-1_909`, `.fwsc`) | Partial | LCD, USB console (`help`), watchdog, note audio/DMA and FX pass; full UI coverage remains incomplete |
| Felucca 1.0.1 (`felucca-1.0.1.fwsc`, published) | Partial | LCD boot to HOME, USB console banner, watchdog, note audio/DMA and release pass; stopped at boot before the EP4 and halfword/byte step fixes |
| Felucca source build (`1e838e1`, `.elf`) | Partial | Boot, note press/release, FX, HOME and ENV pass; other UI paths need broader coverage |
| Official `FM-1_015` (`FM-1.fwsc`) | Partial | LCD boot, PIANO 1, FX/HOME and note audio/DMA pass |
| Baud Girl `FM-1_093` (`FM-1_093.fwsc`) | Partial | LCD boot and FX/HOME pass; its factory preset payload fails integrity validation |

FX now opens and renders in both Felucca builds; see the
[Felucca investigation](rust-emulator/FELUCCA.md).
See the [stock firmware trials](rust-emulator/STOCK-FIRMWARE.md) for the official
and Baud Girl results.

## Still to implement

- [ ] Execute native blocks in batches, then broaden JIT coverage; see the [performance plan](rust-emulator/PERFORMANCE.md).
- [ ] Remaining CPU instructions, peripherals and firmware UI paths.
- [ ] Host audio playback, rotary controls and USB MIDI.
- [ ] Flash persistence, plus fuller encryption, interrupt and timing behavior.

GPL-3.0-only; see [LICENSE](LICENSE). Based on research and components from
[Felucca](https://github.com/hugelton/Felucca), the
[JieLi AC79 SDK](https://gitee.com/Jieli-Tech/fw-AC79_AIoT_SDK), and
[Quarkslab's pi32v2 reference](https://github.com/quarkslab/ghidra-jieli).
