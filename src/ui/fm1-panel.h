/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Native FM-1 front panel. Pixels still come from QEMU's LCD surface;
 * controls emit ordinary input events into the board, never guest commands. */
#include "hw/core/boards.h"
#include "ui/fm1-controls.h"
#import <dispatch/dispatch.h>

@class FM1Panel;
static FM1Panel *fm1_panel;
static bool fm1_panel_keyboard[Q_KEY_CODE__MAX];
static bool fm1_panel_pointer[Q_KEY_CODE__MAX];
static VMChangeStateEntry *fm1_panel_runstate;

static void fm1_panel_keyboard_event(QKbdState *state, unsigned code, bool down)
{
    int qcode = qemu_input_linux_to_qcode(code);
    if (fm1_panel && qcode > Q_KEY_CODE_UNMAPPED && qcode < Q_KEY_CODE__MAX) {
        fm1_panel_keyboard[qcode] = down;
        down |= fm1_panel_pointer[qcode];
    }
    qkbd_state_key_event(state, code, down);
}

static void fm1_panel_contact(QKeyCode code, bool down)
{
    with_bql(^{
        fm1_panel_pointer[code] = down;
        qkbd_state_key_event(kbd, qemu_input_map_qcode_to_linux[code],
                            down || fm1_panel_keyboard[code]);
    });
}

static NSColor *fm1_gray(CGFloat value)
{
    return [NSColor colorWithCalibratedWhite:value alpha:1];
}

static void fm1_text(NSString *text, NSRect rect, CGFloat size, NSColor *color)
{
    NSMutableParagraphStyle *style = [[[NSMutableParagraphStyle alloc] init] autorelease];
    [style setAlignment:NSTextAlignmentCenter];
    [text drawInRect:rect withAttributes:@{
        NSFontAttributeName: [NSFont systemFontOfSize:size weight:NSFontWeightMedium],
        NSForegroundColorAttributeName: color,
        NSParagraphStyleAttributeName: style,
    }];
}

@interface FM1Contact : NSView {
    QKeyCode contact;
    NSString *caption;
    bool piano, pressed;
    NSTimer *releaseTimer;
    NSTimeInterval pressedAt;
}
- (id)initWithFrame:(NSRect)rect contact:(QKeyCode)code caption:(NSString *)text piano:(bool)isPiano;
- (void)releaseContact;
@end

