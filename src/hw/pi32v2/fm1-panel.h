/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_PANEL_H
#define HW_PI32V2_FM1_PANEL_H

#include "qemu/timer.h"
#include "system/runstate.h"
#include "ui/console.h"
#include "ui/input.h"
#include "ui/fm1-controls.h"
#include "fm1-input.h"
#include "fm1-lcd.h"

#define FM1_PANEL_WIDTH 1080
#define FM1_PANEL_HEIGHT 640
/* Buttons, piano keys, the encoders and MASTER. */
#define FM1_PANEL_CONTROLS (FM1_PANEL_BUTTONS + FM1_PANEL_KEYS + \
                            FM1_PANEL_ENCODERS + 1)

typedef struct FM1PanelControl {
    float x, y, w, h;          /* canvas pixels, top-left origin */
    const char *caption;
    bool piano, knob;
    QKeyCode contact;
    bool pressed;
    /* Matrix LEDs: [0] the contact's own, [1] PLAY's extra green one. */
    unsigned leds, led_column[2], led_row[2];
    float led_level[2];        /* smoothed, 0 to 255 */
    int encoder;               /* knobs: fm1_panel_encoders index, -1 MASTER */
    double angle;
    int pending, direction, phase;
    QEMUTimer *phase_timer;
} FM1PanelControl;

/* The FM-1 front panel drawn as the machine's one display. Controls close
 * board contacts through QEMU's input layer, as keys and QMP do; any QEMU
 * display (SDL, VNC, ...) shows it and sends it the pointer. */
typedef struct FM1PocPanel {
    QemuConsole *console;
    QemuInputHandlerState *handler;
    VMChangeStateEntry *runstate;
    FM1PocLCD *lcd;
    FM1PocInput *input;
    uint32_t *background;
    FM1PanelControl controls[FM1_PANEL_CONTROLS];
    FM1PanelControl *held, *dragged;
    QEMUTimer *release_timer;
    int64_t pressed_at;
    int x, y, drag_x, drag_y;
    double drag_remainder;
    int master;
    bool dirty;
} FM1PocPanel;

void fm1_panel_init(FM1PocPanel *panel, FM1PocLCD *lcd, FM1PocInput *input);

#endif
