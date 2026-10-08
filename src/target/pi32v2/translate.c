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
    bool count_enabled;
    TCGv_i32 inputs[16];
} PiDisasContext;
static TCGv_i32 gpr[16], spr[16], pc;
static TCGv_i64 instructions;
#define DISAS_EXIT DISAS_TARGET_0
static uint32_t instruction_end(PiDisasContext *d, uint32_t here);

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
static uint32_t packed_mask(uint16_t x)
{
    unsigned mode = (x >> 10) & 3;
    if (!mode) {
        static const uint32_t repeat[] = {1, 0x00010001, 0x01000100, 0x01010101};
        return (x & 255) * repeat[(x >> 8) & 3];
    }
    return ((uint32_t)(0x80 | (x & 127)) << (32 - mode * 8)) >> ((x >> 7) & 7);
}
static uint16_t fetch(PiDisasContext *d, uint32_t addr)
{
    return translator_lduw(d->env, &d->base, addr);
}
static void count(PiDisasContext *d)
{
    if (d->count_enabled) { tcg_gen_addi_i64(instructions, instructions, 1); }
}
static void set_call_return(PiDisasContext *d, uint32_t next)
{
    /* Retire the call at its sequential boundary before entering its callee;
     * the outgoing control-transfer target is separate. */
    gen_helper_pi32v2_call_return(spr[RETS], tcg_env, tcg_constant_i32(next));
}
static TCGv_i32 read_gpr(PiDisasContext *d, unsigned reg)
{
    return d->inputs[reg] ? d->inputs[reg] : gpr[reg];
}
static void check_memory_access(PiDisasContext *d, TCGv_i32 addr, MemOp op, bool write)
{
    gen_helper_pi32v2_access(tcg_env, addr, tcg_constant_i32(memop_size(op)), tcg_constant_i32(write));
}
static void load(PiDisasContext *d, TCGv_i32 value, TCGv_i32 addr, MemOp op)
{
    check_memory_access(d, addr, op, false);
    tcg_gen_qemu_ld_i32(value, addr, 0, op);
}
static void store(PiDisasContext *d, TCGv_i32 value, TCGv_i32 addr, MemOp op)
{
    check_memory_access(d, addr, op, true);
    tcg_gen_qemu_st_i32(value, addr, 0, op);
}
static void record_branch(PiDisasContext *d)
{
    gen_helper_pi32v2_branch(tcg_env);
}
static TCGv_i32 bit_operand(TCGv_i32 index, uint16_t op)
{
    TCGLabel *valid = gen_new_label();
    tcg_gen_brcondi_i32(TCG_COND_LTU, index, 32, valid);
    gen_helper_pi32v2_illegal(tcg_env, tcg_constant_i32(op));
    gen_set_label(valid);
    TCGv_i32 mask = tcg_temp_new_i32();
    tcg_gen_shl_i32(mask, tcg_constant_i32(1), index);
    return mask;
}
static void push(PiDisasContext *d, TCGv_i32 value)
{
    tcg_gen_subi_i32(spr[SP], spr[SP], 4);
    store(d, value, spr[SP], MO_LEUL | MO_ALIGN);
}
static void pop(PiDisasContext *d, TCGv_i32 value)
{
    load(d, value, spr[SP], MO_LEUL | MO_ALIGN);
    tcg_gen_addi_i32(spr[SP], spr[SP], 4);
    gen_helper_pi32v2_access(tcg_env, spr[SP], tcg_constant_i32(0), tcg_constant_i32(4));
}
static void jump(uint32_t dest)
{
    gen_helper_pi32v2_advance(pc, tcg_env, tcg_constant_i32(dest));
    tcg_gen_exit_tb(NULL, 0);
}
/* Each static successor owns one QEMU chain slot. Active predicates keep
 * dispatcher exits so completion can redirect PC and admit pending IRQs. */
static void chain_jump(PiDisasContext *d, uint32_t dest, unsigned slot)
{
    DisasContextBase *db = &d->base;
    if (translator_use_goto_tb(db, dest)) {
        TCGLabel *conditional = gen_new_label();
        TCGv_i32 end = tcg_temp_new_i32();
        tcg_gen_ld_i32(end, tcg_env, offsetof(CPUPi32v2State, predicate_end));
        tcg_gen_brcondi_i32(TCG_COND_NE, end, 0, conditional);
        tcg_gen_movi_i32(pc, dest);
        tcg_gen_goto_tb(slot);
        tcg_gen_exit_tb(db->tb, slot);
        gen_set_label(conditional);
    }
    jump(dest);
}
static void dynamic_jump(PiDisasContext *d, TCGv_i32 value)
{
    record_branch(d);
    gen_helper_pi32v2_advance(pc, tcg_env, value);
    tcg_gen_exit_tb(NULL, 0);
    d->base.is_jmp = DISAS_NORETURN;
}
static void branch(PiDisasContext *d, uint32_t dest, uint32_t next,
                   TCGv_i32 value, bool nonzero)
{
    TCGLabel *taken = gen_new_label();
    tcg_gen_brcondi_i32(nonzero ? TCG_COND_NE : TCG_COND_EQ, value, 0, taken);
    chain_jump(d, next, 0);
    gen_set_label(taken);
    record_branch(d);
    chain_jump(d, dest, 1);
    d->base.is_jmp = DISAS_NORETURN;
}
static void compare_branch(PiDisasContext *d, uint32_t dest, uint32_t next,
                           TCGCond cond, TCGv_i32 left, TCGv_i32 right)
{
    TCGLabel *taken = gen_new_label();
    tcg_gen_brcond_i32(cond, left, right, taken);
    chain_jump(d, next, 0);
    gen_set_label(taken);
    record_branch(d);
    chain_jump(d, dest, 1);
    d->base.is_jmp = DISAS_NORETURN;
}
static void init_disas(DisasContextBase *db, CPUState *cs)
{
    PiDisasContext *d = container_of(db, PiDisasContext, base);
    d->env = cpu_env(cs);
    d->stop = PI32V2_CPU(cs)->stop_pc;
    d->count_enabled = true;
}
static void tb_start(DisasContextBase *db, CPUState *cs) {}
static void insn_start(DisasContextBase *db, CPUState *cs)
{
    tcg_gen_insn_start(db->pc_next, 0, 0);
}

