// SPDX-License-Identifier: GPL-3.0-only
// Separate executable using existing public Rust interfaces. Never linked into QEMU.
use fm1_emu::{bus::Bus, cpu::Cpu, firmware::Firmware, SYSTEM_STACK, USER_STACK, XIP};
use std::{env, fs, path::Path};
mod display;
mod snapshot;

fn main() {
    let args: Vec<String> = env::args().collect();
    assert!(
        args.len() == 3,
        "usage: reference probe|timer|direct-probe|display|snapshot IMAGE"
    );
    let firmware = Firmware::load(Path::new(&args[2])).unwrap();
    if args[1] == "snapshot" {
        snapshot::run(firmware);
        return;
    }
    if args[1] == "display" {
        display::run(firmware);
        return;
    }
    let timer = args[1] == "timer";
    let mut cpu = Cpu::new(
        Bus::new(firmware.image).unwrap(),
        if timer { 0x02000238 } else { XIP },
    );
    if args[1] == "direct-probe" {
        cpu.pc = firmware.symbols["fm1_probe"];
        let values = cpu.probe(1000, None).unwrap();
        println!(
            "{{\"instructions\":{},\"inspection\":{:?}}}",
            cpu.steps, values
        );
        return;
    }
    assert!(timer || args[1] == "probe");
    cpu.r = std::array::from_fn(|i| 0x10203040 + i as u32 * 0x01010101);
    if timer {
        cpu.sr[14] = USER_STACK;
        cpu.sr[13] = SYSTEM_STACK;
    }
    let stop = if timer { 0x020002ba } else { 0x0200013a };
    let mut trace = env::var("POC_TRACE")
        .ok()
        .map(|p| fs::File::create(p).unwrap());
    cpu.run(
        Some(stop),
        200000,
        trace.as_mut().map(|f| f as &mut dyn std::io::Write),
    )
    .unwrap();
    let address = if timer { 0x01c08010 } else { 0x01c08000 };
    let words = if timer { 10 } else { 12 };
    let results: Vec<_> = (0..words)
        .map(|i| cpu.bus.read(address + i * 4, 4).unwrap())
        .collect();
    println!("{{\"pc\":{},\"instructions\":{},\"irq_entries\":{},\"pending\":{},\"inspection\":{:?},\"registers\":{:?},\"specials\":{:?}}}",
        cpu.pc, cpu.steps, cpu.irq_entries, cpu.bus.devices.timer5.pending, results, cpu.r, cpu.sr);
}
