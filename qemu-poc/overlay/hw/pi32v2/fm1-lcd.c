/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh implementation from the tracked guest's wire protocol and register
 * accesses, with QEMU's MemoryRegion and virtual-clock interfaces. No Rust
 * decoder, peripheral implementation or guest drawing routine is copied.
 *
 * Scope: SPI1 master, BAUD 4 with a 60 MHz peripheral clock, PC7/PC8 CS/D/C,
 * SRAM DMA, and the ST7789 commands used by the 2700-byte display fixture.
 * Timing covers transfer duration, not bus arbitration or panel reset delays.
 */
#include "qemu/osdep.h"
#include "fm1-lcd.h"
#include "exec/address-spaces.h"

#define SPI1_BASE 0x11d00
#define SRAM_BASE 0x01c00000
#define SRAM_SIZE 0x80000
#define LCD_CS (1u << 7)
#define LCD_DC (1u << 8)
#define LCD_BACKLIGHT (1u << 2)
#define SPI_COMPLETE (1u << 15)
#define SPI_ACK (1u << 14)
#define SPI_MODE 0x21u
#define SPI_CLOCK_HZ 60000000ull

static void lcd_fail(FM1PocLCD *lcd, const char *reason)
{
    pi32v2_fail(&lcd->cpu->env, reason);
}

static unsigned parameter_length(uint8_t command)
{
    switch (command) {
    case 0x2a: case 0x2b: return 4;
    case 0x3a: case 0x36: return 1;
    default: return 0;
    }
}

static void panel_reset(FM1PocLCD *lcd)
{
    lcd->sleeping = true;
    lcd->display_on = false;
    lcd->inverted = false;
    lcd->color_mode = 0;
    lcd->address_mode = 0;
    lcd->command = 0;
    lcd->parameter_count = 0;
    lcd->have_pixel_high = false;
    lcd->x0 = lcd->y0 = lcd->x = lcd->y = 0;
    lcd->x1 = FM1_LCD_WIDTH - 1;
    lcd->y1 = FM1_LCD_HEIGHT - 1;
    memset(lcd->pixels, 0, sizeof(lcd->pixels));
}

static void panel_command(FM1PocLCD *lcd, uint8_t command)
{
    if (lcd->parameter_count != parameter_length(lcd->command) ||
        lcd->have_pixel_high) {
        lcd_fail(lcd, "LCD command interrupts an incomplete parameter/pixel");
    }
    lcd->commands++;
    lcd->parameter_count = 0;
    lcd->command = command;
    switch (command) {
    case 0x01: panel_reset(lcd); break;
    case 0x11: lcd->sleeping = false; break;
    case 0x13: break; /* Normal display mode; no partial/scroll modes modeled. */
    case 0x21: lcd->inverted = true; break; /* IPS driving, not byte inversion. */
    case 0x29: lcd->display_on = true; break;
    case 0x2a: case 0x2b: case 0x3a: case 0x36: break;
    case 0x2c:
        if (lcd->color_mode != 0x55 || lcd->address_mode != 0) {
            lcd_fail(lcd, "LCD pixel write needs RGB565 with top-left RGB order");
        }
        lcd->x = lcd->x0;
        lcd->y = lcd->y0;
        break;
    default: lcd_fail(lcd, "unsupported LCD command");
    }
}

