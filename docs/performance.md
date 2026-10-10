# Performance

Unchanged Felucca 1.1.5.1 originally ran at about half real time: audio
underran, the display updated slowly and page changes lagged. A series of
changes made headless emulation about 3.6 times faster, and windowed sessions
are now paced to real time. None of them changes what the guest observes.

## Method

Every change was measured on the same workload: 50 guest seconds of
unchanged Felucca, headless, with the USB console attached as in normal use.
The run stops at a fixed instruction count and leaves a fault capture:

```sh
FM1_POC_STATE_DIR=/tmp/run FM1_POC_MAX_INSTRUCTIONS=6250000000 \
  ./emulator --qemu -M fm1-poc -accel tcg,thread=single \
  -icount shift=3,align=off,sleep=off -display none \
  -chardev null,id=c -serial chardev:c -monitor none -nodefaults \
  -no-user-config -kernel felucca-1.1.5.1-app.bin -append application
```

Icount makes the run deterministic. Each change had to end with the same
`state.json` (registers, virtual time, device and IRQ counters) and a
byte-identical `state.sram` as the original build, besides passing the full
suite, the stress run and the USB console and behavior checks. Only
`guard_checks` and `last_access` changed meaning (see below).

Profiles came from macOS `sample` on the running process, read as self time
per function on the TCG thread.

## Where the time went

The CPU core alone ran tight synthetic loops at 130 to 200 MIPS, above the
125 MIPS that real time needs at 8 ns per instruction. Felucca reached only
64. The cost was around the core: bus-guard checks on every fetch and access
(31% of the TCG thread) and MMIO reads (40%), almost all of them firmware
polling SPI1, timer and GPIO status. Felucca waits for LCD DMA about 95% of
the time, as it does on hardware, and each poll took QEMU's slowest path.

## Changes

| Commit | Change | Host seconds |
| --- | --- | --- |
| (start) | | 97.9 |
| `66a94fc` | Check bus guards when their inputs change | 69.2 |
| `d29047e` | Find unchained successors without leaving the CPU loop | 62.4 |
| `748a55e` | Dispatch peripheral pages without QEMU subpages | 51.2 |
| `b1ca16e` | Read peripheral registers without the BQL | 44.7 |
| `810d537` | Call register handlers directly from peripheral pages | 37.5 |
| `e98df4a` | Stop waking the main loop at every icount deadline | 33.1 |
| `76e54e8` | Record branch traces and ADD/SUB flags inline | 32.8 |
| `d91b431` | Translate runs of instructions into one block | 30.1 |
| `3cf0148` | Read simple MMIO regions without generic dispatch | 26.6 |
| `8e52845` | Pace interactive sessions to real time | (windowed only) |

**Guards.** Fetch checks (PC windows, XIP enabled and bounds) are decided at
translation. The TB key carries an XIP flag and a fetch-guard generation in
`cs_base`, bumped when PC windows change; either change exits the CPU loop.
XIP is a ROM device whose ROMD mode follows the SFC routing, so data reads
fault through the memory map while SFC is disabled. The stack and write
windows are mirrored into the CPU state: translated code compares SP after
every SP write and each store before the store. Loads need no check.

**CPU loop.** Calls, returns and register jumps use `lookup_and_goto_ptr`.
Completing an IF arm or REP block exits the loop only when a deferred IRQ is
pending, so IRQs still enter at the same boundary.

**Peripheral pages.** Small register blocks share 4 KiB pages, which QEMU
dispatches through a subpage, a flat-view walk and a second region dispatch.
`fm1-sfr.c` maps each page as one leaf region forwarding to the owning
block, and calls the block's handler directly for aligned accesses of a size
it accepts. Other accesses keep the block's full dispatch.

**Locking.** Pages are lockless for loads: under icount, device state changes
only on the vCPU thread (MMIO, virtual timers and queued CPU work). USB keeps
the BQL for reads because its host side runs in the main loop. Stores always
hold the BQL, as they reconfigure timers, IRQs, audio and the memory map.

**Main-loop wakeups.** Every icount deadline (TIMER5 alone gives ten
thousand a guest second) notified each AioContext of the virtual clock,
waking the main loop to contend for the BQL. A context with no virtual timer
now ignores that notification.

**Inline work.** The ETM branch ring and ADD/SUB flags are produced by
translated code instead of helpers; ADC and SBC keep their helper.

**Translation blocks.** A block runs until a branch, a memory access or an
IRQ-relevant state change. Under icount only a block's last instruction may
access MMIO; an earlier access would replay the whole instruction, including
register updates that precede the access. IF arms, REP bodies and observer
addresses keep single-instruction blocks. Icount still ends blocks at timer
deadlines, so IRQs enter at the same instruction boundaries. Helpers that
fail mid-block restore state and count instructions through the current
one, so fault captures keep their exact virtual time.

**Pacing.** Once faster than real time, an unaligned windowed session ran
the guest at about twice real time, so ALNK produced samples faster than
CoreAudio plays them. Windowed sessions use `-icount shift=3,align=on,sleep=on`;
headless sessions stay unpaced.

## QEMU hooks

`tools/integrate.py` adds two anchored hooks to the pinned release, besides
the existing launcher one. Each fails the build if its upstream
anchor changes.

| File | Hook |
| --- | --- |
| `accel/tcg/cputlb.c` | TCG loads and stores honor `lockless_io`; simple lockless regions are read directly (`src/accel/tcg/fm1-mmio-read.h`) |
| `util/async.c` | An AioContext with no virtual-clock timer ignores virtual-clock notifications |
| `accel/tcg/tcg-accel-ops-rr.c` | Icount slices are split among runnable vCPUs only, and idle vCPUs are skipped |
| `accel/tcg/cpu-exec.c` | icount's "guest is late" warnings carry the host wall-clock time and guest time |

