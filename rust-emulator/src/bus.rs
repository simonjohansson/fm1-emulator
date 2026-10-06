// SPDX-License-Identifier: GPL-3.0-only
use crate::devices::Devices;
use crate::lcd::{Lcd, SPI};
use crate::{RAM, RAM_SIZE, XIP, XIP_END};
use std::fmt;

pub const MAX_READ_BYTES: u32 = 16 << 20;

#[derive(Debug, PartialEq, Eq)]
pub struct AccessFault {
    pub address: u32,
    pub size: usize,
    pub operation: &'static str,
    pub reason: &'static str,
}

#[cfg(test)]
mod dma_tests {
    use super::*;
    #[test]
    fn uart_dma_completion_uses_the_selected_clock_and_source_20() {
        let mut b = Bus::new(vec![0; 8]).unwrap();
        b.write(RAM, 0x007f3c90, 4).unwrap();
        b.write(0x10010, 1 << 10, 4).unwrap(); // PLL48M
        b.write(crate::devices::IRQ_CONFIG + 2 * 4, 3 << 16, 4)
            .unwrap();
        b.write(0x12100, 0x6d, 2).unwrap();
        b.write(0x12108, 383, 2).unwrap();
        b.write(0x12114, RAM, 4).unwrap();
        b.write(0x12118, 3, 2).unwrap();
        b.advance_devices(23039);
        assert_eq!(b.pending_irq(0x100), None);
        b.advance_devices(1);
        assert_eq!(b.pending_irq(0x100), Some(20));
        assert_eq!(b.read(crate::devices::IRQ_PENDING, 4).unwrap(), 1 << 20);
        b.write(0x12100, 0x206d, 2).unwrap();
        assert_eq!(b.pending_irq(0x100), None);
        b.write(0x10010, 0, 4).unwrap(); // OSC24M halves the baud rate.
        b.write(0x12118, 3, 2).unwrap();
        b.advance_devices(46079);
        assert_eq!(b.pending_irq(0x100), None);
        b.advance_devices(1);
        assert_eq!(b.pending_irq(0x100), Some(20));
        assert_eq!(b.read(RAM, 4).unwrap(), 0x007f3c90);
    }
    #[test]
    fn packaged_lcd_dma_honors_decryption_mapping_and_xip_enable() {
        let mut plain = vec![0; 0x140];
        plain[0x121..0x129].copy_from_slice(&[0xf8, 0, 7, 0xe0, 0, 0x1f, 0xff, 0xff]);
        crate::package::sfc(&mut plain, 0x980f);
        let mut raw = vec![255; 0x4140];
        raw[0x4000..].copy_from_slice(&plain);
        // The application view deliberately differs from the real flash;
        // DMA must go through SFC/encryption, not read Bus::flash directly.
        let mut b = Bus::new(vec![0; 8]).unwrap();
        b.load_flash(&raw, 0x980f);
        for (address, value) in [
            (0x51020, 0x10),
            (0x50088, !0x780),
            (SPI, 0x4021),
            (0x50080, 0),
            (SPI + 8, 0x11),
            (SPI + 8, 0x3a),
            (0x50080, 0x100),
            (SPI + 8, 0x55),
            (0x50080, 0),
            (SPI + 8, 0x2c),
            (0x50080, 0x100),
            (SPI + 12, XIP + 1),
            (SPI + 16, 4),
        ] {
            b.write(address, value, 4).unwrap();
        }
        assert_eq!(&b.lcd.pixels[..2], &[0xff0000, 0x00ff00]);
        b.write(0x4020c, 0x4004, 4).unwrap();
        b.write(SPI + 16, 4, 4).unwrap();
        assert_eq!(&b.lcd.pixels[2..4], &[0x0000ff, 0xffffff]);
        b.write(0x40200, 0, 4).unwrap();
        assert!(b.write(SPI + 16, 4, 4).unwrap_err().reason.contains("XIP"));
        assert_eq!(b.lcd.pixels_written, 4);
    }
}

impl fmt::Display for AccessFault {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{} {} bytes at 0x{:08x}: {}",
            self.operation, self.size, self.address, self.reason
        )
    }
}

pub struct Bus {
    pub flash: Vec<u8>,
    pub devices: Devices,
    pub lcd: Lcd,
    pub system: crate::system::System,
    ram: Vec<u8>,
    guards: crate::guards::Guards,
    nor: crate::nor::Nor,
    pub usb: crate::usb::Usb,
    pub audio: crate::audio::Audio,
    cache: crate::cache::Cache,
    crc: crate::crc::Crc,
    clock: crate::clock::Clock,
    wireless: crate::wireless::Wireless,
    shift_spi: crate::shift_spi::ShiftSpi,
}

