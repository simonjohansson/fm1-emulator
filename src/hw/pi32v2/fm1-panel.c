/* SPDX-License-Identifier: GPL-2.0-or-later */
/* The FM-1 front panel, drawn into the machine's display. Geometry follows
 * the manufacturer's product photo; nothing is copied from a bitmap. Controls
 * emit ordinary input events into the board, never guest commands. */
#include "qemu/osdep.h"
#include <math.h>
#include "fm1-panel.h"
#include "ui/fm1-leds.h"

#define W FM1_PANEL_WIDTH
#define H FM1_PANEL_HEIGHT
#define LCD_X 342
#define LCD_Y (H - 314 - FM1_LCD_HEIGHT)
/* Layout is in a bottom-left origin, as in the product photo's measurements. */
#define BL(x, y, w, h) (x), (H - (y) - (h)), (w), (h)

extern const uint8_t vgafont16[256 * 16];   /* ui/vgafont.c */

/* QEMU's input handlers carry no opaque pointer; there is one board. */
static FM1PocPanel *panel_instance;

typedef struct Color {
    float r, g, b;
} Color;

static Color gray(float value)
{
    return (Color) { value, value, value };
}

static void blend(uint32_t *pixel, Color c, float alpha)
{
    if (alpha <= 0) {
        return;
    }
    alpha = MIN(alpha, 1);
    float r = ((*pixel >> 16) & 255) / 255.f, g = ((*pixel >> 8) & 255) / 255.f;
    float b = (*pixel & 255) / 255.f;
    r += (c.r - r) * alpha;
    g += (c.g - g) * alpha;
    b += (c.b - b) * alpha;
    *pixel = (uint32_t)(r * 255 + .5f) << 16 | (uint32_t)(g * 255 + .5f) << 8 |
             (uint32_t)(b * 255 + .5f);
}

/* Fill (stroke 0) or outline an anti-aliased rounded rectangle, shaded
 * from top to bottom. */
static void draw_box(uint32_t *pixels, int stride, float x, float y, float w,
                     float h, float radius, Color top, Color bottom,
                     float alpha, float stroke)
{
    float cx = x + w / 2, cy = y + h / 2;
    float hx = w / 2 - radius, hy = h / 2 - radius;
    int x0 = MAX(0, (int)floorf(x - stroke - 1));
    int y0 = MAX(0, (int)floorf(y - stroke - 1));
    int x1 = MIN(W, (int)ceilf(x + w + stroke + 1));
    int y1 = MIN(H, (int)ceilf(y + h + stroke + 1));

    for (int py = y0; py < y1; py++) {
        float t = CLAMP((py + .5f - y) / h, 0, 1);
        Color c = { top.r + (bottom.r - top.r) * t, top.g + (bottom.g - top.g) * t,
                    top.b + (bottom.b - top.b) * t };
        for (int px = x0; px < x1; px++) {
            float qx = fabsf(px + .5f - cx) - hx, qy = fabsf(py + .5f - cy) - hy;
            float d = hypotf(MAX(qx, 0), MAX(qy, 0)) + MIN(MAX(qx, qy), 0) - radius;
            float cover = stroke ? stroke / 2 + .5f - fabsf(d) : .5f - d;
            blend(&pixels[py * stride + px], c, MIN(cover, 1) * alpha);
        }
    }
}

static void fill(uint32_t *pixels, int stride, float x, float y, float w,
                 float h, float radius, Color c)
{
    draw_box(pixels, stride, x, y, w, h, radius, c, c, 1, 0);
}

/* A round-capped line, for a knob's pointer. */
static void draw_line(uint32_t *pixels, int stride, float ax, float ay,
                      float bx, float by, float radius, Color c)
{
    float dx = bx - ax, dy = by - ay, length = dx * dx + dy * dy;

    for (int py = MAX(0, (int)(MIN(ay, by) - radius - 1));
         py < MIN(H, (int)(MAX(ay, by) + radius + 2)); py++) {
        for (int px = MAX(0, (int)(MIN(ax, bx) - radius - 1));
             px < MIN(W, (int)(MAX(ax, bx) + radius + 2)); px++) {
            float u = CLAMP(((px + .5f - ax) * dx + (py + .5f - ay) * dy) / length, 0, 1);
            float d = hypotf(px + .5f - ax - u * dx, py + .5f - ay - u * dy) - radius;
            blend(&pixels[py * stride + px], c, MIN(.5f - d, 1));
        }
    }
}

