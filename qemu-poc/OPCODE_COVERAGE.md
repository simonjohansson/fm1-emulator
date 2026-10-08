# FM-1 QEMU known-firmware instruction gaps

## Current production after Batch A (2026-10-08)

**64 missing forms / 69 role rows remain.** Batch A admits 12 forms / 13
role rows; exact compact RETS push 04C8 remains deferred. One unsigned maximum
parallel placement was wrongly merged into the original signed-maximum form:
instrumented F434/0100 + 2E01 at 0x0200DBA4. Correct baseline is 76 forms / 82
role rows. Signed maximum is admitted; unsigned mode0 remains an explicit
parallel fault. The published 75/81 baseline below remains frozen history.

| Same 21 executable payloads / 120,836 confirmed sites | Admitted | Rejected |
| --- | ---: | ---: |
| Frozen baseline | 117,088 | 3,748 |
| Accepted Batch A decoder | 117,441 | 3,395 |

353 new sites are admitted, with zero regressions and no admitted width
mismatch. Unchanged Felucca has 414 remaining rejected static sites (was 516).
All 496 rejected width gaps and the 71 opaque E53F regions / 12 complete raw
patterns remain. Static coverage establishes no runtime reachability,
frequency, performance or universal compatibility.

Production translator SHA-256:
`81e7caef8f6d1e2bb9e20d792e7f1d581ea66fb2f33d578d8e40b4e6dd8147d5`.
Fresh exact-C extraction, 20 sanity checks, original-input replays and
independent correction review: main repo `.deps/qemu-batch-a-2026-10-08/`.
Focused/full regression acceptance and unchanged/default-loader boot are
separate runtime evidence. Latest stop is E99C/8D00 at 0x0200a2fa; HOME and
nonzero audio remain unverified.

## Frozen pre-batch inventory

Reviewed offline on 2026-10-08 against signed CPU commit `f9f80c0842a9a972f9ecd76c1b6ebc8ea66d9e24` (QEMU 11.1.2).

The saved-artifact corpus has **75 identified missing encoding/placement forms**: **68 scalar forms** and **7 additional parallel placements** of operations already admitted in scalar form. The core firmware set accounts for 61 forms; the supplemental builds add 14. Unchanged Felucca uses 60 of the missing forms at 516 static sites.

There are also **71 unresolved E53F code regions**, containing **12 distinct complete byte patterns**. They are kept outside the confirmed instruction/form counts. Their saved disassembly splits probable instructions into unknown halfwords and misleading compact operations. Twelve byte patterns are not twelve established ISA forms; these need a grouped decoding/specification investigation.

A form retains operation, width, signedness, comparison, addressing/update mode and distinct encoding. Different register numbers, constants and branch displacements are instances of a form. Scalar/head/tail rows retain placements without counting a scalar form twice when it also needs parallel admission. Destination conflicts remain separate model-policy rejections.

## Corpus and admission comparison

The manifest contains 37 artifact paths and 35 distinct ELF artifacts, containing 21 distinct executable-section byte/VMA payloads. Those payloads have 120,836 confirmed instruction sites, of which 3,748 are rejected by the current decoder/classifier: 3,747 form-gap sites and one destination-conflict policy site. All 17 loader contexts have identical executable contents even when their ELF metadata differs. They count once in these code totals. Exact executable identity is not whole-image or runtime identity. The table retains every path context so duplicates are auditable.

