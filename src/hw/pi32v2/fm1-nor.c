/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh implementation from the saved diagnostic's SPI0 byte protocol and
 * register accesses. No Rust peripheral implementation or guest driver is
 * copied. The application enters after an explicitly unencrypted handoff;
 * SFC initially reads NOR offset 0x4000 at XIP address 0x02000000.
 * SPI bytes take eight clocks at a fixed functional 12 MHz. This is not a
 * calibrated peripheral divider or encrypted package boot model. */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "system/address-spaces.h"
#include "exec/translation-block.h"
#include "fm1-nor.h"
#include "fm1-image.h"
#include "fm1-sfr.h"

#define SPI0_BASE 0x11c00u
#define SFC_BASE 0x40200u
#define ENCRYPTION_BASE 0x40300u
#define FLASH_CS 1u
#define SFC_ROUTE 0x20u
#define SPI_MODE 0x21u
#define SPI_SHIFT 0x08u
#define SPI_RECEIVE 0x1000u
#define SPI_CLOCK_DRIVE 0x2000u
#define SPI_ACK 0x4000u
#define SPI_PENDING 0x8000u
#define SPI_CLOCK_HZ 12000000ull

static G_NORETURN void nor_fail(FM1PocNOR *nor, const char *reason)
{
    CPUPi32v2State *env = nor->completing_transfer ? &nor->transfer_cpu->env :
                          current_cpu ? cpu_env(current_cpu) : &nor->cpu->env;
    pi32v2_fail(env, reason);
}

bool fm1_nor_xip_enabled(const FM1PocNOR *nor)
{
    return nor->sfc_control != 0 && (nor->iomap_con0 & SFC_ROUTE);
}

int fm1_nor_fetch_fault(FM1PocNOR *nor, uint32_t address, unsigned size)
{
    if (address < FM1_NOR_XIP_BASE || address >= 0x02100000u) {
        return -1;
    }
    if ((uint64_t)address + size > FM1_NOR_XIP_BASE + FM1_NOR_XIP_SIZE) {
        return PI32V2_GUARD_XIP_BOUNDS;
    }
    return nor->cpu->env.xip_fetch ? -1 : PI32V2_GUARD_XIP_DISABLED;
}

void fm1_nor_guard_fault(FM1PocNOR *nor, unsigned kind)
{
    nor_fail(nor, kind == PI32V2_GUARD_XIP_BOUNDS ?
             "XIP access exceeds mapped NOR storage" :
             "XIP access while SFC is disabled or unrouted");
}

/* XIP reads are direct ROM reads while SFC is routed (ROMD mode); otherwise
 * they reach xip_read and fault. Translated code is keyed by the CPU's
 * xip_fetch flag, so leave the TB chain before the next instruction. */
static void update_xip(FM1PocNOR *nor)
{
    bool on = fm1_nor_xip_enabled(nor);
    if (on != nor->cpu->env.xip_fetch) {
        memory_region_rom_device_set_romd(&nor->xip, on);
    }
    CPUState *cs;
    CPU_FOREACH(cs) {
        if (cpu_env(cs)->xip_fetch != on) {
            cpu_env(cs)->xip_fetch = on;
            pi32v2_leave_chain(cs);
        }
    }
}

static uint64_t xip_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocNOR *nor = opaque;
    pi32v2_guard_fault(current_cpu ? cpu_env(current_cpu) : &nor->cpu->env,
                       PI32V2_GUARD_XIP_DISABLED,
                       FM1_NOR_XIP_BASE + offset, size);
}

static void xip_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    nor_fail(opaque, "write to read-only XIP (NOR)");
}

static const MemoryRegionOps xip_ops = {
    .read = xip_read, .write = xip_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 1, .max_access_size = 4},
    .impl = {.min_access_size = 1, .max_access_size = 4},
};

