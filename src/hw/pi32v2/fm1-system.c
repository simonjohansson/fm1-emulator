/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Independently written diagnostic subset. The tracked guest describes the
 * P33 byte protocol, watchdog selector periods, debug unlock key and guard
 * register interfaces. Transfer latency is a functional 1 us per P33 byte;
 * neither the bridge nor watchdog uses host wall time. Guard violations and
 * reset requests terminate explicitly: hardware exception/reset dispatch is
 * not implemented by this bounded machine. */
#include "qemu/osdep.h"
#include "system/address-spaces.h"
#include "fm1-system.h"
#include "fm1-sfr.h"

#define P33_CS 1u
#define P33_BUSY 2u
#define P33_DIVIDER 12u
#define P33_RTC 0x100u
#define P33_START 16u
#define WDT_ENABLE 16u
#define WDT_MODE 32u
#define WDT_CLEAR 64u

static G_NORETURN void system_fail(FM1PocSystem *s, const char *reason)
{
    pi32v2_fail(current_cpu ? cpu_env(current_cpu) : &s->cpu->env, reason);
}

/* Plain P33 bytes stock FM-1 firmware touches, as a real FM-1 holds them
 * after its SPL (measured). Address bit 10 marks the RTC domain. */
static const struct { uint16_t address; uint8_t handoff; } p33_plain[] = {
    {0x11, 0x05},           /* P3_VLVD_CON; no supply-voltage events */
    {0x31, 0x80},           /* written 0xc0 by stock firmware */
    {0x93, 0xff},           /* P3_WKUP_PND */
    {0x94, 0x08},           /* P3_PINR_CON; no external reset-pin events */
    {0x400 | 0xa8, 0x02},   /* R3_WKUP_SRC */
};

static int p33_plain_index(uint16_t address)
{
    for (unsigned i = 0; i < G_N_ELEMENTS(p33_plain); i++) {
        if (p33_plain[i].address == address) { return i; }
    }
    return -1;
}

static bool p33_latch(uint16_t address)
{
    return (address >= 0xad && address <= 0xaf) ||
           (address >= 0xd0 && address <= 0xdf);
}

static uint8_t register_read(FM1PocSystem *s, uint16_t address)
{
    int plain = p33_plain_index(address);
    if (plain >= 0) { return s->p33_plain[plain]; }
    /* P3_{P33,OTH,USB}_LAT and PORT[A-H]_LAT[0-1], measured clear. */
    if (p33_latch(address)) { return 0; }
    switch (address) {
    case 0x12: return s->p3_reset_source;
    case 0x17: return s->valid_keep;
    case 0x80: return s->watchdog_control;
    case 0xa0: return s->power_control;
    default: system_fail(s, "unsupported P33 register address");
    }
}

static void watchdog_expired(void *opaque)
{
    FM1PocSystem *s = opaque;
    s->watchdog_expirations++;
    system_fail(s, "P33 watchdog expired: machine reset is unsupported");
}

static void register_write(FM1PocSystem *s, uint16_t address, uint8_t value)
{
    int plain = p33_plain_index(address);
    if (plain >= 0) { s->p33_plain[plain] = value; return; }
    if (p33_latch(address)) {
        /* Stock clears the port latches. Pin retention itself is not
         * modeled, so refuse attempts to latch a port. */
        if (value) { system_fail(s, "unsupported P33 port latch enable"); }
        return;
    }
    switch (address) {
    case 0x17:
        /* Bit 6 (SDK WDT_EXPT_EN) selects an exception at watchdog expiry,
         * which this machine refuses in either mode. */
        s->valid_keep = value;
        break;
    case 0x80: {
        uint8_t previous = s->watchdog_control;
        /* Stock FM-1 firmware writes 0x2c. Bit 5 only matters at expiry,
         * which this machine refuses either way, so it is kept unmodeled. */
        if (value & ~(WDT_CLEAR | WDT_MODE | WDT_ENABLE | 15u)) {
            system_fail(s, "unsupported P33 watchdog control bits");
        }
        s->watchdog_control = value & ~WDT_CLEAR;
        if (value & WDT_CLEAR) { s->watchdog_feeds++; }
        if (!(value & WDT_ENABLE)) {
            timer_del(s->watchdog_timer);
            s->watchdog_deadline = 0;
            break;
        }
        unsigned selector = value & 15;
        if (selector < 10) {
            system_fail(s, "unsupported P33 watchdog timeout selector");
        }
        if (!(previous & WDT_ENABLE)) { s->watchdog_arms++; }
        if ((value & WDT_CLEAR) || previous != s->watchdog_control ||
            !s->watchdog_deadline) {
            s->watchdog_deadline = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                                   (1000000000ll << (selector - 10));
            timer_mod_ns(s->watchdog_timer, s->watchdog_deadline);
        }
        break;
    }
    case 0xa0:
        if (value & 16) { system_fail(s, "P33 chip reset request is unsupported"); }
        if (value) { system_fail(s, "unsupported P33 power control bits"); }
        s->power_control = value;
        break;
    case 0x12: system_fail(s, "P33 reset source is read-only");
    default: system_fail(s, "unsupported P33 register write");
    }
}

