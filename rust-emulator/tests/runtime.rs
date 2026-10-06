// SPDX-License-Identifier: GPL-3.0-only
use fm1_emu::{bus::Bus, cpu::Cpu, RAM, XIP};

fn cpu(words: &[u16]) -> Cpu {
    Cpu::new(
        Bus::new(words.iter().flat_map(|w| w.to_le_bytes()).collect()).unwrap(),
        XIP,
    )
}
#[test]
fn unsigned_conditional_literals_do_not_expand_into_byte_masks() {
    // Vendor r3 disassembly distinguishes ECB0 0208 (literal 520) from
    // ECA0 0F02 (packed 520), and confirms the four literal relations.
    for (opcode, threshold) in [
        (0xecb0, 520),
        (0xecb0, 1099),
        (0xecb0, 4095),
        (0xec30, 4095),
        (0xe930, 4095),
        (0xe9b0, 4095),
        (0xeca0, 520),
    ] {
        for value in [0, threshold - 1, threshold, threshold + 1, u32::MAX] {
            let constant = if opcode == 0xeca0 {
                0x0f02
            } else {
                threshold as u16
            };
            let mut c = cpu(&[opcode, 0x1000 | constant, 0x2b42, 0x3642, 0]);
            c.r[0] = value;
            c.sr[5] = 15;
            for _ in 0..3 {
                c.step().unwrap();
            }
            let selected = match opcode {
                0xec30 => value > threshold,
                0xe930 => value >= threshold,
                0xe9b0 => value < threshold,
                _ => value <= threshold,
            };
            assert_eq!(c.r[2], if selected { 11 } else { 22 });
            assert_eq!(c.r[0], value);
            assert_eq!(c.sr[5], 15);
            assert_eq!(c.pc, XIP + 10);
        }
    }
}

#[test]
fn signed_not_equal_literals_preserve_negative_sentinels() {
    // Vendor r3: E8B0 0B95 is if (r0 != -1131), not a packed bit mask.
    // Stock also uses this form to check SDK returns against -1 and -97.
    for threshold in [-2048i32, -1131, -97, -1, 0, 2047] {
        for value in [threshold - 1, threshold, threshold + 1] {
            let mut c = cpu(&[
                0xe8b0,
                0x1000 | (threshold as u16 & 4095),
                0x2b42,
                0x3642,
                0,
            ]);
            c.r[0] = value as u32;
            c.sr[5] = 15;
            for _ in 0..3 {
                c.step().unwrap();
            }
            assert_eq!(c.r[2], if value != threshold { 11 } else { 22 });
            assert_eq!(c.r[0], value as u32);
            assert_eq!(c.sr[5], 15);
            assert_eq!(c.pc, XIP + 10);
        }
    }
}

#[test]
fn mask_moves_use_the_same_replicated_byte_lanes_as_arithmetic() {
    // Vendor r3: r0=0xff00ff, r1=0xff00ff00, r2=0x01010101.
    let mut c = cpu(&[0xe060, 0x01ff, 0xe060, 0x12ff, 0xe060, 0x2301]);
    c.sr[5] = 15;
    for _ in 0..3 {
        c.step().unwrap();
    }
    assert_eq!(&c.r[..3], &[0x00ff00ff, 0xff00ff00, 0x01010101]);
    assert_eq!(c.sr[5], 15);
}

#[test]
fn replicated_immediates_fill_both_pixels_and_keep_byte_lanes() {
    // Vendor r3 assembly: r0=r2*0x10001, then the other replicated forms.
    for (word, input, expected) in [
        (0x2101, 0x1234, 0x12341234),
        (0x2101, 0xffff, u32::MAX),
        (0x2201, 0x1234, 0x34123400),
        (0x2301, 0x7f, 0x7f7f7f7f),
    ] {
        let mut c = cpu(&[0xe1e0, word]);
        c.r[2] = input;
        c.sr[5] = 15;
        c.step().unwrap();
        assert_eq!(c.r[0], expected);
        assert_eq!(c.r[2], input);
        assert_eq!(c.sr[5], 15);
    }
    for (word, expected) in [
        (0x21ff, 0x00ff00ff),
        (0x22ff, 0xff00ff00),
        (0x2301, 0x01010101),
    ] {
        let mut c = cpu(&[0xe140, word]);
        c.step().unwrap();
        assert_eq!(c.r[0], expected);
    }
}

#[test]
fn usb_line_status_tracks_the_measured_idle_pullup_configuration() {
    let mut c = cpu(&[0x0020]);
    for (control, expected) in [(0xe0c, 0), (0x164c, 2), (0x0c, 0), (0x064c, 0), (0x160c, 0)] {
        c.bus.write(0x51000, control, 4).unwrap();
        assert_eq!(c.bus.read(0x51004, 4).unwrap(), expected);
    }
    assert!(c.bus.write(0x51004, 2, 4).is_err());
    assert_eq!(c.bus.read(0x51004, 1).unwrap(), 0);
    assert!(c.bus.read(0x51005, 1).is_err());
    assert!(c.bus.read(0x51008, 4).is_err()); // Unmodeled secondary USB I/O.
}

#[test]
fn stock_lcd_transfer_dispatches_irq_16_and_returns_after_guest_ack() {
    use fm1_emu::{devices::IRQ_CONFIG, lcd::SPI};
    let mut c = cpu(&[0x0020, 0x0020]);
    c.r[0] = 0x6021;
    c.r[1] = SPI;
    c.sr[14] = RAM + 256;
    c.sr[13] = RAM + 512;
    c.sr[11] = 0x100;
    c.interrupts_enabled = true;
    c.bus.write(RAM, 0x00816090, 4).unwrap(); // [r1]=r0; rti
    c.bus.write(0x01c7fe00 + 16 * 4, RAM, 4).unwrap();
    c.bus.write(IRQ_CONFIG + 2 * 4, 1 | 6, 4).unwrap();
    for (address, value) in [
        (0x51020, 0x10),
        (0x50088, !0x780),
        (0x50080, 0),
        (SPI, 0x2021),
        (SPI + 8, 0x11),
    ] {
        c.bus.write(address, value, 4).unwrap();
    }
    c.step().unwrap();
    assert_eq!(c.irq_entries, 1);
    assert_eq!(c.pc, RAM);
    c.step().unwrap();
    assert_eq!(c.pc, RAM + 2);
    assert_eq!(c.bus.pending_irq(0x100), None);
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 2);
    assert_eq!(c.sr[14], RAM + 256);
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 4);
    assert_eq!(c.irq_entries, 1);
}

#[test]
fn register_pointer_updates_use_the_old_address_and_byte_stride() {
    // Vendor r3 assembler: the three-bit stride field selects r8..r15.
    for (base, size, value) in [
        (0x1040, 1, 0xef),
        (0x0c40, 2, 0xcdef),
        (0x0840, 4, 0x89abcdef),
    ] {
        for stride in 8..16 {
            let load = base | ((stride - 8) << 7) as u16;
            let store = load | 8;
            for delta in [0, 4, u32::MAX - 3] {
                let mut c = cpu(&[load, store]);
                c.r[4] = RAM + 32;
                c.r[stride] = delta;
                c.sr[5] = 15;
                c.bus.write(RAM + 32, value, size).unwrap();
                c.step().unwrap();
                assert_eq!(c.r[0], value);
                assert_eq!(c.r[4], (RAM + 32).wrapping_add(delta));
                c.r[0] = 0x12345678;
                c.step().unwrap();
                assert_eq!(
                    c.bus.read((RAM + 32).wrapping_add(delta), size).unwrap(),
                    0x12345678 & (u32::MAX >> ((4 - size) * 8))
                );
                assert_eq!(c.r[4], (RAM + 32).wrapping_add(delta).wrapping_add(delta));
                assert_eq!(c.sr[5], 15);
            }
        }
    }
    let mut c = cpu(&[0x13c0]);
    c.r[4] = RAM - 1;
    c.r[15] = 1;
    assert!(c.step().is_err());
    assert_eq!(c.r[4], RAM - 1);
    // Keep the unmeasured aliased form unsupported.
    assert!(cpu(&[0x13f7]).step().is_err());
}

#[test]
fn large_stack_frames_preserve_flags_and_return_to_the_caller() {
    // Vendor r3 assembly, including the stock filesystem's 616-byte frame.
    let mut c = cpu(&[
        0x0410, 0xe8f0, 0x1d98, 0x2080, 0x2001, 0xe8f0, 0x0268, 0x0400,
    ]);
    c.sr[14] = RAM + 1024;
    c.sr[3] = XIP + 16;
    c.sr[5] = 0xa5a5_000f;
    c.r[0] = 0x1234_5678;
    for _ in 0..6 {
        c.step().unwrap();
    }
    assert_eq!(c.r[1], c.r[0]);
    assert_eq!(c.sr[14], RAM + 1024);
    assert_eq!(c.sr[5], 0xa5a5_000f);
    assert_eq!(c.pc, XIP + 16);

    for (word, delta) in [
        (0x1000, -4096i32),
        (0x1d80, -640),
        (0x0280, 640),
        (0x0ffc, 4092),
    ] {
        let mut c = cpu(&[0xe8f0, word]);
        c.sr[14] = 0;
        c.sr[5] = 15;
        c.step().unwrap();
        assert_eq!(c.sr[14], delta as u32);
        assert_eq!(c.sr[5], 15);
        assert_eq!(c.pc, XIP + 4);
    }
    for word in [0x2000, 0x0269] {
        assert!(cpu(&[0xe8f0, word]).step().is_err());
    }
}
#[test]
fn scheduler_restores_the_task_frame_and_stack_pointer_banks() {
    let mut c = cpu(&[0x04e8, 0x04a8, 0x1442, 0x1443, 0x1440, 0x1441]);
    c.sr[14] = RAM + 64;
    c.sr[5] = 0x12345678;
    c.sr[3] = XIP + 20;
    c.sr[0] = XIP + 24;
    c.step().unwrap();
    assert_eq!(c.sr[14], RAM + 56);
    assert_eq!(c.bus.read(RAM + 56, 4).unwrap(), XIP + 20);
    assert_eq!(c.bus.read(RAM + 60, 4).unwrap(), 0x12345678);
    c.sr[3] = 0;
    c.sr[5] = 0;
    c.step().unwrap();
    assert_eq!(c.sr[14], RAM + 64);
    assert_eq!(c.sr[3], XIP + 20);
    assert_eq!(c.sr[5], 0x12345678);
    assert_eq!(c.sr[0], XIP + 24);
    c.step().unwrap();
    c.sr[14] = RAM + 128;
    c.step().unwrap();
    c.sr[14] = 0;
    c.step().unwrap();
    assert_eq!(c.sr[14], RAM + 64);
    c.step().unwrap();
    assert_eq!(c.sr[14], RAM + 128);
}

#[test]
fn idle_waits_for_a_timer_interrupt_and_resumes_after_the_opcode() {
    use fm1_emu::devices::IRQ_CONFIG;
    // FM-1_996: TIMER3 wakes IDLE, four CSYNCs complete, then IRQ entry.
    let mut c = cpu(&[0x0001, 0x0020, 0x0020, 0x0020, 0x0020, 0x2341, 0, 0, 0x0081]);
    c.r[1] = 42;
    c.sr[14] = RAM + 256;
    c.sr[13] = RAM + 512;
    c.sr[11] = 0x100;
    c.bus.write(0x01c7fe00 + 7 * 4, XIP + 16, 4).unwrap();
    c.bus.write(IRQ_CONFIG, 5 << 28, 4).unwrap();
    c.bus.write(0x10708, 32, 4).unwrap();
    c.bus.write(0x10700, 0x4019, 4).unwrap();
    c.interrupts_enabled = true;
    assert_eq!(c.step().unwrap(), "idle");
    for _ in 0..50 {
        assert_eq!(c.step().unwrap(), "idle_wait");
        assert_eq!(c.pc, XIP + 2);
        assert_eq!(c.r[1], 42);
    }
    for _ in 0..3000 {
        c.step().unwrap();
        if c.irq_entries != 0 {
            break;
        }
    }
    assert_eq!(c.irq_entries, 1);
    assert_eq!(c.pc, XIP + 16);
    assert_eq!(c.sr[0], XIP + 10);
    assert_eq!(c.r[1], 42);
    c.bus.write(0x10700, 0x4000, 4).unwrap();
    assert_eq!(c.step().unwrap(), "rti");
    assert_eq!(c.pc, XIP + 10);
    c.step().unwrap();
    assert_eq!(c.r[1], 3);
}

