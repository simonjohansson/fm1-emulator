/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Optional fixture loading, observations and captures. None of these fixture
 * identities select CPU semantics, address maps or peripheral availability. */
#include "qemu/osdep.h"
#include "qemu/error-report.h"
#include "qemu/timer.h"
#include "qemu/main-loop.h"
#include "qapi/error.h"
#include "hw/core/boards.h"
#include "hw/core/loader.h"
#include "hw/core/irq.h"
#include "system/address-spaces.h"
#include "system/runstate.h"
#include "accel/tcg/cpu-loop.h"
#include "cpu.h"
#include "fm1-lcd.h"
#include "fm1-system.h"
#include "fm1-nor.h"
#include "fm1-usb.h"
#include "fm1-alnk.h"

#include "fm1-poc.h"

/* A private observation at the real foreground-loop boundary. The guest
 * performs all initialization, drawing, watchdog feeds and IRQ work. */
static void fm1_poc_diag_loop(CPUPi32v2State *e)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    if (m->finished) { fm1_poc_finish(e); }
    m->loop_visits++;
    if (m->loop_visits >= 3 && e->rti_count >= m->loop_target_irqs) {
        fm1_poc_finish(e);
    }
}

static void save_lcd_ppm(FM1PocState *m, const char *path)
{
    FILE *f = fopen(path, "wb");
    if (!f) { pi32v2_fail(&m->cpu->env, "cannot create display frame PPM"); }
    fprintf(f, "P6\n%u %u\n255\n", FM1_LCD_WIDTH, FM1_LCD_HEIGHT);
    uint8_t row[FM1_LCD_WIDTH * 3];
    for (unsigned y = 0; y < FM1_LCD_HEIGHT; y++) {
        for (unsigned x = 0; x < FM1_LCD_WIDTH; x++) {
            uint32_t rgb = fm1_lcd_rgb(&m->lcd, x, y);
            row[x * 3] = rgb >> 16;
            row[x * 3 + 1] = rgb >> 8;
            row[x * 3 + 2] = rgb;
        }
        if (fwrite(row, sizeof(row), 1, f) != 1) {
            pi32v2_fail(&m->cpu->env, "cannot write display frame PPM");
        }
    }
    if (fclose(f)) { pi32v2_fail(&m->cpu->env, "cannot close display frame PPM"); }
}

/* Private Felucca evidence uses device counters and whole SRAM, never the
 * diagnostic's hard-coded guest result addresses. No guest memory is changed. */
