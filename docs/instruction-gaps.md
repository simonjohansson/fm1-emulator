# Instruction gaps

Generated on 2026-10-09 by `tools/decode_sweep.py`; do not edit by hand.

Every instruction the JieLi objdump decodes in each image was run through the
emulator's own decoder (`src/target/pi32v2/translate.c`, compiled unchanged
against no-op TCG stand-ins in `tools/decode-sweep/`). A form listed here
faults as `unsupported instruction` when executed.

```sh
JIELI_TOOLCHAIN=/path/to/jieli-linux-toolchains \
  mise exec python@3.13.15 -- python tools/decode_sweep.py FIRMWARE...
```

## Reading the lists

- **Opcode** is the halfword the decoder rejects: the instruction's first
  halfword, or the failing half of a parallel instruction.
- **Reachable** counts occurrences found by following calls, branches and
  fallthrough from the entry point and from code pointers stored in the image.
  Register jumps through tables are not followed, so a gap outside reachable
  code may still run; an entry with no reachable occurrence is more likely
  data that the linear disassembly decoded as instructions.
- **Vendor disassembly** is the JieLi objdump's reading. It names operands and
  their order, sign and width, which is usually the main fact needed.
- **Accepted forms with the same top 12 bits** are siblings the emulator
  already implements in this image, often the same family with another mode.

## What an entry does not settle

The vendor text rarely states:

- the exact field layout, best confirmed by comparing several operand values
  of the same opcode (the per-opcode tables show distinct encodings);
- which PSR flags the operation sets, and edge cases such as shift counts of
  32 or more, division by zero or signed overflow;
- the order of register writeback and memory access when the base register
  is also the destination or source;
- whether the form may appear as either half of a parallel instruction.

Where these matter and examples disagree or are missing, the evidence has come
from more vendor examples, the Quarkslab SLEIGH reference, or a measurement on
an FM-1. Forms rejected on purpose (for example unresolved register aliases)
also appear here when firmware contains them.

## FM-1

`FM-1.fwsc`, application SHA-256 `91f1aa3f18ecbe02...`, 1,015,018 bytes, 218,185 decoded instructions of which 76,667 statically reachable.

2986 rejected opcodes (14874 occurrences), 135 of them in statically reachable code.

| Opcode | Occurrences | Reachable | Example | Vendor disassembly |
| --- | --- | --- | --- | --- |
| `0040` | 495 | 320 | `02000834` | `lockclr` |
| `0041` | 346 | 215 | `02000804` | `lockset` |
| `e840` | 78 | 57 | `0200351e` | `ifeq goto -6 <_start+0x33FC : 200351c >` |
| `8a10` | 42 | 36 | `0201db5c` | `rep 4 11 {` |
| `9f10` | 37 | 35 | `0202a350` | `rep 4 32 {` |
| `8510` | 78 | 26 | `0202d474` | `rep 4 6 {` |
| `00b0` | 99 | 25 | `0200351c` | `testset b[r0]` |
| `8f10` | 45 | 18 | `02000a34` | `rep 4 16 {` |
| `8310` | 82 | 16 | `02006738` | `rep 4 4 {` |
| `8710` | 70 | 16 | `0201dd50` | `rep 4 8 {` |
| `00b1` | 21 | 13 | `020037f0` | `testset b[r1]` |
| `8910` | 20 | 12 | `02007c62` | `rep 4 10 {` |
| `ed53` | 16 | 12 | `02041d66` | `h[r3+-4] = r13` |
| `8810` | 20 | 11 | `02007c4e` | `rep 4 9 {` |
| `e1c4` | 11 | 11 | `0201eaa8` | `r0 = r0 <> 1` |
| `8b10` | 16 | 9 | `02005aa2` | `rep 4 12 {` |
| `e180` | 9 | 9 | `020448f0` | `r0 = clz(r0)` |
| `ee59` | 9 | 9 | `0202c580` | `r4 = b[++r2=-1] (u)` |
| `e1f6` | 14 | 8 | `020021e4` | `r3_r2 = r1_r0 / r4 (u)` |
| `8210` | 13 | 8 | `0202d560` | `rep 4 3 {` |
| `00e6` | 19 | 7 | `0200dc9e` | `cli r6` |
| `8400` | 19 | 7 | `0202adf0` | `rep 2 5 {` |
| `8a00` | 16 | 7 | `0202c5d8` | `rep 2 11 {` |
| `edd8` | 11 | 7 | `020278d0` | `h[r3+r4] = r5` |
| `9f20` | 7 | 7 | `02028a82` | `rep 6 32 {` |
| `9f00` | 77 | 6 | `02027b42` | `rep 2 32 {` |
| `e1d8` | 7 | 6 | `02044a56` | `r13_r12 >>= r6` |
| `8f00` | 78 | 5 | `02027a3e` | `rep 2 16 {` |
| `e1fc` | 14 | 5 | `02044f5c` | `r11_r10 += r6 * r14 (u)` |
| `00ba` | 12 | 5 | `0203f4f6` | `testset b[r10]` |
| `8900` | 9 | 5 | `0202a67c` | `rep 2 10 {` |
| `9a10` | 9 | 5 | `0201e202` | `rep 4 27 {` |
| `e8d5` | 6 | 5 | `02019264` | `{pc, r13, r12, r10-r4} = [sp++]` |
| `e8d9` | 6 | 5 | `020191a2` | `[--sp] = {rets, r13, r12, r10-r4}` |
| `ee5b` | 5 | 5 | `02019740` | `b[++r2=-1] = r3` |
| `00b4` | 13 | 4 | `020042f8` | `testset b[r4]` |
| `8610` | 13 | 4 | `02007cbe` | `rep 4 7 {` |
| `8410` | 10 | 4 | `02000a4c` | `rep 4 5 {` |
| `8c00` | 5 | 4 | `0202e320` | `rep 2 13 {` |
| `0b0b` | 19 | 3 | `02019b82` | `[r0++=r14] = r3` |
| `00b3` | 12 | 3 | `02003caa` | `testset b[r3]` |
| `8600` | 11 | 3 | `0205f0f6` | `rep 2 7 {` |
| `8500` | 10 | 3 | `0201e232` | `rep 2 6 {` |
| `9300` | 10 | 3 | `02005af0` | `rep 2 20 {` |
| `0c21` | 9 | 3 | `020326c2` | `r1 = h[r2++=r8] (u)` |
| `8e00` | 6 | 3 | `0202c768` | `rep 2 15 {` |
| `ff4d` | 6 | 3 | `0200abba` | `ifs (r0 <= r1) goto -1248 <_start+0xA5C0 : 200a6e0 >` |
| `9600` | 4 | 3 | `020287e4` | `rep 2 23 {` |
| `e870` | 4 | 3 | `0201f6ba` | `trigger` |
| `8a20` | 3 | 3 | `0202d2e8` | `rep 6 11 {` |
| `0f0b` | 24 | 2 | `02019b88` | `h[r0++=r14] = r3` |
| `8800` | 13 | 2 | `02034ad4` | `rep 2 9 {` |
| `9a00` | 9 | 2 | `02020ac2` | `rep 2 27 {` |
| `00bd` | 7 | 2 | `020666a6` | `testset b[r13]` |
| `0a03` | 7 | 2 | `0207d0f2` | `r3 = [r0++=r12]` |
| `0c03` | 7 | 2 | `020625d0` | `r3 = h[r0++=r8] (u)` |
| `079c` | 5 | 2 | `0207cb2e` | `b[r1++=-1] = r4` |
| `0c0a` | 5 | 2 | `020109cc` | `h[r0++=r8] = r2` |
| `9d10` | 4 | 2 | `0202c6a0` | `rep 4 30 {` |
| `0609` | 3 | 2 | `0207f74e` | `r1 = h[r0++=-2] (u)` |
| `8820` | 3 | 2 | `02007ca4` | `rep 6 9 {` |
| `ed90` | 3 | 2 | `02018fce` | `ifs (r0 < r2) {` |
| `9010` | 2 | 2 | `0202a454` | `rep 4 17 {` |
| `f1c4` | 2 | 2 | `0202c94c` | `r2 = r15 <> 3  #` |
| `002e` | 35 | 1 | `02079992` | `ssync` |
| `00be` | 15 | 1 | `0203d95c` | `testset b[r14]` |
| `0c62` | 15 | 1 | `020326ba` | `r2 = h[r6++=r8] (u)` |
| `00b9` | 14 | 1 | `0203ed4e` | `testset b[r9]` |
| `0f05` | 14 | 1 | `0201eab2` | `r5 = h[r0++=r14] (u)` |
| `00d6` | 13 | 1 | `020001b6` | `goto r6` |
| `ff4c` | 13 | 1 | `02010484` | `ifs (r5 > r2) goto 624 <_start+0x105DA : 20106fa >` |
| `00b7` | 11 | 1 | `0203e008` | `testset b[r7]` |
| `00b8` | 11 | 1 | `020389aa` | `testset b[r8]` |
| `0f02` | 11 | 1 | `0202bb66` | `r2 = h[r0++=r14] (u)` |
| `ff4b` | 11 | 1 | `0201048e` | `ifs (r3 < r2) goto 614 <_start+0x105DA : 20106fa >` |
| `00b2` | 10 | 1 | `02003e36` | `testset b[r2]` |
| `00dd` | 10 | 1 | `0203d71a` | `goto r13` |
| `00bc` | 9 | 1 | `0203f1f6` | `testset b[r12]` |
| `009d` | 8 | 1 | `02064636` | `callns r13` |
| `00a3` | 8 | 1 | `0201eafc` | `swi 3` |
| `00b6` | 8 | 1 | `0203f692` | `testset b[r6]` |
| `0c09` | 7 | 1 | `020832ac` | `h[r0++=r8] = r1` |
| `0808` | 6 | 1 | `02061b9a` | `[r0++=r8] = r0` |
| `0806` | 5 | 1 | `020109ca` | `r6 = [r0++=r8]` |
| `0b60` | 5 | 1 | `02019b8c` | `r0 = [r6++=r14]` |
| `1206` | 5 | 1 | `020109c8` | `r6 = b[r0++=r12] (u)` |
| `8700` | 5 | 1 | `020296aa` | `rep 2 8 {` |
| `078a` | 4 | 1 | `0207efda` | `b[r0++=-1] = r2` |
| `0a08` | 4 | 1 | `02040df0` | `[r0++=r12] = r0` |
| `0e21` | 4 | 1 | `02061ba4` | `r1 = h[r2++=r12] (u)` |
| `0f35` | 4 | 1 | `0201f924` | `r5 = h[r3++=r14] (u)` |
| `8320` | 4 | 1 | `02056cc2` | `rep 6 4 {` |
| `9000` | 4 | 1 | `0202a566` | `rep 2 17 {` |
| `020d` | 3 | 1 | `0202c95c` | `pfetch [r13]` |
| `13c0` | 3 | 1 | `0200de98` | `r0 = b[r4++=r15] (u)` |
| `8300` | 3 | 1 | `02002118` | `rep 2 4 {` |
| `9500` | 3 | 1 | `0202d9d4` | `rep 2 22 {` |
| `9800` | 3 | 1 | `02036274` | `rep 2 25 {` |
| `e86c` | 3 | 1 | `02038f8e` | `[r0+0] >>= 6` |
| `ff22` | 3 | 1 | `0203a308` | `if (r6 >= 65280) goto 6 <_start+0x3A1F4 : 203a314 >` |
| `062c` | 2 | 1 | `0207f0e6` | `r4 = h[r2++=-2] (u)` |
| `079b` | 2 | 1 | `0201948a` | `b[r1++=-1] = r3` |
| `1a1d` | 2 | 1 | `0201eaba` | `r5 <<<= r1` |
| `9400` | 2 | 1 | `0202da70` | `rep 2 21 {` |
| `9510` | 2 | 1 | `02063244` | `rep 4 22 {` |
| `9d00` | 2 | 1 | `02077f36` | `rep 2 30 {` |
| `e820` | 2 | 1 | `020795b0` | `if (r0 == 4096) {` |
| `ed96` | 2 | 1 | `02018330` | `ifs (r6 < r5) {` |
| `f1f6` | 2 | 1 | `0205c580` | `r7_r6 = r9_r8 / r1 (u)  #` |
| `0569` | 1 | 1 | `02080dfa` | `r1 = [r6++=-4]` |
| `056b` | 1 | 1 | `0200b2a8` | `r3 = [r6++=-4]` |
| `061b` | 1 | 1 | `0204068a` | `r3 = h[r1++=-2] (u)` |
| `080e` | 1 | 1 | `020109ce` | `[r0++=r8] = r6` |
| `0905` | 1 | 1 | `0202d93a` | `r5 = [r0++=r10]` |
| `094e` | 1 | 1 | `0207f74c` | `[r4++=r10] = r6` |
| `0b27` | 1 | 1 | `02019b80` | `r7 = [r2++=r14]` |
| `0b6b` | 1 | 1 | `02019b8e` | `[r6++=r14] = r3` |
| `0e61` | 1 | 1 | `02061ba2` | `r1 = h[r6++=r12] (u)` |
| `0f1b` | 1 | 1 | `0201f958` | `h[r1++=r14] = r3` |
| `100e` | 1 | 1 | `0202d93c` | `b[r0++=r8] = r6` |
| `1113` | 1 | 1 | `0202c95e` | `r3 = b[r1++=r10] (u)` |
| `120f` | 1 | 1 | `02017744` | `b[r0++=r12] = r7` |
| `8520` | 1 | 1 | `0205faa6` | `rep 6 6 {` |
| `8530` | 1 | 1 | `02002792` | `rep 8 6 {` |
| `8b20` | 1 | 1 | `0202d002` | `rep 6 12 {` |
| `9610` | 1 | 1 | `0207ff72` | `rep 4 23 {` |
| `a63c` | 1 | 1 | `020645f8` | `r4 = r3 <<< 6` |
| `a93c` | 1 | 1 | `0200827c` | `r4 = r3 <<< 9` |
| `d646` | 1 | 1 | `02019488` | `r6 = r4  #` |
| `ec5c` | 1 | 1 | `0200940e` | `r9_r8 = d[++r1=r0]` |
| `ecd0` | 1 | 1 | `02056b9a` | `[++r0=8] = r0` |
| `ed97` | 1 | 1 | `020182ac` | `ifs (r7 < r6) {` |
| `eda4` | 1 | 1 | `0206d4b0` | `ifs (r4 < 134217728) {` |
| `edd3` | 1 | 1 | `0204068c` | `h[r0++=-4] = r3` |
| `f120` | 1 | 1 | `020006d6` | `r0 = r8 + -6401  #` |

