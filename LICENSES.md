# Licensing and provenance

The maintained C sources and build/test scripts are **GPL-2.0-or-later**, as
marked in their SPDX headers. The GPL version 2 text is in [LICENSE](LICENSE).

The executable incorporates QEMU 11.1.2. QEMU as a whole is GPL version 2;
individual components, including TCG, carry compatible file-specific licenses.
See the downloaded source's `LICENSE`, `COPYING`, and individual files, and
[QEMU's licensing documentation](https://www.qemu.org/docs/master/about/license.html).
The macOS build also links GLib/gettext (LGPL) and PCRE2 (BSD); retain their
notices and meet their source/relinking obligations when redistributing.
An executable archive alone is not a complete corresponding-source offer.

The pi32v2 implementation was written independently from encoding and interface
facts, checked against the Apache-2.0
[Quarkslab SLEIGH reference](https://github.com/quarkslab/ghidra-jieli/tree/e1bd0707874b77b759401555d24839ad43af1267)
(commit `e1bd0707874b77b759401555d24839ad43af1267`) and saved disassemblies.
SLEIGH implementation text was not transplanted into this target.

The retired GPL-3.0-only Rust emulator and firmware tooling remain in Git
history under their original licenses. Their implementation was not copied
or linked into QEMU. Firmware is loaded as runtime data and is not included;
its original license and provenance still apply. Vendor SDKs and toolchains
are not redistributed.

The package decoder was written independently from the documented
[JieLi file formats](https://kagaimiq.github.io/jielie/datafmt/newfw.html),
[JLFS layout](https://kagaimiq.github.io/jielie/datafmt/jlfs.html), and
[cipher description](https://kagaimiq.github.io/jielie/misc/cipher.html).
The MIT-licensed jl-misctools supplied format evidence; its implementation
was not copied. Panel geometry follows the manufacturer’s FM-1 product photo;
the native drawing code uses no copied bitmap assets.
