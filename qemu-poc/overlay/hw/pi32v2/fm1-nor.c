/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh implementation from the saved diagnostic's SPI0 byte protocol and
 * register accesses. No Rust peripheral implementation or guest driver is
 * copied. The application enters after an explicitly unencrypted handoff;
 * SFC initially reads NOR offset 0x4000 at XIP address 0x02000000.
 * SPI bytes take eight clocks at a fixed functional 12 MHz. This is not a
 * calibrated peripheral divider or encrypted package boot model. */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "exec/address-spaces.h"
#include "fm1-nor.h"

#define SPI0_BASE 0x11c00u
#define SFC_BASE 0x40200u
#define ENCRYPTION_BASE 0x40300u
#define FLASH_CS 1u
#define SFC_ROUTE 0x20u
#define SPI_MODE 0x21u
#define SPI_SHIFT 0x08u
#define SPI_RECEIVE 0x1000u
#define SPI_ACK 0x4000u
#define SPI_PENDING 0x8000u
#define SPI_CLOCK_HZ 12000000ull

static G_NORETURN void nor_fail(FM1PocNOR *nor, const char *reason)
{
    pi32v2_fail(&nor->cpu->env, reason);
}

bool fm1_nor_xip_enabled(const FM1PocNOR *nor)
{
    return nor->sfc_control != 0 && (nor->iomap_con0 & SFC_ROUTE);
}

void fm1_nor_check_access(FM1PocNOR *nor, uint32_t address,
                          unsigned size, bool write)
{
    if (address < FM1_NOR_XIP_BASE || address >= 0x02100000u) {
        return;
    }
    if (write) { nor_fail(nor, "write to read-only NOR XIP"); }
    if (!size || (uint64_t)address + size >
                 FM1_NOR_XIP_BASE + FM1_NOR_XIP_SIZE) {
        nor_fail(nor, "XIP access exceeds mapped NOR storage");
    }
    if (!fm1_nor_xip_enabled(nor)) {
        nor_fail(nor, "XIP access while SFC is disabled or unrouted");
    }
}

static void command_start(FM1PocNOR *nor, uint8_t command)
{
    nor->command = command;
    nor->phase = 0;
    nor->address = 0;
    switch (command) {
    case 0x9f: nor->jedec_commands++; break;
    case 0x05: case 0x35: nor->status_commands++; break;
    case 0x0b: nor->read_commands++; break;
    case 0x02: case 0x06: case 0x20:
        nor_fail(nor, "NOR program/erase/write-enable is unimplemented");
        break;
    default: nor_fail(nor, "unsupported NOR command");
    }
}

static uint8_t transfer_byte(FM1PocNOR *nor)
{
    if (!nor->transfer_receive) {
        if (!nor->command) {
            command_start(nor, nor->transfer_byte);
        } else if (nor->command == 0x0b && nor->phase < 3) {
            nor->address = (nor->address << 8) | nor->transfer_byte;
            nor->phase++;
        } else if (nor->command == 0x0b && nor->phase == 3) {
            /* Fast read has one dummy byte after the 24-bit address. */
            nor->phase++;
        } else {
            nor_fail(nor, "unexpected NOR transmit byte");
        }
        return 0xff; /* NOR does not drive useful data during command bytes. */
    }
    nor->received_bytes++;
    switch (nor->command) {
    case 0x9f: {
        static const uint8_t id[] = {0x85, 0x60, 0x14};
        if (nor->phase >= ARRAY_SIZE(id)) {
            nor_fail(nor, "JEDEC transfer exceeds supported three-byte ID");
        }
        return id[nor->phase++];
    }
    case 0x05: case 0x35:
        nor->phase = 1;
        return 0; /* Erased, idle device: WIP=0, WEL=0, SR2=0. */
    case 0x0b:
        if (nor->phase != 4) {
            nor_fail(nor, "NOR fast read before address/dummy completion");
        }
        nor->read_bytes++;
        /* This 1 MiB part ignores the upper bits of the 24-bit address. */
        return nor->bytes[nor->address++ & (FM1_NOR_SIZE - 1)];
    default: nor_fail(nor, "NOR receive without a supported command"); return 0;
    }
}