static void panel_data(FM1PocLCD *lcd, uint8_t byte)
{
    if (lcd->command == 0x2c) {
        if (!lcd->have_pixel_high) {
            lcd->pixel_high = byte;
            lcd->have_pixel_high = true;
            return;
        }
        lcd->pixels[lcd->y * FM1_LCD_WIDTH + lcd->x] =
            (lcd->pixel_high << 8) | byte;
        lcd->have_pixel_high = false;
        lcd->pixels_written++;
        if (lcd->x++ == lcd->x1) {
            lcd->x = lcd->x0;
            if (lcd->y++ == lcd->y1) {
                lcd->y = lcd->y0;
            }
        }
        return;
    }
    unsigned length = parameter_length(lcd->command);
    if (!length || lcd->parameter_count >= length) {
        lcd_fail(lcd, "unexpected LCD parameter data");
    }
    lcd->parameters[lcd->parameter_count++] = byte;
    if (lcd->parameter_count != length) {
        return;
    }
    switch (lcd->command) {
    case 0x2a: case 0x2b: {
        uint16_t first = (lcd->parameters[0] << 8) | lcd->parameters[1];
        uint16_t last = (lcd->parameters[2] << 8) | lcd->parameters[3];
        unsigned bound = lcd->command == 0x2a ? FM1_LCD_WIDTH : FM1_LCD_HEIGHT;
        if (first > last || last >= bound) {
            lcd_fail(lcd, "LCD address window exceeds the 240x240 panel");
        }
        if (lcd->command == 0x2a) {
            lcd->x0 = first; lcd->x1 = last;
        } else {
            lcd->y0 = first; lcd->y1 = last;
        }
        break;
    }
    case 0x3a:
        if (byte != 0x55) { lcd_fail(lcd, "unsupported LCD color mode"); }
        lcd->color_mode = byte;
        break;
    case 0x36:
        if (byte != 0) { lcd_fail(lcd, "unsupported LCD address mode"); }
        lcd->address_mode = byte;
        break;
    default: g_assert_not_reached();
    }
}

static void transfer_complete(void *opaque)
{
    FM1PocLCD *lcd = opaque;
    if (lcd->transfer_dma) {
        /* Read actual guest SRAM when DMA completes. The bounded fixture keeps
         * its source stable until SPI pending is observed. */
        uint8_t bytes[256];
        uint32_t offset = 0;
        while (offset < lcd->transfer_count) {
            uint32_t length = MIN(sizeof(bytes), lcd->transfer_count - offset);
            if (address_space_read(&address_space_memory,
                                   lcd->transfer_address + offset,
                                   MEMTXATTRS_UNSPECIFIED, bytes, length) != MEMTX_OK) {
                lcd_fail(lcd, "SPI1 DMA could not read guest SRAM");
            }
            for (uint32_t i = 0; i < length; i++) {
                panel_data(lcd, bytes[i]);
            }
            offset += length;
        }
    } else if (lcd->transfer_data) {
        panel_data(lcd, lcd->transfer_byte);
    } else {
        panel_command(lcd, lcd->transfer_byte);
    }
    lcd->busy = false;
    lcd->pending = true;
    lcd->completed_transfers++;
}

static void transfer_start(FM1PocLCD *lcd, bool dma)
{
    if (lcd->busy) { lcd_fail(lcd, "overlapping SPI1 transfer"); }
    if (lcd->control != SPI_MODE || lcd->baud != 4 ||
        !(lcd->iomap_con1 & 0x10) || (lcd->pc_out & LCD_CS)) {
        lcd_fail(lcd, "SPI1 transfer needs routed master mode and asserted LCD CS");
    }
    lcd->transfer_data = (lcd->pc_out & LCD_DC) != 0;
    lcd->transfer_dma = dma;
    lcd->transfer_byte = lcd->buffer;
    lcd->transfer_address = lcd->address;
    lcd->transfer_count = dma ? lcd->count : 1;
    if (dma) {
        if (!lcd->transfer_data) { lcd_fail(lcd, "LCD command DMA is unsupported"); }
        if (!lcd->count || lcd->address < SRAM_BASE ||
            (uint64_t)lcd->address + lcd->count > SRAM_BASE + SRAM_SIZE) {
            lcd_fail(lcd, "SPI1 DMA requires a nonempty source entirely in SRAM");
        }
        lcd->dma_transfers++;
    }
    lcd->busy = true;
    lcd->pending = false;
    uint64_t ns = DIV_ROUND_UP((uint64_t)lcd->transfer_count * 8 *
                              (lcd->baud + 1) * 1000000000ull, SPI_CLOCK_HZ);
    timer_mod_ns(lcd->transfer_timer,
                 qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + ns);
}

