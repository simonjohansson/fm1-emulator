# Felucca QEMU handover

Continue in `/Users/simonjohansson/src/fm1-qemu-poc`, branch `codex/qemu-poc`.
The worktree was permanently moved from `/private/tmp`; reuse its current path.

Read [plan.md](../plan.md) for the current next action and agent settings,
[BOOTING.md](BOOTING.md) for actual runtime evidence, and
[ARCHITECTURE.md](ARCHITECTURE.md)/[LICENSES.md](LICENSES.md) for boundaries.
Use the unchanged saved firmware, generic CPU/device behavior and macOS target.
Follow boot → diagnose → fix → retest; existing tests are the default, and
consult the old emulator only for a specific useful ambiguity.

The original 2026-10-06 handover describes an obsolete QEMU10 checkpoint.
Its full text is preserved in Git and the
[archived handover](/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/baseline/qemu-poc/HANDOVER_FELUCCA.md).