static void transfer_complete(void *opaque)
{
    FM1PocNOR *nor = opaque;
    nor->buffer = transfer_byte(nor);
    nor->busy = false;
    nor->pending = true;
    nor->completed_transfers++;
}

static void transfer_start(FM1PocNOR *nor, uint8_t byte)
{
    if (nor->busy) { nor_fail(nor, "overlapping SPI0 byte transfer"); }
    if ((nor->control & (SPI_MODE | SPI_SHIFT)) !=
        (SPI_MODE | SPI_SHIFT) || !nor->selected ||
        (nor->iomap_con0 & SFC_ROUTE) || nor->sfc_control) {
        nor_fail(nor, "SPI0 transfer needs disabled SFC, SPI routing and asserted PD0 CS");
    }
    nor->transfer_byte = byte;
    nor->transfer_receive = (nor->control & SPI_RECEIVE) != 0;
    if (nor->transfer_receive && byte != 0xff) {
        nor_fail(nor, "NOR receive transfer requires the guest dummy byte");
    }
    nor->busy = true;
    nor->pending = false;
    nor->transfers++;
    timer_mod_ns(nor->transfer_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                 DIV_ROUND_UP(8 * 1000000000ull, SPI_CLOCK_HZ));
}

static uint64_t spi_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocNOR *nor = opaque;
    switch (offset) {
    case 0: return nor->control | (nor->pending ? SPI_PENDING : 0);
    case 8:
        if (nor->busy) { nor_fail(nor, "SPI0 result read before completion"); }
        return nor->buffer;
    default: nor_fail(nor, "unsupported SPI0 register read"); return 0;
    }
}

static void spi_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocNOR *nor = opaque;
    switch (offset) {
    case 0: {
        uint32_t control = value & ~(SPI_ACK | SPI_PENDING);
        if (value & ~(uint64_t)(SPI_MODE | SPI_SHIFT | SPI_RECEIVE |
                               SPI_ACK | SPI_PENDING) ||
            (control && (control & SPI_MODE) != SPI_MODE)) {
            nor_fail(nor, "unsupported SPI0 control mode");
        }
        if (nor->busy && control != nor->control) {
            nor_fail(nor, "SPI0 control changed during a byte transfer");
        }
        nor->control = control;
        if (value & SPI_ACK) {
            if (nor->pending) { nor->acknowledgments++; }
            nor->pending = false;
        }
        break;
    }
    case 8:
        if (value > UINT8_MAX) { nor_fail(nor, "SPI0 byte exceeds eight bits"); }
        transfer_start(nor, value);
        break;
    default: nor_fail(nor, "SPI0 DMA and unsupported register writes are unimplemented");
    }
}

static uint64_t sfc_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocNOR *nor = opaque;
    if (offset || size != 4) { nor_fail(nor, "unsupported SFC register read"); }
    return nor->sfc_control;
}

static void sfc_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocNOR *nor = opaque;
    if (offset || size != 4) { nor_fail(nor, "unsupported SFC register write"); }
    if (value != 0 && value != 1) {
        nor_fail(nor, "unsupported SFC handoff control mode");
    }
    if (nor->busy || nor->selected) {
        nor_fail(nor, "SFC mode changed during an active SPI0 transaction");
    }
    if (nor->sfc_control && !value) { nor->sfc_disables++; }
    if (!nor->sfc_control && value) {
        if (!(nor->iomap_con0 & SFC_ROUTE)) {
            nor_fail(nor, "SFC enabled before restoring flash pin routing");
        }
        nor->sfc_restores++;
    }
    nor->sfc_control = value;
}

static uint64_t encryption_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocNOR *nor = opaque;
    if (offset == 0 && size == 1) { return nor->encryption_control; }
    nor_fail(nor, "unsupported SFCENC register read");
    return 0;
}

