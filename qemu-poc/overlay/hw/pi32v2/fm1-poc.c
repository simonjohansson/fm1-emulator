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
#include "system/runstate.h"
#include "cpu.h"
#include "fm1-lcd.h"
#include "fm1-system.h"
#include "fm1-nor.h"
#include "fm1-usb.h"

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
    MemoryRegion xip, irq_mmio, gpio_mmio, iomap_mmio;
    FM1TimerState timers[2];
    qemu_irq irq;
    uint32_t irq_configs[32];
    uint32_t gpio[8][8], iomap_con0, iomap_con1;
    FM1PocLCD lcd;
    FM1PocSystem system;
    FM1PocNOR nor;
    FM1PocUSB usb;
    unsigned frames;
    const char *frame_dir;
    QEMUTimer *display_key_timer;
    int64_t display_key_deadline;
    uint16_t shift, latched;
    uint8_t matrix[11];
    uint64_t shift_edges, latch_edges;
    uint64_t loop_visits, loop_target_irqs;
    bool keep_open, finished, display_live;
};

void fm1_poc_check_access(CPUPi32v2State *e, uint32_t address, unsigned size, unsigned flags)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    if (m->cpu->diag_fixture) {
        fm1_system_check_stack(&m->system);
        if (!(flags & 4)) {
            fm1_system_check_access(&m->system, address, size, flags & 1, flags & 2);
            fm1_nor_check_access(&m->nor, address, size, flags & 1);
        }
    }
}

void fm1_poc_note_branch(CPUPi32v2State *e)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    if (m->cpu->diag_fixture) { fm1_system_note_branch(&m->system, e->pc); }
}

/* A private observation at the real foreground-loop boundary. The guest
 * performs all initialization, drawing, watchdog feeds and IRQ work. */