impl Bus {
    pub(crate) fn core_control(&self, core: usize) -> u32 {
        self.cache.core_control(core)
    }
    pub(crate) fn load_flash(&mut self, bytes: &[u8], key: u16) {
        self.nor.load(bytes, key);
        // SPL handoff values measured before peripheral initialization.
        self.usb
            .write(0x10010, 0x10000, &mut self.ram)
            .unwrap()
            .unwrap();
        self.usb
            .write(0x51000, 0xe0c, &mut self.ram)
            .unwrap()
            .unwrap();
        self.audio.write(0x10014, 6).unwrap().unwrap();
    }
    pub fn new(flash: Vec<u8>) -> Result<Self, String> {
        if flash.is_empty() || flash.len() > (XIP_END - XIP) as usize {
            return Err("application image is empty or exceeds the XIP window".into());
        }
        Ok(Self {
            flash,
            devices: Devices::default(),
            lcd: Lcd::default(),
            system: Default::default(),
            ram: vec![0; RAM_SIZE],
            guards: Default::default(),
            nor: Default::default(),
            usb: Default::default(),
            audio: Default::default(),
            cache: Default::default(),
            crc: Default::default(),
            clock: Default::default(),
            wireless: Default::default(),
            shift_spi: Default::default(),
        })
    }

    fn fault(
        address: u32,
        size: usize,
        operation: &'static str,
        reason: &'static str,
    ) -> AccessFault {
        AccessFault {
            address,
            size,
            operation,
            reason,
        }
    }

    fn check(address: u32, size: usize, operation: &'static str) -> Result<(), AccessFault> {
        if !matches!(size, 1 | 2 | 4) {
            return Err(Self::fault(
                address,
                size,
                operation,
                "unsupported access width",
            ));
        }
        if !address.is_multiple_of(size as u32) {
            return Err(Self::fault(address, size, operation, "unaligned access"));
        }
        Ok(())
    }

    fn offset(address: u32, size: usize, base: u32, length: usize) -> Option<usize> {
        let offset = address.checked_sub(base)? as usize;
        (offset.checked_add(size)? <= length).then_some(offset)
    }

    fn read_as(
        &self,
        address: u32,
        size: usize,
        operation: &'static str,
    ) -> Result<u32, AccessFault> {
        Self::check(address, size, operation)?;
        let bytes = if let Some(offset) = Self::offset(address, size, XIP, self.flash.len()) {
            if !self.nor.xip_active() {
                return Err(Self::fault(
                    address,
                    size,
                    operation,
                    "application XIP is disabled",
                ));
            }
            if self.nor.packaged() {
                return self
                    .nor
                    .xip(address, size)
                    .unwrap()
                    .map_err(|reason| Self::fault(address, size, operation, reason));
            }
            &self.flash[offset..offset + size]
        } else if let Some(offset) = Self::offset(address, size, RAM, self.ram.len()) {
            &self.ram[offset..offset + size]
        } else {
            if let Some(value) = self.shift_spi.read(address & !3) {
                return if size == 4 {
                    Ok(value)
                } else {
                    Err(Self::fault(
                        address,
                        size,
                        operation,
                        "SPI registers require word accesses",
                    ))
                };
            }
            if let Some(result) = self.wireless.read(address, size) {
                return result.map_err(|reason| Self::fault(address, size, operation, reason));
            }
            if let Some(value) = self.clock.read(address) {
                return Ok(value);
            }
            if let Some(value) = self.crc.read(address) {
                return Ok(value);
            }
            if let Some(value) = self.cache.read(address, size) {
                return Ok(value);
            }
            if let Some(value) = self.audio.read(address) {
                return Ok(value);
            }
            if let Some(value) = self.nor.xip(address, size) {
                return value.map_err(|reason| Self::fault(address, size, operation, reason));
            }
            if let Some(value) = self.usb.read(address) {
                return Ok(value);
            }
            if let Some(value) = self.nor.read(address) {
                return Ok(value);
            }
            if let Some(value) = self.guards.read(address) {
                return Ok(value);
            }
            if let Some(value) = self.system.read(address) {
                return Ok(value);
            }
            if let Some(value) = self.lcd.read(address & !3) {
                return if size == 4 {
                    Ok(value)
                } else {
                    Err(Self::fault(
                        address,
                        size,
                        operation,
                        "SPI registers require word accesses",
                    ))
                };
            }
            if let Some(value) = self.devices.read(address, size) {
                return value
                    .map(|value| {
                        if address == crate::devices::IRQ_PENDING && self.audio.pending_irq() {
                            value | (1 << crate::audio::IRQ)
                        } else {
                            value
                        }
                    })
                    .map_err(|reason| Self::fault(address, size, operation, reason));
            }
            // No generic zero-filled MMIO: missing peripherals must be visible.
            return Err(Self::fault(
                address,
                size,
                operation,
                "unmapped memory or unimplemented MMIO",
            ));
        };
        Ok(bytes
            .iter()
            .enumerate()
            .fold(0, |value, (i, byte)| value | ((*byte as u32) << (i * 8))))
    }

