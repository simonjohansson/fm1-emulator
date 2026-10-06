// SPDX-License-Identifier: GPL-3.0-only
// Separate process oracle using public Rust interfaces only. Never linked into QEMU.
use fm1_emu::{bus::Bus, cpu::Cpu, firmware::Firmware, RAM, RAM_SIZE, XIP};
use std::{env, fs, io::Write, path::Path};

fn number(name: &str) -> Result<u64, String> {
    let text = env::var(name).map_err(|_| format!("set {name}"))?;
    let parsed = if let Some(hex) = text.strip_prefix("0x") {
        u64::from_str_radix(hex, 16)
    } else {
        text.parse()
    };
    parsed.map_err(|error| format!("invalid {name}: {error}"))
}

fn quoted(text: &str) -> String {
    let mut output = String::from("\"");
    for character in text.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            c if c <= '\u{1f}' => output.push_str(&format!("\\u{:04x}", c as u32)),
            c => output.push(c),
        }
    }
    output.push('"');
    output
}

fn save(
    cpu: &Cpu,
    directory: &Path,
    reason: &str,
    rti_count: u64,
    timer_entries: u64,
    audio_entries: u64,
) -> Result<(), String> {
    let mut ram = Vec::with_capacity(RAM_SIZE);
    for offset in (0..RAM_SIZE).step_by(4) {
        let value = cpu
            .bus
            .read(RAM + offset as u32, 4)
            .map_err(|error| error.to_string())?;
        ram.extend_from_slice(&value.to_le_bytes());
    }
    fs::write(directory.join("state.sram"), &ram).map_err(|error| error.to_string())?;
    let mut image =
        fs::File::create(directory.join("lcd.ppm")).map_err(|error| error.to_string())?;
    image
        .write_all(b"P6\n240 240\n255\n")
        .map_err(|error| error.to_string())?;
    for pixel in &cpu.bus.lcd.pixels {
        image
            .write_all(&[(pixel >> 16) as u8, (pixel >> 8) as u8, *pixel as u8])
            .map_err(|error| error.to_string())?;
    }
    let timer_registers = |base| -> Result<[u32; 3], String> {
        Ok([
            cpu.bus.read(base, 4).map_err(|error| error.to_string())?,
            cpu.bus
                .read(base + 4, 4)
                .map_err(|error| error.to_string())?,
            cpu.bus
                .read(base + 8, 4)
                .map_err(|error| error.to_string())?,
        ])
    };
    let state = format!(concat!(
        "{{\"profile\":\"felucca-reference\",\"reason\":{},\"pc\":{},",
        "\"instructions\":{},\"registers\":{:?},\"specials\":{:?},",
        "\"irq_entries\":{},\"rti_count_observed\":{},\"interrupts_enabled\":{},",
        "\"irq_sources_observed\":{{\"timer5\":{},\"alnk0\":{}}},",
        "\"timer4_registers\":{:?},\"timer5_registers\":{:?},\"timer5_pending\":{},",
        "\"watchdog_feeds\":{},\"watchdog_ticks\":{},",
        "\"audio\":{{\"frames\":{},\"halves\":{}}},",
        "\"usb\":{{\"setups\":{},\"packets\":{}}},\"latched_columns\":{},",
        "\"lcd\":{{\"visible\":{},\"display_on\":{},\"sleeping\":{},\"pixels_written\":{}}},",
        "\"comparison_scope\":{{\"input_hashes\":\"not checked here; caller must verify pinned inputs\",",
        "\"xip\":\"supplied raw application image\",",
        "\"physical_nor\":\"1 MiB erased FF; application bytes are not seeded in private NOR\",",
        "\"clock\":\"existing Rust functional clock; instruction counts and time need not equal QEMU\",",
        "\"usb_initialization\":\"existing reference defaults, not asserted equal to QEMU disconnected host\",",
        "\"private_device_counters\":\"not exposed; no timer expiration/ack or NOR counters fabricated\"}}}}\n"),
        quoted(reason), cpu.pc, cpu.steps, cpu.r, cpu.sr,
        cpu.irq_entries, rti_count, cpu.interrupts_enabled, timer_entries, audio_entries,
        timer_registers(0x10800)?, timer_registers(0x10900)?, cpu.bus.devices.timer5.pending,
        cpu.bus.system.watchdog_feeds, cpu.bus.system.watchdog_ticks,
        cpu.bus.audio.frames, cpu.bus.audio.halves,
        cpu.bus.usb.setups, cpu.bus.usb.packets, cpu.bus.devices.gpio.latched,
        cpu.bus.screen_visible(), cpu.bus.lcd.display_on, cpu.bus.lcd.sleeping,
        cpu.bus.lcd.pixels_written);
    fs::write(directory.join("state.json"), &state).map_err(|error| error.to_string())?;
    print!("{state}");
    Ok(())
}

