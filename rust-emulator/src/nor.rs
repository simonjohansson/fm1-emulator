// SPDX-License-Identifier: GPL-3.0-only
// SPI0 serial NOR. Application ELF supplies decrypted XIP separately.

enum Pending {
    Program(usize, Vec<u8>),
    Erase(usize),
}

pub struct Nor {
    regs: [u32; 14],
    command: Vec<u8>,
    selected: bool,
    pub bytes: Vec<u8>,
    cursor: usize,
    decoded: Option<Vec<u8>>,
    key: u16,
    write_enabled: bool,
    busy_ticks: u32,
    pending: Option<Pending>,
}

impl Default for Nor {
    fn default() -> Self {
        Self {
            regs: [1, 0, 0, 0x4000, 1, 0, 0, 0, 0, 0, 32, 0, 0, 0],
            command: vec![],
            selected: false,
            bytes: vec![255; 1024 * 1024],
            cursor: 0,
            decoded: None,
            key: 0,
            write_enabled: false,
            busy_ticks: 0,
            pending: None,
        }
    }
}

impl Nor {
    /// Start from a raw full-chip dump; `load` then installs a package on top.
    pub fn set_image(&mut self, image: &[u8]) -> Result<(), String> {
        if image.len() != self.bytes.len() {
            return Err(format!(
                "flash image is {} bytes, expected {}",
                image.len(),
                self.bytes.len()
            ));
        }
        self.bytes.copy_from_slice(image);
        Ok(())
    }
    pub fn load(&mut self, bytes: &[u8], key: u16) {
        self.bytes[..bytes.len()].copy_from_slice(bytes);
        let mut decoded = self.bytes[0x4000..].to_vec();
        crate::package::sfc(&mut decoded, key);
        self.decoded = Some(decoded);
        self.key = key;
        self.regs[..3].copy_from_slice(&[0x809803b5, 1, 0x8e17]);
    }
    pub fn packaged(&self) -> bool {
        self.decoded.is_some()
    }
    pub fn xip_active(&self) -> bool {
        self.read(0x40200).unwrap() & 1 != 0 && self.read(0x5101c).unwrap() & 32 != 0
    }

