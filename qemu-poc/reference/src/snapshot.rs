// SPDX-License-Identifier: GPL-3.0-only
// Standalone black-box oracle, using only the existing public CPU/bus API.
use fm1_emu::{bus::Bus, cpu::Cpu, firmware::Firmware};
use std::env;

pub fn run(firmware: Firmware) {
    let mut cpu = Cpu::new(Bus::new(firmware.image).unwrap(), firmware.entry);
    cpu.r = std::array::from_fn(|i| 0x10203040 + i as u32 * 0x01010101);
    cpu.r[0] = 0x01c7fe08;
    cpu.sr = [0; 16];
    for (start, end) in [(0x01c00000, 0x01c00b48), (0x01c08000, 0x01c08014),
                          (0x01c08020, 0x01c09d00), (0x01c7fd80, 0x01c7fe00)] {
        for address in (start..end).step_by(4) { cpu.bus.write(address, 0xa5a5a5a5, 4).unwrap(); }
    }
    let stop = u32::from_str_radix(env::var("FM1_POC_STOP_PC").unwrap().trim_start_matches("0x"), 16).unwrap();
    cpu.run(Some(stop), 1_000_000, None).unwrap();
    let results: Vec<_> = (0..12).map(|i| cpu.bus.read(0x01c08000 + i * 4, 4).unwrap()).collect();
    println!("{{\"pc\":{},\"instructions\":{},\"inspection\":{:?},\"registers\":{:?},\"specials\":{:?}}}",
             cpu.pc, cpu.steps, results, cpu.r, cpu.sr);
}
