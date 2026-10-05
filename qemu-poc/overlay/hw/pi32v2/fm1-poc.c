/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fixture machine: one CPU, SRAM, XIP, PA matrix, TIMER4/5 and IRQ63.
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
typedef struct FM1TimerState {
    FM1PocState *machine;
    MemoryRegion mmio;
    QEMUTimer *timer;
    unsigned number;
    uint32_t control, counter, period;
    bool pending;
    int64_t epoch, deadline;
    uint64_t expirations, acknowledgments;
} FM1TimerState;

struct FM1PocState {
    MachineState parent_obj;
    Pi32v2CPU *cpu;
    MemoryRegion xip, irq_mmio, gpio_mmio;
    FM1TimerState timers[2];
    qemu_irq irq;
    uint32_t gpio[8];
    uint16_t shift, latched;
    uint8_t matrix[11];
    uint64_t shift_edges, latch_edges;
};

static unsigned divider(FM1TimerState *t) { return t->control & 16 ? 4 : 1; }
static uint64_t period_ticks(FM1TimerState *t)
{
    return t->period == UINT32_MAX ? 1ull << 32 : MAX(t->period, 1);
}
static uint64_t elapsed_ticks(FM1TimerState *t)
{
    if (!(t->control & 1)) { return 0; }
    return (qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - t->epoch) * 24000000ull /
           (divider(t) * 1000000000ull);
}
static uint32_t counter_now(FM1TimerState *t)
{
    return (t->counter + elapsed_ticks(t)) % period_ticks(t);
}
static void timer_irq(FM1TimerState *t)
{
    if (t->number == 5) { qemu_set_irq(t->machine->irq, t->pending); }
    else if (t->pending) {
        pi32v2_fail(&t->machine->cpu->env, "TIMER4 IRQ62 is unimplemented");
    }
}
static void fm1_timer_expired(void *opaque)
{
    FM1TimerState *t = opaque;
    t->pending = true;
    t->expirations++;
    timer_irq(t);
    t->deadline += DIV_ROUND_UP(period_ticks(t) * divider(t) * 1000000000ull, 24000000);
    timer_mod_ns(t->timer, t->deadline);
}
static void rearm(FM1TimerState *t)
{
    timer_del(t->timer);
    t->epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    if (t->control & 1) {
        uint64_t remaining = period_ticks(t) - (t->counter % period_ticks(t));
        t->deadline = t->epoch + DIV_ROUND_UP(remaining * divider(t) * 1000000000ull, 24000000);
        timer_mod_ns(t->timer, t->deadline);
    }
}
static G_NORETURN void timer_fail(FM1TimerState *t, const char *reason)
{
    g_autofree char *s = g_strdup_printf("unsupported TIMER%u %s", t->number, reason);
    pi32v2_fail(&t->machine->cpu->env, s);
}
static uint64_t timer_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1TimerState *t = opaque;
    switch (offset) {
    case 0: return t->control | (t->pending ? 0x8000 : 0);
    case 4: return counter_now(t);
    case 8: return t->period;
    default: timer_fail(t, "register");
    }
}
static void timer_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1TimerState *t = opaque;
    switch (offset) {
    case 0:
        if (value & ~0xc019ull || ((value & 1) && !(value & 8))) {
            timer_fail(t, "clock/control");
        }
        if (value & 0x4000) {
            if (t->pending) { t->acknowledgments++; }
            t->pending = false;
            timer_irq(t);
        }
        /* Acknowledgment clears the latch without restarting timer phase. */
        if ((value & 0x19) == t->control) { return; }
        t->counter = counter_now(t);
        t->control = value & 0x19;
        break;
    case 4: t->counter = value; break;
    case 8: t->counter = counter_now(t); t->period = value; break;
    default: timer_fail(t, "register");
    }
    rearm(t);
}

