/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "qemu/osdep.h"
#include "qemu/error-report.h"
#include "cpu.h"
#include "exec/helper-proto.h"
#define HELPER_H "helper.h"
#include "exec/helper-info.c.inc"

void pi32v2_fail(CPUPi32v2State *env, const char *reason)
{
    error_report("pi32v2 PoC: %s at PC 0x%08x after %" PRIu64 " instructions",
                 reason, env->pc, env->instructions);
    exit(EXIT_FAILURE);
}

void HELPER(pi32v2_illegal)(CPUPi32v2State *env, uint32_t insn)
{
    g_autofree char *reason = g_strdup_printf("unsupported instruction 0x%04x", insn);
    pi32v2_fail(env, reason);
}

void HELPER(pi32v2_finish)(CPUPi32v2State *env)
{
    fm1_poc_finish(env);
}

void HELPER(pi32v2_frame)(CPUPi32v2State *env)
{
    fm1_poc_frame(env);
}

void HELPER(pi32v2_budget)(CPUPi32v2State *env)
{
    pi32v2_fail(env, "diagnostic instruction limit reached");
}

void HELPER(pi32v2_access)(CPUPi32v2State *env, uint32_t address, uint32_t size, uint32_t flags)
{
    fm1_poc_check_access(env, address, size, flags);
}

void HELPER(pi32v2_branch)(CPUPi32v2State *env)
{
    fm1_poc_note_branch(env);
}

uint32_t HELPER(pi32v2_if)(CPUPi32v2State *env, uint32_t result,
                          uint32_t then_end, uint32_t else_end)
{
    if (env->predicate_end) { pi32v2_fail(env, "nested conditional block is unsupported"); }
    env->predicate_end = result ? then_end : else_end;
    if (!result && then_end == else_end) { env->predicate_end = 0; }
    if (result && then_end != else_end) {
        env->predicate_from = then_end;
        env->predicate_to = else_end;
    }
    return result ? env->pc + 4 : then_end;
}

uint32_t HELPER(pi32v2_advance)(CPUPi32v2State *env, uint32_t next)
{
    if (env->predicate_end && next == env->predicate_end) {
        if (env->predicate_from) { next = env->predicate_to; }
        env->predicate_from = env->predicate_to = env->predicate_end = 0;
    }
    return next;
}

/* Fresh implementation of the four observed condition bits. No Rust code
 * is linked or copied. The probe validates values, not all flag semantics. */
uint32_t HELPER(pi32v2_alu)(CPUPi32v2State *env, uint32_t a, uint32_t b,
                          uint32_t sub)
{
    uint32_t r = sub ? a - b : a + b;
    uint32_t carry = sub ? a >= b : r < a;
    uint32_t ov = sub ? ((a ^ b) & (a ^ r)) : (~(a ^ b) & (a ^ r));
    uint32_t flags = (ov >> 31) | (carry << 1) | ((r == 0) << 2) | ((r >> 31) << 3);
    env->spr[PSR] = (env->spr[PSR] & ~15u) | flags;
    return r;
}

void HELPER(pi32v2_rti)(CPUPi32v2State *env)
{
    if (!env->in_irq) {
        pi32v2_fail(env, "rti outside interrupt context");
    }
    env->pc = env->spr[RETI];
    env->spr[SSP] = env->spr[SP];
    env->spr[SP] = env->spr[USP];
    env->in_irq = false;
    env->spr[ICFG] = (env->spr[ICFG] & ~255u) | 0x600;
    env->return_icfg = env->spr[ICFG];
    env->rti_count++;
}
