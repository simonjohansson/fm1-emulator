/* SPDX-License-Identifier: GPL-2.0-or-later */
/* QEMU owns mixing, sample-rate conversion, pause and the selected host driver.
 * This adapter consumes actual completed ALNK stereo samples without waiting
 * for the host. A bounded queue drops the oldest frames on overflow, keeping
 * host delays from accumulating indefinitely. Underflow does not repeat notes.
 */
#include "qemu/osdep.h"
#include "qemu/bswap.h"
#include "qemu/error-report.h"
#include "qapi/error.h"
#include "system/runstate.h"
#include "system/runstate-action.h"
#include "fm1-audio.h"
#include "fm1-alnk.h"

#define FRAME_BYTES 8u

static void audio_drain(void *opaque, int available)
{
    FM1AudioOutput *out = opaque;

    while (out->queued && available >= FRAME_BYTES) {
        unsigned bytes = MIN(out->queued, FM1_AUDIO_QUEUE_BYTES - out->read_pos);
        bytes = MIN(bytes, available - available % FRAME_BYTES);
        size_t written = audio_be_write(out->backend, out->voice,
                                       out->queue + out->read_pos, bytes);
        g_assert(written <= bytes && written % FRAME_BYTES == 0);
        if (!written) {
            break;
        }
        out->read_pos = (out->read_pos + written) % FM1_AUDIO_QUEUE_BYTES;
        out->queued -= written;
        available -= written;
    }
}

static void audio_shutdown(Notifier *notifier, void *data)
{
    FM1AudioOutput *out = container_of(notifier, FM1AudioOutput, shutdown);

    /* Close before the global backend cleanup; the composed controller is not
     * automatically unrealized at process shutdown. A pause can resume. */
    if (shutdown_action != SHUTDOWN_ACTION_PAUSE) {
        fm1_audio_cleanup(out);
    }
}

void fm1_audio_cleanup(FM1AudioOutput *out)
{
    fm1_audio_set_enabled(out, false);
    if (out->shutdown_registered) {
        notifier_remove(&out->shutdown);
        out->shutdown_registered = false;
    }
}

bool fm1_audio_init(FM1AudioOutput *out, Error **errp)
{
    const struct audsettings settings = {
        .freq = FM1_ALNK_FRAME_RATE,
        .nchannels = 2,
        .fmt = AUDIO_FORMAT_S32,
        .big_endian = false,
    };

    /* No default backend: bounded headless runs remain silent and retain their
     * original scheduling. The host launcher explicitly chooses an audiodev. */
    if (!out->backend || out->voice) {
        return true;
    }
    out->voice = audio_be_open_out(out->backend, NULL, "fm1.alnk0.out", out,
                                   audio_drain, &settings);
    if (!out->voice) {
        error_setg(errp, "Could not open FM-1 host audio output");
        return false;
    }
    if (!out->shutdown_registered) {
        out->shutdown.notify = audio_shutdown;
        qemu_register_shutdown_notifier(&out->shutdown);
        out->shutdown_registered = true;
    }
    return true;
}

void fm1_audio_set_enabled(FM1AudioOutput *out, bool enabled)
{
    if (!enabled) {
        if (out->voice) {
            audio_be_set_active_out(out->backend, out->voice, false);
            audio_be_close_out(out->backend, out->voice);
            out->voice = NULL;
        }
        out->queued = out->read_pos = 0;
    } else if (out->backend) {
        Error *err = NULL;
        if (!fm1_audio_init(out, &err)) {
            error_report_err(err);
            return;
        }
        audio_be_set_active_out(out->backend, out->voice, true);
    }
}

void fm1_audio_push(FM1AudioOutput *out, const uint8_t *pcm, unsigned bytes)
{
    if (!out->voice) {
        return;
    }
    g_assert(bytes <= FM1_AUDIO_QUEUE_BYTES && bytes % FRAME_BYTES == 0);
    audio_drain(out, FM1_AUDIO_QUEUE_BYTES);
    if (out->queued + bytes > FM1_AUDIO_QUEUE_BYTES) {
        unsigned discard = out->queued + bytes - FM1_AUDIO_QUEUE_BYTES;
        out->read_pos = (out->read_pos + discard) % FM1_AUDIO_QUEUE_BYTES;
        out->queued -= discard;
    }
    for (unsigned offset = 0; offset < bytes; offset += 4) {
        unsigned pos = (out->read_pos + out->queued) % FM1_AUDIO_QUEUE_BYTES;
        /* The supported slot is modeled as right-justified 24-bit PCM from
         * observed slot data and serial-format evidence (see BOOTING.md).
         * Align it to QEMU's signed 32-bit full scale without changing capture.
         * Unsigned shift deliberately discards sign-extension bits. */
        stl_le_p(out->queue + pos, ldl_le_p(pcm + offset) << 8);
        out->queued += 4;
    }
    audio_drain(out, FM1_AUDIO_QUEUE_BYTES);
}