void fm1_poc_diag_loop(CPUPi32v2State *e)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    if (m->finished) { fm1_poc_finish(e); }
    m->loop_visits++;
    if (m->loop_visits >= 3 && e->rti_count >= m->loop_target_irqs) {
        fm1_poc_finish(e);
    }
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
    return m->gpio[0][0] & ~m->gpio[0][2] & m->gpio[0][3];
}
static uint64_t gpio_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    unsigned port = offset / 0x40;
    offset %= 0x40;
    if ((port != 0 && port != 2 && !(m->cpu->diag_fixture && (port == 1 || port == 3 || port == 7))) ||
        offset >= sizeof(m->gpio[0])) {
        pi32v2_fail(&m->cpu->env, "unsupported GPIO register");
    }
    if (port == 2 || port == 3 || port == 7) {
        if ((!m->cpu->display_fixture && !m->cpu->diag_fixture) || offset == 4) {
            pi32v2_fail(&m->cpu->env, "unsupported PC GPIO read");
        }
        return m->gpio[port][offset / 4];
    }
    if (offset == 4) {
        uint32_t inputs = m->gpio[port][4] & ~m->gpio[port][5] & m->gpio[port][2] & m->gpio[port][3];
        for (unsigned col = 0; col < 11; col++) {
            if (!(m->latched & (1u << col))) {
                if (port == 1) {
                    if (m->matrix[col] & 32) { inputs &= ~(1u << 7); }
                } else {
                    if (m->matrix[col] & 1) { inputs &= ~1u; }
                    for (unsigned row = 1; row < 5; row++) {
                        if (m->matrix[col] & (1u << row)) { inputs &= ~(1u << (row + 4)); }
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
    if ((port != 0 && port != 2 && !(m->cpu->diag_fixture && (port == 1 || port == 3 || port == 7))) ||
        offset == 4 || offset >= sizeof(m->gpio[0])) {
        pi32v2_fail(&m->cpu->env, "unsupported PA GPIO write");
    }
    if (port != 0) {
        if (!m->cpu->display_fixture && !m->cpu->diag_fixture) { pi32v2_fail(&m->cpu->env, "unsupported PC GPIO write"); }
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
    if (m->cpu->display_fixture || m->cpu->diag_fixture) {
        fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]);
    }
}
static uint64_t iomap_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    return m->cpu->diag_fixture && !offset ? m->iomap_con0 : m->iomap_con1;
}
static void iomap_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocState *m = opaque;
    if (m->cpu->diag_fixture && !offset) {
        if (value & ~0x20ull) { pi32v2_fail(&m->cpu->env, "unsupported IOMAP_CON0 routing"); }
        m->iomap_con0 = value;
        fm1_nor_set_pins(&m->nor, m->gpio[3][0], m->iomap_con0);
        return;
    }
    if (value & ~0x10ull) { pi32v2_fail(&m->cpu->env, "unsupported IOMAP_CON1 routing"); }
    m->iomap_con1 = value;
    fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]);
}
static uint64_t irq_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocState *m = opaque;
    if (m->cpu->diag_fixture && offset < 0x80 && !(offset & 3)) { return m->irq_configs[offset / 4]; }
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
    if (m->cpu->diag_fixture && offset < 0x80 && !(offset & 3)) {
        /* Reset can disable every source. Only IRQ1 exception configuration
         * and the implemented TIMER5 IRQ63 may subsequently be enabled. */
        uint32_t allowed = offset == 0 ? 0xf0 : offset == 0x1c ? 0xf0000000u : 0;
        if (value & ~allowed) { pi32v2_fail(&m->cpu->env, "unsupported IRQ source enable"); }
        m->irq_configs[offset / 4] = value;
        if (offset == 0x1c) { m->cpu->env.irq_config = value; }
        return;
    }
    switch (offset) {
    case 0x1c:
        if (value & 0x0fffffffu) { pi32v2_fail(&m->cpu->env, "only IRQ63 configuration is implemented"); }
        m->cpu->env.irq_config = value; break;
    case 0xa8:
        if (value > 7) { pi32v2_fail(&m->cpu->env, "invalid priority mask"); }
        m->cpu->env.priority_mask = value; break;
    case 0xa4:
        if (!m->cpu->diag_fixture || value != 255) { pi32v2_fail(&m->cpu->env, "unsupported IRQ pending clear"); }
        if (m->timers[1].pending) { pi32v2_fail(&m->cpu->env, "IRQ clear requires TIMER5 device acknowledgment"); }
        break;
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

static void save_lcd_ppm(FM1PocState *m, const char *path)
{
    FILE *f = fopen(path, "wb");
    if (!f) { pi32v2_fail(&m->cpu->env, "cannot create display frame PPM"); }
    fprintf(f, "P6\n%u %u\n255\n", FM1_LCD_WIDTH, FM1_LCD_HEIGHT);
    uint8_t row[FM1_LCD_WIDTH * 3];
    for (unsigned y = 0; y < FM1_LCD_HEIGHT; y++) {
        for (unsigned x = 0; x < FM1_LCD_WIDTH; x++) {
            uint32_t rgb = fm1_lcd_rgb(&m->lcd, x, y);
            row[x * 3] = rgb >> 16;
            row[x * 3 + 1] = rgb >> 8;
            row[x * 3 + 2] = rgb;
        }
        if (fwrite(row, sizeof(row), 1, f) != 1) {
            pi32v2_fail(&m->cpu->env, "cannot write display frame PPM");
        }
    }
    if (fclose(f)) { pi32v2_fail(&m->cpu->env, "cannot close display frame PPM"); }
}

static void display_key_toggle(void *opaque)
{
    FM1PocState *m = opaque;
    /* Change a physical matrix closure only. Guest GPIO scans and drawing
     * discover the OCT-minus press/release through the existing wiring. */
    m->matrix[0] ^= 1u << 4;
    m->display_key_deadline += 500000000;
    timer_mod_ns(m->display_key_timer, m->display_key_deadline);
}

/* Private checkpoint instrumentation observes framebuffer and guest state.
 * The standard regression changes the physical key between three frames;
 * live mode uses a virtual-time input timer and keeps executing the guest. */
void fm1_poc_frame(CPUPi32v2State *e)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    uint32_t guest_frames = ldl_le_phys(&address_space_memory, 0x01c0827c);
    if (guest_frames != m->frames + 1 || m->lcd.busy || !fm1_lcd_visible(&m->lcd)) {
        pi32v2_fail(e, "display checkpoint needs the next complete visible guest frame");
    }
    m->frames++;
    if (m->frame_dir) {
        g_autofree char *path = m->display_live ?
            g_strdup_printf("%s/frame-live.ppm.tmp", m->frame_dir) :
            g_strdup_printf("%s/frame-%u.ppm", m->frame_dir, m->frames);
        save_lcd_ppm(m, path);
        g_autofree char *record = m->display_live ?
            g_strdup_printf("%s/frame-live.json.tmp", m->frame_dir) :
            g_strdup_printf("%s/frame-%u.json", m->frame_dir, m->frames);
        FILE *f = fopen(record, "w");
        if (!f) { pi32v2_fail(e, "cannot create display frame record"); }
        fprintf(f, "{\"frame\":%u,\"pc\":%u,\"instructions\":%" PRIu64
                ",\"virtual_ns\":%" PRId64 ",\"display_ticks\":%u,\"sp\":%u,\"ssp\":%u,"
                "\"visible\":true,\"pixels_written\":%" PRIu64
                ",\"commands\":%" PRIu64 ",\"dma_transfers\":%" PRIu64
                ",\"completed_transfers\":%" PRIu64 ",\"matrix\":[",
                m->frames, e->pc, e->instructions, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL),
                ldl_le_phys(&address_space_memory, 0x01c08280), e->spr[SP], e->spr[SSP],
                m->lcd.pixels_written, m->lcd.commands, m->lcd.dma_transfers, m->lcd.completed_transfers);
        for (unsigned i = 0; i < 11; i++) {
            fprintf(f, "%s%u", i ? "," : "", ldl_le_phys(&address_space_memory, 0x01c08068 + i * 4));
        }
        fprintf(f, "],\"registers\":[");
        for (int i = 0; i < 16; i++) { fprintf(f, "%s%u", i ? "," : "", e->gpr[i]); }
        fprintf(f, "],\"specials\":[");
        for (int i = 0; i < 16; i++) { fprintf(f, "%s%u", i ? "," : "", e->spr[i]); }
        fprintf(f, "]}\n");
        if (fclose(f)) { pi32v2_fail(e, "cannot close display frame record"); }
        if (m->display_live) {
            g_autofree char *image_final = g_strdup_printf("%s/frame-live.ppm", m->frame_dir);
            g_autofree char *record_final = g_strdup_printf("%s/frame-live.json", m->frame_dir);
            /* Publish the image before its frame-number record; each individual
             * file is complete, and readers can retry across a frame change. */
            if (rename(path, image_final) || rename(record, record_final)) {
                pi32v2_fail(e, "cannot publish live display snapshot");
            }
        }
    }
    if (!m->display_live) {
        if (m->frames == 3) { fm1_poc_finish(e); }
        m->matrix[0] = m->frames == 1 ? 1u << 4 : 0;
    }
}

