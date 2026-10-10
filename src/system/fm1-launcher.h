/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Private native CLI adapter; all firmware uses the ordinary board loader. */
#ifndef FM1_LAUNCHER_H
#define FM1_LAUNCHER_H

static void fm1_launcher_help(const char *program)
{
    printf("Usage: %s [--headless] [--no-audio] [--] FIRMWARE\n"
           "       %s --qemu [QEMU arguments]\n"
           "\n"
           "Run an FM-1 .bin, .fwsc or .ufw image. Close the window to quit.\n"
           "\n"
           "  --headless  Run without a window or sound\n"
           "  --no-audio  Run without sound\n"
           "  --help      Show this help\n"
           "  --version   Show the emulator version\n"
           "  --qemu      Pass the remaining arguments directly to QEMU\n"
           "\n"
           "Panel: click keys/buttons; drag or scroll knobs to turn.\n"
           "Keyboard: Z/C notes, X/V octave, P/O ENV/LFO, H HOME.\n"
           "USB console: stdout/stdin; Ctrl-C exits.\n",
           program, program);
}

static G_NORETURN void fm1_launcher_error(const char *program, const char *message)
{
    fprintf(stderr, "%s: %s\nTry '%s --help' for usage.\n",
            program, message, program);
    exit(EXIT_FAILURE);
}

static void fm1_launcher_arguments(int *argc, char ***argv)
{
    char **original = *argv;
    g_autofree char *program = g_path_get_basename(original[0]);
    const char *firmware = NULL;
    bool headless = false;
    bool no_audio = false;
    bool positional_only = false;
    GPtrArray *arguments;
    struct stat st;
    int fd;
    int i;

    /* The development entry point retains QEMU's CLI and observers. */
    if (*argc > 1 && !strcmp(original[1], "--qemu")) {
        for (i = 1; i < *argc; i++) {
            original[i] = original[i + 1];
        }
        (*argc)--;
        return;
    }

    /* QEMU's normal option syntax is unchanged on its normal executable. */
    if (strcmp(program, "emulator")) {
        return;
    }

    for (i = 1; i < *argc; i++) {
        const char *argument = original[i];

        if (!positional_only && !strcmp(argument, "--")) {
            positional_only = true;
        } else if (!positional_only && !strcmp(argument, "--help")) {
            fm1_launcher_help(program);
            exit(EXIT_SUCCESS);
        } else if (!positional_only && !strcmp(argument, "--version")) {
            printf("FM-1 emulator (QEMU " QEMU_VERSION ")\n");
            exit(EXIT_SUCCESS);
        } else if (!positional_only && !strcmp(argument, "--headless")) {
            headless = true;
        } else if (!positional_only && !strcmp(argument, "--no-audio")) {
            no_audio = true;
        } else if (!positional_only && argument[0] == '-') {
            g_autofree char *message = g_strdup_printf(
                "unknown option '%s'", argument);
            fm1_launcher_error(program, message);
        } else if (firmware) {
            fm1_launcher_error(program, "specify exactly one firmware file");
        } else {
            firmware = argument;
        }
    }

    if (!firmware) {
        fm1_launcher_error(program, "a firmware file is required");
    }
    fd = open(firmware, O_RDONLY | O_NONBLOCK);
    if (fd < 0) {
        g_autofree char *message = g_strdup_printf(
            "cannot read firmware '%s': %s", firmware, g_strerror(errno));
        fm1_launcher_error(program, message);
    }
    if (fstat(fd, &st) < 0) {
        int saved_errno = errno;
        g_autofree char *message = g_strdup_printf(
            "cannot inspect firmware '%s': %s", firmware,
            g_strerror(saved_errno));
        close(fd);
        fm1_launcher_error(program, message);
    }
    close(fd);
    if (!S_ISREG(st.st_mode)) {
        g_autofree char *message = g_strdup_printf(
            "firmware '%s' must be a regular file", firmware);
        fm1_launcher_error(program, message);
    }

#ifndef CONFIG_SDL
    if (!headless) {
        fm1_launcher_error(program,
            "this build has no SDL display; use --headless");
    }
#endif
#ifndef CONFIG_AUDIO_SDL
    if (!headless && !no_audio) {
        fm1_launcher_error(program,
            "this build has no SDL audio; use --no-audio");
    }
#endif

    /* Match ordinary application execution, without inherited test controls. */
    {
        g_auto(GStrv) environment = g_listenv();
        char **name;

        for (name = environment; *name; name++) {
            if (g_str_has_prefix(*name, "FM1_POC_")) {
                g_unsetenv(*name);
            }
        }
    }

    arguments = g_ptr_array_new();
#define FM1_LAUNCHER_ARG(value) g_ptr_array_add(arguments, g_strdup(value))
    FM1_LAUNCHER_ARG(original[0]);
    FM1_LAUNCHER_ARG("-name");
    FM1_LAUNCHER_ARG("FM-1");
    FM1_LAUNCHER_ARG("-M");
    FM1_LAUNCHER_ARG("fm1-poc");
    /* Both FM-1 cores; core 1 stays in reset until firmware releases it. */
    FM1_LAUNCHER_ARG("-smp");
    FM1_LAUNCHER_ARG("2");
    FM1_LAUNCHER_ARG("-accel");
    FM1_LAUNCHER_ARG("tcg,thread=single");
    FM1_LAUNCHER_ARG("-icount");
    /* The interactive guest runs faster than real time; align paces its
     * virtual clock to the host's, as the host audio consumes samples in real
     * time and an unpaced guest overruns the output queue. */
    FM1_LAUNCHER_ARG(headless ? "shift=3,align=off,sleep=off" :
                              "shift=3,align=on,sleep=on");
    FM1_LAUNCHER_ARG("-display");
    FM1_LAUNCHER_ARG(headless ? "none" :
                     "sdl,show-cursor=on");
    if (!headless && !no_audio) {
        FM1_LAUNCHER_ARG("-audiodev");
        FM1_LAUNCHER_ARG(
            "sdl,id=fm1,out.frequency=44100,out.channels=2");
        FM1_LAUNCHER_ARG("-global");
        FM1_LAUNCHER_ARG("fm1-alnk.audiodev=fm1");
    }
    FM1_LAUNCHER_ARG("-chardev");
    FM1_LAUNCHER_ARG("stdio,id=fm1-console,signal=on");
    FM1_LAUNCHER_ARG("-serial");
    FM1_LAUNCHER_ARG("chardev:fm1-console");
    FM1_LAUNCHER_ARG("-monitor");
    FM1_LAUNCHER_ARG("none");
    FM1_LAUNCHER_ARG("-nodefaults");
    FM1_LAUNCHER_ARG("-no-user-config");
    FM1_LAUNCHER_ARG("-kernel");
    FM1_LAUNCHER_ARG(firmware);
    FM1_LAUNCHER_ARG("-append");
    FM1_LAUNCHER_ARG("application");
#undef FM1_LAUNCHER_ARG
    *argc = arguments->len;
    g_ptr_array_add(arguments, NULL);
    /* QEMU retains these arguments for the lifetime of the process. */
    *argv = (char **)g_ptr_array_free(arguments, false);
}

#endif /* FM1_LAUNCHER_H */
