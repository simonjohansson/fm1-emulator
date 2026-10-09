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
    bool count_enabled, repeating, predicated, in_irq;
    TCGv_i32 inputs[16];
} PiDisasContext;
static TCGv_i32 gpr[16], spr[16], pc;
static TCGv_i64 instructions;
#define DISAS_EXIT DISAS_TARGET_0
static uint32_t instruction_end(PiDisasContext *d, uint32_t here);
static unsigned operation_size(uint16_t op);
static int parallel_writes(PiDisasContext *d, uint32_t here, uint16_t op);
static bool discarded_load(uint16_t tail);

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
/* A final selected RTS, pop PC or GOTO retires its IF arm (or REP body) at
 * its sequential boundary before leaving it. Outside such blocks there is
 * nothing to retire. */
static void retire_final_transfer(PiDisasContext *d, uint32_t next)
{
    if (d->predicated || d->repeating) {
        gen_helper_pi32v2_return_end(tcg_env, tcg_constant_i32(next));
    }
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
/* Guard checks read the machine's mirrors in env. Loads need none: XIP
 * reads fault through the memory map while SFC is disabled. */
static void guard_fault(unsigned kind, TCGv_i32 addr, unsigned size)
{
    gen_helper_pi32v2_guard_fault(tcg_env, tcg_constant_i32(kind), addr,
                                  tcg_constant_i32(size));
}
/* A store must not intersect an enabled write window (before the store). */
static void check_write(TCGv_i32 addr, unsigned size)
{
    TCGv_i32 last = tcg_temp_new_i32(), bound = tcg_temp_new_i32();
    tcg_gen_addi_i32(last, addr, size - 1);
    for (unsigned i = 0; i < 3; i++) {
        TCGLabel *clear = gen_new_label();
        tcg_gen_ld_i32(bound, tcg_env, offsetof(CPUPi32v2State, write_high[i]));
        tcg_gen_brcond_i32(TCG_COND_GTU, addr, bound, clear);
        tcg_gen_ld_i32(bound, tcg_env, offsetof(CPUPi32v2State, write_low[i]));
        tcg_gen_brcond_i32(TCG_COND_LTU, last, bound, clear);
        guard_fault(PI32V2_GUARD_WRITE, addr, size);
        gen_set_label(clear);
    }
}
/* After every SP write: SP must lie in the current context's stack window. */
static void check_stack(PiDisasContext *d)
{
    unsigned w = d->in_irq;
    TCGLabel *bad = gen_new_label(), *ok = gen_new_label();
    TCGv_i32 bound = tcg_temp_new_i32();
    tcg_gen_ld_i32(bound, tcg_env, offsetof(CPUPi32v2State, stack_low[w]));
    tcg_gen_brcond_i32(TCG_COND_LTU, spr[SP], bound, bad);
    tcg_gen_ld_i32(bound, tcg_env, offsetof(CPUPi32v2State, stack_high[w]));
    tcg_gen_brcond_i32(TCG_COND_LEU, spr[SP], bound, ok);
    gen_set_label(bad);
    guard_fault(PI32V2_GUARD_STACK, spr[SP], 0);
    gen_set_label(ok);
}
static int fetch_refused(CPUState *cs, uint32_t address, unsigned size)
{
    Pi32v2CPU *cpu = PI32V2_CPU(cs);
    if (!cpu->ops || !cpu->ops->fetch_fault) {
        return -1;
    }
    return cpu->ops->fetch_fault(cpu_env(cs), address, size);
}
/* ADD (mode 0) and SUB (mode 1) set PSR V, C, Z and N inline; ADC and SBC
 * (modes 2 and 3) read the incoming carry in the helper. */
static void gen_alu(TCGv_i32 dest, TCGv_i32 a, TCGv_i32 b, unsigned mode)
{
    if (mode > 1) {
        gen_helper_pi32v2_alu(dest, tcg_env, a, b, tcg_constant_i32(mode));
        return;
    }
    TCGv_i32 r = tcg_temp_new_i32(), flags = tcg_temp_new_i32();
    TCGv_i32 bit = tcg_temp_new_i32(), ov = tcg_temp_new_i32();
    if (mode) {
        tcg_gen_sub_i32(r, a, b);
        tcg_gen_setcond_i32(TCG_COND_GEU, flags, a, b);      /* C: no borrow */
        tcg_gen_xor_i32(ov, a, b);
    } else {
        tcg_gen_add_i32(r, a, b);
        tcg_gen_setcond_i32(TCG_COND_LTU, flags, r, a);      /* C: carry out */
        tcg_gen_eqv_i32(ov, a, b);
    }
    tcg_gen_xor_i32(bit, a, r);
    tcg_gen_and_i32(ov, ov, bit);
    tcg_gen_shri_i32(ov, ov, 31);                            /* V */
    tcg_gen_shli_i32(flags, flags, 1);
    tcg_gen_or_i32(flags, flags, ov);
    tcg_gen_setcondi_i32(TCG_COND_EQ, bit, r, 0);            /* Z */
    tcg_gen_shli_i32(bit, bit, 2);
    tcg_gen_or_i32(flags, flags, bit);
    tcg_gen_shri_i32(bit, r, 31);                            /* N */
    tcg_gen_shli_i32(bit, bit, 3);
    tcg_gen_or_i32(flags, flags, bit);
    tcg_gen_andi_i32(spr[PSR], spr[PSR], ~15u);
    tcg_gen_or_i32(spr[PSR], spr[PSR], flags);
    tcg_gen_mov_i32(dest, r);
}
/* An instruction that accesses memory ends its TB. With icount, MMIO is
 * allowed only in a TB's last instruction; an earlier access would replay
 * the whole instruction, including register updates that precede it. */
static void load(PiDisasContext *d, TCGv_i32 value, TCGv_i32 addr, MemOp op)
{
    translator_io_start(&d->base);
    tcg_gen_qemu_ld_i32(value, addr, 0, op);
}
static void store(PiDisasContext *d, TCGv_i32 value, TCGv_i32 addr, MemOp op)
{
    translator_io_start(&d->base);
    check_write(addr, memop_size(op));
    tcg_gen_qemu_st_i32(value, addr, 0, op);
}
/* A taken branch enters the ETM ring when the trace is enabled. */
static void record_branch(PiDisasContext *d)
{
    TCGLabel *off = gen_new_label();
    TCGv_i32 value = tcg_temp_new_i32();
    TCGv_i64 count = tcg_temp_new_i64();
    tcg_gen_ld_i32(value, tcg_env, offsetof(CPUPi32v2State, etm_on));
    tcg_gen_brcondi_i32(TCG_COND_EQ, value, 0, off);
    for (unsigned i = 3; i > 0; i--) {
        tcg_gen_ld_i32(value, tcg_env, offsetof(CPUPi32v2State, branch_pc[i - 1]));
        tcg_gen_st_i32(value, tcg_env, offsetof(CPUPi32v2State, branch_pc[i]));
    }
    tcg_gen_st_i32(pc, tcg_env, offsetof(CPUPi32v2State, branch_pc[0]));
    tcg_gen_ld_i64(count, tcg_env, offsetof(CPUPi32v2State, branches));
    tcg_gen_addi_i64(count, count, 1);
    tcg_gen_st_i64(count, tcg_env, offsetof(CPUPi32v2State, branches));
    gen_set_label(off);
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
    check_stack(d);
    store(d, value, spr[SP], MO_LEUL | MO_ALIGN);
}
static void pop(PiDisasContext *d, TCGv_i32 value)
{
    load(d, value, spr[SP], MO_LEUL | MO_ALIGN);
    tcg_gen_addi_i32(spr[SP], spr[SP], 4);
    check_stack(d);
}
/* Unchained transfers find their successor without returning to the CPU
 * loop. Completing an IF arm or REP block exits the loop from the advance
 * helper when an IRQ deferred by that block is pending. */
static void jump(uint32_t dest)
{
    gen_helper_pi32v2_advance(pc, tcg_env, tcg_constant_i32(dest));
    tcg_gen_lookup_and_goto_ptr();
}
/* Each static successor owns one QEMU chain slot. Active predicates keep
 * dispatcher exits so completion can redirect PC and admit pending IRQs. */
static void chain_jump(PiDisasContext *d, uint32_t dest, unsigned slot)
{
    DisasContextBase *db = &d->base;
    if (!d->repeating && translator_use_goto_tb(db, dest)) {
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
    tcg_gen_lookup_and_goto_ptr();
    d->base.is_jmp = DISAS_NORETURN;
}
/* A short backward loop whose body only loads and branches: a polling
 * loop that helper_pi32v2_spin may fast-forward. Loads have no side effects here
 * except MMIO reads, whose values the identical-register test covers. */
static bool spin_loop(PiDisasContext *d, uint32_t dest)
{
    uint32_t here = d->base.pc_next;
    /* Translation may only read the TB's own pages. The body before the TB
     * start is assumed stable: a stock polling loop is loaded once. */
    if (d->predicated || d->repeating || dest >= here || here - dest > 32 ||
        !translator_is_same_page(&d->base, dest)) {
        return false;
    }
    for (uint32_t at = dest; at < here; at = instruction_end(d, at)) {
        uint16_t op = fetch(d, at);
        bool load = ((op & 0xe008) == 0x4008 || (op & 0xe000) == 0x6000) && !(op & 128);
        bool compare = (op & 0xfc00) == 0xf800 || (op & 0xfe00) == 0xfc00 ||
                       (op & 0xff00) == 0xfe00 || (op & 0xe008) == 0x4000;
        if (!load && !compare) { return false; }
    }
    return true;
}
static void spin_check(PiDisasContext *d, uint32_t dest)
{
    if (spin_loop(d, dest)) {
        gen_helper_pi32v2_spin(tcg_env, tcg_constant_i32(dest));
    }
}
static void branch(PiDisasContext *d, uint32_t dest, uint32_t next,
                   TCGv_i32 value, bool nonzero)
{
    TCGLabel *taken = gen_new_label();
    tcg_gen_brcondi_i32(nonzero ? TCG_COND_NE : TCG_COND_EQ, value, 0, taken);
    chain_jump(d, next, 0);
    gen_set_label(taken);
    spin_check(d, dest);
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
    spin_check(d, dest);
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
    d->repeating = d->env->repeat_end != 0;
    d->predicated = d->env->predicate_end != 0;   /* part of the TB flags */
    d->in_irq = d->env->in_irq;          /* part of the TB flags */
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
        if ((op & 0x20) && reg == SP) { check_stack(d); }
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
        if ((x & 128) && special == SP) { check_stack(d); }
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
    } else if (op == 0x1442) {
        /* Vendor 1442: USP = SP in stock's initial task handoff. */
        tcg_gen_mov_i32(spr[USP], spr[SP]);
    } else if (op == 0x1440 || op == 0x1441) {
        /* Vendor 1440/1441: SP = USP or SSP in stock's task switch. */
        tcg_gen_mov_i32(spr[SP], spr[op & 1 ? SSP : USP]);
        check_stack(d);
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
        gen_alu(gpr[op & 15], read_gpr(d, op & 15), read_gpr(d, (op >> 4) & 15), 0);
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
        gen_alu(gpr[a], read_gpr(d, b), read_gpr(d, c), (op & 0x200) != 0);
    } else if ((op & 0xe0c0) == 0x20c0) {
        int imm = sext(((op >> 8) & 31) | (((op >> 3) & 7) << 5), 8);
        gen_alu(gpr[a], read_gpr(d, a), tcg_constant_i32(imm), 0);
    } else if ((op & 0xe088) == 0x8008) {
        gen_alu(gpr[a], read_gpr(d, b), tcg_constant_i32((op >> 8) & 31), 0);
    } else if ((op & 0xe098) == 0x8088) {
        unsigned imm = (((op >> 5) & 3) << 5) | ((op >> 8) & 31);
        tcg_gen_addi_i32(gpr[a], spr[SP], imm);
    } else if ((op & 0xe01f) == 0x8002) {
        int32_t imm = sext((op >> 5) & 7, 3) * 128 + ((op >> 8) & 31) * 4;
        tcg_gen_addi_i32(spr[SP], spr[SP], imm);
        check_stack(d);
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
        gen_alu(gpr[x >> 12], read_gpr(d, (x >> 4) & 15), read_gpr(d, (x >> 8) & 15), (x & 15) == 2);
        next = here + 4;
    } else if (op == 0xe0b8) {
        uint16_t x = fetch(d, here + 2);
        if ((x & 15) != 0 && (x & 15) != 2) { goto illegal; }
        gen_alu(gpr[x >> 12], read_gpr(d, (x >> 4) & 15), read_gpr(d, (x >> 8) & 15), (x & 15) == 2 ? 3 : 2);
        next = here + 4;
    } else if ((op & 0xfff0) == 0xe0a0) {
        uint16_t x = fetch(d, here + 2);
        gen_alu(gpr[op & 15], tcg_constant_i32(packed_mask(x)), read_gpr(d, x >> 12), 1);
        next = here + 4;
    } else if ((op & 0xfff0) == 0xe0e0) {
        uint16_t x = fetch(d, here + 2);
        gen_alu(gpr[op & 15], read_gpr(d, x >> 12), tcg_constant_i32(packed_mask(x)), 0);
        next = here + 4;
    } else if ((op & 0xfff0) == 0xe0f0) {
        uint16_t x = fetch(d, here + 2);
        gen_alu(gpr[op & 15], read_gpr(d, x >> 12), tcg_constant_i32(packed_mask(x)), 1);
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
    } else if (op == 0xe1c4) {
        uint16_t x = fetch(d, here + 2);
        /* Vendor "<>" with E1C0's immediate fields. Stock FM-1 SHA-256
         * schedule code applies it with sigma's 7/18/17/19, so it rotates
         * right. Only mode 0 occurs. */
        if (x & 0x0c00) { goto illegal; }
        tcg_gen_rotri_i32(gpr[x >> 12], read_gpr(d, (x >> 4) & 15),
                          (x & 15) | ((x >> 8) & 3) * 16);
        next = here + 4;
    } else if (op == 0xe53f) {
        /* Single-precision FPU, encodings from the vendor assembler
         * (-mfprev1): D = bits 12-15, A = bits 4-7, B = bits 8-11. Kinds
         * 0-3 are A +, -, *, / B; 5/6 min/max; 7/8 D +=/-= A * B; 15 a
         * unary op of B selected by bits 4-7 (8 itof, 9 unsigned itof,
         * 1 and 5 truncate to int). Semantics are in helper.c. */
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 15, dest = x >> 12, unary = (x >> 4) & 15;
        TCGv_i32 a = read_gpr(d, (x >> 4) & 15), b = read_gpr(d, (x >> 8) & 15);
        if (kind == 15 && (unary == 1 || unary == 5 || unary == 8 || unary == 9)) {
            gen_helper_pi32v2_funary(gpr[dest], tcg_env, tcg_constant_i32(unary), b);
        } else if (kind == 7 || kind == 8) {
            gen_helper_pi32v2_fmac(gpr[dest], tcg_env, tcg_constant_i32(kind),
                                   read_gpr(d, dest), a, b);
        } else if (kind <= 3 || kind == 5 || kind == 6) {
            gen_helper_pi32v2_fop(gpr[dest], tcg_env, tcg_constant_i32(kind), a, b);
        } else {
            goto illegal;
        }
        next = here + 4;
    } else if (op == 0xe180) {
        uint16_t x = fetch(d, here + 2);
        /* Measured on an FM-1: clz(0) is 32. */
        if (x & 255) { goto illegal; }
        tcg_gen_clzi_i32(gpr[x >> 12], read_gpr(d, (x >> 8) & 15), 32);
        next = here + 4;
    } else if (op == 0xe1f6) {
        uint16_t x = fetch(d, here + 2);
        unsigned pair = x >> 12, source = (x >> 4) & 15;
        /* rD+1_rD = rS+1_rS / rT (u): a full 64-bit quotient. Measured on an
         * FM-1, division by zero gives 0. Only the unsigned form occurs. */
        if ((x & 15) || (pair & 1) || (source & 1)) { goto illegal; }
        TCGv_i64 value = tcg_temp_new_i64();
        tcg_gen_concat_i32_i64(value, read_gpr(d, source), read_gpr(d, source + 1));
        gen_helper_pi32v2_divu64(value, value, read_gpr(d, (x >> 8) & 15));
        tcg_gen_extr_i64_i32(gpr[pair], gpr[pair + 1], value);
        next = here + 4;
    } else if (op == 0xe1fc) {
        uint16_t x = fetch(d, here + 2);
        unsigned pair = x >> 12;
        /* rD+1_rD += rA * rB (u). Measured on an FM-1: C is the 64-bit carry
         * out; V, Z and N are unchanged. Only the unsigned form occurs. */
        if ((x & 15) || (pair & 1)) { goto illegal; }
        TCGv_i64 sum = tcg_temp_new_i64(), product = tcg_temp_new_i64();
        TCGv_i64 left = tcg_temp_new_i64(), right = tcg_temp_new_i64();
        TCGv_i32 carry = tcg_temp_new_i32();
        tcg_gen_extu_i32_i64(left, read_gpr(d, (x >> 4) & 15));
        tcg_gen_extu_i32_i64(right, read_gpr(d, (x >> 8) & 15));
        tcg_gen_mul_i64(product, left, right);
        tcg_gen_concat_i32_i64(left, read_gpr(d, pair), read_gpr(d, pair + 1));
        tcg_gen_add_i64(sum, left, product);
        tcg_gen_setcond_i64(TCG_COND_LTU, product, sum, left);
        tcg_gen_extrl_i64_i32(carry, product);
        tcg_gen_extr_i64_i32(gpr[pair], gpr[pair + 1], sum);
        tcg_gen_shli_i32(carry, carry, 1);
        tcg_gen_andi_i32(spr[PSR], spr[PSR], ~2u);
        tcg_gen_or_i32(spr[PSR], spr[PSR], carry);
        next = here + 4;
    } else if (op == 0xe1d0) {
        uint16_t x = fetch(d, here + 2);
        unsigned pair = x >> 12, mode = (x >> 10) & 3;
        unsigned shift = (x & 15) | ((x >> 8) & 3) * 16;
        /* Vendor disassembly (E1D0/0100 r1_r0 <<= 16, E1D0/0A0F r1_r0 >>= 47,
         * E1D0/0D0E r1_r0 >>>= 30): E1C0's immediate shift fields applied to
         * an even/odd pair in place. Every saved form has bits 4-7 clear and an
         * even pair; mode 1 and other forms remain deferred. */
        if ((x & 0xf0) || (pair & 1) || mode == 1) { goto illegal; }
        TCGv_i64 value = tcg_temp_new_i64();
        tcg_gen_concat_i32_i64(value, read_gpr(d, pair), read_gpr(d, pair + 1));
        if (mode == 0) { tcg_gen_shli_i64(value, value, shift); }
        else if (mode == 2) { tcg_gen_shri_i64(value, value, shift); }
        else { tcg_gen_sari_i64(value, value, shift); }
        tcg_gen_extr_i64_i32(gpr[pair], gpr[pair + 1], value);
        next = here + 4;
    } else if (op == 0xe1f8) {
        uint16_t x = fetch(d, here + 2);
        unsigned pair = (x >> 13) * 2;
        /* Vendor disassembly (E1F8/5220 r5_r4 = r2 * r2 (s), E1F8/A420
         * r11_r10 = r2 * r4 (u)): 32x32 -> 64-bit product into an even/odd
         * pair; bit 12 selects signed operands. Every saved form has a zero
         * low nibble; others remain deferred. Flags are unchanged, as MUL. */
        if (x & 15) { goto illegal; }
        TCGv_i32 low = tcg_temp_new_i32(), high = tcg_temp_new_i32();
        TCGv_i32 left = read_gpr(d, (x >> 4) & 15), right = read_gpr(d, (x >> 8) & 15);
        if (x & 0x1000) { tcg_gen_muls2_i32(low, high, left, right); }
        else { tcg_gen_mulu2_i32(low, high, left, right); }
        tcg_gen_mov_i32(gpr[pair], low);
        tcg_gen_mov_i32(gpr[pair + 1], high);
        next = here + 4;
    } else if (op == 0xe1d8) {
        uint16_t x = fetch(d, here + 2);
        unsigned pair = x >> 12;
        /* Reached E1D8/0600 shifts r1:r0 left by r6. Independent finite
         * probes establish identity at zero and zero for counts >= 64.
         * Kind 2 is the vendor's ">>=" (E1D8/4202 r5_r4 >>= r2), the logical
         * right shift, given the same large-count result. Other directions
         * and noncanonical fields remain deferred. */
        unsigned kind = x & 255;
        if ((kind != 0 && kind != 2) || (pair & 1)) { goto illegal; }
        TCGv_i32 shift = read_gpr(d, (x >> 8) & 15);
        TCGv_i64 value = tcg_temp_new_i64(), wide_shift = tcg_temp_new_i64();
        TCGLabel *large = gen_new_label(), *end = gen_new_label();
        tcg_gen_concat_i32_i64(value, read_gpr(d, pair), read_gpr(d, pair + 1));
        tcg_gen_brcondi_i32(TCG_COND_GEU, shift, 64, large);
        tcg_gen_extu_i32_i64(wide_shift, shift);
        if (kind) { tcg_gen_shr_i64(value, value, wide_shift); }
        else { tcg_gen_shl_i64(value, value, wide_shift); }
        tcg_gen_br(end);
        gen_set_label(large);
        tcg_gen_movi_i64(value, 0);
        gen_set_label(end);
        tcg_gen_extr_i64_i32(gpr[pair], gpr[pair + 1], value);
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
               (op & 0xfff0) == 0xed10 || (op & 0xfff0) == 0xee10 ||
               (op & 0xfff0) == 0xed90 || (op & 0xfff0) == 0xee90 ||
               (op & 0xfff0) == 0xe920 || (op & 0xfff0) == 0xe990 ||
               (op & 0xfff0) == 0xec30 ||
               (op & 0xfff0) == 0xec90 || (op & 0xfff0) == 0xeca0 ||
               (op & 0xfff0) == 0xeea0 ||
               (op & 0xfff0) == 0xed20 || (op & 0xfff0) == 0xedb0 ||
               (op & 0xfff0) == 0xee30 ||
               (op & 0xfff0) == 0xe8a0) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = (op >> 4) & 255;
        TCGv_i32 left = read_gpr(d, op & 15), right = NULL, result = tcg_temp_new_i32();
        TCGCond cond = TCG_COND_NEVER;
        bool register_kind = kind == 0x81 || kind == 0x89 || kind == 0x91 || kind == 0x99 ||
                             kind == 0xc1 || kind == 0xc9 || kind == 0xd1 || kind == 0xd9 ||
                             kind == 0xe1 || kind == 0xe9;
        if (register_kind && (x & 255) == 0x80) {
            /* IFF block, measured on an FM-1 (EE11 0080/0280): bit 7 makes
             * the register form a single-precision compare under the same
             * condition (the branch kind + 1); equal signed zeros are equal. */
            gen_helper_pi32v2_fcmp(result, tcg_env, tcg_constant_i32(kind - 1),
                                   left, read_gpr(d, (x >> 8) & 15));
        } else if (kind == 0xa1) {
            if (x & 127) { goto illegal; }
            TCGv_i32 masked = tcg_temp_new_i32();
            tcg_gen_and_i32(masked, left, read_gpr(d, (x >> 8) & 15));
            left = masked; right = tcg_constant_i32(0); cond = x & 128 ? TCG_COND_NE : TCG_COND_EQ;
        } else if (kind == 0xa2 || kind == 0xa3) {
            TCGv_i32 masked = tcg_temp_new_i32();
            tcg_gen_andi_i32(masked, left, packed_mask(x));
            left = masked; right = tcg_constant_i32(0);
            cond = kind == 0xa2 ? TCG_COND_EQ : TCG_COND_NE;
        } else if (kind == 0x92 || kind == 0xca || kind == 0xd2 || kind == 0xea) {
            /* E920/ED20 have pinned packed constructors. Saved vendor
             * ECA1/0980 and complete-state oracle cases establish packed LE.
             * Preserve the existing repeated-byte expansion policy. */
            /* Vendor EEA6/0B80 (Felucca 0x0202f6e2) is ifs (r6 <= 65536),
             * the signed counterpart of ECA0's packed unsigned LE. */
            right = tcg_constant_i32(packed_mask(x));
            cond = kind == 0x92 ? TCG_COND_GEU : kind == 0xca ? TCG_COND_LEU :
                   kind == 0xea ? TCG_COND_LE : TCG_COND_GE;
        } else if (kind == 0x8a) {
            /* E8A6/0B00 compares against packed 0x00020000, not literal
             * 0xB00 or zero. Separate executable discriminators agree;
             * the existing zero operand is the same packed NE operation. */
            right = tcg_constant_i32(packed_mask(x)); cond = TCG_COND_NE;
        } else if (kind == 0xdb) {
            /* Primary signed12 IF; vendor EDB5/0000 selects r5 < 0. */
            right = tcg_constant_i32(sext(x & 4095, 12));
            cond = TCG_COND_LT;
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
        } else if (kind == 0xd1 || kind == 0xe1) {
            /* Admit the constructor's canonical zero low byte. */
            if (x & 255) { goto illegal; }
            right = read_gpr(d, (x >> 8) & 15);
            /* Vendor EE17/0F00 establishes signed r7 > r15. */
            cond = kind == 0xe1 ? TCG_COND_GT : TCG_COND_GE;
        } else if (kind == 0xd9 || kind == 0xe9) {
            if (x & 255) { goto illegal; }
            /* Primary D9 register IF and vendor ED90/0800 at stock
             * 0x02015492 select signed r0 < r8. Existing THEN/ELSE
             * boundaries apply; the IF itself has no memory access. */
            right = read_gpr(d, (x >> 8) & 15);
            cond = kind == 0xd9 ? TCG_COND_LT : TCG_COND_LE;
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
        if (right) { tcg_gen_setcond_i32(cond, result, left, right); }
        uint32_t then_end = here + 4, else_end;
        for (unsigned i = 0; i <= (x >> 14); i++) { then_end = instruction_end(d, then_end); }
        else_end = then_end;
        for (unsigned i = 0; i < ((x >> 12) & 3); i++) { else_end = instruction_end(d, else_end); }
        TCGv_i32 dest = tcg_temp_new_i32();
        gen_helper_pi32v2_if(dest, tcg_env, result, tcg_constant_i32(then_end), tcg_constant_i32(else_end));
        count(d);
        tcg_gen_mov_i32(pc, dest); tcg_gen_lookup_and_goto_ptr();
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
    } else if ((op & 0xffe0) == 0xef00 || (op & 0xffe0) == 0xefc0 ||
               (op & 0xffe0) == 0xef80 || op == 0xe864 || op == 0xe866) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 3;
        if ((op == 0xe864 && kind == 1) || (op & 0xffe0) == 0xef80) {
            /* Vendor E864/E401 and E405 XOR the full register into a word.
             * EF81/047F ANDs a packed mask into a word at base+4.
             * Keep each read/write in one I/O boundary to avoid MMIO replay. */
            translator_io_start(db);
        }
        TCGv_i32 addr = tcg_temp_new_i32(), value = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, x >> 12), op == 0xe864 || op == 0xe866 ? x & 252 :
                          (op & 31) * 4);
        load(d, value, addr, MO_LEUL | MO_ALIGN);
        if ((op & 0xffe0) == 0xefc0) { tcg_gen_andi_i32(value, value, ~packed_mask(x)); }
        else if ((op & 0xffe0) == 0xef80) { tcg_gen_andi_i32(value, value, packed_mask(x)); }
        else if (op != 0xe864 && op != 0xe866) { tcg_gen_ori_i32(value, value, packed_mask(x)); }
        else {
            TCGv_i32 operand = read_gpr(d, (x >> 8) & 15);
            if (op == 0xe866) {
                operand = bit_operand(operand, op);
            }
            if (kind == 0) { tcg_gen_or_i32(value, value, operand); }
            else if (kind == 1) { tcg_gen_xor_i32(value, value, operand); }
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
    } else if (op == 0xe86c || op == 0xe86d) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 3, shift = ((x >> 8) & 15) + (op & 1) * 16;
        if (kind == 1) { goto illegal; }
        /* Vendor E86C 3704 and separate executable probes establish an
         * immediate left shift; the pinned SLEIGH has no exact constructor.
         * Kinds 2 and 3 are the vendor's ">>=" and ">>>=", its logical and
         * arithmetic right-shift operators as in register shifts. E86D adds
         * 16 to the count (vendor E86D 1602: [r1+0] >>= 22). "<<<=" (kind 1)
         * stays open. */
        translator_io_start(db);
        TCGv_i32 addr = tcg_temp_new_i32(), value = tcg_temp_new_i32();
        tcg_gen_addi_i32(addr, read_gpr(d, x >> 12), x & 252);
        load(d, value, addr, MO_LEUL | MO_ALIGN);
        if (kind == 2) { tcg_gen_shri_i32(value, value, shift); }
        else if (kind == 3) { tcg_gen_sari_i32(value, value, shift); }
        else { tcg_gen_shli_i32(value, value, shift); }
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
    } else if ((op & 0xff88) == 0x1280 || (op & 0xff88) == 0x1380) {
        /* Stock vendor 13C0 at 0x0200de98 is r0 = b[r4++=r15] (u);
         * 13D1 at 0x0201cb3a is r1 = b[r5++=r15] (u).
         * Vendor 12F0 at 0x02016d86 is r0 = b[r7++=r13] (u).
         * These forms load the old base then advance by unscaled r13/r15;
         * other register-stride and store encodings remain unsupported.
         * Keep the unresolved destination/base alias rejected, as for
         * the accepted compact immediate post-index byte loads. */
        if (a == b) { goto illegal; }
        load(d, gpr[a], read_gpr(d, b), MO_UB);
        tcg_gen_add_i32(gpr[b], read_gpr(d, b), read_gpr(d, op & 0x100 ? 15 : 13));
    } else if ((op & 0xff88) == 0x0780 || (op & 0xff88) == 0x0788) {
        /* Vendor stock 079B at 0x0201948a is b[r1++=-1] = r3,
         * bundled with r6 = r4. Store at the incoming base before updating. */
        store(d, read_gpr(d, a), read_gpr(d, b), MO_UB);
        tcg_gen_addi_i32(gpr[b], read_gpr(d, b), op & 8 ? -1 : 1);
    } else if ((op & 0xff80) == 0x0680) {
        store(d, read_gpr(d, a), read_gpr(d, b), MO_LEUW | MO_ALIGN);
        tcg_gen_addi_i32(gpr[b], read_gpr(d, b), op & 8 ? -2 : 2);
    } else if ((op & 0xfff0) == 0xee50) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = op & 15;
        if (kind != 0 && kind != 1 && kind != 2 && kind != 3 && kind != 4 && kind != 5 && kind != 8 && kind != 10 && kind != 12) { goto illegal; }
        unsigned base = (x >> 4) & 15, reg = x >> 12;
        if ((kind == 8 || kind == 10 || kind == 12) && base == reg) { goto illegal; }
        TCGv_i32 addr = tcg_temp_new_i32();
        int32_t offset = (x & 15) | ((x >> 8) & 15) * 16;
        /* EE53 stores a byte at base + (imm8 - 256), without writeback.
         * Saved EE53 8F0F encodes b[r0-1] = r8, including high GPRs.
         * Vendor EE55 0B0A (Felucca 0x02002230) is r0 = b[r0+-70] (s), the
         * signed EE51 load; without writeback, base and destination may alias. */
        if (kind == 1 || kind == 3 || kind == 5) { offset -= 256; }
        tcg_gen_addi_i32(addr, read_gpr(d, base), offset);
        if (kind == 2 || kind == 3 || kind == 10) { store(d, read_gpr(d, x >> 12), addr, MO_UB); }
        /* Vendor EE5C/1E61 reads a signed byte at base+225 and updates
         * the base, matching the existing unsigned pre-index load policy. */
        else { load(d, gpr[x >> 12], addr, kind == 4 || kind == 5 || kind == 12 ? MO_SB : MO_UB); }
        if (kind == 8 || kind == 10 || kind == 12) { tcg_gen_mov_i32(gpr[base], addr); }
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
        /* Hardware ECD0 000B ([++r0=8] = r0) stores the incoming r0
         * at the advanced address, then leaves r0 advanced. Capture the
         * source before writeback, including source/base aliases. */
        int32_t offset = sext(op & 7, 3) * 256 + ((x >> 8) & 15) * 16 + ((x >> 2) & 3) * 4;
        translator_io_start(db);
        TCGv_i32 addr = tcg_temp_new_i32();
        TCGv_i32 value = tcg_temp_new_i32();
        tcg_gen_mov_i32(value, read_gpr(d, source));
        tcg_gen_addi_i32(addr, read_gpr(d, base), offset);
        tcg_gen_mov_i32(gpr[base], addr);
        store(d, value, addr, MO_LEUL | MO_ALIGN);
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
    } else if ((op & 0xfff8) == 0xecd8 &&
               (fetch(d, here + 2) & 3) < 2) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, reg = x >> 12;
        int32_t stride = sext(op & 7, 3) * 256 +
                         (x & 12) + ((x >> 8) & 15) * 16;
        /* Vendor ECD8 B005 stores at the incoming base, then adds four;
         * ECDA 0014 loads before adding 516. The opcode carries the signed
         * high displacement bits. Store aliases use the incoming base. */
        if (x & 1) { store(d, read_gpr(d, reg), read_gpr(d, base), MO_LEUL | MO_ALIGN); }
        else {
            if (base == reg) { goto illegal; }
            load(d, gpr[reg], read_gpr(d, base), MO_LEUL | MO_ALIGN);
        }
        tcg_gen_addi_i32(gpr[base], read_gpr(d, base), stride);
        next = here + 4;
    } else if (op == 0xecd8) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 15;
        if (kind != 2 && kind != 3 && kind != 10 && kind != 11) { goto illegal; }
        TCGv_i32 addr = tcg_temp_new_i32();
        tcg_gen_shli_i32(addr, read_gpr(d, (x >> 8) & 15), kind & 8 ? 2 : 0);
        tcg_gen_add_i32(addr, addr, read_gpr(d, (x >> 4) & 15));
        if (!(kind & 1)) { load(d, gpr[x >> 12], addr, MO_LEUL | MO_ALIGN); }
        else { store(d, read_gpr(d, x >> 12), addr, MO_LEUL | MO_ALIGN); }
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
        unsigned kind = x & 15, base = (x >> 4) & 15, dest = x >> 12;
        if (kind > 2 || (kind == 1 && base == dest)) {
            goto illegal;
        }
        TCGv_i32 addr = tcg_temp_new_i32();
        /* Vendor EDDC 3312 and separate executable probes establish the
         * unscaled incoming sum for loads. Vendor EDDC 0B31 stores the low
         * halfword at the same pre-indexed address. EDDC 1100 loads an
         * unsigned halfword, including a destination/index alias. Keep the unresolved
         * source/base store alias rejected, as for ECDC word stores. */
        tcg_gen_add_i32(addr, read_gpr(d, base), read_gpr(d, (x >> 8) & 15));
        /* Preserve the modeled pre-index writeback-before-access order. */
        tcg_gen_mov_i32(gpr[base], addr);
        if (kind == 1) { store(d, read_gpr(d, dest), addr, MO_LEUW | MO_ALIGN); }
        else { load(d, gpr[dest], addr, (kind == 2 ? MO_LESW : MO_LEUW) | MO_ALIGN); }
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
        if (kind != 0 && kind != 1 && kind != 2 && kind != 8 && kind != 9 && kind != 10) { goto illegal; }
        TCGv_i32 addr = tcg_temp_new_i32();
        /* Operand bit 3 scales the index by two. Kind 0 is the unsigned
         * unscaled halfword load, including destination/base aliases.
         * Vendor stock EDD8/5431 at 0x020278d0 is h[r3+r4] = r5;
         * kind 1 stores the low halfword at the unscaled incoming sum. */
        tcg_gen_shli_i32(addr, read_gpr(d, (x >> 8) & 15), kind & 8 ? 1 : 0);
        tcg_gen_add_i32(addr, addr, read_gpr(d, (x >> 4) & 15));
        if (kind == 0 || kind == 8) { load(d, gpr[x >> 12], addr, MO_LEUW | MO_ALIGN); }
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
    } else if ((op & 0xfffc) == 0xed50) {
        uint16_t x = fetch(d, here + 2);
        int32_t offset = sext(op & 3, 2) * 256 + ((x >> 8) & 15) * 16 + (x & 14);
        TCGv_i32 addr = tcg_temp_new_i32();
        /* ED50-ED53 use operand bit 0 to select stores of the low halfword.
         * As in ED54, the op's two low bits extend the offset signed:
         * vendor ED53/0A75 is h[r7+-92] = r0. */
        tcg_gen_addi_i32(addr, read_gpr(d, (x >> 4) & 15), offset);
        if (x & 1) { store(d, read_gpr(d, x >> 12), addr, MO_LEUW | MO_ALIGN); }
        else { load(d, gpr[x >> 12], addr, MO_LEUW | MO_ALIGN); }
        next = here + 4;
    } else if ((op & 0xfffc) == 0xed54) {
        uint16_t x = fetch(d, here + 2);
        if (x & 1) { goto illegal; }
        int32_t offset = sext(op & 3, 2) * 256 + ((x >> 8) & 15) * 16 + (x & 14);
        TCGv_i32 addr = tcg_temp_new_i32();
        /* Vendor ED54/63BC and ED55/52FC use offsets 60 and 300;
         * ED57/7FBC uses -4. The signed high two bits extend the displacement
         * without base writeback. Odd operands remain unsupported. */
        tcg_gen_addi_i32(addr, read_gpr(d, (x >> 4) & 15), offset);
        load(d, gpr[x >> 12], addr, MO_LESW | MO_ALIGN);
        next = here + 4;
    } else if ((op & 0xffc0) == 0xe100) {
        uint16_t x = fetch(d, here + 2);
        int32_t imm = x & 4095;
        if (op & 32) { imm += sext((op >> 4) & 3, 2) * 4096; }
        else if (op & 16) { imm += 4096; }
        gen_alu(gpr[op & 15], read_gpr(d, x >> 12), tcg_constant_i32(imm), 0);
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
        check_stack(d);
        next = here + 4;
    } else if (op == 0xe8f8) {
        uint16_t x = fetch(d, here + 2);
        tcg_gen_addi_i32(gpr[x >> 12], spr[SP], x & 4095);
        next = here + 4;
    } else if (op == 0xe8d9 || op == 0xe8d5) {
        /* E8D8/E8D4 register lists with RETS pushed first, or popped last
         * into PC as a return, like the 0470/0450 forms. */
        uint16_t mask = fetch(d, here + 2);
        next = here + 4;
        if (op == 0xe8d9) {
            push(d, spr[RETS]);
            for (int i = 15; i >= 0; i--) { if (mask & (1 << i)) { push(d, read_gpr(d, i)); } }
        } else {
            gen_helper_pi32v2_return_end(tcg_env, tcg_constant_i32(next));
            TCGv_i32 dest = tcg_temp_new_i32();
            for (int i = 0; i < 16; i++) { if (mask & (1 << i)) { pop(d, gpr[i]); } }
            pop(d, dest);
            count(d); dynamic_jump(d, dest);
        }
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
        retire_final_transfer(d, next);
        count(d); dynamic_jump(d, dest);
    } else if ((op & 0xff00) == 0x0300 || (op & 0xe00f) == 0x8000) {
        /* The immediate form repeats ((op >> 8) & 31) + 1 times; the helper
         * takes 16 + count in place of a register number. */
        bool immediate = (op & 0xe00f) == 0x8000;
        unsigned reg = immediate ? 16 + ((op >> 8) & 31) + 1 : op & 15;
        uint32_t end = next + (((op >> 4) & 15) + 1) * 2;
        /* REP snapshots its count, skips the byte span at zero, and writes
         * the remaining count back after each completed iteration. The body
         * may overwrite the count register. Its instructions must end exactly
         * at the span's end; translate_insn faults any body instruction that
         * transfers control (branch, IF, nested REP), whatever its form. */
        bool qualified = true;
        for (uint32_t at = next; at < end; ) {
            uint32_t after = instruction_end(d, at);
            if (after > end) { qualified = false; break; }
            at = after;
        }
        if (!qualified && immediate) {
            gen_helper_pi32v2_illegal(tcg_env, tcg_constant_i32(op));
        } else if (!qualified) {
            TCGLabel *zero = gen_new_label();
            tcg_gen_brcondi_i32(TCG_COND_EQ, gpr[reg], 0, zero);
            gen_helper_pi32v2_illegal(tcg_env, tcg_constant_i32(op));
            gen_set_label(zero);
        }
        TCGv_i32 dest = tcg_temp_new_i32();
        gen_helper_pi32v2_repeat(dest, tcg_env, tcg_constant_i32(reg),
                                 tcg_constant_i32(next), tcg_constant_i32(end));
        count(d);
        tcg_gen_mov_i32(pc, dest);
        tcg_gen_lookup_and_goto_ptr();
        db->is_jmp = DISAS_NORETURN;
    } else if (op == 0x0410) {
        push(d, spr[RETS]);
    } else if (op == 0x0488) {
        /* A RETS-only restore advances SP and falls through to the tail call. */
        pop(d, spr[RETS]);
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
            /* A final selected stack return closes its IF arm just like RTS,
             * before restoring registers or transferring to the popped PC. */
            gen_helper_pi32v2_return_end(tcg_env, tcg_constant_i32(next));
            TCGv_i32 dest = tcg_temp_new_i32();
            for (unsigned i = 4; i <= hi; i++) { pop(d, gpr[i]); }
            pop(d, dest);
            count(d); dynamic_jump(d, dest);
        }
    } else if (((op & 0xffc0) == 0x04c0 || (op & 0xffc0) == 0x0480) &&
               (op & 63) && !(op & 63 & ~0x29)) {
        /* Special-register list: bits 0, 3 and 5 name RETI, RETS and PSR
         * (their register numbers). Push stores PSR, RETS, RETI in that
         * order; pop reverses it. Stock uses 04E9/04A9, 04E8/04A8 and the
         * RETI-only 04C1/0481. */
        static const unsigned order[] = {PSR, RETS, RETI};
        bool is_push = (op & 0xffc0) == 0x04c0;
        for (unsigned i = 0; i < 3; i++) {
            unsigned reg = order[is_push ? i : 2 - i];
            if (!(op & (1u << reg))) { continue; }
            if (is_push) { push(d, spr[reg]); } else { pop(d, spr[reg]); }
        }
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
        retire_final_transfer(d, next);
        count(d); record_branch(d); chain_jump(d, next + delta, 0); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xe00c) == 0x8004) {
        int32_t delta = sext(((op & 3) << 10) | (((op >> 4) & 15) << 6) | (((op >> 8) & 31) << 1), 12);
        retire_final_transfer(d, next);
        count(d); record_branch(d); chain_jump(d, next + delta, 0); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xe08f) == 0x8001) {
        int32_t delta = sext((((op >> 4) & 7) << 6) | (((op >> 8) & 31) << 1), 9);
        set_call_return(d, next);
        count(d); record_branch(d); jump(next + delta); db->is_jmp = DISAS_NORETURN;
    } else if ((op & 0xe008) == 0x4000) {
        int32_t delta = sext((((op >> 4) & 7) << 6) | (((op >> 8) & 31) << 1), 9);
        count(d); branch(d, next + delta, next, read_gpr(d, a), op & 128);
    } else if (op == 0xe840) {
        /* IFEQ branches on PSR Z, as left by TESTSET in SDK spinlocks. */
        TCGv_i32 zero = tcg_temp_new_i32();
        tcg_gen_andi_i32(zero, spr[PSR], 4);
        next = here + 4;
        count(d); branch(d, next + (int16_t)fetch(d, here + 2) * 2, next, zero, true);
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
               op == 0xff2b || op == 0xff2c || op == 0xff2d) {
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
        case 12: cond = TCG_COND_GT; break;     /* vendor FF2C 157C: ifs (r1 > 0x3f000000) */
        default: cond = TCG_COND_LE; break;
        }
        /* Vendor FF2D/3D7A compares signed r3 <= 16000. The pinned
         * primary instead labels FF0D as packed <=; keep that discrepancy
         * explicit and preserve the existing packed-repeat model policy. */
        next = here + 6;
        count(d); compare_branch(d, next + (int16_t)displacement * 2, next, cond,
                                 read_gpr(d, x >> 12), tcg_constant_i32(packed_mask(x)));
    } else if (op == 0xff0a || op == 0xff0b || op == 0xff0d || op == 0xff40 ||
               op == 0xff42 || op == 0xff43 || op == 0xff48 || op == 0xff49 || op == 0xff4a ||
               op == 0xff4b || op == 0xff4c || op == 0xff4d) {
        uint16_t x = fetch(d, here + 2), displacement = fetch(d, here + 4);
        TCGCond cond;
        TCGv_i32 right;
        if (op == 0xff0a || op == 0xff0b || op == 0xff0d) {
            /* Saved vendor literals and independent full-state probes resolve
             * primary unsigned/packed operand contradictions as signed12. */
            /* Vendor FF0A/5460 0007 (Felucca 0x020329f2): ifs (r5 >= 1120). */
            cond = op == 0xff0a ? TCG_COND_GE : op == 0xff0b ? TCG_COND_LT : TCG_COND_LE;
            right = tcg_constant_i32(sext(x & 4095, 12));
        } else {
            if (x & 255) { goto illegal; }
            switch (op) {
            case 0xff40: cond = TCG_COND_EQ; break;
            case 0xff42: cond = TCG_COND_GEU; break;
            case 0xff43: cond = TCG_COND_LTU; break;
            case 0xff48: cond = TCG_COND_GTU; break;
            case 0xff49: cond = TCG_COND_LEU; break;
            case 0xff4a: cond = TCG_COND_GE; break;
            case 0xff4b: cond = TCG_COND_LT; break;
            case 0xff4c: cond = TCG_COND_GT; break;
            default: cond = TCG_COND_LE; break; /* Exact FF4D. */
            }
            /* The right register is C, bits 8-11 (the primary B field is
             * contradicted); displacements count from the six-byte end.
             * Vendor: FF4A; stock FF4B/5100 ifs (r5 < r1) at 0x01c022e4,
             * FF4C/8200 ifs (r8 > r2) at 0x0201435e, FF4D/0100
             * ifs (r0 <= r1) at 0x0200ac02. */
            right = read_gpr(d, (x >> 8) & 15);
        }
        next = here + 6;
        if (op == 0xff49) {
            gen_helper_pi32v2_unsigned_le_end(tcg_env, tcg_constant_i32(next));
        }
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
        if ((x & 0x0e00) != 0 && (x & 0x0e00) != 0x0800) { goto illegal; }
        next = here + 4;
        if (x & 0x0800) {
            /* IFF: the same condition slots on single-precision values. */
            TCGv_i32 taken = tcg_temp_new_i32();
            gen_helper_pi32v2_fcmp(taken, tcg_env, tcg_constant_i32((op >> 4) & 255),
                                   read_gpr(d, x >> 12), read_gpr(d, op & 15));
            count(d); branch(d, next + sext(x & 511, 9) * 2, next, taken, true);
        } else {
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
            count(d); compare_branch(d, next + sext(x & 511, 9) * 2, next, cond,
                                     read_gpr(d, x >> 12), read_gpr(d, op & 15));
        }
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
        /* EQ and NE compare with a signed immediate, like the signed forms:
         * vendor disassembly of stock FM-1 firmware shows F874/FC04 as
         * "if (r4 == -2)" and never an EQ/NE operand of 512 or more. */
        if (kind == 0x30 || kind == 0x31 || kind >= 0x3a) { immediate = sext(immediate, 10); }
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
    } else if ((op & 0xfff0) == 0x00d0) {
        count(d); dynamic_jump(d, read_gpr(d, op & 15));
    } else if ((op & 0xfff0) == 0x0230) {
        gen_helper_pi32v2_flush(tcg_env, read_gpr(d, op & 15));
        db->is_jmp = DISAS_EXIT;
    } else if (op == 0x0080) {
        retire_final_transfer(d, next);
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
    } else if (op == 0x0040 || op == 0x0041) {
        /* Single-threaded TCG still switches cores between instructions.
         * LOCKSET protects the whole region through LOCKCLR. */
        gen_helper_pi32v2_lock(tcg_env, tcg_constant_i32(op == 0x0041));
        tcg_gen_mb(TCG_MO_ALL | TCG_BAR_SC);
    } else if ((op & 0xfff0) == 0x00b0) {
        /* TESTSET b[rA], measured on an FM-1: writes 0xff and copies the old
         * byte's low nibble into PSR N, Z, C and V. */
        TCGv_i32 old = tcg_temp_new_i32(), addr = read_gpr(d, op & 15);
        load(d, old, addr, MO_UB);
        store(d, tcg_constant_i32(0xff), addr, MO_UB);
        tcg_gen_andi_i32(old, old, 15);
        tcg_gen_andi_i32(spr[PSR], spr[PSR], ~15u);
        tcg_gen_or_i32(spr[PSR], spr[PSR], old);
    } else if (op == 0x0001) {
        /* IDLE waits for an interrupt, which returns to the next one. */
        count(d);
        tcg_gen_movi_i32(pc, next);
        gen_helper_pi32v2_idle(tcg_env);
        db->is_jmp = DISAS_NORETURN;
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
/* A tail offset load (6000 family, no writeback) whose destination the head
 * also writes is a discarded read: the head's value remains. Stock FM-1
 * firmware passes "sys" to clk_get from F100 A06D / 6000 (r0 = r10 + 109 ||
 * r0 = [r0+0]). The tail is translated first, so the head's write holds.
 * Other overlapping writes stay rejected. */
static bool discarded_load(uint16_t tail)
{
    return (tail & 0xe080) == 0x6000;
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
    if ((op & 0xff88) == 0x0700 || (op & 0xff88) == 0x0708) {
        if ((op & 7) == ((op >> 4) & 7)) { return -1; }
        return (1u << ((op >> 4) & 7)) | (1u << (op & 7));
    }
    if ((op & 0xff88) == 0x0780 || (op & 0xff88) == 0x0788 ||
        (op & 0xff88) == 0x0680) {
        return 1u << ((op >> 4) & 7);
    }
    if ((op & 0xfff8) == 0xecd8 && (fetch(d, here + 2) & 3) < 2) {
        uint16_t x = fetch(d, here + 2);
        unsigned base = (x >> 4) & 15, reg = x >> 12;
        if (!(x & 1) && base == reg) { return -1; }
        return (1u << base) | (x & 1 ? 0 : 1u << reg);
    }
    if (op == 0xeed2) { return 1u << ((fetch(d, here + 2) >> 4) & 15); }
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
        (op & 0xfff0) == 0xe140 ||
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
    if (op == 0xe1c4) {
        uint16_t x = fetch(d, here + 2);
        return x & 0x0c00 ? -1 : 1u << (x >> 12);
    }
    if (op == 0xe1f6) {
        uint16_t x = fetch(d, here + 2);
        return (x & 15) || (x & 0x1010) ? -1 : 3u << (x >> 12);
    }
    if (op == 0xe1f8) {
        uint16_t x = fetch(d, here + 2);
        return x & 15 ? -1 : 3u << ((x >> 13) * 2);
    }
    if (op == 0xe1d0) {
        uint16_t x = fetch(d, here + 2);
        return (x & 0xf0) || ((x >> 12) & 1) || ((x >> 10) & 3) == 1 ? -1 : 3u << (x >> 12);
    }
    if (op == 0xe0b8) {
        uint16_t x = fetch(d, here + 2);
        return (x & 15) == 0 || (x & 15) == 2 ? 1u << (x >> 12) : -1;
    }
    if (op == 0xe53f) {
        uint16_t x = fetch(d, here + 2);
        unsigned kind = x & 15, unary = (x >> 4) & 15;
        bool known = kind <= 3 || (kind >= 5 && kind <= 8) ||
                     (kind == 15 && (unary == 1 || unary == 5 || unary == 8 || unary == 9));
        return known ? 1u << (x >> 12) : -1;
    }
    if (op == 0xe1d8) {
        uint16_t x = fetch(d, here + 2);
        return ((x & 255) != 0 && (x & 255) != 2) || ((x >> 12) & 1) ? -1 : 3u << (x >> 12);
    }
    if (op == 0x0000 || (op & 0xffc0) == 0xea40) { return 0; }
    return -1;
}
static unsigned operation_size(uint16_t op)
{
    if ((op & 0xffc0) == 0xffc0 || (op & 0xfff0) == 0xffe0 || op == 0xff80 ||
        op == 0xff00 || op == 0xff01 || op == 0xff02 || op == 0xff03 || op == 0xff08 || op == 0xff09 || op == 0xff0c ||
        op == 0xff0a || op == 0xff0b || op == 0xff0d ||
        op == 0xff20 || op == 0xff21 || op == 0xff23 || op == 0xff28 || op == 0xff29 ||
        op == 0xff2a || op == 0xff2b || op == 0xff2d ||
        (op >= 0xff40 && op <= 0xff43) || (op >= 0xff48 && op <= 0xff4d) ||
        op == 0xff60 || op == 0xff61) { return 6; }
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

/* Observer instructions (stop, frame, loop) start a TB of their own. */
static bool observed(CPUState *cs, uint32_t address)
{
    Pi32v2CPU *cpu = PI32V2_CPU(cs);
    return address == cpu->stop_pc || (cpu->frame_pc && address == cpu->frame_pc) ||
           (cpu->loop_pc && address == cpu->loop_pc);
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
        /* Observe a completed guest frame, then execute its real next opcode.
         * The observer reads the virtual clock: a TB of its own. */
        translator_io_start(db);
        gen_helper_pi32v2_frame(tcg_env);
    }
    if (PI32V2_CPU(cs)->loop_pc && here == PI32V2_CPU(cs)->loop_pc) {
        translator_io_start(db);
        gen_helper_pi32v2_loop(tcg_env);
    }
    /* Fetch guards are decided at translation: the TB key carries the XIP
     * state and the guard generation. A refused fetch faults when executed
     * and never reads the refused bytes. */
    unsigned span = 2;
    int refused = fetch_refused(cs, here, span);
    if (refused < 0) {
        op = fetch(d, here);
        span = instruction_end(d, here) - here;
        refused = fetch_refused(cs, here, span);
    }
    if (refused >= 0) {
        guard_fault(refused, tcg_constant_i32(here), span);
        db->is_jmp = DISAS_NORETURN;
        db->pc_next = next;
        return;
    }
    /* A REP body is straight-line code: emit speculatively and replace a
     * control transfer with the unsupported-instruction fault. */
    TCGOp *body_start = d->repeating ? tcg_last_op() : NULL;
    bool parallel = op >> 13 == 6 || (op & 0xf800) == 0xf000;
    if (parallel) {
        /* Either half may touch MMIO. Ending the TB here lets QEMU enable
         * I/O before both effects, so replay cannot repeat a register update. */
        translator_io_start(db);
        uint16_t head = op >> 13 == 6 ? op & 0x1fff : op & ~0x1000;
        uint32_t tail_pc = here + operation_size(head);
        uint16_t tail = fetch(d, tail_pc);
        int head_writes = parallel_writes(d, here, head);
        int tail_writes = parallel_writes(d, tail_pc, tail);
        if (head_writes < 0 || tail_writes < 0 ||
            ((head_writes & tail_writes) && !discarded_load(tail))) {
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
    if (body_start && db->is_jmp == DISAS_NORETURN) {
        tcg_remove_ops_after(body_start);
        gen_helper_pi32v2_illegal(tcg_env, tcg_constant_i32(op));
    }
    db->pc_next = next;
    if (db->is_jmp == DISAS_NEXT &&
        (!translator_is_same_page(db, next + 5) || observed(cs, next))) {
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
    /* IF arms and REP bodies complete at every instruction boundary, so
     * they keep single-instruction TBs. Elsewhere a TB runs until a branch,
     * an IRQ-relevant state change or a memory access, all of which end it;
     * icount ends TBs at timer deadlines. IRQs are therefore still admitted
     * at the same instruction boundaries. */
    CPUPi32v2State *env = cpu_env(cs);
    if (env->repeat_end || env->predicate_end) {   /* as the TB flags */
        *max_insns = 1;
    }
    translator_loop(cs, tb, max_insns, start, host_pc, &ops, &d.base, TCG_TYPE_VA);
}