static G_NORETURN void hold_checkpoint(CPUPi32v2State *e)
{
    /* Stop virtual time and leave the display/event loop responsive.
     * Exit this helper without retiring the checkpoint instruction. */
    PI32V2_CPU(env_cpu(e))->display_held = true;
    vm_stop(RUN_STATE_PAUSED);
    cpu_loop_exit_noexc(env_cpu(e));
}

void fm1_poc_finish(CPUPi32v2State *e)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    if (m->finished) { hold_checkpoint(e); }
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
    if (m->cpu->display_fixture) {
        printf(",\"frames\":%u,\"display_visible\":%s,\"pixels_written\":%" PRIu64
               ",\"dma_transfers\":%" PRIu64 ",\"completed_transfers\":%" PRIu64,
               m->frames, fm1_lcd_visible(&m->lcd) ? "true" : "false", m->lcd.pixels_written,
               m->lcd.dma_transfers, m->lcd.completed_transfers);
    }
    if (m->cpu->diag_fixture) {
        FM1PocSystem *s = &m->system;
        printf(",\"p33_transfers\":%" PRIu64 ",\"p33_transactions\":%" PRIu64
               ",\"watchdog_arms\":%" PRIu64 ",\"watchdog_feeds\":%" PRIu64
               ",\"watchdog_expirations\":%" PRIu64 ",\"guard_checks\":%" PRIu64
               ",\"branches\":%" PRIu64 ",\"emu_control\":%u,\"debug_enable\":%u,"
               "\"write_enable\":%u",
               s->p33_transfers, s->p33_transactions, s->watchdog_arms, s->watchdog_feeds,
               s->watchdog_expirations, s->guard_checks, s->branches, s->emu_control,
               s->debug_enable, s->write_enable);
        printf(",\"loop_visits\":%" PRIu64 ",\"milliseconds\":%u,\"lcd_timeouts\":%u,"
               "\"p33_timeouts\":%u,\"usb_up\":%u,\"usb_timeouts\":%u,\"usb_retries\":%u",
               m->loop_visits, ldl_le_phys(&address_space_memory, 0x01c096a4),
               ldl_le_phys(&address_space_memory, 0x01c08028),
               ldl_le_phys(&address_space_memory, 0x01c0802c),
               ldub_phys(&address_space_memory, 0x01c097d0),
               ldl_le_phys(&address_space_memory, 0x01c097f4),
               ldl_le_phys(&address_space_memory, 0x01c09804));
        FM1PocNOR *n = &m->nor;
        printf(",\"nor\":{\"xip_enabled\":%s,\"busy\":%s,\"selected\":%s,"
               "\"transactions\":%" PRIu64 ",\"jedec_commands\":%" PRIu64
               ",\"status_commands\":%" PRIu64 ",\"read_commands\":%" PRIu64
               ",\"transfers\":%" PRIu64 ",\"completed_transfers\":%" PRIu64
               ",\"acknowledgments\":%" PRIu64 ",\"received_bytes\":%" PRIu64
               ",\"read_bytes\":%" PRIu64 ",\"sfc_disables\":%" PRIu64
               ",\"sfc_restores\":%" PRIu64 "}",
               fm1_nor_xip_enabled(n) ? "true" : "false", n->busy ? "true" : "false",
               n->selected ? "true" : "false", n->transactions, n->jedec_commands,
               n->status_commands, n->read_commands, n->transfers, n->completed_transfers,
               n->acknowledgments, n->received_bytes, n->read_bytes, n->sfc_disables,
               n->sfc_restores);
        FM1PocUSB *u = &m->usb;
        printf(",\"usb\":{\"host_connected\":%s,\"sie_clock_available\":%s,"
               "\"control\":%u,\"pads\":%u,\"requests\":%" PRIu64
               ",\"poll_reads\":%" PRIu64 ",\"abandoned_requests\":%" PRIu64
               ",\"dma_packets\":%" PRIu64 ",\"recent_requests\":[",
               u->host_connected ? "true" : "false", u->sie_clock_available ? "true" : "false",
               u->control, u->pads, u->requests, u->bridge_poll_reads,
               u->abandoned_requests, u->dma_packets);
        for (unsigned i = 0; i < 6; i++) { printf("%s%u", i ? "," : "", u->recent_requests[i]); }
        printf("],\"recent_polls\":[");
        for (unsigned i = 0; i < 6; i++) { printf("%s%" PRIu64, i ? "," : "", u->recent_polls[i]); }
        printf("]},\"lcd\":{\"visible\":%s,\"busy\":%s,\"pixels_written\":%" PRIu64
               ",\"commands\":%" PRIu64 ",\"dma_transfers\":%" PRIu64
               ",\"completed_transfers\":%" PRIu64 "}",
               fm1_lcd_visible(&m->lcd) ? "true" : "false", m->lcd.busy ? "true" : "false",
               m->lcd.pixels_written, m->lcd.commands, m->lcd.dma_transfers,
               m->lcd.completed_transfers);
        const char *state_dir = getenv("FM1_POC_STATE_DIR");
        if (state_dir) {
            g_autofree char *path = g_strdup_printf("%s/state-%08x.sram", state_dir, e->pc);
            GError *error = NULL;
            if (!g_file_set_contents(path, memory_region_get_ram_ptr(MACHINE(m)->ram),
                                     MACHINE(m)->ram_size, &error)) {
                error_report("cannot save diagnostic SRAM: %s", error->message); exit(EXIT_FAILURE);
            }
            g_autofree char *image = g_strdup_printf("%s/state-%08x.ppm", state_dir, e->pc);
            save_lcd_ppm(m, image);
        }
    }
    puts("}");
    fflush(stdout);
    if (m->keep_open) {
        m->finished = true;
        hold_checkpoint(e);
    }
    exit(EXIT_SUCCESS);
}