static void p33_complete(void *opaque)
{
    FM1PocSystem *s = opaque;
    uint8_t byte = s->transfer_byte;
    switch (s->phase) {
    case 0:
        if ((byte & 0x1c) || ((byte & 0x80) && (byte & 0x60))) {
            system_fail(s, "unsupported P33 transaction command");
        }
        s->command = byte;
        s->address = (byte & 3u) << 8 | (s->p33_control & P33_RTC ? 0x400 : 0);
        break;
    case 1:
        s->address |= byte;
        register_read(s, s->address); /* Reject unknown addresses explicitly. */
        break;
    case 2: {
        uint8_t old = register_read(s, s->address);
        if (s->command & 0x80) {
            byte = old;
        } else {
            switch ((s->command >> 5) & 3) {
            case 0: break;
            case 1: byte |= old; break;
            case 2: byte &= old; break;
            case 3: byte ^= old; break;
            }
            register_write(s, s->address, byte);
        }
        s->p33_transactions++;
        break;
    }
    default: system_fail(s, "P33 transaction exceeds three bytes");
    }
    s->phase++;
    s->p33_data = byte;
    s->p33_busy = false;
    s->p33_transfers++;
}

static uint64_t p33_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocSystem *s = opaque;
    if (offset == 0) { return s->p33_control | (s->p33_busy ? P33_BUSY : 0); }
    if (offset == 4) { return s->p33_data; }
    system_fail(s, "unsupported P33 bridge register read");
}

static void p33_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocSystem *s = opaque;
    if (offset == 4) {
        if (value > UINT8_MAX || s->p33_busy) {
            system_fail(s, "invalid or overlapping P33 data write");
        }
        s->p33_data = value;
        return;
    }
    /* Bits 2-3 are a clock divider (stock FM-1 firmware reads them back and
     * changes them around transfers); transfer latency here ignores it. */
    if (offset != 0 || value & ~(P33_CS | P33_BUSY | P33_DIVIDER | P33_START | P33_RTC)) {
        system_fail(s, "unsupported P33 control or RTC domain");
    }
    if (s->p33_busy) { system_fail(s, "P33 control changed during byte transfer"); }
    bool selected = value & P33_CS;
    if (selected != !!(s->p33_control & P33_CS)) {
        if (!selected && s->phase != 3) {
            system_fail(s, "P33 chip select ends an incomplete transaction");
        }
        s->phase = 0;
    }
    s->p33_control = value & (P33_CS | P33_DIVIDER | P33_RTC);
    if (value & P33_START) {
        if (!selected || s->phase >= 3) {
            system_fail(s, "P33 transfer requires an active three-byte transaction");
        }
        s->transfer_byte = s->p33_data;
        s->p33_busy = true;
        timer_mod_ns(s->p33_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 1000);
    }
}

static uint64_t reset_read(void *opaque, hwaddr offset, unsigned size)
{
    return ((FM1PocSystem *)opaque)->reset_source;
}

static void reset_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    system_fail(opaque, "chip reset source is read-only");
}

/* CACHE_CON, DCACHE_WAY and ICACHE_WAY (WL82 corex2 SFRs). */
static uint64_t cache_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocSystem *s = opaque;
    /* This machine has no asynchronous cache fill/flush queue: QEMU's memory
     * access and translated-code invalidation are synchronous. CACHE_CON's
     * idle flag therefore describes the model's actual idle state. */
    if (offset == 0) { return 0x4000 | s->cache_control; }
    return s->cache_way[(offset - 4) / 4];
}