    pub fn read(&self, address: u32, size: usize) -> Result<u32, AccessFault> {
        self.read_as(address, size, "read")
    }

    /// The guest's view of `len` bytes from `address`, read byte by byte.
    pub fn read_bytes(&self, address: u32, len: u32) -> Result<Vec<u8>, AccessFault> {
        if len > MAX_READ_BYTES {
            return Err(Self::fault(address, 1, "read", "range exceeds 16 MiB"));
        }
        if len > 0 && address.checked_add(len - 1).is_none() {
            return Err(Self::fault(address, 1, "read", "range wraps past 4 GiB"));
        }
        (0..len)
            .map(|i| self.read(address + i, 1).map(|byte| byte as u8))
            .collect()
    }

    pub fn fetch(&self, address: u32) -> Result<u16, AccessFault> {
        // Startup later copies .ram_text here; instructions can execute from RAM.
        self.read_as(address, 2, "fetch").map(|value| value as u16)
    }

    pub fn write(&mut self, address: u32, value: u32, size: usize) -> Result<(), AccessFault> {
        Self::check(address, size, "write")?;
        if let Some(offset) = Self::offset(address, size, RAM, self.ram.len()) {
            self.guards
                .check_write(address, size)
                .map_err(|reason| Self::fault(address, size, "write", reason))?;
            self.ram[offset..offset + size].copy_from_slice(&value.to_le_bytes()[..size]);
            return Ok(());
        }
        if let Some(result) = self.devices.write_uart(address, value, size, &self.ram) {
            return result.map_err(|reason| Self::fault(address, size, "write", reason));
        }
        if self.shift_spi.read(address & !3).is_some() {
            if size != 4 {
                return Err(Self::fault(
                    address,
                    size,
                    "write",
                    "SPI registers require word accesses",
                ));
            }
            if self.shift_spi.write(address, value) {
                let single = [value as u8];
                let bytes = if address == crate::shift_spi::BASE + 8 {
                    &single[..]
                } else {
                    let source = self.shift_spi.dma_address();
                    let length = self.shift_spi.dma_length();
                    let offset =
                        Self::offset(source, length, RAM, self.ram.len()).ok_or_else(|| {
                            Self::fault(
                                source,
                                length,
                                "DMA read",
                                "matrix SPI DMA source must be in SRAM",
                            )
                        })?;
                    &self.ram[offset..offset + length]
                };
                let iomap = self.lcd.read(crate::lcd::IOMAP).unwrap();
                self.shift_spi
                    .start(bytes, iomap)
                    .map_err(|reason| Self::fault(address, size, "write", reason))?;
            }
            return Ok(());
        }
        if let Some(result) = self.wireless.write(address, value, size) {
            return result.map_err(|reason| Self::fault(address, size, "write", reason));
        }
        if self.clock.write(address, value).is_some() {
            return Ok(());
        }
        if self.crc.write(address, value).is_some() {
            return Ok(());
        }
        if let Some(result) = self.audio.write(address, value) {
            return result.map_err(|reason| Self::fault(address, size, "write", reason));
        }
        if let Some(result) = self.usb.write(address, value, &mut self.ram) {
            return result.map_err(|reason| Self::fault(address, size, "write", reason));
        }
        if address == 0x500c0 {
            self.nor.chip_select(value & 1 == 0);
        }
        if let Some(result) = self.nor.write(address, value) {
            return result.map_err(|reason| Self::fault(address, size, "write", reason));
        }
        self.guards
            .check_write(address, size)
            .map_err(|reason| Self::fault(address, size, "write", reason))?;
        if self.cache.write(address, size, value).is_some() {
            return Ok(());
        }
        if let Some(result) = self.guards.write(address, value) {
            return result.map_err(|reason| Self::fault(address, size, "write", reason));
        }
        if let Some(result) = self.system.write(address, value) {
            self.devices.adc.select_pmu(self.system.adc_pmu_selection());
            return result.map_err(|reason| Self::fault(address, size, "write", reason));
        }
        if self.lcd.read(address & !3).is_some() {
            if size != 4 {
                return Err(Self::fault(
                    address,
                    size,
                    "write",
                    "SPI registers require word accesses",
                ));
            }
            let bytes = if address == SPI + 16 {
                let source = self.lcd.dma_address();
                let length = value as usize;
                if let Some(offset) = Self::offset(source, length, RAM, self.ram.len()) {
                    self.ram[offset..offset + length].to_vec()
                } else if Self::offset(source, length, XIP, self.flash.len()).is_some() {
                    // Stock LCD initialization sends constant data from flash.
                    // Read bytes through XIP so packaged encryption/mapping,
                    // flash modifications and disabled-XIP faults still apply.
                    (0..length)
                        .map(|i| {
                            self.read_as(source + i as u32, 1, "DMA read")
                                .map(|v| v as u8)
                        })
                        .collect::<Result<Vec<_>, _>>()?
                } else {
                    return Err(Self::fault(
                        source,
                        length,
                        "DMA read",
                        "LCD DMA source is outside SRAM and application XIP",
                    ));
                }
            } else {
                Vec::new()
            };
            let pc_out = self.devices.gpio.read(0x50080).unwrap();
            let pc_dir = self.devices.gpio.read(0x50088).unwrap();
            let selected = pc_dir & 0x180 == 0 && pc_out & 0x80 == 0;
            return self
                .lcd
                .write(address, value, selected, pc_out & 0x100 != 0, &bytes)
                .map_err(|reason| Self::fault(address, size, "write", reason));
        }
        if let Some(result) = self.devices.write(address, value, size) {
            return result.map_err(|reason| Self::fault(address, size, "write", reason));
        }
        let offset = Self::offset(address, size, RAM, self.ram.len()).ok_or_else(|| {
            Self::fault(
                address,
                size,
                "write",
                "unmapped memory, read-only XIP, or unimplemented MMIO",
            )
        })?;
        self.ram[offset..offset + size].copy_from_slice(&value.to_le_bytes()[..size]);
        Ok(())
    }

