// SPDX-License-Identifier: GPL-3.0-only
// Decode instruction bytes, never guest addresses. Each execution still fetches
// its current words through the bus, including XIP checks and SRAM changes.
use std::sync::OnceLock;

#[derive(Clone, Copy)]
pub(crate) enum First {
    MoveImmediate32,
    RepeatRegister,
    RepeatImmediate,
    MovSpecial,
    MovMask,
    MovImm16,
    MovImm8,
    MovNegative,
    MovReg,
    Arithmetic,
    AddImm8,
    AddSp,
    AddSmall,
    Logic,
    Asr,
    ShiftImmediate,
    MemoryWord,
    StackMask,
    PushRegs,
    PopPc,
    PushRets,
    PushReti,
    PopReturnRegister,
    PopRegs,
    PushRetsRegs,
    PopRetsRegs,
    PopPcRegs,
    MoveStackPointer,
    PushIrqFrame,
    PopIrqFrame,
    CallRel32,
    Relative22,
    CallRel9,
    GotoRel12,
    BranchZero,
    TestsetByte,
    Return,
    CallReg,
    Rti,
    Cli,
    BusLock,
    Sti,
    Idle,
    SyncOrNop,
    Extended(Extended),
}

#[derive(Clone, Copy)]
pub(crate) enum Extended {
    MoveRegisterPair,
    Multiply,
    ClearPair,
    AddRegister,
    ClearHighRegister,
    CacheFlushInvalidate,
    StackWord,
    Extend,
    MoveNegative,
    BitRegister,
    ShiftRegister,
    AddStack,
    GotoRegister,
    Ssync,
    TableBranch,
    MemorySmall,
    MemoryPostincrementRegister,
    MemoryPostincrement,
    Wide,
    Unknown,
}

#[derive(Clone, Copy)]
pub(crate) enum Wide {
    FloatRegister,
    BranchRegisterMask,
    MemoryShift,
    MemoryArithmeticRegister,
    HalfwordExtended,
    HalfwordPostincrement,
    BytePostincrementStore,
    BytePostincrementLoad,
    MultiplyImmediate,
    BitMask,
    StackPair,
    ReverseBytes,
    SubtractPackedImmediate,
    ReverseSubtract,
    MultiplyExtended,
    MultiplyWide,
    DivideWide,
    ShiftWideRegister,
    ShiftWideImmediate,
    Divide,
    CountLeadingZeros,
    Absolute,
    Maximum,
    Minimum,
    MemoryAdd,
    DecrementBranch,
    MemoryBit,
    ArithmeticRegister,
    ShiftRegisterExtended,
    BytePreincrement,
    RotateRightImmediate,
    ShiftExtended,
    BranchLong,
    LogicThree,
    StoreRegisterList,
    LoadRegisterList,
    StackSubword,
    StackExtended,
    MemoryPair,
    BitField,
    BranchEqualFlag,
    BranchBit,
    MemoryMask,
    ConditionalBlock,
    MemoryLogic,
    LogicImmediate,
    StoreImmediate,
    AddImmediate,
    AddSpExtended,
    AddStackExtended,
    BranchCompareImmediate,
    BranchCompareFloat,
    BranchCompareRegister,
    ByteExtended,
    WordExtended,
    WordRegisterPreincrementStore,
    WordRegisterPreincrement,
    HalfwordRegisterPreincrement,
    WordPostincrementStore,
    WordPostincrementLoad,
    MemoryIndexed,
    Unknown,
}