pub fn run(firmware: Firmware) -> Result<(), String> {
    let stop = number("FM1_POC_STOP_PC")?;
    let limit = number("FM1_POC_MAX_INSTRUCTIONS")?;
    if stop > u32::MAX as u64 || stop & 1 != 0 || limit == 0 {
        return Err("use an aligned 32-bit stop PC and a positive instruction limit".into());
    }
    if firmware.entry != XIP || firmware.image.len() != 0x65f84 {
        return Err("Felucca comparison requires the selected application entry and length".into());
    }
    let output = env::var("FM1_REFERENCE_STATE_DIR")
        .map_err(|_| "set FM1_REFERENCE_STATE_DIR".to_string())?;
    let directory = Path::new(&output);
    fs::create_dir_all(directory).map_err(|error| error.to_string())?;
    for name in ["state.json", "state.sram", "lcd.ppm"] {
        match fs::remove_file(directory.join(name)) {
            Ok(()) => (),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
            Err(error) => return Err(error.to_string()),
        }
    }
    let mut cpu = Cpu::new(Bus::new(firmware.image)?, firmware.entry);
    cpu.r = std::array::from_fn(|index| 0x10203040 + index as u32 * 0x01010101);
    cpu.r[0] = 0x01c7fe08;
    cpu.sr = [0; 16];
    // Selected artifact section bounds. Cold noinit/loader/vector state stays zero.
    for (start, end) in [
        (0x01c00000, 0x01c00b48),
        (0x01c08000, 0x01c08218),
        (0x01c08220, 0x01c1282c),
        (0x01c20000, 0x01c5f920),
        (0x01c7fd80, 0x01c7fe00),
    ] {
        for address in (start..end).step_by(4) {
            cpu.bus
                .write(address, 0xa5a5a5a5, 4)
                .map_err(|error| error.to_string())?;
        }
    }
    let mut rti_count = 0;
    let mut timer_entries = 0;
    let mut audio_entries = 0;
    let failure = loop {
        if cpu.pc == stop as u32 {
            break None;
        }
        if cpu.steps >= limit {
            break Some(format!(
                "reference instruction limit reached at PC 0x{:08x}",
                cpu.pc
            ));
        }
        let previous_entries = cpu.irq_entries;
        match cpu.step() {
            Ok("rti") => rti_count += 1,
            Ok(_) => (),
            Err(error) => break Some(error.to_string()),
        }
        if cpu.irq_entries != previous_entries {
            match (cpu.sr[11] >> 16) & 127 {
                63 => timer_entries += cpu.irq_entries - previous_entries,
                11 => audio_entries += cpu.irq_entries - previous_entries,
                _ => (),
            }
        }
    };
    save(
        &cpu,
        directory,
        failure.as_deref().unwrap_or("checkpoint reached"),
        rti_count,
        timer_entries,
        audio_entries,
    )?;
    if let Some(reason) = failure {
        Err(reason)
    } else {
        Ok(())
    }
}
