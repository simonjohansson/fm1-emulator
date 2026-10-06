/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh implementation from reached register/width and guest protocol facts.
 * The sole supported configuration transmits 256 stereo frames per half.
 * 44100 Hz is a documented functional clock matching the selected firmware's
 * generated FS constant, not a calibrated model of the boot PLL or codec.
 * DMA samples are observed at completion boundaries; late callbacks cannot
 * reconstruct earlier SRAM contents and explicitly count skipped captures.
 * No host audio playback, receiver channels or auxiliary pending source is
 * implemented. All other register/configuration accesses fail explicitly. */
#include "qemu/osdep.h"
#include "qemu/bswap.h"
#include "exec/address-spaces.h"
#include "fm1-alnk.h"

#define ALNK_BASE 0x12e00u
#define CLOCK_BASE 0x10014u
#define IOMAP_BASE 0x51030u
#define SRAM_BASE 0x01c00000u
#define SRAM_END 0x01c80000u
#define DMA_ENABLE 0x0800u
#define ACTIVE_HALF 0x8000u
#define HALF_PENDING 0x80u
#define HALF_FRAMES (FM1_ALNK_HALF_WORDS / 2u)
#define PERIOD_NUMERATOR (HALF_FRAMES * 1000000000ull)

static G_NORETURN void alnk_fail(FM1PocALNK *a, const char *reason)
{
    pi32v2_fail(&a->cpu->env, reason);
}

static void update_irq(FM1PocALNK *a)
{
    a->irq_level = a->enabled && (a->pending & HALF_PENDING);
    qemu_set_irq(a->irq, a->irq_level);
}

static int64_t next_deadline(FM1PocALNK *a)
{
    /* Split the rational period to avoid the short overflow horizon of
     * half_count * 256 * 1e9. Rounding occurs on the cumulative remainder. */
    uint64_t n = a->scheduled_halves + 1;
    uint64_t whole = PERIOD_NUMERATOR / FM1_ALNK_FRAME_RATE;
    uint64_t remainder = PERIOD_NUMERATOR % FM1_ALNK_FRAME_RATE;
    if (n > (INT64_MAX - (uint64_t)a->epoch) / (whole + 1)) {
        alnk_fail(a, "ALNK0 virtual completion deadline overflow");
    }
    return a->epoch + n * whole +
           DIV_ROUND_UP(n * remainder, FM1_ALNK_FRAME_RATE);
}

static void capture_half(FM1PocALNK *a, unsigned half)
{
    uint32_t start = a->dma_address + half * FM1_ALNK_HALF_BYTES;
    for (unsigned offset = 0; offset < FM1_ALNK_HALF_BYTES; offset += 256) {
        if (address_space_read(&address_space_memory, start + offset,
                               MEMTXATTRS_UNSPECIFIED, a->latest_half + offset,
                               256) != MEMTX_OK) {
            alnk_fail(a, "ALNK0 DMA could not read guest SRAM");
        }
    }
    for (unsigned offset = 0; offset < FM1_ALNK_HALF_BYTES; offset++) {
        a->sample_digest = (a->sample_digest ^ a->latest_half[offset]) *
                           16777619u;
    }
    for (unsigned offset = 0; offset < FM1_ALNK_HALF_BYTES; offset += 4) {
        if (ldl_le_p(a->latest_half + offset)) {
            a->nonzero_words++;
        }
    }
    a->sample_words += FM1_ALNK_HALF_WORDS;
    a->sample_frames += HALF_FRAMES;
    a->last_half = half;
    a->latest_half_bytes = FM1_ALNK_HALF_BYTES;
}

