// SPDX-License-Identifier: GPL-3.0-only
use crate::{XIP, XIP_END};
use std::{collections::BTreeMap, fs, path::Path};

pub struct Firmware {
    pub image: Vec<u8>,
    pub entry: u32,
    pub symbols: BTreeMap<String, u32>,
    package: Option<crate::package::Package>,
}

fn bytes(data: &[u8], offset: usize, length: usize) -> Result<&[u8], String> {
    let end = offset.checked_add(length).ok_or("ELF offset overflow")?;
    data.get(offset..end).ok_or_else(|| "truncated ELF".into())
}

fn u16_at(data: &[u8], offset: usize) -> Result<u16, String> {
    Ok(u16::from_le_bytes(
        bytes(data, offset, 2)?.try_into().unwrap(),
    ))
}

fn u32_at(data: &[u8], offset: usize) -> Result<u32, String> {
    Ok(u32::from_le_bytes(
        bytes(data, offset, 4)?.try_into().unwrap(),
    ))
}

impl Firmware {
    pub fn load(path: &Path) -> Result<Self, String> {
        if path
            .extension()
            .is_some_and(|extension| extension == "fwsc")
        {
            let raw = fs::read(path).map_err(|error| format!("{}: {error}", path.display()))?;
            let (package, image) = crate::package::Package::decode(&raw)?;
            let mut firmware = Self::from_raw(image)?;
            firmware.package = Some(package);
            return Ok(firmware);
        }
        let data = fs::read(path).map_err(|error| format!("{}: {error}", path.display()))?;
        if data.starts_with(b"\x7fELF") {
            Self::from_elf(&data)
        } else if path.extension().is_some_and(|extension| extension == "elf") {
            Err("invalid ELF magic".into())
        } else {
            Self::from_raw(data)
        }
    }

    pub fn from_raw(image: Vec<u8>) -> Result<Self, String> {
        if image.is_empty() || image.len() > (XIP_END - XIP) as usize {
            return Err("application image is empty or exceeds the XIP window".into());
        }
        Ok(Self {
            image,
            entry: XIP,
            symbols: BTreeMap::new(),
            package: None,
        })
    }

    pub fn from_elf(data: &[u8]) -> Result<Self, String> {
        let header = bytes(data, 0, 52)?;
        if &header[..4] != b"\x7fELF"
            || header[4..7] != [1, 1, 1]
            || u16_at(data, 16)? != 2
            || u16_at(data, 18)? != 241
            || u32_at(data, 20)? != 1
            || u16_at(data, 40)? != 52
        {
            return Err("expected an executable, little-endian ELF32-pi32v2 image".into());
        }
        let entry = u32_at(data, 24)?;
        let phoff = u32_at(data, 28)? as usize;
        let phsize = u16_at(data, 42)? as usize;
        let phcount = u16_at(data, 44)? as usize;
        if phsize != 32 || phcount == 0 {
            return Err("invalid ELF program headers".into());
        }
        let mut image = Vec::new();
        let mut ranges = Vec::new();
        for index in 0..phcount {
            let ph = bytes(data, phoff + index * phsize, phsize)?;
            if u32_at(ph, 0)? != 1 {
                continue;
            }
            let offset = u32_at(ph, 4)? as usize;
            let physical = u32_at(ph, 12)?;
            let size = u32_at(ph, 16)? as usize;
            if size > u32_at(ph, 20)? as usize {
                return Err("ELF segment file size exceeds memory size".into());
            }
            // Build the exact on-flash application. .ram_text and .data reside
            // at their physical load addresses; crt0 must perform the RAM copies.
            // Ignore the ELF-header RAM segment and file-empty .bss/.noinit.
            let physical_end = physical
                .checked_add(size as u32)
                .ok_or("ELF physical address overflow")?;
            if size == 0 || physical_end <= XIP || physical >= XIP_END {
                continue;
            }
            let clipped_start = physical.max(XIP);
            let skip = (clipped_start - physical) as usize;
            let start = (clipped_start - XIP) as usize;
            let end = start
                .checked_add(size - skip)
                .ok_or("ELF segment size overflow")?;
            if end > (XIP_END - XIP) as usize {
                return Err("ELF segment exceeds the XIP window".into());
            }
            if ranges.iter().any(|&(a, b)| start < b && a < end) {
                return Err("overlapping ELF flash segments".into());
            }
            let source = &bytes(data, offset, size)?[skip..];
            image.resize(image.len().max(end), 0xff);
            image[start..end].copy_from_slice(source);
            ranges.push((start, end));
        }
        if image.is_empty()
            || entry % 2 != 0
            || !ranges.iter().any(|&(a, b)| {
                entry >= XIP && ((entry - XIP) as usize) >= a && ((entry - XIP) as usize) < b
            })
        {
            return Err("ELF entry is outside the loaded application".into());
        }
        let shoff = u32_at(data, 32)? as usize;
        let shsize = u16_at(data, 46)? as usize;
        let shcount = u16_at(data, 48)? as usize;
        let mut symbols = BTreeMap::new();
        if shcount > 0 && shsize != 40 {
            return Err("invalid ELF section headers".into());
        }
        for index in 0..shcount {
            let sh = bytes(data, shoff + index * shsize, shsize)?;
            if u32_at(sh, 4)? != 2 {
                continue;
            }
            let stride = u32_at(sh, 36)? as usize;
            let size = u32_at(sh, 20)? as usize;
            let link = u32_at(sh, 24)? as usize;
            if stride != 16 || !size.is_multiple_of(stride) || link >= shcount {
                return Err("invalid ELF symbol table".into());
            }
            let table = bytes(data, u32_at(sh, 16)? as usize, size)?;
            let str_sh = bytes(data, shoff + link * shsize, shsize)?;
            if u32_at(str_sh, 4)? != 3 {
                return Err("invalid ELF string table".into());
            }
            let strings = bytes(
                data,
                u32_at(str_sh, 16)? as usize,
                u32_at(str_sh, 20)? as usize,
            )?;
            for sym in table.chunks_exact(stride) {
                if u16_at(sym, 14)? == 0 {
                    continue;
                }
                let name = strings
                    .get(u32_at(sym, 0)? as usize..)
                    .ok_or("invalid ELF symbol name")?;
                let end = name
                    .iter()
                    .position(|&byte| byte == 0)
                    .ok_or("unterminated ELF symbol name")?;
                let name = std::str::from_utf8(&name[..end])
                    .map_err(|_| "invalid UTF-8 ELF symbol name")?;
                if !name.is_empty() {
                    symbols.insert(name.to_owned(), u32_at(sym, 4)?);
                }
            }
        }
        Ok(Self {
            image,
            entry,
            symbols,
            package: None,
        })
    }

    pub fn bus(&self) -> Result<crate::bus::Bus, String> {
        self.bus_over(None)
    }

    /// Like `bus`, but the NOR starts as `image` (a raw 1 MiB dump) and the
    /// package is installed over it, as a firmware install would.
    pub fn bus_with_flash(&self, image: &[u8]) -> Result<crate::bus::Bus, String> {
        if self.package.is_none() {
            return Err("a flash image needs a .fwsc package to install over it".into());
        }
        self.bus_over(Some(image))
    }

    fn bus_over(&self, flash: Option<&[u8]>) -> Result<crate::bus::Bus, String> {
        let mut bus = crate::bus::Bus::new(self.image.clone())?;
        if let Some(flash) = flash {
            bus.set_flash_image(flash)?;
        }
        if let Some(package) = &self.package {
            package.initialize(&mut bus)?;
        }
        Ok(bus)
    }
}
