# FM-1 QEMU saved-firmware instruction coverage

## Current status

Batch D reviewed 18 saved-corpus forms: 16 have tested qualified implementations;
EE53 byte signed-offset store and EC50 kind3 doubleword pre-store stay
unsupported. These admission totals come from the final production decoder
extraction; focused and relevant QEMU regression checks passed.

| Revision | Admitted sites | Rejected sites | Known width gaps |
| --- | ---: | ---: | ---: |
| Frozen baseline | 117,088 | 3,748 | 496 |
| Accepted A | 117,441 | 3,395 | 496 |
| Accepted B | 117,937 | 2,899 | 0 |
| Accepted C | 118,062 | 2,774 | 0 |
| Current D | 118,358 | 2,478 | 0 |

Current remaining forms / role rows: **23 / 27**.
New admissions since C: **296**, with **0**
admission regressions. Unchanged Felucca rejected sites: **197**.

**Static admission is not runtime correctness.** The offline inventory does
not establish reachability, execution frequency, performance, flags, memory,
predicate completion, IRQ behavior or complete firmware compatibility.
Focused execution gates and unchanged-firmware progress are separate evidence.
The latest runtime checkpoint and next reached gap are in [BOOTING.md](BOOTING.md).

## Inputs and method

The frozen union contains **37 artifact paths, 35 ELF identities and 21
executable byte/VMA payloads**, with **120,836 confirmed instruction sites**.
The target is QEMU 11.1.2. Primary encoding evidence is Apache SLEIGH commit
`e1bd0707874b77b759401555d24839ad43af1267`, checked against saved vendor bytes.
Seventeen loader contexts share executable contents and count once.
The corrected starting denominator is **76 missing forms / 82 role rows**;
the original 75/81 inventory merged an unsigned maximum placement incorrectly.

Inputs are saved ELF/vendor-disassembly pairs for unchanged Felucca,
diagnostic, display, probe, foundation, diagnostic variants and their loaders.
The instrumented Felucca profile is an inventory input only. Artifact hashes
establish identity; they do not establish a reproducible build from current
source. Stock raw wrappers and SDK ELFs without matching disassembly are outside
this corpus. Other compiler configurations can expose further instructions.

Parsing checks vendor bytes against executable ELF sections, uses symbol
boundaries and excludes verified data. The host harness extracts the actual
ordered C decoder, parallel classifier and instruction-width scanner; it
does not execute QEMU or the copied reference. Destination-conflict rejection
and unsupported runtime policies remain distinct from opcode gaps.

Detailed paths, hashes, every counted site, parser exclusions and frozen form
mappings remain in the evidence directories listed below. The **71 opaque
E53F regions / 12 complete raw patterns** stay outside confirmed form counts.

## Remaining groups and qualified limits

| Group | Remaining work |
| --- | --- |
| A carry-over | Compact RETS push 04C8; unsigned parallel maximum mode0 |
| C | EC20 vendor-only IF; E8A0 nonzero low12 operands remain outside its admitted subset |
| D | EE53 signed-offset byte store; EC50 kind3 doubleword pre-store |
| E | Eight register-pair arithmetic/shift forms |
| F | Four special-register/control forms: extended push, SSYNC, trigger, IDLE |
| G | Four testset, flag-EQ branch and repeat forms |
| H | Two qualified floating branches; opaque decoding research is separate |

D update-load destination/base aliases remain restricted. ED5C admission is
confined to the reviewed canonical selector; ED5D–F, odd operands and stores
stay unsupported. ECD8 selectors6/E and E9D9 odd lowbit stay unsupported.
EEDC, ED58-store and ECD0-store source/base aliases remain explicit faults.
EB20 requires a nonempty bitmap, stores ascending selected registers from the
incoming base and does not update that GPR; its primary memory direction
disagreement is retained. Parallel admission retains explicit destination-conflict checks.

Access-fault writeback and bitmap partial stores are qualified model policies,
without hardware fault-state proof. E868 add/sub performs one word read and
one word write, including operand zero; failed writes retain earlier MMIO read
effects. Existing-device success observations and source-inspected read
retention after a failed write are recorded separately. Private predicate state
is not captured.
No firmware name, hash or guest PC selects production CPU behavior.

## Evidence and next action

Frozen inventory: `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-opcode-coverage-2026-10-08/`.
Current D evidence: `/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/`.
Manifests and acceptance receipts retain exact source, QEMU, copied-reference,
firmware and fixture hashes, raw disagreements and actual check results.

Continue from the observed bootguard state using [plan.md](../plan.md). The
firmware runs for 30 guest seconds from entry. Verified healthy HOME, bootguard
clearance, nonzero synthesis, physical input, the running native viewer and
broader unchanged-firmware compatibility remain acceptance goals.
