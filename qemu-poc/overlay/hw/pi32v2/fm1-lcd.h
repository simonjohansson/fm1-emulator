/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_LCD_H
#define HW_PI32V2_FM1_LCD_H

#include "system/memory.h"
#include "qemu/timer.h"
#include "cpu.h"

#define FM1_LCD_WIDTH 240
#define FM1_LCD_HEIGHT 240

/* Private, single-machine state for the bounded display fixture. */
typedef struct FM1PocLCD {
    MemoryRegion spi_mmio;
    QEMUTimer *transfer_timer;
    Pi32v2CPU *cpu;
    QemuConsole *console;
    bool redraw;
    uint32_t control, baud, buffer, address, count;
    uint32_t pc_out, iomap_con1, pa_out;
    uint32_t transfer_address, transfer_count;
    uint8_t transfer_byte;
    bool busy, pending, transfer_dma, transfer_data;
    bool sleeping, display_on, inverted;
    uint8_t command, parameters[4], parameter_count;
    uint8_t color_mode, address_mode, pixel_high;
    bool have_pixel_high;
    uint16_t x0, x1, y0, y1, x, y;
    uint16_t pixels[FM1_LCD_WIDTH * FM1_LCD_HEIGHT];
    uint64_t pixels_written, commands, dma_transfers, completed_transfers;
} FM1PocLCD;

void fm1_lcd_init(FM1PocLCD *lcd, Object *owner, Pi32v2CPU *cpu);
void fm1_lcd_set_pins(FM1PocLCD *lcd, uint32_t pc_out,
                      uint32_t iomap_con1, uint32_t pa_out);
bool fm1_lcd_visible(const FM1PocLCD *lcd);
uint32_t fm1_lcd_rgb(const FM1PocLCD *lcd, unsigned x, unsigned y);

#endif