@implementation FM1Contact
- (id)initWithFrame:(NSRect)rect contact:(QKeyCode)code caption:(NSString *)text piano:(bool)isPiano
{
    self = [super initWithFrame:rect];
    if (self) {
        contact = code; caption = [text copy]; piano = isPiano;
        [self setAccessibilityElement:YES];
        [self setAccessibilityRole:NSAccessibilityButtonRole];
        [self setAccessibilityLabel:caption];
        [self setToolTip:caption];
    }
    return self;
}
- (void)dealloc
{
    [releaseTimer invalidate]; [caption release]; [super dealloc];
}
- (void)releaseContact
{
    [releaseTimer invalidate]; releaseTimer = nil;
    if (pressed) { fm1_panel_contact(contact, false); pressed = false; }
    [self setNeedsDisplay:YES];
}
- (void)mouseDown:(NSEvent *)event
{
    if (!bool_with_bql(^{ return runstate_is_running(); })) { return; }
    [releaseTimer invalidate]; releaseTimer = nil;
    [[self window] makeFirstResponder:cocoaView];
    pressedAt = [NSDate timeIntervalSinceReferenceDate];
    pressed = true; fm1_panel_contact(contact, true);
    [self setNeedsDisplay:YES];
}
- (void)mouseUp:(NSEvent *)event
{
    /* A short click must last long enough for the physical matrix scan.
     * Focus loss and shutdown bypass this hold and release immediately. */
    NSTimeInterval remaining = .08 - ([NSDate timeIntervalSinceReferenceDate] - pressedAt);
    if (remaining > 0) {
        releaseTimer = [NSTimer scheduledTimerWithTimeInterval:remaining target:self
                          selector:@selector(releaseContact) userInfo:nil repeats:NO];
    } else {
        [self releaseContact];
    }
}
- (BOOL)accessibilityPerformPress
{
    if (!bool_with_bql(^{ return runstate_is_running(); })) { return NO; }
    [releaseTimer invalidate];
    pressed = true;
    fm1_panel_contact(contact, true);
    [self setNeedsDisplay:YES];
    releaseTimer = [NSTimer scheduledTimerWithTimeInterval:.08 target:self
                      selector:@selector(releaseContact) userInfo:nil repeats:NO];
    return YES;
}
- (void)drawRect:(NSRect)dirty
{
    NSRect rect = NSInsetRect([self bounds], 1, 1);
    NSBezierPath *outline = [NSBezierPath bezierPathWithRoundedRect:rect
                                    xRadius:piano ? 14 : 7 yRadius:piano ? 14 : 7];
    [fm1_gray(.07) setFill]; [outline fill];
    NSRect face = NSInsetRect(rect, 3, 3);
    face.origin.y += pressed ? 0 : 2;
    NSBezierPath *path = [NSBezierPath bezierPathWithRoundedRect:face
                                    xRadius:piano ? 11 : 5 yRadius:piano ? 11 : 5];
    NSGradient *gradient = [[[NSGradient alloc] initWithStartingColor:fm1_gray(pressed ? .31 : .23)
                                                       endingColor:fm1_gray(pressed ? .24 : .13)] autorelease];
    [gradient drawInBezierPath:path angle:90];
    [fm1_gray(pressed ? .55 : .36) setStroke]; [path stroke];
    if (piano) {
        NSRect mark = NSMakeRect(NSMidX(face)-2, face.origin.y+face.size.height*.35, 4, face.size.height*.43);
        [[NSColor colorWithCalibratedRed:pressed ? .38 : .92 green:pressed ? .91 : .94 blue:pressed ? .64 : .91 alpha:1] setFill];
        [[NSBezierPath bezierPathWithRoundedRect:mark xRadius:2 yRadius:2] fill];
    } else {
        bool play = [caption isEqualToString:@"PLAY/STOP"];
        fm1_text(play ? @"PLAY\nSTOP" : caption,
                 NSMakeRect(0, NSMidY(face)-(play ? 13 : 6), rect.size.width,
                            play ? 30 : 16), 10, fm1_gray(.92));
    }
}
@end

@interface FM1Knob : NSView {
    int encoder, pending, direction, phase;
    double angle, dragRemainder, scrollRemainder;
    NSInteger master;
    NSTimer *phaseTimer;
    NSPoint dragStart;
}
- (id)initWithFrame:(NSRect)rect encoder:(int)index;
- (void)turn:(int)steps;
- (void)releaseContact;
@end