/* Centered lines of QEMU's 8x16 VGA font, doubled (scale 2) or halved in
 * height (scale 0) for small print. */
static void draw_text(uint32_t *pixels, int stride, const char *text, float x,
                      float y, float w, float h, int scale, Color c)
{
    int cw = 8 * MAX(scale, 1), ch = scale ? 16 * scale : 8;
    int lines = 1;
    for (const char *s = text; *s; s++) {
        lines += *s == '\n';
    }
    int top = (int)(y + (h - lines * ch) / 2 + .5f);
    for (const char *line = text; line; line = strchr(line, '\n') ? strchr(line, '\n') + 1 : NULL) {
        int length = strchrnul(line, '\n') - line;
        int left = (int)(x + (w - length * cw) / 2 + .5f);
        for (int i = 0; i < length; i++) {
            const uint8_t *glyph = &vgafont16[(uint8_t)line[i] * 16];
            for (int row = 0; row < ch; row++) {
                uint8_t bits = glyph[scale ? row / scale : row * 2];
                for (int col = 0; col < cw; col++) {
                    int px = left + i * cw + col, py = top + row;
                    if ((bits & (0x80 >> (col / MAX(scale, 1)))) &&
                        px >= 0 && px < W && py >= 0 && py < H) {
                        blend(&pixels[py * stride + px], c, 1);
                    }
                }
            }
        }
        top += ch;
    }
}

/* LED brightness is the fraction of the time its column was selected that
 * its line was driven; the eye is roughly logarithmic, so a dim glow of a few
 * percent reads as clearly on and a full-duty LED as full. */
static double led_brightness(float level)
{
    return pow(MIN(level / 242., 1.), .4);
}

static void draw_led(uint32_t *pixels, int stride, float x, float y,
                     double brightness, Color c)
{
    fill(pixels, stride, x, y, 12, 3.5f, 1.5f, gray(.1f));
    if (brightness < .02) {
        return;
    }
    draw_box(pixels, stride, x - 3, y - 2.5f, 18, 8.5f, 3.5f, c, c, .28f * brightness, 0);
    draw_box(pixels, stride, x, y, 12, 3.5f, 1.5f, c, c, brightness, 0);
}

static void draw_contact(uint32_t *pixels, int stride, const FM1PanelControl *c)
{
    float x = c->x + 1, y = c->y + 1, w = c->w - 2, h = c->h - 2;
    fill(pixels, stride, x, y, w, h, c->piano ? 14 : 7, gray(.07f));
    float fx = x + 3, fy = y + 3 - (c->pressed ? 0 : 2), fw = w - 6, fh = h - 6;
    float radius = c->piano ? 11 : 5;
    draw_box(pixels, stride, fx, fy, fw, fh, radius, gray(c->pressed ? .24f : .13f),
             gray(c->pressed ? .31f : .23f), 1, 0);
    draw_box(pixels, stride, fx, fy, fw, fh, radius, gray(c->pressed ? .55f : .36f),
             gray(c->pressed ? .55f : .36f), 1, 1);
    double lit = c->leds ? led_brightness(c->led_level[0]) : 1;
    if (c->piano) {
        /* The bar is the key's LED: dark when the firmware leaves it off. */
        float tone = .2f + .72f * lit;
        Color bar = c->pressed ? (Color) { .38f, .91f, .64f } :
                                 (Color) { tone, tone + .02f, tone };
        fill(pixels, stride, fx + fw / 2 - 2, fy + fh * .22f, 4, fh * .43f, 2, bar);
        return;
    }
    bool play = !strcmp(c->caption, "PLAY/STOP");
    bool rec = !strcmp(c->caption, "REC");
    float led_y = fy + 4.5f;
    draw_text(pixels, stride, play ? "PLAY\nSTOP" : c->caption, c->x, led_y + 3.5f,
              c->w, fy + fh - led_y - 3.5f, play ? 0 : 1, gray(.92f));
    float led_x = fx + fw / 2 - (play ? 14 : 6);
    for (unsigned i = 0; i < c->leds; i++, led_x += 16) {
        Color color = i == 1 ? (Color) { .25f, 1, .35f } :
                      rec ? (Color) { 1, .15f, .1f } : gray(1);
        draw_led(pixels, stride, led_x, led_y, led_brightness(c->led_level[i]), color);
    }
}