#[test]
fn cli_immediately_after_idle_can_mask_the_waking_interrupt() {
    use fm1_emu::devices::IRQ_CONFIG;
    let mut c = cpu(&[0x0001, 0x0060, 0x2341]);
    c.sr[11] = 0x100;
    c.bus.write(IRQ_CONFIG + 15 * 4, 5, 4).unwrap();
    c.interrupts_enabled = true;
    c.step().unwrap();
    c.bus.write(0x1eef1a0, 1, 4).unwrap();
    assert_eq!(c.step().unwrap(), "idle_wait");
    assert_eq!(c.irq_entries, 0);
    assert_eq!(c.step().unwrap(), "cli");
    c.step().unwrap();
    assert_eq!(c.r[1], 3);
    assert_eq!(c.irq_entries, 0);
}

#[test]
fn leading_zero_count_selects_the_highest_ready_task_priority() {
    for (value, expected) in [
        (0, 32),
        (1, 31),
        (0x2000c001, 2),
        (0x80000000, 0),
        (u32::MAX, 0),
    ] {
        let mut c = cpu(&[0xe180, 0x4200]); // r4 = clz(r2)
        c.r[2] = value;
        c.r[0] = 0x55555555;
        c.step().unwrap();
        assert_eq!(c.r[4], expected);
        assert_eq!(c.r[2], value);
        assert_eq!(c.r[0], 0x55555555);
    }
}

#[test]
fn stock_uart_midi_receive_setup_stays_empty_without_input() {
    let mut c = cpu(&[0]);
    let start = RAM + 1024;
    c.bus.write(start, 0x12345678, 4).unwrap();
    // Stock UART1 setup uses halfword control/divider accesses and an
    // empty 128-byte DMA receive buffer. RDC must not invent received bytes.
    c.bus.write(0x12100, 0x3400, 2).unwrap();
    c.bus.write(0x1211c, start, 4).unwrap();
    c.bus.write(0x12120, start, 4).unwrap();
    c.bus.write(0x12124, 128, 4).unwrap();
    c.bus.write(0x12108, 383, 2).unwrap();
    c.bus.write(0x12110, 48000, 4).unwrap();
    c.bus.write(0x12100, 0x6d, 2).unwrap();
    c.bus.devices.advance(1_000_000);
    c.bus.write(0x12100, 0x14ed, 4).unwrap();
    assert_eq!(c.bus.read(0x12100, 2).unwrap(), 0x6d);
    assert_eq!(c.bus.read(0x12128, 4).unwrap(), 0);
    assert_eq!(c.bus.read(0x12120, 4).unwrap(), start);
    assert_eq!(c.bus.read(start, 4).unwrap(), 0x12345678);
    assert!(c.bus.write(0x12118, 1, 2).is_err());
    c.bus.write(0x1210c, 0x90, 1).unwrap();
    assert_eq!(c.bus.read(0x12100, 2).unwrap(), 0x6d);
    assert!(c.bus.write(0x1210c, 0x40, 1).is_err()); // TX still busy.
    assert!(c.bus.write(0x12128, 1, 2).is_err());
    assert!(c.bus.read(0x1212c, 4).is_err());
}

#[test]
fn stock_bluetooth_can_set_a_tx_descriptor_without_starting_dma() {
    // Stock 0x0206fb8e, vendor __write_reg_txericntl: [r2] = r1.
    let mut c = cpu(&[0x60a1]);
    c.r[2] = 0x200c0;
    c.r[1] = 0x019a10c4;
    c.bus.write(RAM, 0x12345678, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 2);
    assert_eq!(c.r[1], 0x019a10c4);
    assert_eq!(c.r[2], 0x200c0);
    assert_eq!(c.bus.read(RAM, 4).unwrap(), 0x12345678);
    assert_eq!(c.irq_entries, 0);
    assert!(c.bus.read(0x200c0, 4).is_err());
    assert!(c.bus.write(0x200c0, 1, 2).is_err());
    assert!(c.bus.write(0x200c4, 1, 4).is_err());
}

#[test]
fn stock_uart_pin_routing_preserves_the_lcd_and_other_map_bits() {
    // Original startup at 0x02023b6a: clear/select UT1 RX input channel,
    // then route input channel 1 from PH8, using high base register r8.
    let mut c = cpu(&[
        0xefc2, 0x8080, 0xefc2, 0x8070, 0xef02, 0x8050, 0xefc1, 0x8d7c, 0xef01, 0x8d44,
    ]);
    c.r[8] = 0x51020;
    c.bus.write(0x51020, 0x10, 4).unwrap();
    c.bus.write(0x51024, 0xabcdfeff, 4).unwrap();
    c.bus.write(0x51028, 0x1234ffff, 4).unwrap();
    for _ in 0..5 {
        c.step().unwrap();
    }
    assert_eq!(c.bus.read(0x51024, 4).unwrap(), 0xabcdf1ff);
    assert_eq!(c.bus.read(0x51028, 4).unwrap(), 0x1234ff5f);
    assert_eq!(c.bus.read(0x51020, 4).unwrap(), 0x10);
    assert_eq!(c.r[8], 0x51020);
    assert!(c.bus.write(0x51024, 0, 2).is_err());
    assert!(c.bus.read(0x5102c, 4).is_err());
}

#[test]
fn stock_can_disable_the_unused_high_speed_usb_controller() {
    let mut c = cpu(&[0]);
    c.bus.write(0x16800, 0, 4).unwrap();
    assert_eq!(c.bus.read(0x16800, 4).unwrap(), 0);
    assert!(c.bus.write(0x16800, 1, 4).is_err());
}

#[test]
fn register_list_stores_linked_list_fields_without_advancing_the_base() {
    // FM-1_984 capture confirms ascending register order and no writeback.
    let mut c = cpu(&[0xeb20, 6, 0xeb21, 0x101]);
    c.r[0] = RAM;
    c.r[1] = RAM + 64;
    c.r[2] = RAM + 128;
    c.r[8] = 0x12345678;
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM, 4).unwrap(), RAM + 64);
    assert_eq!(c.bus.read(RAM + 4, 4).unwrap(), RAM + 128);
    assert_eq!(c.r[0], RAM);
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM + 64, 4).unwrap(), RAM);
    assert_eq!(c.bus.read(RAM + 68, 4).unwrap(), 0x12345678);
    assert_eq!(c.r[1], RAM + 64);
}

#[test]
fn stock_fifo_consumption_subtracts_the_register_from_both_counts() {
    // Unchanged stock at 0x0202834e: [r6+24] -= r12; [r6+20] -= r12.
    let mut c = cpu(&[0xe868, 0x6c1a, 0xe868, 0x6c16]);
    c.r[6] = RAM;
    c.r[12] = 4;
    c.sr[5] = 0x12345678;
    c.bus.write(RAM + 24, 4, 4).unwrap();
    c.bus.write(RAM + 20, 8, 4).unwrap();
    c.step().unwrap();
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM + 24, 4).unwrap(), 0);
    assert_eq!(c.bus.read(RAM + 20, 4).unwrap(), 4);
    assert_eq!(c.r[6], RAM);
    assert_eq!(c.r[12], 4);
    assert_eq!(c.sr[5], 0x12345678);
    // Both directions wrap as 32-bit memory operations.
    for (word, incoming, expected) in [(0x6c18, u32::MAX, 3), (0x6c1a, 1, 0xfffffffd)] {
        let mut c = cpu(&[0xe868, word]);
        c.r[6] = RAM;
        c.r[12] = 4;
        c.bus.write(RAM + 24, incoming, 4).unwrap();
        c.step().unwrap();
        assert_eq!(c.bus.read(RAM + 24, 4).unwrap(), expected);
    }
}

#[test]
fn flash_identification_shifts_the_jedec_word_in_memory() {
    let mut c = cpu(&[0xe86c, 0x581e]); // [r5+28] >>= 8
    c.r[5] = RAM;
    c.bus.write(RAM + 28, 0x85601400, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM + 28, 4).unwrap(), 0x00856014);
    assert_eq!(c.r[5], RAM);
}

#[test]
fn packed_unsigned_less_equal_checks_the_stock_ram_code_size() {
    for (value, expected) in [(0, 0), (0xd80, 0), (4096, 0), (4097, 1), (u32::MAX, 1)] {
        let mut c = cpu(&[0xeca1, 0x0d80, 0x2040, 0]);
        c.r[1] = value;
        c.r[0] = 1;
        while c.pc < XIP + 6 {
            c.step().unwrap();
        }
        assert_eq!(c.r[0], expected);
    }
}

#[test]
fn interrupt_enable_instructions_expose_the_global_icfg_bit() {
    let mut c = cpu(&[0x0061, 0x0060, 0x0061, 0xe064, 0x0b00, 0xe064, 0x0b80]);
    c.sr[11] = 0x100;
    c.step().unwrap();
    assert_eq!(c.sr[11], 0x300);
    c.step().unwrap();
    assert!(!c.interrupts_enabled);
    assert_eq!(c.sr[11], 0x100);
    c.step().unwrap();
    c.step().unwrap();
    assert_eq!(c.r[0], 0x300);
    c.r[0] = 0x100;
    c.step().unwrap();
    assert!(!c.interrupts_enabled);
}

#[test]
fn stock_startup_long_calls_return_after_six_bytes() {
    // Vendor disassembly: call 176, and nested call -10 to an rts.
    let mut c = cpu(&[0xff80, 0x00b0, 0x0000]);
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 6 + 176);
    assert_eq!(c.sr[3], XIP + 6);
    let mut c = cpu(&[0x0080, 0xff80, 0xfff8, 0xffff]);
    c.pc = XIP + 2;
    c.step().unwrap();
    assert_eq!(c.pc, XIP);
    assert_eq!(c.sr[3], XIP + 8);
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 8);
}

#[test]
fn core_tick_timer_wraps_acknowledges_and_obeys_irq_priority() {
    use fm1_emu::devices::{IRQ_CONFIG, IRQ_PENDING, TICK_IRQ, TICK_TIMER};
    let mut c = cpu(&[0; 16]);
    c.bus.write(TICK_TIMER + 8, 29, 4).unwrap();
    c.bus.write(TICK_TIMER, 1, 1).unwrap();
    c.bus.devices.advance(1);
    assert_eq!(c.bus.read(TICK_TIMER + 4, 4).unwrap(), 15);
    assert_eq!(c.bus.pending_irq(0x100), None);
    c.bus.devices.advance(1);
    assert_eq!(c.bus.read(TICK_TIMER + 4, 4).unwrap(), 0);
    assert_eq!(c.bus.read(TICK_TIMER, 1).unwrap(), 129);
    assert_eq!(c.bus.read(IRQ_PENDING, 4).unwrap(), 8);
    c.bus.write(IRQ_CONFIG, 3 << 12, 4).unwrap();
    assert_eq!(c.bus.pending_irq(0x100), Some(TICK_IRQ));
    c.bus.write(TICK_TIMER, 65, 1).unwrap();
    assert_eq!(c.bus.pending_irq(0x100), None);
    assert_eq!(c.bus.read(TICK_TIMER, 1).unwrap(), 1);
    c.bus.write(TICK_TIMER, 0, 1).unwrap();
    c.bus.devices.advance(100);
    assert_eq!(c.bus.read(TICK_TIMER + 4, 4).unwrap(), 0);
}

