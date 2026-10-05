/* SPDX-License-Identifier: GPL-2.0-or-later */
/* QOM/TCG integration follows QEMU 10.0's CPUClass interfaces. */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/qemu-print.h"
#include "cpu.h"
#include "exec/cputlb.h"
#include "exec/cpu_ldst.h"
#include "exec/translation-block.h"
#include "hw/core/sysemu-cpu-ops.h"
#include "accel/tcg/cpu-ops.h"

static void set_pc(CPUState *cs, vaddr pc) { cpu_env(cs)->pc = pc; }
static vaddr get_pc(CPUState *cs) { return cpu_env(cs)->pc; }
static int mmu_index(CPUState *cs, bool ifetch) { return 0; }
static hwaddr physical_debug(CPUState *cs, vaddr addr) { return addr; }
static bool has_work(CPUState *cs) { return cs->interrupt_request & CPU_INTERRUPT_HARD; }

static void synchronize(CPUState *cs, const TranslationBlock *tb)
{
    cpu_env(cs)->pc = tb->pc;
}
static void restore(CPUState *cs, const TranslationBlock *tb, const uint64_t *data)
{
    cpu_env(cs)->pc = data[0];
}
static bool fill_tlb(CPUState *cs, vaddr addr, int size, MMUAccessType access,
                     int index, bool probe, uintptr_t ra)
{
    int prot = PAGE_READ | PAGE_WRITE | PAGE_EXEC;
    /* QEMU MemoryRegions enforce mapped widths and permissions. */
    if (addr >= 0x02000000 && addr < 0x02100000) {
        if (access == MMU_DATA_STORE) {
            if (probe) { return false; }
            pi32v2_fail(cpu_env(cs), "write to read-only XIP");
        }
        prot &= ~PAGE_WRITE;
    }
    tlb_set_page(cs, addr & TARGET_PAGE_MASK, addr & TARGET_PAGE_MASK,
                 prot, index, TARGET_PAGE_SIZE);
    return true;
}
static void transaction_failed(CPUState *cs, hwaddr phys, vaddr addr,
                               unsigned size, MMUAccessType access, int index,
                               MemTxAttrs attrs, MemTxResult result, uintptr_t ra)
{
    g_autofree char *s = g_strdup_printf("unmapped access at 0x%08" PRIx64, (uint64_t)addr);
    pi32v2_fail(cpu_env(cs), s);
}
static G_NORETURN void unaligned(CPUState *cs, vaddr addr, MMUAccessType access,
                      int index, uintptr_t ra)
{
    pi32v2_fail(cpu_env(cs), "unaligned access");
}
static void unexpected_exception(CPUState *cs)
{
    pi32v2_fail(cpu_env(cs), "unimplemented CPU exception");
}

static bool interrupt(CPUState *cs, int request)
{
    CPUPi32v2State *e = cpu_env(cs);
    unsigned nibble = e->irq_config >> 28;
    unsigned priority = nibble >> 1;
    if (!(request & CPU_INTERRUPT_HARD) || e->in_irq || e->predicate_end ||
        (e->spr[ICFG] & 0x300) != 0x300 || !(nibble & 1) ||
        priority < e->priority_mask) {
        return false;
    }
    fm1_poc_check_access(e, 0x01c7fefc, 4, 0);
    uint32_t handler = cpu_ldl_data(e, 0x01c7fefc);
    /* A missing/unmapped vector fails through QEMU's memory access path. */
    /* The normal per-instruction fetch gate validates the selected handler. */
    e->last_irq_pc = e->pc;
    e->last_irq_handler = handler;
    e->spr[RETI] = e->pc;
    e->spr[USP] = e->spr[SP];
    e->spr[SP] = e->spr[SSP];
    e->spr[ICFG] = (e->spr[ICFG] & ~0x077f04ffu) |
                   (63u << 16) | (priority << 24) | (1u << priority);
    e->entry_icfg = e->spr[ICFG];
    e->pc = handler;
    e->in_irq = true;
    e->irq_entries++;
    return true;
}

