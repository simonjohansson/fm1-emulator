// SPDX-License-Identifier: GPL-3.0-only
use fm1_emu::{bus::Bus, dump::Dump, RAM, RAM_SIZE, XIP};

fn plain_window(bus: &mut Bus) {
    bus.write(0x4030c, 0x0208f000, 4).unwrap();
    bus.write(0x40308, 0x07ffffff, 4).unwrap();
    bus.write(0x40300, 3, 1).unwrap();
}

#[test]
fn erased_user_flash_reads_through_the_guest_plain_window() {
    let mut bus = Bus::new(vec![0x12, 0x34, 0x56, 0x78]).unwrap();
    assert!(bus.read(0x0209c000, 4).is_err());
    plain_window(&mut bus);
    assert_eq!(bus.read(XIP, 4).unwrap(), 0x78563412);
    for address in [0x0208f000, 0x0209c000, 0x020f8000, 0x020fbffc] {
        assert_eq!(bus.read(address, 4).unwrap(), u32::MAX);
        assert_eq!(bus.read(address, 2).unwrap(), 0xffff);
        assert_eq!(bus.read(address, 1).unwrap(), 0xff);
    }
    assert!(bus.read(0x020fc000, 1).is_err()); // Physical NOR is exactly 1 MiB.
    assert!(bus.read(0x0208effc, 4).is_err()); // Outside the plaintext range.
    assert!(bus.write(0x0209c000, 0, 4).is_err()); // XIP is read-only.
    bus.write(0x40200, 0, 4).unwrap();
    assert!(bus.read(XIP, 4).is_err());
    assert!(bus.read(0x0209c000, 4).is_err());
}

#[test]
fn read_bytes_returns_the_guests_bytes_in_order() {
    let mut bus = Bus::new(vec![0; 8]).unwrap();
    bus.write(RAM, 0x04030201, 4).unwrap();
    bus.write(RAM + 4, 0x0807, 2).unwrap();
    assert_eq!(bus.read_bytes(RAM, 6).unwrap(), [1, 2, 3, 4, 7, 8]);
    assert_eq!(bus.read_bytes(RAM + 1, 2).unwrap(), [2, 3]);
    assert!(bus.read_bytes(RAM + 0x1234, 0).unwrap().is_empty());
}

#[test]
fn read_bytes_names_the_first_unreadable_address() {
    let bus = Bus::new(vec![0; 8]).unwrap();
    let end = RAM + RAM_SIZE as u32;
    assert_eq!(bus.read_bytes(end - 4, 4).unwrap().len(), 4);
    assert_eq!(bus.read_bytes(end - 4, 8).unwrap_err().address, end);
    assert_eq!(
        bus.read_bytes(0x1000_0000, 4).unwrap_err().address,
        0x1000_0000
    );
    assert_eq!(
        bus.read_bytes(u32::MAX - 1, 8).unwrap_err().address,
        u32::MAX - 1
    );
    assert!(bus.read_bytes(RAM, 17 << 20).is_err());
}

#[test]
fn dump_specs_parse_and_write_raw_bytes() {
    let symbols = std::collections::BTreeMap::from([("ram_start".to_string(), RAM)]);
    let dump = Dump::parse("ram_start:0x10:/tmp/a:b.bin", &symbols).unwrap();
    assert_eq!((dump.address, dump.len), (RAM, 16));
    assert_eq!(dump.path, std::path::PathBuf::from("/tmp/a:b.bin"));
    assert_eq!(
        Dump::parse("0x1c00004:8:x", &symbols).unwrap().address,
        RAM + 4
    );
    for bad in ["1:2", "1:2:", "nosuch:2:f", "1:z:f"] {
        assert!(Dump::parse(bad, &symbols).is_err(), "{bad}");
    }
    let mut bus = Bus::new(vec![0; 8]).unwrap();
    bus.write(RAM, 0x04030201, 4).unwrap();
    let dir = std::env::temp_dir().join(format!("fm1-dump-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let ok = Dump {
        address: RAM,
        len: 4,
        path: dir.join("ok.bin"),
    };
    ok.write(&bus).unwrap();
    assert_eq!(std::fs::read(&ok.path).unwrap(), [1, 2, 3, 4]);
    let bad = Dump {
        address: 0x1000_0000,
        len: 4,
        path: dir.join("bad.bin"),
    };
    assert!(bad.write(&bus).unwrap_err().contains("0x10000000"));
    assert!(!bad.path.exists());
    std::fs::remove_dir_all(&dir).unwrap();
}
