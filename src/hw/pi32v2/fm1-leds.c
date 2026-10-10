/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "qemu/osdep.h"
#include "qemu/timer.h"
#include "fm1-leds.h"

#define WINDOW_NS (16 * SCALE_MS)

static FM1PocLEDs *board_leds;

static void account(FM1PocLEDs *l, int64_t now)
{
    int64_t elapsed = now - l->last_ns;

    /* The main loop reads a clock the vCPU may have passed already. */
    if (elapsed <= 0) {
        return;
    }
    l->last_ns = now;
    for (unsigned col = 0; col < FM1_LED_COLUMNS; col++) {
        if (!(l->selected & (1u << col))) {
            continue;
        }
        l->selected_ns[col] += elapsed;
        for (unsigned line = 0; line < FM1_LED_ROWS; line++) {
            if (l->lines & (1u << line)) {
                l->on_ns[col][line] += elapsed;
            }
        }
    }
}

static void publish(FM1PocLEDs *l, int64_t now)
{
    for (unsigned col = 0; col < FM1_LED_COLUMNS; col++) {
        for (unsigned line = 0; line < FM1_LED_ROWS; line++) {
            int64_t selected = l->selected_ns[col];

            l->level[col][line] = selected ?
                MIN(255, l->on_ns[col][line] * 255 / selected) : 0;
            l->on_ns[col][line] = 0;
        }
        l->selected_ns[col] = 0;
    }
    l->window_ns = now;
}

void fm1_leds_set(FM1PocLEDs *l, uint16_t latched, uint8_t lines)
{
    uint16_t selected = ~latched & ((1u << FM1_LED_COLUMNS) - 1);
    int64_t now;

    board_leds = l;
    if (selected == l->selected && lines == l->lines) {
        return;
    }
    now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    account(l, now);
    l->selected = selected;
    l->lines = lines;
    if (now - l->window_ns >= WINDOW_NS) {
        publish(l, now);
    }
}

void fm1_leds_read(uint8_t levels[FM1_LED_COLUMNS][FM1_LED_ROWS])
{
    if (board_leds) {
        /* A steady LED raises no events: close the window that has run. */
        int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

        account(board_leds, now);
        if (now - board_leds->window_ns >= WINDOW_NS) {
            publish(board_leds, now);
        }
        memcpy(levels, board_leds->level, sizeof(board_leds->level));
    } else {
        memset(levels, 0, FM1_LED_COLUMNS * FM1_LED_ROWS);
    }
}
