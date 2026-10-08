/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Host keys close board contacts. Firmware interprets contacts and encoder
 * phases; this device has no knowledge of guest variables or execution PCs. */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/main-loop.h"
#include "hw/core/resettable.h"
#include "system/reset.h"
#include "system/runstate.h"
#include "system/runstate-action.h"
#include "fm1-input.h"

typedef struct FM1InputBinding {
    QKeyCode qcode;
    uint8_t column, row;
} FM1InputBinding;

/* Board matrix coordinates. Encoder pairs expose each phase independently,
 * so Cocoa and standard QMP KEY events traverse the same electrical path. */
static const FM1InputBinding bindings[FM1_INPUT_BINDINGS] = {
    {Q_KEY_CODE_Z, 3, 4},
    {Q_KEY_CODE_X, 0, 4},
    {Q_KEY_CODE_A, 0, 0},
    {Q_KEY_CODE_S, 1, 0},
    {Q_KEY_CODE_C, 2, 4},
    {Q_KEY_CODE_V, 1, 4},
    {Q_KEY_CODE_H, 7, 1},
    {Q_KEY_CODE_P, 2, 1},
    {Q_KEY_CODE_O, 0, 1},
    {Q_KEY_CODE_D, 8, 0},
    {Q_KEY_CODE_F, 9, 0},
};

/* All ingress/FIFO bookkeeping is protected by BQL. Invalidation clears the
 * FIFO but keeps its one scheduled worker, which consumes the new generation.
 * Releases live outside the FIFO and cannot be lost to queue overflow. */
static void input_invalidate(FM1PocInput *s)
{
    g_assert(bql_locked());
    s->generation++;
    s->head = s->count = 0;
    s->release_all = true;
    for (unsigned i = 0; i < FM1_INPUT_BINDINGS; i++) {
        s->quarantined[i] |= s->down[i];
    }
}

static void input_drain(CPUState *cpu, run_on_cpu_data data)
{
    FM1PocInput *s = data.host_ptr;

    g_assert(bql_locked());
    g_assert(cpu == CPU(s->cpu));
    /* CPU work also runs while paused. Never replay a pending press there. */
    if (!s->active || !runstate_is_running()) {
        input_invalidate(s);
    }
    if (s->release_all) {
        memset(s->matrix, 0, sizeof(s->matrix));
        s->release_all = false;
    }
    while (s->count) {
        FM1InputLevel level = s->queue[s->head];
        const FM1InputBinding *binding = &bindings[level.binding];
        uint8_t mask = 1u << binding->row;

        s->head = (s->head + 1) % FM1_INPUT_QUEUE_CAPACITY;
        s->count--;
        if (level.generation != s->generation) {
            continue;
        }
        if (level.down) {
            s->matrix[binding->column] |= mask;
        } else {
            s->matrix[binding->column] &= ~mask;
        }
    }
    s->drain_scheduled = false;
}

static void input_schedule(FM1PocInput *s)
{
    g_assert(bql_locked());
    if (!s->drain_scheduled) {
        s->drain_scheduled = true;
        async_run_on_cpu(CPU(s->cpu), input_drain, RUN_ON_CPU_HOST_PTR(s));
    }
}

static void input_event(DeviceState *dev, QemuConsole *src, QemuInputEvent *event)
{
    FM1PocInput *s = FM1_INPUT(dev);
    int qcode = qemu_input_linux_to_qcode(event->key.key);

    g_assert(bql_locked());
    if (!s->active) {
        return;
    }
    for (unsigned i = 0; i < FM1_INPUT_BINDINGS; i++) {
        bool down = event->key.down;
        unsigned tail;

        if (bindings[i].qcode != qcode) {
            continue;
        }
        /* QEMU drops paused keyups after Cocoa updates its own key state.
         * An explicit later keyup rearms a quarantined contact. If the paused
         * keyup was lost, one later press/release cycle only rearms it. */
        if (s->quarantined[i]) {
            s->down[i] = down;
            if (!down) {
                s->quarantined[i] = false;
            }
            return;
        }
        if (s->down[i] == down) {
            return;
        }
        s->down[i] = down;
        if (!runstate_is_running()) {
            input_invalidate(s);
            input_schedule(s);
            return;
        }
        if (s->count == FM1_INPUT_QUEUE_CAPACITY) {
            input_invalidate(s);
            s->overflowed = true;
            error_report("FM-1 host input queue overflow; contacts released, input validation invalidated");
            input_schedule(s);
            return;
        }
        tail = (s->head + s->count) % FM1_INPUT_QUEUE_CAPACITY;
        s->queue[tail] = (FM1InputLevel) {
            .generation = s->generation, .binding = i, .down = down,
        };
        s->count++;
        input_schedule(s);
        return;
    }
}