fn first(h: u32) -> First {
    if matches!(h & 0xfff0, 0xffc0 | 0xffe0) {
        return First::MoveImmediate32;
    }
    if h & 0xff00 == 0x0300 {
        return First::RepeatRegister;
    }
    if h & 0xe00f == 0x8000 {
        return First::RepeatImmediate;
    }
    if h == 0xe064 {
        return First::MovSpecial;
    }
    if h == 0xe060 {
        return First::MovMask;
    }
    if h & 0xfff0 == 0xe040 {
        return First::MovImm16;
    }
    if h & 0xe0c0 == 0x2040 {
        return First::MovImm8;
    }
    if h & 0xe0f8 == 0x2010 {
        return First::MovNegative;
    }
    if h & 0xff00 == 0x1600 {
        return First::MovReg;
    }
    if matches!(h & 0xfe00, 0x1c00 | 0x1e00) {
        return First::Arithmetic;
    }
    if h & 0xe0c0 == 0x20c0 {
        return First::AddImm8;
    }
    if h & 0xe01f == 0x8002 {
        return First::AddSp;
    }
    if h & 0xe088 == 0x8008 {
        return First::AddSmall;
    }
    if matches!(h & 0xff88, 0x1900 | 0x1908 | 0x1980 | 0x1988) {
        return First::Logic;
    }
    if h & 0xe088 == 0xa088 {
        return First::Asr;
    }
    if h & 0xe008 == 0xa000 {
        return First::ShiftImmediate;
    }
    if h & 0xe008 == 0x6000 {
        return First::MemoryWord;
    }
    if matches!(h, 0xe8d4 | 0xe8d5 | 0xe8d8 | 0xe8d9) {
        return First::StackMask;
    }
    if h & 0xfff0 == 0x0460 {
        return First::PushRegs;
    }
    if h == 0x0400 {
        return First::PopPc;
    }
    if h == 0x0410 {
        return First::PushRets;
    }
    if h == 0x04c1 {
        return First::PushReti;
    }
    if matches!(h, 0x0481 | 0x0488) {
        return First::PopReturnRegister;
    }
    if h & 0xfff0 == 0x0440 {
        return First::PopRegs;
    }
    if h & 0xfff0 == 0x0470 && h & 15 >= 4 {
        return First::PushRetsRegs;
    }
    if h & 0xfff0 == 0x0430 && h & 15 >= 4 {
        return First::PopRetsRegs;
    }
    if h & 0xfff0 == 0x0450 && h & 15 >= 4 {
        return First::PopPcRegs;
    }
    if matches!(h, 0x1440..=0x1443) {
        return First::MoveStackPointer;
    }
    if matches!(h, 0x04e1 | 0x04e8 | 0x04e9) {
        return First::PushIrqFrame;
    }
    if matches!(h, 0x04a1 | 0x04a8 | 0x04a9) {
        return First::PopIrqFrame;
    }
    if h == 0xff80 {
        return First::CallRel32;
    }
    if matches!(h & 0xffc0, 0xea80 | 0xeac0) {
        return First::Relative22;
    }
    if h & 0xe08f == 0x8001 {
        return First::CallRel9;
    }
    if h & 0xe00c == 0x8004 {
        return First::GotoRel12;
    }
    if h & 0xe008 == 0x4000 {
        return First::BranchZero;
    }
    if h & 0xfff0 == 0x00b0 {
        return First::TestsetByte;
    }
    if h == 0x0080 {
        return First::Return;
    }
    if h & 0xfff0 == 0x00c0 {
        return First::CallReg;
    }
    if h == 0x0081 {
        return First::Rti;
    }
    if h == 0x0060 {
        return First::Cli;
    }
    if matches!(h, 0x0040 | 0x0041) {
        return First::BusLock;
    }
    if h == 0x0061 {
        return First::Sti;
    }
    if h == 0x0001 {
        return First::Idle;
    }
    if h == 0x0020 || h == 0x0000 {
        return First::SyncOrNop;
    }
    First::Extended(extended(h))
}

