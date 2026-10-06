# FM-1 QEMU handover for booting Felucca

Continue the QEMU proof of concept toward booting the Felucca firmware from `/Users/simonjohansson/src/Felucca`. The immediate goal is the unchanged application's own splash screen, followed by a continuously running home screen with working timer, input and audio interrupt activity. This handover records the state inspected on 2026-10-06. Felucca has not yet been demonstrated booting in this QEMU target.

## Workspace and starting point

| Item | Location or revision |
| --- | --- |
| QEMU worktree root | `/private/tmp/fm1-qemu-poc` |
| Experimental project | `/private/tmp/fm1-qemu-poc/qemu-poc` |
| Branch | `codex/qemu-poc` |
| Implementation baseline | `ea18f7fcedd47e48d84114b7f9e5458c0e5be4d0` |
| Main emulator repository | `/Users/simonjohansson/src/fm1-emulator` |
| Felucca source repository | `/Users/simonjohansson/src/Felucca` |
| Inspected Felucca source HEAD | `1e838e17e170b20ff09b9660c9a7171aadfc5dca` |

Both inspected worktrees were clean before adding this document. The implementation is committed; reuse the existing QEMU worktree. The handover itself is a subsequent documentation commit. Recheck status and applicable instructions before making changes. `/private/tmp` is temporary storage; retain important evidence under the main repository's ignored `.deps/` directory as well.

The user wants discrete, signed commits. Follow Tim Pope's commit-message conventions, include the relevant user prompt in the commit body, and use no emojis. Signing worked in this session. Never use `git -C`; run commands with the correct working directory. Use `git worktree add` if a further feature worktree is needed. Manage language runtimes with mise.

## What already works

The project builds QEMU 10.0.0 with a newly implemented pi32v2 TCG target and a small FM-1 machine model. Guest instructions execute through QEMU; the Rust emulator is a separate reference process.

- **Instruction and foundation diagnostics:** verified instruction probes, application-entry startup, guest data copying and BSS clearing, execution of copied RAM code, GPIO matrix scanning, TIMER4, and a complete TIMER5 interrupt/acknowledgment/return cycle.
- **LCD:** modeled SPI/DMA transfers produce the actual 240 by 240 guest framebuffer. Panel initialization, visibility, backlight and a native macOS Cocoa window work.
- **Small display firmware:** the unchanged 2,700-byte fixture draws a hexadecimal timer counter and 44 key tiles. The original three-frame test matches the separate reference outside the timer digits, including released/pressed/released OCT-minus input.
- **FM-1_980 diagnostic:** boots from application entry, initializes memory and guards, accesses P33/watchdog and NOR flash, draws its status screen, and runs foreground and input interrupts through the scheduled disconnected-USB retry after one guest second. The recorded long run completed 6,173 balanced timer interrupts without watchdog expiry or LCD/P33 timeouts.
- **Continuous viewer:** the display fixture now runs indefinitely with automatic OCT-minus press/release every half-second of virtual time. A native-window test completed 252 frames and six key cycles; 5.838 guest seconds elapsed in 5.849 host seconds without a pause.

These are application-entry boots. ROM/SPL execution, encrypted update-package boot, full Felucca, stock firmware, connected USB, host audio playback and dual-core execution are not established by these results.

The previous Rust performance experiments did not establish a compelling general replacement. QEMU is now being extended for compatibility; there is no measured claim that it is faster for a full Felucca workload.

## Run the existing example

From the QEMU worktree root:

```sh
cd /private/tmp/fm1-qemu-poc
mise exec python@3.13.15 -- python qemu-poc/build.py
mise exec python@3.13.15 -- python qemu-poc/run_display.py
```

The user explicitly wants execution to remain running while viewing the timer. The launcher opens the small display fixture, not Felucca, and does not pause at a checkpoint. Closing its window exits QEMU. Keyboard and mouse input are not mapped; the key animation uses simulated physical matrix closures. A viewer was left running at the end of implementation; inspect current process state before starting another or closing anything.