#[test]
fn stock_slot_timer_raises_irq_41_at_a_real_clock_deadline() {
    use fm1_emu::devices::IRQ_CONFIG;
    let mut c = cpu(&vec![0; 230_000]);
    c.bus.write(0x10014, 6, 4).unwrap(); // Nominal 360 MHz CPU.
    c.bus.write(0x20000, 0x107, 4).unwrap();
    c.bus.write(0x2fd44, 1, 4).unwrap(); // One 625 us slot.
    c.bus.write(0x2fd40, 0x100, 4).unwrap();
    c.bus.write(0x2fd40, 1, 4).unwrap();
    c.bus.write(IRQ_CONFIG + 5 * 4, 0x5f, 4).unwrap();
    c.bus.write(0x01c7fe00 + 41 * 4, RAM, 4).unwrap();
    c.bus.write(RAM, 0x0081, 2).unwrap();
    c.sr[14] = RAM + 256;
    c.sr[13] = RAM + 512;
    c.sr[11] = 0x100;
    c.interrupts_enabled = true;
    for _ in 0..224_999 {
        c.step().unwrap();
    }
    assert_eq!(c.irq_entries, 0);
    assert_eq!(c.bus.read(0x2fd40, 4).unwrap(), 1);
    c.step().unwrap();
    assert_eq!(c.pc, RAM);
    assert_eq!(c.sr[0], XIP + 450_000);
    assert_eq!(c.irq_entries, 1);
    assert_eq!(c.bus.read(0x2fd40, 4).unwrap(), 0x10001);
    assert_eq!(c.bus.pending_irq(0x100), Some(41));
    c.bus.write(0x2fd40, 0x100, 4).unwrap();
    assert_eq!(c.bus.pending_irq(0x100), None);
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 450_000);
}

#[test]
fn stock_rc_calibration_measures_and_rearms_the_low_speed_clock() {
    use fm1_emu::devices::{IRQ_CONFIG, IRQ_PENDING};
    let mut c = cpu(&[0]);
    let lrct = 0x13600;
    // SDK IRQ 44, priority 2; the stock driver clears, disables, configures,
    // then enables each measurement, and acknowledges again in its handler.
    c.bus.write(IRQ_CONFIG + 20, 5 << 16, 4).unwrap();
    for exponent in [1, 0, 1] {
        c.bus.write(lrct, 64, 4).unwrap();
        c.bus.write(lrct, 0, 4).unwrap();
        c.bus.write(lrct, (exponent << 1) | 1, 4).unwrap();
        assert_eq!(c.bus.pending_irq(0x100), None);
        let cycles = 32u32 << exponent;
        c.bus.devices.advance(cycles * 750 - 1);
        assert_eq!(c.bus.pending_irq(0x100), None);
        c.bus.devices.advance(1);
        assert_eq!(c.bus.pending_irq(0), None);
        assert_eq!(c.bus.pending_irq(0x100), Some(44));
        assert_eq!(c.bus.read(IRQ_PENDING + 4, 4).unwrap(), 1 << 12);
        let number = c.bus.read(lrct + 4, 4).unwrap();
        assert_eq!(cycles as u64 * 480_000_000 / number as u64, 32_000);
        c.bus
            .write(lrct, c.bus.read(lrct, 4).unwrap() | 64, 4)
            .unwrap();
        assert_eq!(c.bus.pending_irq(0x100), None);
    }
    c.bus.write(lrct, 0, 4).unwrap();
    c.bus.devices.advance(100_000);
    assert_eq!(c.bus.pending_irq(0x100), None);
    assert!(c.bus.read(lrct, 1).is_err());
    assert!(c.bus.write(lrct, 1, 1).is_err());
}

#[test]
fn startup_timer_banks_count_and_signal_their_sdk_interrupts() {
    use fm1_emu::devices::{IRQ_CONFIG, IRQ_PENDING};
    for index in 0..4 {
        let mut c = cpu(&[0]);
        let base = 0x10400 + index * 256;
        c.bus.write(base, 0x4000, 4).unwrap();
        c.bus.write(base + 8, 3, 4).unwrap();
        c.bus.write(base, 9, 4).unwrap();
        c.bus.write(IRQ_CONFIG, 3 << ((4 + index) * 4), 4).unwrap();
        c.bus.devices.advance(3);
        assert_eq!(c.bus.pending_irq(0x100), Some((4 + index) as usize));
        assert_eq!(c.bus.read(IRQ_PENDING, 4).unwrap(), 1 << (4 + index));
        c.bus.write(base, 0x4009, 4).unwrap();
        assert_eq!(c.bus.pending_irq(0x100), None);
    }
}

#[test]
fn startup_repeat_clears_exactly_the_requested_words() {
    // Stock startup: rep 2 r2 { [r3++=4] = r1 }; if (r2 != 0) goto rep.
    for count in [0, 1, 3, 32, 33, 65] {
        let mut c = cpu(&[0x0302, 0x05b1, 0x5df2, 0x0000]);
        c.r[1] = 0x11223344;
        c.r[2] = count;
        c.r[3] = RAM;
        while c.pc != XIP + 6 {
            c.step().unwrap();
            assert!(c.steps <= count as u64 * 2 + 2);
        }
        assert_eq!(c.r[2], 0);
        assert_eq!(c.r[3], RAM + count * 4);
        for i in 0..count {
            assert_eq!(c.bus.read(RAM + i * 4, 4).unwrap(), 0x11223344);
        }
        assert_eq!(c.bus.read(RAM + count * 4, 4).unwrap(), 0);
    }
}

#[test]
fn stock_memcpy_captures_the_repeat_count_before_overwriting_it() {
    // Stock memcpy at 0x02044596: rep 4 r2 { r2=b[r1++]; b[r3++]=r2 }.
    for count in [0, 1, 4] {
        let mut c = cpu(&[0x0312, 0x0712, 0x07b2, 0x0000]);
        c.r[1] = RAM;
        c.r[2] = count;
        c.r[3] = RAM + 32;
        for (i, byte) in b"btif".iter().enumerate() {
            c.bus.write(RAM + i as u32, *byte as u32, 1).unwrap();
        }
        while c.pc != XIP + 6 {
            c.step().unwrap();
            assert!(c.steps <= 9);
        }
        assert_eq!(c.r[1], RAM + count);
        assert_eq!(c.r[3], RAM + 32 + count);
        for i in 0..count {
            assert_eq!(
                c.bus.read(RAM + 32 + i, 1).unwrap(),
                b"btif"[i as usize] as u32
            );
        }
        assert_eq!(c.bus.read(RAM + 32 + count, 1).unwrap(), 0);
        assert_eq!(c.r[2], 0);
    }
}

#[test]
fn register_repeat_matches_physical_fm1_batches_and_counter_writeback() {
    // tools/build_repeat_probe.py, FM-1_983 USB capture on 2026-10-05.
    for (count, iterations, remaining) in [
        (0, 0, 0),
        (1, 1, 0),
        (2, 2, 0),
        (4, 4, 0),
        (15, 15, 0),
        (16, 16, 0),
        (17, 17, 0),
        (31, 31, 0),
        (32, 1, 31),
        (33, 2, 31),
        (63, 32, 31),
        (64, 1, 63),
        (65, 2, 63),
        (127, 32, 95),
        (129, 2, 127),
        (256, 1, 255),
        (1024, 1, 1023),
    ] {
        let mut c = cpu(&[0x0302, 0x8119, 0x0000]); // rep 2 r2 { r1 += 1 }
        c.r[2] = count;
        while c.pc != XIP + 4 {
            c.step().unwrap();
            assert!(c.steps <= 33);
        }
        assert_eq!((c.r[1], c.r[2]), (iterations, remaining));
    }
    for overwrite in [false, true] {
        let words = if overwrite {
            vec![0x0312, 0x05b2, 0x3962, 0x0000]
        } else {
            vec![0x0302, 0x05b2, 0x0000]
        };
        let end = XIP + if overwrite { 6 } else { 4 };
        let mut c = cpu(&words);
        c.r[2] = 4;
        c.r[3] = RAM;
        while c.pc != end {
            c.step().unwrap();
            assert!(c.steps <= 9);
        }
        for i in 0..4 {
            assert_eq!(c.bus.read(RAM + i * 4, 4).unwrap(), 4 - i);
        }
        assert_eq!(c.r[2], 0);
    }
}

#[test]
fn packed_memory_and_preserves_the_high_cache_way_bits() {
    // Stock cache setup: [r0+4] &= 0xff000000.
    let mut c = cpu(&[0xef81, 0x047f]);
    c.r[0] = RAM;
    c.bus.write(RAM + 4, 0x87654321, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM + 4, 4).unwrap(), 0x87000000);
    assert_eq!(c.r[0], RAM);
}

#[test]
fn wireless_pll_samples_follow_measured_comparator_boundaries() {
    let mut c = cpu(&[0]);
    c.bus.write(0x11968, 0x10000000, 4).unwrap();
    for (cap, feedback, expected) in [
        (64, 109, 0x20081),
        (64, 110, 0x81),
        (64, 122, 0x81),
        (64, 123, 0x40081),
        (0, 255, 0x20081),
        (127, 0, 0x40081),
        (63, 100, 0x40081), // Measured discontinuity at the next bank.
        (64, 100, 0x20081),
    ] {
        c.bus.write(0x11938, cap << 19, 4).unwrap();
        c.bus.write(0x1193c, feedback << 5, 4).unwrap();
        c.bus.write(0x11978, 0, 4).unwrap();
        let previous = c.bus.read(0x11978, 4).unwrap();
        for _ in 0..7 {
            c.bus.write(0x11978, 1, 4).unwrap();
        }
        assert_eq!(c.bus.read(0x11978, 4).unwrap(), previous);
        c.bus.write(0x11978, 1, 4).unwrap();
        c.bus.write(0x11978, 0, 4).unwrap();
        assert_eq!(c.bus.read(0x11978, 4).unwrap(), expected);
    }
    c.bus.write(0x11968, 0x20000000, 4).unwrap();
    assert!(c.bus.write(0x11978, 1, 4).is_err());
}

#[test]
fn stock_ble_anchor_configuration_keeps_columns_and_slots_separate() {
    let mut c = cpu(&[0]);
    // Vendor RF_ble.c __set/__get_ble_anchor_con, reached by unchanged
    // FM-1_015 at 0x0205e5b8/0x0205e546. No packet engine is exercised.
    for (column, slot, data) in [(2, 0, 0xf7ff), (2, 1, 0x1234), (16, 0, 0x4321)] {
        c.bus.write(0x28020, data, 4).unwrap();
        c.bus
            .write(0x2801c, column << 10 | slot << 4 | 5, 4)
            .unwrap();
        // Writing configuration does not update the previous read result.
        assert_eq!(c.bus.read(0x28024, 4).unwrap(), 0);
    }
    for (column, slot, data) in [(2, 0, 0xf7ff), (2, 1, 0x1234), (16, 0, 0x4321), (0, 0, 0)] {
        c.bus
            .write(0x2801c, column << 10 | slot << 4 | 2, 4)
            .unwrap();
        assert_eq!(c.bus.read(0x28024, 4).unwrap(), data);
    }
    assert!(c.bus.write(0x2801c, 17 << 10 | 2, 4).is_err());
    assert!(c.bus.write(0x2801c, 3, 4).is_err());
    assert!(c.bus.read(0x28020, 4).is_err());
    assert!(c.bus.read(0x2801c, 4).is_err());
    assert!(c.bus.write(0x28024, 0, 4).is_err());
    assert!(c.bus.write(0x28020, 0, 2).is_err());
    assert_eq!(c.bus.read(0x28038, 4).unwrap(), 0);
    c.bus.write(0x28020, 0x800, 4).unwrap();
    c.bus.write(0x2801c, 2 << 10 | 5, 4).unwrap();
    assert!(c.bus.read(0x28038, 4).is_err());
    assert!(c.bus.read(0x28040, 4).is_err());
}

