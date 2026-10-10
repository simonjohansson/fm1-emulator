/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef UI_FM1_CONTROLS_H
#define UI_FM1_CONTROLS_H

#include <stdint.h>
#include "qapi/qapi-types-ui.h"

/* Board contacts, not firmware commands. Rows are the six return wires and
 * columns are the eleven shift-register strobes. Recovered from the saved
 * Felucca binary/disassembly: FM1_KEYMAP at 0x02056782 (row-major, 11 columns),
 * FM1_ENC at 0x020567c4 (column,row pairs), PANEL_DEFAULT at 0x02065304
 * (physical labels). Host qcodes are transport choices; legacy bindings stay
 * unchanged. No firmware identity or guest state is needed to close a contact.
 */
#define FM1_PANEL_KEY_CONTACTS(APPLY) \
    APPLY("C",  Z,             3, 4) \
    APPLY("C#", C,             2, 4) \
    APPLY("D",  B,             5, 4) \
    APPLY("D#", N,             4, 4) \
    APPLY("E",  M,             7, 4) \
    APPLY("F",  COMMA,         6, 4) \
    APPLY("F#", DOT,           8, 4) \
    APPLY("G",  SLASH,         9, 4) \
    APPLY("G#", Q,            10, 4) \
    APPLY("A",  W,             0, 3) \
    APPLY("A#", E,             1, 3) \
    APPLY("B",  R,             2, 3) \
    APPLY("C",  T,             3, 3) \
    APPLY("C#", Y,             4, 3) \
    APPLY("D",  U,             5, 3) \
    APPLY("D#", I,             6, 3) \
    APPLY("E",  J,             7, 3) \
    APPLY("F",  K,             8, 3) \
    APPLY("F#", L,             9, 3) \
    APPLY("G",  SEMICOLON,    10, 3) \
    APPLY("G#", APOSTROPHE,    0, 2) \
    APPLY("A",  RET,           1, 2) \
    APPLY("A#", BACKSPACE,     2, 2) \
    APPLY("B",  TAB,           3, 2) \
    APPLY("C",  SPC,           4, 2) \
    APPLY("C#", GRAVE_ACCENT,  6, 2) \
    APPLY("D",  BACKSLASH,     5, 2)

/* Twelve function buttons followed by the two octave buttons. */
#define FM1_PANEL_BUTTON_CONTACTS(APPLY) \
    APPLY("FX",        F1,  6, 1) \
    APPLY("SEL",       F2,  4, 1) \
    APPLY("ENV",       P,   2, 1) \
    APPLY("LFO",       O,   0, 1) \
    APPLY("EDIT",      F5,  9, 2) \
    APPLY("GLO",       F6,  8, 2) \
    APPLY("HOME",      H,   7, 1) \
    APPLY("SAVE",      F8,  5, 1) \
    APPLY("ARP",       F9,  3, 1) \
    APPLY("SEQ",       F10, 1, 1) \
    APPLY("PLAY/STOP", F11, 10, 2) \
    APPLY("REC",       F12, 7, 2) \
    APPLY("OCT-",      X,   0, 4) \
    APPLY("OCT+",      V,   1, 4)

/* SELECT, PRESETS, ALGORITHM, then KNOB 1-4 in physical panel order.
 * Labels follow stock firmware: PRESETS steps the preset number,
 * ALGORITHM shows the algorithm overlay, KNOB 1-4 edit ENV's Attack,
 * Decay, Sustain and Release in turn.
 * A clockwise detent visits 00,01,11,10,00 (B closes first); reverse for CCW.
 * Each transition must remain present across a guest matrix scan.
 */
#define FM1_PANEL_ENCODER_CONTACTS(APPLY) \
    APPLY("SELECT",    A,   S,    0, 0, 1, 0) \
    APPLY("PRESETS",   F13, F14,  0, 5, 1, 5) \
    APPLY("ALGORITHM", F15, F16,  2, 0, 3, 0) \
    APPLY("KNOB 1",    F17, F18,  8, 1, 9, 1) \
    APPLY("KNOB 2",    D,   F,    8, 0, 9, 0) \
    APPLY("KNOB 3",    F19, F20,  6, 0, 7, 0) \
    APPLY("KNOB 4",    F21, F22,  4, 0, 5, 0)

#define FM1_PANEL_COUNT_KEY(label, qcode, column, row) + 1
#define FM1_PANEL_COUNT_ENCODER(label, a, b, ac, ar, bc, br) + 1
enum {
    FM1_PANEL_KEYS = 0 FM1_PANEL_KEY_CONTACTS(FM1_PANEL_COUNT_KEY),
    FM1_PANEL_BUTTONS = 0 FM1_PANEL_BUTTON_CONTACTS(FM1_PANEL_COUNT_KEY),
    FM1_PANEL_ENCODERS = 0 FM1_PANEL_ENCODER_CONTACTS(FM1_PANEL_COUNT_ENCODER),
    FM1_PANEL_CONTACTS = FM1_PANEL_KEYS + FM1_PANEL_BUTTONS +
                         2 * FM1_PANEL_ENCODERS,
    FM1_PANEL_MASTER_MAX = 1023,
    FM1_PANEL_MASTER_DEFAULT = 512,
};
#undef FM1_PANEL_COUNT_KEY
#undef FM1_PANEL_COUNT_ENCODER

typedef struct FM1PanelKey {
    const char *label;
    QKeyCode qcode;
    uint8_t column, row;
} FM1PanelKey;

typedef struct FM1PanelEncoder {
    const char *label;
    QKeyCode phase_a, phase_b;
} FM1PanelEncoder;

#define FM1_PANEL_KEY(label, qcode, column, row) \
    { label, Q_KEY_CODE_##qcode, column, row },
static const FM1PanelKey fm1_panel_keys[FM1_PANEL_KEYS] = {
    FM1_PANEL_KEY_CONTACTS(FM1_PANEL_KEY)
};
static const FM1PanelKey fm1_panel_buttons[FM1_PANEL_BUTTONS] = {
    FM1_PANEL_BUTTON_CONTACTS(FM1_PANEL_KEY)
};
#undef FM1_PANEL_KEY

#define FM1_PANEL_ENCODER(label, a, b, ac, ar, bc, br) \
    { label, Q_KEY_CODE_##a, Q_KEY_CODE_##b },
static const FM1PanelEncoder fm1_panel_encoders[FM1_PANEL_ENCODERS] = {
    FM1_PANEL_ENCODER_CONTACTS(FM1_PANEL_ENCODER)
};
#undef FM1_PANEL_ENCODER

/* MASTER is the 300-degree potentiometer, sampled on SARADC channel 4/PB6.
 * In the saved binary 0x0200e2e2 samples channel 4; the smoothed/squared result
 * supplies audio gain at 0x0200388c. It is not a contact: QEMU ABS X events
 * without a console (QMP) set it, as does the panel's MASTER knob.
 */
#define FM1_PANEL_MASTER_AXIS INPUT_AXIS_X

#endif
