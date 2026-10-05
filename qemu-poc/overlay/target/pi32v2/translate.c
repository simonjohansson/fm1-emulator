/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Independently written fixture subset. Encoding source: pinned Apache-2.0
 * Quarkslab SLEIGH, checked against the saved vendor disassembly.
 * No instruction interpreter or host machine-code emitter is embedded. */
#include "qemu/osdep.h"
#include "cpu.h"
#include "tcg/tcg-op.h"
#include "exec/translator.h"
#include "exec/helper-proto.h"
#include "exec/helper-gen.h"

typedef struct PiDisasContext {
    DisasContextBase base;
    CPUPi32v2State *env;
    uint32_t stop;
} PiDisasContext;
static TCGv_i32 gpr[16], spr[16], pc;
static TCGv_i64 instructions;
#define DISAS_EXIT DISAS_TARGET_0

void pi32v2_translate_init(void)
{
    static const char *const names[] = {
        "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
        "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15",
        "reti", "rete", "retx", "rets", "sr4", "psr", "cnum", "sr7",
        "sr8", "sr9", "sr10", "icfg", "usp", "ssp", "sp", "sr15",
    };
    for (int i = 0; i < 16; i++) {
        gpr[i] = tcg_global_mem_new_i32(tcg_env, offsetof(CPUPi32v2State, gpr[i]), names[i]);
        spr[i] = tcg_global_mem_new_i32(tcg_env, offsetof(CPUPi32v2State, spr[i]), names[i + 16]);
    }
    pc = tcg_global_mem_new_i32(tcg_env, offsetof(CPUPi32v2State, pc), "pc");
    instructions = tcg_global_mem_new_i64(tcg_env, offsetof(CPUPi32v2State, instructions), "instructions");
}

static int32_t sext(uint32_t n, unsigned bits)
{
    return (int32_t)(n << (32 - bits)) >> (32 - bits);
}
static uint16_t fetch(PiDisasContext *d, uint32_t addr)
{
    return translator_lduw(d->env, &d->base, addr);
}
static void count(void) { tcg_gen_addi_i64(instructions, instructions, 1); }
static void push(TCGv_i32 value)
{
    tcg_gen_subi_i32(spr[SP], spr[SP], 4);
    tcg_gen_qemu_st_i32(value, spr[SP], 0, MO_LEUL | MO_ALIGN);
}
static void pop(TCGv_i32 value)
{
    tcg_gen_qemu_ld_i32(value, spr[SP], 0, MO_LEUL | MO_ALIGN);
    tcg_gen_addi_i32(spr[SP], spr[SP], 4);
}
static void jump(PiDisasContext *d, uint32_t dest, int slot)
{
    if (translator_use_goto_tb(&d->base, dest)) {
        tcg_gen_goto_tb(slot);
        tcg_gen_movi_i32(pc, dest);
        tcg_gen_exit_tb(d->base.tb, slot);
    } else {
        tcg_gen_movi_i32(pc, dest);
        tcg_gen_exit_tb(NULL, 0);
    }
}
static void dynamic_jump(PiDisasContext *d, TCGv_i32 value)
{
    tcg_gen_mov_i32(pc, value);
    tcg_gen_exit_tb(NULL, 0);
    d->base.is_jmp = DISAS_NORETURN;
}
static void branch(PiDisasContext *d, uint32_t dest, uint32_t next,
                   TCGv_i32 value, bool nonzero)
{
    TCGLabel *taken = gen_new_label();
    tcg_gen_brcondi_i32(nonzero ? TCG_COND_NE : TCG_COND_EQ, value, 0, taken);
    jump(d, next, 0);
    gen_set_label(taken);
    jump(d, dest, 1);
    d->base.is_jmp = DISAS_NORETURN;
}
static void init_disas(DisasContextBase *db, CPUState *cs)
{
    PiDisasContext *d = container_of(db, PiDisasContext, base);
    d->env = cpu_env(cs);
    d->stop = PI32V2_CPU(cs)->stop_pc;
}
static void tb_start(DisasContextBase *db, CPUState *cs) {}
static void insn_start(DisasContextBase *db, CPUState *cs)
{
    tcg_gen_insn_start(db->pc_next);
}