#[test]
fn wireless_bbp_transactions_store_and_read_selected_bytes() {
    let mut c = cpu(&[0]);
    let command = 0x3101c;
    for (register, value) in [(21, 49), (22, 0), (25, 193)] {
        c.bus
            .write(command, 0x80000 | register << 8 | value, 4)
            .unwrap();
        c.bus
            .write(command, 0xa0000 | register << 8 | value, 4)
            .unwrap();
        assert_eq!(c.bus.read(command, 4).unwrap() & 0x20000, 0);
        c.bus.write(command, 0xb0000 | register << 8, 4).unwrap();
        assert_eq!(c.bus.read(command, 4).unwrap() & 255, value);
    }
    for address in [0x31100, 0x11900, 0x11974] {
        c.bus.write(address, 0x13579bdf, 4).unwrap();
        assert_eq!(c.bus.read(address, 4).unwrap(), 0x13579bdf);
    }
    assert!(c.bus.read(0x31008, 4).is_err());
    assert!(c.bus.read(0x1197c, 4).is_err());
}

#[test]
fn wireless_configuration_preserves_writes_and_rejects_missing_operations() {
    let mut c = cpu(&[0]);
    for address in [
        0x14000, 0x1401c, 0x14028, 0x14034, 0x14040, 0x14064, 0x30f00, 0x30f04,
    ] {
        c.bus.write(address, 0x87654321, 4).unwrap();
        assert_eq!(c.bus.read(address, 4).unwrap(), 0x87654321);
        assert!(c.bus.read(address, 2).is_err());
    }
    for address in [0x14038, 0x1403c, 0x14068, 0x30f08] {
        assert!(c.bus.read(address, 4).is_err());
    }
    assert!(c.bus.write(0x14024, 1, 4).is_err());
    c.bus.write(0x14020, 0, 4).unwrap();
    assert!(c.bus.write(0x14020, 1, 4).is_err());
}

#[test]
fn random_generator_exposes_changing_paired_read_only_words() {
    let mut c = cpu(&[0]);
    let mut replay = cpu(&[0]);
    let value = |c: &Cpu| {
        c.bus.read(0x13b00, 4).unwrap() as u64 | ((c.bus.read(0x13b04, 4).unwrap() as u64) << 32)
    };
    let first = value(&c);
    assert_ne!(first, 0);
    for _ in 0..8 {
        c.bus.devices.advance(1);
        replay.bus.devices.advance(1);
        assert_eq!(value(&c), value(&replay));
        assert_ne!(value(&c), first);
    }
    assert!(c.bus.write(0x13b00, 0, 4).is_err());
    assert!(c.bus.read(0x13b00, 2).is_err());
}

#[test]
fn wide_immediate_shifts_extract_stock_date_and_signed_format_values() {
    for shift in [0, 8, 16, 24, 32, 48, 56, 63] {
        for mode in [0, 2, 3] {
            let encoding = 0x2000 | mode << 10 | (shift / 16) << 8 | (shift % 16);
            let mut c = cpu(&[0xe1d0, encoding]);
            let value = 0x81234567fedcba98u64;
            c.r[2] = value as u32;
            c.r[3] = (value >> 32) as u32;
            c.step().unwrap();
            let expected = match mode {
                0 => value << shift,
                2 => value >> shift,
                _ => ((value as i64) >> shift) as u64,
            };
            assert_eq!(c.r[2] as u64 | ((c.r[3] as u64) << 32), expected);
        }
    }
}

#[test]
fn stock_can_disable_the_unused_hardware_sample_rate_converter() {
    let mut c = cpu(&[0]);
    c.bus.write(0x14300, 0, 4).unwrap();
    assert_eq!(c.bus.read(0x14300, 4).unwrap(), 0);
    assert!(c.bus.write(0x14300, 1, 4).is_err());
    assert!(c.bus.read(0x14304, 4).is_err());
}

#[test]
fn stock_signed_greater_block_updates_the_formatting_width() {
    // ifs (r5 > r2) { r0 = 1; r1 = 2; }
    for (left, right, taken) in [(1, u32::MAX, true), (u32::MAX, 1, false), (2, 2, false)] {
        let mut c = cpu(&[0xee15, 0x4200, 0x2140, 0x2241, 0]);
        c.r[5] = left;
        c.r[2] = right;
        c.r[0] = 0;
        c.r[1] = 0;
        while c.pc < XIP + 8 {
            c.step().unwrap();
        }
        assert_eq!((c.r[0], c.r[1]), if taken { (1, 2) } else { (0, 0) });
    }
}

#[test]
fn stock_adc_handler_saves_and_restores_the_interrupted_pc() {
    let mut c = cpu(&[0x04c1, 0x0481, 0x0488]);
    c.sr[0] = XIP + 128;
    c.sr[14] = RAM + 64;
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM + 60, 4).unwrap(), XIP + 128);
    c.sr[0] = 0;
    c.step().unwrap();
    assert_eq!(c.sr[0], XIP + 128);
    assert_eq!(c.sr[14], RAM + 64);
    c.bus.write(RAM + 64, XIP + 256, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.sr[3], XIP + 256);
    assert_eq!(c.pc, XIP + 6);
}

#[test]
fn stock_p33_field_update_ands_the_value_with_the_inverted_mask() {
    // Vendor RAM routine: r2 = r3 & ~r0. Omitting r3 selects all PMU inputs.
    for (value, expected) in [(5, 5), (0, 0), (0xffffffff, 7), (0x12345678, 0)] {
        let mut c = cpu(&[0xe190, 0x2033]);
        c.r[0] = 0xfffffff8;
        c.r[3] = value;
        c.step().unwrap();
        assert_eq!(c.r[2], expected);
        assert_eq!(c.r[3], value);
    }
}

#[test]
fn carry_arithmetic_matches_seven_physical_fm1_measurements() {
    // FM-1_984 USB capture: result, PSR (V/C/Z/N), then add-with-carry or
    // subtract-with-not-carry. Each row was executed on the connected device.
    for (subtract, left, right, result, psr, chained) in [
        (false, u32::MAX, 1, 0, 6, 1),
        (false, 0, 0, 0, 4, 0),
        (false, 0x7fffffff, 1, 0x80000000, 9, 0x80000000),
        (true, 0, 1, u32::MAX, 8, 0xfffffffe),
        (true, 1, 0, 1, 2, 1),
        (true, 0, 0, 0, 6, 0),
        (true, 0x80000000, 1, 0x7fffffff, 3, 0x7fffffff),
    ] {
        let mut c = cpu(&[
            0xe0b4,
            0x3210 | if subtract { 2 } else { 0 },
            0xe0b8,
            0x5210 | if subtract { 2 } else { 0 },
        ]);
        c.r[1] = left;
        c.r[2] = right;
        c.sr[5] = 0x1000;
        c.step().unwrap();
        assert_eq!(c.r[3], result);
        assert_eq!(c.sr[5], psr | 0x1000);
        c.step().unwrap();
        assert_eq!(c.r[5], chained);
    }
    // Stock's two-word subtraction and addition carry across the low word.
    let mut c = cpu(&[0x1f84, 0xe0b8, 0x5712]); // r4=r0-r6; r5=r1-r7-!c
    c.r[0] = 0;
    c.r[1] = 1;
    c.r[6] = 1;
    c.r[7] = 0;
    c.step().unwrap();
    c.step().unwrap();
    assert_eq!((c.r[5] as u64) << 32 | c.r[4] as u64, u32::MAX as u64);
}

#[test]
fn memory_shifts_cover_the_high_shift_bit_and_signed_right_mode() {
    for shift in [0, 1, 15, 16, 22, 31] {
        for mode in [0, 2, 3] {
            let mut c = cpu(&[0xe86c | shift / 16, 0x1004 | (shift % 16) << 8 | mode]);
            c.r[1] = RAM;
            let value = 0x87654321u32;
            c.bus.write(RAM + 4, value, 4).unwrap();
            c.step().unwrap();
            let expected = match mode {
                0 => value << shift,
                2 => value >> shift,
                _ => ((value as i32) >> shift) as u32,
            };
            assert_eq!(c.bus.read(RAM + 4, 4).unwrap(), expected);
            assert_eq!(c.r[1], RAM);
        }
    }
}

#[test]
fn wide_multiply_accumulate_keeps_carry_and_incoming_aliased_operands() {
    for (encoding, left, right, accumulator, expected) in [
        (0xae60, u32::MAX, 2, u32::MAX as u64, 0x2fffffffd),
        (0xae60, u32::MAX, u32::MAX, u64::MAX, 0xfffffffe00000000),
        (0xbe60, (-3i32) as u32, 7, 5, (-16i64) as u64),
    ] {
        let mut c = cpu(&[0xe1fc, encoding]);
        c.r[6] = left;
        c.r[14] = right;
        c.r[10] = accumulator as u32;
        c.r[11] = (accumulator >> 32) as u32;
        c.step().unwrap();
        assert_eq!(c.r[10] as u64 | ((c.r[11] as u64) << 32), expected);
    }
    // Both multiplicands overlap the accumulator, so read them first.
    let mut c = cpu(&[0xe1fc, 0x0010]);
    c.r[0] = u32::MAX;
    c.r[1] = 2;
    let value = 0x2ffffffffu64;
    c.step().unwrap();
    assert_eq!(
        c.r[0] as u64 | ((c.r[1] as u64) << 32),
        value + 2 * u32::MAX as u64
    );
}

#[test]
fn wide_divides_decode_as_the_vendor_assembler_does() {
    // Vendor objdump of every E1F6 xxxx: a divide exactly when x & 0x1f == 0;
    // x >> 12 is the destination pair with its low bit selecting signed (as for
    // the wide multiply), (x >> 4) & 15 the dividend pair, (x >> 8) & 15 the
    // divisor. The compiler emits E1F6 1200 for a long long / int.
    for (x, d, s, c, signed) in [
        (0x1200, 0, 0, 2, true),    // r1_r0 = r1_r0 / r2 (s)
        (0x1020, 0, 2, 0, true),    // r1_r0 = r3_r2 / r0 (s)
        (0x3200, 2, 0, 2, true),    // r3_r2 = r1_r0 / r2 (s)
        (0xf2e0, 14, 14, 2, true),  // r15_r14 = r15_r14 / r2 (s)
        (0xe0e0, 14, 14, 0, false), // r15_r14 = r15_r14 / r0 (u)
        (0x0e40, 0, 4, 14, false),  // r1_r0 = r5_r4 / r14 (u)
    ] {
        for (dividend, divisor) in [
            (-7i64, 2i32),
            (i64::MAX, -3),
            (-(1 << 40) - 5, 1000),
            (5, -1),
        ] {
            let mut cpu = cpu(&[0xe1f6, x]);
            cpu.r[s] = dividend as u32;
            cpu.r[s + 1] = (dividend >> 32) as u32;
            cpu.r[c] = divisor as u32;
            cpu.step().unwrap();
            let expected = if signed {
                (dividend / divisor as i64) as u64
            } else {
                dividend as u64 / divisor as u32 as u64
            };
            assert_eq!(
                (cpu.r[d + 1] as u64) << 32 | cpu.r[d] as u64,
                expected,
                "{x:#06x}"
            );
        }
    }
    let mut cpu = cpu(&[0]);
    for x in 0..=u16::MAX {
        cpu.bus.write(RAM, 0xe1f6 | (x as u32) << 16, 4).unwrap();
        cpu.pc = RAM;
        cpu.r = [3; 16];
        assert_eq!(cpu.step().is_ok(), x & 0x1f == 0, "{x:#06x}");
    }
}
#[test]
fn stock_wide_arithmetic_preserves_high_words_and_overlapping_operands() {
    // Vendor stock clock arithmetic, with aliased inputs/outputs and values
    // requiring both words. The destination's low bit selects signed multiply.
    for (encoding, left, right, expected) in [
        (0x0010, u32::MAX, 2, u32::MAX as u64 * 2),
        (0x1010, i32::MIN as u32, (-2i32) as u32, 1u64 << 32),
        (0x1010, (-3i32) as u32, 7, (-21i64) as u64),
    ] {
        let mut c = cpu(&[0xe1f8, encoding]); // r1_r0 = r1 * r0
        c.r[1] = left;
        c.r[0] = right;
        c.step().unwrap();
        assert_eq!((c.r[1] as u64) << 32 | c.r[0] as u64, expected);
    }
    for (value, divisor) in [(128_000_000u64, 1_000_000u32), (u64::MAX, 65535)] {
        let mut c = cpu(&[0xe1f6, 0x0020]); // r1_r0 = r3_r2 / r0 (u)
        c.r[2] = value as u32;
        c.r[3] = (value >> 32) as u32;
        c.r[0] = divisor;
        c.step().unwrap();
        assert_eq!(
            (c.r[1] as u64) << 32 | c.r[0] as u64,
            value / divisor as u64
        );
    }
    for shift in [0, 1, 32, 63, 64, 65] {
        for right in [false, true] {
            let mut c = cpu(&[0xe1d8, 0x0400 | if right { 2 } else { 0 }]);
            let value = 0x81234567fedcba98u64;
            c.r[0] = value as u32;
            c.r[1] = (value >> 32) as u32;
            c.r[4] = shift;
            c.step().unwrap();
            let expected = if right {
                value.checked_shr(shift)
            } else {
                value.checked_shl(shift)
            }
            .unwrap_or(0);
            assert_eq!((c.r[1] as u64) << 32 | c.r[0] as u64, expected);
        }
    }
}

