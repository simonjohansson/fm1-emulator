/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Private hooks in the pinned Cocoa backend; other QEMU boards keep its UI. */
static bool fm1_panel_resize(NSView *view);
static bool fm1_panel_native_event(NSEvent *event);
static void fm1_panel_install(void);
static void fm1_panel_release(void);
static void fm1_panel_cleanup(void);
static void fm1_panel_keyboard_event(QKbdState *state, unsigned code, bool down);