static void command_start(FM1PocNOR *nor, uint8_t command)
{
    nor->command = command;
    nor->phase = 0;
    nor->address = 0;
    /* Only status reads are meaningful during a self-timed write. Other
     * commands are ignored by the flash until the operation completes. */
    nor->ignore_command = nor->write_busy && command != 0x05 && command != 0x35;
    if (nor->ignore_command) { return; }
    switch (command) {
    case 0x9f: nor->jedec_commands++; break;
    case 0x05: case 0x35: nor->status_commands++; break;
    case 0x03: case 0x0b: nor->read_commands++; break;
    case 0x02:
        memset(nor->page_buffer, 0xff, sizeof(nor->page_buffer));
        nor->program_data = false;
        break;
    case 0x04: case 0x06: case 0x20: case 0x4b: break;
    default: {
        g_autofree char *reason = g_strdup_printf("unsupported NOR command 0x%02x", command);
        nor_fail(nor, reason);
    }
    }
}

static uint8_t transfer_byte(FM1PocNOR *nor)
{
    if (!nor->transfer_receive) {
        if (!nor->command) {
            command_start(nor, nor->transfer_byte);
        } else if (nor->ignore_command) {
            return 0xff;
        } else if ((nor->command == 0x03 || nor->command == 0x0b ||
                    nor->command == 0x02 || nor->command == 0x20) && nor->phase < 3) {
            nor->address = (nor->address << 8) | nor->transfer_byte;
            nor->phase++;
            if (nor->phase == 3) {
                nor->address &= FM1_NOR_SIZE - 1;
                nor->program_base = nor->address & ~255u;
            }
        } else if (nor->command == 0x0b && nor->phase == 3) {
            /* Fast read has one dummy byte after the 24-bit address. */
            nor->phase++;
        } else if (nor->command == 0x4b && nor->phase < 4) {
            /* P25Q80H unique-ID read: four dummy bytes precede the ID. */
            nor->phase++;
        } else if (nor->command == 0x02 && nor->phase == 3) {
            /* Page wrap retains only the last byte supplied for each slot;
             * programming subsequently clears bits, never sets them. */
            nor->page_buffer[nor->address++ & 255] = nor->transfer_byte;
            nor->program_data = true;
        } else {
            nor_fail(nor, "unexpected NOR transmit byte");
        }
        return 0xff; /* NOR does not drive useful data during command bytes. */
    }
    nor->received_bytes++;
    if (nor->ignore_command) { return 0xff; }
    switch (nor->command) {
    case 0x9f: {
        static const uint8_t id[] = {0x85, 0x60, 0x14};
        if (nor->phase >= ARRAY_SIZE(id)) {
            nor_fail(nor, "JEDEC transfer exceeds supported three-byte ID");
        }
        return id[nor->phase++];
    }
    case 0x05:
        nor->phase = 1;
        return (nor->write_busy ? 1 : 0) | (nor->write_enabled ? 2 : 0);
    case 0x35:
        nor->phase = 1;
        return 0; /* No modeled SR2 configuration bits. */
    case 0x4b: {
        /* The 128-bit unique ID read twice identically from a real FM-1. */
        static const uint8_t uid[] = {0x41, 0x50, 0x35, 0x44, 0x33, 0x33, 0x36, 0x0f,
                                      0x00, 0x36, 0xea, 0x5c, 0x07, 0x20, 0xdc, 0x78};
        if (nor->phase < 4 || nor->phase >= 4 + ARRAY_SIZE(uid)) {
            nor_fail(nor, "NOR unique-ID read outside its four dummy and 16 ID bytes");
        }
        return uid[nor->phase++ - 4];
    }
    case 0x03: case 0x0b:
        /* Read Data has no dummy byte; fast read has one. */
        if (nor->phase != (nor->command == 0x03 ? 3 : 4)) {
            nor_fail(nor, "NOR read before address/dummy completion");
        }
        nor->read_bytes++;
        /* This 1 MiB part ignores the upper bits of the 24-bit address. */
        return nor->bytes[nor->address++ & (FM1_NOR_SIZE - 1)];
    default: nor_fail(nor, "NOR receive without a supported command"); return 0;
    }
}