static void completed(void *opaque)
{
    FM1PocALNK *a = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    if (!a->enabled || now < a->deadline) {
        alnk_fail(a, "ALNK0 completion outside an enabled deadline");
    }
    uint64_t elapsed = now - a->epoch;
    uint64_t frames = (elapsed / 1000000000ull) * FM1_ALNK_FRAME_RATE +
                      (elapsed % 1000000000ull) * FM1_ALNK_FRAME_RATE /
                      1000000000ull;
    uint64_t reached = frames / HALF_FRAMES;
    uint64_t due = reached - a->scheduled_halves;
    if (!due) { alnk_fail(a, "ALNK0 deadline did not complete a half"); }
    /* Keep the real level latch, rather than queueing one ISR per deadline.
     * If callbacks skipped boundaries, only the newest completed half can
     * be sampled from current SRAM; earlier contents are not fabricated. */
    a->coalesced_completions += due - !(a->pending & HALF_PENDING);
    a->skipped_captures += due - 1;
    unsigned finished_half = (a->active_half + due - 1) & 1;
    capture_half(a, finished_half);
    a->active_half = (a->active_half + due) & 1;
    a->scheduled_halves = reached;
    a->completions += due;
    a->pending |= HALF_PENDING;
    update_irq(a);
    a->deadline = next_deadline(a);
    timer_mod_ns(a->timer, a->deadline);
}

static void check_configuration(FM1PocALNK *a)
{
    if (a->control0 != (0x0180u | DMA_ENABLE) ||
        a->control1 != 0x5000u || a->control3 != 0x83u ||
        a->half_words != FM1_ALNK_HALF_WORDS ||
        a->clock_control || a->iomap_control) {
        alnk_fail(a, "unsupported ALNK0 enabled configuration");
    }
    if ((a->dma_address & 3) || a->dma_address < SRAM_BASE ||
        (uint64_t)a->dma_address + 2u * FM1_ALNK_HALF_BYTES > SRAM_END) {
        alnk_fail(a, "ALNK0 double buffer must be aligned and entirely in SRAM");
    }
}

static void control0_write(FM1PocALNK *a, uint16_t value)
{
    if (value & ~(0x0180u | DMA_ENABLE | ACTIVE_HALF)) {
        alnk_fail(a, "unsupported ALNK0 CON0 bits");
    }
    uint16_t control = value & ~ACTIVE_HALF;
    if (a->enabled && ((control ^ a->control0) & ~DMA_ENABLE)) {
        alnk_fail(a, "ALNK0 configuration changed while DMA is enabled");
    }
    a->control0 = control;
    bool enabled = control & DMA_ENABLE;
    if (enabled && !a->enabled) {
        check_configuration(a);
        a->epoch = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        a->scheduled_halves = 0;
        a->active_half = 0;
        a->deadline = next_deadline(a);
        timer_mod_ns(a->timer, a->deadline);
    } else if (!enabled && a->enabled) {
        timer_del(a->timer);
        a->deadline = 0;
    }
    a->enabled = enabled;
    update_irq(a);
}

static bool correct_width(hwaddr offset, unsigned size)
{
    switch (offset) {
    case 0: case 4: case 0x20: return size == 2;
    case 8: case 12: return size == 1;
    case 0x1c: return size == 4;
    default: return false;
    }
}

static uint64_t alnk_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocALNK *a = opaque;
    if (!correct_width(offset, size)) {
        alnk_fail(a, "unsupported ALNK0 register read or width");
    }
    switch (offset) {
    case 0: return a->control0 | (a->active_half ? ACTIVE_HALF : 0);
    case 4: return a->control1;
    case 8: return a->pending;
    case 12: return a->control3;
    default: alnk_fail(a, "ALNK0 DMA address and length are write-only");
    }
}

