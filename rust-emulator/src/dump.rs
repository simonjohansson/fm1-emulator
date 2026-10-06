// SPDX-License-Identifier: GPL-3.0-only
// `--dump ADDRESS:BYTES:FILE`: raw guest memory written after a run.
use crate::bus::Bus;
use std::{collections::BTreeMap, fs, path::PathBuf};

#[derive(Debug, PartialEq, Eq)]
pub struct Dump {
    pub address: u32,
    pub len: u32,
    pub path: PathBuf,
}

fn number(value: &str) -> Result<u32, String> {
    if let Some(hex) = value.strip_prefix("0x") {
        u32::from_str_radix(hex, 16)
    } else {
        value.parse()
    }
    .map_err(|_| format!("invalid number: {value}"))
}

impl Dump {
    /// ADDRESS is a symbol or number; FILE may itself contain colons.
    pub fn parse(spec: &str, symbols: &BTreeMap<String, u32>) -> Result<Self, String> {
        let mut parts = spec.splitn(3, ':');
        let (Some(address), Some(len), Some(path)) = (parts.next(), parts.next(), parts.next())
        else {
            return Err("--dump requires ADDRESS:BYTES:FILE".into());
        };
        if path.is_empty() {
            return Err("--dump requires ADDRESS:BYTES:FILE".into());
        }
        Ok(Self {
            address: symbols
                .get(address)
                .copied()
                .map_or_else(|| number(address), Ok)?,
            len: number(len)?,
            path: path.into(),
        })
    }

    /// Reads the whole range first, so a bad range leaves no partial file.
    pub fn write(&self, bus: &Bus) -> Result<(), String> {
        let bytes = bus
            .read_bytes(self.address, self.len)
            .map_err(|error| format!("dump {}: {error}", self.path.display()))?;
        fs::write(&self.path, bytes)
            .map_err(|error| format!("dump {}: {error}", self.path.display()))
    }
}