| Artifact context | Static sites | Decoder admits | Decoder rejects | ELF SHA-256 prefix |
| --- | ---: | ---: | ---: | --- |
| felucca | 24,395 | 23,879 | 516 | `9404c41dcd5c` |
| diagnostic | 4,329 | 4,155 | 174 | `aaa5c1bfaf3f` |
| display | 5,183 | 5,008 | 175 | `91b0d497dff0` |
| foundation | 197 | 197 | 0 | `31a73717fef8` |
| display-fixture | 1,031 | 1,031 | 0 | `929077b8a5e3` |
| probe | 50 | 50 | 0 | `0c6b78f995df` |
| diagnostic-loader | 2,361 | 2,322 | 39 | `95b0eb9d0cf5` |
| display-loader | 2,361 | 2,322 | 39 | `95b0eb9d0cf5` |
| trial-arithmetic | 4,521 | 4,333 | 188 | `469c6c901d6d` |
| trial-arithmetic-loader | 2,361 | 2,322 | 39 | `ae17d16b6e5c` |
| trial-atomic | 4,738 | 4,538 | 200 | `d4f835a211b4` |
| trial-atomic-loader | 2,361 | 2,322 | 39 | `e0dce7decc24` |
| trial-bt-indirect | 4,524 | 4,347 | 177 | `e675f9a59f31` |
| trial-bt-indirect-loader | 2,361 | 2,322 | 39 | `7c290064c08e` |
| trial-float-branch | 4,539 | 4,349 | 190 | `9e5db809373d` |
| trial-float-branch-loader | 2,361 | 2,322 | 39 | `b2d279e6b40a` |
| trial-float-probe | 4,876 | 4,701 | 175 | `328f50136869` |
| trial-float-probe-loader | 2,361 | 2,322 | 39 | `3df8943bfca3` |
| trial-idle | 4,468 | 4,294 | 174 | `950e116b3a5e` |
| trial-idle-loader | 2,361 | 2,322 | 39 | `a4310cd41107` |
| trial-irq-context | 4,487 | 4,312 | 175 | `594fae062587` |
| trial-irq-context-loader | 2,361 | 2,322 | 39 | `02bec4861932` |
| trial-predicate-irq | 4,434 | 4,260 | 174 | `00948072298f` |
| trial-predicate-irq-loader | 2,361 | 2,322 | 39 | `c3da477a1e45` |
| trial-repeat | 4,490 | 4,296 | 194 | `789d5f64e489` |
| trial-repeat-loader | 2,361 | 2,322 | 39 | `700f9286351d` |
| trial-repeat-irq | 4,426 | 4,252 | 174 | `a12e80f80345` |
| trial-repeat-irq-loader | 2,361 | 2,322 | 39 | `ec9fe519dda4` |
| trial-rf-probe | 4,537 | 4,362 | 175 | `832f0ae4503b` |
| trial-rf-probe-loader | 2,361 | 2,322 | 39 | `db7b4fa41b55` |
| trial-temperature | 4,420 | 4,247 | 173 | `7b5594aa3ecd` |
| trial-temperature-loader | 2,361 | 2,322 | 39 | `1348a5211eea` |
| trial-usb-io | 4,377 | 4,203 | 174 | `0680b9a836a5` |
| trial-usb-io-loader | 2,361 | 2,322 | 39 | `b31f2109b65b` |
| felucca-profile-instrumented | 24,453 | 23,952 | 501 | `50cb2606936d` |
| felucca-profile-loader | 2,361 | 2,322 | 39 | `95b0eb9d0cf5` |
| felucca-loader | 2,361 | 2,322 | 39 | `4fc238ea1987` |

Inputs are existing ELF and vendor-disassembly pairs from the Felucca build, main diagnostic/display/probe/foundation builds, saved diagnostic variants, their loaders and the instrumented Felucca profile build. The table counts confirmed code sites; the 71 opaque regions remain separately retained. The profile image is useful for instruction inventory and is excluded from unchanged-firmware acceptance. Artifact hashes establish identity, not correspondence to current source or a reproducible build.

Unknown-source stock raw ELF wrappers and SDK ELF files without a matching disassembly are outside this source-known corpus. Stock/package/ROM boot compatibility remains a separate planned gate. New compiler configurations can produce additional instructions.

## Method and limits

The parser compares saved vendor bytes with executable ELF sections, uses sized function/object symbols, preserves untyped assembler rows for audit, and pairs genuine parallel prefixes with their adjacent tails. It excludes proven literal tables, objects, raw dumps and the independently checked display font/label data. Disassembling every byte in `.text` would count large embedded data regions as instructions.

The supplemental audit added the saved `testset`, `ifeq` and `rep` syntaxes that the initial recognizer omitted. Sixteen floating-condition branch sites use a saved alternate disassembly whose four-byte rows match ELF bytes exactly: eight `iff >` and eight `iff u<=`. The alternate spelling and authority limits remain explicit; architectural semantics are not established by disassembly. The 142 raw rows surrounding the 71 E53F starts are quarantined together, including their apparent operand instructions. Initial parser outputs and every correction are preserved.

The host-only C harness extracts the production `decode_operation`, `parallel_writes`, `operation_size` and `instruction_end` functions exactly, including ordered matches, operand fetches and C guards. TCG/runtime emission is stubbed. Twenty retained host sanity cases exercise scalar admission, guards, bundles, conflicts and emitted runtime checks. The inventory neither runs QEMU nor calls the copied reference emulator.

**Admission is not runtime correctness.** This analysis does not execute arithmetic, flags, memory accesses, predicate completion, IRQ delivery or device effects. Static occurrence counts do not show reachability, execution frequency, throughput or universal ISA support. Existing runtime helper and alias policies can still stop an admitted instruction.