void fm1_poc_fault(CPUPi32v2State *e, const char *reason)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    if (!m || (!m->application && !m->felucca_fixture && !m->alnk_probe) || m->saving_fault) { return; }
    const char *dir = getenv("FM1_POC_STATE_DIR");
    if (!dir) { return; }
    m->saving_fault = true;
    /* A failed evidence write must not recurse through the guest-fault path. */
    g_autofree char *record = g_strdup_printf("%s/state.json", dir);
    FILE *f = fopen(record, "w");
    if (!f) { error_report("cannot save machine state"); exit(EXIT_FAILURE); }
    g_autofree char *escaped = g_strescape(reason, NULL);
    fprintf(f, "{\"profile\":\"%s\",\"reason\":\"%s\",\"pc\":%u,"
            "\"instructions\":%" PRIu64 ",\"virtual_ns\":%" PRId64
            ",\"last_access\":{\"address\":%u,\"size\":%u,\"flags\":%u},"
            "\"registers\":[", m->application ? "application" : m->alnk_probe ? "alnk-probe" : "felucca",
            escaped, e->pc, e->instructions,
            qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), m->last_access_address,
            m->last_access_size, m->last_access_flags);
    for (unsigned i = 0; i < 16; i++) { fprintf(f, "%s%u", i ? "," : "", e->gpr[i]); }
    fprintf(f, "],\"specials\":[");
    for (unsigned i = 0; i < 16; i++) { fprintf(f, "%s%u", i ? "," : "", e->spr[i]); }
    fprintf(f, "],\"irq_entries\":%" PRIu64 ",\"rti_count\":%" PRIu64
            ",\"in_irq\":%s,\"timer_expirations\":%" PRIu64
            ",\"acknowledgments\":%" PRIu64 ",\"pending\":%s,"
            "\"p33_transfers\":%" PRIu64 ",\"watchdog_arms\":%" PRIu64
            ",\"watchdog_feeds\":%" PRIu64 ",\"watchdog_expirations\":%" PRIu64
            ",\"guard_checks\":%" PRIu64 ",\"write_enable\":%u,"
            "\"nor_transactions\":%" PRIu64 ",\"nor_read_bytes\":%" PRIu64
            ",\"lcd\":{\"visible\":%s,\"busy\":%s,\"pixels_written\":%" PRIu64
            ",\"commands\":%" PRIu64 ",\"dma_transfers\":%" PRIu64
            ",\"completed_transfers\":%" PRIu64 "},",
            e->irq_entries, e->rti_count, e->in_irq ? "true" : "false",
            m->timers[1].expirations, m->timers[1].acknowledgments,
            m->timers[1].pending ? "true" : "false", m->system.p33_transfers,
            m->system.watchdog_arms, m->system.watchdog_feeds,
            m->system.watchdog_expirations, m->system.guard_checks,
            m->system.write_enable, m->nor.transactions, m->nor.read_bytes,
            fm1_lcd_visible(&m->lcd) ? "true" : "false", m->lcd.busy ? "true" : "false",
            m->lcd.pixels_written, m->lcd.commands, m->lcd.dma_transfers,
            m->lcd.completed_transfers);
    fprintf(f, "\"last_irq_source\":%u,\"irq11_entries\":%" PRIu64
            ",\"irq11_rti_count\":%" PRIu64 ",\"irq63_entries\":%" PRIu64
            ",\"irq63_rti_count\":%" PRIu64 ",", e->last_irq_source,
            e->irq11_entries, e->irq11_rti_count,
            e->irq63_entries, e->irq63_rti_count);
    FM1PocALNK *a = &m->alnk;
    fprintf(f, "\"alnk\":{\"control0\":%u,\"control1\":%u,\"control3\":%u,"
            "\"pending\":%u,\"dma_address\":%u,\"half_words\":%u,"
            "\"active_half\":%u,\"enabled\":%s,\"irq_level\":%s,"
            "\"clock_control\":%u,\"iomap_control\":%u,"
            "\"completions\":%" PRIu64 ",\"acknowledgments\":%" PRIu64
            ",\"coalesced_completions\":%" PRIu64 ",\"skipped_captures\":%" PRIu64
            ",\"sample_words\":%" PRIu64 ",\"sample_frames\":%" PRIu64
            ",\"nonzero_words\":%" PRIu64 ",\"sample_digest\":%u,"
            "\"last_half\":%u,\"latest_half_bytes\":%u,\"epoch\":%" PRId64
            ",\"deadline\":%" PRId64 "},", a->control0, a->control1, a->control3,
            a->pending, a->dma_address, a->half_words, a->active_half,
            a->enabled ? "true" : "false", a->irq_level ? "true" : "false",
            fm1_syscon_get(a->syscon, FM1_SYSCON_CLK_CON2),
            fm1_syscon_get(a->syscon, FM1_SYSCON_IOMAP_CON5),
            a->completions, a->acknowledgments,
            a->coalesced_completions, a->skipped_captures, a->sample_words,
            a->sample_frames, a->nonzero_words, a->sample_digest, a->last_half,
            a->latest_half_bytes, a->epoch, a->deadline);
    FM1PocSystem *s = &m->system;
    fprintf(f, "\"guards\":{\"emu_control\":%u,\"debug_enable\":%u,"
            "\"debug_message\":%u,\"emu_message\":%u,\"debug_unlocked\":%s,"
            "\"write_enable\":%u,\"write_windows\":[", s->emu_control,
            s->debug_enable, s->debug_message, s->emu_message,
            s->debug_unlocked ? "true" : "false", s->write_enable);
    for (unsigned i = 0; i < 3; i++) {
        fprintf(f, "%s[%u,%u]", i ? "," : "", s->write_low[i], s->write_high[i]);
    }
    fprintf(f, "],\"pc_windows\":[");
    for (unsigned i = 0; i < 2; i++) {
        fprintf(f, "%s[%u,%u]", i ? "," : "", s->pc_low[i], s->pc_high[i]);
    }
    fprintf(f, "],\"stack_windows\":[");
    for (unsigned i = 0; i < 2; i++) {
        fprintf(f, "%s[%u,%u]", i ? "," : "", s->stack_low[i], s->stack_high[i]);
    }
    fprintf(f, "]}}\n");
    if (fclose(f)) { error_report("cannot close machine state"); exit(EXIT_FAILURE); }
    g_autofree char *ram_path = g_strdup_printf("%s/state.sram", dir);
    if (!g_file_set_contents(ram_path, memory_region_get_ram_ptr(MACHINE(m)->ram),
                             MACHINE(m)->ram_size, NULL)) {
        error_report("cannot save machine SRAM"); exit(EXIT_FAILURE);
    }
    g_autofree char *audio_path = g_strdup_printf("%s/state.alnk", dir);
    if (!g_file_set_contents(audio_path, (const char *)m->alnk.latest_half,
                             m->alnk.latest_half_bytes, NULL)) {
        error_report("cannot save ALNK sample evidence"); exit(EXIT_FAILURE);
    }
    /* Only call the framebuffer writer after the JSON/SRAM files exist. */
    g_autofree char *image = g_strdup_printf("%s/lcd.ppm", dir);
    save_lcd_ppm(m, image);
}

static void display_key_toggle(void *opaque)
{
    FM1PocState *m = opaque;
    /* Change a physical matrix closure only. Guest GPIO scans and drawing
     * discover the OCT-minus press/release through the existing wiring. */
    m->matrix[0] ^= 1u << 4;
    m->display_key_deadline += 500000000;
    timer_mod_ns(m->display_key_timer, m->display_key_deadline);
}

/* Private checkpoint instrumentation observes framebuffer and guest state.
 * The standard regression changes the physical key between three frames;
 * live mode uses a virtual-time input timer and keeps executing the guest. */
