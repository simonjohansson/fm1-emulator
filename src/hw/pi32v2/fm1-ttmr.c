/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Core tick timer (q32DSP TTMR_CON byte, TTMR_CNT, TTMR_PRD; IRQ 3).
 * Measured on a real FM-1: CNT counts at 360 MHz, the system clock the SPL
 * configures; CON bit 0 runs it and stopping freezes CNT. When CNT reaches
 * PRD it restarts at 0 and sets CON bit 7, which writing bit 6 clears. A CNT
 * above PRD counts on through 2^32 without a match. */
#include "qemu/osdep.h"
#include "fm1-ttmr.h"
#include "fm1-sfr.h"

#define TTMR_ENABLE 1u
#define TTMR_CLEAR 0x40u
#define TTMR_PENDING 0x80u

static uint64_t ticks_to_ns(uint64_t ticks) { return DIV_ROUND_UP(ticks * 25, 9); }

static uint64_t elapsed(FM1PocTTMR *t)
{
    return (qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) - t->epoch) * 9 / 25;
}

static uint32_t counter(FM1PocTTMR *t)
{
    if (!(t->control & TTMR_ENABLE)) { return t->counter; }
    uint64_t e = elapsed(t), first = (uint64_t)(uint32_t)(t->period - t->counter) + 1;
    return e < first ? t->counter + e : (e - first) % ((uint64_t)t->period + 1);
}

static void rearm(FM1PocTTMR *t)
{
    timer_del(t->timer);
    t->epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    if (t->control & TTMR_ENABLE) {
        t->next_wrap = (uint64_t)(uint32_t)(t->period - t->counter) + 1;
        timer_mod_ns(t->timer, t->epoch + ticks_to_ns(t->next_wrap));
    }
}

static void expired(void *opaque)
{
    FM1PocTTMR *t = opaque;
    t->pending = true;
    t->update_irq(t->opaque);
    t->next_wrap += (uint64_t)t->period + 1;
    timer_mod_ns(t->timer, t->epoch + ticks_to_ns(t->next_wrap));
}

static uint64_t ttmr_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocTTMR *t = opaque;
    switch (offset) {
    case 0: return t->control | (t->pending ? TTMR_PENDING : 0);
    case 4: return counter(t);
    case 8: return t->period;
    default: pi32v2_fail(&t->cpu->env, "unsupported TTMR register read");
    }
}

static void ttmr_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocTTMR *t = opaque;
    t->counter = counter(t);
    switch (offset) {
    case 0:
        if (value & ~(uint64_t)(TTMR_ENABLE | TTMR_CLEAR)) {
            pi32v2_fail(&t->cpu->env, "unsupported TTMR control bits");
        }
        if (value & TTMR_CLEAR) {
            t->pending = false;
            t->update_irq(t->opaque);
        }
        t->control = value & TTMR_ENABLE;
        break;
    case 4: t->counter = value; break;
    case 8: t->period = value; break;
    default: pi32v2_fail(&t->cpu->env, "unsupported TTMR register write");
    }
    rearm(t);
}

static bool ttmr_accepts(void *opaque, hwaddr offset, unsigned size,
                         bool is_write, MemTxAttrs attrs)
{
    return offset ? size == 4 : size == 1;
}

static const MemoryRegionOps ttmr_ops = {
    .read = ttmr_read, .write = ttmr_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 1, .max_access_size = 4, .accepts = ttmr_accepts},
};

void fm1_ttmr_init(FM1PocTTMR *t, Object *owner, Pi32v2CPU *cpu,
                   void (*update_irq)(void *opaque), void *opaque)
{
    t->cpu = cpu;
    t->update_irq = update_irq;
    t->opaque = opaque;
    t->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, expired, t);
    memory_region_init_io(&t->mmio, owner, &ttmr_ops, t, "fm1.ttmr", 12);
    fm1_sfr_map(0x01eef0ec, &t->mmio);
}
