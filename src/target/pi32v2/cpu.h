/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef PI32V2_CPU_H
#define PI32V2_CPU_H
#include "cpu-qom.h"
#include "exec/cpu-defs.h"
#include "exec/cpu-common.h"
#include "exec/cpu-interrupt.h"

/* Register numbering: Apache-2.0 Quarkslab pi32v2.slaspec. */
enum { RETI = 0, RETS = 3, PSR = 5, ICFG = 11, USP = 12, SSP = 13, SP = 14 };
enum { PI32V2_TB_REPEAT = 2 };
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
} CPUPi32v2State;

/* Hardware and optional validation interfaces supplied by the machine. */
typedef struct Pi32v2MachineOps {
    void (*reset_state)(CPUPi32v2State *env);
    bool (*select_irq)(CPUPi32v2State *env, unsigned *number, unsigned *priority);
    void (*check_access)(CPUPi32v2State *env, uint32_t address,
                         unsigned size, unsigned flags);
    void (*note_branch)(CPUPi32v2State *env);
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
};
struct Pi32v2CPUClass {
    CPUClass parent_class;
    DeviceRealize parent_realize;
    ResettablePhases parent_phases;
};
#define CPU_RESOLVING_TYPE TYPE_PI32V2_CPU
void pi32v2_translate_init(void);
void pi32v2_translate_code(CPUState *, TranslationBlock *, int *, vaddr, void *);
void pi32v2_check_access(CPUPi32v2State *env, uint32_t address,
                         unsigned size, unsigned flags);
void pi32v2_note_branch(CPUPi32v2State *env);
G_NORETURN void pi32v2_fail(CPUPi32v2State *env, const char *reason);
#endif
