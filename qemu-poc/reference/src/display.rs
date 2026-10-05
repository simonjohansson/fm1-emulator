// SPDX-License-Identifier: GPL-3.0-only
// Black-box runner through public interfaces. Never linked into QEMU.
use fm1_emu::{bus::Bus, cpu::Cpu, firmware::Firmware, RAM};
use std::{env, fs::File, io::Write, path::Path};

pub fn run(firmware: Firmware) {
    let output = env::var("FM1_POC_FRAME_DIR").expect("set FM1_POC_FRAME_DIR");
    let stop = firmware.symbols["display_frame_done"];
    let mut cpu = Cpu::new(Bus::new(firmware.image).unwrap(), firmware.entry);
    cpu.r = std::array::from_fn(|i| 0x10203040 + i as u32 * 0x01010101);
    for offset in (0..512 * 1024).step_by(4) {
        cpu.bus.write(RAM + offset, 0xa5a5a5a5, 4).unwrap();
    }
    for frame in 1..=3 {
        if frame > 1 {
            cpu.bus.devices.gpio.press(0, 4, frame == 2).unwrap();
        }
        // Leave the previous checkpoint by executing the guest's own goto.
        cpu.step().unwrap();
        cpu.run(Some(stop), cpu.steps + 500_000, None).unwrap();
        let mut ppm = File::create(Path::new(&output).join(format!("frame-{frame}.ppm"))).unwrap();
        ppm.write_all(b"P6\n240 240\n255\n").unwrap();
        for pixel in &cpu.bus.lcd.pixels {
            ppm.write_all(&[(pixel >> 16) as u8, (pixel >> 8) as u8, *pixel as u8])
                .unwrap();
        }
        let matrix: Vec<_> = (0..11)
            .map(|i| cpu.bus.read(0x01c08068 + i * 4, 4).unwrap())
            .collect();
        let ticks = cpu.bus.read(0x01c08280, 4).unwrap();
        let mut record =
            File::create(Path::new(&output).join(format!("frame-{frame}.json"))).unwrap();
        writeln!(record,
                 "{{\"frame\":{},\"pc\":{},\"instructions\":{},\"display_ticks\":{},\"sp\":{},\"ssp\":{},\"visible\":{},\"pixels_written\":{},\"matrix\":{:?},\"registers\":{:?},\"specials\":{:?}}}",
                 frame, cpu.pc, cpu.steps, ticks, cpu.sr[14], cpu.sr[13],
                 cpu.bus.screen_visible(), cpu.bus.lcd.pixels_written, matrix, cpu.r, cpu.sr).unwrap();
    }
    let results: Vec<_> = (0..10)
        .map(|i| cpu.bus.read(0x01c08010 + i * 4, 4).unwrap())
        .collect();
    let probe: Vec<_> = (0..12)
        .map(|i| cpu.bus.read(0x01c08038 + i * 4, 4).unwrap())
        .collect();
    println!(
        "{{\"pc\":{},\"instructions\":{},\"irq_entries\":{},\"inspection\":{:?},\"probe\":{:?}}}",
        cpu.pc, cpu.steps, cpu.irq_entries, results, probe
    );
}
