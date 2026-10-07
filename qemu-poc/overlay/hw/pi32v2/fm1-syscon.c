/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Ownership extraction of existing register behavior only. No clock tree,
 * rates, reset behavior or new register fields are implemented here. */
#include "qemu/osdep.h"
#include "fm1-syscon.h"

static const uint64_t masks[FM1_SYSCON_WORD_COUNT] = {
    [FM1_SYSCON_CLK_CON1] = 3,
    [FM1_SYSCON_CLK_CON2] = 0xf00,
    [FM1_SYSCON_IOMAP_CON5] = 0xc0,
};

static const char *const region_names[FM1_SYSCON_WORD_COUNT] = {
    [FM1_SYSCON_CLK_CON1] = "fm1.usb-clock",
    [FM1_SYSCON_CLK_CON2] = "fm1.alnk-clock",
    [FM1_SYSCON_IOMAP_CON5] = "fm1.alnk-routing",
};

static const char *const read_errors[FM1_SYSCON_WORD_COUNT] = {
    [FM1_SYSCON_CLK_CON2] = "unsupported ALNK0 clock read or width",
    [FM1_SYSCON_IOMAP_CON5] = "unsupported ALNK0 routing read or width",
};

static const char *const write_errors[FM1_SYSCON_WORD_COUNT] = {
    [FM1_SYSCON_CLK_CON1] = "unsupported USB clock selector fields",
    [FM1_SYSCON_CLK_CON2] = "unsupported ALNK0 clock configuration",
    [FM1_SYSCON_IOMAP_CON5] = "unsupported ALNK0 pin routing configuration",
};

static uint64_t word_read(FM1PocSyscon *s, FM1SysconWord word,
                          hwaddr offset, unsigned size)
{
    /* Retain the existing ALNK callback checks; CLK_CON1 uses the same
     * word-only MemoryRegionOps validation as its former USB owner. */
    if (word != FM1_SYSCON_CLK_CON1 && (offset || size != 4)) {
        pi32v2_fail(&s->cpu->env, read_errors[word]);
    }
    return fm1_syscon_get(s, word);
}

static void word_write(FM1PocSyscon *s, FM1SysconWord word, hwaddr offset,
                       uint64_t value, unsigned size)
{
    if ((word != FM1_SYSCON_CLK_CON1 && (offset || size != 4)) ||
        (value & ~masks[word])) {
        pi32v2_fail(&s->cpu->env, write_errors[word]);
    }
    /* A consumer may reject an otherwise valid field change while active.
     * Canonical state must still contain the previous value when it faults. */
    if (s->validators[word]) {
        s->validators[word](s->validator_opaque[word], s->words[word], value);
    }
    s->words[word] = value;
}

#define WORD_OPS(name, word) \
static uint64_t name##_read(void *opaque, hwaddr offset, unsigned size) \
{ \
    return word_read(opaque, word, offset, size); \
} \
static void name##_write(void *opaque, hwaddr offset, uint64_t value, \
                         unsigned size) \
{ \
    word_write(opaque, word, offset, value, size); \
} \
static const MemoryRegionOps name##_ops = { \
    .read = name##_read, .write = name##_write, \
    .endianness = DEVICE_LITTLE_ENDIAN, \
    .valid = {.min_access_size = 4, .max_access_size = 4}, \
    .impl = {.min_access_size = 4, .max_access_size = 4}, \
}
WORD_OPS(clk_con1, FM1_SYSCON_CLK_CON1);
WORD_OPS(clk_con2, FM1_SYSCON_CLK_CON2);
WORD_OPS(iomap_con5, FM1_SYSCON_IOMAP_CON5);

static const MemoryRegionOps *const word_ops[FM1_SYSCON_WORD_COUNT] = {
    [FM1_SYSCON_CLK_CON1] = &clk_con1_ops,
    [FM1_SYSCON_CLK_CON2] = &clk_con2_ops,
    [FM1_SYSCON_IOMAP_CON5] = &iomap_con5_ops,
};

void fm1_syscon_init(FM1PocSyscon *s, Object *owner, Pi32v2CPU *cpu)
{
    s->cpu = cpu;
    for (unsigned i = 0; i < FM1_SYSCON_WORD_COUNT; i++) {
        s->words[i] = 0;
        s->validators[i] = NULL;
        s->validator_opaque[i] = NULL;
        memory_region_init_io(&s->mmio[i], owner, word_ops[i], s,
                              region_names[i], 4);
    }
}

uint32_t fm1_syscon_get(const FM1PocSyscon *s, FM1SysconWord word)
{
    g_assert((unsigned)word < FM1_SYSCON_WORD_COUNT);
    return s->words[word];
}

void fm1_syscon_set_validator(FM1PocSyscon *s, FM1SysconWord word,
                              FM1SysconValidateWrite validate, void *opaque)
{
    g_assert((unsigned)word < FM1_SYSCON_WORD_COUNT);
    g_assert(validate && !s->validators[word]);
    s->validators[word] = validate;
    s->validator_opaque[word] = opaque;
}

void fm1_syscon_clear_validator(FM1PocSyscon *s, FM1SysconWord word,
                                FM1SysconValidateWrite validate, void *opaque)
{
    g_assert((unsigned)word < FM1_SYSCON_WORD_COUNT);
    g_assert(validate && s->validators[word] == validate &&
             s->validator_opaque[word] == opaque);
    s->validators[word] = NULL;
    s->validator_opaque[word] = NULL;
}
