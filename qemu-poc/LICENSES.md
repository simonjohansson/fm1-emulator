# Licensing and implementation provenance

The existing repository's Rust implementation and firmware sources are
GPL-3.0-only; see [../LICENSE](../LICENSE). That license is retained for the
separate reference executable in `reference/`. Guest binaries are loaded as
runtime data and remain subject to their existing licenses and provenance.
The diagnostic package, SDK and vendor compiler are not redistributed here.

QEMU 11.1.2's `LICENSE` states that the emulator as a whole is GPL version 2,
with compatible file-specific licenses. Its TCG components have mixed
file-specific licenses. See the
[QEMU licensing documentation](https://www.qemu.org/docs/master/about/license.html)
and the downloaded pinned source's `LICENSE`, `COPYING` and individual files.
GPL-3.0-only repository code is not assumed compatible with the QEMU program.
Copying or linking such implementation code would require resolving the
applicable rights and compatibility first.

The fresh C overlay and Python build/integration/validation scripts carry
`SPDX-License-Identifier: GPL-2.0-or-later`. Their GPL version 2 text is included
in [COPYING.GPL-2.0](COPYING.GPL-2.0). They contain no copied Rust decoder,
interpreter, JIT emitter or peripheral implementation. The Rust reference is a
separate GPL-3.0-only executable connected only by subprocess invocation and
JSON results; it is not linked into QEMU and QEMU does not call it to execute
instructions.

Encoding facts and register numbering were checked against the Apache-2.0
[Quarkslab pi32v2 SLEIGH reference](https://github.com/quarkslab/ghidra-jieli/tree/e1bd0707874b77b759401555d24839ad43af1267),
pinned to commit `e1bd0707874b77b759401555d24839ad43af1267`, particularly
`pi32v2.slaspec`, `stack.sinc`, `arithops.sinc` and `progflow.sinc`. The
implementation is newly written from those encoding facts and the saved
vendor disassemblies; SLEIGH
implementation text is not transplanted into the target.

Guest-visible register addresses, expected behavior and fixture entry points
were investigated from the existing guest assembly, disassembly, tests and
hardware records. These are used as interface/behavioral facts, not as copied
implementation source. QOM, TCG, MemoryRegion, timer and CPU hook integration
was checked against the pinned QEMU headers and existing QEMU target patterns.

The overlay keeps this boundary explicit. A future port should preserve it,
or obtain appropriate permission for reused implementation code before
combining it with QEMU. This prototype makes no blanket licensing claim for
the vendor toolchain, ignored packages or a possible unpublished vendor fork.