fn extended(h: u32) -> Extended {
    if h & 0xff11 == 0x1500 {
        return Extended::MoveRegisterPair;
    }
    if h & 0xff00 == 0x1b00 {
        return Extended::Multiply;
    }
    if h & 0xfff1 == 0x1480 {
        return Extended::ClearPair;
    }
    if h & 0xff00 == 0x1800 {
        return Extended::AddRegister;
    }
    if h & 0xfff8 == 0x14c0 {
        return Extended::ClearHighRegister;
    }
    if h & 0xfff0 == 0x0230 {
        return Extended::CacheFlushInvalidate;
    }
    if h & 0xe058 == 0x2000 {
        return Extended::StackWord;
    }
    if matches!(h & 0xff88, 0x1700 | 0x1708 | 0x1780 | 0x1788) {
        return Extended::Extend;
    }
    if h & 0xe0f8 == 0x2010 {
        return Extended::MoveNegative;
    }
    if matches!(h & 0xe0f8, 0x2030 | 0x2038 | 0x20b8) {
        return Extended::BitRegister;
    }
    if matches!(h & 0xff88, 0x1a00 | 0x1a80 | 0x1a88) {
        return Extended::ShiftRegister;
    }
    if h & 0xe098 == 0x8088 {
        return Extended::AddStack;
    }
    if h & 0xfff0 == 0x00d0 {
        return Extended::GotoRegister;
    }
    if h == 0x0022 {
        return Extended::Ssync;
    }
    if matches!(h & 0xfff0, 0x0100 | 0x0110) {
        return Extended::TableBranch;
    }
    if matches!(h & 0xe088, 0x4008 | 0x4088 | 0x6008 | 0x6088) {
        return Extended::MemorySmall;
    }
    if matches!(h & 0xfc00, 0x0800 | 0x0c00 | 0x1000) {
        return Extended::MemoryPostincrementRegister;
    }
    if (0x0500..0x0800).contains(&(h & 0xff80)) {
        return Extended::MemoryPostincrement;
    }
    if h >> 13 == 7 {
        return Extended::Wide;
    }
    Extended::Unknown
}