static void draw_knob(uint32_t *pixels, int stride, const FM1PanelControl *c)
{
    fill(pixels, stride, c->x + 3, c->y + 3, c->w - 6, c->h - 6, (c->w - 6) / 2, gray(.06f));
    float x = c->x + 6, y = c->y + 6, size = c->w - 12;
    draw_box(pixels, stride, x, y, size, size, size / 2, gray(.12f), gray(.34f), 1, 0);
    draw_box(pixels, stride, x, y, size, size, size / 2, gray(.52f), gray(.52f), 1, 1);
    float cx = x + size / 2, cy = y + size / 2, s = sinf(c->angle * (float)G_PI / 180);
    float k = cosf(c->angle * (float)G_PI / 180);
    draw_line(pixels, stride, cx + s * size * .28f, cy - k * size * .28f,
              cx + s * size * .41f, cy - k * size * .41f, 1.5f, gray(.9f));
}

static void draw_background(uint32_t *pixels)
{
    static const char *const black_labels[] = {
        "OP1", "OP2", "OP3", "OP4", "OP5", "OP6", "PIT", "GLO", "MONO", "POLY", "",
    };
    static const char *const knob_labels[] = { "MASTER", "SELECT", "PRESETS", "ALGORITHM" };

    fill(pixels, W, 0, 0, W, H, 0, gray(.09f));
    draw_box(pixels, W, BL(20, 38, 1040, 583), 35, gray(.20f), gray(.28f), 1, 0);
    draw_box(pixels, W, BL(20, 38, 1040, 583), 35, gray(.43f), gray(.43f), 1, 1.5f);
    draw_text(pixels, W, "M-VAVE", BL(55, 570, 190, 31), 2, gray(.91f));
    draw_text(pixels, W, "FM-1", BL(882, 570, 130, 31), 2, gray(.91f));
    fill(pixels, W, BL(324, 296, 276, 276), 18, gray(.045f));
    fill(pixels, W, BL(54, 84, 964, 197), 27, gray(.085f));
    fill(pixels, W, BL(632, 285, 368, 118), 14, gray(.085f));
    for (unsigned i = 0; i < 4; i++) {
        draw_text(pixels, W, knob_labels[i], BL(62 + (i % 2) * 103, 514 - (i / 2) * 87, 105, 20),
                  1, gray(.85f));
        g_autofree char *knob = g_strdup_printf("KNOB %u", i + 1);
        draw_text(pixels, W, knob, BL(644 + i * 90, 514, 99, 20), 1, gray(.85f));
    }
    unsigned white = 0, black = 0;
    for (unsigned i = 0; i < FM1_PANEL_KEYS; i++) {
        if (strchr(fm1_panel_keys[i].label, '#')) {
            draw_text(pixels, W, black_labels[black++], BL(49 + white * 58, 273, 42, 15),
                      0, gray(.7f));
        } else {
            g_autofree char *number = g_strdup_printf("%u", ++white);
            draw_text(pixels, W, number, BL(66 + (white - 1) * 58, 65, 49, 15), 0, gray(.62f));
        }
    }
    draw_text(pixels, W, "Click and hold keys \xfa Drag or scroll knobs \xfa Z / C notes \xfa H HOME",
              BL(0, 11, 1080, 17), 1, gray(.58f));
}

