// SPDX-License-Identifier: GPL-3.0-only
// Post-increment step encodings, checked against the vendor assembler
// (JieLi clang 4.0.1, -target pi32v2): each pair below is its output.
use fm1_emu::{bus::Bus, cpu::Cpu, RAM, XIP};

fn cpu(words: &[u16]) -> Cpu {
    Cpu::new(
        Bus::new(words.iter().flat_map(|w| w.to_le_bytes()).collect()).unwrap(),
        XIP,
    )
}

#[test]
fn byte_postincrement_uses_the_signed_nine_bit_step() {
    // imm9m1: h bit 0 is step bit 8 (the sign), h bit 1 store, h bit 2 signed load.
    for (h, x, step, store, sign) in [
        (0xeed0, 0x30b1, 1i32, false, false), // r3 = b[r11++=1] (u)
        (0xeed0, 0x3fbf, 255, false, false),  // r3 = b[r11++=255] (u)
        (0xeed1, 0x3fbf, -1, false, false),   // r3 = b[r11++=-1] (u)
        (0xeed1, 0x30b0, -256, false, false), // r3 = b[r11++=-256] (u)
        (0xeed5, 0x3fbf, -1, false, true),    // r3 = b[r11++=-1] (s)
        (0xeed2, 0x30b1, 1, true, false),     // b[r11++=1] = r3
        (0xeed3, 0x3fbf, -1, true, false),    // b[r11++=-1] = r3
        (0xeed3, 0x30b0, -256, true, false),  // b[r11++=-256] = r3
    ] {
        let mut c = cpu(&[h, x]);
        let base = RAM + 0x400;
        c.r[11] = base;
        c.r[3] = if store { 0x1234_56a7 } else { 0 };
        c.bus.write(base, 0x5555_5581, 4).unwrap();
        c.step().unwrap();
        assert_eq!(c.r[11], base.wrapping_add(step as u32), "{h:04x} {x:04x}");
        if store {
            assert_eq!(c.bus.read(base, 4).unwrap(), 0x5555_55a7, "{h:04x} {x:04x}");
        } else {
            let expected = if sign { 0xffff_ff81 } else { 0x81 };
            assert_eq!(c.r[3], expected, "{h:04x} {x:04x}");
        }
    }
}

#[test]
fn felucca_draw_graph_byte_load_steps_backwards() {
    // r10 = b[r13++=-21] (u) at 0x0201b8aa, draw_graph, Felucca 1.0.1.
    let mut c = cpu(&[0xeed1, 0xaedb]);
    let base = RAM + 0x400;
    c.r[13] = base;
    c.bus.write(base, 0x0000_00c3, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.r[10], 0xc3);
    assert_eq!(c.r[13], base - 21);
}