static void cache_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocSystem *s = opaque;
    /* Allow read/modify/write of the read-only idle bit, the two enables
     * stock startup clears around way allocation and sets again (bits 8 and
     * 9) and bit 1, found set at handoff. Enabling a cache changes nothing
     * here. Other cache commands remain unqualified rather than discarded. */
    if (offset == 0) {
        if (value & ~0x4302ull) {
            system_fail(s, "cache control writes are unsupported");
        }
        s->cache_control = value & 0x302;
        return;
    }
    /* Way allocation only partitions the cache, which memory here does not
     * model: QEMU accesses are coherent and synchronous. Keep the value. */
    s->cache_way[(offset - 4) / 4] = value;
}

/* SDRAM (JL_SDR) and PSRAM controllers: the board has neither memory, and
 * application handoff leaves both disabled. Reads return the values a real
 * FM-1 holds after its SPL (measured; SDR_CON0 bit 11 and PSRAM_CON bit 0
 * clear). Configuring either is unimplemented. */
static const uint32_t sdr_handoff[18] = {
    0, 0, 0, 0x40ff0000,
    0xffffffff, 0xffffffff, 0xffffffff, 0xffffffff, 0xffffffff,
    0xffffffff, 0xffffffff, 0xffffffff, 0xffffffff, 0xffffffff,
    0, 0, 0, 0,
};

static uint64_t sdr_read(void *opaque, hwaddr offset, unsigned size)
{
    if (offset == 4) { system_fail(opaque, "SDR_CON1 is write-only"); }
    return sdr_handoff[offset / 4];
}

static void sdr_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    /* Stock FM-1 firmware clears SDR word 14, which already reads 0. */
    if (offset != 4 && value == sdr_handoff[offset / 4]) { return; }
    system_fail(opaque, "SDRAM controller configuration is unimplemented");
}

static uint64_t psram_read(void *opaque, hwaddr offset, unsigned size)
{
    if (offset) { system_fail(opaque, "PSRAM baud and queue registers are write-only"); }
    return 0x01520034;
}

static void psram_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    system_fail(opaque, "PSRAM controller configuration is unimplemented");
}

/* JL_CLOCK SYS_DIV as a real FM-1's SPL leaves it (measured). Only the
 * core-start bit is writable; divider changes remain unimplemented. */
static uint64_t sys_div_read(void *opaque, hwaddr offset, unsigned size)
{
    return ((FM1PocSystem *)opaque)->sys_div;
}

static void sys_div_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocSystem *s = opaque;
    /* Stock toggles bit 3 around core-1 release and restores it afterwards.
     * Divider/rate changes remain unsupported; icount policy is unchanged. */
    if ((value ^ 0x00010200ull) & ~8ull) {
        system_fail(s, "system clock divider changes are unimplemented");
    }
    s->sys_div = value;
}

/* JL_INTEST CHIP_ID as a real FM-1 reads it. */
static uint64_t chip_id_read(void *opaque, hwaddr offset, unsigned size)
{
    return 0x6f01;
}

static void chip_id_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    system_fail(opaque, "CHIP_ID is read-only");
}

/* Configuration words whose effects this machine does not model: clocks,
 * rates and analog settings follow virtual time instead. Each starts as a
 * real FM-1's SPL leaves it (measured; Felucca writes none of them) and
 * keeps whatever firmware writes. Stock FM-1 firmware reads the PLLs to
 * derive its clocks and reprograms the USB PHY PLL. */
static const struct {
    const char *name;
    hwaddr address;
    unsigned words;
    uint32_t handoff[FM1_STORED_WORDS];
} stored_blocks[FM1_STORED_COUNT] = {
    [FM1_STORED_PMU] = {"fm1.p33-pmu", 0x13e00, 2, {0x100, 0xe0}},   /* PMU_CON, RTC_CON */
    [FM1_STORED_PLL] = {"fm1.pll", 0x119a0, 4,                      /* PLL_CON0..PLL2_CON1 */
                        {0x45400203, 0x3f503026, 0x0940022b, 0x0750310c}},
    [FM1_STORED_USB_PHY] = {"fm1.usb-phy", 0x16a00, 4, {0, 0x008881c3}},
    [FM1_STORED_OSA] = {"fm1.osa", 0x13400, 1, {0x80}},              /* JL_OSA CON */
    [FM1_STORED_DBG] = {"fm1.dbg", 0x41c00, 4, {0}},                 /* JL_DBG; stock clears it */
};

static uint64_t stored_read(void *opaque, hwaddr offset, unsigned size)
{
    return ((uint32_t *)opaque)[offset / 4];
}