The earlier `physmem.c` subpage-read hook was retired once no subpages
remained; integration restores the upstream text in trees it had patched.

## Keeping it fast

- An instruction that accesses memory must end its block. Do not move a
  register update ahead of an access in an instruction that may hit MMIO.
- Write the guard mirrors (`fm1_system_sync_guards`) whenever a guard register
  changes, and bump `fetch_epoch` with `cpu_exit` when PC windows change.
- New register blocks go through `fm1_sfr_map`; use `fm1_sfr_map_locked` if
  another thread changes the block's state.
- A helper that may fail mid-block uses `helper_fail` (or `stop_at` first).
- Check a change with the run above: identical `state.json` and `state.sram`,
  then compare host time.

## Remaining cost

At 26.6 s the TCG thread spends about 31% in translated code, 33% in MMIO
reads (mostly the TLB slow path every device access takes) and 12% leaving
the CPU loop at icount deadlines. Further gains need deeper changes, for
example modelling TIMER5 without a QEMU timer.

## Second core

The experimental `-smp 2` machine runs both vCPUs round-robin on one host
thread. Upstream QEMU splits every icount slice evenly among all vCPUs and
enters each one per slice, even when it is held in reset or halted. On
Felucca, which never releases core 1, that cost about 17% (27.7 s to 32.5 s)
and retranslated blocks cut short by the halved slices. The round-robin hook
divides each slice among runnable vCPUs only and skips idle ones. This
leaves about 7% (about 29.9 s) with identical `state.json` and `state.sram`.
Without core-0 observers, both cores share translated code.

Guest time charges 8 ns per instruction while one core is runnable and
4 ns while both are, so each busy core advances at 8 ns per instruction as
it would executing in parallel. With the summed clock, stock firmware's
audio task on core 0 got half of each slice and sometimes missed its
1.45 ms buffer, which played as 64 frames of silence (five in eight seconds
of a held note); a sixteen-second note now has none. Two busy cores need up
to 250 million host-executed guest instructions per guest second, less
whatever the fast-forwards skip.

## Stock firmware

Stock FM-1 firmware keeps core 1 rendering audio and spends little time
idle, so it needs that full instruction rate. Measured headless over 17.46
guest seconds (core 0 430 million, core 1 1.5 billion instructions):

| Change | Host seconds |
| --- | --- |
| HOME reached | 16.5 |
| Re-arm timers lazily on counter rewrites | 15.5 |
| Fast-forward pure polling loops | about 15.4 (noisy) |
| Write timer counters without the BQL | about 14.5 |

Core 1's render routine rewrites TIMER5's free-running counter constantly.
Each write used to delete and re-arm the QEMU timer; now an armed timer
whose deadline only moves later stays in place and re-arms on an early
expiry. A short backward loop that only loads and branches, reaching its
head eight times with identical registers, charges the rest of its
round-robin slice at once: within a slice no other core runs and no device
timer fires, so spinning on would read the same values. Core 1 polls only
about a tenth of the time, so this gains little for stock; it applies to any
firmware.

Stock's core 1 spends most of its time in a for(;;) loop that calls its
render routine, which finds no block requested and returns; the call pushes
registers and rewrites TIMER5's counter, so the polling fast-forward did not
apply. A short unconditional backward GOTO now qualifies a loop for idle
detection: once its head repeats with unchanged registers, iterations run
in watch translations (a TB flag) that log every store, and two logged
iterations with identical registers and identical stores (addresses, sizes,
values, MMIO included) charge the rest of the slice. Thirty guest seconds of
stock firmware with a held note went from 26.1 to 6.5 host seconds headless;
core 1 executes 140 million instead of 2.8 billion instructions. Felucca's
A/B is unchanged.

Core 1 reads and rewrites TIMER5's counter about five million times a
guest second each. Stores to the peripheral pages now skip the BQL in
QEMU's TCG path; timer blocks take it only for CON writes, which may change
an IRQ. A paced 40-second run then stays within a second of real time
instead of falling 5 seconds behind. The rest of the cost is translated
code, indirect-jump TB lookups and the MMIO slow path itself.

Two-core runs are mostly reproducible. Main-loop activity kicks the vCPU
thread at host-dependent moments; a kicked vCPU now resumes its unrun icount
budget instead of handing over early, and slices end at guest timer deadlines
only (upstream also stopped them at host REALTIME timers). Eight identical
stock runs to 15 guest seconds on a loaded host gave seven identical results
(before: few matched). A kick that lands on the next vCPU before it starts
still recomputes its budget at a later guest time; clearing it there stalled
the guest, so that case remains. One-core runs, such as the Felucca A/B, are
exact.

## Hardware timing

A diagnostic Felucca build on a real FM-1 timed SPI1 DMA and CPU loops
against TIMER4, which counts the 24 MHz crystal:

| Measurement | FM-1 | Model |
| --- | --- | --- |
| SPI1 byte, BAUD b | (b + 1) × 133.3 ns + 45.8 ns | (b + 1) × 133.3 ns |
| Peripheral (lsb) clock | 60 MHz | 60 MHz |
| ADD + loop branch | 19.4 ns | 16 ns |
| AND, shift, add, word RMW + loop branch | 38.9 ns | 40 ns |

The LCD model is about 6% fast at BAUD 4 (no inter-byte gap), and only BAUD 4
is accepted. The CPU costs about 6.5 ns per instruction plus about 6.5 ns per
taken branch, against a uniform 8 ns; Felucca's audio rendering takes 0.8
times as long on hardware. Neither model has been changed.