static void encryption_write(void *opaque, hwaddr offset, uint64_t value,
                             unsigned size)
{
    FM1PocNOR *nor = opaque;
    if (offset == 0 && size == 1) {
        if (value & ~2ull) { nor_fail(nor, "SFC decryption is unimplemented"); }
        if ((value & 2) && nor->plain_low > nor->plain_high) {
            nor_fail(nor, "SFC plain window has reversed bounds");
        }
        nor->encryption_control = value;
    } else if (offset == 8 && size == 4) {
        if (nor->encryption_control & 2) {
            nor_fail(nor, "SFC plain window changed while enabled");
        }
        nor->plain_high = value;
    } else if (offset == 12 && size == 4) {
        if (nor->encryption_control & 2) {
            nor_fail(nor, "SFC plain window changed while enabled");
        }
        nor->plain_low = value;
    } else {
        nor_fail(nor, "unsupported SFCENC register write");
    }
}

static const MemoryRegionOps spi_ops = {
    .read = spi_read, .write = spi_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static const MemoryRegionOps sfc_ops = {
    .read = sfc_read, .write = sfc_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};
static const MemoryRegionOps encryption_ops = {
    .read = encryption_read, .write = encryption_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 1, .max_access_size = 4},
    .impl = {.min_access_size = 1, .max_access_size = 4},
};

void fm1_nor_set_pins(FM1PocNOR *nor, uint32_t pd_out, uint32_t iomap_con0)
{
    bool selected = !(pd_out & FLASH_CS);
    if (nor->busy && (((pd_out ^ nor->pd_out) & FLASH_CS) ||
                     ((iomap_con0 ^ nor->iomap_con0) & SFC_ROUTE))) {
        nor_fail(nor, "SPI0 flash CS or routing changed during a byte transfer");
    }
    if (!nor->selected && selected) {
        nor->command = nor->phase = 0;
        nor->address = 0;
        nor->transactions++;
    }
    if (nor->selected && !selected && nor->command) {
        if ((nor->command == 0x9f && nor->phase != 3) ||
            ((nor->command == 0x05 || nor->command == 0x35) && !nor->phase) ||
            (nor->command == 0x0b && nor->phase != 4)) {
            nor_fail(nor, "NOR CS released before a complete supported command");
        }
    }
    nor->selected = selected;
    nor->pd_out = pd_out;
    nor->iomap_con0 = iomap_con0;
}

void fm1_nor_init(FM1PocNOR *nor, Object *owner, Pi32v2CPU *cpu,
                  const char *raw_path)
{
    g_autofree gchar *raw = NULL;
    gsize length = 0;
    nor->cpu = cpu;
    if (!g_file_get_contents(raw_path, &raw, &length, NULL) || !length ||
        length > FM1_NOR_SIZE - 0x4120u) {
        nor_fail(nor, "cannot load diagnostic raw application into NOR");
    }
    nor->bytes = g_malloc(FM1_NOR_SIZE);
    memset(nor->bytes, 0xff, FM1_NOR_SIZE);
    memcpy(nor->bytes + 0x4120u, raw, length);
    nor->sfc_control = 1;
    nor->pd_out = FLASH_CS;
    nor->iomap_con0 = SFC_ROUTE;
    nor->transfer_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, transfer_complete, nor);
    memory_region_init_io(&nor->spi_mmio, owner, &spi_ops, nor, "fm1.spi0", 20);
    memory_region_add_subregion(get_system_memory(), SPI0_BASE, &nor->spi_mmio);
    memory_region_init_io(&nor->sfc_mmio, owner, &sfc_ops, nor, "fm1.sfc", 4);
    memory_region_add_subregion(get_system_memory(), SFC_BASE, &nor->sfc_mmio);
    memory_region_init_io(&nor->encryption_mmio, owner, &encryption_ops, nor,
                          "fm1.sfcenc", 16);
    memory_region_add_subregion(get_system_memory(), ENCRYPTION_BASE,
                                &nor->encryption_mmio);
    /* Immutable for this stage; unsupported program/erase never changes it. */
    memory_region_init_rom(&nor->xip, NULL, "fm1.diag-xip", FM1_NOR_XIP_SIZE,
                           &error_fatal);
    memcpy(memory_region_get_ram_ptr(&nor->xip), nor->bytes + FM1_NOR_XIP_OFFSET,
            FM1_NOR_XIP_SIZE);
    memory_region_add_subregion(get_system_memory(), FM1_NOR_XIP_BASE, &nor->xip);
}