static void irq_input(void *opaque, int n, int level)
{
    CPUState *cs = CPU(opaque);
    if (level) { cpu_interrupt(cs, CPU_INTERRUPT_HARD); }
    else { cpu_reset_interrupt(cs, CPU_INTERRUPT_HARD); }
}
static void init(Object *obj) { qdev_init_gpio_in(DEVICE(obj), irq_input, 1); }
static void reset(Object *obj, ResetType type)
{
    Pi32v2CPU *cpu = PI32V2_CPU(obj);
    Pi32v2CPUClass *klass = PI32V2_CPU_GET_CLASS(obj);
    if (klass->parent_phases.hold) { klass->parent_phases.hold(obj, type); }
    memset(&cpu->env, 0, sizeof(cpu->env));
    cpu->env.pc = cpu->boot_pc;
    for (int i = 0; i < 16; i++) { cpu->env.gpr[i] = 0x10203040u + i * 0x01010101u; }
    if (cpu->diag_fixture) { cpu->env.gpr[0] = 0x01c7fe08; }
    if (cpu->timer_fixture) {
        cpu->env.spr[SP] = 0x01c7a000;
        cpu->env.spr[SSP] = 0x01c7c000;
    }
}
static ObjectClass *class_by_name(const char *name)
{
    return object_class_by_name(TYPE_PI32V2_CPU);
}
static void realize(DeviceState *dev, Error **errp)
{
    CPUState *cs = CPU(dev);
    Pi32v2CPUClass *klass = PI32V2_CPU_GET_CLASS(dev);
    Error *local_err = NULL;
    cpu_exec_realizefn(cs, &local_err);
    if (local_err) { error_propagate(errp, local_err); return; }
    qemu_init_vcpu(cs);
    cpu_reset(cs);
    klass->parent_realize(dev, errp);
}
static void dump(CPUState *cs, FILE *f, int flags)
{
    CPUPi32v2State *e = cpu_env(cs);
    qemu_fprintf(f, "PC=%08x SP=%08x ICFG=%08x instructions=%" PRIu64 "\n",
                 e->pc, e->spr[SP], e->spr[ICFG], e->instructions);
    for (int i = 0; i < 16; i++) { qemu_fprintf(f, "r%d=%08x%c", i, e->gpr[i], i % 4 == 3 ? '\n' : ' '); }
}
static const SysemuCPUOps system_ops = {
    .has_work = has_work, .get_phys_page_debug = physical_debug,
};
static const TCGCPUOps tcg_ops = {
    .initialize = pi32v2_translate_init, .translate_code = pi32v2_translate_code,
    .synchronize_from_tb = synchronize, .restore_state_to_opc = restore,
    .tlb_fill = fill_tlb, .do_transaction_failed = transaction_failed,
    .do_unaligned_access = unaligned, .cpu_exec_interrupt = interrupt,
    .cpu_exec_halt = has_work, .do_interrupt = unexpected_exception,
};
static void class_init(ObjectClass *oc, void *data)
{
    Pi32v2CPUClass *klass = PI32V2_CPU_CLASS(oc);
    CPUClass *cc = CPU_CLASS(oc);
    device_class_set_parent_realize(DEVICE_CLASS(oc), realize, &klass->parent_realize);
    resettable_class_set_parent_phases(RESETTABLE_CLASS(oc), NULL, reset, NULL, &klass->parent_phases);
    cc->class_by_name = class_by_name;
    cc->set_pc = set_pc; cc->get_pc = get_pc; cc->mmu_index = mmu_index;
    cc->dump_state = dump; cc->sysemu_ops = &system_ops; cc->tcg_ops = &tcg_ops;
}
static const TypeInfo info = {
    .name = TYPE_PI32V2_CPU, .parent = TYPE_CPU,
    .instance_size = sizeof(Pi32v2CPU), .instance_align = __alignof(Pi32v2CPU),
    .class_size = sizeof(Pi32v2CPUClass), .instance_init = init, .class_init = class_init,
};
static void register_types(void) { type_register_static(&info); }
type_init(register_types)
