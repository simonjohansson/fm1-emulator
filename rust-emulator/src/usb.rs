// SPDX-License-Identifier: GPL-3.0-only
// USB0 register bridge and a small USB host for the CDC console.
use crate::RAM;
use std::collections::VecDeque;
#[derive(Default)]
pub struct Usb {
    regs: [u32; 16],
    io: u32,
    clock: u32,
    sie: [u8; 16],
    endpoints: [[u8; 8]; 5],
    index: usize,
    ticks: u64,
    deadline: u64,
    attached: bool,
    phase: usize,
    waiting: bool,
    response: Vec<u8>,
    cdc_interface: Option<u8>,
    cdc_endpoint: Option<usize>,
    cdc_out: Option<(usize, usize)>,
    input: VecDeque<u8>,
    pub serial: VecDeque<u8>,
    pub setups: u64,
    pub packets: u64,
    pub iso_packets: u64,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn configured() -> (Usb, Vec<u8>) {
        let mut usb = Usb::default();
        usb.io = 0x40;
        usb.regs[0] = 4;
        usb.attached = true;
        usb.phase = 2;
        // MIDI OUT 1 must be ignored. CDC OUT 2 and IN 3 are deliberately
        // distinct so neither direction can be guessed from the other.
        usb.response = vec![
            9, 4, 1, 0, 1, 1, 3, 0, 0, 7, 5, 1, 2, 64, 0, 0, 9, 4, 2, 0, 0, 2, 2, 1, 0, 9, 4, 3, 0,
            2, 10, 0, 0, 0, 7, 5, 2, 2, 32, 0, 0, 7, 5, 0x83, 2, 64, 0, 0,
        ];
        usb.complete();
        usb.phase = 5;
        usb.regs[10] = RAM + 64; // EP2 RX DMA.
        usb.endpoints[2][3] = 0xff;
        (usb, vec![0; 256])
    }
    fn sie_write(usb: &mut Usb, ram: &mut [u8], register: u32, value: u32) {
        usb.write(0x11804, register << 8 | value, ram)
            .unwrap()
            .unwrap();
    }
    fn sie_read(usb: &mut Usb, ram: &mut [u8], register: u32) -> u32 {
        usb.write(0x11804, register << 8 | 0x4000, ram)
            .unwrap()
            .unwrap();
        usb.read(0x11804).unwrap() & 255
    }

    #[test]
    fn cdc_out_discovers_its_endpoint_and_preserves_packets_until_guest_ack() {
        let (mut usb, mut ram) = configured();
        let bytes: Vec<_> = (0..70).collect();
        assert!(usb.receive_serial(&bytes));
        usb.advance(1, &mut ram).unwrap();
        assert_eq!(&ram[64..96], &bytes[..32]);
        assert_eq!(sie_read(&mut usb, &mut ram, 4), 1 << 2);
        assert_eq!(sie_read(&mut usb, &mut ram, 4), 0); // IRQ read acknowledges latch.
        sie_write(&mut usb, &mut ram, 14, 2);
        assert_eq!(sie_read(&mut usb, &mut ram, 22), 32);
        assert_eq!(sie_read(&mut usb, &mut ram, 20) & 1, 1);
        usb.advance(24_000, &mut ram).unwrap();
        assert_eq!(&ram[64..96], &bytes[..32]); // NAK: unread packet stays intact.
        assert_eq!(usb.input.len(), 38);
        sie_write(&mut usb, &mut ram, 20, 0x11); // Guest RX FIFO flush/ack.
        usb.advance(1, &mut ram).unwrap();
        assert_eq!(&ram[64..96], &bytes[32..64]);
        assert_eq!(sie_read(&mut usb, &mut ram, 4), 1 << 2);
        sie_write(&mut usb, &mut ram, 20, 0); // Clear RXPKTRDY without flush.
        usb.advance(1, &mut ram).unwrap();
        assert_eq!(&ram[64..70], &bytes[64..]);
        assert_eq!(sie_read(&mut usb, &mut ram, 22), 6);
        assert!(usb.input.is_empty());
    }