#[test]
fn stock_cache_bound_keeps_unsigned_long_branch_immediates_positive() {
    // Vendor display startup at 0x0200205a: if (r2 < 2111) goto -22.
    for (condition, value, taken) in [
        (3, 2110, true),
        (3, 2111, false),
        (3, u32::MAX, false),
        (2, 2110, false),
        (2, 2111, true),
        (8, 2111, false),
        (8, 2112, true),
        (9, 2111, true),
        (9, 2112, false),
    ] {
        let mut c = cpu(&[0xff00 | condition, 0x283f, 0x0010]);
        c.r[2] = value;
        c.step().unwrap();
        assert_eq!(c.pc, XIP + 6 + if taken { 32 } else { 0 });
    }
}

#[test]
fn stock_empty_flash_comparison_sign_extends_the_long_branch_immediate() {
    // Vendor 0x02034bb4: if (r0 == -768) goto +44; six-byte signed-12 immediate.
    for (value, taken) in [(0xfffffd00, true), (0x00000d00, false), (0, false)] {
        let mut c = cpu(&[0xff00, 0x0d00, 0x0016]);
        c.r[0] = value;
        c.step().unwrap();
        assert_eq!(c.pc, XIP + 6 + if taken { 44 } else { 0 });
        assert_eq!(c.r[0], value);
    }
}

#[test]
fn word_postincrement_store_uses_the_old_base_and_preserves_the_gap() {
    let mut c = cpu(&[0xecd8, 0x1009]); // [r0++=8] = r1
    c.r[0] = RAM;
    c.r[1] = u32::MAX;
    c.bus.write(RAM + 4, 0x12345678, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM, 4).unwrap(), u32::MAX);
    assert_eq!(c.bus.read(RAM + 4, 4).unwrap(), 0x12345678);
    assert_eq!(c.r[0], RAM + 8);
}

#[test]
fn stock_init_calls_load_the_word_before_advancing_by_28_bytes() {
    // Vendor 0x020347fc: r0 = [r4++=28]. r1 must not enter the address.
    let mut c = cpu(&[0xecd8, 0x014c]);
    c.r[4] = RAM;
    c.r[1] = 0x100;
    c.bus.write(RAM, XIP + 100, 4).unwrap();
    c.bus.write(RAM + 0x100, 0xdeadbeef, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.r[0], XIP + 100);
    assert_eq!(c.r[4], RAM + 28);
    assert_eq!(c.r[1], 0x100);
    assert_eq!(c.pc, XIP + 4);
}

#[test]
fn packed_inequality_selects_the_stock_oscillator_frequency() {
    // Vendor stock disassembly: if (r7 != 0x08000000) { 24 MHz } else { 40 MHz }.
    for (value, expected) in [(0x04000000, 24_000_000), (0x08000000, 40_000_000)] {
        let mut c = cpu(&[
            0xe8a7, 0x1600, 0xffc7, 0x3600, 0x016e, 0xffc7, 0x5a00, 0x0262, 0,
        ]);
        c.r[7] = value;
        while c.pc < XIP + 16 {
            c.step().unwrap();
        }
        assert_eq!(c.r[7], expected);
    }
}

#[test]
fn immediate_repeat_clears_twenty_words_and_copies_multiword_blocks() {
    let mut c = cpu(&[0x9300, 0x0592, 0x0000]);
    c.r[1] = RAM;
    c.r[2] = 0x12345678;
    while c.pc != XIP + 4 {
        c.step().unwrap();
        assert!(c.steps <= 21);
    }
    assert_eq!(c.r[1], RAM + 80);
    for i in 0..20 {
        assert_eq!(c.bus.read(RAM + i * 4, 4).unwrap(), 0x12345678);
    }
    assert_eq!(c.bus.read(RAM + 80, 4).unwrap(), 0);

    let mut c = cpu(&[0x8210, 0x0513, 0x05c3, 0x0000]);
    c.r[1] = RAM;
    c.r[4] = RAM + 32;
    for i in 0..3 {
        c.bus.write(RAM + i * 4, 0x12340000 + i, 4).unwrap();
    }
    while c.pc != XIP + 6 {
        c.step().unwrap();
        assert!(c.steps <= 7);
    }
    for i in 0..3 {
        assert_eq!(c.bus.read(RAM + 32 + i * 4, 4).unwrap(), 0x12340000 + i);
    }
}

#[test]
fn conditional_finishes_and_skips_else_before_an_interrupt_enters() {
    // FM-1_988: pending software IRQ observes the final then-arm value,
    // and RETI points past the skipped else instruction.
    use fm1_emu::devices::IRQ_CONFIG;
    let mut c = cpu(&[0xe8a3, 0x9000, 0x60a3, 0x2241, 0x2341, 0x3e79, 0x0081]);
    c.r[2] = 0x1eef1a0;
    c.r[3] = 1;
    c.sr[14] = RAM + 256;
    c.sr[13] = RAM + 512;
    c.sr[11] = 0x100;
    c.bus.write(0x01c7fe00 + 120 * 4, XIP + 12, 4).unwrap();
    c.bus.write(IRQ_CONFIG + 15 * 4, 5, 4).unwrap();
    c.interrupts_enabled = true;
    c.step().unwrap();
    c.step().unwrap(); // Pending inside the then arm.
    c.step().unwrap();
    assert_eq!(c.irq_entries, 0);
    c.step().unwrap();
    assert_eq!(c.r[1], 3);
    assert_eq!(c.irq_entries, 1);
    assert_eq!(c.sr[0], XIP + 12);
    c.bus.write(0x1eef1a4, 1, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 12);
    assert_eq!(c.r[1], 3);
}

#[test]
fn repeat_finishes_before_dispatching_a_pending_interrupt() {
    use fm1_emu::devices::{IRQ_CONFIG, TIMER5};
    for repeat in [0x8200, 0x0303] {
        let mut c = cpu(&[repeat, 0x0592, 0x0000, 0x0081]);
        c.r[1] = RAM;
        c.r[2] = 42;
        c.r[3] = 3;
        c.sr[14] = RAM + 256;
        c.sr[13] = RAM + 512;
        c.sr[11] = 0x100;
        c.bus.write(0x01c7fe00 + 63 * 4, XIP + 6, 4).unwrap();
        c.bus.write(IRQ_CONFIG + 7 * 4, 1 << 28, 4).unwrap();
        c.bus.write(TIMER5 + 8, 1, 4).unwrap();
        c.bus.write(TIMER5, 9, 4).unwrap();
        c.interrupts_enabled = true;
        c.bus.devices.advance(1); // Pending before REP; delivery must wait.
        c.step().unwrap();
        assert_eq!(c.pc, XIP + 2);
        assert_eq!(c.irq_entries, 0);
        c.step().unwrap();
        c.step().unwrap();
        assert_eq!(c.pc, XIP + 2);
        assert_eq!(c.irq_entries, 0);
        c.step().unwrap();
        assert_eq!(c.pc, XIP + 6);
        assert_eq!(c.irq_entries, 1);
        assert_eq!(c.sr[0], XIP + 4); // RETI is after the complete repeat.
        c.bus.write(TIMER5, 0x4000, 4).unwrap();
        c.step().unwrap(); // rti
        assert_eq!(c.pc, XIP + 4);
        assert_eq!(c.r[1], RAM + 12);
        assert_eq!(c.bus.read(RAM + 8, 4).unwrap(), 42);
        assert_eq!(c.r[3], if repeat == 0x0303 { 0 } else { 3 });
    }
}

#[test]
fn atomic_lock_flags_match_twelve_physical_fm1_measurements() {
    for (byte, flags, taken) in [
        (0, 0, false),
        (1, 1, false),
        (0x80, 0, false),
        (0xff, 15, true),
        (2, 2, false),
        (4, 4, true),
        (8, 8, false),
        (15, 15, true),
        (0x7f, 15, true),
        (0xfe, 14, true),
        (0xf0, 0, false),
        (0x10, 0, false),
    ] {
        let mut c = cpu(&[0x00b1, 0xe840, 0x0002, 0x0000, 0x0000, 0x0000]);
        c.r[1] = RAM + 1;
        c.bus.write(RAM, 0x44332211, 4).unwrap();
        c.bus.write(RAM + 1, byte, 1).unwrap();
        c.step().unwrap();
        assert_eq!(c.sr[5], flags);
        assert_eq!(c.bus.read(RAM, 4).unwrap(), 0x4433ff11);
        assert_eq!(c.r[1], RAM + 1);
        c.step().unwrap();
        assert_eq!(c.pc, if taken { XIP + 10 } else { XIP + 6 });
    }
}

#[test]
fn conditional_skip_counts_the_whole_long_call() {
    // if (r5 < 5) { call ... } else { r0=22 }.
    let mut c = cpu(&[0xe9b5, 0x1005, 0xff80, 0x00b0, 0x0000, 0x3640]);
    c.r[5] = 5;
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 10);
    c.step().unwrap();
    assert_eq!(c.r[0], 22);
    assert_eq!(c.sr[3], 0);
}
#[test]
fn compiler_parallel_store_uses_the_previous_register_value() {
    // Vendor compiler startup: r0 = 0x4009 # [r1+4] = r0.
    let mut c = cpu(&[0xf040, 0x4009, 0x6190]);
    c.r[0] = 17;
    c.r[1] = RAM;
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM + 4, 4).unwrap(), 17);
    assert_eq!(c.r[0], 0x4009);
    assert_eq!(c.pc, XIP + 6);
}