The viewer uses `-icount shift=8,align=on,sleep=on`: 256 ns per functional guest instruction. Exact headless regressions use `shift=3` and 8 ns. The larger interval lets the small example keep up with wall time by reducing busy-loop polling. It is not a CPU optimization or a hardware-cycle calibration. Do not carry the viewer's clock setting into Felucca correctness or performance claims: audio deadlines and overload decisions make the instruction-to-time ratio significant.

`FM1_POC_DISPLAY_LIVE=1` applies only to the small `display` fixture. Without it, that fixture exits after three frames. Live mode creates no frame files by default; setting `FM1_POC_FRAME_DIR` explicitly enables fixed latest-image/state files. The older `FM1_POC_KEEP_OPEN=1` diagnostic option deliberately pauses at completion and must not be used for a continuous Felucca viewer.

## Code and evidence to read first

All paths below are relative to `/private/tmp/fm1-qemu-poc/qemu-poc`.

| Path | Purpose |
| --- | --- |
| `BOOTING.md` | Current boot milestones, commands, limits and validation evidence |
| `LICENSES.md` | Required implementation provenance boundary |
| `overlay/target/pi32v2/translate.c` | Instruction decoding and TCG translation |
| `overlay/target/pi32v2/cpu.c`, `helper.c` | Reset, interrupt entry/return and instruction helpers |
| `overlay/hw/pi32v2/fm1-poc.c` | Machine wiring, timers, GPIO, interrupt registers and fixture observers |
| `overlay/hw/pi32v2/fm1-system.c` | System registers, P33, watchdog and protection behavior |
| `overlay/hw/pi32v2/fm1-nor.c` | NOR, SPI0 and XIP routing |
| `overlay/hw/pi32v2/fm1-lcd.c` | LCD SPI/DMA and native console |
| `overlay/hw/pi32v2/fm1-usb.c` | Limited disconnected-host behavior |
| `build.py`, `integrate.py` | Reproducible integration into the downloaded QEMU tree |
| `reference/` | Separate Rust behavioral oracle |

Edit `overlay/`, not generated copies under `.cache/qemu-10.0.0/`. The build regenerates those copies. The later part of `README.md` records the original milestone and contains historical missing-feature/uncommitted-work statements; use `BOOTING.md` and current source for present status.

Evidence is in `.cache/boot-validation/`, `.cache/display-validation/`, `.cache/display-frames/`, `.cache/diag-validation/`, `.cache/diag-flash-validation/`, `.cache/diag-boot-validation/`, `.cache/window-validation/`, and `.cache/live-display-validation/`. Earlier evidence was also preserved under:

- `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-boot-2026-10-05/`
- `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-diag-boot-2026-10-05/`
- `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-window-2026-10-06/`

The minimal build has no QMP `screendump` because Pixman is disabled. Automated image checks use the modeled guest LCD capture. The user has separately confirmed that the native window opens and displays output.

Recent signed implementation commits are `57ea938` for the native window, `c516c50` for continuous execution, and `ea18f7f` for native-window pacing. Earlier boot milestones and commits are listed in `BOOTING.md`.

## Felucca inputs and startup path

Existing local artifacts are available without rebuilding:

| File under `/Users/simonjohansson/src/Felucca/build` | SHA-256 |
| --- | --- |
| `felucca.bin` | `12a4b4ea47248467f566ec3b6984b08f2f89d6ef5a8e9e494cab5184e8fadb36` |
| `felucca.elf` | `9404c41dcd5c7516243e99cdbae1a4e731ea0c69e13dea397a8db343244b5deb` |
| `felucca.dis` | `0d992c3113a1765f21a10bfb233bef81ee908073f26a16ea1f9d13c6fe897618` |

The raw binary is approximately 408 KiB. These hashes identify available inputs; they do not establish that the artifacts were produced from the current source HEAD. Preserve them and establish build provenance before rebuilding or attributing source symbols. Read `BUILDING.md` and `build.sh` for the existing compiler/SDK setup; retain mise-managed runtimes and existing dependencies.

`firmware/crt0.S` and `firmware/app.ld` describe the application handoff: raw application offset zero maps to XIP `0x02000120`; `r0` carries the boot-parameter pointer `0x01c7fe08`. The guest establishes application SP `0x01c7a000` and SSP `0x01c7c000`. RAM code, ordinary data, a large zeroed pool, persistent `.noinit` state and the reserved boot/vector area have separate roles.