static uint32_t decode_operation(PiDisasContext *d, uint32_t here, uint16_t op)
{
    DisasContextBase *db = &d->base;
    uint32_t next = here + 2;
    unsigned a = op & 7, b = (op >> 4) & 7;
    if ((op & 0xfff0) == 0xffc0 || (op & 0xfff0) == 0xffe0) {
        uint32_t value = fetch(d, here + 2) | ((uint32_t)fetch(d, here + 4) << 16);
        unsigned reg = op & 15;
        if ((op & 0xfff0) == 0xffe0 && reg != SP && reg != SSP && reg != USP && reg != RETI) {
            goto illegal;
        }
        tcg_gen_movi_i32((op & 0x20) ? spr[reg] : gpr[reg], value);
        next = here + 6;
    } else if (op == 0xe040 || (op & 0xfff0) == 0xe040) {
        /* Vendor disassembly (E04A FFFF -> r10 = -1) and the external
         * oracle agree on sign extension; SLEIGH's movz label conflicts. */
        tcg_gen_movi_i32(gpr[op & 15], (int16_t)fetch(d, here + 2));
        next = here + 4;
    } else if (op == 0xe060) {
        uint16_t x = fetch(d, here + 2);
        tcg_gen_movi_i32(gpr[x >> 12], packed_mask(x));
        next = here + 4;
    } else if (op == 0xe064) {
        uint16_t x = fetch(d, here + 2);
        unsigned reg = x >> 12, special = (x >> 8) & 15;
        if (special == 15 || ((x & 255) != 0 && (x & 255) != 128)) { goto illegal; }
        tcg_gen_mov_i32((x & 128) ? spr[special] : gpr[reg],
                       (x & 128) ? read_gpr(d, reg) : spr[special]);
        next = here + 4;
        /* Writing ICFG can make an already asserted IRQ deliverable. */
        if (special == ICFG && (x & 128)) { db->is_jmp = DISAS_EXIT; }
    } else if ((op & 0xe0c0) == 0x2040) {
        tcg_gen_movi_i32(gpr[a], ((op >> 8) & 31) | (((op >> 3) & 7) << 5));
    } else if ((op & 0xe0f8) == 0x2010) {
        tcg_gen_movi_i32(gpr[a], 0xffffffe0u | ((op >> 8) & 31));
    } else if ((op & 0xe0d0) == 0x2000 || (op & 0xe0d0) == 0x2080) {
        TCGv_i32 addr = tcg_temp_new_i32();
        /* Vendor 21A0/2120 at Felucca 0x0200cd2a/2c address SP+132:
         * bit 5 supplies the sixth unsigned word-offset bit. */
        tcg_gen_addi_i32(addr, spr[SP], (((op >> 8) & 31) | (op & 32)) * 4);
        if (op & 128) { store(d, read_gpr(d, op & 15), addr, MO_LEUL | MO_ALIGN); }
        else { load(d, gpr[op & 15], addr, MO_LEUL | MO_ALIGN); }
    } else if ((op & 0xe0f8) == 0x2030 || (op & 0xe0f8) == 0x2038 || (op & 0xe0f8) == 0x20b8) {
        uint32_t mask = 1u << ((op >> 8) & 31);
        if ((op & 0xf8) == 0x30) { tcg_gen_ori_i32(gpr[a], read_gpr(d, a), mask); }
        else if ((op & 0xf8) == 0x38) { tcg_gen_xori_i32(gpr[a], read_gpr(d, a), mask); }
        else { tcg_gen_andi_i32(gpr[a], read_gpr(d, a), ~mask); }
    } else if ((op & 0xff00) == 0x1500) {
        unsigned dest = op & 14, source = (op >> 4) & 14;
        if (op & 17) { goto illegal; }
        tcg_gen_mov_i32(gpr[dest], read_gpr(d, source));
        tcg_gen_mov_i32(gpr[dest + 1], read_gpr(d, source + 1));
    } else if ((op & 0xff00) == 0x1600) {
        tcg_gen_mov_i32(gpr[op & 15], read_gpr(d, (op >> 4) & 15));
    } else if ((op & 0xfff8) == 0x14c0) {
        tcg_gen_movi_i32(gpr[8 + (op & 7)], 0);
    } else if ((op & 0xfff0) == 0x1480) {
        unsigned reg = op & 14;
        if (op & 1) { goto illegal; }
        tcg_gen_movi_i32(gpr[reg], 0); tcg_gen_movi_i32(gpr[reg + 1], 0);
    } else if ((op & 0xff00) == 0x1700) {
        unsigned kind = (op >> 7) & 1;
        if (op & 8) {
            if (kind) { tcg_gen_ext16s_i32(gpr[a], read_gpr(d, b)); }
            else { tcg_gen_ext8s_i32(gpr[a], read_gpr(d, b)); }
        } else {
            tcg_gen_andi_i32(gpr[a], read_gpr(d, b), kind ? 0xffff : 0xff);
        }
    } else if ((op & 0xff00) == 0x1800) {
        gen_helper_pi32v2_alu(gpr[op & 15], tcg_env, read_gpr(d, op & 15),
                              read_gpr(d, (op >> 4) & 15), tcg_constant_i32(0));
    } else if ((op & 0xff88) == 0x1a00 || (op & 0xff88) == 0x1a80) {
        TCGv_i32 amount = read_gpr(d, b), result = tcg_temp_new_i32();
        TCGLabel *large = gen_new_label(), *end = gen_new_label();
        tcg_gen_brcondi_i32(TCG_COND_GEU, amount, 32, large);
        if (op & 128) { tcg_gen_shr_i32(result, read_gpr(d, a), amount); }
        else { tcg_gen_shl_i32(result, read_gpr(d, a), amount); }
        tcg_gen_br(end); gen_set_label(large); tcg_gen_movi_i32(result, 0);
        gen_set_label(end); tcg_gen_mov_i32(gpr[a], result);
    } else if ((op & 0xff88) == 0x1a88) {
        TCGv_i32 amount = read_gpr(d, b), result = tcg_temp_new_i32();
        TCGLabel *large = gen_new_label(), *end = gen_new_label();
        tcg_gen_brcondi_i32(TCG_COND_GEU, amount, 32, large);
        tcg_gen_sar_i32(result, read_gpr(d, a), amount);
        tcg_gen_br(end); gen_set_label(large);
        tcg_gen_sari_i32(result, read_gpr(d, a), 31);
        gen_set_label(end); tcg_gen_mov_i32(gpr[a], result);
    } else if ((op & 0xff00) == 0x1b00) {
        tcg_gen_mul_i32(gpr[op & 15], read_gpr(d, op & 15), read_gpr(d, (op >> 4) & 15));
    } else if ((op & 0xfe00) == 0x1c00 || (op & 0xfe00) == 0x1e00) {
        unsigned c = ((op >> 7) & 3) * 2 + ((op >> 3) & 1);
        gen_helper_pi32v2_alu(gpr[a], tcg_env, read_gpr(d, b), read_gpr(d, c), tcg_constant_i32((op & 0x200) != 0));
    } else if ((op & 0xe0c0) == 0x20c0) {
        int imm = sext(((op >> 8) & 31) | (((op >> 3) & 7) << 5), 8);
        gen_helper_pi32v2_alu(gpr[a], tcg_env, read_gpr(d, a), tcg_constant_i32(imm), tcg_constant_i32(0));
    } else if ((op & 0xe088) == 0x8008) {
        gen_helper_pi32v2_alu(gpr[a], tcg_env, read_gpr(d, b), tcg_constant_i32((op >> 8) & 31), tcg_constant_i32(0));
    } else if ((op & 0xe098) == 0x8088) {
        unsigned imm = (((op >> 5) & 3) << 5) | ((op >> 8) & 31);
        tcg_gen_addi_i32(gpr[a], spr[SP], imm);
    } else if ((op & 0xe01f) == 0x8002) {
        int32_t imm = sext((op >> 5) & 7, 3) * 128 + ((op >> 8) & 31) * 4;
        tcg_gen_addi_i32(spr[SP], spr[SP], imm);
        gen_helper_pi32v2_access(tcg_env, spr[SP], tcg_constant_i32(0), tcg_constant_i32(4));
    } else if ((op & 0xfff0) == 0xe160 || (op & 0xfff0) == 0xe170) {
        uint16_t x = fetch(d, here + 2);
        unsigned mode = (x >> 10) & 3;
        uint32_t mask = mode ? packed_mask(x) : x & 1023;
        tcg_gen_andi_i32(gpr[op & 15], read_gpr(d, x >> 12), op & 16 ? ~mask : mask);
        next = here + 4;
    } else if ((op & 0xfff0) == 0xe140 || (op & 0xfff0) == 0xe150) {
        uint16_t x = fetch(d, here + 2);
        if (op & 16) { tcg_gen_xori_i32(gpr[op & 15], read_gpr(d, x >> 12), packed_mask(x)); }
        else { tcg_gen_ori_i32(gpr[op & 15], read_gpr(d, x >> 12), packed_mask(x)); }
        next = here + 4;
    } else if (op == 0xe190 || op == 0xe194) {
        uint16_t x = fetch(d, here + 2);
        TCGv_i32 left = read_gpr(d, (x >> 4) & 15), right = read_gpr(d, (x >> 8) & 15);
        if (op == 0xe194) { right = bit_operand(right, op); }
        switch (x & 15) {
        case 0: tcg_gen_or_i32(gpr[x >> 12], left, right); break;
        case 1: tcg_gen_xor_i32(gpr[x >> 12], left, right); break;
        case 2: tcg_gen_and_i32(gpr[x >> 12], left, right); break;
        case 3: tcg_gen_andc_i32(gpr[x >> 12], left, right); break;
        default: goto illegal;
        }
        next = here + 4;
    } else if (op == 0xe070) {
        uint16_t x = fetch(d, here + 2);
        if (x & 255) { goto illegal; }
        tcg_gen_bswap32_i32(gpr[x >> 12], read_gpr(d, (x >> 8) & 15));
        next = here + 4;
    } else if (op == 0xe0b4) {
        uint16_t x = fetch(d, here + 2);
        if ((x & 15) != 0 && (x & 15) != 2) { goto illegal; }
        gen_helper_pi32v2_alu(gpr[x >> 12], tcg_env, read_gpr(d, (x >> 4) & 15),
                              read_gpr(d, (x >> 8) & 15), tcg_constant_i32((x & 15) == 2));
        next = here + 4;
    } else if (op == 0xe0b8) {
        uint16_t x = fetch(d, here + 2);
        if ((x & 15) != 0 && (x & 15) != 2) { goto illegal; }
        gen_helper_pi32v2_alu(gpr[x >> 12], tcg_env, read_gpr(d, (x >> 4) & 15),
                              read_gpr(d, (x >> 8) & 15), tcg_constant_i32((x & 15) == 2 ? 3 : 2));
        next = here + 4;
    } else if ((op & 0xfff0) == 0xe0a0) {
        uint16_t x = fetch(d, here + 2);
        gen_helper_pi32v2_alu(gpr[op & 15], tcg_env, tcg_constant_i32(packed_mask(x)),
                              read_gpr(d, x >> 12), tcg_constant_i32(1));
        next = here + 4;
    } else if ((op & 0xfff0) == 0xe0e0) {
        uint16_t x = fetch(d, here + 2);
        gen_helper_pi32v2_alu(gpr[op & 15], tcg_env, read_gpr(d, x >> 12),
                              tcg_constant_i32(packed_mask(x)), tcg_constant_i32(0));
        next = here + 4;
    } else if ((op & 0xfff0) == 0xe0f0) {
        uint16_t x = fetch(d, here + 2);
        gen_helper_pi32v2_alu(gpr[op & 15], tcg_env, read_gpr(d, x >> 12),
                              tcg_constant_i32(packed_mask(x)), tcg_constant_i32(1));
        next = here + 4;
    } else if ((op & 0xfff0) == 0xe1e0) {
        uint16_t x = fetch(d, here + 2);
        tcg_gen_muli_i32(gpr[op & 15], read_gpr(d, x >> 12), packed_mask(x));
        next = here + 4;
    } else if (op == 0xe430) {
        uint16_t x = fetch(d, here + 2);
        if (x & 255) { goto illegal; }
        tcg_gen_abs_i32(gpr[x >> 12], read_gpr(d, (x >> 8) & 15));
        next = here + 4;
    } else if (op == 0xe1f0 || op == 0xe1f4 || op == 0xe434 || op == 0xe435) {
        uint16_t x = fetch(d, here + 2);
        unsigned mode = x & 15;
        if (mode && ((op != 0xe1f4 && op != 0xe434 && op != 0xe435) || mode != 1)) { goto illegal; }
        TCGv_i32 left = read_gpr(d, (x >> 4) & 15), right = read_gpr(d, (x >> 8) & 15);
        if (op == 0xe1f0) { tcg_gen_mul_i32(gpr[x >> 12], left, right); }
        else if (op == 0xe1f4 && mode) { gen_helper_pi32v2_divs(gpr[x >> 12], tcg_env, left, right); }
        else if (op == 0xe1f4) { gen_helper_pi32v2_div(gpr[x >> 12], tcg_env, left, right); }
        else if (op == 0xe434 && mode == 1) { tcg_gen_smax_i32(gpr[x >> 12], left, right); }
        else if (op == 0xe434) { tcg_gen_umax_i32(gpr[x >> 12], left, right); }
        else if (op == 0xe435 && mode == 1) { tcg_gen_smin_i32(gpr[x >> 12], left, right); }
        else { tcg_gen_umin_i32(gpr[x >> 12], left, right); }
        next = here + 4;
    } else if (op == 0xe1c0) {
        uint16_t x = fetch(d, here + 2);
        unsigned mode = (x >> 10) & 3, shift = (x & 15) | ((x >> 8) & 3) * 16;
        if (mode == 1) { goto illegal; }
        if (shift >= 32 && mode != 3) { tcg_gen_movi_i32(gpr[x >> 12], 0); }
        else if (mode == 3) { tcg_gen_sari_i32(gpr[x >> 12], read_gpr(d, (x >> 4) & 15), MIN(shift, 31)); }
        else if (mode == 2) { tcg_gen_shri_i32(gpr[x >> 12], read_gpr(d, (x >> 4) & 15), shift); }
        else { tcg_gen_shli_i32(gpr[x >> 12], read_gpr(d, (x >> 4) & 15), shift); }
        next = here + 4;
    } else if (op == 0xe1c8) {
        uint16_t x = fetch(d, here + 2);
        unsigned mode = x & 15;
        if (mode != 0 && mode != 2 && mode != 3) { goto illegal; }
        TCGv_i32 shift = read_gpr(d, (x >> 8) & 15), src = read_gpr(d, (x >> 4) & 15);
        TCGv_i32 result = tcg_temp_new_i32();
        TCGLabel *large = gen_new_label(), *end = gen_new_label();
        tcg_gen_brcondi_i32(TCG_COND_GEU, shift, 32, large);
        if (mode == 0) { tcg_gen_shl_i32(result, src, shift); }
        else if (mode == 2) { tcg_gen_shr_i32(result, src, shift); }
        else { tcg_gen_sar_i32(result, src, shift); }
        tcg_gen_br(end);
        gen_set_label(large);
        if (mode == 3) { tcg_gen_sari_i32(result, src, 31); }
        else { tcg_gen_movi_i32(result, 0); }
        gen_set_label(end);
        tcg_gen_mov_i32(gpr[x >> 12], result);
        next = here + 4;
    } else if ((op & 0xfff0) == 0xe1a0 || (op & 0xfff0) == 0xe1b0) {
        uint16_t x = fetch(d, here + 2);
        unsigned pos = (x >> 7) & 31, len = (x >> 2) & 31;
        unsigned dst = op & 15;
        if ((x & 2) || !len || pos + len > 32) { goto illegal; }
        uint32_t mask = (1u << len) - 1;
        if ((op & 0xfff0) == 0xe1a0) {
            TCGv_i32 value = tcg_temp_new_i32();
            tcg_gen_shli_i32(value, read_gpr(d, x >> 12), pos);
            tcg_gen_andi_i32(value, value, mask << pos);
            tcg_gen_andi_i32(gpr[dst], read_gpr(d, dst), ~(mask << pos));
            tcg_gen_or_i32(gpr[dst], gpr[dst], value);
        } else {
            tcg_gen_shli_i32(gpr[dst], read_gpr(d, x >> 12), 32 - pos - len);
            if (x & 1) { tcg_gen_sari_i32(gpr[dst], gpr[dst], 32 - len); }
            else { tcg_gen_shri_i32(gpr[dst], gpr[dst], 32 - len); }
        }
        next = here + 4;
    } else if ((op & 0xfff0) == 0xea20 || (op & 0xfff0) == 0xea30 ||
               (op & 0xfff0) == 0xea10 ||
               (op & 0xfff0) == 0xe830 || (op & 0xfff0) == 0xe8b0 ||
               (op & 0xfff0) == 0xe810 || (op & 0xfff0) == 0xe890 ||
               (op & 0xfff0) == 0xe910 ||
               (op & 0xfff0) == 0xe930 || (op & 0xfff0) == 0xe9b0 ||
               (op & 0xfff0) == 0xec10 ||
               (op & 0xfff0) == 0xecb0 ||
               (op & 0xfff0) == 0xed30 || (op & 0xfff0) == 0xeeb0 ||
               (op & 0xfff0) == 0xed10 || (op & 0xfff0) == 0xee90 ||
               (op & 0xfff0) == 0xe920 || (op & 0xfff0) == 0xe990 ||
               (op & 0xfff0) == 0xec30 ||
               (op & 0xfff0) == 0xec90 || (op & 0xfff0) == 0xeca0 ||
               (op & 0xfff0) == 0xed20 || (op & 0xfff0) == 0xee30 ||
               (op & 0xfff0) == 0xe8a0) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = (op >> 4) & 255;
        TCGv_i32 left = read_gpr(d, op & 15), right, result = tcg_temp_new_i32();
        TCGCond cond;
        if (kind == 0xa1) {
            if (x & 127) { goto illegal; }
            TCGv_i32 masked = tcg_temp_new_i32();
            tcg_gen_and_i32(masked, left, read_gpr(d, (x >> 8) & 15));
            left = masked; right = tcg_constant_i32(0); cond = x & 128 ? TCG_COND_NE : TCG_COND_EQ;
        } else if (kind == 0xa2 || kind == 0xa3) {
            TCGv_i32 masked = tcg_temp_new_i32();
            tcg_gen_andi_i32(masked, left, packed_mask(x));
            left = masked; right = tcg_constant_i32(0);
            cond = kind == 0xa2 ? TCG_COND_EQ : TCG_COND_NE;
        } else if (kind == 0x92 || kind == 0xca || kind == 0xd2) {
            /* E920/ED20 have pinned packed constructors. Saved vendor
             * ECA1/0980 and complete-state oracle cases establish packed LE.
             * Preserve the existing repeated-byte expansion policy. */
            right = tcg_constant_i32(packed_mask(x));
            cond = kind == 0x92 ? TCG_COND_GEU :
                   kind == 0xca ? TCG_COND_LEU : TCG_COND_GE;
        } else if (kind == 0x8a) {
            /* Vendor E8A3/9000 and complete-state zero-operand cases
             * establish nonzero IF. Other operands select packed NE in the
             * oracle; that separate operand form remains unsupported. */
            if (x & 4095) { goto illegal; }
            right = tcg_constant_i32(0); cond = TCG_COND_NE;
        } else if (kind == 0xc3 || kind == 0xe3) {
            /* Full-state literal boundaries establish EC30 unsigned12,
             * contradicting the primary packed constructor. Vendor EE30/6FFF
             * and signed-boundary cases establish > -1; primary is absent. */
            right = tcg_constant_i32(kind == 0xe3 ? sext(x & 4095, 12) : x & 4095);
            cond = kind == 0xe3 ? TCG_COND_GT : TCG_COND_GTU;
        } else if (kind == 0x81 || kind == 0x89 || kind == 0x91 || kind == 0xc1 ||
                   kind == 0x99 || kind == 0xc9) {
            if (x & 255) { goto illegal; }
            right = read_gpr(d, (x >> 8) & 15);
            cond = kind == 0x81 ? TCG_COND_EQ : kind == 0x89 ? TCG_COND_NE :
                   kind == 0x91 ? TCG_COND_GEU : kind == 0xc1 ? TCG_COND_GTU :
                   kind == 0x99 ? TCG_COND_LTU : TCG_COND_LEU;
        } else if (kind == 0xd1) {
            /* Admit the constructor's canonical zero low byte. */
            if (x & 255) { goto illegal; }
            right = read_gpr(d, (x >> 8) & 15); cond = TCG_COND_GE;
        } else if (kind == 0xe9) {
            if (x & 255) { goto illegal; }
            right = read_gpr(d, (x >> 8) & 15); cond = TCG_COND_LE;
        } else if (kind == 0xcb) {
            /* Vendor-backed unsigned literals disagree with SLEIGH's packed
             * label (ECB0 0208 means 520). Keep all twelve literal bits. */
            right = tcg_constant_i32(x & 4095); cond = TCG_COND_LEU;
        } else if (kind == 0xd3) {
            /* Vendor ED31 0F00 selects >= -256; the pinned SLEIGH
             * constructor instead names the unsigned imm1627 token. */
            right = tcg_constant_i32(sext(x & 4095, 12)); cond = TCG_COND_GE;
        } else if (kind == 0xeb) {
            /* Vendor EEB2 4FFF selects <= -1; the pinned SLEIGH
             * constructor instead names the packedimm12 token. */
            right = tcg_constant_i32(sext(x & 4095, 12)); cond = TCG_COND_LE;
        } else {
            right = tcg_constant_i32(kind == 0x83 || kind == 0x8b ? sext(x & 4095, 12) : x & 4095);
            /* Vendor E9B5 1005 at Felucca 0x02004b30 selects r5 < 5. */
            cond = kind == 0x83 ? TCG_COND_EQ : kind == 0x8b ? TCG_COND_NE :
                   kind == 0x9b ? TCG_COND_LTU : TCG_COND_GEU;
        }
        tcg_gen_setcond_i32(cond, result, left, right);
        uint32_t then_end = here + 4, else_end;
        for (unsigned i = 0; i <= (x >> 14); i++) { then_end = instruction_end(d, then_end); }
        else_end = then_end;
        for (unsigned i = 0; i < ((x >> 12) & 3); i++) { else_end = instruction_end(d, else_end); }
        TCGv_i32 dest = tcg_temp_new_i32();
        gen_helper_pi32v2_if(dest, tcg_env, result, tcg_constant_i32(then_end), tcg_constant_i32(else_end));
        count(d);
        tcg_gen_mov_i32(pc, dest); tcg_gen_exit_tb(NULL, 0);
        db->is_jmp = DISAS_NORETURN;
        next = here + 4;
    } else if ((op & 0xfff0) == 0xeb20) {
        uint16_t bitmap = fetch(d, here + 2);
        if (!bitmap) { goto illegal; }
        /* Vendor traversal and independent probes store ascending selected
         * registers at the incoming base. The primary has no GPR writeback,
         * but its predecrement memory direction conflicts with this evidence. */
        translator_io_start(db);
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_mov_i32(addr, read_gpr(d, op & 15));
        for (unsigned reg = 0; reg < 16; reg++) {
            if (bitmap & (1u << reg)) {
                /* Earlier stores remain visible if a later word access fails.
                 * This sequential partial-fault order is model policy only. */
                store(d, read_gpr(d, reg), addr, MO_LEUL | MO_ALIGN);
                tcg_gen_addi_i32(addr, addr, 4);
            }
        }
        next = here + 4;
    } else if ((op & 0xffc0) == 0xea40) {
        uint16_t x = fetch(d, here + 2);
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, x >> 12), (op & 63) * 4);
        store(d, tcg_constant_i32(packed_mask(x)), addr, MO_LEUL | MO_ALIGN);
        next = here + 4;
    } else if ((op & 0xffe0) == 0xef00 || (op & 0xffe0) == 0xefc0 || op == 0xe864 || op == 0xe866) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 3;
        TCGv_i32 addr = tcg_temp_new_i32(), value = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, x >> 12), op == 0xe864 || op == 0xe866 ? x & 252 :
                          (op & 31) * 4);
        load(d, value, addr, MO_LEUL | MO_ALIGN);
        if ((op & 0xffe0) == 0xefc0) { tcg_gen_andi_i32(value, value, ~packed_mask(x)); }
        else if (op != 0xe864 && op != 0xe866) { tcg_gen_ori_i32(value, value, packed_mask(x)); }
        else {
            TCGv_i32 operand = read_gpr(d, (x >> 8) & 15);
            if (op == 0xe866) {
                operand = bit_operand(operand, op);
            }
            if (kind == 0) { tcg_gen_or_i32(value, value, operand); }
            else if (kind == 1 && op == 0xe866) { tcg_gen_xor_i32(value, value, operand); }
            else if (kind == 2) { tcg_gen_and_i32(value, value, operand); }
            else if (kind == 3) { tcg_gen_andc_i32(value, value, operand); }
            else { goto illegal; }
        }
        store(d, value, addr, MO_LEUL | MO_ALIGN);
        next = here + 4;
    } else if (op == 0xe868) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 3;
        if (kind != 0 && kind != 2) { goto illegal; }
        /* Exact primary word RMW: one read and one write, including zero.
         * End this TB before MMIO can replay a prior read side effect. */
        translator_io_start(db);
        TCGv_i32 addr = tcg_temp_new_i32(), value = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, x >> 12), x & 252);
        load(d, value, addr, MO_LEUL | MO_ALIGN);
        if (kind == 0) { tcg_gen_add_i32(value, value, read_gpr(d, (x >> 8) & 15)); }
        else { tcg_gen_sub_i32(value, value, read_gpr(d, (x >> 8) & 15)); }
        store(d, value, addr, MO_LEUL | MO_ALIGN);
        next = here + 4;
    } else if (op == 0xe86c) {
        uint16_t x = fetch(d, here + 2);
        if (x & 3) { goto illegal; }
        /* Vendor E86C 3704 and separate executable probes establish an
         * immediate left shift; the pinned SLEIGH has no exact constructor. */
        translator_io_start(db);
        TCGv_i32 addr = tcg_temp_new_i32(), value = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, x >> 12), x & 252);
        load(d, value, addr, MO_LEUL | MO_ALIGN);
        tcg_gen_shli_i32(value, value, (x >> 8) & 15);
        /* Count zero still performs both accesses; a failed write does not
         * undo any preceding MMIO read effect. */
        store(d, value, addr, MO_LEUL | MO_ALIGN);
        next = here + 4;
    } else if ((op & 0xffe0) == 0xebc0) {
        uint16_t x = fetch(d, here + 2);
        TCGv_i32 addr = tcg_temp_new_i32(), value = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, x >> 12), (op & 31) * 4);
        load(d, value, addr, MO_LEUL | MO_ALIGN);
        tcg_gen_addi_i32(value, value, sext(x & 4095, 12));
        store(d, value, addr, MO_LEUL | MO_ALIGN);
        next = here + 4;
    } else if ((op & 0xff88) == 0x1900) {
        tcg_gen_or_i32(gpr[a], read_gpr(d, a), read_gpr(d, b));
    } else if ((op & 0xff88) == 0x1908) {
        tcg_gen_xor_i32(gpr[a], read_gpr(d, a), read_gpr(d, b));
    } else if ((op & 0xff88) == 0x1980) {
        tcg_gen_and_i32(gpr[a], read_gpr(d, a), read_gpr(d, b));
    } else if ((op & 0xff88) == 0x1988) {
        tcg_gen_not_i32(gpr[a], read_gpr(d, b));
    } else if ((op & 0xe008) == 0xa000) {
        if (op & 128) { tcg_gen_shri_i32(gpr[a], read_gpr(d, b), (op >> 8) & 31); }
        else { tcg_gen_shli_i32(gpr[a], read_gpr(d, b), (op >> 8) & 31); }
    } else if ((op & 0xe088) == 0xa088) {
        tcg_gen_sari_i32(gpr[a], read_gpr(d, b), (op >> 8) & 31);
    } else if ((op & 0xe000) == 0x6000) {
        TCGv_i32 addr = tcg_temp_new_i32();
        MemOp size = op & 8 ? MO_LEUW | MO_ALIGN : MO_LEUL | MO_ALIGN;
        tcg_gen_addi_i32(addr, read_gpr(d, b), sext((op >> 8) & 31, 5) * (op & 8 ? 2 : 4));
        if (op & 128) { store(d, read_gpr(d, a), addr, size); }
        else { load(d, gpr[a], addr, size); }
    } else if ((op & 0xe008) == 0x4008) {
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, b), sext((op >> 8) & 31, 5));
        if (op & 128) { store(d, read_gpr(d, a), addr, MO_UB); }
        else { load(d, gpr[a], addr, MO_UB); }
    } else if ((op & 0xff80) == 0x0500 || (op & 0xff80) == 0x0580) {
        if (op & 8) { goto illegal; }
        if (!(op & 128) && a == b) { goto illegal; }
        if (op & 128) { store(d, read_gpr(d, a), read_gpr(d, b), MO_LEUL | MO_ALIGN); }
        else { load(d, gpr[a], read_gpr(d, b), MO_LEUL | MO_ALIGN); }
        tcg_gen_addi_i32(gpr[b], read_gpr(d, b), 4);
    } else if ((op & 0xff88) == 0x0600) {
        if (a == b) { goto illegal; }
        load(d, gpr[a], read_gpr(d, b), MO_LEUW | MO_ALIGN);
        tcg_gen_addi_i32(gpr[b], read_gpr(d, b), 2);
    } else if ((op & 0xff88) == 0x0708) {
        if (a == b) { goto illegal; }
        load(d, gpr[a], read_gpr(d, b), MO_UB);
        tcg_gen_addi_i32(gpr[b], read_gpr(d, b), -1);
    } else if ((op & 0xff88) == 0x0700) {
        if (a == b) { goto illegal; }
        load(d, gpr[a], read_gpr(d, b), MO_UB);
        tcg_gen_addi_i32(gpr[b], read_gpr(d, b), 1);
    } else if ((op & 0xff88) == 0x0780) {
        store(d, read_gpr(d, a), read_gpr(d, b), MO_UB);
        tcg_gen_addi_i32(gpr[b], read_gpr(d, b), 1);
    } else if ((op & 0xff88) == 0x0680) {
        store(d, read_gpr(d, a), read_gpr(d, b), MO_LEUW | MO_ALIGN);
        tcg_gen_addi_i32(gpr[b], read_gpr(d, b), 2);
    } else if ((op & 0xfff0) == 0xee50) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = op & 15;
        if (kind != 0 && kind != 1 && kind != 2 && kind != 3 && kind != 4 && kind != 8 && kind != 10) { goto illegal; }
        unsigned base = (x >> 4) & 15, reg = x >> 12;
        if ((kind == 8 || kind == 10) && base == reg) { goto illegal; }
        TCGv_i32 addr = tcg_temp_new_i32();
        int32_t offset = (x & 15) | ((x >> 8) & 15) * 16;
        /* EE53 stores a byte at base + (imm8 - 256), without writeback.
         * Saved EE53 8F0F encodes b[r0-1] = r8, including high GPRs. */
        if (kind == 1 || kind == 3) { offset -= 256; }
        tcg_gen_addi_i32(addr, read_gpr(d, base), offset);
        if (kind == 2 || kind == 3 || kind == 10) { store(d, read_gpr(d, x >> 12), addr, MO_UB); }
        else { load(d, gpr[x >> 12], addr, kind == 4 ? MO_SB : MO_UB); }
        if (kind == 8 || kind == 10) { tcg_gen_mov_i32(gpr[base], addr); }
        next = here + 4;
    } else if (op == 0xeed0) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, dest = x >> 12;
        if (base == dest) { goto illegal; }
        load(d, gpr[dest], read_gpr(d, base), MO_UB);
        tcg_gen_addi_i32(gpr[base], read_gpr(d, base), (x & 15) | ((x >> 8) & 15) * 16);
        next = here + 4;
    } else if (op == 0xeed2) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, source = x >> 12;
        unsigned stride = (x & 15) | ((x >> 8) & 15) * 16;
        /* Vendor EED2 2510 stores b[r1++=80] = r2. Store the incoming
         * low byte at the old base, including source==base, then advance
         * by the unsigned byte stride. A failed store leaves base intact. */
        store(d, read_gpr(d, source), read_gpr(d, base), MO_UB);
        tcg_gen_addi_i32(gpr[base], read_gpr(d, base), stride);
        next = here + 4;
    } else if (op == 0xeed4) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, dest = x >> 12;
        if (base == dest) { goto illegal; }
        load(d, gpr[dest], read_gpr(d, base), MO_SB);
        tcg_gen_addi_i32(gpr[base], read_gpr(d, base), (x & 15) | ((x >> 8) & 15) * 16);
        next = here + 4;
    } else if (op == 0xeed8) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 15;
        if (kind > 2) { goto illegal; }
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_add_i32(addr, read_gpr(d, (x >> 4) & 15), read_gpr(d, (x >> 8) & 15));
        if (kind == 1) { store(d, read_gpr(d, x >> 12), addr, MO_UB); }
        else { load(d, gpr[x >> 12], addr, kind == 2 ? MO_SB : MO_UB); }
        next = here + 4;
    } else if (op == 0xeedc && (fetch(d, here + 2) & 15) == 1) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, source = x >> 12;
        /* Vendor and independent probes use source12:15/index8:11;
         * the primary constructor swaps them. Source==base is deferred. */
        if (base == source) { goto illegal; }
        translator_io_start(db);
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_add_i32(addr, read_gpr(d, base), read_gpr(d, (x >> 8) & 15));
        /* Preserve the existing modeled pre-index writeback-before-access. */
        tcg_gen_mov_i32(gpr[base], addr);
        store(d, read_gpr(d, source), addr, MO_UB);
        next = here + 4;
    } else if (op == 0xeedc) {
        uint16_t x = fetch(d, here + 2);
        if (x & 15) { goto illegal; }
        unsigned base = (x >> 4) & 15, dest = x >> 12;
        if (base == dest) { goto illegal; }
        tcg_gen_add_i32(gpr[base], read_gpr(d, base), read_gpr(d, (x >> 8) & 15));
        load(d, gpr[dest], gpr[base], MO_UB);
        next = here + 4;
    } else if ((op & 0xfff8) == 0xecd0 && (fetch(d, here + 2) & 3) == 3) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, source = x >> 12;
        /* Exact primary stores after writeback, while the reference stores
         * the incoming source. Keep their source==base disagreement explicit. */
        if (base == source) { goto illegal; }
        int32_t offset = sext(op & 7, 3) * 256 + ((x >> 8) & 15) * 16 + ((x >> 2) & 3) * 4;
        translator_io_start(db);
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, base), offset);
        tcg_gen_mov_i32(gpr[base], addr);
        store(d, read_gpr(d, source), addr, MO_LEUL | MO_ALIGN);
        next = here + 4;
    } else if ((op & 0xfff8) == 0xecd0 && (fetch(d, here + 2) & 3) == 2) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, dest = x >> 12;
        /* The primary loads last for this alias; the separate reference
         * writes the address last. Reject the unresolved alias before effects. */
        if (base == dest) { goto illegal; }
        int32_t offset = sext(op & 7, 3) * 256 + ((x >> 8) & 15) * 16 + ((x >> 2) & 3) * 4;
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, base), offset);
        /* Preserve the modeled pre-index writeback-before-access order. */
        tcg_gen_mov_i32(gpr[base], addr);
        load(d, gpr[dest], addr, MO_LEUL | MO_ALIGN);
        next = here + 4;
    } else if ((op & 0xfff8) == 0xecd0 || (op & 0xfff8) == 0xec50) {
        uint16_t x = fetch(d, here + 2);
        if (x & 2) { goto illegal; }
        int32_t offset = sext(op & 7, 3) * 256 + ((x >> 8) & 15) * 16 + ((x >> 2) & 3) * 4;
        unsigned reg = x >> 12;
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, (x >> 4) & 15), offset);
        if ((op & 0xfff8) == 0xec50 && (reg & 1)) { goto illegal; }
        if (x & 1) { store(d, read_gpr(d, reg), addr, MO_LEUL | MO_ALIGN); }
        else { load(d, gpr[reg], addr, MO_LEUL | MO_ALIGN); }
        if ((op & 0xfff8) == 0xec50) {
            tcg_gen_addi_i32(addr, addr, 4);
            if (x & 1) { store(d, read_gpr(d, reg + 1), addr, MO_LEUL | MO_ALIGN); }
            else { load(d, gpr[reg + 1], addr, MO_LEUL | MO_ALIGN); }
        }
        next = here + 4;
    } else if (op == 0xecd8) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 15;
        if ((x & 3) == 0) {
            unsigned base = (x >> 4) & 15, dest = x >> 12;
            if (base == dest) { goto illegal; }
            load(d, gpr[dest], read_gpr(d, base), MO_LEUL | MO_ALIGN);
            tcg_gen_addi_i32(gpr[base], read_gpr(d, base), (x & 12) | ((x >> 8) & 15) * 16);
        } else {
            if (kind != 2 && kind != 3 && kind != 10 && kind != 11) { goto illegal; }
            TCGv_i32 addr = tcg_temp_new_i32();
            tcg_gen_shli_i32(addr, read_gpr(d, (x >> 8) & 15), kind & 8 ? 2 : 0);
            tcg_gen_add_i32(addr, addr, read_gpr(d, (x >> 4) & 15));
            if (!(kind & 1)) { load(d, gpr[x >> 12], addr, MO_LEUL | MO_ALIGN); }
            else { store(d, read_gpr(d, x >> 12), addr, MO_LEUL | MO_ALIGN); }
        }
        next = here + 4;
    } else if (op == 0xecdc) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, dest = x >> 12;
        unsigned kind = x & 15;
        /* Vendor ECDC 1162 loads r1 from [++r6=r1]; ECDC 5013 stores
         * r5 to [++r1=r0]. Both use an unscaled incoming register sum.
         * Source==base stores disagree between pinned SLEIGH and the
         * separate oracle; reject that unresolved alias before effects. */
        if ((kind != 2 && kind != 3) || base == dest) { goto illegal; }
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_add_i32(addr, read_gpr(d, base), read_gpr(d, (x >> 8) & 15));
        /* Keep the existing pre-index writeback-before-access policy.
         * Hardware state after an access fault has not been established. */
        tcg_gen_mov_i32(gpr[base], addr);
        if (kind == 3) { store(d, read_gpr(d, dest), addr, MO_LEUL | MO_ALIGN); }
        else { load(d, gpr[dest], addr, MO_LEUL | MO_ALIGN); }
        next = here + 4;
    } else if (op == 0xeddc) {
        uint16_t x = fetch(d, here + 2);
        if ((x & 15) != 2) { goto illegal; }
        unsigned base = (x >> 4) & 15;
        TCGv_i32 addr = tcg_temp_new_i32();
        /* Vendor EDDC 3312 and separate executable probes establish the
         * unscaled incoming sum, including destination/base/index aliases. */
        tcg_gen_add_i32(addr, read_gpr(d, base), read_gpr(d, (x >> 8) & 15));
        /* Preserve the modeled pre-index writeback-before-access order. */
        tcg_gen_mov_i32(gpr[base], addr);
        load(d, gpr[x >> 12], addr, MO_LESW | MO_ALIGN);
        next = here + 4;
    } else if (op == 0xedd4) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, dest = x >> 12;
        if ((x & 1) || base == dest) { goto illegal; }
        /* Vendor EDD4 C032 and finite independent probes agree on the
         * old-base read/postupdate. The primary display agrees, but its
         * body uses an offset read without writeback; retain that caveat. */
        load(d, gpr[dest], read_gpr(d, base), MO_LESW | MO_ALIGN);
        tcg_gen_addi_i32(gpr[base], read_gpr(d, base), (x & 14) | ((x >> 8) & 15) * 16);
        next = here + 4;
    } else if (op == 0xedd0 && (fetch(d, here + 2) & 1)) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, source = x >> 12;
        /* Vendor EDD0/10F3 and independent probes establish an old-base
         * low-halfword store followed by an unsigned even byte stride. */
        translator_io_start(db);
        store(d, read_gpr(d, source), read_gpr(d, base), MO_LEUW | MO_ALIGN);
        tcg_gen_addi_i32(gpr[base], read_gpr(d, base), (x & 14) | ((x >> 8) & 15) * 16);
        next = here + 4;
    } else if (op == 0xedd0) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, dest = x >> 12;
        if ((x & 1) || base == dest) { goto illegal; }
        /* Vendor EDD0 2104 at Felucca 0x0200d258 walks GP defaults:
         * load the old base, then advance by the unsigned even byte stride. */
        load(d, gpr[dest], read_gpr(d, base), MO_LEUW | MO_ALIGN);
        tcg_gen_addi_i32(gpr[base], read_gpr(d, base), (x & 14) | ((x >> 8) & 15) * 16);
        next = here + 4;
    } else if (op == 0xedd8) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 15;
        if (kind != 2 && kind != 8 && kind != 9 && kind != 10) { goto illegal; }
        TCGv_i32 addr = tcg_temp_new_i32();
        /* Felucca's palette loop EDD8 2108/2139 uses index << 1. */
        tcg_gen_shli_i32(addr, read_gpr(d, (x >> 8) & 15), kind == 2 ? 0 : 1);
        tcg_gen_add_i32(addr, addr, read_gpr(d, (x >> 4) & 15));
        if (kind == 8) { load(d, gpr[x >> 12], addr, MO_LEUW | MO_ALIGN); }
        else if (kind == 2 || kind == 10) { load(d, gpr[x >> 12], addr, MO_LESW | MO_ALIGN); }
        else { store(d, read_gpr(d, x >> 12), addr, MO_LEUW | MO_ALIGN); }
        next = here + 4;
    } else if ((op & 0xfffc) == 0xed58 && (fetch(d, here + 2) & 1)) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, source = x >> 12;
        /* Vendor and independent store probes use unsigned high2 offset
         * bits, unlike the existing signed load direction. Alias is deferred. */
        if (base == source) { goto illegal; }
        unsigned offset = (op & 3) * 256 + ((x >> 8) & 15) * 16 + (x & 14);
        translator_io_start(db);
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, base), offset);
        tcg_gen_mov_i32(gpr[base], addr);
        store(d, read_gpr(d, source), addr, MO_LEUW | MO_ALIGN);
        next = here + 4;
    } else if ((op & 0xfffc) == 0xed58) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, dest = x >> 12;
        /* Store direction and unresolved address-wins reference aliases
         * remain deferred; the exact primary alias contract is absent. */
        if ((x & 1) || base == dest) { goto illegal; }
        int32_t offset = sext(op & 3, 2) * 256 + ((x >> 8) & 15) * 16 + (x & 14);
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, base), offset);
        /* Preserve the modeled pre-index writeback-before-access policy. */
        tcg_gen_mov_i32(gpr[base], addr);
        load(d, gpr[dest], addr, MO_LEUW | MO_ALIGN);
        next = here + 4;
    } else if (op == 0xed5c) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, dest = x >> 12;
        if ((x & 1) || base == dest) { goto illegal; }
        /* A preupdate before MMIO must not be repeated by an icount replay. */
        translator_io_start(db);
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, base), (x & 14) | ((x >> 8) & 15) * 16);
        tcg_gen_mov_i32(gpr[base], addr);
        load(d, gpr[dest], addr, MO_LESW | MO_ALIGN);
        next = here + 4;
    } else if ((op & 0xfffe) == 0xed50) {
        uint16_t x = fetch(d, here + 2);
        unsigned offset = (op & 1) * 256 + ((x >> 8) & 15) * 16 + (x & 14);
        TCGv_i32 addr = tcg_temp_new_i32();
        /* ED50/ED51 use operand bit 0 to select stores of the low halfword. */
        tcg_gen_addi_i32(addr, read_gpr(d, (x >> 4) & 15), offset);
        if (x & 1) { store(d, read_gpr(d, x >> 12), addr, MO_LEUW | MO_ALIGN); }
        else { load(d, gpr[x >> 12], addr, MO_LEUW | MO_ALIGN); }
        next = here + 4;
    } else if ((op & 0xfffe) == 0xed54) {
        uint16_t x = fetch(d, here + 2);
        if (x & 1) { goto illegal; }
        unsigned offset = (op & 1) * 256 + ((x >> 8) & 15) * 16 + (x & 14);
        TCGv_i32 addr = tcg_temp_new_i32();
        /* Vendor ED54/63BC and ED55/52FC load signed halfwords at byte
         * offsets 60 and 300, with no base writeback. Odd operands and
         * ED56/57 remain unverified and explicitly unsupported. */
        tcg_gen_addi_i32(addr, read_gpr(d, (x >> 4) & 15), offset);
        load(d, gpr[x >> 12], addr, MO_LESW | MO_ALIGN);
        next = here + 4;
    } else if ((op & 0xffc0) == 0xe100) {
        uint16_t x = fetch(d, here + 2);
        int32_t imm = x & 4095;
        if (op & 32) { imm += sext((op >> 4) & 3, 2) * 4096; }
        else if (op & 16) { imm += 4096; }
        gen_helper_pi32v2_alu(gpr[op & 15], tcg_env, read_gpr(d, x >> 12),
                              tcg_constant_i32(imm), tcg_constant_i32(0));
        next = here + 4;
    } else if (op == 0xe9d8) {
        uint16_t x = fetch(d, here + 2);
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, spr[SP], x & 4094);
        if (x & 1) { store(d, read_gpr(d, x >> 12), addr, MO_LEUW | MO_ALIGN); }
        else { load(d, gpr[x >> 12], addr, MO_LEUW | MO_ALIGN); }
        next = here + 4;
    } else if (op == 0xe9d9) {
        uint16_t x = fetch(d, here + 2);
        if (x & 1) { goto illegal; }
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, spr[SP], x & 4094);
        load(d, gpr[x >> 12], addr, MO_LESW | MO_ALIGN);
        next = here + 4;
    } else if (op == 0xe9dc) {
        uint16_t x = fetch(d, here + 2);
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, spr[SP], x & 4095);
        load(d, gpr[x >> 12], addr, MO_UB);
        next = here + 4;
    } else if (op == 0xe9de) {
        uint16_t x = fetch(d, here + 2);
        TCGv_i32 addr = tcg_temp_new_i32();
        /* All low 12 bits are an unsigned byte offset from the special SP. */
        tcg_gen_addi_i32(addr, spr[SP], x & 4095);
        store(d, read_gpr(d, x >> 12), addr, MO_UB);
        next = here + 4;
    } else if (op == 0xe9d4 || op == 0xe9d0) {
        uint16_t x = fetch(d, here + 2);
        if ((x & 2) || (op == 0xe9d0 && ((x >> 12) & 1))) { goto illegal; }
        unsigned offset = x & 4092;
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, spr[SP], offset);
        if (x & 1) { store(d, read_gpr(d, x >> 12), addr, MO_LEUL | MO_ALIGN); }
        else { load(d, gpr[x >> 12], addr, MO_LEUL | MO_ALIGN); }
        if (op == 0xe9d0) {
            tcg_gen_addi_i32(addr, addr, 4);
            if (x & 1) { store(d, read_gpr(d, (x >> 12) + 1), addr, MO_LEUL | MO_ALIGN); }
            else { load(d, gpr[(x >> 12) + 1], addr, MO_LEUL | MO_ALIGN); }
        }
        next = here + 4;
    } else if (op == 0xe8f0) {
        uint16_t x = fetch(d, here + 2);
        /* Vendor negative constants and copied-reference discriminators:
         * signed 13-bit adjustment in whole words; other fields deferred. */
        if (x & 0xe003) { goto illegal; }
        tcg_gen_addi_i32(spr[SP], spr[SP], sext(x, 13));
        gen_helper_pi32v2_access(tcg_env, spr[SP], tcg_constant_i32(0), tcg_constant_i32(4));
        next = here + 4;
    } else if (op == 0xe8f8) {
        uint16_t x = fetch(d, here + 2);
        tcg_gen_addi_i32(gpr[x >> 12], spr[SP], x & 4095);
        next = here + 4;
    } else if (op == 0xe8d8 || op == 0xe8d4) {
        uint16_t mask = fetch(d, here + 2);
        if (op == 0xe8d8) {
            for (int i = 15; i >= 0; i--) { if (mask & (1 << i)) { push(d, read_gpr(d, i)); } }
        } else {
            for (int i = 0; i < 16; i++) { if (mask & (1 << i)) { pop(d, gpr[i]); } }
        }
        next = here + 4;
    } else if ((op & 0xfff0) == 0xeb00) {
        uint16_t mask = fetch(d, here + 2);
        unsigned base = op & 15;
        if (!mask || (mask & (1u << base))) { goto illegal; }
        /* EB04 0104 reads r2 then r8 from consecutive words. SLEIGH's
         * cursor and Felucca's later r4-relative accesses leave r4 intact. */
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_mov_i32(addr, read_gpr(d, base));
        for (unsigned i = 0; i < 16; i++) {
            if (mask & (1u << i)) {
                load(d, gpr[i], addr, MO_LEUL | MO_ALIGN);
                tcg_gen_addi_i32(addr, addr, 4);
            }
        }
        next = here + 4;
    } else if (op == 0x0400) {
        TCGv_i32 dest = tcg_temp_new_i32();
        pop(d, dest);
        count(d); dynamic_jump(d, dest);
    } else if (op == 0x0410) {
        push(d, spr[RETS]);
    } else if ((op & 0xfff0) == 0x0460 || (op & 0xfff0) == 0x0440) {
        unsigned boundary = op & 15;
        unsigned lo = boundary < 4 ? boundary : 4;
        unsigned hi = boundary < 4 ? 3 : boundary;
        if (op & 32) { for (int i = hi; i >= (int)lo; i--) { push(d, read_gpr(d, i)); } }
        else { for (unsigned i = lo; i <= hi; i++) { pop(d, gpr[i]); } }
    } else if ((op & 0xfff0) == 0x0430) {
        unsigned hi = op & 15;
        if (hi < 4) { goto illegal; }
        /* Invert the existing RETS/range push without returning: Felucca
         * LCD-window 0438 restores RETS before its separate tail branch. */
        for (unsigned i = 4; i <= hi; i++) { pop(d, gpr[i]); }
        pop(d, spr[RETS]);
    } else if ((op & 0xfff0) == 0x0470 || (op & 0xfff0) == 0x0450) {
        unsigned hi = op & 15;
        if (hi < 4) { goto illegal; }
        if (op & 32) {
            push(d, spr[RETS]);
            for (int i = hi; i >= 4; i--) { push(d, read_gpr(d, i)); }
        } else {
            TCGv_i32 dest = tcg_temp_new_i32();
            for (unsigned i = 4; i <= hi; i++) { pop(d, gpr[i]); }
            pop(d, dest);
            count(d); dynamic_jump(d, dest);
        }
    } else if (op == 0x04e9) {
        push(d, spr[PSR]); push(d, spr[RETS]); push(d, spr[RETI]);
    } else if (op == 0x04a9) {
        pop(d, spr[RETI]); pop(d, spr[RETS]); pop(d, spr[PSR]);
    } else if ((op & 0xfff0) == 0xea00) {
        int32_t delta = (int16_t)fetch(d, here + 2) * 2;
        next = here + 4;
        tcg_gen_subi_i32(gpr[op & 15], read_gpr(d, op & 15), 1);
        count(d); branch(d, next + delta, next, gpr[op & 15], true);
    } else if (op == 0xff80) {
        /* Vendor FF80 000000B0 / FFFFFFF6 call forward 176 / back 10
         * bytes from the six-byte instruction's sequential boundary. */
        int32_t delta = (int32_t)(fetch(d, here + 2) |
                                  ((uint32_t)fetch(d, here + 4) << 16));
        next = here + 6;
        set_call_return(d, next);
        count(d); record_branch(d); jump(next + delta); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xffc0) == 0xea80) {
        int32_t delta = sext(((uint32_t)(op & 63) << 16) | fetch(d, here + 2), 22) * 2;
        next = here + 4;
        set_call_return(d, next);
        count(d); record_branch(d); jump(next + delta); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xffc0) == 0xeac0) {
        int32_t delta = sext(((uint32_t)(op & 63) << 16) | fetch(d, here + 2), 22) * 2;
        next = here + 4;
        /* The long GOTO shares CALL's displacement fields but preserves RETS. */
        count(d); record_branch(d); chain_jump(d, next + delta, 0); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xe00c) == 0x8004) {
        int32_t delta = sext(((op & 3) << 10) | (((op >> 4) & 15) << 6) | (((op >> 8) & 31) << 1), 12);
        count(d); record_branch(d); chain_jump(d, next + delta, 0); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xe08f) == 0x8001) {
        int32_t delta = sext((((op >> 4) & 7) << 6) | (((op >> 8) & 31) << 1), 9);
        set_call_return(d, next);
        count(d); record_branch(d); jump(next + delta); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xe008) == 0x4000) {
        int32_t delta = sext((((op >> 4) & 7) << 6) | (((op >> 8) & 31) << 1), 9);
        count(d); branch(d, next + delta, next, read_gpr(d, a), op & 128);
    } else if ((op & 0xfff0) == 0xe850) {
        uint16_t x = fetch(d, here + 2);
        TCGv_i32 masked = tcg_temp_new_i32();
        if (x & 1024) { goto illegal; }
        tcg_gen_andi_i32(masked, read_gpr(d, op & 15), 1u << (x >> 11));
        next = here + 4;
        count(d); branch(d, next + sext(x & 511, 9) * 2, next, masked, x & 512);
    } else if ((op & 0xff00) == 0xfa00 || (op & 0xff00) == 0xfb00) {
        int32_t delta = (int16_t)fetch(d, here + 2) * 2;
        TCGv_i32 masked = tcg_temp_new_i32();
        tcg_gen_and_i32(masked, read_gpr(d, op & 15), read_gpr(d, (op >> 4) & 15));
        next = here + 4;
        count(d); branch(d, next + delta, next, masked, (op & 0x0100) != 0);
    } else if (op == 0xff60 || op == 0xff61) {
        uint16_t x = fetch(d, here + 2), displacement = fetch(d, here + 4);
        TCGv_i32 masked = tcg_temp_new_i32();
        tcg_gen_andi_i32(masked, read_gpr(d, x >> 12), packed_mask(x));
        next = here + 6;
        count(d); branch(d, next + (int16_t)displacement * 2, next, masked, op & 1);
    } else if (op == 0xff20 || op == 0xff21 || op == 0xff23 ||
               op == 0xff28 || op == 0xff29 || op == 0xff2a ||
               op == 0xff2b || op == 0xff2d) {
        uint16_t x = fetch(d, here + 2), displacement = fetch(d, here + 4);
        TCGCond cond;
        switch (op & 15) {
        case 0: cond = TCG_COND_EQ; break;
        case 1: cond = TCG_COND_NE; break;
        case 3: cond = TCG_COND_LTU; break;
        case 8: cond = TCG_COND_GTU; break;
        case 9: cond = TCG_COND_LEU; break;
        case 10: cond = TCG_COND_GE; break;
        case 11: cond = TCG_COND_LT; break;
        default: cond = TCG_COND_LE; break;
        }
        /* Vendor FF2D/3D7A compares signed r3 <= 16000. The pinned
         * primary instead labels FF0D as packed <=; keep that discrepancy
         * explicit and preserve the existing packed-repeat model policy. */
        next = here + 6;
        count(d); compare_branch(d, next + (int16_t)displacement * 2, next, cond,
                                 read_gpr(d, x >> 12), tcg_constant_i32(packed_mask(x)));
    } else if (op == 0xff0b || op == 0xff0d || op == 0xff40 ||
               op == 0xff42 || op == 0xff43 || op == 0xff48 || op == 0xff4a) {
        uint16_t x = fetch(d, here + 2), displacement = fetch(d, here + 4);
        TCGCond cond;
        TCGv_i32 right;
        if (op == 0xff0b || op == 0xff0d) {
            /* Saved vendor literals and independent full-state probes resolve
             * primary unsigned/packed operand contradictions as signed12. */
            cond = op == 0xff0b ? TCG_COND_LT : TCG_COND_LE;
            right = tcg_constant_i32(sext(x & 4095, 12));
        } else {
            if (x & 255) { goto illegal; }
            switch (op) {
            case 0xff40: cond = TCG_COND_EQ; break;
            case 0xff42: cond = TCG_COND_GEU; break;
            case 0xff43: cond = TCG_COND_LTU; break;
            case 0xff48: cond = TCG_COND_GTU; break;
            default: cond = TCG_COND_GE; break; /* Exact FF4A. */
            }
            /* Vendor FF4A uses C bits8:11; the primary B field is contradicted. */
            right = read_gpr(d, (x >> 8) & 15);
        }
        next = here + 6;
        count(d); compare_branch(d, next + (int16_t)displacement * 2, next, cond,
                                 read_gpr(d, x >> 12), right);
    } else if (op == 0xff41) {
        uint16_t x = fetch(d, here + 2), displacement = fetch(d, here + 4);
        if (x & 255) { goto illegal; }
        next = here + 6;
        gen_helper_pi32v2_long_register_ne_end(tcg_env, tcg_constant_i32(next));
        count(d); compare_branch(d, next + (int16_t)displacement * 2, next, TCG_COND_NE,
                                 read_gpr(d, x >> 12), read_gpr(d, (x >> 8) & 15));
    } else if (op == 0xff00 || op == 0xff01 || op == 0xff02 || op == 0xff03 || op == 0xff08 || op == 0xff09 || op == 0xff0c) {
        uint16_t x = fetch(d, here + 2), displacement = fetch(d, here + 4);
        TCGCond cond;
        switch (op & 15) {
        case 0: cond = TCG_COND_EQ; break;
        case 1: cond = TCG_COND_NE; break;
        case 2: cond = TCG_COND_GEU; break;
        case 3: cond = TCG_COND_LTU; break;
        case 8: cond = TCG_COND_GTU; break;
        case 12: cond = TCG_COND_GT; break;
        default: cond = TCG_COND_LEU; break;
        }
        next = here + 6;
        if (op == 0xff0c) {
            gen_helper_pi32v2_signed_branch_end(tcg_env, tcg_constant_i32(next));
        }
        count(d); compare_branch(d, next + (int16_t)displacement * 2, next, cond,
                                 read_gpr(d, x >> 12),
                                 tcg_constant_i32(op == 0xff0c ? sext(x & 4095, 12) : x & 4095));
    } else if ((op & 0xfff0) == 0xe800 || (op & 0xfff0) == 0xe880 ||
               (op & 0xfff0) == 0xe900 || (op & 0xfff0) == 0xe980 ||
               (op & 0xfff0) == 0xec00 || (op & 0xfff0) == 0xec80 ||
               (op & 0xfff0) == 0xed00 || (op & 0xfff0) == 0xed80 ||
               (op & 0xfff0) == 0xee00 || (op & 0xfff0) == 0xee80) {
        uint16_t x = fetch(d, here + 2);
        if (x & 0x0e00) { goto illegal; }
        TCGCond cond;
        switch ((op >> 4) & 255) {
        case 0x80: cond = TCG_COND_EQ; break;
        case 0x88: cond = TCG_COND_NE; break;
        case 0x90: cond = TCG_COND_GEU; break;
        case 0x98: cond = TCG_COND_LTU; break;
        case 0xc0: cond = TCG_COND_GTU; break;
        case 0xd0: cond = TCG_COND_GE; break;
        case 0xd8: cond = TCG_COND_LT; break;
        case 0xe0: cond = TCG_COND_GT; break;
        case 0xe8: cond = TCG_COND_LE; break;
        default: cond = TCG_COND_LEU; break;
        }
        next = here + 4;
        count(d); compare_branch(d, next + sext(x & 511, 9) * 2, next, cond,
                                 read_gpr(d, x >> 12), read_gpr(d, op & 15));
    } else if ((op & 0xfc00) == 0xf800 || (op & 0xfe00) == 0xfc00 || (op & 0xff00) == 0xfe00) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = (op >> 7) & 63;
        TCGCond cond;
        switch (kind) {
        case 0x30: cond = TCG_COND_EQ; break;
        case 0x31: cond = TCG_COND_NE; break;
        case 0x32: cond = TCG_COND_GEU; break;
        case 0x33: cond = TCG_COND_LTU; break;
        case 0x38: cond = TCG_COND_GTU; break;
        case 0x39: cond = TCG_COND_LEU; break;
        case 0x3a: cond = TCG_COND_GE; break;
        case 0x3b: cond = TCG_COND_LT; break;
        case 0x3c: cond = TCG_COND_GT; break;
        case 0x3d: cond = TCG_COND_LE; break;
        default: goto illegal;
        }
        next = here + 4;
        uint32_t immediate = (((op >> 4) & 7) << 7) | (x >> 9);
        if (kind == 0x31 || kind >= 0x3a) { immediate = sext(immediate, 10); }
        count(d); compare_branch(d, next + sext(x & 511, 9) * 2, next, cond,
                                 read_gpr(d, op & 15), tcg_constant_i32(immediate));
    } else if ((op & 0xfff0) == 0x0100 || (op & 0xfff0) == 0x0110) {
        TCGv_i32 addr = tcg_temp_new_i32(), target = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, op & 15), next);
        load(d, target, addr, (op & 0x0010) ? MO_LEUW | MO_ALIGN : MO_UB);
        tcg_gen_shli_i32(target, target, 1);
        tcg_gen_addi_i32(target, target, next);
        count(d); dynamic_jump(d, target);
    } else if ((op & 0xfff0) == 0x00c0) {
        set_call_return(d, next);
        count(d); dynamic_jump(d, read_gpr(d, op & 15));
    } else if ((op & 0xfff0) == 0x0230) {
        gen_helper_pi32v2_flush(tcg_env, read_gpr(d, op & 15));
        db->is_jmp = DISAS_EXIT;
    } else if (op == 0x0080) {
        count(d); dynamic_jump(d, spr[RETS]);
    } else if (op == 0x0081) {
        count(d); record_branch(d); gen_helper_pi32v2_rti(tcg_env); tcg_gen_exit_tb(NULL, 0);
        db->is_jmp = DISAS_NORETURN;
    } else if (op == 0x0060 || op == 0x0061) {
        if (op == 0x0060) { tcg_gen_andi_i32(spr[ICFG], spr[ICFG], ~0x200u); }
        else { tcg_gen_ori_i32(spr[ICFG], spr[ICFG], 0x200); }
        db->is_jmp = DISAS_EXIT;
    } else if (op == 0x0022) {
        /* SSYNC orders memory and MMIO. This single-CPU machine has coherent
         * synchronous accesses and no outstanding CPU write/cache queue.
         * Preserve full ordering without advancing virtual time or IRQs. */
        tcg_gen_mb(TCG_MO_ALL | TCG_BAR_SC);
    } else if (op != 0x0020 && op != 0x0000) {
        goto illegal;
    }

    if (db->is_jmp != DISAS_NORETURN) { count(d); }
    return next;
