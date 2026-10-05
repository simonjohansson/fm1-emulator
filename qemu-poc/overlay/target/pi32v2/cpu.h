/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef PI32V2_CPU_H
#define PI32V2_CPU_H
#include "cpu-qom.h"
#include "exec/cpu-defs.h"

/* Register numbering: Apache-2.0 Quarkslab pi32v2.slaspec. */
enum { RETI = 0, RETS = 3, PSR = 5, ICFG = 11, USP = 12, SSP = 13, SP = 14 };
typedef struct CPUArchState {
    uint32_t gpr[16], spr[16], pc;
    uint32_t irq_config, priority_mask;
    uint32_t predicate_from, predicate_to, predicate_end;
    bool in_irq;
    uint64_t instructions, irq_entries, rti_count;
    uint32_t last_irq_pc, last_irq_handler, entry_icfg, return_icfg;
} CPUPi32v2State;

struct ArchCPU {
    CPUState parent_obj;
    CPUPi32v2State env;
    uint32_t boot_pc, stop_pc, frame_pc;
    uint64_t instruction_limit;
    bool timer_fixture, foundation_fixture, display_fixture, diag_fixture;
    void *machine;
};
struct Pi32v2CPUClass {
    CPUClass parent_class;
    DeviceRealize parent_realize;
    ResettablePhases parent_phases;
};
#define CPU_RESOLVING_TYPE TYPE_PI32V2_CPU
void pi32v2_translate_init(void);
void pi32v2_translate_code(CPUState *, TranslationBlock *, int *, vaddr, void *);
G_NORETURN void fm1_poc_finish(CPUPi32v2State *env);
void fm1_poc_frame(CPUPi32v2State *env);
void fm1_poc_check_access(CPUPi32v2State *env, uint32_t address, unsigned size, unsigned flags);
void fm1_poc_note_branch(CPUPi32v2State *env);
G_NORETURN void pi32v2_fail(CPUPi32v2State *env, const char *reason);
#include "exec/cpu-all.h"

static inline void cpu_get_tb_cpu_state(CPUPi32v2State *env, vaddr *pc,
                                       uint64_t *cs_base, uint32_t *flags)
{
    *pc = env->pc;
    *cs_base = 0;
    *flags = env->in_irq;
}
#endif