static void fm1_poc_frame(CPUPi32v2State *e)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    uint32_t guest_frames = ldl_le_phys(&address_space_memory, 0x01c0827c);
    if (guest_frames != m->frames + 1 || m->lcd.busy || !fm1_lcd_visible(&m->lcd)) {
        pi32v2_fail(e, "display checkpoint needs the next complete visible guest frame");
    }
    m->frames++;
    if (m->frame_dir) {
        g_autofree char *path = m->display_live ?
            g_strdup_printf("%s/frame-live.ppm.tmp", m->frame_dir) :
            g_strdup_printf("%s/frame-%u.ppm", m->frame_dir, m->frames);
        save_lcd_ppm(m, path);
        g_autofree char *record = m->display_live ?
            g_strdup_printf("%s/frame-live.json.tmp", m->frame_dir) :
            g_strdup_printf("%s/frame-%u.json", m->frame_dir, m->frames);
        FILE *f = fopen(record, "w");
        if (!f) { pi32v2_fail(e, "cannot create display frame record"); }
        fprintf(f, "{\"frame\":%u,\"pc\":%u,\"instructions\":%" PRIu64
                ",\"virtual_ns\":%" PRId64 ",\"display_ticks\":%u,\"sp\":%u,\"ssp\":%u,"
                "\"visible\":true,\"pixels_written\":%" PRIu64
                ",\"commands\":%" PRIu64 ",\"dma_transfers\":%" PRIu64
                ",\"completed_transfers\":%" PRIu64 ",\"matrix\":[",
                m->frames, e->pc, e->instructions, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL),
                ldl_le_phys(&address_space_memory, 0x01c08280), e->spr[SP], e->spr[SSP],
                m->lcd.pixels_written, m->lcd.commands, m->lcd.dma_transfers, m->lcd.completed_transfers);
        for (unsigned i = 0; i < 11; i++) {
            fprintf(f, "%s%u", i ? "," : "", ldl_le_phys(&address_space_memory, 0x01c08068 + i * 4));
        }
        fprintf(f, "],\"registers\":[");
        for (int i = 0; i < 16; i++) { fprintf(f, "%s%u", i ? "," : "", e->gpr[i]); }
        fprintf(f, "],\"specials\":[");
        for (int i = 0; i < 16; i++) { fprintf(f, "%s%u", i ? "," : "", e->spr[i]); }
        fprintf(f, "]}\n");
        if (fclose(f)) { pi32v2_fail(e, "cannot close display frame record"); }
        if (m->display_live) {
            g_autofree char *image_final = g_strdup_printf("%s/frame-live.ppm", m->frame_dir);
            g_autofree char *record_final = g_strdup_printf("%s/frame-live.json", m->frame_dir);
            /* Publish the image before its frame-number record; each individual
             * file is complete, and readers can retry across a frame change. */
            if (rename(path, image_final) || rename(record, record_final)) {
                pi32v2_fail(e, "cannot publish live display snapshot");
            }
        }
    }
    if (!m->display_live) {
        if (m->frames == 3) { fm1_poc_finish(e); }
        m->matrix[0] = m->frames == 1 ? 1u << 4 : 0;
    }
}

static G_NORETURN void hold_checkpoint(CPUPi32v2State *e)
{
    /* Stop virtual time and leave the display/event loop responsive.
     * Exit this helper without retiring the checkpoint instruction. */
    PI32V2_CPU(env_cpu(e))->observer_held = true;
    vm_stop(RUN_STATE_PAUSED);
    cpu_loop_exit_noexc(env_cpu(e));
}