static void panel_render(FM1PocPanel *p, bool all)
{
    DisplaySurface *surface = qemu_console_surface(p->console);
    /* qemu_console_resize creates QEMU's native 32-bit RGB surface. */
    g_assert(surface_format(surface) == PIXMAN_x8r8g8b8);
    uint32_t *pixels = surface_data(surface);
    int stride = surface_stride(surface) / 4;

    if (all) {
        for (unsigned y = 0; y < H; y++) {
            memcpy(&pixels[y * stride], &p->background[y * W], W * 4);
        }
        for (unsigned i = 0; i < FM1_PANEL_CONTROLS; i++) {
            if (p->controls[i].knob) {
                draw_knob(pixels, stride, &p->controls[i]);
            } else {
                draw_contact(pixels, stride, &p->controls[i]);
            }
        }
    }
    bool visible = fm1_lcd_visible(p->lcd);
    for (unsigned y = 0; y < FM1_LCD_HEIGHT; y++) {
        for (unsigned x = 0; x < FM1_LCD_WIDTH; x++) {
            pixels[(LCD_Y + y) * stride + LCD_X + x] = visible ? fm1_lcd_rgb(p->lcd, x, y) : 0;
        }
    }
    p->lcd->redraw = false;
    if (all) {
        qemu_console_update(p->console, 0, 0, W, H);
    } else {
        qemu_console_update(p->console, LCD_X, LCD_Y, FM1_LCD_WIDTH, FM1_LCD_HEIGHT);
    }
}

static bool panel_update(void *opaque)
{
    FM1PocPanel *p = opaque;
    uint8_t levels[FM1_LED_COLUMNS][FM1_LED_ROWS];

    fm1_leds_read(levels);
    for (unsigned i = 0; i < FM1_PANEL_CONTROLS; i++) {
        FM1PanelControl *c = &p->controls[i];
        for (unsigned j = 0; j < c->leds; j++) {
            float target = levels[c->led_column[j]][c->led_row[j] - 1];
            float before = c->led_level[j];
            /* Ease towards the firmware's level, like the eye averaging its
             * frame-to-frame dimming jitter; redraw only on a visible step. */
            c->led_level[j] += (target - before) * .5f;
            if (fabsf(target - c->led_level[j]) < 1) {
                c->led_level[j] = target;
            }
            if ((int)(led_brightness(c->led_level[j]) * 24) !=
                (int)(led_brightness(before) * 24)) {
                p->dirty = true;
            }
        }
    }
    if (p->dirty || p->lcd->redraw) {
        panel_render(p, p->dirty);
        p->dirty = false;
    }
    return true;
}

static void panel_invalidate(void *opaque)
{
    FM1PocPanel *p = opaque;
    p->dirty = true;
}

static const GraphicHwOps panel_ops = {
    .invalidate = panel_invalidate,
    .gfx_update = panel_update,
};

/* shortcut: a contact held by both a key and the pointer is released by
 * whichever lets go first; track both if that matters to a player. */
static void contact(QKeyCode code, bool down)
{
    qemu_input_event_send_key_linux(NULL, qemu_input_map_qcode_to_linux[code], down);
}

static void panel_release(FM1PocPanel *p)
{
    timer_del(p->release_timer);
    if (p->held) {
        contact(p->held->contact, false);
        p->held->pressed = false;
        p->held = NULL;
        p->dirty = true;
    }
}

static void release_timeout(void *opaque)
{
    panel_release(opaque);
}

static void panel_press(FM1PocPanel *p, FM1PanelControl *c)
{
    if (!runstate_is_running()) {
        return;
    }
    panel_release(p);
    p->held = c;
    c->pressed = true;
    p->pressed_at = qemu_clock_get_ms(QEMU_CLOCK_REALTIME);
    contact(c->contact, true);
    p->dirty = true;
}

static void knob_phase(void *opaque)
{
    FM1PanelControl *c = opaque;

    if (!c->pending && !c->phase) {
        return;
    }
    if (!c->phase) {
        c->direction = c->pending > 0 ? 1 : -1;
        c->pending -= c->direction;
    }
    const FM1PanelEncoder *binding = &fm1_panel_encoders[c->encoder];
    QKeyCode first = c->direction > 0 ? binding->phase_b : binding->phase_a;
    QKeyCode second = c->direction > 0 ? binding->phase_a : binding->phase_b;
    contact(c->phase % 2 ? second : first, c->phase < 2);
    c->phase = (c->phase + 1) % 4;
    /* Each quadrature phase must outlast a guest matrix scan; Felucca
     * samples the encoders once per 1.1 ms frame. 4 ms phases play about
     * 60 detents a second, so a fast turn does not queue up. */
    timer_mod(c->phase_timer, qemu_clock_get_ms(QEMU_CLOCK_REALTIME) + 4);
}