    #[test]
    fn cdc_out_waits_for_configuration_enable_and_valid_dma() {
        let (mut usb, mut ram) = configured();
        assert!(usb.receive_serial(b"help\n"));
        usb.phase = 4;
        usb.waiting = true;
        usb.advance(1, &mut ram).unwrap();
        assert_eq!(&ram[64..69], &[0; 5]);
        usb.phase = 5;
        usb.regs[0] |= 1 << 21; // EP2 disabled.
        usb.advance(1, &mut ram).unwrap();
        assert_eq!(usb.input.len(), 5);
        usb.regs[0] &= !(1 << 21);
        usb.regs[10] = RAM + 255;
        assert_eq!(usb.advance(1, &mut ram), Err("USB DMA exceeds SRAM"));
        assert_eq!(usb.input.len(), 5); // Fault must not lose host bytes.
        usb.regs[10] = RAM + 64;
        usb.advance(1, &mut ram).unwrap();
        assert_eq!(&ram[64..69], b"help\n");
    }

    #[test]
    fn isochronous_ep4_uses_its_own_count_and_dma_registers() {
        let (mut usb, mut ram) = configured();
        usb.regs[13] = 184; // EP4 count: 46 stereo frames, Felucca's largest packet.
        usb.regs[14] = RAM + 64; // EP4 TX DMA.
        usb.regs[2 + 3] = 999; // EP3's count must not be used.
        sie_write(&mut usb, &mut ram, 14, 4);
        sie_write(&mut usb, &mut ram, 17, 1); // TXCSR1: TxPktRdy.
        assert_eq!(usb.iso_packets, 1);
        assert_eq!(sie_read(&mut usb, &mut ram, 2), 1 << 4);
        assert_eq!(sie_read(&mut usb, &mut ram, 17) & 1, 0);
        assert!(usb.serial.is_empty());
        usb.regs[13] = 1024;
        assert_eq!(
            usb.write(0x11804, 17 << 8 | 1, &mut ram),
            Some(Err("USB full-speed isochronous packet exceeds 1023 bytes"))
        );
        assert_eq!(
            usb.write(0x11804, 14 << 8 | 5, &mut ram),
            Some(Err("USB endpoint index exceeds modeled controller"))
        );
    }