void fm1_poc_finish(CPUPi32v2State *e)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    if (m->application || m->felucca_fixture || m->alnk_probe) {
        fm1_poc_fault(e, "checkpoint reached");
        exit(EXIT_SUCCESS);
    }
    if (m->finished) { hold_checkpoint(e); }
    FM1TimerState *t = &m->timers[1];
    bool foundation = m->foundation_fixture;
    uint32_t addr = m->timer_fixture || foundation ? 0x01c08010 : 0x01c08000;
    unsigned words = m->timer_fixture || foundation ? 10 : 12;
    printf("{\"pc\":%u,\"instructions\":%" PRIu64 ",\"irq_entries\":%" PRIu64
           ",\"rti_count\":%" PRIu64 ",\"timer_expirations\":%" PRIu64
           ",\"acknowledgments\":%" PRIu64 ",\"pending\":%s,\"in_irq\":%s,"
           "\"virtual_ns\":%" PRId64 ",\"last_irq_pc\":%u,\"last_irq_handler\":%u,"
           "\"entry_icfg\":%u,\"return_icfg\":%u,\"inspection\":[",
           e->pc, e->instructions, e->irq_entries, e->rti_count, t->expirations,
           t->acknowledgments, t->pending ? "true" : "false", e->in_irq ? "true" : "false",
           qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), e->last_irq_pc, e->last_irq_handler,
           e->entry_icfg, e->return_icfg);
    for (unsigned i = 0; i < words; i++) {
        printf("%s%u", i ? "," : "", ldl_le_phys(&address_space_memory, addr + i * 4));
    }
    printf("],\"registers\":[");
    for (int i = 0; i < 16; i++) { printf("%s%u", i ? "," : "", e->gpr[i]); }
    printf("],\"specials\":[");
    for (int i = 0; i < 16; i++) { printf("%s%u", i ? "," : "", e->spr[i]); }
    printf("]");
    if (foundation) {
        printf(",\"probe\":[");
        for (unsigned i = 0; i < 12; i++) {
            printf("%s%u", i ? "," : "", ldl_le_phys(&address_space_memory, 0x01c08038 + i * 4));
        }
        printf("],\"matrix\":[");
        for (unsigned i = 0; i < 11; i++) {
            printf("%s%u", i ? "," : "", ldl_le_phys(&address_space_memory, 0x01c08068 + i * 4));
        }
        printf("],\"ram_code\":[%u,%u],\"data\":%u,\"bss\":%u,"
               "\"shift_edges\":%" PRIu64 ",\"latch_edges\":%" PRIu64
               ",\"latched_columns\":%u,\"timer4_counter\":%u",
               ldl_le_phys(&address_space_memory, 0x01c00000),
               ldl_le_phys(&address_space_memory, 0x01c00004),
               ldl_le_phys(&address_space_memory, 0x01c08000),
               ldl_le_phys(&address_space_memory, 0x01c08004),
               m->shift_edges, m->latch_edges, m->latched, fm1_timer_counter(&m->timers[0]));
    }
    if (m->display_fixture) {
        printf(",\"frames\":%u,\"display_visible\":%s,\"pixels_written\":%" PRIu64
               ",\"dma_transfers\":%" PRIu64 ",\"completed_transfers\":%" PRIu64,
               m->frames, fm1_lcd_visible(&m->lcd) ? "true" : "false", m->lcd.pixels_written,
               m->lcd.dma_transfers, m->lcd.completed_transfers);
    }
    if (m->diag_fixture) {
        FM1PocSystem *s = &m->system;
        printf(",\"p33_transfers\":%" PRIu64 ",\"p33_transactions\":%" PRIu64
               ",\"watchdog_arms\":%" PRIu64 ",\"watchdog_feeds\":%" PRIu64
               ",\"watchdog_expirations\":%" PRIu64 ",\"guard_checks\":%" PRIu64
               ",\"branches\":%" PRIu64 ",\"emu_control\":%u,\"debug_enable\":%u,"
               "\"write_enable\":%u",
               s->p33_transfers, s->p33_transactions, s->watchdog_arms, s->watchdog_feeds,
               s->watchdog_expirations, s->guard_checks, s->branches, s->emu_control,
               s->debug_enable, s->write_enable);
        printf(",\"loop_visits\":%" PRIu64 ",\"milliseconds\":%u,\"lcd_timeouts\":%u,"
               "\"p33_timeouts\":%u,\"usb_up\":%u,\"usb_timeouts\":%u,\"usb_retries\":%u",
               m->loop_visits, ldl_le_phys(&address_space_memory, 0x01c096a4),
               ldl_le_phys(&address_space_memory, 0x01c08028),
               ldl_le_phys(&address_space_memory, 0x01c0802c),
               ldub_phys(&address_space_memory, 0x01c097d0),
               ldl_le_phys(&address_space_memory, 0x01c097f4),
               ldl_le_phys(&address_space_memory, 0x01c09804));
        FM1PocNOR *n = &m->nor;
        printf(",\"nor\":{\"xip_enabled\":%s,\"busy\":%s,\"selected\":%s,"
               "\"transactions\":%" PRIu64 ",\"jedec_commands\":%" PRIu64
               ",\"status_commands\":%" PRIu64 ",\"read_commands\":%" PRIu64
               ",\"transfers\":%" PRIu64 ",\"completed_transfers\":%" PRIu64
               ",\"acknowledgments\":%" PRIu64 ",\"received_bytes\":%" PRIu64
               ",\"read_bytes\":%" PRIu64 ",\"sfc_disables\":%" PRIu64
               ",\"sfc_restores\":%" PRIu64 "}",
               fm1_nor_xip_enabled(n) ? "true" : "false", n->busy ? "true" : "false",
               n->selected ? "true" : "false", n->transactions, n->jedec_commands,
               n->status_commands, n->read_commands, n->transfers, n->completed_transfers,
               n->acknowledgments, n->received_bytes, n->read_bytes, n->sfc_disables,
               n->sfc_restores);
        FM1PocUSB *u = &m->usb;
        printf(",\"usb\":{\"host_connected\":%s,\"sie_clock_available\":%s,"
               "\"control\":%u,\"pads\":%u,\"requests\":%" PRIu64
               ",\"poll_reads\":%" PRIu64 ",\"abandoned_requests\":%" PRIu64
               ",\"dma_packets\":%" PRIu64 ",\"recent_requests\":[",
               u->host_connected ? "true" : "false", u->sie_clock_available ? "true" : "false",
               u->control, u->pads, u->requests, u->bridge_poll_reads,
               u->abandoned_requests, u->dma_packets);
        for (unsigned i = 0; i < 6; i++) { printf("%s%u", i ? "," : "", u->recent_requests[i]); }
        printf("],\"recent_polls\":[");
        for (unsigned i = 0; i < 6; i++) { printf("%s%" PRIu64, i ? "," : "", u->recent_polls[i]); }
        printf("]},\"lcd\":{\"visible\":%s,\"busy\":%s,\"pixels_written\":%" PRIu64
               ",\"commands\":%" PRIu64 ",\"dma_transfers\":%" PRIu64
               ",\"completed_transfers\":%" PRIu64 "}",
               fm1_lcd_visible(&m->lcd) ? "true" : "false", m->lcd.busy ? "true" : "false",
               m->lcd.pixels_written, m->lcd.commands, m->lcd.dma_transfers,
               m->lcd.completed_transfers);
        const char *state_dir = getenv("FM1_POC_STATE_DIR");
        if (state_dir) {
            g_autofree char *path = g_strdup_printf("%s/state-%08x.sram", state_dir, e->pc);
            GError *error = NULL;
            if (!g_file_set_contents(path, memory_region_get_ram_ptr(MACHINE(m)->ram),
                                     MACHINE(m)->ram_size, &error)) {
                error_report("cannot save diagnostic SRAM: %s", error->message); exit(EXIT_FAILURE);
            }
            g_autofree char *image = g_strdup_printf("%s/state-%08x.ppm", state_dir, e->pc);
            save_lcd_ppm(m, image);
        }
    }
    puts("}");
    fflush(stdout);
    if (m->keep_open) {
        m->finished = true;
        hold_checkpoint(e);
    }
    exit(EXIT_SUCCESS);
}