static void translate_insn(DisasContextBase *db, CPUState *cs)
{
    PiDisasContext *d = container_of(db, PiDisasContext, base);
    uint32_t here = db->pc_next, next = here + 2;
    uint16_t op;
    int a, b;
    tcg_gen_movi_i32(pc, here);
    if (here == d->stop) {
        gen_helper_pi32v2_finish(tcg_env);
        db->is_jmp = DISAS_NORETURN;
        db->pc_next = next;
        return;
    }
    op = fetch(d, here);
    a = op & 7;
    b = (op >> 4) & 7;

    if ((op & 0xfff0) == 0xffc0 || (op & 0xfff0) == 0xffe0) {
        uint32_t value = fetch(d, here + 2) | ((uint32_t)fetch(d, here + 4) << 16);
        unsigned reg = op & 15;
        if ((op & 0xfff0) == 0xffe0 && reg != SP && reg != SSP && reg != USP && reg != RETI) {
            goto illegal;
        }
        tcg_gen_movi_i32((op & 0x20) ? spr[reg] : gpr[reg], value);
        next = here + 6;
    } else if (op == 0xe040 || (op & 0xfff0) == 0xe040) {
        tcg_gen_movi_i32(gpr[op & 15], (int16_t)fetch(d, here + 2));
        next = here + 4;
    } else if (op == 0xe060) {
        uint16_t x = fetch(d, here + 2);
        unsigned mode = (x >> 10) & 3;
        uint32_t value;
        if (!mode) {
            static const uint32_t repeat[] = {1, 0x00010001, 0x01000100, 0x01010101};
            value = (x & 255) * repeat[(x >> 8) & 3];
        } else {
            value = ((uint32_t)(0x80 | (x & 127)) << (32 - mode * 8)) >> ((x >> 7) & 7);
        }
        tcg_gen_movi_i32(gpr[x >> 12], value);
        next = here + 4;
    } else if (op == 0xe064) {
        uint16_t x = fetch(d, here + 2);
        unsigned reg = x >> 12, special = (x >> 8) & 15;
        if (special == 15 || ((x & 255) != 0 && (x & 255) != 128)) { goto illegal; }
        tcg_gen_mov_i32((x & 128) ? spr[special] : gpr[reg],
                       (x & 128) ? gpr[reg] : spr[special]);
        next = here + 4;
        /* Writing ICFG can make an already asserted IRQ deliverable. */
        if (special == ICFG && (x & 128)) { db->is_jmp = DISAS_EXIT; }
    } else if ((op & 0xe0c0) == 0x2040) {
        tcg_gen_movi_i32(gpr[a], ((op >> 8) & 31) | (((op >> 3) & 7) << 5));
    } else if ((op & 0xe0f8) == 0x2010) {
        tcg_gen_movi_i32(gpr[a], 0xffffffe0u | ((op >> 8) & 31));
    } else if ((op & 0xff00) == 0x1600) {
        tcg_gen_mov_i32(gpr[op & 15], gpr[(op >> 4) & 15]);
    } else if ((op & 0xfe00) == 0x1c00 || (op & 0xfe00) == 0x1e00) {
        unsigned c = ((op >> 7) & 3) * 2 + ((op >> 3) & 1);
        gen_helper_pi32v2_alu(gpr[a], tcg_env, gpr[b], gpr[c], tcg_constant_i32((op & 0x200) != 0));
    } else if ((op & 0xe0c0) == 0x20c0) {
        int imm = sext(((op >> 8) & 31) | (((op >> 3) & 7) << 5), 8);
        gen_helper_pi32v2_alu(gpr[a], tcg_env, gpr[a], tcg_constant_i32(imm), tcg_constant_i32(0));
    } else if ((op & 0xe088) == 0x8008) {
        gen_helper_pi32v2_alu(gpr[a], tcg_env, gpr[b], tcg_constant_i32((op >> 8) & 31), tcg_constant_i32(0));
    } else if ((op & 0xff88) == 0x1900) {
        tcg_gen_or_i32(gpr[a], gpr[a], gpr[b]);
    } else if ((op & 0xff88) == 0x1908) {
        tcg_gen_xor_i32(gpr[a], gpr[a], gpr[b]);
    } else if ((op & 0xff88) == 0x1980) {
        tcg_gen_and_i32(gpr[a], gpr[a], gpr[b]);
    } else if ((op & 0xff88) == 0x1988) {
        tcg_gen_not_i32(gpr[a], gpr[b]);
    } else if ((op & 0xe008) == 0xa000) {
        if (op & 128) { tcg_gen_shri_i32(gpr[a], gpr[b], (op >> 8) & 31); }
        else { tcg_gen_shli_i32(gpr[a], gpr[b], (op >> 8) & 31); }
    } else if ((op & 0xe008) == 0x6000) {
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, gpr[b], sext((op >> 8) & 31, 5) * 4);
        if (op & 128) { tcg_gen_qemu_st_i32(gpr[a], addr, 0, MO_LEUL | MO_ALIGN); }
        else { tcg_gen_qemu_ld_i32(gpr[a], addr, 0, MO_LEUL | MO_ALIGN); }
    } else if (op == 0xe8d8 || op == 0xe8d4) {
        uint16_t mask = fetch(d, here + 2);
        if (op == 0xe8d8) {
            for (int i = 15; i >= 0; i--) { if (mask & (1 << i)) { push(gpr[i]); } }
        } else {
            for (int i = 0; i < 16; i++) { if (mask & (1 << i)) { pop(gpr[i]); } }
        }
        next = here + 4;
    } else if ((op & 0xfff0) == 0x0460 || (op & 0xfff0) == 0x0440) {
        unsigned boundary = op & 15;
        unsigned lo = boundary < 4 ? boundary : 4;
        unsigned hi = boundary < 4 ? 3 : boundary;
        if (op & 32) { for (int i = hi; i >= (int)lo; i--) { push(gpr[i]); } }
        else { for (unsigned i = lo; i <= hi; i++) { pop(gpr[i]); } }
    } else if (op == 0x04e9) {
        push(spr[PSR]); push(spr[RETS]); push(spr[RETI]);
    } else if (op == 0x04a9) {
        pop(spr[RETI]); pop(spr[RETS]); pop(spr[PSR]);
    } else if ((op & 0xffc0) == 0xea80) {
        int32_t delta = sext(((uint32_t)(op & 63) << 16) | fetch(d, here + 2), 22) * 2;
        next = here + 4;
        tcg_gen_movi_i32(spr[RETS], next);
        count(); jump(d, next + delta, 0); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xe00c) == 0x8004) {
        int32_t delta = sext(((op & 3) << 10) | (((op >> 4) & 15) << 6) | (((op >> 8) & 31) << 1), 12);
        count(); jump(d, next + delta, 0); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xe008) == 0x4000) {
        int32_t delta = sext((((op >> 4) & 7) << 6) | (((op >> 8) & 31) << 1), 9);
        count(); branch(d, next + delta, next, gpr[a], op & 128);
    } else if ((op & 0xfff0) == 0x00c0) {
        tcg_gen_movi_i32(spr[RETS], next);
        count(); dynamic_jump(d, gpr[op & 15]);
    } else if (op == 0x0080) {
        count(); dynamic_jump(d, spr[RETS]);
    } else if (op == 0x0081) {
        count(); gen_helper_pi32v2_rti(tcg_env); tcg_gen_exit_tb(NULL, 0);
        db->is_jmp = DISAS_NORETURN;
    } else if (op == 0x0060 || op == 0x0061) {
        if (op == 0x0060) { tcg_gen_andi_i32(spr[ICFG], spr[ICFG], ~0x200u); }
        else { tcg_gen_ori_i32(spr[ICFG], spr[ICFG], 0x200); }
        db->is_jmp = DISAS_EXIT;
    } else if (op != 0x0020 && op != 0x0000) {
        goto illegal;
    }

    if (db->is_jmp != DISAS_NORETURN) { count(); }
    db->pc_next = next;
    if (db->is_jmp == DISAS_NEXT && !translator_is_same_page(db, next + 5)) {
        db->is_jmp = DISAS_TOO_MANY;
    }
    return;
illegal:
    gen_helper_pi32v2_illegal(tcg_env, tcg_constant_i32(op));
    db->is_jmp = DISAS_NORETURN;
    db->pc_next = next;
}
static void tb_stop(DisasContextBase *db, CPUState *cs)
{
    if (db->is_jmp == DISAS_EXIT) {
        /* Re-evaluate a pending IRQ after its architectural mask changes. */
        tcg_gen_movi_i32(pc, db->pc_next);
        tcg_gen_exit_tb(NULL, 0);
    } else if (db->is_jmp != DISAS_NORETURN) {
        jump(container_of(db, PiDisasContext, base), db->pc_next, 0);
    }
}
static const TranslatorOps ops = {
    .init_disas_context = init_disas, .tb_start = tb_start,
    .insn_start = insn_start, .translate_insn = translate_insn, .tb_stop = tb_stop,
};
void pi32v2_translate_code(CPUState *cs, TranslationBlock *tb,
                          int *max_insns, vaddr start, void *host_pc)
{
    PiDisasContext d = {};
    translator_loop(cs, tb, max_insns, start, host_pc, &ops, &d.base);
}