    pub fn xip(&self, address: u32, size: usize) -> Option<Result<u32, &'static str>> {
        // The SFC maps flash offset 0x4000 at CPU address 0x02000000.
        let offset = address.checked_sub(0x0200_0000)? as usize + self.read(0x4020c)? as usize;
        let bytes = self.bytes.get(offset..offset.checked_add(size)?)?;
        if self.busy_ticks != 0 {
            return Some(Err("XIP unavailable while SPI NOR is busy"));
        }
        if !self.xip_active() {
            return Some(Err(
                "XIP unavailable while SFC or flash pin routing is disabled",
            ));
        }
        let control = self.read(0x40300).unwrap();
        let plain = control & 1 == 0
            || (control & 2 != 0
                && address >= self.read(0x4030c).unwrap()
                && address.checked_add(size as u32 - 1)? <= self.read(0x40308).unwrap());
        if !plain {
            if let Some(decoded) = &self.decoded {
                if offset < 0x4000 {
                    return Some(Err(
                        "encrypted XIP below application area is not implemented",
                    ));
                }
                let bytes = &decoded[offset - 0x4000..offset - 0x4000 + size];
                return Some(Ok(bytes
                    .iter()
                    .enumerate()
                    .fold(0, |value, (i, byte)| value | ((*byte as u32) << (i * 8)))));
            } else {
                return Some(Err(
                    "encrypted XIP outside the supplied application is not available",
                ));
            }
        }
        Some(Ok(bytes.iter().enumerate().fold(0, |value, (i, byte)| {
            value | ((*byte as u32) << (i * 8))
        })))
    }

    pub fn read(&self, a: u32) -> Option<u32> {
        Self::register_index(a).map(|index| {
            let value = self.regs[index];
            // Bit 31 is transaction busy, not retained configuration. The
            // functional bus completes each access before a following read.
            if a == 0x40200 {
                value & !0x80000000
            } else {
                value
            }
        })
    }
    fn register_index(a: u32) -> Option<usize> {
        match a {
            0x40200..=0x4020c if a & 3 == 0 => Some(((a - 0x40200) / 4) as usize),
            0x40300..=0x40314 if a & 3 == 0 => Some(4 + ((a - 0x40300) / 4) as usize),
            0x5101c => Some(10),
            0x11c00..=0x11c08 if a & 3 == 0 => Some(11 + ((a - 0x11c00) / 4) as usize),
            _ => None,
        }
    }
    pub fn chip_select(&mut self, selected: bool) {
        if self.selected != selected {
            if !selected && self.busy_ticks == 0 {
                self.finish_command();
            }
            self.command.clear();
            self.cursor = 0;
        }
        self.selected = selected;
    }

    fn finish_command(&mut self) {
        // P25Q80H datasheet sections 10.2, 10.3, 10.20 and 10.24:
        // commands latch on CS rising; writes require WEL. Typical times are
        // 2 ms program and 8 ms sector erase, on the 24 MHz oscillator clock.
        match self.command.as_slice() {
            [0x06] => self.write_enabled = true,
            [0x04] => self.write_enabled = false,
            [0x02, a, b, c, data @ ..] if self.write_enabled && !data.is_empty() => {
                let address = ((*a as usize) << 16) | ((*b as usize) << 8) | *c as usize;
                self.pending = Some(Pending::Program(address % self.bytes.len(), data.to_vec()));
                self.busy_ticks = 48_000;
            }
            [0x20, a, b, c] if self.write_enabled => {
                let address = ((*a as usize) << 16) | ((*b as usize) << 8) | *c as usize;
                self.pending = Some(Pending::Erase(address % self.bytes.len() & !0xfff));
                self.busy_ticks = 192_000;
            }
            _ => {}
        }
    }

    pub(crate) fn advance(&mut self, ticks: u32) {
        self.busy_ticks = self.busy_ticks.saturating_sub(ticks);
        if self.busy_ticks != 0 {
            return;
        }
        let (start, end) = match self.pending.take() {
            Some(Pending::Program(address, data)) => {
                let page = address & !255;
                // The page buffer wraps; when overloaded, only its last 256
                // bytes survive. Programming can only clear bits.
                for (i, byte) in data.iter().enumerate().skip(data.len().saturating_sub(256)) {
                    self.bytes[page + ((address + i) & 255)] &= byte;
                }
                (page, page + 256)
            }
            Some(Pending::Erase(address)) => {
                self.bytes[address..address + 4096].fill(255);
                (address, address + 4096)
            }
            None => return,
        };
        self.write_enabled = false;
        if let Some(decoded) = &mut self.decoded {
            let start = start.max(0x4000);
            if start < end {
                let plain = &mut decoded[start - 0x4000..end - 0x4000];
                plain.copy_from_slice(&self.bytes[start..end]);
                for (i, block) in plain.chunks_mut(32).enumerate() {
                    crate::package::enc(block, self.key ^ ((start - 0x4000) / 4 + i * 8) as u16);
                }
            }
        }
    }
    pub fn write(&mut self, a: u32, v: u32) -> Option<Result<(), &'static str>> {
        self.read(a)?;
        if (a == 0x40304 && v != 0) || ((a == 0x40310 || a == 0x40314) && v != 0) {
            return Some(Err(
                "SFC dynamic key and encrypted-window changes are not implemented",
            ));
        }
        let mut value = v;
        if a == 0x11c00 {
            value = v & !0xc000;
            if v & 0x4000 == 0 {
                value |= self.read(a).unwrap() & 0x8000;
            }
        }
        if a == 0x11c08 {
            if !self.selected {
                return Some(Err("SPI0 transfer with flash deselected"));
            }
            self.command.push(v as u8);
            let len = self.command.len();
            value = 255;
            match self.command[0] {
                0x4b => {
                    // Puya P25Q80H: four dummy bytes then a 128-bit ID.
                    // Stable emulator chip identity, not the physical unit's ID.
                    const UID: [u8; 16] = *b"FM1-EMU-NOR-0001";
                    if len > 5 {
                        value = UID.get(len - 6).copied().unwrap_or(255) as u32;
                    }
                }
                0x9f => {
                    if len > 1 {
                        value = [0x85, 0x60, 0x14].get(len - 2).copied().unwrap_or(255);
                    }
                }
                0x05 => value = ((self.write_enabled as u32) << 1) | (self.busy_ticks != 0) as u32,
                0x35 => value = 0,
                0x06 | 0x04 | 0x02 | 0x20 => {}
                0x03 | 0x0b | 0x6b => {
                    let start = if self.command[0] == 3 { 4 } else { 5 };
                    if len == 4 {
                        self.cursor = ((self.command[1] as usize) << 16)
                            | ((self.command[2] as usize) << 8)
                            | self.command[3] as usize;
                    }
                    if len > start {
                        value = self.bytes[self.cursor % self.bytes.len()] as u32;
                        self.cursor += 1;
                    }
                }
                _ => return Some(Err("unimplemented SPI NOR command")),
            }
            self.regs[11] |= 0x8000;
        }
        self.regs[Self::register_index(a).unwrap()] = value;
        Some(Ok(()))
    }
}