illegal:
    gen_helper_pi32v2_illegal(tcg_env, tcg_constant_i32(op));
    db->is_jmp = DISAS_NORETURN;
    return next;
}
static int parallel_writes(PiDisasContext *d, uint32_t here, uint16_t op)
{
    if ((op & 0xff88) == 0x1a00 || (op & 0xff88) == 0x1a80) { return 1u << (op & 7); }
    if ((op & 0xe008) == 0x4008) { return op & 128 ? 0 : 1u << (op & 7); }
    if ((op & 0xff80) == 0x0500 || (op & 0xff80) == 0x0580) {
        if (op & 8) { return -1; }
        return (1u << ((op >> 4) & 7)) | (op & 128 ? 0 : 1u << (op & 7));
    }
    if ((op & 0xff88) == 0x0600) {
        if ((op & 7) == ((op >> 4) & 7)) { return -1; }
        return (1u << ((op >> 4) & 7)) | (1u << (op & 7));
    }
    if ((op & 0xff88) == 0x0700) {
        if ((op & 7) == ((op >> 4) & 7)) { return -1; }
        return (1u << ((op >> 4) & 7)) | (1u << (op & 7));
    }
    if ((op & 0xff88) == 0x0780 || (op & 0xff88) == 0x0680) {
        return 1u << ((op >> 4) & 7);
    }
    if ((op & 0xe008) == 0x6000) { return op & 128 ? 0 : 1u << (op & 7); }
    if ((op & 0xe088) == 0x6008) { return 1u << (op & 7); }
    /* Reached F101/3020 + 60B9 stores the incoming low halfword,
     * with neither a GPR destination nor base writeback. */
    if ((op & 0xe088) == 0x6088) { return 0; }
    if ((op & 0xe0c0) == 0x2040 || (op & 0xe0f8) == 0x2010 ||
        (op & 0xfe00) == 0x1c00 || (op & 0xfe00) == 0x1e00 ||
        (op & 0xe0c0) == 0x20c0 || (op & 0xe088) == 0x8008 ||
        (op & 0xff00) == 0x1900 || (op & 0xe008) == 0xa000) {
        return 1u << (op & 7);
    }
    if ((op & 0xff00) == 0x1600 || (op & 0xfff0) == 0xe040 ||
        (op & 0xfff0) == 0xe160 || (op & 0xfff0) == 0xe170 || (op & 0xffc0) == 0xe100 ||
        (op & 0xfff0) == 0xe1a0 || (op & 0xfff0) == 0xe1b0 ||
        (op & 0xfff0) == 0xe0a0 || (op & 0xfff0) == 0xe0e0 ||
        (op & 0xfff0) == 0xe0f0 || (op & 0xfff0) == 0xe150 ||
        (op & 0xfff0) == 0xe1e0) {
        return 1u << (op & 15);
    }
    if ((op & 0xff00) == 0x1700) { return 1u << (op & 7); }
    if ((op & 0xff00) == 0x1500 && !(op & 17)) { return 3u << (op & 14); }
    if ((op & 0xff00) == 0x1800) { return 1u << (op & 15); }
    if ((op & 0xff00) == 0x1b00) { return 1u << (op & 15); }
    if ((op & 0xfff8) == 0x14c0) { return 1u << (8 + (op & 7)); }
    if ((op & 0xfff0) == 0x1480 && !(op & 1)) { return 3u << (op & 14); }
    if ((op & 0xe0d0) == 0x2000) { return 1u << (op & 15); }
    if ((op & 0xe0d0) == 0x2080) { return 0; }
    if (op == 0xe060) { return 1u << (fetch(d, here + 2) >> 12); }
    if (op == 0xe1f0) {
        uint16_t x = fetch(d, here + 2);
        return x & 15 ? -1 : 1u << (x >> 12);
    }
    if (op == 0xe1f4 || op == 0xe435) {
        uint16_t x = fetch(d, here + 2);
        return (x & 15) <= 1 ? 1u << (x >> 12) : -1;
    }
    if (op == 0xe434) {
        uint16_t x = fetch(d, here + 2);
        return (x & 15) == 1 ? 1u << (x >> 12) : -1;
    }
    if (op == 0xe194) {
        uint16_t x = fetch(d, here + 2);
        return (x & 15) == 2 ? 1u << (x >> 12) : -1;
    }
    if (op == 0xe430 || op == 0xe070) {
        uint16_t x = fetch(d, here + 2);
        return x & 255 ? -1 : 1u << (x >> 12);
    }
    if (op == 0xe1c8 || op == 0xe1c0 || op == 0xe190 || op == 0xe0b4) {
        return 1u << (fetch(d, here + 2) >> 12);
    }
    if (op == 0x0000 || (op & 0xffc0) == 0xea40) { return 0; }
    return -1;
}
static unsigned operation_size(uint16_t op)
{
    if ((op & 0xffc0) == 0xffc0 || (op & 0xfff0) == 0xffe0 || op == 0xff80 ||
        op == 0xff00 || op == 0xff01 || op == 0xff02 || op == 0xff03 || op == 0xff08 || op == 0xff09 || op == 0xff0c ||
        op == 0xff0b || op == 0xff0d ||
        op == 0xff20 || op == 0xff21 || op == 0xff23 || op == 0xff28 || op == 0xff29 ||
        op == 0xff2a || op == 0xff2b || op == 0xff2d ||
        op == 0xff40 || op == 0xff42 || op == 0xff43 || op == 0xff48 || op == 0xff4a ||
        op == 0xff41 || op == 0xff60 || op == 0xff61) { return 6; }
    return op >> 13 == 7 ? 4 : 2;
}
static uint32_t instruction_end(PiDisasContext *d, uint32_t here)
{
    uint16_t op = fetch(d, here);
    if (op >> 13 == 6 || (op & 0xf800) == 0xf000) {
        uint16_t head = op >> 13 == 6 ? op & 0x1fff : op & ~0x1000;
        here += operation_size(head);
        return here + operation_size(fetch(d, here));
    }
    return here + operation_size(op);
}

