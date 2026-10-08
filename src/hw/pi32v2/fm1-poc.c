/* SPDX-License-Identifier: GPL-2.0-or-later */
/* FM-1 board composition and implemented SoC behavior. Hardware availability
 * does not depend on firmware identity or optional validation fixtures.
 * Controllers remain functional subsets; lifecycle extraction follows. */
#include "qemu/osdep.h"
#include "qemu/error-report.h"
#include "qemu/timer.h"
#include "qapi/error.h"
#include "hw/core/boards.h"
#include "hw/core/loader.h"
#include "hw/core/irq.h"
#include "system/address-spaces.h"
#include "system/runstate.h"
#include "cpu.h"
#include "fm1-lcd.h"
#include "fm1-system.h"
#include "fm1-nor.h"
#include "fm1-usb.h"
#include "fm1-alnk.h"

#include "fm1-poc.h"
#include "fm1-sfr.h"

/* Guards: translated code checks stores and SP writes against CPU mirrors
 * kept by the system controller; fetches are decided at translation. */
static int fm1_poc_fetch_fault(CPUPi32v2State *e, uint32_t address, unsigned size)
{
    FM1PocState *m = env_archcpu(e)->machine;
    if (!fm1_system_fetch_allowed(&m->system, address, size)) {
        return PI32V2_GUARD_PC;
    }
    return fm1_nor_fetch_fault(&m->nor, address, size);
}

static G_NORETURN void fm1_poc_guard_fault(CPUPi32v2State *e, unsigned kind,
                                           uint32_t address, unsigned size)
{
    FM1PocState *m = env_archcpu(e)->machine;
    /* The access that fired, as the capture's last_access: flags 1 write,
     * 2 instruction fetch, 4 stack pointer. */
    m->last_access_address = address;
    m->last_access_size = size;
    m->last_access_flags = kind == PI32V2_GUARD_WRITE ? 1 :
                           kind == PI32V2_GUARD_STACK ? 4 :
                           kind == PI32V2_GUARD_PC || address == e->pc ? 2 : 0;
    if (kind == PI32V2_GUARD_XIP_DISABLED || kind == PI32V2_GUARD_XIP_BOUNDS) {
        fm1_nor_guard_fault(&m->nor, kind);
    }
    fm1_system_guard_fault(&m->system, kind);
}

static void fm1_poc_check_stack(CPUPi32v2State *e)
{
    FM1PocState *m = env_archcpu(e)->machine;
    fm1_system_check_stack(&m->system);
}

/* Resets clear the CPU state, including the machine's guard mirrors. */
static void fm1_poc_reset_state(CPUPi32v2State *e)
{
    FM1PocState *m = env_archcpu(e)->machine;
    fm1_test_reset_state(e);
    if (m->system.cpu) {
        fm1_system_sync_guards(&m->system);
    }
    e->xip_fetch = m->nor.cpu && fm1_nor_xip_enabled(&m->nor);
}

static void fm1_poc_note_branch(CPUPi32v2State *e)
{
    FM1PocState *m = env_archcpu(e)->machine;
    fm1_system_note_branch(&m->system, e->pc);
}

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
uint32_t fm1_timer_counter(FM1TimerState *t)
{
    return (t->counter + elapsed_ticks(t)) % period_ticks(t);
}
static void update_irq(FM1PocState *m)
{
    qemu_set_irq(m->irq, m->timers[1].pending || m->alnk_irq_level);
}
/* Private source selection for the reached audio/timer pair. Raw device
 * levels remain pending until their guest acknowledgments. */