    #[test]
    fn host_input_is_bounded_and_failed_enqueue_keeps_existing_bytes() {
        let (mut usb, mut ram) = configured();
        assert!(usb.receive_serial(&vec![42; 4096]));
        assert!(!usb.receive_serial(b"discard nothing"));
        assert_eq!(usb.input.len(), 4096);
        usb.advance(1, &mut ram).unwrap();
        assert!(usb.receive_serial(&[7; 32]));
        assert!(!usb.receive_serial(b"x"));
        assert_eq!(usb.input.back(), Some(&7));
    }
}
impl Usb {
    /// Queue terminal bytes for the guest's CDC bulk OUT endpoint. Backpressure
    /// keeps host input bounded while the guest is paused or its RX FIFO is full.
    pub fn receive_serial(&mut self, bytes: &[u8]) -> bool {
        if bytes.len() > 4096 - self.input.len() {
            return false;
        }
        self.input.extend(bytes.iter().copied());
        true
    }
    pub fn read(&self, a: u32) -> Option<u32> {
        match a {
            0x51000 => Some(self.io),
            // FM-1_997: CON1=0 at SPL handoff (CON0=0xe0c), then
            // CON1=2 with the full-speed D+ pull-up/input enabled (0x164c).
            // Model the idle wire level; individual bus symbols are absent.
            0x51004 => Some(if self.io & 0x1040 == 0x1040 { 2 } else { 0 }),
            0x10010 => Some(self.clock),
            // SDK H0_SIE_CON: stock startup disables the unused high-speed port.
            0x16800 => Some(0),
            0x11800..=0x1183c if a.is_multiple_of(4) => {
                Some(self.regs[((a - 0x11800) / 4) as usize])
            }
            _ => None,
        }
    }
    fn dma(ram: &mut [u8], a: u32, n: usize) -> Result<&mut [u8], &'static str> {
        let start = a.checked_sub(RAM).ok_or("USB DMA must address SRAM")? as usize;
        ram.get_mut(start..start + n).ok_or("USB DMA exceeds SRAM")
    }
    fn complete(&mut self) {
        if self.phase == 2 {
            let mut i = 0;
            let mut cdc_data = false;
            while i + 2 <= self.response.len() {
                let n = self.response[i] as usize;
                if n < 2 || i + n > self.response.len() {
                    break;
                }
                let d = &self.response[i..i + n];
                if d[1] == 4 && n >= 9 {
                    if d[5] == 2 {
                        self.cdc_interface = Some(d[2]);
                    }
                    cdc_data = d[5] == 10;
                }
                if d[1] == 5 && n >= 7 && cdc_data && d[3] & 3 == 2 {
                    let endpoint = (d[2] & 15) as usize;
                    if d[2] & 128 != 0 {
                        self.cdc_endpoint = Some(endpoint);
                    } else {
                        self.cdc_out = Some((endpoint, u16::from_le_bytes([d[4], d[5]]) as usize));
                    }
                }
                i += n;
            }
        }
        self.phase += 1;
        self.waiting = false;
        self.deadline = self.ticks + 24000;
    }
    fn send(&mut self, ep: usize, ram: &mut [u8]) -> Result<(), &'static str> {
        // EP4 has its own count and DMA address registers (0x11834 / 0x11838;
        // SDK usb_write_ep_cnt / usb_set_dma_taddr, ep 4).
        let (n, a) = match ep {
            0 => (self.regs[2], self.regs[6]),
            4 => (self.regs[13], self.regs[14]),
            _ => (self.regs[2 + ep], self.regs[7 + (ep - 1) * 2]),
        };
        let n = n as usize;
        if ep == 4 && n > 1023 {
            return Err("USB full-speed isochronous packet exceeds 1023 bytes");
        }
        if ep != 4 && n > 64 {
            return Err("USB full-speed packet exceeds 64 bytes");
        }
        let bytes = Self::dma(ram, a, n)?;
        if ep == 4 {
            // The host takes each isochronous packet at once and has no
            // audio sink; frame pacing is not modeled.
            self.iso_packets += 1;
        } else if ep == 0 {
            self.response.extend_from_slice(bytes);
        } else if Some(ep) == self.cdc_endpoint {
            self.serial.extend(bytes.iter().copied());
            self.packets += 1;
        }
        self.sie[2] |= 1 << ep;
        Ok(())
    }
    pub fn write(&mut self, a: u32, v: u32, ram: &mut [u8]) -> Option<Result<(), &'static str>> {
        self.read(a)?;
        Some(self.write_inner(a, v, ram))
    }
    fn write_inner(&mut self, a: u32, v: u32, ram: &mut [u8]) -> Result<(), &'static str> {
        match a {
            0x16800 => {
                if v != 0 {
                    return Err("high-speed USB controller is not implemented");
                }
            }
            0x51000 => self.io = v,
            0x51004 => return Err("USB I/O input status is read-only in the supported model"),
            0x10010 => self.clock = v,
            0x11800 => {
                self.regs[0] = v & !0x1000;
                if v & 0x1000 != 0 {
                    self.regs[0] &= !0x2000;
                }
                if v & 4 == 0 {
                    self.attached = false;
                    self.phase = 0;
                    self.waiting = false;
                    self.sie = [0; 16];
                    self.endpoints = [[0; 8]; 5];
                    self.cdc_interface = None;
                    self.cdc_endpoint = None;
                    self.cdc_out = None;
                }
            }
            0x11804 => {
                if self.regs[0] & 4 == 0 {
                    self.regs[1] = v;
                    return Ok(());
                }
                let r = ((v >> 8) & 0x3f) as usize;
                if r > 23 {
                    return Err("unimplemented USB SIE register");
                }
                let data = if v & 0x4000 != 0 {
                    let data = if r < 16 {
                        self.sie[r]
                    } else {
                        self.endpoints[self.index][r - 16]
                    };
                    if (2..=6).contains(&r) {
                        self.sie[r] = 0;
                    }
                    data
                } else {
                    let data = v as u8;
                    if r == 14 {
                        // EP4 is the isochronous endpoint (Felucca 1.0 USB audio).
                        if data > 4 {
                            return Err("USB endpoint index exceeds modeled controller");
                        }
                        self.index = data as usize;
                        self.sie[r] = data;
                    } else if r < 16 {
                        self.sie[r] = data;
                    } else if r == 17 {
                        if self.index == 0 {
                            if data & 0x20 != 0 {
                                return Err("guest stalled host USB control request");
                            }
                            if data & 0x40 != 0 {
                                self.endpoints[0][1] &= !1;
                            }
                            if data & 2 != 0 {
                                self.send(0, ram)?;
                            }
                            if data & 8 != 0 && self.waiting {
                                self.complete();
                            }
                        } else {
                            self.endpoints[self.index][1] = data & !0xc9;
                            if data & 1 != 0 {
                                self.send(self.index, ram)?;
                            }
                        }
                    } else if r == 20 && self.index != 0 {
                        // RXCSR: flush FIFO and clear data toggle are commands.
                        // A held RXPKTRDY packet must survive polling until the
                        // guest clears it or explicitly flushes the FIFO.
                        self.endpoints[self.index][4] = data & !0x90;
                        if data & 0x10 != 0 || data & 1 == 0 {
                            self.endpoints[self.index][4] &= !1;
                            self.endpoints[self.index][6] = 0;
                            self.endpoints[self.index][7] = 0;
                        }
                    } else {
                        self.endpoints[self.index][r - 16] = data;
                    }
                    data
                };
                self.regs[1] = 0x8000 | data as u32;
            }
            _ => self.regs[((a - 0x11800) / 4) as usize] = v,
        }
        Ok(())
    }
    pub fn advance(&mut self, ticks: u32, ram: &mut [u8]) -> Result<(), &'static str> {
        self.ticks += ticks as u64;
        if self.regs[0] & 4 == 0 || self.io & 0x40 == 0 {
            return Ok(());
        }
        let frame = (self.ticks / 24000) as u16 & 2047;
        self.sie[12] = frame as u8;
        self.sie[13] = (frame >> 8) as u8;
        if self.ticks % 24000 < ticks as u64 {
            self.regs[0] |= 0x2000;
        }
        if !self.attached {
            self.attached = true;
            self.sie[6] |= 4;
            self.deadline = self.ticks + 120000;
        }
        if self.phase >= 5 && !self.input.is_empty() {
            self.receive(ram)?;
        }
        if self.waiting || self.ticks < self.deadline || self.phase >= 5 {
            return Ok(());
        }
        let setup = match self.phase {
            0 => [0x80, 6, 0, 1, 0, 0, 18, 0],
            1 => [0, 5, 1, 0, 0, 0, 0, 0],
            2 => [0x80, 6, 0, 2, 0, 0, 255, 0],
            3 => [0, 9, 1, 0, 0, 0, 0, 0],
            _ => [
                0x21,
                0x22,
                1,
                0,
                self.cdc_interface
                    .ok_or("USB configuration has no CDC interface")?,
                0,
                0,
                0,
            ],
        };
        Self::dma(ram, self.regs[6], 8)?.copy_from_slice(&setup);
        self.response.clear();
        self.endpoints[0][1] = 1;
        self.endpoints[0][6] = 8;
        self.sie[2] |= 1;
        self.waiting = true;
        self.setups += 1;
        Ok(())
    }

    fn receive(&mut self, ram: &mut [u8]) -> Result<(), &'static str> {
        let Some((ep, packet_size)) = self.cdc_out else {
            return Ok(());
        };
        if !(1..=3).contains(&ep) || !(1..=64).contains(&packet_size) {
            return Err("CDC OUT endpoint exceeds modeled full-speed controller");
        }
        if self.regs[0] & (1 << (19 + ep)) != 0
            || self.endpoints[ep][3] == 0
            || self.endpoints[ep][4] & 0x21 != 0
        {
            return Ok(()); // Endpoint disabled, stalled, or still holding a packet (NAK).
        }
        let n = self.input.len().min(packet_size);
        let destination = Self::dma(ram, self.regs[8 + (ep - 1) * 2], n)?;
        for (byte, &input) in destination.iter_mut().zip(self.input.iter()) {
            *byte = input;
        }
        self.input.drain(..n);
        self.endpoints[ep][4] |= 1;
        self.endpoints[ep][6] = n as u8;
        self.endpoints[ep][7] = 0;
        self.sie[4] |= 1 << ep;
        Ok(())
    }
}