The union has 720 context-level sizing mismatches (496 after executable payload deduplication): rejected six-byte branches currently modeled as four bytes. They are the same missing branch forms, not extra instructions. Their batch must update exact decoder and scanner widths together. The review receipt retains each mismatch and the parser audits.

The existing destination-overlap bundle rejections stay explicit. Both components already admit individually; adding an opcode cannot resolve their architectural ordering policy.

Pinned primary descriptions disagree with vendor wide-shift direction and wide-multiply signedness selectors. These variants remain distinct in the inventory, with the disagreement recorded. Their batch needs discriminating runtime evidence before semantics are selected. Specialized controls require evidenced effects; unsupported operations must not be admitted as arbitrary no-ops.

## Missing forms and placements

The following 81 role rows account for 75 distinct missing forms. Exact operand values, first-word matches, source branches, widths, image counts, authority qualifications and example addresses remain in the machine-readable mapping.

| Form | Role | Unique executable-payload sites | Felucca sites | Signature | Intent |
| --- | --- | ---: | ---: | --- | --- |
| `compact-register-asr-1a88` | scalar | 5 | 3 | `(op&FF88)==1A88` | Compact arithmetic right shift by register |
| `register-add-carry-e0b8-mode0` | scalar | 3 | 0 | `op==E0B8` | Register add with carry |
| `register-subtract-borrow-e0b8-mode2` | scalar | 4 | 0 | `op==E0B8` | Register subtract with inverted carry |
| `subtract-packed-e0f0` | head | 15 | 7 | `(op&FFF0)==E0F0` | Register minus packed immediate |
| `subtract-packed-e0f0` | scalar | 247 | 64 | `(op&FFF0)==E0F0` | Register minus packed immediate |
| `word-bitmap-store-eb20` | scalar | 6 | 2 | `(op&FFF0)==EB20` | Register-bitmap word store |
| `flag-equal-branch-e840` | scalar | 12 | 0 | `op==E840` | Branch on equal status flag |
| `register-mask-zero-branch-fa00` | scalar | 7 | 3 | `(op&FF00)==FA00` | Branch when register AND is zero |
| `long-literal-branch-ff0b` | scalar | 9 | 3 | `op==FF0B` | Long signed literal LT branch |
| `long-literal-branch-ff0d` | scalar | 17 | 1 | `op==FF0D` | Long signed literal LE branch |
| `long-packed-branch-ff20` | scalar | 55 | 3 | `op==FF20` | Long unsigned packed EQ branch |
| `long-packed-branch-ff21` | scalar | 7 | 3 | `op==FF21` | Long unsigned packed NE branch |
| `long-packed-branch-ff23` | scalar | 81 | 10 | `op==FF23` | Long unsigned packed LT branch |
| `long-packed-branch-ff28` | scalar | 176 | 20 | `op==FF28` | Long unsigned packed GT branch |
| `long-packed-branch-ff29` | scalar | 4 | 2 | `op==FF29` | Long unsigned packed LE branch |
| `long-packed-branch-ff2a` | scalar | 36 | 3 | `op==FF2A` | Long signed packed GE branch |
| `long-packed-branch-ff2b` | scalar | 24 | 4 | `op==FF2B` | Long signed packed LT branch |
| `long-packed-branch-ff2d` | scalar | 6 | 3 | `op==FF2D` | Long signed packed LE branch |
| `long-register-branch-ff40` | scalar | 28 | 7 | `op==FF40` | Long unsigned register EQ branch |
| `long-register-branch-ff42` | scalar | 12 | 5 | `op==FF42` | Long unsigned register GE branch |
| `long-register-branch-ff43` | scalar | 35 | 3 | `op==FF43` | Long unsigned register LT branch |
| `long-register-branch-ff48` | scalar | 4 | 0 | `op==FF48` | Long unsigned register GT branch |
| `long-register-branch-ff4a` | scalar | 2 | 1 | `op==FF4A` | Long signed register GE branch |
| `idle-0001` | scalar | 1 | 0 | `op==0001` | IDLE |
| `ssync-0022` | scalar | 53 | 3 | `op==0022` | System synchronization |
| `trigger-e870` | scalar | 17 | 1 | `op==E870` | Trigger control operation |
| `float-register-branch-ee02` | scalar | 8 | 0 | `op==EE02` | Floating register branch GT |
| `float-register-branch-ee82` | scalar | 8 | 0 | `op==EE82` | Floating register branch u<= (alternate spelling) |
| `compact-half-post-store-0680` | tail | 2 | 1 | `(op&FF88)==0680` | Compact halfword post-index store |
| `parallel-divide-unsigned-mode0` | head | 14 | 6 | `op==E1F4` | Unsigned division |
| `parallel-packed-minus-register-e0a0` | head | 27 | 14 | `(op&FFF0)==E0A0` | Packed immediate minus register |
| `parallel-packed-xor-e150` | head | 3 | 2 | `(op&FFF0)==E150` | Packed immediate XOR |
| `parallel-smax-mode1` | head | 5 | 2 | `op==E434` | Signed maximum |
| `parallel-umin-mode0` | head | 22 | 2 | `op==E435` | Unsigned minimum |
| `rev8-e070` | head | 2 | 1 | `op==E070` | Byte reversal (REV8) |
| `byte-signed-offset-ee51` | scalar | 32 | 15 | `op==EE51` | Unsigned byte load signed offset |
| `byte-signed-offset-ee53` | scalar | 2 | 1 | `op==EE53` | Byte store signed offset |
| `half-signed-sp-load-e9d9` | scalar | 2 | 1 | `op==E9D9` | Signed SP-relative halfword load |
| `half-signed-unscaled-index-edd8-kind2` | scalar | 8 | 4 | `op==EDD8` | Signed halfword unscaled register-index load |
| `if-e920-packed` | scalar | 6 | 3 | `(op&FFF0)==E920` | Unsigned packed GE IF |
| `if-e990` | scalar | 8 | 4 | `(op&FFF0)==E990` | Unsigned register LT IF |
| `if-ec20-packed` | scalar | 17 | 1 | `(op&FFF0)==EC20` | Unsigned packed GT IF |
| `if-ec30-immediate` | scalar | 1 | 0 | `(op&FFF0)==EC30` | Unsigned immediate GT IF |
| `if-ec90` | scalar | 36 | 10 | `(op&FFF0)==EC90` | Unsigned register LE IF |
| `if-eca0-packed` | scalar | 4 | 2 | `(op&FFF0)==ECA0` | Unsigned packed LE IF |
| `if-ed20-packed` | scalar | 26 | 13 | `(op&FFF0)==ED20` | Signed packed GE IF |
| `if-ee30-literal` | scalar | 18 | 1 | `(op&FFF0)==EE30` | Signed literal GT IF |
| `register-ne-zero-if-e8a0` | scalar | 1 | 0 | `(op&FFF0)==E8A0` | Register nonzero IF |
| `repeat-immediate-count-8000` | scalar | 2 | 0 | `(op&E00F)==8000` | Repeat block with immediate count |
| `repeat-register-count-0300` | scalar | 20 | 0 | `(op&FF00)==0300` | Repeat block with register count |
| `byte-testset-00b0` | scalar | 12 | 0 | `(op&FFF0)==00B0` | Byte test-and-set |
| `word-memory-add-e868` | scalar | 130 | 49 | `op==E868` | Word memory add with GPR operand |
| `word-memory-subtract-e868` | scalar | 17 | 8 | `op==E868` | Word memory subtract with GPR operand |
| `compact-push-rets` | scalar | 2176 | 128 | `op==04C8` | Compact RETS push |
| `extended-push-special-map` | scalar | 17 | 1 | `op==E958` | Special-register bitmap push |
| `special-sp-immediate-add-e8f0` | scalar | 5 | 0 | `op==E8F0` | Special SP immediate addition |
| `tbb-0100` | scalar | 18 | 9 | `(op&FFF0)==0100` | Table byte branch |
| `byte-post-load-eed0` | scalar | 21 | 10 | `op==EED0` | Unsigned byte post-index load |
| `byte-pre-store-eedc-kind1` | scalar | 38 | 3 | `op==EEDC` | Byte register pre-index store |
| `compact-byte-post-decrement-load-0708` | scalar | 1 | 0 | `(op&FF88)==0708` | Compact unsigned byte post-decrement load |
| `compact-half-post-load-0600` | scalar | 1 | 1 | `(op&FF88)==0600` | Compact unsigned halfword post-index load |
| `compact-half-post-load-0600` | tail | 1 | 0 | `(op&FF88)==0600` | Compact unsigned halfword post-index load |
| `doubleword-pre-store-ec50-kind3` | scalar | 2 | 1 | `(op&FFF8)==EC50` | Doubleword immediate pre-index store |
| `half-post-store-edd0` | scalar | 2 | 1 | `op==EDD0` | Halfword immediate post-index store |
| `half-pre-store-ed58-family` | scalar | 18 | 1 | `(op&FFFC)==ED58` | Halfword immediate pre-index store |
| `half-signed-post-load-edd4` | scalar | 6 | 3 | `op==EDD4` | Signed halfword immediate post-index load |
| `half-signed-pre-load-ed5c` | scalar | 2 | 1 | `op==ED5C` | Signed halfword immediate pre-index load |
| `word-post-load-ecd8` | scalar | 6 | 3 | `op==ECD8` | Word immediate post-index load |
| `word-pre-store-ecd0-kind3` | scalar | 5 | 2 | `(op&FFF8)==ECD0` | Word immediate pre-index store |
| `wide-arithmetic-right-shift-e1d0` | scalar | 6 | 3 | `op==E1D0` | Register-pair arithmetic-right immediate shift (vendor intent) |
| `wide-left-register-shift-e1d8` | scalar | 7 | 0 | `op==E1D8` | Register-pair left shift by register |
| `wide-left-shift-e1d0` | scalar | 4 | 2 | `op==E1D0` | Register-pair left immediate shift (vendor intent) |
| `wide-logical-right-shift-e1d0` | head | 4 | 2 | `op==E1D0` | Register-pair logical-right immediate shift (vendor intent) |
| `wide-logical-right-shift-e1d0` | scalar | 46 | 23 | `op==E1D0` | Register-pair logical-right immediate shift (vendor intent) |
| `wide-signed-divide-e1f6` | scalar | 2 | 1 | `op==E1F6` | Signed register-pair division |
| `wide-signed-muladd-e1fc` | head | 3 | 2 | `op==E1FC` | Signed multiply-accumulate register pair |
| `wide-signed-muladd-e1fc` | scalar | 23 | 11 | `op==E1FC` | Signed multiply-accumulate register pair |
| `wide-signed-multiply-e1f8` | head | 2 | 1 | `op==E1F8` | Signed multiply to register pair (vendor intent) |
| `wide-signed-multiply-e1f8` | scalar | 10 | 5 | `op==E1F8` | Signed multiply to register pair (vendor intent) |
| `wide-unsigned-multiply-e1f8` | head | 2 | 1 | `op==E1F8` | Unsigned multiply to register pair (vendor intent) |
| `wide-unsigned-multiply-e1f8` | scalar | 20 | 10 | `op==E1F8` | Unsigned multiply to register pair (vendor intent) |