static void translate_insn(DisasContextBase *db, CPUState *cs)
{
    PiDisasContext *d = container_of(db, PiDisasContext, base);
    uint32_t here = db->pc_next, next = here + 2;
    uint16_t op;
    tcg_gen_movi_i32(pc, here);
    if (PI32V2_CPU(cs)->instruction_limit) {
        TCGLabel *within_budget = gen_new_label();
        tcg_gen_brcondi_i64(TCG_COND_LTU, instructions,
                           PI32V2_CPU(cs)->instruction_limit, within_budget);
        gen_helper_pi32v2_budget(tcg_env);
        gen_set_label(within_budget);
    }
    if (here == d->stop) {
        gen_helper_pi32v2_finish(tcg_env);
        db->is_jmp = DISAS_NORETURN;
        db->pc_next = next;
        return;
    }
    if (PI32V2_CPU(cs)->frame_pc && here == PI32V2_CPU(cs)->frame_pc) {
        /* Observe a completed guest frame, then execute its real next opcode. */
        gen_helper_pi32v2_frame(tcg_env);
    }
    if (PI32V2_CPU(cs)->loop_pc && here == PI32V2_CPU(cs)->loop_pc) {
        translator_io_start(db);
        gen_helper_pi32v2_loop(tcg_env);
    }
    op = fetch(d, here);
    bool parallel = op >> 13 == 6 || (op & 0xf800) == 0xf000;
    unsigned span = instruction_end(d, here) - here;
    gen_helper_pi32v2_access(tcg_env, tcg_constant_i32(here),
                             tcg_constant_i32(span), tcg_constant_i32(2));
    if (parallel) {
        /* Either half may touch MMIO. Ending the TB here lets QEMU enable
         * I/O before both effects, so replay cannot repeat a register update. */
        translator_io_start(db);
        uint16_t head = op >> 13 == 6 ? op & 0x1fff : op & ~0x1000;
        uint32_t tail_pc = here + operation_size(head);
        uint16_t tail = fetch(d, tail_pc);
        int head_writes = parallel_writes(d, here, head);
        int tail_writes = parallel_writes(d, tail_pc, tail);
        if (head_writes < 0 || tail_writes < 0 || (head_writes & tail_writes)) {
            gen_helper_pi32v2_illegal(tcg_env, tcg_constant_i32(op));
            db->is_jmp = DISAS_NORETURN;
        } else {
            TCGv_i32 incoming[16];
            for (unsigned i = 0; i < 16; i++) {
                incoming[i] = tcg_temp_new_i32();
                tcg_gen_mov_i32(incoming[i], gpr[i]);
            }
            d->count_enabled = false;
            next = decode_operation(d, tail_pc, tail);
            d->count_enabled = true;
            for (unsigned i = 0; i < 16; i++) { d->inputs[i] = incoming[i]; }
            decode_operation(d, here, head);
            for (unsigned i = 0; i < 16; i++) { d->inputs[i] = NULL; }
        }
    } else {
        next = decode_operation(d, here, op);
    }
    db->pc_next = next;
    if (db->is_jmp == DISAS_NEXT && !translator_is_same_page(db, next + 5)) {
        db->is_jmp = DISAS_TOO_MANY;
    }
}
static void tb_stop(DisasContextBase *db, CPUState *cs)
{
    if (db->is_jmp == DISAS_EXIT) {
        /* Re-evaluate a pending IRQ after its architectural mask changes. */
        gen_helper_pi32v2_advance(pc, tcg_env, tcg_constant_i32(db->pc_next));
        tcg_gen_exit_tb(NULL, 0);
    } else if (db->is_jmp != DISAS_NORETURN) {
        PiDisasContext *d = container_of(db, PiDisasContext, base);
        chain_jump(d, db->pc_next, 0);
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
    /* Preserve conditional completion and IRQ admission at each architectural
     * instruction boundary for every image. Wider TBs need a separate gate. */
    *max_insns = 1;
    translator_loop(cs, tb, max_insns, start, host_pc, &ops, &d.base, TCG_TYPE_VA);
}