static void panel_master(FM1PocPanel *p, int raw)
{
    if (!runstate_is_running()) {
        return;
    }
    p->master = CLAMP(raw, 0, FM1_PANEL_MASTER_MAX);
    p->controls[FM1_PANEL_CONTROLS - 1].angle = -150 + 300. * p->master / FM1_PANEL_MASTER_MAX;
    fm1_input_master(p->input, p->master);
    p->dirty = true;
}

static void knob_turn(FM1PocPanel *p, FM1PanelControl *c, int steps)
{
    if (!steps || !runstate_is_running()) {
        return;
    }
    if (c->encoder < 0) {
        panel_master(p, p->master + steps * 24);
        return;
    }
    c->angle += steps * 15;
    c->pending = CLAMP(c->pending + steps, -32, 32);
    if (!timer_pending(c->phase_timer)) {
        knob_phase(c);
    }
    p->dirty = true;
}

static FM1PanelControl *panel_hit(FM1PocPanel *p)
{
    for (unsigned i = 0; i < FM1_PANEL_CONTROLS; i++) {
        FM1PanelControl *c = &p->controls[i];
        if (p->x >= c->x && p->x < c->x + c->w && p->y >= c->y && p->y < c->y + c->h) {
            return c;
        }
    }
    return NULL;
}

static void panel_event(DeviceState *dev, QemuConsole *src, QemuInputEvent *event)
{
    FM1PocPanel *p = panel_instance;

    if (event->type == INPUT_EVENT_KIND_ABS) {
        int value = CLAMP(event->abs.value, INPUT_EVENT_ABS_MIN, INPUT_EVENT_ABS_MAX);
        if (!src) {
            /* QMP without a console turns MASTER, as it always has. */
            if (event->abs.axis == FM1_PANEL_MASTER_AXIS) {
                panel_master(p, qemu_input_scale_axis(value, INPUT_EVENT_ABS_MIN,
                             INPUT_EVENT_ABS_MAX, 0, FM1_PANEL_MASTER_MAX));
            }
        } else if (event->abs.axis == INPUT_AXIS_X) {
            p->x = qemu_input_scale_axis(value, INPUT_EVENT_ABS_MIN, INPUT_EVENT_ABS_MAX, 0, W);
        } else {
            p->y = qemu_input_scale_axis(value, INPUT_EVENT_ABS_MIN, INPUT_EVENT_ABS_MAX, 0, H);
        }
        return;
    }
    if (event->type != INPUT_EVENT_KIND_BTN || !src) {
        return;
    }
    FM1PanelControl *c = panel_hit(p);
    switch (event->btn.button) {
    case INPUT_BUTTON_LEFT:
        if (event->btn.down && c && c->knob) {
            p->dragged = c;
            p->drag_x = p->x;
            p->drag_y = p->y;
            p->drag_remainder = 0;
        } else if (event->btn.down && c) {
            panel_press(p, c);
        } else if (!event->btn.down) {
            p->dragged = NULL;
            /* A short click must last long enough for the matrix scan. */
            int64_t release = p->pressed_at + 80;
            if (p->held && release > qemu_clock_get_ms(QEMU_CLOCK_REALTIME)) {
                timer_mod(p->release_timer, release);
            } else {
                panel_release(p);
            }
        }
        break;
    case INPUT_BUTTON_WHEEL_UP:
    case INPUT_BUTTON_WHEEL_DOWN:
        if (event->btn.down && c && c->knob) {
            knob_turn(p, c, event->btn.button == INPUT_BUTTON_WHEEL_UP ? 1 : -1);
        }
        break;
    default:
        break;
    }
}

static void panel_sync(DeviceState *dev)
{
    FM1PocPanel *p = panel_instance;

    if (!p->dragged) {
        return;
    }
    /* Up or right turns clockwise. */
    p->drag_remainder += (p->drag_y - p->y) + (p->x - p->drag_x);
    int steps = p->drag_remainder / 7;
    p->drag_remainder -= steps * 7;
    p->drag_x = p->x;
    p->drag_y = p->y;
    knob_turn(p, p->dragged, steps);
}

static const QemuInputHandler panel_handler = {
    .name = "FM-1 panel",
    .mask = INPUT_EVENT_MASK_BTN | INPUT_EVENT_MASK_ABS,
    .event = panel_event,
    .sync = panel_sync,
};