static const QemuInputHandler input_handler = {
    .name = "FM-1 board contacts",
    .mask = INPUT_EVENT_MASK_KEY,
    .event = input_event,
};

static void input_runstate(void *opaque, bool running, RunState state)
{
    FM1PocInput *s = opaque;

    if (!running) {
        input_invalidate(s);
        input_schedule(s);
    }
}

static void input_reset_enter(Object *obj, ResetType type)
{
    FM1PocInput *s = FM1_INPUT(obj);

    if (s->active) {
        input_invalidate(s);
        input_schedule(s);
    }
}

static void input_barrier(CPUState *cpu, run_on_cpu_data data)
{
    /* Synchronous CPU work is FIFO-ordered after the outstanding drain. */
    input_drain(cpu, data);
}

static void input_unrealize(DeviceState *dev)
{
    FM1PocInput *s = FM1_INPUT(dev);

    g_assert(bql_locked());
    /* Teardown must come from the I/O thread while the CPU is still alive.
     * CPU-timer tests must marshal it to the main AioContext first. */
    g_assert(!qemu_cpu_is_self(CPU(s->cpu)));
    s->active = false;
    qemu_unregister_resettable(OBJECT(s));
    input_invalidate(s);
    qemu_input_handler_unregister(s->handler);
    s->handler = NULL;
    qemu_del_vm_change_state_handler(s->runstate);
    s->runstate = NULL;
    notifier_remove(&s->shutdown);
    run_on_cpu(CPU(s->cpu), input_barrier, RUN_ON_CPU_HOST_PTR(s));
    memset(s->down, 0, sizeof(s->down));
    memset(s->quarantined, 0, sizeof(s->quarantined));
    s->cpu = NULL;
}

static void input_shutdown(Notifier *notifier, void *data)
{
    FM1PocInput *s = container_of(notifier, FM1PocInput, shutdown);

    /* A shutdown pause is resumable; its runstate callback releases keys.
     * Poweroff notification precedes vm_shutdown/CPU teardown. */
    if (shutdown_action != SHUTDOWN_ACTION_PAUSE) {
        qdev_unrealize(DEVICE(s));
    }
}

static void input_realize(DeviceState *dev, Error **errp)
{
    FM1PocInput *s = FM1_INPUT(dev);

    if (!s->cpu) {
        error_setg(errp, "FM-1 input requires a composition-owned CPU");
        return;
    }
    s->active = true;
    s->handler = qemu_input_handler_register(dev, &input_handler);
    s->runstate = qemu_add_vm_change_state_handler(input_runstate, s);
    s->shutdown.notify = input_shutdown;
    qemu_register_shutdown_notifier(&s->shutdown);
    /* This busless board child is not traversed by the default bus root. */
    qemu_register_resettable(OBJECT(s));
}

static void input_finalize(Object *obj)
{
    FM1PocInput *s = FM1_INPUT(obj);

    g_assert(!s->active && !s->handler && !s->runstate && !s->drain_scheduled);
}

static void input_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->realize = input_realize;
    dc->unrealize = input_unrealize;
    dc->user_creatable = false;
    dc->hotpluggable = false;
    rc->phases.enter = input_reset_enter;
}

static const TypeInfo input_info = {
    .name = TYPE_FM1_INPUT,
    .parent = TYPE_DEVICE,
    .instance_size = sizeof(FM1PocInput),
    .instance_finalize = input_finalize,
    .class_init = input_class_init,
};

static void input_register_types(void)
{
    type_register_static(&input_info);
}
type_init(input_register_types)

void fm1_input_bind(FM1PocInput *s, Pi32v2CPU *cpu)
{
    g_assert(!DEVICE(s)->realized && !s->cpu);
    s->cpu = cpu;
}
