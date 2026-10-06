// SPDX-License-Identifier: GPL-3.0-only
use fm1_emu::{cpu::Cpu, dump::Dump, firmware::Firmware, NAMES};
use std::{env, fs::File, io::Write, path::Path, process::ExitCode};

fn number(value: &str) -> Result<u32, String> {
    if let Some(hex) = value.strip_prefix("0x") {
        u32::from_str_radix(hex, 16)
    } else {
        value.parse()
    }
    .map_err(|_| format!("invalid number: {value}"))
}

fn address(firmware: &Firmware, value: &str) -> Result<u32, String> {
    firmware
        .symbols
        .get(value)
        .copied()
        .map(Ok)
        .unwrap_or_else(|| number(value))
}

fn main_run() -> Result<(), String> {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.len() < 2 || !matches!(args[0].as_str(), "probe" | "boot") {
        return Err("usage: fm1-emu <probe|boot> <application.elf|application.bin> [--entry ADDRESS] [--limit COUNT] [--trace PATH] [--until SYMBOL_OR_ADDRESS] [--inspect SYMBOL_OR_ADDRESS:WORDS] [--dump ADDRESS:BYTES:FILE] [--press COLUMN:ROW]".into());
    }
    let mut entry = None;
    let mut limit = 100_000;
    let mut trace_path = None;
    let mut until = None;
    let mut inspect = None;
    let mut dump_specs = Vec::new();
    let mut keys = Vec::new();
    let mut i = 2;
    while i < args.len() {
        let value = args
            .get(i + 1)
            .ok_or_else(|| format!("missing value for {}", args[i]))?;
        match args[i].as_str() {
            "--entry" => entry = Some(number(value)?),
            "--limit" => limit = value.parse().map_err(|_| "invalid instruction limit")?,
            "--trace" => trace_path = Some(value),
            "--until" => until = Some(value),
            "--inspect" => inspect = Some(value),
            "--dump" => dump_specs.push(value),
            "--press" => {
                let (column, row) = value.split_once(':').ok_or("--press requires COLUMN:ROW")?;
                keys.push((
                    column
                        .parse::<usize>()
                        .map_err(|_| "invalid matrix column")?,
                    row.parse::<usize>().map_err(|_| "invalid matrix row")?,
                ));
            }
            option => return Err(format!("unknown option: {option}")),
        }
        i += 2;
    }
    let firmware = Firmware::load(Path::new(&args[1]))?;
    let stop = until.map(|name| address(&firmware, name)).transpose()?;
    let dumps = dump_specs
        .iter()
        .map(|spec| Dump::parse(spec, &firmware.symbols))
        .collect::<Result<Vec<_>, _>>()?;
    if args[0] == "probe" && !dumps.is_empty() {
        return Err("--dump needs boot".into());
    }
    let inspection = inspect
        .map(|request| {
            let (name, count) = request
                .split_once(':')
                .ok_or("--inspect requires SYMBOL:WORDS")?;
            let address = address(&firmware, name)?;
            let count: u32 = count.parse().map_err(|_| "invalid inspection word count")?;
            if count > 1024 {
                return Err("inspection is limited to 1024 words".into());
            }
            Ok::<_, String>((address, count))
        })
        .transpose()?;
    let entry = if let Some(entry) = entry {
        entry
    } else if args[0] == "probe" {
        *firmware
            .symbols
            .get("fm1_probe")
            .ok_or("probe needs ELF symbols or --entry for a raw image")?
    } else {
        firmware.entry
    };
    let image_bytes = firmware.image.len();
    let mut cpu = Cpu::new(firmware.bus()?, entry);
    for (column, row) in keys {
        cpu.bus.devices.gpio.press(column, row, true)?;
    }
    let mut trace = trace_path
        .map(File::create)
        .transpose()
        .map_err(|error| error.to_string())?;
    let trace_writer = trace.as_mut().map(|file| file as &mut dyn Write);
    if args[0] == "probe" {
        let values = cpu
            .probe(limit, trace_writer)
            .map_err(|error| error.to_string())?;
        println!("{{\"mode\":\"probe\",\"entry\":{entry},\"image_bytes\":{image_bytes},\"instructions\":{},\"values\":{{", cpu.steps);
        for (i, (name, value)) in NAMES.iter().zip(values).enumerate() {
            println!(
                "  \"{name}\":{value}{}",
                if i + 1 == NAMES.len() { "" } else { "," }
            );
        }
        println!("}}}}");
        Ok(())
    } else {
        // Application handoff, not a ROM/SPL emulator. Unknown initial CPU state
        // is zeroed; crt0 immediately supplies the application's own stacks.
        cpu.r[0] = 0x01c7_fe08;
        let run = cpu.run(stop, limit, trace_writer);
        // Dump even after a fault: that is when a post-mortem is needed.
        let dump_errors: Vec<String> = dumps
            .iter()
            .filter_map(|dump| dump.write(&cpu.bus).err())
            .collect();
        if let Err(error) = run {
            for dump_error in dump_errors {
                eprintln!("fm1-emu: {dump_error}");
            }
            return Err(format!("after {} instructions: {error}", cpu.steps));
        }
        if let Some(error) = dump_errors.into_iter().next() {
            return Err(error);
        }
        let values = if let Some((address, count)) = inspection {
            (0..count)
                .map(|i| {
                    let address = address
                        .checked_add(i * 4)
                        .ok_or("inspection address overflow")?;
                    cpu.bus.read(address, 4).map_err(|error| error.to_string())
                })
                .collect::<Result<Vec<_>, String>>()?
        } else {
            Vec::new()
        };
        println!("{{\"mode\":\"boot\",\"pc\":{},\"instructions\":{},\"irq_entries\":{},\"inspection\":{:?}}}", cpu.pc,cpu.steps,cpu.irq_entries,values);
        Ok(())
    }
}

fn main() -> ExitCode {
    match main_run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("fm1-emu: {error}");
            ExitCode::FAILURE
        }
    }
}
