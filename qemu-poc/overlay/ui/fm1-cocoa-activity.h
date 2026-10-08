/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Host scheduling only: guest clocks and CPU/device behavior are unchanged. */
static id cocoa_vm_activity;
static VMChangeStateEntry *cocoa_vm_activity_entry;

static void cocoa_vm_activity_changed(void *opaque, bool running, RunState state)
{
    @autoreleasepool {
        if (running && !cocoa_vm_activity) {
            /* An unfocused Cocoa VM is still user-requested work. Prevent
             * App Nap, while allowing normal idle display/system sleep. */
            cocoa_vm_activity = [[[NSProcessInfo processInfo]
                beginActivityWithOptions:NSActivityUserInitiatedAllowingIdleSystemSleep
                reason:@"Running virtual machine"] retain];
        } else if (!running && cocoa_vm_activity) {
            [[NSProcessInfo processInfo] endActivity:cocoa_vm_activity];
            [cocoa_vm_activity release];
            cocoa_vm_activity = nil;
        }
    }
}

static void cocoa_vm_activity_init(void)
{
    cocoa_vm_activity_entry = qemu_add_vm_change_state_handler(
        cocoa_vm_activity_changed, NULL);
    cocoa_vm_activity_changed(NULL, runstate_is_running(), runstate_get());
}

static void cocoa_vm_activity_cleanup(void)
{
    if (cocoa_vm_activity_entry) {
        qemu_del_vm_change_state_handler(cocoa_vm_activity_entry);
        cocoa_vm_activity_entry = NULL;
    }
    cocoa_vm_activity_changed(NULL, false, RUN_STATE_SHUTDOWN);
}