@implementation FM1Knob
- (id)initWithFrame:(NSRect)rect encoder:(int)index
{
    self = [super initWithFrame:rect];
    if (self) {
        encoder = index; master = FM1_PANEL_MASTER_DEFAULT;
        [self setAccessibilityElement:YES];
        [self setAccessibilityRole:NSAccessibilitySliderRole];
        NSString *name = index < 0 ? @"MASTER" : [NSString stringWithUTF8String:fm1_panel_encoders[index].label];
        [self setAccessibilityLabel:name];
        [self setToolTip:[name stringByAppendingString:@" — drag up/down or scroll to turn"]];
    }
    return self;
}
- (void)dealloc { [phaseTimer invalidate]; [super dealloc]; }
- (void)advancePhase
{
    if (!pending && !phase) { [phaseTimer invalidate]; phaseTimer = nil; return; }
    if (!phase) { direction = pending > 0 ? 1 : -1; pending -= direction; }
    const FM1PanelEncoder *binding = &fm1_panel_encoders[encoder];
    QKeyCode first = direction > 0 ? binding->phase_b : binding->phase_a;
    QKeyCode second = direction > 0 ? binding->phase_a : binding->phase_b;
    switch (phase) {
    case 0: fm1_panel_contact(first, true); break;
    case 1: fm1_panel_contact(second, true); break;
    case 2: fm1_panel_contact(first, false); break;
    case 3: fm1_panel_contact(second, false); break;
    }
    phase = (phase + 1) % 4;
}
- (void)turn:(int)steps
{
    if (!bool_with_bql(^{ return runstate_is_running(); })) { return; }
    if (!steps) { return; }
    if (encoder < 0) {
        master = CLAMP(master + steps * 24, 0, FM1_PANEL_MASTER_MAX);
        angle = -150 + 300. * master / FM1_PANEL_MASTER_MAX;
        with_bql(^{
            qemu_input_queue_abs(dcl.con, FM1_PANEL_MASTER_AXIS, master, 0, FM1_PANEL_MASTER_MAX);
            qemu_input_event_sync();
        });
    } else {
        angle += steps * 15;
        pending = CLAMP(pending + steps, -32, 32);
        if (!phaseTimer) {
            [self advancePhase];
            phaseTimer = [NSTimer timerWithTimeInterval:.02 target:self
                           selector:@selector(advancePhase) userInfo:nil repeats:YES];
            [[NSRunLoop mainRunLoop] addTimer:phaseTimer forMode:NSRunLoopCommonModes];
        }
    }
    [self setNeedsDisplay:YES];
}
- (void)releaseContact
{
    [phaseTimer invalidate]; phaseTimer = nil; pending = phase = 0;
    if (encoder >= 0) {
        fm1_panel_contact(fm1_panel_encoders[encoder].phase_a, false);
        fm1_panel_contact(fm1_panel_encoders[encoder].phase_b, false);
    }
}
- (void)mouseDown:(NSEvent *)event
{
    [[self window] makeFirstResponder:cocoaView];
    dragStart = [event locationInWindow]; dragRemainder = 0;
}
- (void)mouseDragged:(NSEvent *)event
{
    NSPoint point = [event locationInWindow];
    dragRemainder += (point.y-dragStart.y) + (point.x-dragStart.x);
    int steps = dragRemainder / 7;
    dragRemainder -= steps * 7; dragStart = point;
    [self turn:steps];
}
- (void)scrollWheel:(NSEvent *)event
{
    scrollRemainder += [event scrollingDeltaY] * ([event hasPreciseScrollingDeltas] ? .16 : 1);
    int steps = scrollRemainder;
    scrollRemainder -= steps; [self turn:steps];
}
- (BOOL)accessibilityPerformIncrement { [self turn:1]; return YES; }
- (BOOL)accessibilityPerformDecrement { [self turn:-1]; return YES; }
- (void)drawRect:(NSRect)dirty
{
    NSRect circle = NSInsetRect([self bounds], 3, 3);
    [fm1_gray(.06) setFill]; [[NSBezierPath bezierPathWithOvalInRect:circle] fill];
    circle = NSInsetRect(circle, 3, 3);
    NSBezierPath *face = [NSBezierPath bezierPathWithOvalInRect:circle];
    NSGradient *gradient = [[[NSGradient alloc] initWithStartingColor:fm1_gray(.34)
                                                       endingColor:fm1_gray(.12)] autorelease];
    [gradient drawInBezierPath:face angle:90];
    [fm1_gray(.52) setStroke]; [face stroke];
    NSPoint center = NSMakePoint(NSMidX(circle), NSMidY(circle));
    double radians = angle * M_PI / 180.;
    NSBezierPath *mark = [NSBezierPath bezierPath];
    [mark moveToPoint:NSMakePoint(center.x + sin(radians)*circle.size.width*.28,
                                 center.y + cos(radians)*circle.size.height*.28)];
    [mark lineToPoint:NSMakePoint(center.x + sin(radians)*circle.size.width*.41,
                                 center.y + cos(radians)*circle.size.height*.41)];
    [fm1_gray(.9) setStroke]; [mark setLineWidth:3]; [mark setLineCapStyle:NSLineCapStyleRound]; [mark stroke];
}
@end

@interface FM1Panel : NSView {
    NSMutableArray *controls;
}
- (void)releaseContacts;
@end

