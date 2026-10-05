/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fixture machine: one CPU, SRAM, XIP, TIMER5 and IRQ63 configuration.
 * JieLi register behavior is modeled here, not supplied by generic QEMU IRQs.
 * The timer is functional, not cycle accurate. */
#include "qemu/osdep.h"
#include "qemu/error-report.h"
#include "qemu/timer.h"
#include "qapi/error.h"
#include "hw/boards.h"
#include "hw/loader.h"
#include "hw/irq.h"
#include "exec/address-spaces.h"
#include "cpu.h"

#define TYPE_FM1_POC_MACHINE MACHINE_TYPE_NAME("fm1-poc")
OBJECT_DECLARE_SIMPLE_TYPE(FM1PocState, FM1_POC_MACHINE)
struct FM1PocState {
    MachineState parent_obj;
    Pi32v2CPU *cpu;
    MemoryRegion xip, timer_mmio, irq_mmio;
    QEMUTimer *timer;
    qemu_irq irq;
    uint32_t control, counter, period;
    bool pending;
    int64_t epoch, deadline;
    uint64_t expirations, acknowledgments;
};

static unsigned divider(FM1PocState *m) { return m->control & 16 ? 4 : 1; }
static uint64_t period_ticks(FM1PocState *m)
{
    return m->period == UINT32_MAX ? 1ull << 32 : MAX(m->period, 1);
}
static uint64_t elapsed_ticks(FM1PocState *m)
{
    if (!(m->control & 1)) { return 0; }
    return (qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - m->epoch) * 24000000ull /
           (divider(m) * 1000000000ull);
}
static uint32_t counter_now(FM1PocState *m)
{
    return (m->counter + elapsed_ticks(m)) % period_ticks(m);
}
static void fm1_timer_expired(void *opaque)
{
    FM1PocState *m = opaque;
    m->pending = true;
    m->expirations++;
    qemu_set_irq(m->irq, 1);
    m->deadline += DIV_ROUND_UP(period_ticks(m) * divider(m) * 1000000000ull, 24000000);
    timer_mod_ns(m->timer, m->deadline);
}
static void rearm(FM1PocState *m)
{
    timer_del(m->timer);
    m->epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    if (m->control & 1) {
        uint64_t remaining = period_ticks(m) - (m->counter % period_ticks(m));
        m->deadline = m->epoch + DIV_ROUND_UP(remaining * divider(m) * 1000000000ull, 24000000);
        timer_mod_ns(m->timer, m->deadline);
    }
}
static uint64_t timer_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    switch (offset) {
    case 0: return m->control | (m->pending ? 0x8000 : 0);
    case 4: return counter_now(m);
    case 8: return m->period;
    default: pi32v2_fail(&m->cpu->env, "unsupported TIMER5 register");
    }
}
static void timer_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    switch (offset) {
    case 0:
        if (value & ~0xc019ull || ((value & 1) && !(value & 8))) {
            pi32v2_fail(&m->cpu->env, "unsupported TIMER5 clock/control");
        }
        if (value & 0x4000) {
            if (m->pending) { m->acknowledgments++; }
            m->pending = false;
            qemu_set_irq(m->irq, 0);
        }
        /* Acknowledgment clears the latch without restarting timer phase. */
        if ((value & 0x19) == m->control) { return; }
        m->counter = counter_now(m);
        m->control = value & 0x19;
        break;
    case 4: m->counter = value; break;
    case 8: m->counter = counter_now(m); m->period = value; break;
    default: pi32v2_fail(&m->cpu->env, "unsupported TIMER5 register");
    }
    rearm(m);
}
static uint64_t irq_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    switch (offset) {
    case 0x1c: return m->cpu->env.irq_config;
    case 0x84: return m->pending ? 0x80000000u : 0;
    case 0xa8: return m->cpu->env.priority_mask;
    default: pi32v2_fail(&m->cpu->env, "unsupported IRQ register");
    }
}
static void irq_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    switch (offset) {
    case 0x1c:
        if (value & 0x0fffffffu) { pi32v2_fail(&m->cpu->env, "only IRQ63 configuration is implemented"); }
        m->cpu->env.irq_config = value; break;
    case 0xa8:
        if (value > 7) { pi32v2_fail(&m->cpu->env, "invalid priority mask"); }
        m->cpu->env.priority_mask = value; break;
    default: pi32v2_fail(&m->cpu->env, "unsupported IRQ write");
    }
}
static const MemoryRegionOps timer_ops = {
    .read = timer_read, .write = timer_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static const MemoryRegionOps irq_ops = {
    .read = irq_read, .write = irq_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

void fm1_poc_finish(CPUPi32v2State *e)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    uint32_t addr = m->cpu->timer_fixture ? 0x01c08010 : 0x01c08000;
    unsigned words = m->cpu->timer_fixture ? 10 : 12;
    printf("{\"pc\":%u,\"instructions\":%" PRIu64 ",\"irq_entries\":%" PRIu64
           ",\"rti_count\":%" PRIu64 ",\"timer_expirations\":%" PRIu64
           ",\"acknowledgments\":%" PRIu64 ",\"pending\":%s,\"in_irq\":%s,"
           "\"virtual_ns\":%" PRId64 ",\"last_irq_pc\":%u,\"last_irq_handler\":%u,"
           "\"entry_icfg\":%u,\"return_icfg\":%u,\"inspection\":[",
           e->pc, e->instructions, e->irq_entries, e->rti_count, m->expirations,
           m->acknowledgments, m->pending ? "true" : "false", e->in_irq ? "true" : "false",
           qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), e->last_irq_pc, e->last_irq_handler,
           e->entry_icfg, e->return_icfg);
    for (unsigned i = 0; i < words; i++) {
        printf("%s%u", i ? "," : "", ldl_le_phys(&address_space_memory, addr + i * 4));
    }
    printf("],\"registers\":[");
    for (int i = 0; i < 16; i++) { printf("%s%u", i ? "," : "", e->gpr[i]); }
    printf("],\"specials\":[");
    for (int i = 0; i < 16; i++) { printf("%s%u", i ? "," : "", e->spr[i]); }
    puts("]}");
    exit(EXIT_SUCCESS);
}