#[test]
fn parallel_add_reads_the_old_value_of_the_register_loaded_by_the_other_slot() {
    // cv_text: r1 += r0 # r0 = [sp].
    let mut c = cpu(&[0xd801, 0x2000]);
    c.r[0] = 0x3900;
    c.r[1] = 0x0205c9f0;
    c.sr[14] = RAM;
    c.bus.write(RAM, 0x0205c930, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.r[1], 0x020602f0);
    assert_eq!(c.r[0], 0x0205c930);
    assert_eq!(c.pc, XIP + 4);
}
#[test]
fn compiler_signed_immediates_terminate_decimal_printing() {
    let mut c = cpu(&[0xe040, 0xa240, 0xf8f5, 0xfffe]);
    c.step().unwrap();
    assert_eq!(c.r[0], (-24000i32) as u32);
    c.r[5] = u32::MAX;
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 8);
}
#[test]
fn conditional_block_executes_only_the_selected_arm() {
    // if ((r3 & 0x8000)==0) { r0=1 } else { r0=2 }
    for (condition, expected) in [(0, 1), (0x8000, 2)] {
        let mut c = cpu(&[0xea23, 0x1c00, 0x2140, 0x2240, 0x0000]);
        c.r[3] = condition;
        c.step().unwrap();
        c.step().unwrap();
        c.step().unwrap();
        assert_eq!(c.r[0], expected);
        assert_eq!(c.pc, XIP + 10);
    }
}

#[test]
fn conditional_memset_alignment_counts_a_parallel_pair_once() {
    for offset in [0, 1, 2, 3] {
        // Stock memset alignment loop; subtract and store form one bundle.
        let mut c = cpu(&[
            0x5202, 0xea33, 0x4003, 0xf0f2, 0x2001, 0x07b1, 0x99f7, 0x2144,
        ]);
        c.r[2] = 16;
        c.r[3] = RAM + offset;
        c.r[1] = 0xa5;
        while c.pc < XIP + 14 {
            c.step().unwrap();
            assert!(c.steps < 20);
        }
        let filled = (4 - offset) % 4;
        assert_eq!(c.r[2], 16 - filled);
        assert_eq!(c.r[3], RAM + offset + filled);
        for i in 0..filled {
            assert_eq!(c.bus.read(RAM + offset + i, 1).unwrap(), 0xa5);
        }
        assert_eq!(c.bus.read(RAM + offset + filled, 1).unwrap(), 0);
    }
}

#[test]
fn unsigned_immediate_conditional_selects_storage_header_region() {
    // Vendor form from st_head: if (r5 < 5) { r0=11 } else { r0=22 }.
    for (value, expected) in [(0, 11), (4, 11), (5, 22), (u32::MAX, 22)] {
        let mut c = cpu(&[0xe9b5, 0x1005, 0x2b40, 0x3640, 0x0000]);
        c.r[5] = value;
        for _ in 0..3 {
            c.step().unwrap();
        }
        assert_eq!(c.r[0], expected);
        assert_eq!(c.pc, XIP + 10);
    }
}

#[test]
fn register_list_loads_ascend_without_changing_the_base() {
    // Vendor form: 04 eb 04 01 => {r8, r2} = [r4+].
    for upper in [4, 8, 15] {
        let mut c = cpu(&[0xeb04, (1 << upper) | (1 << 2)]);
        c.r[4] = RAM;
        c.bus.write(RAM, 0x11223344, 4).unwrap();
        c.bus.write(RAM + 4, 0xaabbccdd, 4).unwrap();
        c.step().unwrap();
        assert_eq!(c.r[2], 0x11223344);
        assert_eq!(c.r[upper], 0xaabbccdd);
        if upper != 4 {
            assert_eq!(c.r[4], RAM);
        }
    }
}

#[test]
fn stock_list_insertions_keep_a_circular_queue_linked_to_its_head() {
    // Stock 0x02003338 stores {r2,r1}, then updates the old tail's next link.
    let mut c = cpu(&[0xeb20, 6, 0x60a0]);
    let head = RAM + 64;
    let nodes = [RAM + 128, RAM + 156];
    c.bus.write(head, head, 4).unwrap();
    for (node, tail) in [(nodes[0], head), (nodes[1], nodes[0])] {
        c.pc = XIP;
        c.r[0] = node;
        c.r[1] = head;
        c.r[2] = tail;
        c.step().unwrap();
        c.step().unwrap();
    }
    assert_eq!(c.bus.read(head, 4).unwrap(), nodes[0]);
    assert_eq!(c.bus.read(nodes[0], 4).unwrap(), nodes[1]);
    assert_eq!(c.bus.read(nodes[1], 4).unwrap(), head);
    assert_eq!(c.bus.read(nodes[1] + 4, 4).unwrap(), nodes[0]);
}

#[test]
fn register_pairs_move_both_words_without_clobbering_the_source() {
    for destination in (0..16).step_by(2) {
        for source in (0..16).step_by(2) {
            let mut c = cpu(&[0x1500 | (source as u16) << 4 | destination as u16]);
            c.r = std::array::from_fn(|i| 0x12345600 + i as u32);
            let before = c.r;
            c.step().unwrap();
            assert_eq!(
                c.r[destination..destination + 2],
                before[source..source + 2]
            );
            assert_eq!(c.pc, XIP + 2);
        }
    }
}

#[test]
fn signed_division_truncates_toward_zero_and_rejects_undefined_cases() {
    for (left, right, expected) in [(128, 2, 64), (-9, 2, -4), (9, -2, -4), (-9, -2, 4)] {
        let mut c = cpu(&[0xe1f4, 0x0101]); // r0 = r0 / r1 (s)
        c.r[0] = left as u32;
        c.r[1] = right as u32;
        c.step().unwrap();
        assert_eq!(c.r[0], expected as u32);
    }
    for (left, right) in [(1, 0), (i32::MIN, -1)] {
        let mut c = cpu(&[0xe1f4, 0x0101]);
        c.r[0] = left as u32;
        c.r[1] = right as u32;
        assert!(c.step().is_err());
    }
}

#[test]
fn three_operand_subtraction_preserves_the_text_canvas_address() {
    for (left, right, expected) in [(RAM + 128, 4, RAM + 124), (0, 1, u32::MAX)] {
        let mut c = cpu(&[0xe0b4, 0x00c2]); // r0 = r12 - r0
        c.r[12] = left;
        c.r[0] = right;
        c.step().unwrap();
        assert_eq!(c.r[0], expected);
        assert_eq!(c.r[12], left);
    }
}

#[test]
fn halfword_postincrement_reads_before_advancing_parameter_pointer() {
    let mut c = cpu(&[0xedd0, 0x2104]); // r2 = h[r0++=20] (u)
    c.r[0] = RAM;
    c.bus.write(RAM, 0xfedc, 2).unwrap();
    c.bus.write(RAM + 20, 0x1234, 2).unwrap();
    c.step().unwrap();
    assert_eq!(c.r[2], 0xfedc);
    assert_eq!(c.r[0], RAM + 20);
}

#[test]
fn stock_pixel_stores_advance_by_two_bytes_without_loading_the_buffer() {
    // Stock 0x02012724: h[r13++=2] = r0; bit 0 is direction.
    let mut c = cpu(&[0xedd0, 0x00d3, 0xedd0, 0x00d3]);
    c.r[13] = RAM + 8;
    c.r[0] = 0x1234f800;
    c.sr[5] = 15;
    c.bus.write(RAM + 8, 0xabcdef12, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.r[0], 0x1234f800);
    assert_eq!(c.r[13], RAM + 10);
    assert_eq!(c.bus.read(RAM + 8, 4).unwrap(), 0xabcdf800);
    c.r[0] = 0x07e0;
    c.step().unwrap();
    assert_eq!(c.r[13], RAM + 12);
    assert_eq!(c.bus.read(RAM + 8, 4).unwrap(), 0x07e0f800);
    assert_eq!(c.sr[5], 15);
    assert!(cpu(&[0xedd4, 0x00d3]).step().is_err());
}

#[test]
fn fx_signed_halfword_load_reads_before_advancing_the_parameter_pointer() {
    for (value, expected) in [
        (0, 0),
        (0x7fff, 0x7fff),
        (0x8000, 0xffff8000),
        (0xffff, u32::MAX),
    ] {
        let mut c = cpu(&[0xedd4, 0x4062]); // r4 = h[r6++=2] (s), Felucca graph_fx
        c.r[6] = RAM;
        c.bus.write(RAM, value, 2).unwrap();
        c.bus.write(RAM + 2, 0x1234, 2).unwrap();
        c.step().unwrap();
        assert_eq!(c.r[4], expected);
        assert_eq!(c.r[6], RAM + 2);
        assert_eq!(c.pc, XIP + 4);
        assert_eq!(c.bus.read(RAM, 2).unwrap(), value);
        assert_eq!(c.bus.read(RAM + 2, 2).unwrap(), 0x1234);
    }
}

#[test]
fn single_return_address_push_and_pop_restore_the_call_target() {
    let mut c = cpu(&[0x0410, 0x0400]);
    c.sr[14] = RAM + 16;
    c.sr[3] = XIP + 0x200;
    c.step().unwrap();
    assert_eq!(c.sr[14], RAM + 12);
    assert_eq!(c.bus.read(RAM + 12, 4).unwrap(), XIP + 0x200);
    assert_eq!(c.sr[3], XIP + 0x200);
    c.step().unwrap();
    assert_eq!(c.pc, XIP + 0x200);
    assert_eq!(c.sr[14], RAM + 16);
}

#[test]
fn register_preincrement_store_advances_the_mixer_buffer_pointer() {
    let mut c = cpu(&[0xecdc, 0x5013]); // [++r1=r0] = r5
    c.r[0] = 4;
    c.r[1] = RAM;
    c.r[5] = 0x11223344;
    c.step().unwrap();
    assert_eq!(c.r[1], RAM + 4);
    assert_eq!(c.bus.read(RAM + 4, 4).unwrap(), 0x11223344);
    assert_eq!(c.bus.read(RAM, 4).unwrap(), 0);
}

#[test]
fn byte_postincrement_store_updates_the_event_cursor() {
    let mut c = cpu(&[0xeed2, 0x2011]); // b[r1++=1] = r2
    c.r[1] = RAM;
    c.r[2] = 0x12345678;
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM, 1).unwrap(), 0x78);
    assert_eq!(c.bus.read(RAM + 1, 1).unwrap(), 0);
    assert_eq!(c.r[1], RAM + 1);
}
#[test]
fn immediate_arithmetic_shift_extends_the_sign_in_mixer_interpolation() {
    for (value, expected) in [(65536, 2), (-65536, -2), (-1, -1)] {
        let mut c = cpu(&[0xaf88]); // r0 = r0 >>> 15
        c.r[0] = value as u32;
        c.step().unwrap();
        assert_eq!(c.r[0], expected as u32);
    }
}

#[test]
fn register_arithmetic_shift_preserves_the_synth_envelope_sign() {
    // Stock 0x01C02028: r0 = r14 >>> r0. Capture the incoming count
    // before replacing its aliased destination with the envelope value.
    for (value, shift, expected) in [
        (0x80000000, 0, 0x80000000),
        (0x80000000, 1, 0xc0000000),
        (0xfffff000, 12, u32::MAX),
        (0x7fffffff, 31, 0),
        (0x80000000, 31, u32::MAX),
        (0x80000000, 32, u32::MAX),
        (0x80000000, u32::MAX, u32::MAX),
    ] {
        let mut c = cpu(&[0xe1c8, 0x00e3]);
        c.r[0] = shift;
        c.r[14] = value;
        c.sr[5] = 15;
        c.step().unwrap();
        assert_eq!(c.r[0], expected);
        assert_eq!(c.r[14], value);
        assert_eq!(c.sr[5], 15);
    }
}

#[test]
fn extended_halfword_load_separates_sign_extension_from_the_offset() {
    for (h, expected) in [(0xed51, 0xfedc), (0xed55, 0xfffffedc)] {
        let mut c = cpu(&[h, 0x120c]); // r1 = h[r0+300] (u/s)
        c.r[0] = RAM + 1024;
        c.bus.write(RAM + 1324, 0xfedc, 2).unwrap();
        c.bus.write(RAM + 300, 0x1234, 2).unwrap();
        c.step().unwrap();
        assert_eq!(c.r[1], expected);
        assert_eq!(c.r[0], RAM + 1024);
    }
    let mut c = cpu(&[0xed51, 0x1e85]); // h[r8+484] = r1
    c.r[8] = RAM;
    c.r[1] = 0xabcdef12;
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM + 484, 2).unwrap(), 0xef12);
}

