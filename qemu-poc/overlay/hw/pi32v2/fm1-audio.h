/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_PI32V2_FM1_AUDIO_H
#define HW_PI32V2_FM1_AUDIO_H

#include "qemu/audio.h"
#include "qemu/notify.h"

/* Private host output. Neither host readiness nor this queue affects DMA/IRQ
 * deadlines, captured bytes, or any guest-visible controller state. */
#define FM1_AUDIO_QUEUE_BYTES 8192u

typedef struct FM1AudioOutput {
    AudioBackend *backend;
    SWVoiceOut *voice;
    Notifier shutdown;
    bool shutdown_registered;
    uint8_t queue[FM1_AUDIO_QUEUE_BYTES];
    unsigned read_pos, queued;
} FM1AudioOutput;

bool fm1_audio_init(FM1AudioOutput *out, Error **errp);
void fm1_audio_cleanup(FM1AudioOutput *out);
void fm1_audio_set_enabled(FM1AudioOutput *out, bool enabled);
void fm1_audio_push(FM1AudioOutput *out, const uint8_t *pcm, unsigned bytes);

#endif