static const Pi32v2ObserverOps observers = {
    .fault = fm1_poc_fault, .finish = fm1_poc_finish,
    .frame = fm1_poc_frame, .loop = fm1_poc_diag_loop,
};

static void alnk_reset_snapshot(FILE *f, FM1PocState *m)
{
    FM1PocALNK *a = &m->alnk;
    CPUPi32v2State *e = &m->cpu->env;
    FM1TimerState *t = &m->timers[1];
    g_autofree char *sram_sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256,
        memory_region_get_ram_ptr(MACHINE(m)->ram), MACHINE(m)->ram_size);
    g_autofree char *sample_sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256,
        a->latest_half, a->latest_half_bytes);

    fprintf(f, "{\"alnk\":{\"control0\":%u,\"control1\":%u,\"control3\":%u,"
            "\"pending\":%u,\"dma_address\":%u,\"half_words\":%u,"
            "\"active_half\":%u,\"last_half\":%u,\"enabled\":%s,\"irq_level\":%s,"
            "\"epoch\":%" PRId64 ",\"deadline\":%" PRId64
            ",\"scheduled_halves\":%" PRIu64 ",\"completions\":%" PRIu64
            ",\"acknowledgments\":%" PRIu64 ",\"coalesced_completions\":%" PRIu64
            ",\"skipped_captures\":%" PRIu64 ",\"sample_words\":%" PRIu64
            ",\"sample_frames\":%" PRIu64 ",\"nonzero_words\":%" PRIu64
            ",\"sample_digest\":%u,\"latest_half_bytes\":%u,"
            "\"latest_half_sha256\":\"%s\",\"realized\":%s,\"validators_registered\":%s},"
            "\"alnk_timer_pending\":%s,\"syscon\":[%u,%u,%u],"
            "\"sram_sha256\":\"%s\",\"cpu_hard_irq\":%s,"
            "\"cpu\":{\"pc\":%u,\"instructions\":%" PRIu64 ",\"in_irq\":%s,"
            "\"irq_entries\":%" PRIu64 ",\"rti_count\":%" PRIu64 ",\"registers\":[",
            a->control0, a->control1, a->control3, a->pending, a->dma_address,
            a->half_words, a->active_half, a->last_half,
            a->enabled ? "true" : "false", a->irq_level ? "true" : "false",
            a->epoch, a->deadline, a->scheduled_halves, a->completions,
            a->acknowledgments, a->coalesced_completions, a->skipped_captures,
            a->sample_words, a->sample_frames, a->nonzero_words, a->sample_digest,
            a->latest_half_bytes, sample_sha, DEVICE(a)->realized ? "true" : "false",
            a->validators_registered ? "true" : "false",
            timer_pending(a->timer) ? "true" : "false",
            fm1_syscon_get(&m->syscon, FM1_SYSCON_CLK_CON1),
            fm1_syscon_get(&m->syscon, FM1_SYSCON_CLK_CON2),
            fm1_syscon_get(&m->syscon, FM1_SYSCON_IOMAP_CON5), sram_sha,
            (CPU(m->cpu)->interrupt_request & CPU_INTERRUPT_HARD) ? "true" : "false",
            e->pc, e->instructions, e->in_irq ? "true" : "false",
            e->irq_entries, e->rti_count);
    for (unsigned i = 0; i < 16; i++) {
        fprintf(f, "%s%u", i ? "," : "", e->gpr[i]);
    }
    fprintf(f, "],\"specials\":[");
    for (unsigned i = 0; i < 16; i++) {
        fprintf(f, "%s%u", i ? "," : "", e->spr[i]);
    }
    fprintf(f, "]},\"timer5\":{\"control\":%u,\"counter\":%u,\"period\":%u,"
            "\"pending\":%s,\"epoch\":%" PRId64 ",\"deadline\":%" PRId64
            ",\"expirations\":%" PRIu64 ",\"acknowledgments\":%" PRIu64
            ",\"timer_pending\":%s},\"unrelated\":{"
            "\"p33_transfers\":%" PRIu64 ",\"p33_transactions\":%" PRIu64
            ",\"watchdog_arms\":%" PRIu64 ",\"watchdog_feeds\":%" PRIu64
            ",\"watchdog_expirations\":%" PRIu64 ",\"watchdog_deadline\":%" PRId64
            ",\"guard_checks\":%" PRIu64 ",\"write_enable\":%u,"
            "\"nor_transactions\":%" PRIu64 ",\"nor_transfers\":%" PRIu64
            ",\"nor_completed_transfers\":%" PRIu64 ",\"nor_read_bytes\":%" PRIu64
            ",\"nor_control\":%u,\"nor_sfc_control\":%u,"
            "\"usb_control\":%u,\"usb_bridge\":%u,\"usb_requests\":%" PRIu64
            ",\"usb_polls\":%" PRIu64 ",\"usb_dma_packets\":%" PRIu64
            ",\"lcd_control\":%u,\"lcd_pixels_written\":%" PRIu64
            ",\"lcd_commands\":%" PRIu64 ",\"lcd_dma_transfers\":%" PRIu64
            ",\"lcd_completed_transfers\":%" PRIu64 "}}",
            t->control, t->counter, t->period, t->pending ? "true" : "false",
            t->epoch, t->deadline, t->expirations, t->acknowledgments,
            timer_pending(t->timer) ? "true" : "false", m->system.p33_transfers,
            m->system.p33_transactions, m->system.watchdog_arms,
            m->system.watchdog_feeds, m->system.watchdog_expirations,
            m->system.watchdog_deadline, m->system.guard_checks, m->system.write_enable,
            m->nor.transactions, m->nor.transfers, m->nor.completed_transfers,
            m->nor.read_bytes, m->nor.control, m->nor.sfc_control,
            m->usb.control, m->usb.bridge, m->usb.requests, m->usb.bridge_poll_reads,
            m->usb.dma_packets, m->lcd.control, m->lcd.pixels_written,
            m->lcd.commands, m->lcd.dma_transfers, m->lcd.completed_transfers);
}