#[cfg(test)]
mod tests {
    use super::Nor;

    fn transaction(nor: &mut Nor, bytes: &[u32]) -> u32 {
        nor.chip_select(true);
        for &byte in bytes {
            nor.write(0x11c08, byte).unwrap().unwrap();
        }
        let result = nor.read(0x11c08).unwrap();
        nor.chip_select(false);
        result
    }

    #[test]
    fn package_installs_over_an_image_before_the_xip_decode() {
        let mut nor = Nor::default();
        assert!(nor.set_image(&[0; 0x1000]).is_err());
        nor.set_image(&vec![0xa5; 0x100000]).unwrap();
        nor.load(&vec![0x55; 0x4100], 0x980f);
        assert!(nor.bytes[..0x4100].iter().all(|&byte| byte == 0x55));
        assert!(nor.bytes[0x4100..].iter().all(|&byte| byte == 0xa5));
        // Decoded after both are down: package bytes, then the image's.
        let mut expected = vec![0x55; 0x100];
        expected.resize(0x100000 - 0x4000, 0xa5);
        crate::package::sfc(&mut expected, 0x980f);
        assert_eq!(nor.decoded.as_deref(), Some(&expected[..]));
    }

    #[test]
    fn writes_require_a_complete_write_enable_transaction() {
        let mut nor = Nor::default();
        nor.chip_select(true);
        nor.write(0x11c08, 6).unwrap().unwrap();
        assert!(!nor.write_enabled);
        nor.chip_select(false);
        assert_eq!(transaction(&mut nor, &[5, 255]), 2);
        transaction(&mut nor, &[4]);
        assert_eq!(transaction(&mut nor, &[5, 255]), 0);
        transaction(&mut nor, &[2, 9, 0, 0, 0]);
        nor.advance(48_000);
        assert_eq!(nor.bytes[0x90000], 255);
        transaction(&mut nor, &[6]);
        transaction(&mut nor, &[0x20, 9, 0]); // incomplete address
        assert_eq!(nor.busy_ticks, 0);
        assert!(nor.write_enabled);
    }

    #[test]
    fn page_program_waits_for_completion_wraps_and_only_clears_bits() {
        let mut nor = Nor::default();
        transaction(&mut nor, &[6]);
        transaction(&mut nor, &[2, 9, 0, 255, 0x0f, 0xf0]);
        assert_eq!(nor.bytes[0x900ff], 255);
        assert_eq!(transaction(&mut nor, &[5, 255]), 3); // WEL and WIP
        assert!(nor.xip(0x0208c000, 1).unwrap().is_err());
        nor.advance(47_999);
        assert_eq!(transaction(&mut nor, &[5, 255]), 3);
        nor.advance(1);
        assert_eq!(transaction(&mut nor, &[5, 255]), 0);
        assert_eq!(nor.bytes[0x900ff], 0x0f);
        assert_eq!(nor.bytes[0x90000], 0xf0);
        assert_eq!(nor.bytes[0x90100], 255);
        transaction(&mut nor, &[6]);
        transaction(&mut nor, &[2, 9, 0, 255, 0xf0]);
        nor.advance(48_000);
        assert_eq!(nor.bytes[0x900ff], 0);
    }