## Batch implementation and acceptance

Use the batch membership and ordering in [plan.md](../plan.md). The main agent remains Astra high; workers and independent reviewers use Sol 6.1 Extra high. Workers prepare private patches and focused validators for nonoverlapping forms. The parent owns shared-file integration, builds, firmware runs, documentation and signed commits.

For each batch: collect one finite primary/copied-reference evidence matrix, retain raw disagreements, independently review the combined source and per-form validators, integrate once, build once, run focused and relevant broad regression gates, then run unchanged bounded Felucca and renamed generic replay once. Fix any failing gate before accepting the batch. Recompute the offline inventory from the accepted production decoder and publish static admission separately from tested runtime behavior.

This removes the repeated full build/regression/boot cycle after each individual opcode. Focused semantic checks remain necessary. Keep CPU behavior generic: no firmware names, hashes or PCs select instruction semantics. The CPU, reusable SoC/board hardware and macOS host retain their separate responsibilities.

CPU admission alone cannot complete the boot: MMIO, ROM services, loader contracts, peripheral timing and runtime policies remain independent possible blockers. The acceptance goal stays completed HOME, 30 guest seconds, physical-input effects and a native viewer left executing, then broader unchanged firmware and macOS host acceptance.

## Reproducible evidence

Full evidence lives at `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-opcode-coverage-2026-10-08`. Core inputs/outputs remain frozen; `expanded/` contains the complete union. Manifests retain full ELF/disassembly paths and SHA-256 identities. `sites.jsonl`, `requests.txt`, admission outputs, rejected-site records and parser exclusions preserve every counted site. `harness/` retains the exact C extraction, host runner, provenance and sanity checks; the review directory retains normalized forms and receipts. `expanded/unknown-code-regions.json` retains the unresolved sequences. The report does not assign a final total ISA-gap count to those sequences.

Production translator SHA-256:

`9a83e582471872649173199a0e6307171eceddafb885fc204a6546a7c323472b`

Accepted QEMU binary SHA-256:

`2a05c3ae0571a81b59745dc8d15adefa60a1b88c83750deda60785e6026fc817`

This milestone changes documentation and saved analysis only. Firmware artifacts, production CPU/device behavior, public capture formats and the Rust emulator are unchanged. No firmware rebuild or flashing occurred.