static void alnk_write(void *opaque, hwaddr offset, uint64_t value,
                       unsigned size)
{
    FM1PocALNK *a = opaque;
    if (!correct_width(offset, size)) {
        alnk_fail(a, "unsupported ALNK0 register write or width");
    }
    switch (offset) {
    case 0: control0_write(a, value); return;
    case 8: {
        /* Pending bits are read-only. RMW acknowledgments may write them
         * back; low bits clear the matching high bits and self-clear. */
        uint8_t cleared = a->pending & ((value & 15u) << 4);
        if (cleared & HALF_PENDING) { a->acknowledgments++; }
        a->pending &= ~cleared;
        update_irq(a);
        return;
    }
    case 4:
        if (value != 0 && value != 0x1000 && value != 0x5000) {
            alnk_fail(a, "unsupported ALNK0 CON1 configuration");
        }
        if (a->enabled && value != a->control1) {
            alnk_fail(a, "ALNK0 configuration changed while DMA is enabled");
        }
        a->control1 = value;
        return;
    case 12:
        if (value != 0 && value != 3 && value != 0x83) {
            alnk_fail(a, "unsupported ALNK0 CON3 configuration");
        }
        if (a->enabled && value != a->control3) {
            alnk_fail(a, "ALNK0 configuration changed while DMA is enabled");
        }
        a->control3 = value;
        return;
    case 0x1c:
        if (a->enabled) { alnk_fail(a, "ALNK0 DMA address changed while enabled"); }
        a->dma_address = value;
        return;
    case 0x20:
        if (a->enabled) { alnk_fail(a, "ALNK0 DMA length changed while enabled"); }
        if (value != FM1_ALNK_HALF_WORDS) {
            alnk_fail(a, "unsupported ALNK0 DMA half length");
        }
        a->half_words = value;
        return;
    }
    g_assert_not_reached();
}

static uint64_t clock_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocALNK *a = opaque;
    if (offset || size != 4) { alnk_fail(a, "unsupported ALNK0 clock read or width"); }
    return a->clock_control;
}

static void clock_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocALNK *a = opaque;
    if (offset || size != 4 || value & ~0xf00ull) {
        alnk_fail(a, "unsupported ALNK0 clock configuration");
    }
    if (a->enabled && value != a->clock_control) {
        alnk_fail(a, "ALNK0 clock changed while DMA is enabled");
    }
    a->clock_control = value;
}

static uint64_t iomap_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocALNK *a = opaque;
    if (offset || size != 4) { alnk_fail(a, "unsupported ALNK0 routing read or width"); }
    return a->iomap_control;
}

static void iomap_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocALNK *a = opaque;
    if (offset || size != 4 || value & ~0xc0ull) {
        alnk_fail(a, "unsupported ALNK0 pin routing configuration");
    }
    if (a->enabled && value != a->iomap_control) {
        alnk_fail(a, "ALNK0 routing changed while DMA is enabled");
    }
    a->iomap_control = value;
}

static const MemoryRegionOps alnk_ops = {
    .read = alnk_read, .write = alnk_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 1, .max_access_size = 4},
    .impl = {.min_access_size = 1, .max_access_size = 4},
};
#define WORD_OPS(name) \
static const MemoryRegionOps name##_ops = { \
    .read = name##_read, .write = name##_write, \
    .endianness = DEVICE_LITTLE_ENDIAN, \
    .valid = {.min_access_size = 4, .max_access_size = 4}, \
    .impl = {.min_access_size = 4, .max_access_size = 4}, \
}
WORD_OPS(clock);
WORD_OPS(iomap);

void fm1_alnk_init(FM1PocALNK *a, Object *owner, Pi32v2CPU *cpu,
                  qemu_irq irq)
{
    a->cpu = cpu;
    a->irq = irq;
    a->sample_digest = 2166136261u;
    a->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, completed, a);
    memory_region_init_io(&a->mmio, owner, &alnk_ops, a, "fm1.alnk0", 0x24);
    memory_region_add_subregion(get_system_memory(), ALNK_BASE, &a->mmio);
    memory_region_init_io(&a->clock_mmio, owner, &clock_ops, a, "fm1.alnk-clock", 4);
    memory_region_add_subregion(get_system_memory(), CLOCK_BASE, &a->clock_mmio);
    memory_region_init_io(&a->iomap_mmio, owner, &iomap_ops, a, "fm1.alnk-routing", 4);
    memory_region_add_subregion(get_system_memory(), IOMAP_BASE, &a->iomap_mmio);
    update_irq(a);
}
