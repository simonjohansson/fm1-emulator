/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh subset from WL82 declarations, vendor SDK accesses and an FM-1 UART
 * DMA probe. Serial pins are disconnected: TX completion affects registers
 * and IRQ20, while RX remains idle. No transmitted bytes enter USB console
 * output. Abort, restart and active configuration changes are unqualified. */
#include "qemu/osdep.h"
#include "fm1-uart.h"

#define UART_ENABLE 1u
#define UART_TX_IRQ 4u
#define UART_DIV3 0x10u
#define UART_TX_ACK 0x2000u
#define UART_TX_PENDING 0x8000u
#define UART_CLOCK_MASK 0xc00u
#define UART_CLOCK_PLL48 0x400u
#define SRAM_BASE 0x01c00000u
#define SRAM_SIZE 0x80000u

static G_NORETURN void uart_fail(FM1PocUART *u, const char *reason)
{
    pi32v2_fail(&u->cpu->env, reason);
}

static void uart_update_irq(FM1PocUART *u)
{
    /* spec_uart.c's ISR tests CON0 bit 15 and bit 2, acknowledges with
     * bit 13; hwi.h assigns UART1 source 20. The physical probe confirms
     * that the pending bit and raw IRQ remain set until acknowledgment. */
    u->irq_level = u->pending && (u->registers[0] & UART_TX_IRQ);
    u->update_irq(u->opaque);
}

static void transfer_complete(void *opaque)
{
    FM1PocUART *u = opaque;
    u->busy = false;
    u->registers[6] = 0;
    u->pending = true;
    uart_update_irq(u);
}

static void validate_clock_write(void *opaque, uint32_t old, uint32_t value)
{
    FM1PocUART *u = opaque;
    if (u->busy && ((old ^ value) & UART_CLOCK_MASK)) {
        uart_fail(u, "unsupported UART1 active clock change");
    }
}

static uint64_t transfer_duration_ns(FM1PocUART *u, uint32_t count)
{
    /* FM-1 OSC24M TIMER4 measurements, counts 1/3/8: at BAUD383, /4
     * completion took 7881/23628/63004 ticks; /3 took 5960/17869/47643.
     * The /4 BAUD95/191/767 sweep fits this empirical PL48M per-byte
     * period, with 3..7 OSC ticks of total launch/poll observation latency.
     * That guest observation overhead is not part of the device period.
     * Nominal ten-bit frame timing alone does not fit the hardware data. */
    uint64_t coefficient = u->registers[0] & UART_DIV3 ? 31 : 41;
    uint64_t ticks = count * (coefficient * (u->registers[2] + 1) + 6);
    return DIV_ROUND_UP(ticks * 1000000000ull, 48000000ull);
}

static void start_transfer(FM1PocUART *u, uint64_t count)
{
    if (u->busy || u->pending) {
        uart_fail(u, "unsupported UART1 DMA restart");
    }
    if (!count) { u->registers[6] = 0; return; }
    uint32_t source = u->registers[5];
    if (count > UINT16_MAX || source < SRAM_BASE ||
        (uint64_t)source + count > SRAM_BASE + SRAM_SIZE) {
        uart_fail(u, "unsupported UART1 DMA source or count");
    }
    uint32_t baud = u->registers[2];
    bool measured_baud = u->registers[0] & UART_DIV3 ? baud == 383 :
                         baud == 95 || baud == 191 || baud == 383 || baud == 767;
    if (!(u->registers[0] & UART_ENABLE) || u->registers[1] || !measured_baud ||
        (fm1_syscon_get(u->syscon, FM1_SYSCON_CLK_CON1) & UART_CLOCK_MASK) !=
        UART_CLOCK_PLL48) {
        uart_fail(u, "unsupported UART1 DMA mode or clock");
    }
    uint64_t duration = transfer_duration_ns(u, count);
    /* Disconnected TX has no observable payload consumer. Only the bounded
     * SRAM range and measured completion/status transitions are modeled. */
    u->registers[6] = count;
    u->busy = true;
    timer_mod_ns(u->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + duration);
}

static void check_width(FM1PocUART *u, hwaddr offset, unsigned size)
{
    if (offset & 3 || offset > 40 ||
        (offset <= 8 || offset == 24 || offset == 40 ?
         size != 2 && size != 4 : size != 4)) {
        uart_fail(u, "unsupported UART1 register access or width");
    }
}

static uint64_t uart_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocUART *u = opaque;
    check_width(u, offset, size);
    switch (offset) {
    case 0:
        return u->registers[0] | (u->pending ? UART_TX_PENDING : 0);
    case 24:
        if (u->busy) {
            uart_fail(u, "unsupported UART1 active DMA count read");
        }
        return 0;
    case 4: case 16: case 20: case 28: case 32: case 36:
        return u->registers[offset / 4];
    case 40: return 0; /* No bytes have arrived at the external input. */
    default: uart_fail(u, "unsupported UART1 receive or write-only read");
    }
}

static void uart_write(void *opaque, hwaddr offset, uint64_t value,
                        unsigned size)
{
    FM1PocUART *u = opaque;
    check_width(u, offset, size);
    switch (offset) {
    case 0:
        if (value & ~0xb4fdull) {
            uart_fail(u, "unsupported UART1 control configuration");
        }
        if (u->busy && ((value ^ u->registers[0]) &
                        (UART_ENABLE | UART_DIV3))) {
            uart_fail(u, "unsupported UART1 active control change");
        }
        if (u->busy && (value & UART_TX_ACK)) {
            uart_fail(u, "unsupported UART1 active acknowledgment");
        }
        /* ACK/reload are commands; pending is read-only but may be echoed
         * by the SDK's CON0 |= TXACK. Independent RX remains idle. */
        if (value & UART_TX_ACK) { u->pending = false; }
        u->registers[0] = value & 0x7d;
        uart_update_irq(u);
        return;
    case 4:
        if (value) { uart_fail(u, "unsupported UART1 CON1 configuration"); }
        break;
    case 8:
        if (value > UINT16_MAX) { uart_fail(u, "unsupported UART1 baud width"); }
        if (u->busy && value != u->registers[2]) {
            uart_fail(u, "unsupported UART1 active baud change");
        }
        break;
    case 20:
        if (u->busy && value != u->registers[5]) {
            uart_fail(u, "unsupported UART1 active DMA source change");
        }
        break;
    case 16: case 28: case 32: case 36:
        break;
    case 24:
        start_transfer(u, value);
        return;
    default: uart_fail(u, "unsupported UART1 transmit or read-only write");
    }
    u->registers[offset / 4] = value;
}

static const MemoryRegionOps uart_ops = {
    .read = uart_read, .write = uart_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 2, .max_access_size = 4},
    .impl = {.min_access_size = 2, .max_access_size = 4},
};

void fm1_uart_init(FM1PocUART *u, Object *owner, Pi32v2CPU *cpu,
                    FM1PocSyscon *syscon, void (*update_irq)(void *opaque),
                    void *opaque)
{
    u->cpu = cpu;
    u->syscon = syscon;
    u->update_irq = update_irq;
    u->opaque = opaque;
    u->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, transfer_complete, u);
    fm1_syscon_set_validator(syscon, FM1_SYSCON_CLK_CON1,
                              validate_clock_write, u);
    memory_region_init_io(&u->mmio, owner, &uart_ops, u, "fm1.uart1", 44);
}