/* Digital GPIO and the board's 2x74HC595 chain. A shift/latch edge only
 * reaches the chain when its PA pin is configured as a digital output.
 * Matrix closures pull an enabled input low while a latched column is low.
 * These are wiring/register facts from the guest HAL, not copied driver code. */
static uint32_t gpio_pins(FM1PocState *m)
{
    return m->gpio[0] & ~m->gpio[2] & m->gpio[3];
}
static uint64_t gpio_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    if (offset == 4) {
        uint32_t inputs = m->gpio[4] & ~m->gpio[5] & m->gpio[2] & m->gpio[3];
        for (unsigned col = 0; col < 11; col++) {
            if (!(m->latched & (1u << col))) {
                if (m->matrix[col] & 1) { inputs &= ~1u; }
                for (unsigned row = 1; row < 5; row++) {
                    if (m->matrix[col] & (1u << row)) { inputs &= ~(1u << (row + 4)); }
                }
            }
        }
        return gpio_pins(m) | inputs;
    }
    if (offset >= sizeof(m->gpio)) { pi32v2_fail(&m->cpu->env, "unsupported PA GPIO register"); }
    return m->gpio[offset / 4];
}
static void gpio_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    if (offset == 4 || offset >= sizeof(m->gpio)) {
        pi32v2_fail(&m->cpu->env, "unsupported PA GPIO write");
    }
    uint32_t before = gpio_pins(m);
    m->gpio[offset / 4] = value;
    uint32_t after = gpio_pins(m);
    if (!(before & 8) && (after & 8)) {
        m->shift = (m->shift << 1) | ((after >> 4) & 1);
        m->shift_edges++;
    }
    if (!(before & 2) && (after & 2)) {
        m->latched = m->shift;
        m->latch_edges++;
    }
}
static uint64_t irq_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    switch (offset) {
    case 0x1c: return m->cpu->env.irq_config;
    case 0x84: return m->timers[1].pending ? 0x80000000u : 0;
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
static const MemoryRegionOps gpio_ops = {
    .read = gpio_read, .write = gpio_write, .endianness = DEVICE_LITTLE_ENDIAN,
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
    FM1TimerState *t = &m->timers[1];
    bool foundation = m->cpu->foundation_fixture;
    uint32_t addr = m->cpu->timer_fixture || foundation ? 0x01c08010 : 0x01c08000;
    unsigned words = m->cpu->timer_fixture || foundation ? 10 : 12;
    printf("{\"pc\":%u,\"instructions\":%" PRIu64 ",\"irq_entries\":%" PRIu64
           ",\"rti_count\":%" PRIu64 ",\"timer_expirations\":%" PRIu64
           ",\"acknowledgments\":%" PRIu64 ",\"pending\":%s,\"in_irq\":%s,"
           "\"virtual_ns\":%" PRId64 ",\"last_irq_pc\":%u,\"last_irq_handler\":%u,"
           "\"entry_icfg\":%u,\"return_icfg\":%u,\"inspection\":[",
           e->pc, e->instructions, e->irq_entries, e->rti_count, t->expirations,
           t->acknowledgments, t->pending ? "true" : "false", e->in_irq ? "true" : "false",
           qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), e->last_irq_pc, e->last_irq_handler,
           e->entry_icfg, e->return_icfg);
    for (unsigned i = 0; i < words; i++) {
        printf("%s%u", i ? "," : "", ldl_le_phys(&address_space_memory, addr + i * 4));
    }
    printf("],\"registers\":[");
    for (int i = 0; i < 16; i++) { printf("%s%u", i ? "," : "", e->gpr[i]); }
    printf("],\"specials\":[");
    for (int i = 0; i < 16; i++) { printf("%s%u", i ? "," : "", e->spr[i]); }
    printf("]");
    if (foundation) {
        printf(",\"probe\":[");
        for (unsigned i = 0; i < 12; i++) {
            printf("%s%u", i ? "," : "", ldl_le_phys(&address_space_memory, 0x01c08038 + i * 4));
        }
        printf("],\"matrix\":[");
        for (unsigned i = 0; i < 11; i++) {
            printf("%s%u", i ? "," : "", ldl_le_phys(&address_space_memory, 0x01c08068 + i * 4));
        }
        printf("],\"ram_code\":[%u,%u],\"data\":%u,\"bss\":%u,"
               "\"shift_edges\":%" PRIu64 ",\"latch_edges\":%" PRIu64
               ",\"latched_columns\":%u,\"timer4_counter\":%u",
               ldl_le_phys(&address_space_memory, 0x01c00000),
               ldl_le_phys(&address_space_memory, 0x01c00004),
               ldl_le_phys(&address_space_memory, 0x01c08000),
               ldl_le_phys(&address_space_memory, 0x01c08004),
               m->shift_edges, m->latch_edges, m->latched, counter_now(&m->timers[0]));
    }
    puts("}");
    exit(EXIT_SUCCESS);
}