static bool fm1_poc_select_irq(CPUPi32v2State *e, unsigned *number, unsigned *priority)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    const unsigned sources[] = {11, 63};
    const bool pending[] = {m->alnk_irq_level, m->timers[1].pending};
    const unsigned config[] = {(m->irq_configs[1] >> 12) & 15,
                               e->irq_config >> 28};
    bool selected = false;
    for (unsigned i = 0; i < G_N_ELEMENTS(sources); i++) {
        unsigned level = config[i] >> 1;
        if (!pending[i] || !(config[i] & 1) || level < e->priority_mask) {
            continue;
        }
        if (selected && level == *priority) {
            pi32v2_fail(e, "equal-priority audio/timer arbitration is unsupported");
        }
        if (!selected || level > *priority) {
            *number = sources[i];
            *priority = level;
            selected = true;
        }
    }
    return selected;
}
static void alnk_irq_input(void *opaque, int number, int level)
{
    FM1PocState *m = opaque;
    m->alnk_irq_level = level;
    update_irq(m);
}
static void timer_irq(FM1TimerState *t)
{
    if (t->number == 5) { update_irq(t->machine); }
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
    case 4: return fm1_timer_counter(t);
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
        t->counter = fm1_timer_counter(t);
        t->control = value & 0x19;
        break;
    case 4: t->counter = value; break;
    case 8: t->counter = fm1_timer_counter(t); t->period = value; break;
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
    return m->gpio[0][0] & ~m->gpio[0][2] & m->gpio[0][3];
}
static uint64_t gpio_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    unsigned port = offset / 0x40;
    offset %= 0x40;
    if ((port != 0 && port != 1 && port != 2 && port != 3 && port != 7) ||
        offset >= sizeof(m->gpio[0])) {
        pi32v2_fail(&m->cpu->env, "unsupported GPIO register");
    }
    if (port == 2 || port == 3 || port == 7) {
        if (offset == 4) {
            pi32v2_fail(&m->cpu->env, "unsupported PC GPIO read");
        }
        return m->gpio[port][offset / 4];
    }
    if (offset == 4) {
        uint32_t inputs = m->gpio[port][4] & ~m->gpio[port][5] & m->gpio[port][2] & m->gpio[port][3];
        for (unsigned col = 0; col < 11; col++) {
            uint8_t closures = m->matrix[col] | m->input.matrix[col];

            if (!(m->latched & (1u << col))) {
                if (port == 1) {
                    if (closures & 32) { inputs &= ~(1u << 7); }
                } else {
                    if (closures & 1) { inputs &= ~1u; }
                    for (unsigned row = 1; row < 5; row++) {
                        if (closures & (1u << row)) { inputs &= ~(1u << (row + 4)); }
                    }
                }
            }
        }
        return (m->gpio[port][0] & ~m->gpio[port][2] & m->gpio[port][3]) | inputs;
    }
    if (offset >= sizeof(m->gpio[0])) { pi32v2_fail(&m->cpu->env, "unsupported PA GPIO register"); }
    return m->gpio[port][offset / 4];
}
static void gpio_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    unsigned port = offset / 0x40;
    offset %= 0x40;
    if ((port != 0 && port != 1 && port != 2 && port != 3 && port != 7) ||
        offset == 4 || offset >= sizeof(m->gpio[0])) {
        pi32v2_fail(&m->cpu->env, "unsupported PA GPIO write");
    }
    if (port != 0) {
        m->gpio[port][offset / 4] = value;
        if (port == 2) { fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]); }
        else if (port == 3) { fm1_nor_set_pins(&m->nor, m->gpio[3][0], m->iomap_con0); }
        return;
    }
    uint32_t before = gpio_pins(m);
    m->gpio[0][offset / 4] = value;
    uint32_t after = gpio_pins(m);
    if (!(before & 8) && (after & 8)) {
        m->shift = (m->shift << 1) | ((after >> 4) & 1);
        m->shift_edges++;
    }
    if (!(before & 2) && (after & 2)) {
        m->latched = m->shift;
        m->latch_edges++;
    }
    fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]);
}
static uint64_t iomap_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    switch (offset) {
    case 0: return m->iomap_con0;
    case 4: return m->iomap_con1;
    case 8: return m->iomap_con2;
    case 12: return m->iomap_con3;
    default: pi32v2_fail(&m->cpu->env, "unsupported IOMAP register");
    }
}
static void iomap_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    if (!offset) {
        if (value & ~0x20ull) { pi32v2_fail(&m->cpu->env, "unsupported IOMAP_CON0 routing"); }
        m->iomap_con0 = value;
        fm1_nor_set_pins(&m->nor, m->gpio[3][0], m->iomap_con0);
        return;
    }
    /* Reached UART pin selections are independent of NOR/LCD routing.
     * The UART receiver has no external input; no GPIO edge becomes a byte. */
    if (offset == 8 || offset == 12) {
        uint64_t mask = offset == 8 ? 0x3f00 : 0xf0;
        if (value & ~mask) {
            pi32v2_fail(&m->cpu->env, "unsupported UART IOMAP routing");
        }
        if (offset == 8) { m->iomap_con2 = value; }
        else { m->iomap_con3 = value; }
        return;
    }
    if (value & ~0x10ull) { pi32v2_fail(&m->cpu->env, "unsupported IOMAP_CON1 routing"); }
    m->iomap_con1 = value;
    fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]);
}
static uint64_t irq_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    if (offset < 0x80 && !(offset & 3)) { return m->irq_configs[offset / 4]; }
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
    if (offset < 0x80 && !(offset & 3)) {
        /* Implemented sources are fatal exception1, audio11 and TIMER5 63. */
        uint32_t allowed = offset == 0 ? 0xf0 : offset == 0x1c ? 0xf0000000u :
                           offset == 4 ? 0xf000 : 0;
        if (value & ~allowed) { pi32v2_fail(&m->cpu->env, "unsupported IRQ source enable"); }
        m->irq_configs[offset / 4] = value;
        if (offset == 0x1c) { m->cpu->env.irq_config = value; }
        update_irq(m);
        return;
    }
    switch (offset) {
    case 0xa8:
        if (value > 7) { pi32v2_fail(&m->cpu->env, "invalid priority mask"); }
        m->cpu->env.priority_mask = value; break;
    case 0xa4:
        if (value != 255) { pi32v2_fail(&m->cpu->env, "unsupported IRQ pending clear"); }
        if (m->timers[1].pending) { pi32v2_fail(&m->cpu->env, "IRQ clear requires TIMER5 device acknowledgment"); }
        break;
    default: pi32v2_fail(&m->cpu->env, "unsupported IRQ write");
    }
    update_irq(m);
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
static const MemoryRegionOps iomap_ops = {
    .read = iomap_read, .write = iomap_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static const MemoryRegionOps irq_ops = {
    .read = irq_read, .write = irq_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

static const Pi32v2MachineOps machine_ops = {
    .reset_state = fm1_poc_reset_state, .select_irq = fm1_poc_select_irq,
    .fetch_fault = fm1_poc_fetch_fault, .guard_fault = fm1_poc_guard_fault,
    .check_stack = fm1_poc_check_stack, .note_branch = fm1_poc_note_branch,
};

static bool board_adc_raw(void *opaque, unsigned channel, uint32_t *raw)
{
    FM1PocState *m = opaque;
    /* Functional board defaults, without physical calibration or a claim
     * about converter resolution. The controller retains each raw value. */
    switch (channel) {
    case 3: *raw = 600; return true; /* PB1 */
    case 4: *raw = m->input.master_raw; return true; /* PB6 / MASTER */
    default: return false;
    }
}

static void machine_init(MachineState *ms)
{
    FM1PocState *m = FM1_POC_MACHINE(ms);
    m->cpu = PI32V2_CPU(cpu_create(TYPE_PI32V2_CPU));
    m->cpu->machine = m;
    m->cpu->ops = &machine_ops;
    fm1_test_configure(m, ms);
    cpu_reset(CPU(m->cpu));
    memory_region_add_subregion(get_system_memory(), 0x01c00000, ms->ram);
    fm1_test_seed_ram(m);
    m->latched = UINT16_MAX;
    /* Explicit application handoff with SFC routed to the board NOR. This
     * same device topology is used by every image and optional test fixture. */
    m->gpio[3][0] = 1;
    m->iomap_con0 = 0x20;
    fm1_nor_init(&m->nor, OBJECT(m), m->cpu, ms->kernel_filename);
    fm1_nor_set_pins(&m->nor, m->gpio[3][0], m->iomap_con0);
    for (unsigned i = 0; i < 2; i++) {
        FM1TimerState *t = &m->timers[i];
        t->machine = m;
        t->number = i + 4;
        t->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, fm1_timer_expired, t);
        memory_region_init_io(&t->mmio, OBJECT(m), &timer_ops, t,
                              i ? "fm1.timer5" : "fm1.timer4", 12);
        fm1_sfr_map(0x10800 + i * 0x100, &t->mmio);
    }
    memory_region_init_io(&m->gpio_mmio, OBJECT(m), &gpio_ops, m, "fm1.gpio", 0x1e0);
    fm1_sfr_map(0x50000, &m->gpio_mmio);
    memory_region_init_io(&m->irq_mmio, OBJECT(m), &irq_ops, m, "fm1.irq", 0xac);
    fm1_sfr_map(0x01eef100, &m->irq_mmio);
    m->irq = qdev_get_gpio_in(DEVICE(m->cpu), 0);
    fm1_system_init(&m->system, OBJECT(m), m->cpu);
    fm1_syscon_init(&m->syscon, OBJECT(m), m->cpu);
    fm1_sfr_map(0x10010, &m->syscon.mmio[FM1_SYSCON_CLK_CON1]);
    fm1_sfr_map(0x10014, &m->syscon.mmio[FM1_SYSCON_CLK_CON2]);
    fm1_sfr_map(0x51030, &m->syscon.mmio[FM1_SYSCON_IOMAP_CON5]);
    m->alnk_irq = qemu_allocate_irq(alnk_irq_input, m, 11);
    object_initialize_child(OBJECT(m), "alnk0", &m->alnk, TYPE_FM1_ALNK);
    fm1_alnk_bind(&m->alnk, m->cpu, &m->syscon);
    sysbus_realize(SYS_BUS_DEVICE(&m->alnk), &error_fatal);
    fm1_sfr_map(0x12e00, sysbus_mmio_get_region(SYS_BUS_DEVICE(&m->alnk), 0));
    sysbus_connect_irq(SYS_BUS_DEVICE(&m->alnk), 0, m->alnk_irq);
    /* Zero is the board's functional application handoff choice, not an
     * established analog-block reset value. Optional test seeds are separate. */
    fm1_analog_init(&m->analog, OBJECT(m), m->cpu, m->analog_initial_wla_con0);
    fm1_sfr_map(0x11900, &m->analog.mmio);
    object_initialize_child(OBJECT(m), "sar-adc", &m->adc, TYPE_FM1_ADC);
    fm1_adc_bind(&m->adc, m->cpu, &m->analog, (1u << 3) | (1u << 4),
                 board_adc_raw, m);
    sysbus_realize(SYS_BUS_DEVICE(&m->adc), &error_fatal);
    fm1_sfr_map(0x13100, sysbus_mmio_get_region(SYS_BUS_DEVICE(&m->adc), 0));
    fm1_usb_init(&m->usb, OBJECT(m), m->cpu);
    fm1_uart_init(&m->uart, OBJECT(m), m->cpu);
    fm1_sfr_map(0x12100, &m->uart.mmio);
    fm1_lcd_init(&m->lcd, OBJECT(m), m->cpu);
    memory_region_init_io(&m->iomap_mmio, OBJECT(m), &iomap_ops, m, "fm1.iomap", 16);
    fm1_sfr_map(0x5101c, &m->iomap_mmio);
    fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]);
    object_initialize_child(OBJECT(m), "board-input", &m->input, TYPE_FM1_INPUT);
    fm1_input_bind(&m->input, m->cpu);
    qdev_realize(DEVICE(&m->input), NULL, &error_fatal);
    fm1_test_start(m);
}
static void machine_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);
    mc->desc = "FM-1 pi32v2 application machine (partial hardware model)";
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
