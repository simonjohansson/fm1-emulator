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

#define P33_CS 1u
#define P33_BUSY 2u
#define P33_START 16u
#define WDT_ENABLE 16u
#define WDT_CLEAR 64u

static G_NORETURN void system_fail(FM1PocSystem *s, const char *reason)
{
    pi32v2_fail(&s->cpu->env, reason);
}

static uint8_t register_read(FM1PocSystem *s, uint16_t address)
{
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
    switch (address) {
    case 0x17:
        if (value & 0x40) {
            system_fail(s, "P33 watchdog exception mode is unsupported");
        }
        s->valid_keep = value;
        break;
    case 0x80: {
        uint8_t previous = s->watchdog_control;
        if (value & ~(WDT_CLEAR | WDT_ENABLE | 15u)) {
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
        s->address = (byte & 3u) << 8;
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
    if (offset != 0 || value & ~(P33_CS | P33_BUSY | P33_START)) {
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
    s->p33_control = value & P33_CS;
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

static uint64_t cache_read(void *opaque, hwaddr offset, unsigned size)
{
    /* This machine has no asynchronous cache fill/flush queue: QEMU's memory
     * access and translated-code invalidation are synchronous. CACHE_CON's
     * idle flag therefore describes the model's actual idle state. No other
     * cache control/status semantics are claimed. */
    return 0x4000;
}

static void cache_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    system_fail(opaque, "cache control writes are unsupported");
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
        s->write_enable = value; return;
    }
    if (offset >= 0x280 && offset < 0x28c) { s->write_high[(offset - 0x280) / 4] = value; return; }
    if (offset >= 0x2c0 && offset < 0x2cc) { s->write_low[(offset - 0x2c0) / 4] = value; return; }
    if (offset >= 0x380 && offset < 0x390) {
        unsigned window = (offset - 0x380) / 8;
        if (offset & 4) { s->pc_low[window] = value; }
        else { s->pc_high[window] = value; }
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
        if (value & ~12ull) { system_fail(s, "unsupported EMU control bits"); }
        s->emu_control = value;
        return;
    }
    if (offset == 4) { s->emu_message &= ~(uint32_t)value; return; }
    if (offset >= 8 && offset < 24) {
        unsigned window = (offset - 8) / 8;
        if (offset & 4) { s->stack_low[window] = value; }
        else { s->stack_high[window] = value; }
        return;
    }
    system_fail(s, "unsupported EMU register write");
}

static uint64_t etm_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocSystem *s = opaque;
    if (!offset) { return s->etm_control; }
    if (offset >= 4 && offset < 20) { return s->branch_pc[(offset - 4) / 4]; }
    system_fail(s, "unsupported ETM register read");
}

static void etm_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocSystem *s = opaque;
    if (offset || value & ~1ull) { system_fail(s, "unsupported ETM register write"); }
    s->etm_control = value;
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
SYSTEM_OPS(etm);

void fm1_system_init(FM1PocSystem *s, Object *owner, Pi32v2CPU *cpu)
{
    s->cpu = cpu;
    s->p3_reset_source = 1; /* Cold power-on, rather than a synthetic warm reset. */
    s->p33_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, p33_complete, s);
    s->watchdog_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, watchdog_expired, s);
#define MAP(region, ops, label, address, length) \
    memory_region_init_io(&s->region, owner, &ops, s, label, length); \
    memory_region_add_subregion(get_system_memory(), address, &s->region)
    MAP(p33_mmio, p33_ops, "fm1.p33", 0x13e08, 8);
    MAP(reset_mmio, reset_ops, "fm1.reset-source", 0x100c0, 4);
    MAP(cache_mmio, cache_ops, "fm1.cache-idle", 0x01eee008, 4);
    MAP(debug_mmio, debug_ops, "fm1.debug-guards", 0x01eee240, 0x150);
    MAP(emu_mmio, emu_ops, "fm1.emu-guards", 0x01eef0d0, 24);
    MAP(etm_mmio, etm_ops, "fm1.branch-trace", 0x01eef1c0, 20);
#undef MAP
}

void fm1_system_check_access(FM1PocSystem *s, uint32_t address,
                             unsigned size, bool write, bool fetch)
{
    if (!size || (uint64_t)address + size > 1ull << 32) {
        system_fail(s, "invalid guarded address range");
    }
    s->guard_checks++;
    uint64_t last = (uint64_t)address + size - 1;
    if (write) {
        for (unsigned i = 0; i < 3; i++) {
            if ((s->write_enable & (1u << i)) &&
                s->write_low[i] <= s->write_high[i] &&
                address <= s->write_high[i] && last >= s->write_low[i]) {
                s->debug_message |= 1u << 13;
                system_fail(s, "CPU write intersects an enabled guest guard window");
            }
        }
    }
    if (fetch) {
        bool configured = false, allowed = false;
        for (unsigned i = 0; i < 2; i++) {
            if ((s->pc_low[i] || s->pc_high[i]) && s->pc_low[i] <= s->pc_high[i]) {
                configured = true;
                allowed |= address >= s->pc_low[i] && last <= s->pc_high[i];
            }
        }
        if (configured && !allowed) {
            s->debug_message |= 1u << 12;
            system_fail(s, "guest PC lies outside both configured guard windows");
        }
    }
}

void fm1_system_check_stack(FM1PocSystem *s)
{
    if (s->emu_control & 8) {
        unsigned window = s->cpu->env.in_irq ? 0 : 1;
        uint32_t sp = s->cpu->env.spr[SP];
        if (s->stack_low[window] > s->stack_high[window] ||
            sp < s->stack_low[window] || sp > s->stack_high[window]) {
            s->emu_message |= 8;
            system_fail(s, "guest stack pointer lies outside its configured guard window");
        }
    }
}

void fm1_system_note_branch(FM1PocSystem *s, uint32_t from)
{
    if (s->etm_control & 1) {
        for (unsigned i = 3; i > 0; i--) { s->branch_pc[i] = s->branch_pc[i - 1]; }
        s->branch_pc[0] = from;
        s->branches++;
    }
}