`firmware/src/main.c` gives the useful milestone order:

1. `_start` enters `fm1_cstart`: time/reset/watchdog setup, boot guard, interrupt vectors, BSS and pool clearing, data and RAM-code copies, mailbox and memory protection.
2. `fm1_main` calls persistence initialization and optional OTA cleanup, initializes settings and LCD, and draws the FELUCCA splash.
3. It initializes input, ADC, panel state, synth state, audio and USB, starts TIMER5, enables interrupts, and waits through startup delays.
4. It clears the splash and enters a foreground loop that feeds the watchdog, retries USB, reads ADC channels, processes input and redraws the UI.

Audio rendering runs through ALNK0 IRQ11; TIMER5 uses IRQ63. The source assigns audio higher priority. The initial plan should use one emulated CPU unless execution or firmware evidence demonstrates a second-core requirement.

## Next implementation steps

### Establish a Felucca boot profile and first failure

Add a distinct private Felucca mode to the machine and a bounded runner with an instruction limit, captured failure PC/opcode, nearby disassembly, registers and device access details. Derive checkpoints from the selected ELF/disassembly and bind them to the binary hash.

The current launcher cannot simply substitute Felucca's binary: fixture selection controls device wiring, reset state, diagnostic observers and hard-coded addresses. Audit and separate those assumptions, including `diag_fixture`, SRAM poison ranges, instruction hooks and summary fields. Reuse verified device models with an explicit application handoff. Do not enable diagnostic checkpoint hooks at addresses that could be unrelated Felucca instructions.

The first deliverable is a reproducible boot attempt and the first genuine missing behavior. This handover does not claim an observed first Felucca fault; the gaps below come from source inspection.

### Reach the unchanged splash screen

Advance through `fm1_cstart`, persistence and LCD setup in bounded stages. Extend CPU instructions and MMIO only when the real boot path reaches them. Add focused instruction/device regressions for each new semantic case, using the separate reference and interface evidence where appropriate.

NOR currently supports identification/status/fast reads but explicitly rejects write enable, program and erase. Felucca persistence and OTA cleanup may exercise more than the diagnostic did. Identify actual first-boot transactions and provide reproducible flash contents. Implement required writes and busy/status behavior if encountered; do not bypass guest persistence by forcing return values or changing guest RAM.

Splash success requires guest initialization and real LCD transfers to produce the screen, with sound stack/guard state and no hidden unsupported-operation fallback.

### Reach a continuously running home screen

Add the device behavior reached after the splash:

- **ALNK0 audio DMA and IRQ11:** model control registers, buffer address/length, half-buffer selection, completion deadlines and acknowledgments. Generalize the current IRQ63-specific interrupt path to source selection, masking, priorities and the nesting behavior actually needed by audio and TIMER5. A deterministic sample sink is sufficient initially, but the guest audio ISR must really execute.
- **ADC:** implement the conversion/status/result behavior used for battery and master-volume inputs, including the relevant WLA/clock registers. Give the virtual controls explicit deterministic values.
- **GPIO and routing:** cover the codec control lines and additional IOMAP registers used during audio initialization. Reuse the existing matrix wiring for real guest scans.
- **CPU coverage:** expect additional arithmetic, DSP, memory and conditional forms in audio/UI code. Inventory executed failures rather than assuming that the diagnostic subset is sufficient. General nested conditional blocks and the unresolved final-THEN-call-with-ELSE case are known current limitations.
- **USB:** start from the existing host-absent scenario and extend only the accesses Felucca actually makes. Keep its retry path progressing alongside other interrupts.

Run first with a documented deterministic functional clock. Measure guest progress, audio deadlines, watchdog behavior and interrupt balance before choosing an interactive clock configuration. Reaching the splash alone is not a completed boot.

### Validate sustained operation and expose the viewer

Require repeated home-screen frames, progressing milliseconds and foreground counters, balanced interrupt service, valid stacks and no unexplained watchdog resets or crash screen. Observe the initial 30-second boot-guard period in guest time, then exercise a reproducible physical key/encoder sequence and verify the resulting UI changes. Derive observation addresses from this Felucca build; `felucca_dbg` contains useful audio, timer and UI counters.