@implementation FM1Panel
- (id)initWithFrame:(NSRect)rect
{
    self = [super initWithFrame:rect];
    if (self) {
        controls = [[NSMutableArray alloc] init];
        /* A fixed logical canvas scales with the native window. */
        [self setBoundsSize:NSMakeSize(1080, 640)];
        [self setAutoresizingMask:NSViewWidthSizable | NSViewHeightSizable];
        for (unsigned i = 0; i < FM1_PANEL_BUTTONS; i++) {
            NSRect frame = i < 12 ? NSMakeRect(646+(i%6)*58, 347-(i/6)*54, 50, 45)
                                  : NSMakeRect(79+(i-12)*84, 311, 75, 36);
            FM1Contact *button = [[FM1Contact alloc] initWithFrame:frame contact:fm1_panel_buttons[i].qcode
                                 caption:[NSString stringWithUTF8String:fm1_panel_buttons[i].label] piano:false];
            [self addSubview:button]; [controls addObject:button]; [button release];
        }
        unsigned white = 0;
        for (unsigned i = 0; i < FM1_PANEL_KEYS; i++) {
            bool black = strchr(fm1_panel_keys[i].label, '#') != NULL;
            NSRect frame = black ? NSMakeRect(73+white*58-24, 193, 37, 76)
                                 : NSMakeRect(66+white*58, 97, 49, 91);
            if (!black) { white++; }
            NSString *label = [NSString stringWithFormat:@"Key %u %@", i+1,
                               [NSString stringWithUTF8String:fm1_panel_keys[i].label]];
            FM1Contact *key = [[FM1Contact alloc] initWithFrame:frame contact:fm1_panel_keys[i].qcode caption:label piano:true];
            [self addSubview:key]; [controls addObject:key]; [key release];
        }
        for (int i = -1; i < FM1_PANEL_ENCODERS; i++) {
            NSPoint point = i < 3 ? NSMakePoint((i == -1 || i == 1) ? 87 : 190,
                                              i < 1 ? 455 : 368)
                                  : NSMakePoint(666+(i-3)*90, 455);
            FM1Knob *knob = [[FM1Knob alloc] initWithFrame:NSMakeRect(point.x, point.y, 56, 56) encoder:i];
            [self addSubview:knob]; [controls addObject:knob]; [knob release];
        }
        [cocoaView retain];
        [cocoaView removeFromSuperview];
        [cocoaView setFrame:NSMakeRect(342, 314, 240, 240)];
        [cocoaView setAutoresizingMask:NSViewNotSizable];
        [self addSubview:cocoaView]; [cocoaView release];
        [cocoaView updateBounds];
    }
    return self;
}
- (void)dealloc { [controls release]; [super dealloc]; }
- (void)setFrameSize:(NSSize)size
{
    [super setFrameSize:size];
    [self setBoundsSize:NSMakeSize(1080, 640)];
}
- (void)releaseContacts
{
    for (id control in controls) { [control releaseContact]; }
}
- (void)drawRect:(NSRect)dirty
{
    [fm1_gray(.09) setFill]; NSRectFill([self bounds]);
    NSBezierPath *body = [NSBezierPath bezierPathWithRoundedRect:NSMakeRect(20, 38, 1040, 583) xRadius:35 yRadius:35];
    NSGradient *gradient = [[[NSGradient alloc] initWithStartingColor:fm1_gray(.28)
                                                       endingColor:fm1_gray(.20)] autorelease];
    [gradient drawInBezierPath:body angle:90];
    [fm1_gray(.43) setStroke]; [body setLineWidth:1.5]; [body stroke];
    fm1_text(@"M-VAVE", NSMakeRect(55, 570, 190, 31), 25, fm1_gray(.91));
    fm1_text(@"FM-1", NSMakeRect(882, 570, 130, 31), 27, fm1_gray(.91));
    [fm1_gray(.045) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(324, 296, 276, 276) xRadius:18 yRadius:18] fill];
    [fm1_gray(.085) setFill];
    [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(54, 84, 964, 197) xRadius:27 yRadius:27] fill];
    [[NSBezierPath bezierPathWithRoundedRect:NSMakeRect(632, 285, 368, 118) xRadius:14 yRadius:14] fill];
    const char *labels[] = {"MASTER", "SELECT", "PRESETS", "ALGORITHM"};
    for (unsigned i = 0; i < 4; i++) {
        fm1_text([NSString stringWithUTF8String:labels[i]],
                 NSMakeRect(62+(i%2)*103, 514-(i/2)*87, 105, 20), 11, fm1_gray(.85));
    }
    for (unsigned i = 0; i < 4; i++) {
        fm1_text([NSString stringWithFormat:@"KNOB %u", i+1], NSMakeRect(644+i*90, 514, 99, 20), 11, fm1_gray(.85));
    }
    const char *blackLabels[] = {"OP1", "OP2", "OP3", "OP4", "OP5", "OP6", "PIT", "GLO", "MONO", "POLY", ""};
    unsigned white = 0, black = 0;
    for (unsigned i = 0; i < FM1_PANEL_KEYS; i++) {
        if (strchr(fm1_panel_keys[i].label, '#')) {
            fm1_text([NSString stringWithUTF8String:blackLabels[black++]], NSMakeRect(49+white*58, 273, 42, 15), 8, fm1_gray(.7));
        } else {
            white++;
            fm1_text([NSString stringWithFormat:@"%u", white], NSMakeRect(66+(white-1)*58, 65, 49, 15), 9, fm1_gray(.62));
        }
    }
    fm1_text(@"Click and hold keys · Drag or scroll knobs · Z / C notes · H HOME",
             NSMakeRect(0, 11, 1080, 17), 11, fm1_gray(.58));
}
@end