static void machine_init(MachineState *ms)
{
    FM1PocState *m = FM1_POC_MACHINE(ms);
    bool timer = !strcmp(ms->kernel_cmdline, "timer");
    if (strcmp(ms->kernel_cmdline, "probe") && !timer) {
        error_report("select -append probe or -append timer"); exit(EXIT_FAILURE);
    }
    if (!ms->kernel_filename) { error_report("a raw fixture must be supplied with -kernel"); exit(EXIT_FAILURE); }
    m->cpu = PI32V2_CPU(cpu_create(TYPE_PI32V2_CPU));
    m->cpu->machine = m;
    m->cpu->timer_fixture = timer;
    m->cpu->boot_pc = timer ? 0x02000238 : 0x02000120;
    m->cpu->stop_pc = timer ? 0x020002ba : 0x0200013a;
    cpu_reset(CPU(m->cpu));
    memory_region_add_subregion(get_system_memory(), 0x01c00000, ms->ram);
    memory_region_init_rom(&m->xip, NULL, "fm1.xip", 0x100000, &error_fatal);
    memory_region_add_subregion(get_system_memory(), 0x02000000, &m->xip);
    if (load_image_targphys(ms->kernel_filename, 0x02000120, 0xffee0) <= 0) {
        error_report("cannot load raw fixture"); exit(EXIT_FAILURE);
    }
    memory_region_init_io(&m->timer_mmio, OBJECT(m), &timer_ops, m, "fm1.timer5", 12);
    memory_region_add_subregion(get_system_memory(), 0x10900, &m->timer_mmio);
    memory_region_init_io(&m->irq_mmio, OBJECT(m), &irq_ops, m, "fm1.irq63", 0xac);
    memory_region_add_subregion(get_system_memory(), 0x01eef100, &m->irq_mmio);
    m->irq = qdev_get_gpio_in(DEVICE(m->cpu), 0);
    m->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, fm1_timer_expired, m);
}
static void machine_class_init(ObjectClass *oc, void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);
    mc->desc = "FM-1 pi32v2 instruction/timer proof of concept";
    mc->init = machine_init;
    mc->default_cpu_type = TYPE_PI32V2_CPU;
    mc->default_ram_size = 512 * 1024;
    mc->default_ram_id = "fm1.sram";
    mc->max_cpus = mc->min_cpus = mc->default_cpus = 1;
    mc->no_floppy = true; mc->no_cdrom = true; mc->no_parallel = true;
}
static const TypeInfo machine_type = {
    .name = TYPE_FM1_POC_MACHINE, .parent = TYPE_MACHINE,
    .instance_size = sizeof(FM1PocState), .class_init = machine_class_init,
};
static void register_types(void) { type_register_static(&machine_type); }
type_init(register_types)