static void panel_runstate(void *opaque, bool running, RunState state)
{
    FM1PocPanel *p = opaque;

    if (running) {
        return;
    }
    /* The board input releases its contacts on a stop; forget ours. */
    panel_release(p);
    p->dragged = NULL;
    for (unsigned i = 0; i < FM1_PANEL_CONTROLS; i++) {
        FM1PanelControl *c = &p->controls[i];
        if (c->phase_timer) {
            timer_del(c->phase_timer);
            c->pending = c->phase = 0;
        }
    }
}

static FM1PanelControl *add_contact(FM1PocPanel *p, unsigned *count, float x,
                                    float y, float w, float h, const FM1PanelKey *key,
                                    bool piano)
{
    FM1PanelControl *c = &p->controls[(*count)++];
    *c = (FM1PanelControl) {
        .x = x, .y = H - y - h, .w = w, .h = h, .caption = key->label,
        .piano = piano, .contact = key->qcode, .leds = 1,
        .led_column = { key->column }, .led_row = { key->row },
    };
    return c;
}

void fm1_panel_init(FM1PocPanel *p, FM1PocLCD *lcd, FM1PocInput *input)
{
    unsigned count = 0;

    g_assert(!panel_instance);
    panel_instance = p;
    p->lcd = lcd;
    p->input = input;
    for (unsigned i = 0; i < FM1_PANEL_BUTTONS; i++) {
        FM1PanelControl *c = i < 12 ?
            add_contact(p, &count, 646 + (i % 6) * 58, 347 - (i / 6) * 54, 50, 45,
                        &fm1_panel_buttons[i], false) :
            add_contact(p, &count, 79 + (i - 12) * 84, 311, 75, 36,
                        &fm1_panel_buttons[i], false);
        if (!strcmp(c->caption, "PLAY/STOP")) {
            /* PLAY's green LED. */
            c->leds = 2;
            c->led_column[1] = 8;
            c->led_row[1] = 1;
        }
    }
    unsigned white = 0;
    for (unsigned i = 0; i < FM1_PANEL_KEYS; i++) {
        if (strchr(fm1_panel_keys[i].label, '#')) {
            add_contact(p, &count, 73 + white * 58 - 24, 193, 37, 76, &fm1_panel_keys[i], true);
        } else {
            add_contact(p, &count, 66 + white++ * 58, 97, 49, 91, &fm1_panel_keys[i], true);
        }
    }
    /* The encoders, then MASTER last. */
    for (int i = 0; i <= FM1_PANEL_ENCODERS; i++) {
        int encoder = i < FM1_PANEL_ENCODERS ? i : -1;
        float x = encoder < 3 ? (encoder == -1 || encoder == 1 ? 87 : 190) : 666 + (encoder - 3) * 90;
        float y = encoder < 1 ? 455 : encoder < 3 ? 368 : 455;
        FM1PanelControl *c = &p->controls[count++];
        *c = (FM1PanelControl) {
            .x = x, .y = H - y - 56, .w = 56, .h = 56, .knob = true, .encoder = encoder,
        };
        if (encoder >= 0) {
            c->caption = fm1_panel_encoders[encoder].label;
            c->phase_timer = timer_new_ms(QEMU_CLOCK_REALTIME, knob_phase, c);
        }
    }
    g_assert(count == FM1_PANEL_CONTROLS);
    p->master = FM1_PANEL_MASTER_DEFAULT;
    p->controls[FM1_PANEL_CONTROLS - 1].angle = -150 + 300. * p->master / FM1_PANEL_MASTER_MAX;
    p->release_timer = timer_new_ms(QEMU_CLOCK_REALTIME, release_timeout, p);
    p->background = g_new(uint32_t, W * H);
    draw_background(p->background);
    p->dirty = true;
    /* This private panel is machine state rather than a qdev device. */
    p->console = qemu_graphic_console_create(NULL, 0, &panel_ops, p);
    qemu_console_resize(p->console, W, H);
    p->handler = qemu_input_handler_register(NULL, &panel_handler);
    p->runstate = qemu_add_vm_change_state_handler(panel_runstate, p);
}