static void alnk_test_reset(void *opaque)
{
    BQL_LOCK_GUARD();
    FM1PocState *m = opaque;
    g_autofree char *path = g_strdup_printf("%s/alnk-reset.jsonl",
                                           getenv("FM1_POC_STATE_DIR"));
    FILE *f = fopen(path, "a");
    if (!f) { error_report("cannot append ALNK reset evidence"); exit(EXIT_FAILURE); }
    fprintf(f, "{\"index\":%u,\"total\":%u,\"scheduled_ns\":%" PRId64
            ",\"actual_ns\":%" PRId64 ",\"before\":", m->alnk_reset_index,
            m->alnk_reset_count,
            m->alnk_reset_times[m->alnk_reset_index],
            qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    alnk_reset_snapshot(f, m);
    device_cold_reset(DEVICE(&m->alnk));
    fprintf(f, ",\"after\":");
    alnk_reset_snapshot(f, m);
    fprintf(f, "}\n");
    if (fclose(f)) { error_report("cannot close ALNK reset evidence"); exit(EXIT_FAILURE); }
    m->alnk_reset_index++;
    if (m->alnk_reset_index < m->alnk_reset_count) {
        timer_mod_ns(m->alnk_reset_timer, m->alnk_reset_times[m->alnk_reset_index]);
    }
}

static void configure_alnk_test_resets(FM1PocState *m)
{
    const char *schedule = getenv("FM1_POC_ALNK_RESETS_NS");
    if (!schedule) { return; }
    if (!getenv("FM1_POC_STATE_DIR") || !*getenv("FM1_POC_STATE_DIR")) {
        error_report("FM1_POC_ALNK_RESETS_NS requires FM1_POC_STATE_DIR");
        exit(EXIT_FAILURE);
    }
    g_auto(GStrv) entries = g_strsplit(schedule, ",", FM1_POC_MAX_ALNK_RESETS + 1);
    unsigned count = g_strv_length(entries);
    if (!count || count > FM1_POC_MAX_ALNK_RESETS) {
        error_report("FM1_POC_ALNK_RESETS_NS requires 1 to 16 sorted positive nanoseconds");
        exit(EXIT_FAILURE);
    }
    for (unsigned i = 0; i < count; i++) {
        char *end = NULL;
        errno = 0;
        uint64_t ns = g_ascii_strtoull(entries[i], &end, 0);
        if (errno || !*entries[i] || *entries[i] == '-' || *end || !ns ||
            ns > INT64_MAX || (i && ns < (uint64_t)m->alnk_reset_times[i - 1])) {
            error_report("FM1_POC_ALNK_RESETS_NS requires 1 to 16 sorted positive nanoseconds");
            exit(EXIT_FAILURE);
        }
        m->alnk_reset_times[i] = ns;
    }
    m->alnk_reset_count = count;
}

static void adc_test_reset(void *opaque)
{
    BQL_LOCK_GUARD();
    FM1PocState *m = opaque;

    /* Private opt-in lifecycle injection only. Guest MMIO reads into SRAM
     * provide evidence through the existing capture, without a new sidecar. */
    device_cold_reset(DEVICE(&m->adc));
    m->adc_reset_index++;
    if (m->adc_reset_index < m->adc_reset_count) {
        timer_mod_ns(m->adc_reset_timer, m->adc_reset_times[m->adc_reset_index]);
    }
}

static void configure_adc_test_inputs(FM1PocState *m)
{
    const char *initial = getenv("FM1_POC_ANALOG_INITIAL_WLA_CON0");
    const char *schedule = getenv("FM1_POC_ADC_RESETS_NS");

    if (initial) {
        char *end = NULL;
        errno = 0;
        uint64_t value = g_ascii_strtoull(initial, &end, 0);
        if (errno || !*initial || *initial == '-' || *end || value > UINT32_MAX) {
            error_report("FM1_POC_ANALOG_INITIAL_WLA_CON0 requires a 32-bit unsigned integer");
            exit(EXIT_FAILURE);
        }
        m->analog_initial_wla_con0 = value;
    }
    if (!schedule) {
        return;
    }
    if (!getenv("FM1_POC_STATE_DIR") || !*getenv("FM1_POC_STATE_DIR")) {
        error_report("FM1_POC_ADC_RESETS_NS requires FM1_POC_STATE_DIR");
        exit(EXIT_FAILURE);
    }
    g_auto(GStrv) entries = g_strsplit(schedule, ",", FM1_POC_MAX_ADC_RESETS + 1);
    unsigned count = g_strv_length(entries);
    if (!count || count > FM1_POC_MAX_ADC_RESETS) {
        error_report("FM1_POC_ADC_RESETS_NS requires 1 to 16 sorted positive nanoseconds");
        exit(EXIT_FAILURE);
    }
    for (unsigned i = 0; i < count; i++) {
        char *end = NULL;
        errno = 0;
        uint64_t ns = g_ascii_strtoull(entries[i], &end, 0);
        if (errno || !*entries[i] || *entries[i] == '-' || *end || !ns ||
            ns > INT64_MAX || (i && ns < (uint64_t)m->adc_reset_times[i - 1])) {
            error_report("FM1_POC_ADC_RESETS_NS requires 1 to 16 sorted positive nanoseconds");
            exit(EXIT_FAILURE);
        }
        m->adc_reset_times[i] = ns;
    }
    m->adc_reset_count = count;
}

void fm1_test_reset_state(CPUPi32v2State *e)
{
    FM1PocState *m = PI32V2_CPU(env_cpu(e))->machine;
    memcpy(e->gpr, m->initial_gpr, sizeof(e->gpr));
    memcpy(e->spr, m->initial_spr, sizeof(e->spr));
}

void fm1_test_configure(FM1PocState *m, MachineState *ms)
{
    const char *profile = ms->kernel_cmdline ? ms->kernel_cmdline : "";
    bool timer = !strcmp(profile, "timer");
    bool display = !strcmp(profile, "display");
    bool diag = !strcmp(profile, "diag");
    bool felucca = !strcmp(profile, "felucca");
    m->alnk_probe = !strcmp(profile, "alnk-probe");
    m->application = !*profile || !strcmp(profile, "application");
    bool application = m->application || diag || felucca || m->alnk_probe;
    const char *display_live = getenv("FM1_POC_DISPLAY_LIVE");
    if (display_live && (strcmp(display_live, "1") || !display)) {
        error_report("FM1_POC_DISPLAY_LIVE=1 requires the display fixture"); exit(EXIT_FAILURE);
    }
    m->display_live = display_live != NULL;
    const char *keep_open = getenv("FM1_POC_KEEP_OPEN");
    if (keep_open && (strcmp(keep_open, "1") || !diag)) {
        error_report("FM1_POC_KEEP_OPEN=1 requires the diagnostic fixture"); exit(EXIT_FAILURE);
    }
    m->keep_open = keep_open != NULL;
    bool foundation = display || !strcmp(profile, "foundation") ||
                      !strcmp(profile, "foundation-released");
    if (strcmp(profile, "probe") && !timer && !foundation && !application) {
        error_report("select application or an optional test fixture: probe, timer, foundation, foundation-released, display, diag, felucca, alnk-probe"); exit(EXIT_FAILURE);
    }
    if (!ms->kernel_filename) { error_report("a raw application must be supplied with -kernel"); exit(EXIT_FAILURE); }
    if (felucca) {
        g_autofree char *raw = NULL;
        gsize length;
        if (!g_file_get_contents(ms->kernel_filename, &raw, &length, NULL)) {
            error_report("cannot read Felucca image"); exit(EXIT_FAILURE);
        }
        g_autofree char *sha = g_compute_checksum_for_data(G_CHECKSUM_SHA256,
                                                         (uint8_t *)raw, length);
        if (strcmp(sha, "12a4b4ea47248467f566ec3b6984b08f2f89d6ef5a8e9e494cab5184e8fadb36")) {
            error_report("Felucca profile requires the pinned unchanged application"); exit(EXIT_FAILURE);
        }
    }
    m->timer_fixture = timer;
    m->foundation_fixture = foundation;
    m->display_fixture = display;
    m->diag_fixture = diag || m->alnk_probe;
    m->felucca_fixture = felucca;
    configure_alnk_test_resets(m);
    configure_adc_test_inputs(m);
    m->cpu->frame_pc = display ? 0x020004fa : 0;
    m->frame_dir = getenv("FM1_POC_FRAME_DIR");
    if (!m->frame_dir && !m->display_live) { m->frame_dir = "."; }
    m->cpu->boot_pc = timer ? 0x02000238 : 0x02000120;
    m->cpu->stop_pc = display ? 0x020002be : timer || foundation ? 0x020002ba : 0x0200013a;
    {
        const char *limit = getenv("FM1_POC_MAX_INSTRUCTIONS");
        const char *stop = getenv("FM1_POC_STOP_PC");
        const char *loop_irqs = getenv("FM1_POC_LOOP_IRQS");
        char *end = NULL;
        uint64_t parsed;
        m->cpu->instruction_limit = application && !m->application ? 100000000 : 0;
        if (application) { m->cpu->stop_pc = UINT32_MAX; }
        if (limit) {
            errno = 0;
            parsed = g_ascii_strtoull(limit, &end, 0);
            if (errno || !*limit || *limit == '-' || *end || !parsed) {
                error_report("FM1_POC_MAX_INSTRUCTIONS must be a positive integer"); exit(EXIT_FAILURE);
            }
            m->cpu->instruction_limit = parsed;
        }
        if (stop) {
            errno = 0;
            parsed = g_ascii_strtoull(stop, &end, 0);
            if (errno || !*stop || *stop == '-' || *end || parsed > UINT32_MAX || (parsed & 1)) {
                error_report("FM1_POC_STOP_PC must be an aligned 32-bit address"); exit(EXIT_FAILURE);
            }
            m->cpu->stop_pc = parsed;
        }
        if (loop_irqs && !diag) {
            error_report("FM1_POC_LOOP_IRQS requires the diagnostic fixture"); exit(EXIT_FAILURE);
        }
        if (loop_irqs) {
            errno = 0;
            parsed = g_ascii_strtoull(loop_irqs, &end, 0);
            if (errno || !*loop_irqs || *loop_irqs == '-' || *end || !parsed) {
                error_report("FM1_POC_LOOP_IRQS must be a positive integer"); exit(EXIT_FAILURE);
            }
            m->loop_target_irqs = parsed;
            m->cpu->loop_pc = 0x02002632;
        }
    }
    /* Test seeds belong to the harness; ordinary application handoff is
     * zeroed state with the documented boot-parameter pointer in r0. */
    if (!m->application) {
        for (unsigned i = 0; i < 16; i++) {
            m->initial_gpr[i] = 0x10203040u + i * 0x01010101u;
        }
    }
    if (application) { m->initial_gpr[0] = 0x01c7fe08; }
    if (timer) {
        m->initial_spr[SP] = 0x01c7a000;
        m->initial_spr[SSP] = 0x01c7c000;
    }
    /* Production application execution has no observers unless requested. */
    if (!m->application || getenv("FM1_POC_STOP_PC") ||
        getenv("FM1_POC_MAX_INSTRUCTIONS") || getenv("FM1_POC_STATE_DIR")) {
        m->cpu->observer_ops = &observers;
    }
}

void fm1_test_seed_ram(FM1PocState *m)
{
    MachineState *ms = MACHINE(m);
    if (m->diag_fixture && !m->alnk_probe) {
        uint8_t *ram = memory_region_get_ram_ptr(ms->ram);
        /* Preserve the explicit loader handoff and zeroed persistent regions;
         * poison every region the unchanged startup must initialize. */
        memset(ram, 0xa5, 0xb48);
        memset(ram + 0x8000, 0xa5, 0x14);
        memset(ram + 0x8020, 0xa5, 0x1ce0);
        memset(ram + 0x7fd80, 0xa5, 0x80);
    }
    if (m->felucca_fixture) {
        /* Selected artifact section bounds, checked against its pinned hash.
         * Cold power-on .noinit and loader/vector area remain zero; guest
         * startup must copy/clear the following poisoned sections itself. */
        uint8_t *ram = memory_region_get_ram_ptr(ms->ram);
        memset(ram, 0xa5, 0xb48);
        memset(ram + 0x8000, 0xa5, 0x218);
        memset(ram + 0x8220, 0xa5, 0xa60c);
        memset(ram + 0x20000, 0xa5, 0x3f920);
        memset(ram + 0x7fd80, 0xa5, 0x80);
    }
    if (m->foundation_fixture) {
        /* Poison only the new startup fixture. Guest copies/clears must replace
         * this state; the existing probe/timer seed stays unchanged. */
        memset(memory_region_get_ram_ptr(ms->ram), 0xa5, ms->ram_size);
        if (!strcmp(ms->kernel_cmdline, "foundation")) { m->matrix[0] = 1u << 4; }
    }
}

void fm1_test_start(FM1PocState *m)
{
    if (m->adc_reset_count) {
        m->adc_reset_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, adc_test_reset, m);
        timer_mod_ns(m->adc_reset_timer, m->adc_reset_times[0]);
    }
    if (m->alnk_reset_count) {
        g_autofree char *path = g_strdup_printf("%s/alnk-reset.jsonl",
                                               getenv("FM1_POC_STATE_DIR"));
        FILE *f = fopen(path, "w");
        if (!f || fclose(f)) {
            error_report("cannot initialize ALNK reset evidence"); exit(EXIT_FAILURE);
        }
        m->alnk_reset_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, alnk_test_reset, m);
        timer_mod_ns(m->alnk_reset_timer, m->alnk_reset_times[0]);
    }
    if (m->display_live) {
        m->display_key_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, display_key_toggle, m);
        m->display_key_deadline = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + 500000000;
        timer_mod_ns(m->display_key_timer, m->display_key_deadline);
    }
}