static void write_complete(void *opaque)
{
    FM1PocNOR *nor = opaque;
    unsigned length = nor->write_command == 0x02 ? 256 : 4096;
    uint32_t start = nor->write_address;
    if (nor->write_command == 0x02) {
        for (unsigned i = 0; i < length; i++) {
            nor->bytes[start + i] &= nor->page_buffer[i];
        }
    } else {
        memset(nor->bytes + start, 0xff, length);
    }
    /* Publish to the XIP ROM device's storage and invalidate translated
     * code. This also holds while SFC is disabled (MMIO mode), where the
     * address-space ROM-write path would skip the device. */
    uint32_t mapped = MAX(start, FM1_NOR_XIP_OFFSET);
    if (mapped < start + length) {
        hwaddr offset = mapped - FM1_NOR_XIP_OFFSET;
        hwaddr bytes = start + length - mapped;
        ram_addr_t ram = memory_region_get_ram_addr(&nor->xip) + offset;
        memcpy((uint8_t *)memory_region_get_ram_ptr(&nor->xip) + offset,
               nor->bytes + mapped, bytes);
        /* As memory_region_flush_rom_device, which requires ROMD mode. */
        tb_invalidate_phys_range(NULL, ram, ram + bytes - 1);
        memory_region_set_dirty(&nor->xip, offset, bytes);
    }
    nor->write_busy = nor->write_enabled = false;
}

static void command_end(FM1PocNOR *nor)
{
    if (nor->ignore_command) { return; }
    if (nor->command == 0x06) { nor->write_enabled = true; }
    else if (nor->command == 0x04) { nor->write_enabled = false; }
    else if (nor->write_enabled && nor->phase == 3 &&
             (nor->command == 0x20 ||
              (nor->command == 0x02 && nor->program_data))) {
        nor->write_command = nor->command;
        nor->write_address = nor->command == 0x20 ?
                             nor->address & ~4095u : nor->program_base;
        nor->write_busy = true;
        /* P25Q80H typical times, used as bounded functional timing rather
         * than a calibrated physical part: PP 2 ms, sector erase 8 ms. */
        timer_mod_ns(nor->write_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                     (nor->command == 0x20 ? 8000000 : 2000000));
    }
}

static void transfer_complete(void *opaque)
{
    FM1PocNOR *nor = opaque;
    nor->completing_transfer = true;
    nor->buffer = transfer_byte(nor);
    nor->completing_transfer = false;
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
    /* A timer callback may run after the scheduler selected another CPU.
     * Attribute its fault to the guest that submitted this byte. */
    nor->transfer_cpu = current_cpu ? PI32V2_CPU(current_cpu) : nor->cpu;
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
        if (value & ~(uint64_t)(SPI_MODE | SPI_SHIFT | SPI_RECEIVE | SPI_CLOCK_DRIVE |
                               SPI_ACK | SPI_PENDING) ||
            ((control & ~SPI_CLOCK_DRIVE) && (control & SPI_MODE) != SPI_MODE)) {
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
    /* BAUD is write-only in the SDK, yet stock FM-1 firmware reads it to
     * derive the flash clock. A real FM-1 reads 1 after its SPL (measured). */
    if (offset == 4 && size == 4) { return 1; }
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
    update_xip(nor);
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
        if (!nor->ignore_command && ((nor->command == 0x9f && nor->phase != 3) ||
            ((nor->command == 0x05 || nor->command == 0x35) && !nor->phase) ||
            (nor->command == 0x0b && nor->phase != 4))) {
            nor_fail(nor, "NOR CS released before a complete supported command");
        }
        command_end(nor);
    }
    nor->selected = selected;
    nor->pd_out = pd_out;
    nor->iomap_con0 = iomap_con0;
    if (nor->cpu) {
        update_xip(nor);
    }
}

