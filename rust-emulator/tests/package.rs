// SPDX-License-Identifier: GPL-3.0-only
use fm1_emu::{cpu::Cpu, firmware::Firmware, XIP};
use std::path::Path;

#[test]
fn full_package_preserves_application_and_supplies_the_spl_handoff() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../build/display");
    let package = Firmware::load(&root.join("firmware.fwsc")).unwrap();
    let elf = Firmware::load(&root.join("firmware.elf")).unwrap();
    assert!(package.image.starts_with(&elf.image));
    let mut bus = package.bus().unwrap();
    assert_eq!(bus.read(0x10200, 4).unwrap(), 0x6f01);
    assert!(bus.write(0x10200, 0, 4).is_err());
    for (address, value) in [
        (0x40200, 0x009803b5),
        (0x40204, 1),
        (0x40208, 0x8e17),
        (0x4020c, 0x4000),
        (0x40300, 1),
        (0x40304, 0),
        (0x16a04, 0x8881c3),
        (0x51000, 0xe0c),
        (0x51004, 0),
    ] {
        assert_eq!(bus.read(address, 4).unwrap(), value);
    }
    bus.write(0x4020c, 0x4004, 4).unwrap();
    assert_eq!(
        bus.read(XIP, 2).unwrap(),
        u16::from_le_bytes([package.image[4], package.image[5]]) as u32
    );
    bus.write(0x4020c, 0x4000, 4).unwrap();
    assert!(bus.write(0x40304, 1, 4).is_err());
    // Measured FM-1 handoff before application peripheral initialization.
    for (address, value) in [
        (0x10008, 0x10200),
        (0x1000c, 0x1c1),
        (0x10010, 0x10000),
        (0x10014, 6),
        (0x10018, 2),
        (0x119a0, 0x45400203),
        (0x119a4, 0x3f503026),
        (0x119a8, 0x0940022b),
        (0x119ac, 0x0750310c),
        (0x13e00, 0x100),
        (0x13e04, 0xe0),
    ] {
        assert_eq!(bus.read(address, 4).unwrap(), value);
    }
    let head = bus.read(0x01c7fe08, 4).unwrap();
    assert_eq!(bus.read(head + 8, 4).unwrap(), 0xff000);
    assert_eq!(bus.read(0x01c7fe0c, 4).unwrap(), 0x4000);
    assert_eq!(bus.read(0x01c7fe10, 4).unwrap(), 0x02000000);
    assert_eq!(bus.read(0x01c7fe14, 2).unwrap(), 0x980f);
    assert_eq!(
        bus.read(XIP, 2).unwrap(),
        u16::from_le_bytes([package.image[0], package.image[1]]) as u32
    );
    // Directory bytes preceding app.bin must also be available through SFC.
    assert_eq!(
        bus.read(0x02000010, 4).unwrap(),
        u32::from_le_bytes(*b"app_")
    );
    let mut cpu = Cpu::new(bus, package.entry);
    cpu.r[0] = 0x01c7fe08;
    for _ in 0..200_000_000 {
        cpu.step().unwrap();
    }
    assert!(cpu.bus.screen_visible());
    assert!(cpu.bus.lcd.pixels_written >= 240 * 240);
    assert!(cpu.bus.system.watchdog_feeds > 0);
    assert_eq!(cpu.bus.usb.setups, 5);
}

fn display_package() -> Firmware {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../build/display");
    Firmware::load(&root.join("firmware.fwsc")).unwrap()
}

fn plain_window(bus: &mut fm1_emu::bus::Bus) {
    bus.write(0x4030c, 0x0200_0000, 4).unwrap();
    bus.write(0x40308, 0x07ff_ffff, 4).unwrap();
    bus.write(0x40300, 3, 1).unwrap();
}

#[test]
fn flash_image_supplies_everything_outside_the_package() {
    let package = display_package();
    let image = vec![0xa5; 0x100000];
    let mut plain = package.bus().unwrap();
    let mut over = package.bus_with_flash(&image).unwrap();
    // The package's own flash: encrypted XIP reads see the application.
    assert_eq!(over.read(XIP, 4).unwrap(), plain.read(XIP, 4).unwrap());
    assert_eq!(over.read(XIP, 4).unwrap(), {
        u32::from_le_bytes(package.image[..4].try_into().unwrap())
    });
    // Beyond it (user data, 0x93000 and up) the image shows through the
    // plain window, where a normal boot sees erased flash.
    plain_window(&mut plain);
    plain_window(&mut over);
    for address in [0x0208f000, 0x0209c000, 0x020fbffc] {
        assert_eq!(plain.read(address, 4).unwrap(), u32::MAX);
        assert_eq!(over.read(address, 4).unwrap(), 0xa5a5a5a5);
    }
    // And the package still wins where it has bytes.
    for address in [XIP, 0x02004000] {
        assert_eq!(
            over.read(address, 4).unwrap(),
            plain.read(address, 4).unwrap()
        );
    }
}

#[test]
fn an_all_erased_image_matches_the_normal_boot() {
    let package = display_package();
    let mut plain = package.bus().unwrap();
    let mut over = package.bus_with_flash(&vec![0xff; 0x100000]).unwrap();
    plain_window(&mut plain);
    plain_window(&mut over);
    for address in (0x0200_0000..0x0210_0000).step_by(0x1c3d4) {
        assert_eq!(over.read(address, 4).ok(), plain.read(address, 4).ok());
    }
}

#[test]
fn flash_image_must_be_a_full_dump_over_a_package() {
    let package = display_package();
    for length in [0, 0x1000, 0xfffff, 0x100001] {
        let error = package.bus_with_flash(&vec![0; length]).err().unwrap();
        assert!(error.contains("expected 1048576"), "{error}");
    }
    let elf = Firmware::load(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("../build/display/firmware.elf"),
    )
    .unwrap();
    let error = elf.bus_with_flash(&vec![0; 0x100000]).err().unwrap();
    assert!(error.contains(".fwsc"), "{error}");
}