    pub(crate) fn advance_nor(&mut self, ticks: u32) {
        self.nor.advance(ticks);
    }

    pub(crate) fn advance_wireless(&mut self, ticks: u32) {
        self.wireless.advance(ticks);
    }
    pub(crate) fn advance_devices(&mut self, ticks: u32) {
        let clk_con3 = self.audio.read(0x10014).unwrap();
        let peripheral_hz = self.clock.timer_hz(clk_con3);
        let core_hz = self.clock.system_hz(clk_con3);
        self.devices
            .advance_with_clocks(ticks, peripheral_hz, core_hz);
        // spec_uart.c: CLK_CON2[11:10] selects OSC, PLL48M, or LSB.
        let uart_hz = match (self.usb.read(0x10010).unwrap() >> 10) & 3 {
            0 => 24_000_000,
            1 => 48_000_000,
            2 => peripheral_hz,
            _ => 0,
        };
        self.devices.advance_uart(ticks, uart_hz);
        if let Some(bytes) = self.shift_spi.advance(ticks, peripheral_hz) {
            self.devices.gpio.shift_spi(&bytes);
        }
    }
    pub(crate) fn instruction_ticks(&mut self) -> u32 {
        self.clock
            .instruction_ticks(self.audio.read(0x10014).unwrap())
    }

    pub fn advance_usb(&mut self, ticks: u32) -> Result<(), AccessFault> {
        self.usb
            .advance(ticks, &mut self.ram)
            .map_err(|reason| Self::fault(0x11800, 4, "USB host", reason))
    }

    pub fn screen_visible(&self) -> bool {
        self.lcd.display_on
            && !self.lcd.sleeping
            && self.devices.gpio.read(0x50000).unwrap() & 4 == 0
            && self.devices.gpio.read(0x50008).unwrap() & 4 == 0
    }

    pub fn advance_audio(&mut self, ticks: u32) -> Result<(), AccessFault> {
        self.audio
            .advance(ticks, &self.ram)
            .map_err(|reason| Self::fault(0x12e1c, 4, "audio DMA", reason))
    }

    pub fn pending_irq(&self, icfg: u32) -> Option<usize> {
        self.pending_irq_for(icfg, 0)
    }
    pub(crate) fn pending_irq_for(&self, icfg: u32, core: usize) -> Option<usize> {
        let sources = self.devices.pending_sources(core)
            | ((self.lcd.pending_irq() as u128) << crate::lcd::IRQ)
            | ((self.audio.pending_irq() as u128) << crate::audio::IRQ)
            | ((self.shift_spi.pending_irq() as u128) << crate::shift_spi::IRQ)
            | ((self.wireless.clock_pending_irq() as u128) << 40)
            | ((self.wireless.slot_pending_irq() as u128) << 41);
        self.devices.select_irq(sources, icfg, core)
    }
}