fn wide(h: u32, x: u32) -> Wide {
    let d = (x >> 12) as usize;
    let s = ((x >> 4) & 15) as usize;
    if h == 0xe53f {
        return Wide::FloatRegister;
    }
    if matches!(h & 0xff00, 0xfa00 | 0xfb00) {
        return Wide::BranchRegisterMask;
    }
    if matches!(h, 0xe86c | 0xe86d) && matches!(x & 3, 0 | 2 | 3) {
        return Wide::MemoryShift;
    }
    if h == 0xe868 && matches!(x & 3, 0 | 2) {
        return Wide::MemoryArithmeticRegister;
    }
    if h & 0xfff8 == 0xed50 || h & 0xfff8 == 0xed58 {
        return Wide::HalfwordExtended;
    }
    // imm10m2 offset in h[1:0] and x; x bit 0 = store, h bit 2 = signed load
    // (there is no signed store).
    if h & 0xfff8 == 0xedd0 && !(h & 4 != 0 && x & 1 != 0) {
        return Wide::HalfwordPostincrement;
    }
    // imm9m1 offset in h[0] and x; h bit 1 = store, h bit 2 = signed load.
    if h & 0xfffe == 0xeed2 {
        return Wide::BytePostincrementStore;
    }
    if matches!(h & 0xfffe, 0xeed0 | 0xeed4) {
        return Wide::BytePostincrementLoad;
    }
    if h & 0xfff0 == 0xe1e0 {
        return Wide::MultiplyImmediate;
    }
    if h == 0xe194 && x & 15 <= 3 {
        return Wide::BitMask;
    }
    if h == 0xe9d0 {
        return Wide::StackPair;
    }
    if h == 0xe070 && x & 255 == 0 {
        return Wide::ReverseBytes;
    }
    if h & 0xfff0 == 0xe0f0 {
        return Wide::SubtractPackedImmediate;
    }
    if h & 0xfff0 == 0xe0a0 {
        return Wide::ReverseSubtract;
    }
    if h == 0xe1f0 && x & 15 == 0 {
        return Wide::MultiplyExtended;
    }
    if matches!(h, 0xe1f8 | 0xe1fc) && x & 15 == 0 {
        return Wide::MultiplyWide;
    }
    // Vendor-exhaustive: x & 0x1f == 0; the destination's low bit selects signed.
    if h == 0xe1f6 && s & 1 == 0 && x & 15 == 0 {
        return Wide::DivideWide;
    }
    if h == 0xe1d8 && d & 1 == 0 && s == 0 && matches!(x & 15, 0 | 2) {
        return Wide::ShiftWideRegister;
    }
    if h == 0xe1d0 && d & 1 == 0 && s == 0 && (x >> 10) & 3 != 1 {
        return Wide::ShiftWideImmediate;
    }
    if h == 0xe1f4 && x & 15 <= 1 {
        return Wide::Divide;
    }
    if h == 0xe180 && x & 255 == 0 {
        return Wide::CountLeadingZeros;
    }
    if h == 0xe430 && x & 255 == 0 {
        return Wide::Absolute;
    }
    if h == 0xe434 && x & 15 <= 1 {
        return Wide::Maximum;
    }
    if h == 0xe435 && x & 15 <= 1 {
        return Wide::Minimum;
    }
    if h & 0xffe0 == 0xebc0 {
        return Wide::MemoryAdd;
    }
    if h & 0xfff0 == 0xea00 {
        return Wide::DecrementBranch;
    }
    if h == 0xe866 {
        return Wide::MemoryBit;
    }
    if matches!(h, 0xe0b4 | 0xe0b8) && matches!(x & 15, 0 | 2) {
        return Wide::ArithmeticRegister;
    }
    if h == 0xe1c8 {
        return Wide::ShiftRegisterExtended;
    }
    if h == 0xeedc {
        return Wide::BytePreincrement;
    }
    if h == 0xe1c4 && x & 0x0e00 == 0 {
        return Wide::RotateRightImmediate;
    }
    if h == 0xe1c0 && (x >> 10) & 3 != 1 {
        return Wide::ShiftExtended;
    }
    if h & 0xff80 == 0xff00 && matches!(h & 15, 0 | 1 | 2 | 3 | 8 | 9 | 10 | 11 | 12 | 13) {
        return Wide::BranchLong;
    }
    if h == 0xe190 && x & 15 <= 3 {
        return Wide::LogicThree;
    }
    if h & 0xfff0 == 0xeb20 && x != 0 {
        return Wide::StoreRegisterList;
    }
    if h & 0xfff0 == 0xeb00 && x != 0 {
        return Wide::LoadRegisterList;
    }
    if matches!(h, 0xe9d8 | 0xe9d9 | 0xe9dc | 0xe9dd | 0xe9de) {
        return Wide::StackSubword;
    }
    if h == 0xe9d4 {
        return Wide::StackExtended;
    }
    if h & 0xfff8 == 0xec50 && x & 3 <= 1 {
        return Wide::MemoryPair;
    }
    if matches!(h & 0xfff0, 0xe1a0 | 0xe1b0) {
        return Wide::BitField;
    }
    if h == 0xe840 {
        return Wide::BranchEqualFlag;
    }
    if h & 0xfff0 == 0xe850 {
        return Wide::BranchBit;
    }
    if matches!(h & 0xffe0, 0xef00 | 0xef80 | 0xefc0) {
        return Wide::MemoryMask;
    }
    if matches!(
        (h >> 4) & 255,
        0x81 | 0x82
            | 0x83
            | 0x89
            | 0x8a
            | 0x8b
            | 0x91
            | 0x92
            | 0x93
            | 0x99
            | 0x9b
            | 0xa1
            | 0xa2
            | 0xa3
            | 0xc1
            | 0xc3
            | 0xc9
            | 0xca
            | 0xcb
            | 0xd1
            | 0xd2
            | 0xd3
            | 0xd9
            | 0xda
            | 0xdb
            | 0xe1
            | 0xe2
            | 0xe3
            | 0xe9
            | 0xeb
    ) && h & 0xf000 == 0xe000
    {
        return Wide::ConditionalBlock;
    }
    if h == 0xe864 {
        return Wide::MemoryLogic;
    }
    if matches!(h & 0xfff0, 0xe140 | 0xe150 | 0xe160 | 0xe170) {
        return Wide::LogicImmediate;
    }
    if h & 0xffe0 == 0xea40 {
        return Wide::StoreImmediate;
    }
    if matches!(h & 0xfff0, 0xe100 | 0xe110 | 0xe120 | 0xe130 | 0xe0e0) {
        return Wide::AddImmediate;
    }
    if h == 0xe8f0 && x & 0xe003 == 0 {
        return Wide::AddSpExtended;
    }
    if h == 0xe8f8 {
        return Wide::AddStackExtended;
    }
    if h & 0xff80 == 0xf800
        || h & 0xff80 == 0xf880
        || matches!(
            h & 0xff80,
            0xf900 | 0xf980 | 0xfc00 | 0xfc80 | 0xfd00 | 0xfd80 | 0xfe00 | 0xfe80
        )
    {
        return Wide::BranchCompareImmediate;
    }
    if matches!(h & 0xfff0, 0xed00 | 0xed80 | 0xee00 | 0xee80) && x & 0xe00 == 0x800 {
        return Wide::BranchCompareFloat;
    }
    if matches!(
        h & 0xfff0,
        0xe800 | 0xe880 | 0xe900 | 0xe980 | 0xec00 | 0xec80 | 0xed00 | 0xed80 | 0xee00 | 0xee80
    ) && x & 0xe00 == 0
    {
        return Wide::BranchCompareRegister;
    }
    if matches!(
        h,
        0xee50 | 0xee51 | 0xee52 | 0xee54 | 0xee55 | 0xee58 | 0xee5a
    ) {
        return Wide::ByteExtended;
    }
    if h & 0xfff8 == 0xecd0 {
        return Wide::WordExtended;
    }
    if h == 0xecdc && x & 15 == 3 {
        return Wide::WordRegisterPreincrementStore;
    }
    if h == 0xecdc && x & 15 == 2 {
        return Wide::WordRegisterPreincrement;
    }
    if h == 0xeddc && matches!(x & 15, 0..=2) {
        return Wide::HalfwordRegisterPreincrement;
    }
    if h == 0xecd8 && x & 3 == 1 {
        return Wide::WordPostincrementStore;
    }
    if h == 0xecd8 && x & 3 == 0 {
        return Wide::WordPostincrementLoad;
    }
    if matches!(h, 0xecd8 | 0xedd8 | 0xeed8) {
        return Wide::MemoryIndexed;
    }
    Wide::Unknown
}