void fm1_nor_init(FM1PocNOR *nor, Object *owner, Pi32v2CPU *cpu,
                  const char *raw_path)
{
    g_autofree gchar *raw = NULL;
    gsize length = 0;
    char image_error[256];
    FM1ImageHandoff handoff;
    const char *extension = strrchr(raw_path, '.');
    bool packaged = extension && (!g_ascii_strcasecmp(extension, ".fwsc") ||
                                  !g_ascii_strcasecmp(extension, ".ufw"));
    nor->cpu = cpu;
    if (!g_file_get_contents(raw_path, &raw, &length, NULL)) {
        nor_fail(nor, "cannot read firmware image");
    }
    nor->bytes = g_malloc(FM1_NOR_SIZE);
    if (!fm1_image_decode_handoff((const uint8_t *)raw, length, packaged,
                                  nor->bytes, FM1_NOR_SIZE, &handoff,
                                  image_error, sizeof(image_error))) {
        error_report("cannot load application image: %s", image_error);
        nor_fail(nor, "invalid or unsupported application image");
    }
    if (handoff.available) {
        /* The generic package handoff carries a pointer to its decoded flash
         * header and the parsed SFC key. The saved stock app consumes header
         * fields +8/+13 through param[0], and the key through param+12.
         * Param+8 supplies the first application-area directory. The
         * decoder validates this area at the board's fixed XIP origin.
         * Reserved storage, calibration and MAC remain zero/unverified; this
         * does not implement the SPL's complete hardware/ROM boot contract.
         * Raw applications retain the previous zeroed SRAM handoff exactly. */
        static const uint8_t header_pointer[4] = {0x40, 0xfe, 0xc7, 0x01};
        static const uint8_t directory_pointer[4] = {0x00, 0x00, 0x00, 0x02};
        uint8_t key[2] = {handoff.chip_key, handoff.chip_key >> 8};
        if (address_space_write(&address_space_memory, 0x01c7fe40,
                                MEMTXATTRS_UNSPECIFIED, handoff.flash_header,
                                sizeof(handoff.flash_header)) != MEMTX_OK ||
            address_space_write(&address_space_memory, 0x01c7fe08,
                                MEMTXATTRS_UNSPECIFIED, header_pointer,
                                sizeof(header_pointer)) != MEMTX_OK ||
            address_space_write(&address_space_memory, 0x01c7fe10,
                                MEMTXATTRS_UNSPECIFIED, directory_pointer,
                                sizeof(directory_pointer)) != MEMTX_OK ||
            address_space_write(&address_space_memory, 0x01c7fe14,
                                MEMTXATTRS_UNSPECIFIED, key,
                                sizeof(key)) != MEMTX_OK) {
            nor_fail(nor, "cannot initialize package boot metadata in SRAM");
        }
    }
    nor->sfc_control = 1;
    nor->pd_out = FLASH_CS;
    nor->iomap_con0 = SFC_ROUTE;
    nor->transfer_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, transfer_complete, nor);
    nor->write_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, write_complete, nor);
    memory_region_init_io(&nor->spi_mmio, owner, &spi_ops, nor, "fm1.spi0", 20);
    fm1_sfr_map(SPI0_BASE, &nor->spi_mmio);
    memory_region_init_io(&nor->sfc_mmio, owner, &sfc_ops, nor, "fm1.sfc", 8);
    fm1_sfr_map(SFC_BASE, &nor->sfc_mmio);
    memory_region_init_io(&nor->encryption_mmio, owner, &encryption_ops, nor,
                          "fm1.sfcenc", 16);
    fm1_sfr_map(ENCRYPTION_BASE, &nor->encryption_mmio);
    /* Read-only to CPU stores (fill_tlb); completed SPI writes update the
     * device storage directly. ROMD mode follows the SFC routing. */
    memory_region_init_rom_device(&nor->xip, NULL, &xip_ops, nor, "fm1.diag-xip",
                                  FM1_NOR_XIP_SIZE, &error_fatal);
    memcpy(memory_region_get_ram_ptr(&nor->xip), nor->bytes + FM1_NOR_XIP_OFFSET,
            FM1_NOR_XIP_SIZE);
    memory_region_add_subregion(get_system_memory(), FM1_NOR_XIP_BASE, &nor->xip);
    nor->cpu->env.xip_fetch = true;     /* ROMD is the device's initial mode */
    update_xip(nor);
}
