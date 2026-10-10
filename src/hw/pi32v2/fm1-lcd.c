/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Fresh implementation from the tracked guest's wire protocol and register
 * accesses, with QEMU's MemoryRegion and virtual-clock interfaces. No Rust
 * decoder, peripheral implementation or guest drawing routine is copied.
 *
 * Scope: SPI1 master, BAUD 4 with a 60 MHz peripheral clock, PC7/PC8 CS/D/C,
 * SRAM/XIP DMA, and the ST7789 commands used by Felucca and stock firmware.
 * Timing covers transfer duration, not bus arbitration or panel reset delays.
 */
#include "qemu/osdep.h"
#include "fm1-lcd.h"
#include "fm1-nor.h"
#include "fm1-sfr.h"
#include "system/address-spaces.h"
#include "ui/console.h"

#define SPI1_BASE 0x11d00
#define SRAM_BASE 0x01c00000
#define SRAM_SIZE 0x80000
#define LCD_CS (1u << 7)
#define LCD_DC (1u << 8)
#define LCD_BACKLIGHT (1u << 2)
#define SPI_COMPLETE (1u << 15)
#define SPI_ACK (1u << 14)
#define SPI_MODE 0x21u
#define SPI_IRQ_ENABLE 0x2000u
#define SPI_CLOCK_HZ 60000000ull

static void lcd_fail(FM1PocLCD *lcd, const char *reason)
{
    /* Transfers complete on a timer; attribute to the running core. */
    pi32v2_fail(current_cpu ? cpu_env(current_cpu) : &lcd->cpu->env, reason);
}

static void lcd_update_irq(FM1PocLCD *lcd)
{
    /* SDK spi_wait_ok sets CON bit 13 before waiting for its ISR and clears
     * it afterwards; hwi.h assigns SPI1 to interrupt source 16. */
    lcd->irq_level = (lcd->control & SPI_IRQ_ENABLE) && lcd->pending;
    lcd->update_irq(lcd->opaque);
}

static bool lcd_update_display(void *opaque)
{
    FM1PocLCD *lcd = opaque;
    if (!lcd->redraw) { return true; }
    DisplaySurface *surface = qemu_console_surface(lcd->console);
    /* qemu_console_resize creates QEMU's native 32-bit RGB surface. The
     * console observes completed panel writes; it never advances the guest. */
    g_assert(surface_format(surface) == PIXMAN_x8r8g8b8);
    bool visible = fm1_lcd_visible(lcd);
    for (unsigned y = 0; y < FM1_LCD_HEIGHT; y++) {
        uint32_t *row = (uint32_t *)((uint8_t *)surface_data(surface) +
                                   y * surface_stride(surface));
        for (unsigned x = 0; x < FM1_LCD_WIDTH; x++) {
            row[x] = visible ? fm1_lcd_rgb(lcd, x, y) : 0;
        }
    }
    lcd->redraw = false;
    qemu_console_update(lcd->console, 0, 0, FM1_LCD_WIDTH, FM1_LCD_HEIGHT);
    return true;
}

static void lcd_invalidate_display(void *opaque)
{
    FM1PocLCD *lcd = opaque;
    lcd->redraw = true;
}

static const GraphicHwOps lcd_graphic_ops = {
    .invalidate = lcd_invalidate_display,
    .gfx_update = lcd_update_display,
};