static void stored_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    ((uint32_t *)opaque)[offset / 4] = value;
}

static uint64_t debug_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocSystem *s = opaque;
    offset += 0x240;
    switch (offset) {
    case 0x240: return s->debug_unlocked;
    case 0x244: return s->debug_message;
    case 0x248: system_fail(s, "debug event acknowledgment register is write-only");
    case 0x340: return s->debug_enable;
    case 0x348: return s->write_enable;
    case 0x344: case 0x34c: case 0x350: case 0x354: case 0x358:
        return 0;
    }
    if (offset >= 0x280 && offset < 0x28c) { return s->write_high[(offset - 0x280) / 4]; }
    if (offset >= 0x2c0 && offset < 0x2cc) { return s->write_low[(offset - 0x2c0) / 4]; }
    if (offset >= 0x380 && offset < 0x390) {
        unsigned window = (offset - 0x380) / 8;
        return offset & 4 ? s->pc_low[window] : s->pc_high[window];
    }
    system_fail(s, "unsupported debug/guard register read");
}

static void debug_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocSystem *s = opaque;
    offset += 0x240;
    if (offset == 0x240) {
        if (value != 0xe7) { system_fail(s, "invalid debug register unlock key"); }
        s->debug_unlocked = !s->debug_unlocked;
        return;
    }
    if (!s->debug_unlocked) { system_fail(s, "write to locked debug/guard registers"); }
    switch (offset) {
    case 0x248: s->debug_message &= ~(uint32_t)value; return;
    case 0x340:
        if (value & ~0x3f0030ull) { system_fail(s, "unsupported bus guard enable bits"); }
        s->debug_enable = value; return;
    case 0x348:
        if (value & ~7ull) { system_fail(s, "unsupported write guard window mask"); }
        s->write_enable = value;
        fm1_system_sync_guards(s);
        return;
    case 0x344: case 0x34c: case 0x350: case 0x354: case 0x358:
        /* Stock startup disables DBG_CON, core-1 write limits and the
         * peripheral write limits. Only their disabled state is modeled. */
        if (value) { system_fail(s, "unsupported debug/peripheral guard configuration"); }
        return;
    }
    if (offset >= 0x280 && offset < 0x28c) {
        s->write_high[(offset - 0x280) / 4] = value;
        fm1_system_sync_guards(s);
        return;
    }
    if (offset >= 0x2c0 && offset < 0x2cc) {
        s->write_low[(offset - 0x2c0) / 4] = value;
        fm1_system_sync_guards(s);
        return;
    }
    if (offset >= 0x380 && offset < 0x390) {
        unsigned window = (offset - 0x380) / 8;
        if (offset & 4) { s->pc_low[window] = value; }
        else { s->pc_high[window] = value; }
        /* Translated code was checked against the old windows: retire it
         * and leave the current TB chain before the next instruction. */
        s->fetch_epoch++;
        CPUState *cs;
        CPU_FOREACH(cs) {
            cpu_env(cs)->fetch_epoch = s->fetch_epoch;
            cpu_exit(cs);
        }
        return;
    }
    system_fail(s, "unsupported debug/guard register write");
}

static uint64_t emu_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocSystem *s = opaque;
    if (offset == 0) { return s->emu_control; }
    if (offset == 4) { return s->emu_message; }
    if (offset >= 8 && offset < 24) {
        unsigned window = (offset - 8) / 8;
        return offset & 4 ? s->stack_low[window] : s->stack_high[window];
    }
    system_fail(s, "unsupported EMU register read");
}

static void emu_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocSystem *s = opaque;
    if (offset == 0) {
        /* Bit 8 enables ETM watchpoint-0 errors. No watchpoint can be
         * armed by the currently accepted ETM configuration, so keeping
         * this enable cannot create a watchpoint event. */
        if (value & ~0x10cull) { system_fail(s, "unsupported EMU control bits"); }
        s->emu_control = value;
        fm1_system_sync_guards(s);
        fm1_system_check_stack(s);      /* enabling it checks the current SP */
        return;
    }
    if (offset == 4) { s->emu_message &= ~(uint32_t)value; return; }
    if (offset >= 8 && offset < 24) {
        unsigned window = (offset - 8) / 8;
        if (offset & 4) { s->stack_low[window] = value; }
        else { s->stack_high[window] = value; }
        fm1_system_sync_guards(s);
        fm1_system_check_stack(s);
        return;
    }
    system_fail(s, "unsupported EMU register write");
}