static FIRST: OnceLock<Box<[First; 65536]>> = OnceLock::new();

#[derive(Clone, Copy)]
struct Entry {
    word: u32,
    kind: Wide,
    valid: bool,
}

pub(crate) struct Cache {
    first: &'static [First; 65536],
    wide: Box<[Entry]>,
}

impl Cache {
    pub(crate) fn new() -> Self {
        Self {
            first: FIRST.get_or_init(|| {
                // Allocate directly on the heap, including on Windows threads
                // with smaller stacks. The table is shared by all CPU instances.
                (0..65536)
                    .map(first)
                    .collect::<Vec<_>>()
                    .into_boxed_slice()
                    .try_into()
                    .unwrap_or_else(|_| unreachable!("all halfwords are present"))
            }),
            wide: vec![
                Entry {
                    word: 0,
                    kind: Wide::Unknown,
                    valid: false
                };
                4096
            ]
            .into_boxed_slice(),
        }
    }
    #[inline]
    pub(crate) fn first(&self, h: u32) -> First {
        if h <= u16::MAX as u32 {
            self.first[h as usize]
        } else {
            first(h) // Preserve the existing handling of wide MMIO read values.
        }
    }
    #[inline]
    pub(crate) fn wide(&mut self, h: u32, x: u32) -> Wide {
        if h > u16::MAX as u32 || x > u16::MAX as u32 {
            return wide(h, x);
        }
        let word = h << 16 | x;
        let index = (word.wrapping_mul(0x9e3779b9) >> 20) as usize;
        let entry = &mut self.wide[index];
        if !entry.valid || entry.word != word {
            *entry = Entry {
                word,
                kind: wide(h, x),
                valid: true,
            };
        }
        entry.kind
    }
}
