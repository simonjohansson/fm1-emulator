/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_INPUT_H
#define HW_PI32V2_FM1_INPUT_H

#include "hw/core/qdev.h"
#include "qemu/notify.h"
#include "ui/input.h"
#include "ui/fm1-controls.h"
#include "cpu.h"

#define TYPE_FM1_INPUT "fm1-board-input"
#define FM1_INPUT_BINDINGS FM1_PANEL_CONTACTS
#define FM1_INPUT_QUEUE_CAPACITY 64
#define FM1_INPUT_COLUMNS 11
OBJECT_DECLARE_SIMPLE_TYPE(FM1PocInput, FM1_INPUT)

typedef struct FM1InputLevel {
    uint64_t generation;
    uint8_t binding;
    bool down;
} FM1InputLevel;

struct FM1PocInput {
    DeviceState parent_obj;
    Pi32v2CPU *cpu;
    QemuInputHandlerState *handler;
    VMChangeStateEntry *runstate;
    Notifier shutdown;
    FM1InputLevel queue[FM1_INPUT_QUEUE_CAPACITY];
    unsigned head, count;
    uint64_t generation;
    bool down[FM1_INPUT_BINDINGS], quarantined[FM1_INPUT_BINDINGS];
    /* Only CPU work changes these levels; GPIO reads run on the same CPU. */
    uint8_t matrix[FM1_INPUT_COLUMNS];
    uint16_t master_raw, master_pending;
    bool master_changed;
    bool active, drain_scheduled, release_all, overflowed;
};

/* Private board composition binding, before realization. */
void fm1_input_bind(FM1PocInput *input, Pi32v2CPU *cpu);
/* Turn the MASTER potentiometer to raw, 0 to FM1_PANEL_MASTER_MAX. */
void fm1_input_master(FM1PocInput *input, int raw);

#endif