    #[test]
    fn page_buffer_keeps_only_the_last_256_bytes() {
        let mut nor = Nor::default();
        let mut command = vec![2, 9, 0, 0, 0];
        command.extend([255; 256]);
        transaction(&mut nor, &[6]);
        transaction(&mut nor, &command);
        nor.advance(48_000);
        assert!(nor.bytes[0x90000..0x90100].iter().all(|&byte| byte == 255));
    }

    #[test]
    fn erase_updates_raw_and_encrypted_xip_without_touching_neighbors() {
        let mut nor = Nor::default();
        nor.load(&vec![0x55; 0x94000], 0x980f);
        transaction(&mut nor, &[6]);
        transaction(&mut nor, &[0x20, 9, 0x11, 0x23]);
        assert_eq!(transaction(&mut nor, &[5, 255]), 3);
        nor.advance(192_000);
        assert_eq!(transaction(&mut nor, &[5, 255]), 0);
        assert!(nor.bytes[0x91000..0x92000].iter().all(|&byte| byte == 255));
        assert_eq!(nor.bytes[0x90fff], 0x55);
        assert_eq!(nor.bytes[0x92000], 0x55);
        let mut block = [255; 32];
        crate::package::enc(&mut block, 0x980f ^ ((0x91000 - 0x4000) / 4) as u16);
        assert_eq!(
            nor.xip(0x0208d000, 4).unwrap().unwrap(),
            u32::from_le_bytes(block[..4].try_into().unwrap())
        );
        nor.write(0x40300, 0).unwrap().unwrap();
        assert_eq!(nor.xip(0x0208d000, 4).unwrap().unwrap(), u32::MAX);
    }

    #[test]
    fn unique_id_consumes_four_dummy_bytes_and_restarts_on_chip_select() {
        let mut nor = Nor::default();
        for _ in 0..2 {
            nor.chip_select(true);
            for byte in [0x4b, 0, 0, 0, 0] {
                nor.write(0x11c08, byte).unwrap().unwrap();
                assert_eq!(nor.read(0x11c08), Some(255));
            }
            for &byte in b"FM1-EMU-NOR-0001" {
                nor.write(0x11c08, 255).unwrap().unwrap();
                assert_eq!(nor.read(0x11c08), Some(byte as u32));
            }
            nor.chip_select(false);
        }
    }

    #[test]
    fn spi_and_plain_xip_read_the_same_physical_bytes() {
        let mut nor = Nor::default();
        nor.bytes[0xa0000..0xa0004].copy_from_slice(&[0x46, 0x53, 0x4d, 0x50]);
        nor.write(0x4030c, 0x0208f000).unwrap().unwrap();
        nor.write(0x40308, 0x07ffffff).unwrap().unwrap();
        nor.write(0x40300, 3).unwrap().unwrap();
        assert_eq!(nor.xip(0x0209c000, 4).unwrap().unwrap(), 0x504d5346);
        nor.chip_select(true);
        for byte in [0x03, 0x0a, 0x00, 0x00] {
            nor.write(0x11c08, byte).unwrap().unwrap();
        }
        for expected in [0x46, 0x53, 0x4d, 0x50] {
            nor.write(0x11c08, 0xff).unwrap().unwrap();
            assert_eq!(nor.read(0x11c08), Some(expected));
        }
    }

    #[test]
    fn quad_output_read_uses_the_raw_package_bytes_after_its_dummy_byte() {
        let mut nor = Nor::default();
        nor.bytes[0x92ff0..0x92ff4].copy_from_slice(&[0x12, 0x34, 0x56, 0x78]);
        nor.chip_select(true);
        for byte in [0x6b, 9, 0x2f, 0xf0, 0] {
            nor.write(0x11c08, byte).unwrap().unwrap();
        }
        for expected in [0x12, 0x34, 0x56, 0x78] {
            nor.write(0x11c08, 255).unwrap().unwrap();
            assert_eq!(nor.read(0x11c08), Some(expected));
        }
    }
}