#[test]
fn halfword_loads_sign_extend_addresses_independently_of_pixel_values() {
    // Vendor assembler encodings include both ends of the signed ten-bit
    // displacement and the stock LVGL renderer's negative preincrements.
    for (h, x, offset) in [
        (0xed52, 0x5080, -512),
        (0xed52, 0x5d84, -300),
        (0xed53, 0x5f8c, -4),
        (0xed53, 0x5f8e, -2),
        (0xed50, 0x5080, 0),
        (0xed50, 0x5082, 2),
        (0xed51, 0x528c, 300),
        (0xed51, 0x5f8e, 510),
    ] {
        for (sign, expected) in [(0, 0xfedc), (4, 0xfffffedc)] {
            for update in [0, 8] {
                let mut c = cpu(&[h | sign | update, x]);
                let base = RAM + 1024;
                let address = base.wrapping_add(offset as u32);
                c.r[8] = base;
                c.bus.write(address, 0xfedc, 2).unwrap();
                c.step().unwrap();
                assert_eq!(c.r[5], expected);
                assert_eq!(c.r[8], if update == 0 { base } else { address });
                assert_eq!(c.bus.read(address, 2).unwrap(), 0xfedc);
                assert_eq!(c.pc, XIP + 4);
            }
        }
    }
}

#[test]
fn stock_pixel_blending_keeps_writes_inside_the_frame_buffer() {
    // 0x0201282C loads the previous pixel and updates its pointer; the
    // final store at 0x020128B4 must use that same address, not r8+508.
    let mut c = cpu(&[0xed5b, 0x5f8c, 0xed50, 0x1081]);
    let pixel = RAM + 1024;
    c.r[8] = pixel + 4;
    c.r[1] = 0xe000;
    c.bus.write(pixel, 0xffff, 2).unwrap();
    c.bus.write(pixel + 512, 1, 2).unwrap(); // Audio run flag in stock.
    c.step().unwrap();
    assert_eq!(c.r[5], 0xffff);
    assert_eq!(c.r[8], pixel);
    c.step().unwrap();
    assert_eq!(c.bus.read(pixel, 2).unwrap(), 0xe000);
    assert_eq!(c.bus.read(pixel + 512, 2).unwrap(), 1);
}

#[test]
fn stock_name_comparison_loads_a_byte_from_a_negative_offset() {
    // Vendor stock instruction at 0x020035c4: r3 = b[r3+-18] (u).
    let mut c = cpu(&[0xee51, 0x3e3e]);
    c.r[3] = RAM + 32;
    c.bus.write(RAM + 14, 0xff, 1).unwrap();
    c.bus.write(RAM + 32, 0x12, 1).unwrap();
    c.bus.write(RAM + 270, 0x34, 1).unwrap();
    c.step().unwrap();
    assert_eq!(c.r[3], 255);
    assert_eq!(c.pc, XIP + 4);
    assert_eq!(c.bus.read(RAM + 14, 1).unwrap(), 0xff);
    assert_eq!(c.bus.read(RAM + 32, 1).unwrap(), 0x12);
}

#[test]
fn stock_formatter_sign_extends_bytes_at_negative_offsets() {
    // Vendor r3 encodings: r0=b[r5-1/-256](s), r3=b[r3-18](s).
    for (word, base, destination, offset) in [
        (0x0f5f, 5, 0, -1i32),
        (0x0050, 5, 0, -256),
        (0x3e3e, 3, 3, -18),
    ] {
        for (byte, expected) in [(0x7f, 127), (0x80, 0xffffff80), (0xff, u32::MAX)] {
            let mut c = cpu(&[0xee55, word]);
            c.r[base] = RAM + 512;
            c.sr[5] = 15;
            c.bus
                .write((RAM + 512).wrapping_add(offset as u32), byte, 1)
                .unwrap();
            c.step().unwrap();
            assert_eq!(c.r[destination], expected);
            if base != destination {
                assert_eq!(c.r[base], RAM + 512);
            }
            assert_eq!(c.sr[5], 15);
        }
    }
}

#[test]
fn stock_formatter_rotates_bits_before_classifying_format_characters() {
    // Compiler-generated (v>>1)|(v<<31) and assembler count 31/32.
    for (word, source, destination, value, expected) in [
        (0x0001, 0, 0, 4, 2),
        (0x0001, 0, 0, 11, 0x80000005),
        (0x315f, 5, 3, 0x80000001, 3),
        (0x0010, 1, 0, 0x89abcdef, 0x89abcdef),
    ] {
        let mut c = cpu(&[0xe1c4, word]);
        c.r[source] = value;
        c.sr[5] = 15;
        c.step().unwrap();
        assert_eq!(c.r[destination], expected);
        assert_eq!(c.sr[5], 15);
        assert_eq!(c.pc, XIP + 4);
    }
    assert!(cpu(&[0xe1c4, 0x0201]).step().is_err());
}

#[test]
fn signed_minimum_clips_audio_with_signed_comparison() {
    for (left, right, expected) in [(-10, 32767, -10), (40000, 32767, 32767), (-10, -20, -20)] {
        let mut c = cpu(&[0xe435, 0x0031]); // r0 = smin(r3, r0)
        c.r[3] = left as u32;
        c.r[0] = right as u32;
        c.step().unwrap();
        assert_eq!(c.r[0], expected as u32);
    }
}

#[test]
fn absolute_and_maximum_compute_the_audio_peak() {
    for (value, expected) in [(-123, 123), (123, 123), (i32::MIN, i32::MIN)] {
        let mut c = cpu(&[0xe430, 0x1500]); // r1 = abs(r5)
        c.r[5] = value as u32;
        c.step().unwrap();
        assert_eq!(c.r[1], expected as u32);
    }
    for (mode, expected) in [(0, u32::MAX), (1, 123)] {
        let mut c = cpu(&[0xe434, 0x1130 | mode]); // r1 = u/smax(r3, r1)
        c.r[3] = u32::MAX;
        c.r[1] = 123;
        c.step().unwrap();
        assert_eq!(c.r[1], expected);
    }
}

#[test]
fn halfword_register_preincrement_reads_signed_lookup_values() {
    let mut c = cpu(&[0xeddc, 0x3312]); // r3 = h[++r1=r3] (s)
    c.r[1] = 4;
    c.r[3] = RAM;
    c.bus.write(RAM + 4, 0xfedc, 2).unwrap();
    c.step().unwrap();
    assert_eq!(c.r[1], RAM + 4);
    assert_eq!(c.r[3], 0xfffffedc);
}

#[test]
fn memory_shift_scales_the_stereo_output_word() {
    let mut c = cpu(&[0xe86c, 0x3704]); // [r3+4] <<= 7
    c.r[3] = RAM;
    c.bus.write(RAM, 123, 4).unwrap();
    c.bus.write(RAM + 4, (-100i32) as u32, 4).unwrap();
    c.step().unwrap();
    assert_eq!(c.bus.read(RAM + 4, 4).unwrap(), (-12800i32) as u32);
    assert_eq!(c.bus.read(RAM, 4).unwrap(), 123);
    assert_eq!(c.r[3], RAM);
}

#[test]
fn byte_and_halfword_stack_accesses_preserve_adjacent_fields() {
    let mut c = cpu(&[
        0xe9de, 0x814a, 0xe9d8, 0x8149, 0xe9dd, 0x114a, 0xe9d9, 0x2148,
    ]);
    c.sr[14] = RAM;
    c.r[8] = 0xabcdfedc;
    c.bus.write(RAM + 328, 0x11223344, 4).unwrap();
    c.step().unwrap(); // b[sp+330] = r8
    c.step().unwrap(); // h[sp+328] = r8
    assert_eq!(c.bus.read(RAM + 328, 4).unwrap(), 0x11dcfedc);
    c.step().unwrap(); // r1 = b[sp+330] (s)
    c.step().unwrap(); // r2 = h[sp+328] (s)
    assert_eq!(c.r[1], 0xffffffdc);
    assert_eq!(c.r[2], 0xfffffedc);
    assert_eq!(c.sr[14], RAM);
}

#[test]
fn register_pair_clear_uses_the_encoded_even_register() {
    for destination in (0..16).step_by(2) {
        let mut c = cpu(&[0x1480 | destination as u16]);
        c.r = std::array::from_fn(|i| 100 + i as u32);
        let mut expected = c.r;
        expected[destination..destination + 2].fill(0);
        c.step().unwrap();
        assert_eq!(c.r, expected);
    }
}

#[test]
fn extended_byte_postincrement_reads_before_advancing_the_string() {
    for (h, expected) in [(0xeed0, 0xdc), (0xeed4, 0xffffffdc)] {
        let mut c = cpu(&[h, 0x9001]); // r9 = b[r0++=1] (u/s)
        c.r[0] = RAM;
        c.bus.write(RAM, 0xdc, 1).unwrap();
        c.bus.write(RAM + 1, 0x12, 1).unwrap();
        c.step().unwrap();
        assert_eq!(c.r[9], expected);
        assert_eq!(c.r[0], RAM + 1);
    }
}

#[test]
fn signed_immediate_conditional_adds_a_minus_only_for_negative_numbers() {
    for (value, expected_pc) in [
        (-1, XIP + 4),
        (-120, XIP + 4),
        (0, XIP + 10),
        (120, XIP + 10),
    ] {
        let mut c = cpu(&[0xeeb1, 0x8fff, 0x2140, 0, 0, 0]); // ifs (r1 <= -1) { 3 instructions }
        c.r[1] = value as u32;
        c.step().unwrap();
        assert_eq!(c.pc, expected_pc);
    }
}

#[test]
fn packed_subtraction_centers_the_oscillator_waveform() {
    for (value, expected) in [(32768, 0), (0, 0xffff8000), (65535, 32767)] {
        let mut c = cpu(&[0xe0f4, 0x4c00]); // r4 = r4 - 0x8000
        c.r[4] = value;
        c.step().unwrap();
        assert_eq!(c.r[4], expected);
    }
}

fn p33(c: &mut Bus, command: u8, address: u8, value: u8) {
    c.write(0x13e08, 1, 4).unwrap();
    for byte in [command, address, value] {
        c.write(0x13e0c, byte as u32, 4).unwrap();
        c.write(0x13e08, 17, 4).unwrap();
    }
    c.write(0x13e08, 0, 4).unwrap();
}
#[test]
fn watchdog_is_fed_via_serial_p33_and_really_expires() {
    let mut b = Bus::new(vec![0, 0]).unwrap();
    p33(&mut b, 0, 0x80, 0x1a);
    b.system.advance(23_999_999).unwrap();
    p33(&mut b, 0x20, 0x80, 0x40);
    assert_eq!(b.system.watchdog_feeds, 1);
    b.system.advance(23_999_999).unwrap();
    assert!(b.system.advance(1).is_err());
}

#[test]
fn p33_rtc_registers_are_separate_from_the_watchdog_domain() {
    let mut b = Bus::new(vec![0, 0]).unwrap();
    p33(&mut b, 0, 0x80, 0x1a);
    for (command, value) in [(0, 0x40), (0x80, 0)] {
        b.write(0x13e08, 0x101, 4).unwrap();
        for byte in [command, 0x80, value] {
            b.write(0x13e0c, byte as u32, 4).unwrap();
            b.write(0x13e08, 0x111, 4).unwrap();
        }
        b.write(0x13e08, 0, 4).unwrap();
    }
    assert_eq!(b.read(0x13e0c, 4).unwrap(), 0x40);
    assert_eq!(b.system.watchdog_feeds, 0);
    p33(&mut b, 0x80, 0x80, 0);
    assert_eq!(b.read(0x13e0c, 4).unwrap(), 0x1a);
}
#[test]
fn guarded_ram_rejects_writes_and_usb_dma_checks_its_address() {
    let mut b = Bus::new(vec![0, 0]).unwrap();
    b.write(0x1eee2c0, RAM, 4).unwrap();
    b.write(0x1eee280, RAM + 255, 4).unwrap();
    b.write(0x1eee348, 1, 4).unwrap();
    assert!(b.write(RAM, 0, 4).is_err());
    b.write(RAM + 256, 0, 4).unwrap();
    b.write(0x11800, 4, 4).unwrap();
    b.write(0x51000, 0x40, 4).unwrap();
    b.advance_usb(1).unwrap();
    assert!(b.advance_usb(120000).is_err());
}