static bool fm1_panel_resize(NSView *view)
{
    if (!fm1_panel) { return false; }
    [cocoaView updateBounds];
    return true;
}

static bool fm1_panel_native_event(NSEvent *event)
{
    if (!fm1_panel || [event window] != [fm1_panel window]) { return false; }
    switch ([event type]) {
    case NSEventTypeLeftMouseDown: case NSEventTypeLeftMouseUp:
    case NSEventTypeLeftMouseDragged: case NSEventTypeMouseMoved:
    case NSEventTypeScrollWheel: return true;
    default: return false;
    }
}

static void fm1_panel_state_changed(void *opaque, bool running, RunState state)
{
    if (!running) {
        if ([NSThread isMainThread]) {
            fm1_panel_release();
        } else {
            dispatch_async(dispatch_get_main_queue(), ^{ fm1_panel_release(); });
        }
    }
}

static void fm1_panel_install(void)
{
    if (!object_dynamic_cast(OBJECT(current_machine), MACHINE_TYPE_NAME("fm1-poc"))) { return; }
    NSWindow *window = [cocoaView window];
    fm1_panel = [[FM1Panel alloc] initWithFrame:NSMakeRect(0, 0, 1080, 640)];
    [window setContentView:fm1_panel];
    [window setContentAspectRatio:NSMakeSize(1080, 640)];
    [window setContentMinSize:NSMakeSize(810, 480)];
    [window setContentSize:NSMakeSize(1080, 640)];
    [window center];
    [window makeFirstResponder:cocoaView];
    fm1_panel_runstate = qemu_add_vm_change_state_handler(fm1_panel_state_changed, NULL);

}

static void fm1_panel_release(void)
{
    if (!fm1_panel) { return; }
    [fm1_panel releaseContacts];
    with_bql(^{
        memset(fm1_panel_keyboard, 0, sizeof(fm1_panel_keyboard));
        memset(fm1_panel_pointer, 0, sizeof(fm1_panel_pointer));
        qkbd_state_lift_all_keys(kbd);
    });
}

static void fm1_panel_cleanup(void)
{
    if (fm1_panel_runstate) {
        qemu_del_vm_change_state_handler(fm1_panel_runstate);
        fm1_panel_runstate = NULL;
    }
    fm1_panel_release();
    [fm1_panel release]; fm1_panel = nil;
}