static unsigned parameter_length(uint8_t command)
{
    switch (command) {
    case 0x2a: case 0x2b: return 4;
    case 0x3a: case 0x36: return 1;
    /* ST7789V v1.3: porch, power, voltage, frame-rate and gamma controls.
     * These analog settings do not transform the digital RGB565 capture.
     * Stock sends only C2's first byte, leaving its fixed FF byte at reset. */
    case 0xb2: return 5;
    case 0xb7: case 0xbb: case 0xc0: case 0xc2: case 0xc3: case 0xc4:
    case 0xc6: case 0xe7: case 0x51: return 1;
    case 0xd0: return 2;
    case 0xe0: case 0xe1: return 14;
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
    lcd->y1 = FM1_LCD_RAM_HEIGHT - 1;
    memset(lcd->pixels, 0, sizeof(lcd->pixels));
    lcd->redraw = true;
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
    lcd->redraw = true;
    switch (command) {
    case 0x01: panel_reset(lcd); break;
    case 0x11: lcd->sleeping = false; break;
    case 0x13: break; /* Normal display mode; no partial/scroll modes modeled. */
    case 0x20: lcd->inverted = false; break;
    case 0x21: lcd->inverted = true; break; /* IPS driving, not byte inversion. */
    case 0x29: lcd->display_on = true; break;
    case 0x2a: case 0x2b: case 0x3a: case 0x36: break;
    case 0xb2: case 0xb7: case 0xbb: case 0xc0: case 0xc2: case 0xc3:
    case 0xc4: case 0xc6: case 0xd0: case 0xe0: case 0xe1: case 0xe7:
    case 0x51: break;
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
        /* CASET/RASET retain their inclusive endpoints. ST7789V ignores
         * pixel data outside GRAM rather than clamping the window; stock's
         * initial clear deliberately uses column endpoint 240. */
        if (lcd->x < FM1_LCD_WIDTH && lcd->y < FM1_LCD_RAM_HEIGHT) {
            lcd->pixels[lcd->y * FM1_LCD_WIDTH + lcd->x] =
                (lcd->pixel_high << 8) | byte;
            lcd->pixels_written++;
            lcd->redraw = true;
        }
        lcd->have_pixel_high = false;
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
        if (first > last) {
            lcd_fail(lcd, "LCD address window has reversed endpoints");
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
    case 0xc0:
        if (byte != 0x2c) { lcd_fail(lcd, "unsupported LCD scan control"); }
        break; /* Reset LCMCTRL preserves the accepted MADCTL orientation. */
    case 0xe7:
        if (byte != 0) { lcd_fail(lcd, "unsupported LCD data-lane mode"); }
        break;
    case 0x51:
        if (byte != 0xff) { lcd_fail(lcd, "unsupported LCD brightness"); }
        break;
    case 0xb2: case 0xb7: case 0xbb: case 0xc2: case 0xc3: case 0xc4:
    case 0xc6: case 0xd0: case 0xe0: case 0xe1:
        break; /* Analog drive parameters; capture retains digital pixels. */
    default: g_assert_not_reached();
    }
}

static void transfer_complete(void *opaque)
{
    FM1PocLCD *lcd = opaque;
    if (lcd->transfer_dma) {
        /* Read actual guest memory when DMA completes. The bounded fixture keeps
         * its source stable until SPI pending is observed. */
        uint8_t bytes[256];
        uint32_t offset = 0;
        while (offset < lcd->transfer_count) {
            uint32_t length = MIN(sizeof(bytes), lcd->transfer_count - offset);
            if (address_space_read(&address_space_memory,
                                   lcd->transfer_address + offset,
                                   MEMTXATTRS_UNSPECIFIED, bytes, length) != MEMTX_OK) {
                lcd_fail(lcd, "SPI1 DMA could not read guest memory");
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
    lcd_update_irq(lcd);
}

static void transfer_start(FM1PocLCD *lcd, bool dma)
{
    if (lcd->busy) { lcd_fail(lcd, "overlapping SPI1 transfer"); }
    if ((lcd->control & SPI_MODE) != SPI_MODE || lcd->baud != 4 ||
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
        uint64_t end = (uint64_t)lcd->address + lcd->count;
        bool sram = lcd->address >= SRAM_BASE && end <= SRAM_BASE + SRAM_SIZE;
        bool xip = lcd->address >= FM1_NOR_XIP_BASE &&
                   end <= FM1_NOR_XIP_BASE + FM1_NOR_XIP_SIZE;
        /* Stock's LCD initialization table is in mapped XIP flash. Keep DMA
         * away from MMIO and unmapped memory; XIP reads still use SFC routing. */
        if (!lcd->count || (!sram && !xip)) {
            lcd_fail(lcd, "SPI1 DMA requires a nonempty source entirely in SRAM or XIP");
        }
        lcd->dma_transfers++;
    }
    lcd->busy = true;
    lcd->pending = false;
    lcd_update_irq(lcd);
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
        if (value & ~(uint64_t)(SPI_MODE | SPI_IRQ_ENABLE | SPI_ACK | SPI_COMPLETE) ||
            ((value & SPI_MODE) != SPI_MODE && (value & SPI_MODE) != 0)) {
            lcd_fail(lcd, "unsupported SPI1 control mode");
        }
        if (lcd->busy && (value & SPI_MODE) != (lcd->control & SPI_MODE)) {
            lcd_fail(lcd, "SPI1 mode changed during a transfer");
        }
        lcd->control = value & (SPI_MODE | SPI_IRQ_ENABLE);
        if (value & SPI_ACK) { lcd->pending = false; }
        lcd_update_irq(lcd);
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

#ifdef __EMSCRIPTEN__
void fm1_web_lcd_register(FM1PocLCD *lcd);
#endif

void fm1_lcd_init(FM1PocLCD *lcd, Object *owner, Pi32v2CPU *cpu,
                  void (*update_irq)(void *opaque), void *opaque)
{
    lcd->cpu = cpu;
    lcd->update_irq = update_irq;
    lcd->opaque = opaque;
    panel_reset(lcd);
    lcd->transfer_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, transfer_complete, lcd);
    memory_region_init_io(&lcd->spi_mmio, owner, &spi_ops, lcd, "fm1.spi1", 20);
    fm1_sfr_map(SPI1_BASE, &lcd->spi_mmio);
    /* This private panel is machine state rather than a qdev device. */
    lcd->console = qemu_graphic_console_create(NULL, 0, &lcd_graphic_ops, lcd);
    qemu_console_resize(lcd->console, FM1_LCD_WIDTH, FM1_LCD_HEIGHT);
#ifdef __EMSCRIPTEN__
    fm1_web_lcd_register(lcd);
#endif
}

void fm1_lcd_set_pins(FM1PocLCD *lcd, uint32_t pc_out,
                      uint32_t iomap_con1, uint32_t pa_out)
{
    if (lcd->busy && (((pc_out ^ lcd->pc_out) & (LCD_CS | LCD_DC)) ||
                     ((iomap_con1 ^ lcd->iomap_con1) & 0x10))) {
        lcd_fail(lcd, "LCD SPI routing or CS/D/C changed during a transfer");
    }
    if ((pa_out ^ lcd->pa_out) & LCD_BACKLIGHT) { lcd->redraw = true; }
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

#ifdef __EMSCRIPTEN__
#include <emscripten.h>

/* The browser page's exports. This one paints the panel: RGBA, 240 by 240, or NULL while
 * the panel is dark or no board exists. The page reads it while the guest runs
 * on another thread, so a frame can tear; it is a picture, not state. */
static FM1PocLCD *web_lcd;
static uint8_t web_frame[FM1_LCD_WIDTH * FM1_LCD_HEIGHT * 4];

EMSCRIPTEN_KEEPALIVE uint8_t *fm1_web_lcd_frame(void)
{
    if (!web_lcd || !fm1_lcd_visible(web_lcd)) {
        return NULL;
    }
    for (unsigned y = 0; y < FM1_LCD_HEIGHT; y++) {
        for (unsigned x = 0; x < FM1_LCD_WIDTH; x++) {
            uint32_t rgb = fm1_lcd_rgb(web_lcd, x, y);
            uint8_t *out = &web_frame[(y * FM1_LCD_WIDTH + x) * 4];
            out[0] = rgb >> 16; out[1] = rgb >> 8; out[2] = rgb; out[3] = 255;
        }
    }
    return web_frame;
}

void fm1_web_lcd_register(FM1PocLCD *lcd) { web_lcd = lcd; }

/* Guest time in ns, so the page can show how fast the guest runs against the
 * wall clock. */
EMSCRIPTEN_KEEPALIVE int64_t fm1_web_guest_ns(void)
{
    return qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
}
#endif