/* The board has no running second core and hence no core-1 EMU events.
 * Stock core-0 startup acknowledges both cores' messages. Keep this
 * separate from core 0 so it cannot accidentally clear active guards. */
static uint64_t core1_emu_message_read(void *opaque, hwaddr offset, unsigned size)
{
    return 0;
}

static void core1_emu_message_write(void *opaque, hwaddr offset, uint64_t value,
                                    unsigned size)
{
}

static uint64_t etm_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocSystem *s = opaque;
    if (!offset) { return s->etm_control; }
    if (offset >= 4 && offset < 20) { return s->cpu->env.branch_pc[(offset - 4) / 4]; }
    system_fail(s, "unsupported ETM register read");
}

static void etm_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocSystem *s = opaque;
    if (offset || value & ~1ull) { system_fail(s, "unsupported ETM register write"); }
    s->etm_control = value;
    s->cpu->env.etm_on = value & 1;     /* translated branches record the trace */
}

#define SYSTEM_OPS(name) \
static const MemoryRegionOps name##_ops = { \
    .read = name##_read, .write = name##_write, .endianness = DEVICE_LITTLE_ENDIAN, \
    .valid = {.min_access_size = 4, .max_access_size = 4}, \
    .impl = {.min_access_size = 4, .max_access_size = 4}, \
}
SYSTEM_OPS(p33);
SYSTEM_OPS(reset);
SYSTEM_OPS(cache);
SYSTEM_OPS(debug);
SYSTEM_OPS(emu);
SYSTEM_OPS(core1_emu_message);
SYSTEM_OPS(etm);
SYSTEM_OPS(sdr);
SYSTEM_OPS(psram);
SYSTEM_OPS(stored);
SYSTEM_OPS(sys_div);
SYSTEM_OPS(chip_id);

void fm1_system_init(FM1PocSystem *s, Object *owner, Pi32v2CPU *cpu)
{
    s->cpu = cpu;
    s->sys_div = 0x00010200;
    s->p3_reset_source = 1; /* Cold power-on, rather than a synthetic warm reset. */
    s->p33_control = P33_DIVIDER;   /* as a real FM-1's SPL leaves it (measured) */
    for (unsigned i = 0; i < G_N_ELEMENTS(p33_plain); i++) {
        s->p33_plain[i] = p33_plain[i].handoff;
    }
    s->p33_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, p33_complete, s);
    s->watchdog_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, watchdog_expired, s);
    /* The SPL leaves unconfigured stack windows unrestricted. Measured
     * on the idle second core; stock initializes only the SSP window
     * before enabling guards while still using its startup user stack. */
    s->stack_high[0] = s->stack_high[1] = UINT32_MAX;
#define MAP(region, ops, label, address, length) \
    memory_region_init_io(&s->region, owner, &ops, s, label, length); \
    fm1_sfr_map(address, &s->region)
    MAP(p33_mmio, p33_ops, "fm1.p33", 0x13e08, 8);
    MAP(reset_mmio, reset_ops, "fm1.reset-source", 0x100c0, 4);
    MAP(cache_mmio, cache_ops, "fm1.cache", 0x01eee008, 12);
    MAP(debug_mmio, debug_ops, "fm1.debug-guards", 0x01eee240, 0x150);
    MAP(emu_mmio, emu_ops, "fm1.emu-guards", 0x01eef0d0, 24);
    if (!s->secondary) {
        MAP(core1_emu_message_mmio, core1_emu_message_ops,
            "fm1.core1-emu-message", 0x01eef2d4, 4);
    }
    MAP(etm_mmio, etm_ops, "fm1.branch-trace", 0x01eef1c0, 20);
    MAP(sdr_mmio, sdr_ops, "fm1.sdram-controller", 0x40400, 0x48);
    MAP(psram_mmio, psram_ops, "fm1.psram-controller", 0x40500, 12);
    MAP(sys_div_mmio, sys_div_ops, "fm1.sys-div", 0x10008, 4);
    MAP(chip_id_mmio, chip_id_ops, "fm1.chip-id", 0x10200, 4);
    /* Cache configuration as a real FM-1's SPL leaves it (measured). */
    s->cache_control = 0x302;
    s->cache_way[0] = 0x00ffffff;
    s->cache_way[1] = 0x0000ffff;
    for (unsigned i = 0; i < FM1_STORED_COUNT; i++) {
        memcpy(s->stored[i], stored_blocks[i].handoff, sizeof(s->stored[i]));
        memory_region_init_io(&s->stored_mmio[i], owner, &stored_ops, s->stored[i],
                              stored_blocks[i].name, stored_blocks[i].words * 4);
        fm1_sfr_map(stored_blocks[i].address, &s->stored_mmio[i]);
    }