static void machine_init(MachineState *ms)
{
    FM1PocState *m = FM1_POC_MACHINE(ms);
    bool timer = !strcmp(ms->kernel_cmdline, "timer");
    bool foundation = !strcmp(ms->kernel_cmdline, "foundation") ||
                      !strcmp(ms->kernel_cmdline, "foundation-released");
    if (strcmp(ms->kernel_cmdline, "probe") && !timer && !foundation) {
        error_report("select -append probe, timer, foundation or foundation-released"); exit(EXIT_FAILURE);
    }
    if (!ms->kernel_filename) { error_report("a raw fixture must be supplied with -kernel"); exit(EXIT_FAILURE); }
    m->cpu = PI32V2_CPU(cpu_create(TYPE_PI32V2_CPU));
    m->cpu->machine = m;
    m->cpu->timer_fixture = timer;
    m->cpu->foundation_fixture = foundation;
    m->cpu->boot_pc = timer ? 0x02000238 : 0x02000120;
    m->cpu->stop_pc = timer || foundation ? 0x020002ba : 0x0200013a;
    cpu_reset(CPU(m->cpu));
    memory_region_add_subregion(get_system_memory(), 0x01c00000, ms->ram);
    if (foundation) {
        /* Poison only the new startup fixture. Guest copies/clears must replace
         * this state; the existing probe/timer seed stays unchanged. */
        memset(memory_region_get_ram_ptr(ms->ram), 0xa5, ms->ram_size);
        if (!strcmp(ms->kernel_cmdline, "foundation")) { m->matrix[0] = 1u << 4; }
    }
    m->latched = UINT16_MAX;
    memory_region_init_rom(&m->xip, NULL, "fm1.xip", 0x100000, &error_fatal);
    memory_region_add_subregion(get_system_memory(), 0x02000000, &m->xip);
    if (load_image_targphys(ms->kernel_filename, 0x02000120, 0xffee0) <= 0) {
        error_report("cannot load raw fixture"); exit(EXIT_FAILURE);
    }
    for (unsigned i = 0; i < 2; i++) {
        FM1TimerState *t = &m->timers[i];
        t->machine = m;
        t->number = i + 4;
        t->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, fm1_timer_expired, t);
        memory_region_init_io(&t->mmio, OBJECT(m), &timer_ops, t,
                              i ? "fm1.timer5" : "fm1.timer4", 12);
        memory_region_add_subregion(get_system_memory(), 0x10800 + i * 0x100, &t->mmio);
    }
    memory_region_init_io(&m->gpio_mmio, OBJECT(m), &gpio_ops, m, "fm1.pa", sizeof(m->gpio));
    memory_region_add_subregion(get_system_memory(), 0x50000, &m->gpio_mmio);
    memory_region_init_io(&m->irq_mmio, OBJECT(m), &irq_ops, m, "fm1.irq63", 0xac);
    memory_region_add_subregion(get_system_memory(), 0x01eef100, &m->irq_mmio);
    m->irq = qdev_get_gpio_in(DEVICE(m->cpu), 0);
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
