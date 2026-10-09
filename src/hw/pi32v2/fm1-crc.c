/* SPDX-License-Identifier: GPL-2.0-or-later */
/* CRC unit (JL_CRC: FIFO, REG). Measured on a real FM-1: each FIFO write
 * folds its low byte into REG as CRC-16 polynomial 0x1021, MSB first, from
 * the value last written to REG (single bytes from seeds 0, 1, 0x8000 and
 * 0xffff all match). The unit needs a moment per byte; firmware issues csync
 * before reading REG, so completing each byte at once is the observable
 * behavior. */
#include "qemu/osdep.h"
#include "fm1-crc.h"
#include "fm1-sfr.h"

static uint64_t crc_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocCRC *c = opaque;
    if (offset != 4) {
        pi32v2_fail(&c->cpu->env, "CRC FIFO is write-only");
    }
    return c->value;
}

static void crc_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocCRC *c = opaque;
    if (offset == 4) {
        c->value = value & 0xffff;
        return;
    }
    c->value ^= (value & 0xff) << 8;
    for (unsigned bit = 0; bit < 8; bit++) {
        c->value = c->value & 0x8000 ? (c->value << 1 ^ 0x1021) & 0xffff
                                     : (c->value << 1) & 0xffff;
    }
}

static const MemoryRegionOps crc_ops = {
    .read = crc_read, .write = crc_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

void fm1_crc_init(FM1PocCRC *c, Object *owner, Pi32v2CPU *cpu)
{
    c->cpu = cpu;
    memory_region_init_io(&c->mmio, owner, &crc_ops, c, "fm1.crc", 8);
    fm1_sfr_map(0x13500, &c->mmio);
}