#undef MAP
    fm1_system_sync_guards(s);
}

void fm1_system_init_core1(FM1PocSystem *s, FM1PocSystem *shared,
                           Object *owner, Pi32v2CPU *cpu)
{
    s->cpu = cpu;
    s->shared = shared;
    shared->secondary = s;
    s->stack_high[0] = s->stack_high[1] = UINT32_MAX;
    memory_region_init_io(&s->emu_mmio, owner, &emu_ops, s, "fm1.core1-emu", 24);
    fm1_sfr_map(0x01eef2d0, &s->emu_mmio);
    memory_region_init_io(&s->etm_mmio, owner, &etm_ops, s, "fm1.core1-etm", 20);
    fm1_sfr_map(0x01eef3c0, &s->etm_mmio);
    fm1_system_sync_guards(s);
}

/* Translated code checks stores and SP writes against these CPU mirrors.
 * Register indexes differ: EMU window 0 is the interrupt stack, while the
 * CPU mirror is indexed by in_irq. */
void fm1_system_sync_guards(FM1PocSystem *s)
{
    CPUPi32v2State *env = &s->cpu->env;
    FM1PocSystem *shared = s->shared ? s->shared : s;
    for (unsigned irq = 0; irq < 2; irq++) {
        unsigned window = irq ? 0 : 1;
        bool on = s->emu_control & 8;
        /* A reversed enabled window keeps low > high and refuses every SP. */
        env->stack_low[irq] = on ? s->stack_low[window] : 0;
        env->stack_high[irq] = on ? s->stack_high[window] : UINT32_MAX;
    }
    env->etm_on = s->etm_control & 1;
    for (unsigned i = 0; i < 3; i++) {
        /* The address windows are shared, but C0_WR_LIMIT_EN and
         * C1_WR_LIMIT_EN select them independently. Core-1 enables are
         * currently supported only in their disabled state. */
        bool on = (s->write_enable & (1u << i)) && shared->write_low[i] <= shared->write_high[i];
        env->write_low[i] = on ? shared->write_low[i] : UINT32_MAX;
        env->write_high[i] = on ? shared->write_high[i] : 0;
    }
    if (s->secondary) { fm1_system_sync_guards(s->secondary); }
}

bool fm1_system_fetch_allowed(FM1PocSystem *s, uint32_t address, unsigned size)
{
    uint64_t last = (uint64_t)address + size - 1;
    bool configured = false, allowed = false;
    s->guard_checks++;
    for (unsigned i = 0; i < 2; i++) {
        if ((s->pc_low[i] || s->pc_high[i]) && s->pc_low[i] <= s->pc_high[i]) {
            configured = true;
            allowed |= address >= s->pc_low[i] && last <= s->pc_high[i];
        }
    }
    return !configured || allowed;
}

/* Out-of-line stack check for SP or window changes outside translated code:
 * interrupt entry and return, and EMU guard register writes. */
void fm1_system_check_stack(FM1PocSystem *s)
{
    s->guard_checks++;
    if (s->emu_control & 8) {
        unsigned window = s->cpu->env.in_irq ? 0 : 1;
        uint32_t sp = s->cpu->env.spr[SP];
        if (s->stack_low[window] > s->stack_high[window] ||
            sp < s->stack_low[window] || sp > s->stack_high[window]) {
            fm1_system_guard_fault(s, PI32V2_GUARD_STACK);
        }
    }
}

void fm1_system_guard_fault(FM1PocSystem *s, unsigned kind)
{
    switch (kind) {
    case PI32V2_GUARD_STACK:
        s->emu_message |= 8;
        system_fail(s, "guest stack pointer lies outside its configured guard window");
    case PI32V2_GUARD_WRITE:
        s->debug_message |= 1u << 13;
        system_fail(s, "CPU write intersects an enabled guest guard window");
    case PI32V2_GUARD_PC:
        s->debug_message |= 1u << 12;
        system_fail(s, "guest PC lies outside both configured guard windows");
    default:
        system_fail(s, "unknown system guard fault");
    }
}
