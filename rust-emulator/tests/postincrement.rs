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

#[test]
fn halfword_postincrement_uses_the_signed_ten_bit_step() {
    // imm10m2: h bits 1:0 are step bits 9:8, x bit 0 store, h bit 2 signed load.
    for (h, x, step, store, sign) in [
        (0xedd0, 0x30b3, 2i32, true, false), // h[r11++=2] = r3
        (0xedd0, 0x3fbf, 254, true, false),  // h[r11++=254] = r3
        (0xedd1, 0x30b1, 256, true, false),  // h[r11++=256] = r3
        (0xedd1, 0x3fbf, 510, true, false),  // h[r11++=510] = r3
        (0xedd3, 0x3fbf, -2, true, false),   // h[r11++=-2] = r3
        (0xedd3, 0x30b1, -256, true, false), // h[r11++=-256] = r3
        (0xedd2, 0x3fbf, -258, true, false), // h[r11++=-258] = r3
        (0xedd2, 0x30b1, -512, true, false), // h[r11++=-512] = r3
        (0xedd3, 0x3fbe, -2, false, false),  // r3 = h[r11++=-2] (u)
        (0xedd7, 0x3fbe, -2, false, true),   // r3 = h[r11++=-2] (s)
        (0xedd5, 0x30b0, 256, false, true),  // r3 = h[r11++=256] (s)
    ] {
        let mut c = cpu(&[h, x]);
        let base = RAM + 0x400;
        c.r[11] = base;
        c.r[3] = if store { 0x1234_8765 } else { 0 };
        c.bus.write(base, 0x5555_8001, 4).unwrap();
        c.step().unwrap();
        assert_eq!(c.r[11], base.wrapping_add(step as u32), "{h:04x} {x:04x}");
        if store {
            assert_eq!(c.bus.read(base, 4).unwrap(), 0x5555_8765, "{h:04x} {x:04x}");
        } else {
            let expected = if sign { 0xffff_8001 } else { 0x8001 };
            assert_eq!(c.r[3], expected, "{h:04x} {x:04x}");
        }
    }
}

#[test]
fn halfword_register_preincrement_stores_then_updates_the_base() {
    // h[++r2=r11] = r0 at 0x02000808 (gfx.c ramp), Felucca 1.0 / 1.0.1.
    let mut c = cpu(&[0xeddc, 0x0b21]);
    c.r[0] = 0x1234_abcd;
    c.r[2] = RAM + 0x400;
    c.r[11] = 6;
    c.bus.write(RAM + 0x404, 0x5555_5555, 4).unwrap();
    c.bus.write(RAM + 0x408, 0x7777_7777, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.r[2], RAM + 0x406);
    assert_eq!(c.bus.read(RAM + 0x404, 4).unwrap(), 0xabcd_5555);
    assert_eq!(c.bus.read(RAM + 0x408, 4).unwrap(), 0x7777_7777);
}

#[test]
fn halfword_register_preincrement_store_of_its_base_writes_the_old_base() {
    // h[++r2=r3] = r2: the stored value is the base before the update.
    let mut c = cpu(&[0xeddc, 0x2321]);
    let base = RAM + 0x400;
    c.r[2] = base;
    c.r[3] = 2;
    c.step().unwrap();
    assert_eq!(c.r[2], base + 2);
    assert_eq!(c.bus.read(base + 2, 2).unwrap(), base & 0xffff);
}

#[test]
fn halfword_register_preincrement_loads_still_decode() {
    for (x, expected) in [(0x3b20, 0x8001), (0x3b22, 0xffff_8001)] {
        // r3 = h[++r2=r11] (u) / (s)
        let mut c = cpu(&[0xeddc, x]);
        c.r[2] = RAM + 0x400;
        c.r[11] = 4;
        c.bus.write(RAM + 0x404, 0x8001, 2).unwrap();
        c.step().unwrap();
        assert_eq!(c.r[3], expected);
        assert_eq!(c.r[2], RAM + 0x404);
    }
}

#[test]
fn bit_field_extract_sign_extends_sextra_only() {
    // Vendor assembler words: x bit 0 selects sextra (signed) over uextra.
    for (h, x, src, input, expected) in [
        (0xe1b0, 0x0745, 0, -132_210i32, -9i32), // r0 = sextra(r0, p:14, l:17) (eng_analog mulq15)
        (0xe1b0, 0x0744, 0, -132_210, 131_063),  // r0 = uextra(r0, p:14, l:17)
        (0xe1b0, 0xb021, 11, 0x80, -128),        // r0 = sextra(r11, p:0, l:8)
        (0xe1b0, 0xb020, 11, 0x80, 128),         // r0 = uextra(r11, p:0, l:8)
        (0xe1b5, 0x3f85, 3, i32::MIN, -1),       // r5 = sextra(r3, p:31, l:1)
        (0xe1b5, 0x3f84, 3, i32::MIN, 1),        // r5 = uextra(r3, p:31, l:1)
        (0xe1b5, 0x30fd, 3, -2, -1),             // r5 = sextra(r3, p:1, l:31)
        (0xe1b0, 0x0745, 0, 0x3fff_c000, 65535), // positive field: sextra leaves it as is
    ] {
        let mut c = cpu(&[h, x]);
        c.r[src] = input as u32;
        c.step().unwrap();
        assert_eq!(c.r[(h & 15) as usize] as i32, expected, "{h:04x} {x:04x}");
    }
}