Use the existing Rust emulator as a separate behavioral comparison where it supports the same firmware. Compare meaningful milestones, pixels and device effects; different functional clocks need not produce equal instruction counts or timer samples. Keep the original firmware byte-for-byte intact for the acceptance run. If a reduced-feature build helps isolate a failure, label it separately and return to the full build.

Once the sustained run passes, add a Felucca launcher using the existing native LCD console, leave execution running, and document the exact firmware hash, command, input scenario and remaining unsupported features. Host audio playback and interactive keyboard/mouse controls can follow verified boot and guest device behavior.

## Validation and working boundaries

Useful existing checks, run from `/private/tmp/fm1-qemu-poc`:

```sh
mise exec python@3.13.15 -- python qemu-poc/validate_boot.py
mise exec python@3.13.15 -- python qemu-poc/validate_display.py
mise exec python@3.13.15 -- python qemu-poc/validate_diag_boot.py
mise exec python@3.13.15 -- python qemu-poc/validate_live_display.py --cocoa --cycles 6
```

Additional focused gates are `validate_isa.py`, `validate_peripherals.py`, `validate_diag_startup.py`, `validate_diag_flash.py` and `validate_window.py`. Choose checks for the changed subsystem and retain the established boot regressions before declaring Felucca support. Native-window checks need a graphical session; local QMP sockets may require execution outside a restricted sandbox. Do not modify expected snapshots merely to make a regression pass.

Preserve the provenance boundary in `LICENSES.md`: the fresh QEMU overlay is GPL-2.0-or-later; existing Rust and firmware implementation sources are GPL-3.0-only. Use source/disassembly to establish guest interface and encoding facts, write fresh QEMU implementation, and keep the Rust oracle in a separate process. Do not copy or link its implementation into QEMU.

Keep unsupported instructions and device operations explicit. Do not replace missing peripherals with blanket zero reads, patch firmware around failures, or fabricate guest progress. Physical hardware is unnecessary for the initial bring-up; any later hardware experiment needs a current device/recovery check and session-appropriate authorization. Preserve existing toolchain/dependency pins and established public interfaces.

## Required subagent workflow

Use subagents throughout the Felucca bring-up. The user explicitly requires **Sol 6.1 with extra-high reasoning for every subagent**, including any nested delegation. Set `model="gpt-6.1-sol"` and `reasoning_effort="xhigh"` explicitly when spawning each agent. Use `fork_turns="none"` with a self-contained assignment and this handover's absolute path, or a supported positive history count; full-history forks do not accept model overrides. Do not silently substitute another model or reasoning level. If the requested configuration is unavailable, report the limitation and continue useful coordinator work while resolving it.

The parent agent coordinates the boot milestones, integrates changes, runs shared builds and acceptance checks, and creates discrete signed commits. Use the available worker slots for these responsibilities:

- **CPU agent:** investigate reached instruction failures and implement focused decoder, translation and interrupt-semantics changes with matching regressions.
- **Device agent:** implement the Felucca boot profile and reached peripheral behavior, including NOR, ADC and ALNK0 as the boot progresses.
- **Review and validation agent:** independently review changes, compare behavior with source/disassembly and the separate reference, and check milestone evidence and regression coverage.

Assign explicit file ownership before each round. The CPU and device agents must coordinate changes spanning interrupt delivery and machine wiring; give each shared file one owner at a time. Serialize shared builds, acceptance runs and commits to avoid interference. Use subagents within the current session rather than creating separate user-facing chats.

Start with parallel CPU/startup and peripheral-gap inspection while the parent establishes the bounded boot attempt. After each captured failure, assign the concrete blocker to the relevant worker, integrate and validate its fix, obtain independent review, and repeat toward the splash and sustained home-screen milestones. Keep each commit reviewable, record evidence alongside each milestone, and report precisely how far the unchanged firmware reaches.

**Start with the bounded Felucca boot profile and its first captured failure. Then work toward the splash and sustained home-screen milestones in order.**