static void machine_init(MachineState *ms)
{
    FM1PocState *m = FM1_POC_MACHINE(ms);
    bool timer = !strcmp(ms->kernel_cmdline, "timer");
    bool display = !strcmp(ms->kernel_cmdline, "display");
    bool diag = !strcmp(ms->kernel_cmdline, "diag");
    const char *display_live = getenv("FM1_POC_DISPLAY_LIVE");
    if (display_live && (strcmp(display_live, "1") || !display)) {
        error_report("FM1_POC_DISPLAY_LIVE=1 requires the display fixture"); exit(EXIT_FAILURE);
    }
    m->display_live = display_live != NULL;
    const char *keep_open = getenv("FM1_POC_KEEP_OPEN");
    if (keep_open && (strcmp(keep_open, "1") || !diag)) {
        error_report("FM1_POC_KEEP_OPEN=1 requires the diagnostic fixture"); exit(EXIT_FAILURE);
    }
    m->keep_open = keep_open != NULL;
    bool foundation = display || !strcmp(ms->kernel_cmdline, "foundation") ||
                      !strcmp(ms->kernel_cmdline, "foundation-released");
    if (strcmp(ms->kernel_cmdline, "probe") && !timer && !foundation && !diag) {
        error_report("select -append probe, timer, foundation, foundation-released, display or diag"); exit(EXIT_FAILURE);
    }
    if (!ms->kernel_filename) { error_report("a raw fixture must be supplied with -kernel"); exit(EXIT_FAILURE); }
    m->cpu = PI32V2_CPU(cpu_create(TYPE_PI32V2_CPU));
    m->cpu->machine = m;
    m->cpu->timer_fixture = timer;
    m->cpu->foundation_fixture = foundation;
    m->cpu->display_fixture = display;
    m->cpu->diag_fixture = diag;
    m->cpu->frame_pc = display ? 0x020004fa : 0;
    m->frame_dir = getenv("FM1_POC_FRAME_DIR");
    if (!m->frame_dir && !m->display_live) { m->frame_dir = "."; }
    m->cpu->boot_pc = timer ? 0x02000238 : 0x02000120;
    m->cpu->stop_pc = display ? 0x020002be : timer || foundation ? 0x020002ba : 0x0200013a;
    if (diag) {
        const char *limit = getenv("FM1_POC_MAX_INSTRUCTIONS");
        const char *stop = getenv("FM1_POC_STOP_PC");
        const char *loop_irqs = getenv("FM1_POC_LOOP_IRQS");
        char *end = NULL;
        uint64_t parsed;
        m->cpu->instruction_limit = 100000000;
        m->cpu->stop_pc = UINT32_MAX;
        if (limit) {
            errno = 0;
            parsed = g_ascii_strtoull(limit, &end, 0);
            if (errno || !*limit || *limit == '-' || *end || !parsed) {
                error_report("FM1_POC_MAX_INSTRUCTIONS must be a positive integer"); exit(EXIT_FAILURE);
            }
            m->cpu->instruction_limit = parsed;
        }
        if (stop) {
            errno = 0;
            parsed = g_ascii_strtoull(stop, &end, 0);
            if (errno || !*stop || *stop == '-' || *end || parsed > UINT32_MAX || (parsed & 1)) {
                error_report("FM1_POC_STOP_PC must be an aligned 32-bit address"); exit(EXIT_FAILURE);
            }
            m->cpu->stop_pc = parsed;
        }
        if (loop_irqs) {
            errno = 0;
            parsed = g_ascii_strtoull(loop_irqs, &end, 0);
            if (errno || !*loop_irqs || *loop_irqs == '-' || *end || !parsed) {
                error_report("FM1_POC_LOOP_IRQS must be a positive integer"); exit(EXIT_FAILURE);
            }
            m->loop_target_irqs = parsed;
            m->cpu->diag_loop_checkpoint = true;
        }
    }
    cpu_reset(CPU(m->cpu));
    memory_region_add_subregion(get_system_memory(), 0x01c00000, ms->ram);
    if (diag) {
        uint8_t *ram = memory_region_get_ram_ptr(ms->ram);
        /* Preserve the explicit loader handoff and zeroed persistent regions;
         * poison every region the unchanged startup must initialize. */
        memset(ram, 0xa5, 0xb48);
        memset(ram + 0x8000, 0xa5, 0x14);
        memset(ram + 0x8020, 0xa5, 0x1ce0);
        memset(ram + 0x7fd80, 0xa5, 0x80);
    }
    if (foundation) {
        /* Poison only the new startup fixture. Guest copies/clears must replace
         * this state; the existing probe/timer seed stays unchanged. */
        memset(memory_region_get_ram_ptr(ms->ram), 0xa5, ms->ram_size);
        if (!strcmp(ms->kernel_cmdline, "foundation")) { m->matrix[0] = 1u << 4; }
    }
    m->latched = UINT16_MAX;
    if (diag) {
        m->gpio[3][0] = 1;
        m->iomap_con0 = 0x20;
        fm1_nor_init(&m->nor, OBJECT(m), m->cpu, ms->kernel_filename);
        fm1_nor_set_pins(&m->nor, m->gpio[3][0], m->iomap_con0);
    } else {
        memory_region_init_rom(&m->xip, NULL, "fm1.xip", 0x100000, &error_fatal);
        memory_region_add_subregion(get_system_memory(), 0x02000000, &m->xip);
        if (load_image_targphys(ms->kernel_filename, 0x02000120, 0xffee0) <= 0) {
            error_report("cannot load raw fixture"); exit(EXIT_FAILURE);
        }
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
    memory_region_init_io(&m->gpio_mmio, OBJECT(m), &gpio_ops, m, "fm1.gpio", diag ? 0x1e0 : display ? 0xa0 : sizeof(m->gpio[0]));
    memory_region_add_subregion(get_system_memory(), 0x50000, &m->gpio_mmio);
    memory_region_init_io(&m->irq_mmio, OBJECT(m), &irq_ops, m, "fm1.irq63", 0xac);
    memory_region_add_subregion(get_system_memory(), 0x01eef100, &m->irq_mmio);
    m->irq = qdev_get_gpio_in(DEVICE(m->cpu), 0);
    if (diag) {
        fm1_system_init(&m->system, OBJECT(m), m->cpu);
        fm1_usb_init(&m->usb, OBJECT(m), m->cpu);
    }
    if (display || diag) {
        fm1_lcd_init(&m->lcd, OBJECT(m), m->cpu);
        memory_region_init_io(&m->iomap_mmio, OBJECT(m), &iomap_ops, m, "fm1.iomap", diag ? 8 : 4);
        memory_region_add_subregion(get_system_memory(), diag ? 0x5101c : 0x51020, &m->iomap_mmio);
        fm1_lcd_set_pins(&m->lcd, m->gpio[2][0], m->iomap_con1, m->gpio[0][0]);
    }
    if (m->display_live) {
        m->display_key_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, display_key_toggle, m);
        m->display_key_deadline = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 500000000;
        timer_mod_ns(m->display_key_timer, m->display_key_deadline);
    }
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
