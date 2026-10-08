/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh idle-receiver subset from WL82 register declarations and saved
 * startup disassembly. There is no external UART input on the modeled board:
 * no received bytes, DMA writes, pending events or receive IRQ are invented.
 * Independent executable observations confirm idle clear/reload readback;
 * they do not establish transmission or reception semantics. Those accesses
 * remain explicit faults until implemented with an actual serial transport. */
#include "qemu/osdep.h"
#include "fm1-uart.h"

static G_NORETURN void uart_fail(FM1PocUART *u, const char *reason)
{
    pi32v2_fail(&u->cpu->env, reason);
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
    case 0: case 4: case 16: case 20: case 28: case 32: case 36:
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
        if (value & ~0x34c1ull) {
            uart_fail(u, "unsupported UART1 control configuration");
        }
        /* Idle pending-clear/reload commands are not persistent config.
         * Only enable and the reached receiver interrupt mask read back. */
        u->registers[0] = value & 0x41;
        return;
    case 4:
        if (value) { uart_fail(u, "unsupported UART1 CON1 configuration"); }
        break;
    case 8:
        if (value > UINT16_MAX) { uart_fail(u, "unsupported UART1 baud width"); }
        break;
    case 16: case 20: case 28: case 32: case 36:
        break;
    case 24:
        if (value) { uart_fail(u, "UART1 transmission is unimplemented"); }
        break;
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

void fm1_uart_init(FM1PocUART *u, Object *owner, Pi32v2CPU *cpu)
{
    u->cpu = cpu;
    memory_region_init_io(&u->mmio, owner, &uart_ops, u, "fm1.uart1", 44);
}
