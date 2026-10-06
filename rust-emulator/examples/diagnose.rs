// SPDX-License-Identifier: GPL-3.0-only
// Bounded boot diagnostics using the same CPU/bus as the graphical emulator.
use fm1_emu::{cpu::Cpu, firmware::Firmware};
use std::{
    collections::{BTreeMap, VecDeque},
    env,
    io::{self, Write},
    path::Path,
    process::ExitCode,
};

fn location(symbols: &BTreeMap<String, u32>, pc: u32) -> String {
    let closest = symbols
        .iter()
        .filter(|(_, address)| **address <= pc)
        .max_by_key(|(_, address)| **address);
    match closest {
        Some((name, address)) => format!("0x{pc:08x} ({name}+0x{:x})", pc - address),
        None => format!("0x{pc:08x}"),
    }
}

fn run() -> Result<(), String> {
    let mut args: Vec<_> = env::args().skip(1).collect();
    let mut flash = None;
    if let Some(i) = args.iter().position(|arg| arg == "--flash") {
        if i + 1 >= args.len() {
            return Err("missing value for --flash".into());
        }
        flash = Some(args.remove(i + 1));
        args.remove(i);
    }
    if !(1..=2).contains(&args.len()) {
        return Err(
            "usage: diagnose [--flash IMAGE] FIRMWARE.{fwsc,elf,bin} [INSTRUCTION_LIMIT]".into(),
        );
    }
    let limit: u64 = args
        .get(1)
        .map(|s| s.parse())
        .transpose()
        .map_err(|_| "invalid instruction limit")?
        .unwrap_or(10_000_000);
    let firmware = Firmware::load(Path::new(&args[0]))?;
    let bus = match flash {
        Some(path) => {
            let image = std::fs::read(&path).map_err(|error| format!("{path}: {error}"))?;
            firmware.bus_with_flash(&image)?
        }
        None => firmware.bus()?,
    };
    let mut cpu = Cpu::new(bus, firmware.entry);
    cpu.r[0] = 0x01c7_fe08;
    let mut recent = VecDeque::new();
    let mut serial_bytes = 0u64;
    let mut stdout = io::stdout().lock();
    let mut fault = None;
    for _ in 0..limit {
        let pc = cpu.pc;
        match cpu.step() {
            Ok(op) => {
                if recent.len() == 12 {
                    recent.pop_front();
                }
                recent.push_back((pc, op));
            }
            Err(error) => {
                fault = Some(error);
                break;
            }
        }
        // Terminal stdout contains only real guest CDC endpoint data.
        while let Some(byte) = cpu.bus.usb.serial.pop_front() {
            stdout.write_all(&[byte]).map_err(|e| e.to_string())?;
            serial_bytes += 1;
        }
    }
    stdout.flush().map_err(|e| e.to_string())?;
    eprintln!("application: {}", args[0]);
    eprintln!("executed: {} instructions", cpu.steps);
    eprintln!("stopped: {}", location(&firmware.symbols, cpu.pc));
    eprintln!(
        "LCD: {} pixels written; visible={}",
        cpu.bus.lcd.pixels_written,
        cpu.bus.screen_visible()
    );
    eprintln!(
        "interrupts: {}; USB: {} host setups, {} packets, {} CDC bytes",
        cpu.irq_entries, cpu.bus.usb.setups, cpu.bus.usb.packets, serial_bytes
    );
    eprintln!("watchdog: {} feeds", cpu.bus.system.watchdog_feeds);
    eprintln!(
        "audio: {} stereo frames, {} DMA halves; ADC: {} conversions",
        cpu.bus.audio.frames, cpu.bus.audio.halves, cpu.bus.devices.adc.conversions
    );
    if let Some(&address) = firmware.symbols.get("felucca_dbg") {
        // Existing guest diagnostics from Felucca's audio.c; no guest hooks.
        if cpu.bus.read(address, 4).ok() == Some(0x44424731) {
            let read = |offset| cpu.bus.read(address + offset, 4).unwrap_or(0);
            eprintln!(
                "Felucca: {} UI frames, {} rendered audio halves, {} timer IRQs; stage={}, page={}, home={}",
                read(28), read(4), read(24), read(44), read(48), read(52)
            );
        }
    }
    eprintln!("recent completed instructions:");
    for (pc, op) in recent {
        eprintln!("  {}: {op}", location(&firmware.symbols, pc));
    }
    eprintln!("registers:");
    for (i, register) in cpu.r.iter().enumerate() {
        eprintln!("  r{i}=0x{register:08x}");
    }
    eprintln!(
        "  sp=0x{:08x}; rets=0x{:08x}; interrupts_enabled={}",
        cpu.sr[14], cpu.sr[3], cpu.interrupts_enabled
    );
    match fault {
        Some(error) => Err(error.to_string()),
        None => Err(format!("instruction limit {limit} reached")),
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("diagnose: {error}");
            ExitCode::FAILURE
        }
    }
}