static uint64_t spi_read(void *opaque, hwaddr offset, unsigned size)
{
    FM1PocLCD *lcd = opaque;
    switch (offset) {
    case 0: return lcd->control | (lcd->pending ? SPI_COMPLETE : 0);
    case 4: return lcd->baud;
    case 8: return lcd->buffer;
    case 12: return lcd->address;
    case 16: return lcd->count;
    default: lcd_fail(lcd, "unsupported SPI1 register read"); return 0;
    }
}

static void spi_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    FM1PocLCD *lcd = opaque;
    switch (offset) {
    case 0:
        if (value & ~(uint64_t)(SPI_MODE | SPI_ACK | SPI_COMPLETE) ||
            ((value & SPI_MODE) != SPI_MODE && (value & SPI_MODE) != 0)) {
            lcd_fail(lcd, "unsupported SPI1 control mode");
        }
        if (lcd->busy && (value & SPI_MODE) != lcd->control) {
            lcd_fail(lcd, "SPI1 mode changed during a transfer");
        }
        lcd->control = value & SPI_MODE;
        if (value & SPI_ACK) { lcd->pending = false; }
        break;
    case 4:
        if (lcd->busy || value != 4) {
            lcd_fail(lcd, "SPI1 supports idle BAUD 4 only");
        }
        lcd->baud = value;
        break;
    case 8:
        if (value > UINT8_MAX) { lcd_fail(lcd, "SPI1 byte exceeds eight bits"); }
        lcd->buffer = value;
        transfer_start(lcd, false);
        break;
    case 12:
        if (lcd->busy) { lcd_fail(lcd, "SPI1 DMA source changed during a transfer"); }
        lcd->address = value;
        break;
    case 16:
        lcd->count = value;
        transfer_start(lcd, true);
        break;
    default: lcd_fail(lcd, "unsupported SPI1 register write");
    }
}

static const MemoryRegionOps spi_ops = {
    .read = spi_read, .write = spi_write, .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {.min_access_size = 4, .max_access_size = 4},
    .impl = {.min_access_size = 4, .max_access_size = 4},
};

void fm1_lcd_init(FM1PocLCD *lcd, Object *owner, Pi32v2CPU *cpu)
{
    lcd->cpu = cpu;
    panel_reset(lcd);
    lcd->transfer_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, transfer_complete, lcd);
    memory_region_init_io(&lcd->spi_mmio, owner, &spi_ops, lcd, "fm1.spi1", 20);
    memory_region_add_subregion(get_system_memory(), SPI1_BASE, &lcd->spi_mmio);
}

void fm1_lcd_set_pins(FM1PocLCD *lcd, uint32_t pc_out,
                      uint32_t iomap_con1, uint32_t pa_out)
{
    if (lcd->busy && (((pc_out ^ lcd->pc_out) & (LCD_CS | LCD_DC)) ||
                     ((iomap_con1 ^ lcd->iomap_con1) & 0x10))) {
        lcd_fail(lcd, "LCD SPI routing or CS/D/C changed during a transfer");
    }
    lcd->pc_out = pc_out;
    lcd->iomap_con1 = iomap_con1;
    lcd->pa_out = pa_out;
}

bool fm1_lcd_visible(const FM1PocLCD *lcd)
{
    return lcd->display_on && !lcd->sleeping && !(lcd->pa_out & LCD_BACKLIGHT);
}

uint32_t fm1_lcd_rgb(const FM1PocLCD *lcd, unsigned x, unsigned y)
{
    g_assert(x < FM1_LCD_WIDTH && y < FM1_LCD_HEIGHT);
    uint16_t pixel = lcd->pixels[y * FM1_LCD_WIDTH + x];
    uint32_t red = pixel >> 11, green = (pixel >> 5) & 63, blue = pixel & 31;
    return (((red << 3) | (red >> 2)) << 16) |
           (((green << 2) | (green >> 4)) << 8) | (blue << 3) | (blue >> 2);
}