2851 further opcodes (12491 occurrences) appear only outside reachable code, mostly data decoded as instructions; they are not listed.

### `0040`

495 occurrences, 320 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02000834` | yes | `0040` | `lockclr` |

### `0041`

346 occurrences, 215 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02000804` | yes | `0041` | `lockset` |

### `e840`

78 occurrences, 57 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200351e` | yes | `e840 fffd` | `ifeq goto -6 <_start+0x33FC : 200351c >` |

### `8a10`

42 occurrences, 36 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201db5c` | yes | `8a10` | `rep 4 11 {` |

Accepted forms with the same top 12 bits: `8a14` `goto 84 <_start+0x4372 : 2004492 >`; `8a15` `goto 1108 <_start+0x27CDA : 2027dfa >`; `8a1b` `r3 = r1 + 10`.

### `9f10`

37 occurrences, 35 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202a350` | yes | `9f10` | `rep 4 32 {` |

Accepted forms with the same top 12 bits: `9f14` `goto 126 <_start+0x13CD2 : 2013df2 >`; `9f19` `r1 = r1 + 31`.

### `8510`

78 occurrences, 26 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d474` | yes | `8510` | `rep 4 6 {` |

Accepted forms with the same top 12 bits: `8514` `goto 74 <_start+0x14D6 : 20015f6 >`; `8517` `goto -950 <_start+0x7BFC0 : 207c0e0 >`; `8518` `r0 = r1 + 5`; `851a` `r2 = r1 + 5`; `851b` `r3 = r1 + 5`; `851f` `r7 = r1 + 5`.

### `00b0`

99 occurrences, 25 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200351c` | yes | `00b0` | `testset b[r0]` |

### `8f10`

45 occurrences, 18 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02000a34` | yes | `8f10` | `rep 4 16 {` |

Accepted forms with the same top 12 bits: `8f11` `call 94 <_start+0x4A8EA : 204aa0a >`; `8f14` `goto 94 <_start+0x28CF6 : 2028e16 >`; `8f17` `goto -930 <_start+0x52D98 : 2052eb8 >`; `8f18` `r0 = r1 + 15`; `8f1b` `r3 = r1 + 15`.

### `8310`

82 occurrences, 16 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02006738` | yes | `8310` | `rep 4 4 {` |

Accepted forms with the same top 12 bits: `8314` `goto 70 <_start+0x448 : 2000568 >`; `8315` `goto 1094 <_start+0x692B2 : 20693d2 >`; `8318` `r0 = r1 + 3`; `8319` `r1 = r1 + 3`; `831a` `r2 = r1 + 3`; `831c` `r4 = r1 + 3`.

### `8710`

70 occurrences, 16 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201dd50` | yes | `8710` | `rep 4 8 {` |

Accepted forms with the same top 12 bits: `8714` `goto 78 <_start+0x2FE0 : 2003100 >`; `8718` `r0 = r1 + 7`; `871a` `r2 = r1 + 7`; `871c` `r4 = r1 + 7`; `871f` `r7 = r1 + 7`.

### `00b1`

21 occurrences, 13 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020037f0` | yes | `00b1` | `testset b[r1]` |

### `8910`

20 occurrences, 12 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02007c62` | yes | `8910` | `rep 4 10 {` |

Accepted forms with the same top 12 bits: `8914` `goto 82 <_start+0x39D8 : 2003af8 >`; `8915` `goto 1106 <_start+0x14EA4 : 2014fc4 >`; `8917` `goto -942 <_start+0x66CC8 : 2066de8 >`; `891a` `r2 = r1 + 9`; `891c` `r4 = r1 + 9`.

### `ed53`

16 occurrences, 12 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02041d66` | yes | `ed53 df3d` | `h[r3+-4] = r13` |
| `0206d8ca` | yes | `ed53 ff2a` | `r15 = h[r2+-6] (u)` |
| `0206f9b2` | yes | `ed53 0a75` | `h[r7+-92] = r0` |
| `0206f9b6` | yes | `ed53 0a77` | `h[r7+-90] = r0` |
| `0206f9bc` | yes | `ed53 0d71` | `h[r7+-48] = r0` |
| `0206f9c0` | yes | `ed53 0d73` | `h[r7+-46] = r0` |
| `0206f9c8` | yes | `ed53 0977` | `h[r7+-106] = r0` |
| `0206f9d0` | yes | `ed53 0a79` | `h[r7+-88] = r0` |

Accepted forms with the same top 12 bits: `ed50 142b` `h[r2+74] = r1`; `ed51 0550` `r0 = h[r5+336] (u)`; `ed54 3046` `r3 = h[r4+6] (s)`; `ed55 231a` `r2 = h[r1+314] (s)`; `ed57 0f54` `r0 = h[r5+-12] (s)`; `ed58 4409` `h[++r0=72] = r4`.

### `8810`

20 occurrences, 11 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02007c4e` | yes | `8810` | `rep 4 9 {` |

Accepted forms with the same top 12 bits: `8814` `goto 80 <_start+0xAC2 : 2000be2 >`; `8818` `r0 = r1 + 8`; `881a` `r2 = r1 + 8`; `881b` `r3 = r1 + 8`; `881c` `r4 = r1 + 8`; `881e` `r6 = r1 + 8`.

### `e1c4`

11 occurrences, 11 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201eaa8` | yes | `e1c4 0001` | `r0 = r0 <> 1` |
| `020806a6` | yes | `e1c4 6161` | `r6 = r6 <> 17` |
| `020806b0` | yes | `e1c4 7142` | `r7 = r4 <> 18` |
| `020806b6` | yes | `e1c4 4047` | `r4 = r4 <> 7` |
| `020806e0` | yes | `e1c4 70ad` | `r7 = r10 <> 13` |
| `020806e4` | yes | `e1c4 50a2` | `r5 = r10 <> 2` |
| `020806ea` | yes | `e1c4 71a6` | `r7 = r10 <> 22` |
| `02080702` | yes | `e1c4 702b` | `r7 = r2 <> 11` |

Accepted forms with the same top 12 bits: `e1c0 9894` `r9 = r9 >> 4`; `e1c8 3342` `r3 = r4 >> r3`.

### `8b10`

16 occurrences, 9 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02005aa2` | yes | `8b10` | `rep 4 12 {` |

Accepted forms with the same top 12 bits: `8b14` `goto 86 <_start+0x1122 : 2001242 >`; `8b18` `r0 = r1 + 11`; `8b1b` `r3 = r1 + 11`.

### `e180`

9 occurrences, 9 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020448f0` | yes | `e180 0000` | `r0 = clz(r0)` |
| `020448f6` | yes | `e180 0100` | `r0 = clz(r1)` |
| `02044e66` | yes | `e180 1200` | `r1 = clz(r2)` |
| `02044e6c` | yes | `e180 1300` | `r1 = clz(r3)` |
| `02045200` | yes | `e180 4000` | `r4 = clz(r0)` |
| `0204523a` | yes | `e180 2000` | `r2 = clz(r0)` |

### `ee59`

9 occurrences, 9 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202c580` | yes | `ee59 4f2f` | `r4 = b[++r2=-1] (u)` |
| `02040b06` | yes | `ee59 0e4c` | `r0 = b[++r4=-20] (u)` |
| `02040b9a` | yes | `ee59 0ecc` | `r0 = b[++r12=-20] (u)` |
| `020445d2` | yes | `ee59 6f1f` | `r6 = b[++r1=-1] (u)` |
| `0206289a` | yes | `ee59 1f08` | `r1 = b[++r0=-8] (u)` |
| `0207955c` | yes | `ee59 3f1e` | `r3 = b[++r1=-2] (u)` |

Accepted forms with the same top 12 bits: `ee50 0858` `r0 = b[r5+136] (u)`; `ee51 3e3e` `r3 = b[r3+-18] (u)`; `ee52 3200` `b[r0+32] = r3`; `ee53 5d07` `b[r0+-41] = r5`; `ee54 2050` `r2 = b[r5+0] (s)`; `ee55 1f2c` `r1 = b[r2+-4] (s)`.

### `e1f6`

14 occurrences, 8 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020021e4` | yes | `e1f6 2400` | `r3_r2 = r1_r0 / r4 (u)` |
| `02032994` | yes | `e1f6 0040` | `r1_r0 = r5_r4 / r0 (u)` |
| `020337f8` | yes | `e1f6 0120` | `r1_r0 = r3_r2 / r1 (u)` |
| `0203400a` | yes | `e1f6 6200` | `r7_r6 = r1_r0 / r2 (u)` |
| `0205c55a` | yes | `e1f6 2720` | `r3_r2 = r3_r2 / r7 (u)` |
| `02002930` | no | `e1f6 6520` | `r7_r6 = r3_r2 / r5 (u)` |
| `02019510` | no | `e1f6 a360` | `r11_r10 = r7_r6 / r3 (u)` |
| `0201e868` | no | `e1f6 8e00` | `r9_r8 = r1_r0 / r14 (u)` |

Accepted forms with the same top 12 bits: `e1f0 e500` `r14 = r0 * r5`; `e1f4 1510` `r1 = r1 / r5 (u)`; `e1f8 0010` `r1_r0 = r1 * r0 (u)`.

### `8210`

13 occurrences, 8 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d560` | yes | `8210` | `rep 4 3 {` |

Accepted forms with the same top 12 bits: `8214` `goto 68 <_start+0x2CD2 : 2002df2 >`; `8218` `r0 = r1 + 2`; `821a` `r2 = r1 + 2`; `821b` `r3 = r1 + 2`; `821c` `r4 = r1 + 2`; `821d` `r5 = r1 + 2`.

### `00e6`

19 occurrences, 7 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200dc9e` | yes | `00e6` | `cli r6` |

### `8400`

19 occurrences, 7 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202adf0` | yes | `8400` | `rep 2 5 {` |

Accepted forms with the same top 12 bits: `8401` `call 8 <_start+0x58352 : 2058472 >`; `8402` `sp += 16`; `8404` `goto 8 <_start+0x149A : 20015ba >`; `8405` `goto 1032 <_start+0x36042 : 2036162 >`; `8406` `goto -2040 <_start+0x66CD0 : 2066df0 >`; `8408` `r0 = r0 + 4`.

### `8a00`

16 occurrences, 7 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202c5d8` | yes | `8a00` | `rep 2 11 {` |

Accepted forms with the same top 12 bits: `8a02` `sp += 40`; `8a04` `goto 20 <_start+0x3004 : 2003124 >`; `8a05` `goto 1044 <_start+0x13F1E : 201403e >`; `8a07` `goto -1004 <_start+0x78738 : 2078858 >`; `8a09` `r1 = r0 + 10`; `8a0b` `r3 = r0 + 10`.

### `edd8`

11 occurrences, 7 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020278d0` | yes | `edd8 5431` | `h[r3+r4] = r5` |
| `020278e2` | yes | `edd8 3201` | `h[r0+r2] = r3` |
| `0204057e` | yes | `edd8 2541` | `h[r4+r5] = r2` |
| `020406aa` | yes | `edd8 6131` | `h[r3+r1] = r6` |
| `02041dde` | yes | `edd8 2481` | `h[r8+r4] = r2` |
| `02041ec0` | yes | `edd8 34d1` | `h[r13+r4] = r3` |
| `0203ee3c` | no | `edd8 4311` | `h[r1+r3] = r4` |
| `02074862` | no | `edd8 2311` | `h[r1+r3] = r2` |

Accepted forms with the same top 12 bits: `edd0 00d3` `h[r13++=2] = r0`; `edd4 7014` `r7 = h[r1++=4] (s)`; `edd8 3569` `h[r6+r5<<1] = r3`; `eddc 1100` `r1 = h[++r0=r1] (u)`.

### `9f20`

7 occurrences, 7 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02028a82` | yes | `9f20` | `rep 6 32 {` |

Accepted forms with the same top 12 bits: `9f24` `goto 190 <_start+0x1616 : 2001736 >`.

### `9f00`

77 occurrences, 6 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02027b42` | yes | `9f00` | `rep 2 32 {` |

Accepted forms with the same top 12 bits: `9f01` `call 62 <_start+0x45AF6 : 2045c16 >`; `9f02` `sp += 124`; `9f04` `goto 62 <_start+0x1C46 : 2001d66 >`; `9f05` `goto 1086 <_start+0x66CD0 : 2066df0 >`; `9f06` `goto -1986 <_start+0x463FC : 204651c >`; `9f07` `goto -962 <_start+0x7BFC0 : 207c0e0 >`.

### `e1d8`

7 occurrences, 6 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02044a56` | yes | `e1d8 c602` | `r13_r12 >>= r6` |
| `02044b38` | yes | `e1d8 8402` | `r9_r8 >>= r4` |
| `020451da` | yes | `e1d8 4202` | `r5_r4 >>= r2` |
| `02045462` | yes | `e1d8 8002` | `r9_r8 >>= r0` |
| `02045492` | yes | `e1d8 6002` | `r7_r6 >>= r0` |
| `0206a438` | yes | `e1d8 2c02` | `r3_r2 >>= r12` |
| `02002bcc` | no | `e1d8 2a02` | `r3_r2 >>= r10` |

Accepted forms with the same top 12 bits: `e1d0 280c` `r3_r2 >>= 12`; `e1d8 0600` `r1_r0 <<= r6`.

### `8f00`

78 occurrences, 5 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02027a3e` | yes | `8f00` | `rep 2 16 {` |

Accepted forms with the same top 12 bits: `8f01` `call 30 <_start+0x494FE : 204961e >`; `8f02` `sp += 60`; `8f04` `goto 30 <_start+0x4D9A : 2004eba >`; `8f07` `goto -994 <_start+0x2B5E6 : 202b706 >`.

### `e1fc`

14 occurrences, 5 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02044f5c` | yes | `e1fc ae60` | `r11_r10 += r6 * r14 (u)` |
| `02044f7a` | yes | `e1fc e650` | `r15_r14 += r5 * r6 (u)` |
| `02044fcc` | yes | `e1fc 02f0` | `r1_r0 += r15 * r2 (u)` |
| `02045386` | yes | `e1fc 0cb0` | `r1_r0 += r11 * r12 (u)` |
| `02080d54` | yes | `e1fc 4010` | `r5_r4 += r1 * r0 (u)` |
| `020029fa` | no | `e1fc 4260` | `r5_r4 += r6 * r2 (u)` |
| `02043f60` | no | `e1fc 7320` | `r7_r6 += r2 * r3 (s)` |
| `02043ff4` | no | `e1fc 7540` | `r7_r6 += r4 * r5 (s)` |

Accepted forms with the same top 12 bits: `e1f0 e500` `r14 = r0 * r5`; `e1f4 1510` `r1 = r1 / r5 (u)`; `e1f8 0010` `r1_r0 = r1 * r0 (u)`.

### `00ba`

12 occurrences, 5 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0203f4f6` | yes | `00ba` | `testset b[r10]` |

### `8900`

9 occurrences, 5 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202a67c` | yes | `8900` | `rep 2 10 {` |

Accepted forms with the same top 12 bits: `8902` `sp += 36`; `8904` `goto 18 <_start+0x1AFC : 2001c1c >`; `8909` `r1 = r0 + 9`; `890d` `r5 = r0 + 9`.

### `9a10`

9 occurrences, 5 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201e202` | yes | `9a10` | `rep 4 27 {` |

Accepted forms with the same top 12 bits: `9a14` `goto 116 <_start+0x448 : 2000568 >`; `9a17` `goto -908 <_start+0x6699C : 2066abc >`; `9a18` `r0 = r1 + 26`.

### `e8d5`

6 occurrences, 5 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02019264` | yes | `e8d5 37f0` | `{pc, r13, r12, r10-r4} = [sp++]` |
| `0202fb54` | yes | `e8d5 dff0` | `{pc, r15, r14, r12-r4} = [sp++]` |
| `0204491a` | yes | `e8d5 00d0` | `{pc, r7, r6, r4} = [sp++]` |
| `02074ba2` | yes | `e8d5 0df0` | `{pc, r11, r10, r8-r4} = [sp++]` |

Accepted forms with the same top 12 bits: `e8d4 0001` `{r0} = [sp++]`; `e8d8 ffff` `[--sp] = {r15-r0}`.

### `e8d9`

6 occurrences, 5 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020191a2` | yes | `e8d9 37f0` | `[--sp] = {rets, r13, r12, r10-r4}` |
| `0202f8ae` | yes | `e8d9 dff0` | `[--sp] = {rets, r15, r14, r12-r4}` |
| `020448fc` | yes | `e8d9 00d0` | `[--sp] = {rets, r7, r6, r4}` |
| `02074b18` | yes | `e8d9 0df0` | `[--sp] = {rets, r11, r10, r8-r4}` |

Accepted forms with the same top 12 bits: `e8d4 0001` `{r0} = [sp++]`; `e8d8 ffff` `[--sp] = {r15-r0}`.

### `ee5b`

5 occurrences, 5 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02019740` | yes | `ee5b 3f2f` | `b[++r2=-1] = r3` |
| `02042e82` | yes | `ee5b 0f44` | `b[++r4=-12] = r0` |
| `020430be` | yes | `ee5b 5f44` | `b[++r4=-12] = r5` |
| `020445d6` | yes | `ee5b 6f3f` | `b[++r3=-1] = r6` |

Accepted forms with the same top 12 bits: `ee50 0858` `r0 = b[r5+136] (u)`; `ee51 3e3e` `r3 = b[r3+-18] (u)`; `ee52 3200` `b[r0+32] = r3`; `ee53 5d07` `b[r0+-41] = r5`; `ee54 2050` `r2 = b[r5+0] (s)`; `ee55 1f2c` `r1 = b[r2+-4] (s)`.

### `00b4`

13 occurrences, 4 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020042f8` | yes | `00b4` | `testset b[r4]` |

### `8610`

13 occurrences, 4 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02007cbe` | yes | `8610` | `rep 4 7 {` |

Accepted forms with the same top 12 bits: `8614` `goto 76 <_start+0x4372 : 2004492 >`; `8618` `r0 = r1 + 6`; `861a` `r2 = r1 + 6`; `861b` `r3 = r1 + 6`; `861d` `r5 = r1 + 6`.

### `8410`

10 occurrences, 4 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02000a4c` | yes | `8410` | `rep 4 5 {` |

Accepted forms with the same top 12 bits: `8414` `goto 72 <_start+0x2AC6 : 2002be6 >`; `8415` `goto 1096 <_start+0x3B1EE : 203b30e >`; `8416` `goto -1976 <_start+0x4D3EA : 204d50a >`; `8418` `r0 = r1 + 4`; `8419` `r1 = r1 + 4`; `841a` `r2 = r1 + 4`.

### `8c00`

5 occurrences, 4 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202e320` | yes | `8c00` | `rep 2 13 {` |

Accepted forms with the same top 12 bits: `8c01` `call 24 <_start+0x4CDFC : 204cf1c >`; `8c02` `sp += 48`; `8c04` `goto 24 <_start+0x693A : 2006a5a >`; `8c06` `goto -2024 <_start+0x6C6D8 : 206c7f8 >`; `8c09` `r1 = r0 + 12`; `8c0a` `r2 = r0 + 12`.

### `0b0b`

19 occurrences, 3 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02019b82` | yes | `0b0b` | `[r0++=r14] = r3` |

### `00b3`

12 occurrences, 3 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02003caa` | yes | `00b3` | `testset b[r3]` |

### `8600`

11 occurrences, 3 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0205f0f6` | yes | `8600` | `rep 2 7 {` |

Accepted forms with the same top 12 bits: `8602` `sp += 24`; `8604` `goto 12 <_start+0x2726 : 2002846 >`; `8607` `goto -1012 <_start+0x4C58C : 204c6ac >`; `8609` `r1 = r0 + 6`; `860a` `r2 = r0 + 6`.

### `8500`

10 occurrences, 3 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201e232` | yes | `8500` | `rep 2 6 {` |

Accepted forms with the same top 12 bits: `8502` `sp += 20`; `8504` `goto 10 <_start+0x30A : 200042a >`; `8509` `r1 = r0 + 5`.

### `9300`

10 occurrences, 3 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02005af0` | yes | `9300` | `rep 2 20 {` |

Accepted forms with the same top 12 bits: `9302` `sp += 76`; `9304` `goto 38 <_start+0x1AB4 : 2001bd4 >`; `9309` `r1 = r0 + 19`; `930a` `r2 = r0 + 19`.

### `0c21`

9 occurrences, 3 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020326c2` | yes | `0c21` | `r1 = h[r2++=r8] (u)` |

### `8e00`

6 occurrences, 3 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202c768` | yes | `8e00` | `rep 2 15 {` |

Accepted forms with the same top 12 bits: `8e02` `sp += 56`; `8e04` `goto 28 <_start+0x2EA : 200040a >`; `8e05` `goto 1052 <_start+0x6C642 : 206c762 >`; `8e06` `goto -2020 <_start+0x510DA : 20511fa >`; `8e0b` `r3 = r0 + 14`; `8e0c` `r4 = r0 + 14`.

### `ff4d`

6 occurrences, 3 reachable; 6 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200abba` | yes | `ff4d 0100 fd90` | `ifs (r0 <= r1) goto -1248 <_start+0xA5C0 : 200a6e0 >` |
| `0200ac02` | yes | `ff4d 0100 fd6c` | `ifs (r0 <= r1) goto -1320 <_start+0xA5C0 : 200a6e0 >` |
| `0202222a` | yes | `ff4d 0300 feed` | `ifs (r0 <= r3) goto -550 <_start+0x21EEA : 202200a >` |
| `0200e8a4` | no | `ff4d 0200 fdbc` | `ifs (r0 <= r2) goto -1160 <_start+0xE302 : 200e422 >` |
| `020129ea` | no | `ff4d 0800 feb2` | `ifs (r0 <= r8) goto -668 <_start+0x12634 : 2012754 >` |
| `020160ee` | no | `ff4d 4500 0133` | `ifs (r4 <= r5) goto 614 <_start+0x1623A : 201635a >` |

Accepted forms with the same top 12 bits: `ff40 3100 0106` `if (r3 == r1) goto 524 <_start+0x167C : 200179c >`; `ff41 0400 014d` `if (r0 != r4) goto 666 <_start+0x968A : 20097aa >`; `ff42 0100 0262` `if (r0 >= r1) goto 1220 <_start+0x11AD2 : 2011bf2 >`; `ff43 0600 fca4` `if (r0 < r6) goto -1720 <_start+0x1E6BA : 201e7da >`; `ff48 0100 0114` `if (r0 > r1) goto 552 <_start+0x2C8BC : 202c9dc >`; `ff49 1700 fdef` `if (r1 <= r7) goto -1058 <_start+0x23E88 : 2023fa8 >`.

### `9600`

4 occurrences, 3 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020287e4` | yes | `9600` | `rep 2 23 {` |

Accepted forms with the same top 12 bits: `9602` `sp += 88`; `9604` `goto 44 <_start+0x1032 : 2001152 >`; `9606` `goto -2004 <_start+0x4FB28 : 204fc48 >`; `9609` `r1 = r0 + 22`.

### `e870`

4 occurrences, 3 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201f6ba` | yes | `e870 0000` | `trigger` |

### `8a20`

3 occurrences, 3 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d2e8` | yes | `8a20` | `rep 6 11 {` |

Accepted forms with the same top 12 bits: `8a22` `sp += 168`; `8a24` `goto 148 <_start+0xAA90 : 200abb0 >`; `8a25` `goto 1172 <_start+0x6E612 : 206e732 >`; `8a26` `goto -1900 <_start+0x4FAD8 : 204fbf8 >`; `8a28` `r0 = r2 + 10`.

### `0f0b`

24 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02019b88` | yes | `0f0b` | `h[r0++=r14] = r3` |

### `8800`

13 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02034ad4` | yes | `8800` | `rep 2 9 {` |

Accepted forms with the same top 12 bits: `8802` `sp += 32`; `8804` `goto 16 <_start+0x766 : 2000886 >`; `8808` `r0 = r0 + 8`; `8809` `r1 = r0 + 8`; `880a` `r2 = r0 + 8`; `880b` `r3 = r0 + 8`.

### `9a00`

9 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02020ac2` | yes | `9a00` | `rep 2 27 {` |

Accepted forms with the same top 12 bits: `9a02` `sp += 104`; `9a04` `goto 52 <_start+0x2130 : 2002250 >`; `9a05` `goto 1076 <_start+0x6922E : 206934e >`; `9a06` `goto -1996 <_start+0x53DDA : 2053efa >`; `9a08` `r0 = r0 + 26`.

### `00bd`

7 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020666a6` | yes | `00bd` | `testset b[r13]` |

### `0a03`

7 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0207d0f2` | yes | `0a03` | `r3 = [r0++=r12]` |

### `0c03`

7 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020625d0` | yes | `0c03` | `r3 = h[r0++=r8] (u)` |

### `079c`

5 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0207cb2e` | yes | `079c` | `b[r1++=-1] = r4` |

Accepted forms with the same top 12 bits: `0790` `b[r1++=1] = r0`; `0791` `b[r1++=1] = r1`; `0792` `b[r1++=1] = r2`; `0793` `b[r1++=1] = r3`; `0794` `b[r1++=1] = r4`; `0795` `b[r1++=1] = r5`.

### `0c0a`

5 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020109cc` | yes | `0c0a` | `h[r0++=r8] = r2` |

### `9d10`

4 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202c6a0` | yes | `9d10` | `rep 4 30 {` |

Accepted forms with the same top 12 bits: `9d14` `goto 122 <_start+0x7792 : 20078b2 >`; `9d15` `goto 1146 <_start+0x4614C : 204626c >`; `9d16` `goto -1926 <_start+0x50B70 : 2050c90 >`; `9d1f` `r7 = r1 + 29`.

### `0609`

3 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0207f74e` | yes | `0609` | `r1 = h[r0++=-2] (u)` |

Accepted forms with the same top 12 bits: `0601` `r1 = h[r0++=2] (u)`; `0602` `r2 = h[r0++=2] (u)`; `0603` `r3 = h[r0++=2] (u)`; `0604` `r4 = h[r0++=2] (u)`; `0605` `r5 = h[r0++=2] (u)`; `0606` `r6 = h[r0++=2] (u)`.

### `8820`

3 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02007ca4` | yes | `8820` | `rep 6 9 {` |

Accepted forms with the same top 12 bits: `8824` `goto 144 <_start+0x6832 : 2006952 >`; `8825` `goto 1168 <_start+0x52E9A : 2052fba >`; `8828` `r0 = r2 + 8`; `8829` `r1 = r2 + 8`; `882b` `r3 = r2 + 8`; `882c` `r4 = r2 + 8`.

### `ed90`

3 occurrences, 2 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02018fce` | yes | `ed90 1200` | `ifs (r0 < r2) {` |
| `020739ba` | yes | `ed90 8300` | `ifs (r0 < r3) {` |
| `02015492` | no | `ed90 0800` | `ifs (r0 < r8) {` |

### `9010`

2 occurrences, 2 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202a454` | yes | `9010` | `rep 4 17 {` |

Accepted forms with the same top 12 bits: `9014` `goto 96 <_start+0x2CD2 : 2002df2 >`; `9015` `goto 1120 <_start+0x779B4 : 2077ad4 >`; `9018` `r0 = r1 + 16`; `901a` `r2 = r1 + 16`; `901b` `r3 = r1 + 16`; `901c` `r4 = r1 + 16`.

### `f1c4`

2 occurrences, 2 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202c94c` | yes | `f1c4 20f3` | `r2 = r15 <> 3  #` |
| `0208069c` | yes | `f1c4 8163` | `r8 = r6 <> 19  #` |

Accepted forms with the same top 12 bits: `f1c0 0083` `r0 = r8 << 3  #`; `f1c8 1162` `r1 = r6 >> r1  #`.

### `002e`

35 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02079992` | yes | `002e` | `ssync` |

Accepted forms with the same top 12 bits: `0020` `csync`; `0022` `ssync`.

### `00be`

15 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0203d95c` | yes | `00be` | `testset b[r14]` |

### `0c62`

15 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020326ba` | yes | `0c62` | `r2 = h[r6++=r8] (u)` |

### `00b9`

14 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0203ed4e` | yes | `00b9` | `testset b[r9]` |

### `0f05`

14 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201eab2` | yes | `0f05` | `r5 = h[r0++=r14] (u)` |

### `00d6`

13 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020001b6` | yes | `00d6` | `goto r6` |

### `ff4c`

13 occurrences, 1 reachable; 6 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02010484` | yes | `ff4c 5200 0138` | `ifs (r5 > r2) goto 624 <_start+0x105DA : 20106fa >` |
| `0200f600` | no | `ff4c f200 fea4` | `ifs (r15 > r2) goto -696 <_start+0xF22E : 200f34e >` |
| `0201020e` | no | `ff4c 4b00 012a` | `ifs (r4 > r11) goto 596 <_start+0x10348 : 2010468 >` |
| `0201435e` | no | `ff4c 8200 0220` | `ifs (r8 > r2) goto 1088 <_start+0x14684 : 20147a4 >` |
| `0201438c` | no | `ff4c 7000 0209` | `ifs (r7 > r0) goto 1042 <_start+0x14684 : 20147a4 >` |
| `02014ad8` | no | `ff4c 7500 0273` | `ifs (r7 > r5) goto 1254 <_start+0x14EA4 : 2014fc4 >` |
| `02014c46` | no | `ff4c 7500 0194` | `ifs (r7 > r5) goto 808 <_start+0x14E54 : 2014f74 >` |
| `02015996` | no | `ff4c 7000 0155` | `ifs (r7 > r0) goto 682 <_start+0x15B26 : 2015c46 >` |

Accepted forms with the same top 12 bits: `ff40 3100 0106` `if (r3 == r1) goto 524 <_start+0x167C : 200179c >`; `ff41 0400 014d` `if (r0 != r4) goto 666 <_start+0x968A : 20097aa >`; `ff42 0100 0262` `if (r0 >= r1) goto 1220 <_start+0x11AD2 : 2011bf2 >`; `ff43 0600 fca4` `if (r0 < r6) goto -1720 <_start+0x1E6BA : 201e7da >`; `ff48 0100 0114` `if (r0 > r1) goto 552 <_start+0x2C8BC : 202c9dc >`; `ff49 1700 fdef` `if (r1 <= r7) goto -1058 <_start+0x23E88 : 2023fa8 >`.

### `00b7`

11 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0203e008` | yes | `00b7` | `testset b[r7]` |

### `00b8`

11 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020389aa` | yes | `00b8` | `testset b[r8]` |

### `0f02`

11 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202bb66` | yes | `0f02` | `r2 = h[r0++=r14] (u)` |

### `ff4b`

11 occurrences, 1 reachable; 6 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201048e` | yes | `ff4b 3200 0133` | `ifs (r3 < r2) goto 614 <_start+0x105DA : 20106fa >` |
| `02001322` | no | `ff4b 5000 fefd` | `ifs (r5 < r0) goto -518 <_start+0x1002 : 2001122 >` |
| `0200fbfa` | no | `ff4b 9000 fece` | `ifs (r9 < r0) goto -612 <_start+0xF87C : 200f99c >` |
| `020124ba` | no | `ff4b 7000 fe94` | `ifs (r7 < r0) goto -728 <_start+0x120C8 : 20121e8 >` |
| `02014354` | no | `ff4b 2500 0225` | `ifs (r2 < r5) goto 1098 <_start+0x14684 : 20147a4 >` |
| `02014382` | no | `ff4b 0300 020e` | `ifs (r0 < r3) goto 1052 <_start+0x14684 : 20147a4 >` |
| `02016004` | no | `ff4b a800 02d3` | `ifs (r10 < r8) goto 1446 <_start+0x16490 : 20165b0 >` |
| `02016378` | no | `ff4b 8600 013e` | `ifs (r8 < r6) goto 636 <_start+0x164DA : 20165fa >` |

Accepted forms with the same top 12 bits: `ff40 3100 0106` `if (r3 == r1) goto 524 <_start+0x167C : 200179c >`; `ff41 0400 014d` `if (r0 != r4) goto 666 <_start+0x968A : 20097aa >`; `ff42 0100 0262` `if (r0 >= r1) goto 1220 <_start+0x11AD2 : 2011bf2 >`; `ff43 0600 fca4` `if (r0 < r6) goto -1720 <_start+0x1E6BA : 201e7da >`; `ff48 0100 0114` `if (r0 > r1) goto 552 <_start+0x2C8BC : 202c9dc >`; `ff49 1700 fdef` `if (r1 <= r7) goto -1058 <_start+0x23E88 : 2023fa8 >`.

### `00b2`

10 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02003e36` | yes | `00b2` | `testset b[r2]` |

### `00dd`

10 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0203d71a` | yes | `00dd` | `goto r13` |

### `00bc`

9 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0203f1f6` | yes | `00bc` | `testset b[r12]` |

### `009d`

8 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02064636` | yes | `009d` | `callns r13` |

### `00a3`

8 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201eafc` | yes | `00a3` | `swi 3` |

### `00b6`

8 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0203f692` | yes | `00b6` | `testset b[r6]` |

### `0c09`

7 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020832ac` | yes | `0c09` | `h[r0++=r8] = r1` |

### `0808`

6 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02061b9a` | yes | `0808` | `[r0++=r8] = r0` |

### `0806`

5 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020109ca` | yes | `0806` | `r6 = [r0++=r8]` |

### `0b60`

5 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02019b8c` | yes | `0b60` | `r0 = [r6++=r14]` |

### `1206`

5 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020109c8` | yes | `1206` | `r6 = b[r0++=r12] (u)` |

### `8700`

5 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020296aa` | yes | `8700` | `rep 2 8 {` |

Accepted forms with the same top 12 bits: `8702` `sp += 28`; `8704` `goto 14 <_start+0x1E0A : 2001f2a >`; `8708` `r0 = r0 + 7`; `8709` `r1 = r0 + 7`; `870a` `r2 = r0 + 7`; `870b` `r3 = r0 + 7`.

### `078a`

4 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0207efda` | yes | `078a` | `b[r0++=-1] = r2` |

Accepted forms with the same top 12 bits: `0780` `b[r0++=1] = r0`; `0781` `b[r0++=1] = r1`; `0782` `b[r0++=1] = r2`; `0783` `b[r0++=1] = r3`; `0784` `b[r0++=1] = r4`; `0785` `b[r0++=1] = r5`.

### `0a08`

4 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02040df0` | yes | `0a08` | `[r0++=r12] = r0` |

### `0e21`

4 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02061ba4` | yes | `0e21` | `r1 = h[r2++=r12] (u)` |

### `0f35`

4 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201f924` | yes | `0f35` | `r5 = h[r3++=r14] (u)` |

### `8320`

4 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02056cc2` | yes | `8320` | `rep 6 4 {` |

Accepted forms with the same top 12 bits: `8322` `sp += 140`; `8324` `goto 134 <_start+0xE68 : 2000f88 >`; `8327` `goto -890 <_start+0x50718 : 2050838 >`; `8328` `r0 = r2 + 3`; `8329` `r1 = r2 + 3`; `832c` `r4 = r2 + 3`.

### `9000`

4 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202a566` | yes | `9000` | `rep 2 17 {` |

Accepted forms with the same top 12 bits: `9002` `sp += 64`; `9004` `goto 32 <_start+0x1616 : 2001736 >`; `9009` `r1 = r0 + 16`; `900a` `r2 = r0 + 16`; `900b` `r3 = r0 + 16`; `900c` `r4 = r0 + 16`.

### `020d`

3 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202c95c` | yes | `020d` | `pfetch [r13]` |

### `13c0`

3 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200de98` | yes | `13c0` | `r0 = b[r4++=r15] (u)` |

### `8300`

3 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02002118` | yes | `8300` | `rep 2 4 {` |

Accepted forms with the same top 12 bits: `8301` `call 6 <_start+0x4CAA2 : 204cbc2 >`; `8302` `sp += 12`; `8304` `goto 6 <_start+0x11FC : 200131c >`; `8305` `goto 1030 <_start+0x4F270 : 204f390 >`; `8307` `goto -1018 <_start+0x4CA26 : 204cb46 >`; `8308` `r0 = r0 + 3`.

### `9500`

3 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d9d4` | yes | `9500` | `rep 2 22 {` |

Accepted forms with the same top 12 bits: `9502` `sp += 84`; `9504` `goto 42 <_start+0x9EEE : 200a00e >`; `9506` `goto -2006 <_start+0x4D688 : 204d7a8 >`; `9507` `goto -982 <_start+0x56A8C : 2056bac >`; `9508` `r0 = r0 + 21`; `950a` `r2 = r0 + 21`.

### `9800`

3 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02036274` | yes | `9800` | `rep 2 25 {` |

Accepted forms with the same top 12 bits: `9802` `sp += 96`; `9804` `goto 48 <_start+0x705A : 200717a >`; `9806` `goto -2000 <_start+0x8E408 : 208e528 >`; `9807` `goto -976 <_start+0x66CC8 : 2066de8 >`; `9809` `r1 = r0 + 24`; `980b` `r3 = r0 + 24`.

### `e86c`

3 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02038f8e` | yes | `e86c 0602` | `[r0+0] >>= 6` |
| `020845d8` | no | `e86c 581e` | `[r5+28] >>= 8` |

Accepted forms with the same top 12 bits: `e864 1000` `[r1+0] /= r0`; `e868 4608` `[r4+8] += r6`; `e86c 1800` `[r1+0] <<= 8`.

### `ff22`

3 occurrences, 1 reachable; 6 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0203a308` | yes | `ff22 6c7f 0003` | `if (r6 >= 65280) goto 6 <_start+0x3A1F4 : 203a314 >` |
| `02044da2` | no | `ff22 14ff 0033` | `if (r1 >= 2139095040) goto 102 <_start+0x44CEE : 2044e0e >` |
| `0204b6b6` | no | `ff22 0060 4f00` | `if (r0 >= 96) goto 40448 <_start+0x5539C : 20554bc >` |

Accepted forms with the same top 12 bits: `ff20 0600 0017` `if (r0 == 134217728) goto 46 <_start+0x1A66 : 2001b86 >`; `ff21 6c00 ffee` `if (r6 != 32768) goto -36 <_start+0x4442 : 2004562 >`; `ff23 4d80 001d` `if (r4 < 4096) goto 58 <_start+0x29256 : 2029376 >`; `ff28 7c80 000b` `if (r7 > 16384) goto 22 <_start+0x24F4C : 202506c >`; `ff29 0780 002a` `if (r0 <= 16777216) goto 84 <_start+0x37FE : 200391e >`; `ff2a ef40 0090` `ifs (r14 >= 768) goto 288 <_start+0x48DF2 : 2048f12 >`.

### `062c`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0207f0e6` | yes | `062c` | `r4 = h[r2++=-2] (u)` |

Accepted forms with the same top 12 bits: `0620` `r0 = h[r2++=2] (u)`; `0621` `r1 = h[r2++=2] (u)`; `0623` `r3 = h[r2++=2] (u)`; `0624` `r4 = h[r2++=2] (u)`; `0625` `r5 = h[r2++=2] (u)`; `0626` `r6 = h[r2++=2] (u)`.

### `079b`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201948a` | yes | `079b` | `b[r1++=-1] = r3` |

Accepted forms with the same top 12 bits: `0790` `b[r1++=1] = r0`; `0791` `b[r1++=1] = r1`; `0792` `b[r1++=1] = r2`; `0793` `b[r1++=1] = r3`; `0794` `b[r1++=1] = r4`; `0795` `b[r1++=1] = r5`.

### `1a1d`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201eaba` | yes | `1a1d` | `r5 <<<= r1` |

Accepted forms with the same top 12 bits: `1a10` `r0 <<= r1`; `1a11` `r1 <<= r1`; `1a12` `r2 <<= r1`; `1a13` `r3 <<= r1`; `1a14` `r4 <<= r1`; `1a15` `r5 <<= r1`.

### `9400`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202da70` | yes | `9400` | `rep 2 21 {` |

Accepted forms with the same top 12 bits: `9402` `sp += 80`; `9404` `goto 40 <_start+0x3544 : 2003664 >`; `9409` `r1 = r0 + 20`; `940a` `r2 = r0 + 20`; `940b` `r3 = r0 + 20`; `940c` `r4 = r0 + 20`.

### `9510`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02063244` | yes | `9510` | `rep 4 22 {` |

Accepted forms with the same top 12 bits: `9514` `goto 106 <_start+0x2FEC : 200310c >`; `9517` `goto -918 <_start+0x7BFC0 : 207c0e0 >`; `951b` `r3 = r1 + 21`; `951e` `r6 = r1 + 21`.

### `9d00`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02077f36` | yes | `9d00` | `rep 2 30 {` |

Accepted forms with the same top 12 bits: `9d01` `call 58 <_start+0x45C64 : 2045d84 >`; `9d02` `sp += 116`; `9d04` `goto 58 <_start+0x191E : 2001a3e >`; `9d05` `goto 1082 <_start+0x1007C : 201019c >`; `9d0f` `r7 = r0 + 29`.

### `e820`

2 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020795b0` | yes | `e820 4d80` | `if (r0 == 4096) {` |
| `0208e2e4` | no | `e820 1403` | `if (r0 == 2197815296) {` |

### `ed96`

2 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02018330` | yes | `ed96 c500` | `ifs (r6 < r5) {` |
| `020201ae` | no | `ed96 0000` | `ifs (r6 < r0) {` |

### `f1f6`

2 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0205c580` | yes | `f1f6 6180` | `r7_r6 = r9_r8 / r1 (u)  #` |
| `020891a2` | no | `f1f6 4120` | `r5_r4 = r3_r2 / r1 (u)  #` |

Accepted forms with the same top 12 bits: `f1f0 0570` `r0 = r7 * r5  #`; `f1f4 0070` `r0 = r7 / r0 (u)  #`; `f1f8 b180` `r11_r10 = r8 * r1 (s)  #`.

### `0569`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02080dfa` | yes | `0569` | `r1 = [r6++=-4]` |

Accepted forms with the same top 12 bits: `0560` `r0 = [r6++=4]`; `0561` `r1 = [r6++=4]`; `0563` `r3 = [r6++=4]`; `0564` `r4 = [r6++=4]`; `0565` `r5 = [r6++=4]`; `0567` `r7 = [r6++=4]`.

### `056b`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200b2a8` | yes | `056b` | `r3 = [r6++=-4]` |

Accepted forms with the same top 12 bits: `0560` `r0 = [r6++=4]`; `0561` `r1 = [r6++=4]`; `0563` `r3 = [r6++=4]`; `0564` `r4 = [r6++=4]`; `0565` `r5 = [r6++=4]`; `0567` `r7 = [r6++=4]`.

### `061b`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0204068a` | yes | `061b` | `r3 = h[r1++=-2] (u)` |

Accepted forms with the same top 12 bits: `0610` `r0 = h[r1++=2] (u)`; `0612` `r2 = h[r1++=2] (u)`; `0613` `r3 = h[r1++=2] (u)`; `0614` `r4 = h[r1++=2] (u)`; `0615` `r5 = h[r1++=2] (u)`; `0617` `r7 = h[r1++=2] (u)`.

### `080e`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020109ce` | yes | `080e` | `[r0++=r8] = r6` |

### `0905`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d93a` | yes | `0905` | `r5 = [r0++=r10]` |

### `094e`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0207f74c` | yes | `094e` | `[r4++=r10] = r6` |

### `0b27`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02019b80` | yes | `0b27` | `r7 = [r2++=r14]` |

### `0b6b`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02019b8e` | yes | `0b6b` | `[r6++=r14] = r3` |

### `0e61`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02061ba2` | yes | `0e61` | `r1 = h[r6++=r12] (u)` |

### `0f1b`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0201f958` | yes | `0f1b` | `h[r1++=r14] = r3` |

### `100e`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d93c` | yes | `100e` | `b[r0++=r8] = r6` |

### `1113`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202c95e` | yes | `1113` | `r3 = b[r1++=r10] (u)` |

### `120f`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02017744` | yes | `120f` | `b[r0++=r12] = r7` |

### `8520`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0205faa6` | yes | `8520` | `rep 6 6 {` |

Accepted forms with the same top 12 bits: `8522` `sp += 148`; `8524` `goto 138 <_start+0x827E : 200839e >`; `8527` `goto -886 <_start+0x4DB4C : 204dc6c >`.

### `8530`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02002792` | yes | `8530` | `rep 8 6 {` |

Accepted forms with the same top 12 bits: `8534` `goto 202 <_start+0x1007C : 201019c >`; `8537` `goto -822 <_start+0x69CDE : 2069dfe >`; `8539` `r1 = r3 + 5`; `853f` `r7 = r3 + 5`.

### `8b20`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d002` | yes | `8b20` | `rep 6 12 {` |

Accepted forms with the same top 12 bits: `8b22` `sp += 172`; `8b24` `goto 150 <_start+0xD3E : 2000e5e >`; `8b25` `goto 1174 <_start+0x6922E : 206934e >`; `8b28` `r0 = r2 + 11`; `8b2b` `r3 = r2 + 11`; `8b2c` `r4 = r2 + 11`.

### `9610`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0207ff72` | yes | `9610` | `rep 4 23 {` |

Accepted forms with the same top 12 bits: `9614` `goto 108 <_start+0x82AE : 20083ce >`; `9618` `r0 = r1 + 22`; `961e` `r6 = r1 + 22`; `961f` `r7 = r1 + 22`.

### `a63c`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020645f8` | yes | `a63c` | `r4 = r3 <<< 6` |

Accepted forms with the same top 12 bits: `a630` `r0 = r3 << 6`; `a633` `r3 = r3 << 6`; `a636` `r6 = r3 << 6`.

### `a93c`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200827c` | yes | `a93c` | `r4 = r3 <<< 9` |

Accepted forms with the same top 12 bits: `a931` `r1 = r3 << 9`; `a932` `r2 = r3 << 9`.

### `d646`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02019488` | yes | `d646` | `r6 = r4  #` |

Accepted forms with the same top 12 bits: `d640` `r0 = r4  #`; `d641` `r1 = r4  #`; `d642` `r2 = r4  #`; `d643` `r3 = r4  #`; `d645` `r5 = r4  #`; `d646` `r6 = r4  #`.

### `ec5c`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200940e` | yes | `ec5c 8012` | `r9_r8 = d[++r1=r0]` |

Accepted forms with the same top 12 bits: `ec50 2004` `r3_r2 = d[r0+4]`; `ec51 0949` `d[r4+408] = r1_r0`; `ec52 6f51` `d[r5+752] = r7_r6`; `ec53 20f9` `d[r15+776] = r3_r2`; `ec57 8f19` `d[r1+-8] = r9_r8`.

### `ecd0`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02056b9a` | yes | `ecd0 000b` | `[++r0=8] = r0` |

Accepted forms with the same top 12 bits: `ecd0 0090` `r0 = [r9+0]`; `ecd1 1c0d` `[r0+460] = r1`; `ecd2 2528` `r2 = [r2+600]`; `ecd3 0ef0` `r0 = [r15+992]`; `ecd7 df0c` `r13 = [r0+-4]`; `ecd8 278a` `r2 = [r8+r7<<2]`.

### `ed97`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020182ac` | yes | `ed97 c600` | `ifs (r7 < r6) {` |

### `eda4`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0206d4b0` | yes | `eda4 0600` | `ifs (r4 < 134217728) {` |

### `edd3`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0204068c` | yes | `edd3 3f0d` | `h[r0++=-4] = r3` |

Accepted forms with the same top 12 bits: `edd0 00d3` `h[r13++=2] = r0`; `edd4 7014` `r7 = h[r1++=4] (s)`; `edd8 3569` `h[r6+r5<<1] = r3`; `eddc 1100` `r1 = h[++r0=r1] (u)`.

### `f120`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020006d6` | yes | `f120 86ff` | `r0 = r8 + -6401  #` |

Accepted forms with the same top 12 bits: `f120 86ff` `r0 = r8 + -6401  #`; `f122 28c0` `r2 = r2 + -5952  #`; `f125 6710` `r5 = r6 + -6384  #`; `f12f b07f` `r15 = r11 + -8065  #`.

### Runtime-guarded forms

Accepted, but fault for some register values (a bit index of 32 or more, a REP body that is not a qualified linear block when the count is nonzero).

| Opcode | Occurrences | Reachable | Example | Vendor disassembly |
| --- | --- | --- | --- | --- |
| `0313` | 5 | 1 | `020832aa` | `rep 4 r3 {` |
| `0326` | 1 | 1 | `0206684e` | `rep 6 r6 {` |
| `0332` | 5 | 1 | `020445f0` | `rep 8 r2 {` |
| `0335` | 3 | 1 | `020445e0` | `rep 8 r5 {` |
| `0336` | 1 | 1 | `020445d0` | `rep 8 r6 {` |
| `033d` | 1 | 1 | `02008326` | `rep 8 r13 {` |
| `034d` | 2 | 1 | `02066850` | `rep 10 r13 {` |
| `03a5` | 2 | 1 | `02066852` | `rep 22 r5 {` |
| `03a6` | 2 | 2 | `020666a4` | `rep 22 r6 {` |
| `03b5` | 5 | 1 | `020445fe` | `rep 24 r5 {` |
| `03d2` | 3 | 1 | `02066854` | `rep 28 r2 {` |
| `03e1` | 1 | 1 | `02066856` | `rep 30 r1 {` |
| `03ee` | 1 | 1 | `02066858` | `rep 30 r14 {` |
| `03f8` | 2 | 1 | `0206685a` | `rep 32 r8 {` |
| `e194` | 152 | 83 | `020009f6` | `r1 = r2 & (1 << r1)` |
| `e866` | 39 | 33 | `02004014` | `[r0+8] /= 1 << r2` |
| `f194` | 3 | 1 | `0205f5d8` | `r4 = r0 & (1 << r1)  #` |

## felucca-1.1.5.1-app

`felucca-1.1.5.1-app.bin`, application SHA-256 `f075afa030d3da40...`, 447,580 bytes, 161,524 decoded instructions of which 33,532 statically reachable.

4914 rejected opcodes (20502 occurrences), 39 of them in statically reachable code.

| Opcode | Occurrences | Reachable | Example | Vendor disassembly |
| --- | --- | --- | --- | --- |
| `e1f6` | 17 | 17 | `0202a262` | `r3_r2 = r3_r2 / r6 (s)` |
| `e1fc` | 23 | 16 | `0202a610` | `r5_r4 += r10 * r0 (s)` |
| `f1fc` | 5 | 5 | `0202a618` | `r5_r4 += r0 * r3 (s)  #` |
| `0907` | 53 | 1 | `02013cf8` | `r7 = [r0++=r10]` |
| `1212` | 35 | 1 | `020175a6` | `r2 = b[r1++=r12] (u)` |
| `0906` | 27 | 1 | `02013d00` | `r6 = [r0++=r10]` |
| `0c03` | 24 | 1 | `02032b62` | `r3 = h[r0++=r8] (u)` |
| `0909` | 19 | 1 | `02013cfa` | `[r0++=r10] = r1` |
| `0807` | 12 | 1 | `020169d2` | `r7 = [r0++=r8]` |
| `0904` | 9 | 1 | `0202b4d4` | `r4 = [r0++=r10]` |
| `0b07` | 9 | 1 | `020169d4` | `r7 = [r0++=r14]` |
| `1203` | 9 | 1 | `02029068` | `r3 = b[r0++=r12] (u)` |
| `0d07` | 7 | 1 | `020169d6` | `r7 = h[r0++=r10] (u)` |
| `0473` | 6 | 1 | `0202fcb8` | `[--sp] = {rets, r3}` |
| `0d0d` | 6 | 1 | `02031364` | `h[r0++=r10] = r5` |
| `1247` | 6 | 1 | `020175aa` | `r7 = b[r4++=r12] (u)` |
| `0b19` | 5 | 1 | `0202b4d6` | `[r1++=r14] = r1` |
| `058b` | 3 | 1 | `02002a1a` | `[r0++=-4] = r3` |
| `0803` | 3 | 1 | `02016a00` | `r3 = [r0++=r8]` |
| `0497` | 2 | 1 | `02019370` | `{sr4, retx, rete, reti} = [sp++]` |
| `05ea` | 2 | 1 | `0202fcbc` | `[r6++=-4] = r2` |
| `0932` | 2 | 1 | `02013cfe` | `r2 = [r3++=r10]` |
| `0f03` | 2 | 1 | `0202ab9a` | `r3 = h[r0++=r14] (u)` |
| `1206` | 2 | 1 | `020175a4` | `r6 = b[r0++=r12] (u)` |
| `125f` | 2 | 1 | `020175ac` | `b[r5++=r12] = r7` |
| `ec58` | 2 | 1 | `0202d64c` | `d[r0++=8] = r3_r2` |
| `ee23` | 2 | 1 | `02031726` | `ifs (r3 > 16384) {` |
| `f434` | 2 | 1 | `02026dba` | `r0 = umax(r0, r1)  #` |
| `ff2c` | 2 | 1 | `02031a60` | `ifs (r10 > 16384) goto 14 <_start+0x31954 : 2031a74 >` |
| `110e` | 1 | 1 | `0202b4d8` | `b[r0++=r10] = r6` |
| `e1d8` | 1 | 1 | `0202cbda` | `r1_r0 >>= r2` |
| `e825` | 1 | 1 | `0202d99a` | `if (r5 == 1073741824) {` |
| `e8d5` | 1 | 1 | `0202cbde` | `{pc, r11, r10, r8-r4} = [sp++]` |
| `e8d9` | 1 | 1 | `0202cacc` | `[--sp] = {rets, r11, r10, r8-r4}` |
| `ec20` | 1 | 1 | `0200d386` | `if (r0 > 20480) {` |
| `ed53` | 1 | 1 | `02017944` | `h[r11+-4] = r8` |
| `ee2c` | 1 | 1 | `0202dc0a` | `ifs (r12 > 49152) {` |
| `f0b8` | 1 | 1 | `0202d064` | `r13 = r3 + r3 + c  #` |
| `f194` | 1 | 1 | `0200c012` | `r3 = r3 / (1 << r6)  #` |

4875 further opcodes (20194 occurrences) appear only outside reachable code, mostly data decoded as instructions; they are not listed.

### `e1f6`

17 occurrences, 17 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202a262` | yes | `e1f6 3620` | `r3_r2 = r3_r2 / r6 (s)` |
| `0202cc52` | yes | `e1f6 6280` | `r7_r6 = r9_r8 / r2 (u)` |
| `0202cc9a` | yes | `e1f6 2680` | `r3_r2 = r9_r8 / r6 (u)` |
| `0202cce8` | yes | `e1f6 1200` | `r1_r0 = r1_r0 / r2 (s)` |
| `0202cd98` | yes | `e1f6 2720` | `r3_r2 = r3_r2 / r7 (u)` |
| `0202ce30` | yes | `e1f6 0720` | `r1_r0 = r3_r2 / r7 (u)` |
| `0202cfc6` | yes | `e1f6 0020` | `r1_r0 = r3_r2 / r0 (u)` |
| `0202d5c4` | yes | `e1f6 2e20` | `r3_r2 = r3_r2 / r14 (u)` |

Accepted forms with the same top 12 bits: `e1f0 7800` `r7 = r0 * r8`; `e1f4 0800` `r0 = r0 / r8 (u)`; `e1f8 5220` `r5_r4 = r2 * r2 (s)`.

### `e1fc`

23 occurrences, 16 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202a610` | yes | `e1fc 50a0` | `r5_r4 += r10 * r0 (s)` |
| `0202a630` | yes | `e1fc 13a0` | `r1_r0 += r10 * r3 (s)` |
| `0202a638` | yes | `e1fc 19a0` | `r1_r0 += r10 * r9 (s)` |
| `0202a648` | yes | `e1fc b3e0` | `r11_r10 += r14 * r3 (s)` |
| `0202a650` | yes | `e1fc b8e0` | `r11_r10 += r14 * r8 (s)` |
| `0202a660` | yes | `e1fc 1840` | `r1_r0 += r4 * r8 (s)` |
| `0202a66e` | yes | `e1fc b400` | `r11_r10 += r0 * r4 (s)` |
| `0202a684` | yes | `e1fc 7040` | `r7_r6 += r4 * r0 (s)` |

Accepted forms with the same top 12 bits: `e1f0 7800` `r7 = r0 * r8`; `e1f4 0800` `r0 = r0 / r8 (u)`; `e1f8 5220` `r5_r4 = r2 * r2 (s)`.

### `f1fc`

5 occurrences, 5 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202a618` | yes | `f1fc 5300` | `r5_r4 += r0 * r3 (s)  #` |
| `0202a622` | yes | `f1fc 5e00` | `r5_r4 += r0 * r14 (s)  #` |
| `0202a67a` | yes | `f1fc 7d00` | `r7_r6 += r0 * r13 (s)  #` |
| `0202e1fe` | yes | `f1fc f710` | `r15_r14 += r1 * r7 (s)  #` |
| `0202e68c` | yes | `f1fc f830` | `r15_r14 += r3 * r8 (s)  #` |

Accepted forms with the same top 12 bits: `f1f0 0560` `r0 = r6 * r5  #`; `f1f4 2230` `r2 = r3 / r2 (u)  #`; `f1f8 5440` `r5_r4 = r4 * r4 (s)  #`.

### `0907`

53 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02013cf8` | yes | `0907` | `r7 = [r0++=r10]` |

### `1212`

35 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020175a6` | yes | `1212` | `r2 = b[r1++=r12] (u)` |

### `0906`

27 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02013d00` | yes | `0906` | `r6 = [r0++=r10]` |

### `0c03`

24 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02032b62` | yes | `0c03` | `r3 = h[r0++=r8] (u)` |

### `0909`

19 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02013cfa` | yes | `0909` | `[r0++=r10] = r1` |

### `0807`

12 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020169d2` | yes | `0807` | `r7 = [r0++=r8]` |

### `0904`

9 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202b4d4` | yes | `0904` | `r4 = [r0++=r10]` |

### `0b07`

9 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020169d4` | yes | `0b07` | `r7 = [r0++=r14]` |

### `1203`

9 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02029068` | yes | `1203` | `r3 = b[r0++=r12] (u)` |

### `0d07`

7 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020169d6` | yes | `0d07` | `r7 = h[r0++=r10] (u)` |

### `0473`

6 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202fcb8` | yes | `0473` | `[--sp] = {rets, r3}` |

Accepted forms with the same top 12 bits: `0474` `[--sp] = {rets, r4}`; `0475` `[--sp] = {rets, r5, r4}`; `0476` `[--sp] = {rets, r6-r4}`; `0477` `[--sp] = {rets, r7-r4}`; `0478` `[--sp] = {rets, r8-r4}`; `0479` `[--sp] = {rets, r9-r4}`.

### `0d0d`

6 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02031364` | yes | `0d0d` | `h[r0++=r10] = r5` |

### `1247`

6 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020175aa` | yes | `1247` | `r7 = b[r4++=r12] (u)` |

### `0b19`

5 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202b4d6` | yes | `0b19` | `[r1++=r14] = r1` |

### `058b`

3 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02002a1a` | yes | `058b` | `[r0++=-4] = r3` |

Accepted forms with the same top 12 bits: `0580` `[r0++=4] = r0`; `0582` `[r0++=4] = r2`; `0583` `[r0++=4] = r3`; `0584` `[r0++=4] = r4`; `0587` `[r0++=4] = r7`.

### `0803`

3 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02016a00` | yes | `0803` | `r3 = [r0++=r8]` |

### `0497`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02019370` | yes | `0497` | `{sr4, retx, rete, reti} = [sp++]` |

### `05ea`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202fcbc` | yes | `05ea` | `[r6++=-4] = r2` |

Accepted forms with the same top 12 bits: `05e0` `[r6++=4] = r0`; `05e1` `[r6++=4] = r1`; `05e2` `[r6++=4] = r2`; `05e3` `[r6++=4] = r3`; `05e4` `[r6++=4] = r4`; `05e5` `[r6++=4] = r5`.

### `0932`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02013cfe` | yes | `0932` | `r2 = [r3++=r10]` |

### `0f03`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202ab9a` | yes | `0f03` | `r3 = h[r0++=r14] (u)` |

### `1206`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020175a4` | yes | `1206` | `r6 = b[r0++=r12] (u)` |

### `125f`

2 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `020175ac` | yes | `125f` | `b[r5++=r12] = r7` |

### `ec58`

2 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d64c` | yes | `ec58 2009` | `d[r0++=8] = r3_r2` |
| `02056134` | no | `ec58 00f0` | `r1_r0 = d[r15++=0]` |

Accepted forms with the same top 12 bits: `ec50 0049` `d[r4+8] = r1_r0`; `ec51 6041` `d[r4+256] = r7_r6`; `ec53 0771` `d[r7+880] = r1_r0`.

### `ee23`

2 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02031726` | yes | `ee23 9c80` | `ifs (r3 > 16384) {` |
| `0205e8be` | no | `ee23 ada8` | `ifs (r3 > 5376) {` |

### `f434`

2 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02026dba` | yes | `f434 0100` | `r0 = umax(r0, r1)  #` |
| `0205e69c` | no | `f434 f8f0` | `r15 = umax(r15, r8)  #` |

Accepted forms with the same top 12 bits: `f430 7600` `r7 = abs(r6)  #`; `f434 0011` `r0 = smax(r1, r0)  #`; `f435 0d00` `r0 = umin(r0, r13)  #`.

### `ff2c`

2 occurrences, 1 reachable; 6 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02031a60` | yes | `ff2c ac80 0007` | `ifs (r10 > 16384) goto 14 <_start+0x31954 : 2031a74 >` |
| `02058d36` | no | `ff2c dd00 017d` | `ifs (r13 > 8192) goto 762 <_start+0x58F16 : 2059036 >` |

Accepted forms with the same top 12 bits: `ff20 0c00 0005` `if (r0 == 32768) goto 10 <_start+0x442C : 200454c >`; `ff21 1c00 000a` `if (r1 != 32768) goto 20 <_start+0x5E80 : 2005fa0 >`; `ff23 1d80 000e` `if (r1 < 4096) goto 28 <_start+0xCFAC : 200d0cc >`; `ff28 2d00 fffb` `if (r2 > 8192) goto -10 <_start+0x4A92 : 2004bb2 >`; `ff29 1b97 0001` `if (r1 <= 77312) goto 2 <_start+0x9FEE : 200a10e >`; `ff2a 0d7c 0002` `ifs (r0 >= 16128) goto 4 <_start+0x5EC4 : 2005fe4 >`.

### `110e`

1 occurrences, 1 reachable; 2 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202b4d8` | yes | `110e` | `b[r0++=r10] = r6` |

### `e1d8`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202cbda` | yes | `e1d8 0202` | `r1_r0 >>= r2` |

Accepted forms with the same top 12 bits: `e1d0 e80c` `r15_r14 >>= 12`; `e1d8 2000` `r3_r2 <<= r0`.

### `e825`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d99a` | yes | `e825 0480` | `if (r5 == 1073741824) {` |

### `e8d5`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202cbde` | yes | `e8d5 0df0` | `{pc, r11, r10, r8-r4} = [sp++]` |

Accepted forms with the same top 12 bits: `e8d8 ffff` `[--sp] = {r15-r0}`.

### `e8d9`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202cacc` | yes | `e8d9 0df0` | `[--sp] = {rets, r11, r10, r8-r4}` |

Accepted forms with the same top 12 bits: `e8d8 ffff` `[--sp] = {r15-r0}`.

### `ec20`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200d386` | yes | `ec20 0ca0` | `if (r0 > 20480) {` |

### `ed53`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `02017944` | yes | `ed53 8fbd` | `h[r11+-4] = r8` |

Accepted forms with the same top 12 bits: `ed50 00b0` `r0 = h[r11+0] (u)`; `ed51 119f` `h[r9+286] = r1`; `ed54 133e` `r1 = h[r3+62] (s)`; `ed57 7fbc` `r7 = h[r11+-4] (s)`; `ed58 c2e2` `r12 = h[++r14=34] (u)`; `ed59 0811` `h[++r1=384] = r0`.

### `ee2c`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202dc0a` | yes | `ee2c 0c40` | `ifs (r12 > 49152) {` |

### `f0b8`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0202d064` | yes | `f0b8 d330` | `r13 = r3 + r3 + c  #` |

Accepted forms with the same top 12 bits: `f0b4 6582` `r6 = r8 - r5  #`.

### `f194`

1 occurrences, 1 reachable; 4 bytes.

| Address | Reachable | Halfwords | Vendor disassembly |
| --- | --- | --- | --- |
| `0200c012` | yes | `f194 3630` | `r3 = r3 / (1 << r6)  #` |

Accepted forms with the same top 12 bits: `f190 1143` `r1 = r4 & ~r1  #`.

### Runtime-guarded forms

Accepted, but fault for some register values (a bit index of 32 or more, a REP body that is not a qualified linear block when the count is nonzero).

| Opcode | Occurrences | Reachable | Example | Vendor disassembly |
| --- | --- | --- | --- | --- |
| `0303` | 18 | 1 | `02017cf6` | `rep 2 r3 {` |
| `03eb` | 1 | 1 | `0202fcb6` | `rep 30 r11 {` |
| `e194` | 128 | 76 | `02001260` | `r1 = r2 & (1 << r1)` |
| `e866` | 11 | 8 | `020036b6` | `[r0+120] /= 1 << r2` |
| `f194` | 3 | 3 | `02010aac` | `r0 = r4 & (1 << r1)  #` |