#[test]
fn stock_protection_setup_acknowledges_events_without_enabling_sdram() {
    let mut b = Bus::new(vec![0, 0]).unwrap();
    b.write(0x13400, 0x6d, 4).unwrap();
    assert_eq!(b.read(0x13400, 4).unwrap(), 0x2d);
    b.write(0x40438, 0, 4).unwrap();
    assert!(b.write(0x40438, 1, 4).is_err());
    b.write(0x1eef2d4, u32::MAX, 4).unwrap();
    assert_eq!(b.read(0x1eef2d4, 4).unwrap(), 0);
}

#[test]
fn floating_branches_match_the_fm1_994_capture_without_changing_psr() {
    for (lhs, rhs, greater) in [
        (0x3e449ba6, 0x3f800000, false),
        (0x3f800000, 0x3e449ba6, true),
        (0xbfc00000, 0xbf800000, false),
        (0xbf800000, 0xbfc00000, true),
        (0, 0x80000000, false),
        (0x80000000, 0, false),
        (0x3fc00000, 0x3fc00000, false),
        (0xbfc00000, 0x3fc00000, false),
    ] {
        for (h, taken) in [(0xee02, greater), (0xee82, !greater)] {
            let mut c = cpu(&[h, 0x1801]);
            c.r[1] = lhs;
            c.r[2] = rhs;
            c.sr[5] = 15;
            let registers = c.r;
            c.step().unwrap();
            assert_eq!(c.pc, XIP + if taken { 6 } else { 4 });
            assert_eq!(c.r, registers);
            assert_eq!(c.sr[5], 15);
        }
    }
    // The stock form uses r14 and a longer displacement; backward offsets
    // must still be sign-extended as nine-bit halfword displacements.
    for (x, expected) in [(0xe8a5, XIP + 334), (0xe9fd, XIP - 2)] {
        let mut c = cpu(&[0xee81, x]);
        c.r[14] = 0x3e449ba6;
        c.r[1] = 0x3f800000;
        c.step().unwrap();
        assert_eq!(c.pc, expected);
    }
    for exceptional in [0x7fc00000, 0x7f800000, 0xff800000] {
        let mut c = cpu(&[0xee82, 0x1801]);
        c.r[1] = exceptional;
        assert!(c.step().is_err());
    }
}

#[test]
fn floating_pitch_conditions_compare_values_instead_of_signed_bits() {
    // Vendor r3 encodings from the stock voice pitch path. Negative floats
    // distinguish float ordering from the signed-integer interpretation.
    for (lhs, rhs, greater, greater_equal) in [
        (0xc3000000, 0xc2fe0000, false, false), // -128 < -127
        (0xc2fe0000, 0xc3000000, true, true),
        (0xbf800000, 0xbf800000, false, true),
        (0, 0x80000000, false, true),
        (0x80000000, 0, false, true),
        (0x42fe0000, 0x43000000, false, false), // 127 < 128
        (0x43000000, 0x42fe0000, true, true),
    ] {
        for (h, taken) in [(0xed02, greater_equal), (0xed82, !greater_equal)] {
            let mut c = cpu(&[h, 0x182a]); // iff (r1 >=/u< r2) goto 84
            c.r[1] = lhs;
            c.r[2] = rhs;
            c.sr[5] = 15;
            let registers = c.r;
            c.step().unwrap();
            assert_eq!(c.pc, XIP + if taken { 88 } else { 4 });
            assert_eq!(c.r, registers);
            assert_eq!(c.sr[5], 15);
        }
        for (h, taken) in [
            (0xed11, greater_equal),
            (0xed91, !greater_equal),
            (0xee11, greater),
            (0xee91, !greater),
        ] {
            let mut c = cpu(&[h, 0x0280, 0x2143, 0]); // iff (...) { r3=1 }
            c.r[1] = lhs;
            c.r[2] = rhs;
            c.sr[5] = 15;
            c.step().unwrap();
            c.step().unwrap();
            assert_eq!(c.r[3], u32::from(taken));
            assert_eq!(c.r[1], lhs);
            assert_eq!(c.r[2], rhs);
            assert_eq!(c.sr[5], 15);
            assert_eq!(c.pc, XIP + if taken { 6 } else { 8 });
        }
    }
    for h in [0xed02, 0xed82, 0xed11, 0xed91, 0xee11, 0xee91] {
        let mut c = cpu(&[h, if h & 0x10 == 0 { 0x182a } else { 0x0280 }]);
        c.r[1] = 0x7fc00000;
        assert!(c.step().is_err());
    }
    let mut c = cpu(&[0xed11, 0x0200, 0x2143, 0]); // integer mode: ifs
    c.r[1] = 0xc3000000;
    c.r[2] = 0xc2fe0000;
    c.step().unwrap();
    c.step().unwrap();
    assert_eq!(c.r[3], 1); // Opposite ordering for signed integer bits.
}

#[test]
fn float_arithmetic_and_conversions_match_the_fm1_986_capture() {
    // Physical results: 2026-10-05, finite cases, interrupts suppressed.
    // The cancellation cases distinguish rounded MAC from a fused operation.
    for (x, a, b, accumulator, expected) in [
        (0x3210, 0x3fc00000, 0x40100000, 0x3f800000, 0x40700000),
        (0x3210, 0x3f800001, 0x3f7ffffe, 0xbf800000, 0x40000000),
        (0x3211, 0x3fc00000, 0x40100000, 0x3f800000, 0xbf400000),
        (0x3211, 0x3f800001, 0x3f7ffffe, 0xbf800000, 0x34800000),
        (0x3212, 0x3fc00000, 0x40100000, 0x3f800000, 0x40580000),
        (0x3212, 0x3f800001, 0x3f7ffffe, 0xbf800000, 0x3f800000),
        (0x3213, 0x3fc00000, 0x40100000, 0x3f800000, 0x3f2aaaab),
        (0x3213, 0x3f800001, 0x3f7ffffe, 0xbf800000, 0x3f800002),
        (0x3217, 0x3fc00000, 0x40100000, 0x3f800000, 0x408c0000),
        (0x3217, 0x3f800001, 0x3f7ffffe, 0xbf800000, 0x00000000),
        (0x3218, 0x3fc00000, 0x40100000, 0x3f800000, 0xc0180000),
        (0x3218, 0x3f800001, 0x3f7ffffe, 0xbf800000, 0xc0000000),
        (0x318f, 0x00000000, 0x00000000, 0x00000000, 0x00000000),
        (0x318f, 0x00000001, 0x00000000, 0x00000000, 0x3f800000),
        (0x318f, 0xffffffff, 0x00000000, 0x00000000, 0xbf800000),
        (0x318f, 0x80000000, 0x00000000, 0x00000000, 0xcf000000),
        (0x318f, 0x01000001, 0x00000000, 0x00000000, 0x4b800000),
        (0x319f, 0x00000000, 0x00000000, 0x00000000, 0x00000000),
        (0x319f, 0x00000001, 0x00000000, 0x00000000, 0x3f800000),
        (0x319f, 0xffffffff, 0x00000000, 0x00000000, 0x4f800000),
        (0x319f, 0x80000000, 0x00000000, 0x00000000, 0x4f000000),
        (0x319f, 0x01000001, 0x00000000, 0x00000000, 0x4b800000),
        (0x311f, 0x3fc00000, 0x00000000, 0x00000000, 0x00000001),
        (0x311f, 0x40f00000, 0x00000000, 0x00000000, 0x00000007),
        (0x311f, 0x4effffff, 0x00000000, 0x00000000, 0x7fffff80),
        (0x311f, 0x00000000, 0x00000000, 0x00000000, 0x00000000),
        (0x315f, 0x3fc00000, 0x00000000, 0x00000000, 0x00000001),
        (0x315f, 0x40f00000, 0x00000000, 0x00000000, 0x00000007),
        (0x315f, 0x4effffff, 0x00000000, 0x00000000, 0x7fffff80),
        (0x315f, 0x00000000, 0x00000000, 0x00000000, 0x00000000),
    ] {
        let mut c = cpu(&[0xe53f, x]);
        c.r[1] = a;
        c.r[2] = b;
        c.r[3] = accumulator;
        c.sr[5] = 0;
        c.step().unwrap();
        assert_eq!(
            c.r[3], expected,
            "FP encoding {x:04x}, inputs {a:08x}/{b:08x}"
        );
        assert_eq!(c.sr[5], 0);
        assert_eq!(c.pc, XIP + 4);
    }
}

#[test]
fn masked_call_frame_restores_noncontiguous_registers_and_returns() {
    let mut c = cpu(&[0xe8d9, 0x0df0, 0xe8d5, 0x0df0]);
    c.sr[14] = RAM + 128;
    c.sr[3] = XIP + 8;
    for i in 0..16 {
        c.r[i] = 0x12340000 + i as u32;
    }
    c.step().unwrap();
    assert_eq!(c.sr[14], RAM + 96);
    assert_eq!(c.bus.read(RAM + 96, 4).unwrap(), 0x12340004);
    assert_eq!(c.bus.read(RAM + 120, 4).unwrap(), 0x1234000b);
    assert_eq!(c.bus.read(RAM + 124, 4).unwrap(), XIP + 8);
    c.r.fill(0);
    c.step().unwrap();
    for i in 0..16 {
        assert_eq!(
            c.r[i],
            if 0x0df0 & (1 << i) != 0 {
                0x12340000 + i as u32
            } else {
                0
            }
        );
    }
    assert_eq!(c.sr[14], RAM + 128);
    assert_eq!(c.pc, XIP + 8);
}

#[test]
fn float_min_max_and_comparison_flags_match_the_fm1_986_capture() {
    // Physical finite, negative, equal, and signed-zero results, 2026-10-05.
    for (x, a, b, expected, flags) in [
        (0x3215, 0x3fc00000, 0x40100000, 0x3fc00000, 8),
        (0x3215, 0x3f800001, 0x3f7ffffe, 0x3f7ffffe, 2),
        (0x3216, 0x3fc00000, 0x40100000, 0x40100000, 8),
        (0x3216, 0x3f800001, 0x3f7ffffe, 0x3f800001, 2),
        (0x3215, 0xbfc00000, 0xc0100000, 0xc0100000, 2),
        (0x3215, 0x80000000, 0x00000000, 0x80000000, 6),
        (0x3215, 0x00000000, 0x80000000, 0x80000000, 6),
        (0x3215, 0x3fc00000, 0xc0100000, 0xc0100000, 2),
        (0x3215, 0x3fc00000, 0x3fc00000, 0x3fc00000, 6),
        (0x3216, 0xbfc00000, 0xc0100000, 0xbfc00000, 2),
        (0x3216, 0x80000000, 0x00000000, 0x00000000, 6),
        (0x3216, 0x00000000, 0x80000000, 0x00000000, 6),
        (0x3216, 0x3fc00000, 0xc0100000, 0x3fc00000, 2),
        (0x3216, 0x3fc00000, 0x3fc00000, 0x3fc00000, 6),
    ] {
        let mut c = cpu(&[0xe53f, x]);
        c.r[1] = a;
        c.r[2] = b;
        c.sr[5] = 0xabc0000f;
        c.step().unwrap();
        assert_eq!(
            c.r[3], expected,
            "FP encoding {x:04x}, inputs {a:08x}/{b:08x}"
        );
        assert_eq!(c.sr[5], 0xabc00000 | flags);
    }
}
