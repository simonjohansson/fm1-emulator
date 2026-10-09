# Instruction gaps

Generated on @DATE@ by `tools/decode_sweep.py`; do not edit by hand.

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
