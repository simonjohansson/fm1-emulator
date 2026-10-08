/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "qemu/osdep.h"
#include "qemu/error-report.h"
#include "cpu.h"
#include "exec/helper-proto.h"
#define HELPER_H "helper.h"
#include "exec/helper-info.c.inc"

void pi32v2_fail(CPUPi32v2State *env, const char *reason)
{
    Pi32v2CPU *cpu = PI32V2_CPU(env_cpu(env));
    if (cpu->observer_ops && cpu->observer_ops->fault) {
        cpu->observer_ops->fault(env, reason);
    }
    error_report("pi32v2: %s at PC 0x%08x after %" PRIu64 " instructions",
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
    Pi32v2CPU *cpu = PI32V2_CPU(env_cpu(env));
    if (cpu->observer_ops && cpu->observer_ops->finish) {
        cpu->observer_ops->finish(env);
    }
    pi32v2_fail(env, "checkpoint observer missing or returned");
}

void HELPER(pi32v2_frame)(CPUPi32v2State *env)
{
    Pi32v2CPU *cpu = PI32V2_CPU(env_cpu(env));
    if (cpu->observer_ops && cpu->observer_ops->frame) {
        cpu->observer_ops->frame(env);
    }
}

void HELPER(pi32v2_loop)(CPUPi32v2State *env)
{
    Pi32v2CPU *cpu = PI32V2_CPU(env_cpu(env));
    if (cpu->observer_ops && cpu->observer_ops->loop) {
        cpu->observer_ops->loop(env);
    }
}

void HELPER(pi32v2_budget)(CPUPi32v2State *env)
{
    pi32v2_fail(env, "instruction limit reached");
}

void pi32v2_check_access(CPUPi32v2State *env, uint32_t address,
                         unsigned size, unsigned flags)
{
    Pi32v2CPU *cpu = env_archcpu(env);
    if (cpu->ops && cpu->ops->check_access) {
        cpu->ops->check_access(env, address, size, flags);
    }
}

void pi32v2_note_branch(CPUPi32v2State *env)
{
    Pi32v2CPU *cpu = env_archcpu(env);
    if (cpu->ops && cpu->ops->note_branch) {
        cpu->ops->note_branch(env);
    }
}

void HELPER(pi32v2_access)(CPUPi32v2State *env, uint32_t address, uint32_t size, uint32_t flags)
{
    pi32v2_check_access(env, address, size, flags);
}

void HELPER(pi32v2_branch)(CPUPi32v2State *env)
{
    pi32v2_note_branch(env);
}

void HELPER(pi32v2_flush)(CPUPi32v2State *env, uint32_t address)
{
    /* Guest data stores already invalidate affected TCG code. The bounded
     * machine has synchronous coherent memory, so no cache queue remains. */
    pi32v2_check_access(env, address & ~31u, 32, 0);
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

uint32_t HELPER(pi32v2_call_return)(CPUPi32v2State *env, uint32_t next)
{
    /* Close a final selected CALL before its callee starts another block.
     * THEN+ELSE return handling disagrees with the separate reference; keep
     * that form explicit until the hardware contract is established. */
    if (next == env->predicate_end && env->predicate_from) {
        pi32v2_fail(env, "final THEN call with ELSE is unsupported");
    }
    return HELPER(pi32v2_advance)(env, next);
}

void HELPER(pi32v2_return_end)(CPUPi32v2State *env, uint32_t next)
{
    /* A final selected RTS retires the arm at its sequential boundary before
     * transferring to RETS. Match CALL's bounded policy; THEN with ELSE stays
     * explicit until that control-transfer contract is established. */
    if (next == env->predicate_end && env->predicate_from) {
        pi32v2_fail(env, "final THEN return with ELSE is unsupported");
    }
    HELPER(pi32v2_advance)(env, next);
}

void HELPER(pi32v2_unsigned_le_end)(CPUPi32v2State *env, uint32_t next)
{
    /* Retire a final selected FF49 at its sequential arm boundary, before
     * either destination starts another IF. Keep CALL/RTS's bounded policy
     * for the unresolved final THEN with ELSE control transfer. */
    if (next == env->predicate_end && env->predicate_from) {
        pi32v2_fail(env, "final THEN FF49 branch with ELSE is unsupported");
    }
    HELPER(pi32v2_advance)(env, next);
}

/* This new branch is established outside conditional blocks. The reference
 * disagrees for a final selected THEN with ELSE, and hardware evidence for
 * that combination is absent. Reject it before any retirement/branch effect. */
void HELPER(pi32v2_signed_branch_end)(CPUPi32v2State *env, uint32_t next)
{
    if (env->predicate_from && next == env->predicate_end) {
        pi32v2_fail(env, "final THEN signed-literal branch with ELSE is unsupported");
    }
}

/* FF41 has independent six-byte evidence, but the separate reference scans
 * it as four bytes inside IF arms. Preserve the scoped final-THEN/ELSE
 * rejection before retirement or branch effects; hardware remains unverified. */
void HELPER(pi32v2_long_register_ne_end)(CPUPi32v2State *env, uint32_t next)
{
    if (env->predicate_from && next == env->predicate_end) {
        pi32v2_fail(env, "final THEN FF41 register branch with ELSE is unsupported");
    }
}

/* Fresh implementation of the four observed condition bits. No Rust code
 * is linked or copied. The probe validates values, not all flag semantics. */
uint32_t HELPER(pi32v2_alu)(CPUPi32v2State *env, uint32_t a, uint32_t b,
                          uint32_t sub)
{
    if (sub <= 1) {
        uint32_t r = sub ? a - b : a + b;
        uint32_t carry = sub ? a >= b : r < a;
        uint32_t ov = sub ? ((a ^ b) & (a ^ r)) : (~(a ^ b) & (a ^ r));
        uint32_t flags = (ov >> 31) | (carry << 1) | ((r == 0) << 2) | ((r >> 31) << 3);
        env->spr[PSR] = (env->spr[PSR] & ~15u) | flags;
        return r;
    }
    /* Internal modes 2/3 are ADC/SBC. Preserve the incoming carry before
     * replacing flags; widen both sums to avoid intermediate wrap/overflow. */
    uint32_t cin = (env->spr[PSR] >> 1) & 1;
    uint32_t extra = sub == 3 ? !cin : cin;
    uint64_t right = (uint64_t)b + extra;
    uint64_t total = sub == 3 ? (uint64_t)a - right : (uint64_t)a + right;
    int64_t signed_total = sub == 3 ? (int64_t)(int32_t)a - (int32_t)b - extra
                                  : (int64_t)(int32_t)a + (int32_t)b + extra;
    uint32_t r = total;
    uint32_t carry = sub == 3 ? (uint64_t)a >= right : total > UINT32_MAX;
    uint32_t overflow = signed_total < INT32_MIN || signed_total > INT32_MAX;
    uint32_t flags = overflow | (carry << 1) | ((r == 0) << 2) | ((r >> 31) << 3);
    env->spr[PSR] = (env->spr[PSR] & ~15u) | flags;
    return r;
}

uint32_t HELPER(pi32v2_div)(CPUPi32v2State *env, uint32_t numerator, uint32_t denominator)
{
    if (!denominator) { pi32v2_fail(env, "divide-by-zero behavior is unsupported"); }
    return numerator / denominator;
}

uint32_t HELPER(pi32v2_divs)(CPUPi32v2State *env, uint32_t numerator, uint32_t denominator)
{
    if (!denominator) { pi32v2_fail(env, "divide-by-zero behavior is unsupported"); }
    if (numerator == 0x80000000u && denominator == 0xffffffffu) {
        pi32v2_fail(env, "signed-division-overflow behavior is unsupported");
    }
    return (int32_t)numerator / (int32_t)denominator;
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
    if (env->last_irq_source == 11) { env->irq11_rti_count++; }
    else if (env->last_irq_source == 63) { env->irq63_rti_count++; }
}
