/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef PI32V2_CPU_H
#define PI32V2_CPU_H
#include "cpu-qom.h"
#include "exec/cpu-defs.h"
#include "exec/cpu-common.h"
#include "exec/cpu-interrupt.h"

/* Register numbering: Apache-2.0 Quarkslab pi32v2.slaspec. */
enum { RETI = 0, RETS = 3, PSR = 5, ICFG = 11, USP = 12, SSP = 13, SP = 14 };
/* TB flags: bit 0 in_irq, bit 1 an active REP block, bit 2 XIP fetch enabled,
 * bit 3 an active IF arm, bit 4 core 1 while core 0 has translation
 * observers; otherwise both cores share translations.
 * cs_base carries the machine's fetch-guard
 * generation. */
enum { PI32V2_TB_IRQ = 1, PI32V2_TB_REPEAT = 2, PI32V2_TB_XIP = 4,
       PI32V2_TB_PREDICATE = 8, PI32V2_TB_CORE1 = 16 };
/* Guard kinds reported through Pi32v2MachineOps.guard_fault. */
enum { PI32V2_GUARD_STACK, PI32V2_GUARD_WRITE, PI32V2_GUARD_PC,
       PI32V2_GUARD_XIP_DISABLED, PI32V2_GUARD_XIP_BOUNDS };
typedef struct CPUArchState {
    uint32_t gpr[16], spr[16], pc;
    uint32_t irq_config, priority_mask;
    uint32_t predicate_from, predicate_to, predicate_end;
    uint32_t repeat_start, repeat_end, repeat_register, repeat_remaining;
    bool in_irq;
    uint64_t instructions, irq_entries, rti_count;
    uint64_t irq11_entries, irq11_rti_count, irq63_entries, irq63_rti_count;
    uint32_t last_irq_source;
    uint32_t last_irq_pc, last_irq_handler, entry_icfg, return_icfg;
    /* Machine-maintained guard mirrors read by translated code; not
     * architectural state. Stack windows are indexed by in_irq and pass
     * every SP when the guard is off. An inactive write window has
     * low > high and never matches. */
    uint32_t stack_low[2], stack_high[2];
    uint32_t write_low[3], write_high[3];
    uint32_t fetch_epoch;
    bool xip_fetch;
    /* Branch trace written by translated code while the machine's ETM is
     * enabled: the PCs of the last four taken branches, newest first. */
    uint32_t etm_on, branch_pc[4];
    uint64_t branches;
} CPUPi32v2State;

/* Hardware and optional validation interfaces supplied by the machine.
 * fetch_fault returns the guard kind that refuses an instruction fetch of
 * size bytes at address, or -1. It has no side effects: translation calls it,
 * and a refused fetch faults when executed. The machine bumps fetch_epoch and
 * exits the CPU loop whenever its answer may change. */
typedef struct Pi32v2MachineOps {
    void (*reset_state)(CPUPi32v2State *env);
    bool (*select_irq)(CPUPi32v2State *env, unsigned *number, unsigned *priority);
    int (*fetch_fault)(CPUPi32v2State *env, uint32_t address, unsigned size);
    G_NORETURN void (*guard_fault)(CPUPi32v2State *env, unsigned kind,
                                   uint32_t address, unsigned size);
    void (*check_stack)(CPUPi32v2State *env);
    bool (*lock)(CPUPi32v2State *env, bool acquire);
} Pi32v2MachineOps;

typedef struct Pi32v2ObserverOps {
    void (*fault)(CPUPi32v2State *env, const char *reason);
    void (*finish)(CPUPi32v2State *env);
    void (*frame)(CPUPi32v2State *env);
    void (*loop)(CPUPi32v2State *env);
} Pi32v2ObserverOps;

struct ArchCPU {
    CPUState parent_obj;
    CPUPi32v2State env;
    /* Explicit loader entry and optional observer addresses/budget. */
    uint32_t boot_pc, stop_pc, frame_pc, loop_pc;
    uint64_t instruction_limit;
    const Pi32v2MachineOps *ops;
    const Pi32v2ObserverOps *observer_ops;
    void *machine;
    /* Host observer checkpoint, not architectural guest state. */
    bool observer_held;
    /* Translates apart from its peer, whose observers differ. */
    bool private_translation;
    bool lock_waiting;
    bool held_reset;
    bool core_paused;
    bool resume_requested;
};
struct Pi32v2CPUClass {
    CPUClass parent_class;
    DeviceRealize parent_realize;
    ResettablePhases parent_phases;
};
#define CPU_RESOLVING_TYPE TYPE_PI32V2_CPU
void pi32v2_translate_init(void);
void pi32v2_translate_code(CPUState *, TranslationBlock *, int *, vaddr, void *);
void pi32v2_check_stack(CPUPi32v2State *env);
G_NORETURN void pi32v2_guard_fault(CPUPi32v2State *env, unsigned kind,
                                   uint32_t address, unsigned size);
G_NORETURN void pi32v2_fail(CPUPi32v2State *env, const char *reason);
/* Look up cs's next TB afresh after fetch-guard state changed. */
void pi32v2_leave_chain(CPUState *cs);
#endif
