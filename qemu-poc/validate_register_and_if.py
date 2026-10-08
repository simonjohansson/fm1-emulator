#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact EA10 register-AND IF zero/nonzero predicates.

Pinned Apache ifthenelse.sinc:493-507/514-528 specifies canonical second-word
low byte00 for AND==0 and80 for AND!=0. Vendor EA13/0180 at0x0200A842 selects
r9=2 when incoming r3&r1 is nonzero. Left is first-word bits0:3 and right is
second-word bits8:11; THEN count is bits14:15+1, ELSE count is bits12:13.
There is no encoded zero-length THEN arm. The misleading primary comment
before the nonzero constructor is retained in the separate primary evidence;
its executable constructor/body and vendor establish the nonzero selector.
The public standalone reference accepts all18 tested noncanonical low bytes;
QEMU retains the primary canonical admission rule before IF effects/count.
That difference does not establish hardware validity of the unused bits.

Balanced nonnested arms cover scalar2/4/6 and bundle4/6/8 widths and a following
independent IF. Predicate inputs are latched before selected bodies overwrite
them. PSR/RETS stay unchanged for this nonflagging fixture vocabulary. Existing
nested/final-CALL/final-FF0C/final-FF41/taken-exit restrictions remain explicit
model limits with raw contradictory reference outcomes. Taken exit retires
its branch before the following IF faults; retained predicates block IRQ by
source inspection only, not IRQ validation. Existing helpers, scanner and
public state fields remain fixed. Reference fatal records show access category
only; no reference fault state or hardware completion/fault claim is made.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

import validate
import validate_isa as isa
from validate_peripherals import Guest

HERE=Path(__file__).resolve().parent
CACHE=HERE/".cache/register-and-if-validation"
ENTRY=0x02000120
INSPECTION=0x01C08000
STACK=INSPECTION-16
PSR=0x89ABCDE5
RETS=0x12345678
GPRS=[0x10203040+i*0x01010101 for i in range(16)]
GPRS[0]=0x01C7FE08
MARKERS=[0x12345678,0x89ABCDEF,0x76543210]
INITIAL_INSPECTION=[0xA5A5A5A5]*5+[0]*3+[0xA5A5A5A5]*4


def setup(registers,psr=PSR,stack=STACK,seed=False,guard=None):
    guest=Guest()
    if seed:
        for index,value in enumerate(MARKERS):
            guest.write(INSPECTION+index*4,value)
    if guard=="write":
        guest.write(0x01EEE240,0xE7)
        guest.write(0x01EEE2C0,INSPECTION)
        guest.write(0x01EEE280,INSPECTION)
        guest.write(0x01EEE348,1)
    elif guard in ("header","body"):
        guest.write(0x01EEE240,0xE7)
        # The remaining two guard writes precede the full-state initializers.
        header=guest.pc+28+122  # Two14-byte guard writes, then the full-state initializers.
        guest.write(0x01EEE380,header-1 if guard=="header" else header+3)
        guest.write(0x01EEE384,ENTRY)
    guest.literal(4,psr)
    guest.emit(0xE064,0x4580)
    guest.literal(4,RETS)
    guest.emit(0xE064,0x4380)
    guest.literal(14,stack,special=True)
    expected=list(GPRS)
    for register,value in registers.items(): expected[register]=value&0xFFFFFFFF
    for register,value in enumerate(expected): guest.literal(register,value)
    return guest,expected


def specials(psr=PSR,stack=STACK):
    expected=[0]*16
    expected[3]=RETS
    expected[5]=psr
    expected[14]=stack
    return expected


def save_image(name,guest):
    image=CACHE/f"{name}.bin"
    image.write_bytes(guest.bytes()+bytes(16))
    return image

# Independent finite fixture vocabulary uses only accepted nonflagging forms.
def emit(guest,op):
    kind,*args=op
    if kind=='nop': guest.emit(0)
    elif kind=='lit4': guest.emit(0xe040|args[0],args[1])
    elif kind=='lit6': guest.literal(*args)
    elif kind=='mov': guest.emit(0x1600|args[0]|(args[1]<<4))
    elif kind=='bundle4': guest.emit(0xd608,0x1609)
    elif kind=='bundle6': guest.emit(0xd608,0xe049,0x2222)
    elif kind=='bundle8': guest.emit(0xf048,0x1111,0xe049,0x2222)
    elif kind=='spstore': guest.emit(0x2080|args[0]|(args[1]//4<<8))
    elif kind=='store': guest.store(*args)
    else: raise ValueError(op)

def apply(expected,inspection,op,stack):
    kind,*args=op
    if kind in ('lit4','lit6'): expected[args[0]]=args[1]&0xffffffff
    elif kind=='mov': expected[args[0]]=expected[args[1]]
    elif kind=='bundle4': expected[8]=expected[9]=expected[0]
    elif kind=='bundle6': expected[8]=expected[0]; expected[9]=0x2222
    elif kind=='bundle8': expected[8]=0x1111; expected[9]=0x2222
    elif kind=='spstore': inspection[(stack+args[1]-INSPECTION)//4]=expected[args[0]]
    elif kind=='store': inspection[(expected[args[1]]+args[2]-INSPECTION)//4]=expected[args[0]]

def success_case(name,leftreg=3,rightreg=1,left=1,right=1,nonzero=True,
                 then=(("lit4",8,0x1111),),otherwise=(),psr=PSR,stack=STACK,registers=None):
    incoming=dict(registers or {})
    incoming[leftreg]=left
    incoming[rightreg]=right
    guest,expected=setup(incoming,psr,stack,seed=True)
    inspection=MARKERS+INITIAL_INSPECTION[3:]
    assert 1<=len(then)<=4 and 0<=len(otherwise)<=3
    low=0x80 if nonzero else 0
    encoded=(rightreg<<8)|((len(then)-1)<<14)|(len(otherwise)<<12)|low
    guest.emit(0xea10|leftreg,encoded)
    for op in (*then,*otherwise): emit(guest,op)
    actualleft,actualright=expected[leftreg],expected[rightreg]
    masked=actualleft&actualright
    selected=bool(masked) if nonzero else not bool(masked)
    for op in then if selected else otherwise: apply(expected,inspection,op,stack)
    skipped=len(otherwise) if selected else len(then)
    # Completion is tested by an independent IF after both arms.
    guest.emit(0xe04a,0x3333)
    expected[10]=0x3333
    guest.emit(0xea20,1)
    guest.emit(0xe04c,0x7777)
    if expected[0]&1: skipped+=1
    else: expected[12]=0x7777
    guest.emit(0)
    image=save_image(name,guest)
    state=isa.compare(name,image,guest.pc,limit=100)
    validate.check(state["pc"]==guest.pc and state["instructions"]==guest.instructions-skipped,
                   f"{name}: arm counts, instruction sizing or retirement differ")
    validate.check(state["registers"]==expected and state["specials"]==specials(psr,stack),
                   f"{name}: AND predicate, latched inputs, selected effects, PSR or RETS differ")
    validate.check(state["inspection"]==inspection,
                   f"{name}: selected memory effects or neighbors differ")
    evidence={"opcode":0xEA10|leftreg,"second_word":encoded,
              "incoming_left":actualleft,"incoming_right":actualright,"incoming_AND":masked,
              "nonzero_selector":nonzero,"selected":selected,"canonical_low_byte":low,
              "then_count":len(then),"else_count":len(otherwise),
              "balanced_nonnested_followup_if_completed":True}
    (CACHE/f"{name}-evidence.json").write_text(json.dumps(evidence,indent=2)+"\n")
    return image,state


def reference_record(image,env):
    command=["mise","exec","--","cargo","run","--manifest-path",
             str(HERE/"reference/Cargo.toml"),"--locked","--offline","--","snapshot",str(image)]
    result=subprocess.run(command,cwd=validate.ROOT,env=env,text=True,capture_output=True,timeout=15)
    record={"reference_command":command,"reference_returncode":result.returncode,
            "reference_stdout":result.stdout,"reference_stderr":result.stderr}
    if result.returncode==0: record["reference_state"]=json.loads(result.stdout)
    return record


def fault_snapshot(name,guest,expected,stop,pc,count,reason,access,oracle=False):
    image=save_image(name,guest)
    directory=CACHE/name
    directory.mkdir(exist_ok=True)
    for filename in ("state.json","state.sram","state.alnk"):
        (directory/filename).unlink(missing_ok=True)
    env={key:value for key,value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings={"FM1_POC_STOP_PC":hex(stop),"FM1_POC_MAX_INSTRUCTIONS":"100",
              "FM1_POC_STATE_DIR":str(directory)}
    env.update(settings)
    command=[*validate.COMMAND,"-kernel",str(image),"-append","alnk-probe"]
    result=subprocess.run(command,cwd=validate.ROOT,env=env,text=True,capture_output=True,timeout=15)
    state_path=directory/"state.json"
    validate.check(state_path.exists(),f"{name}: missing fault snapshot: {result.stderr}")
    state=json.loads(state_path.read_text())
    validate.check(result.returncode!=0 and state["reason"]==reason and
                   state["pc"]==pc and state["instructions"]==count,
                   f"{name}: reason, PC or fault retirement stage differs: {result.stderr}")
    validate.check(state["last_access"]==access,f"{name}: fault address/width differs")
    validate.check(state["registers"]==expected and state["specials"]==specials(),
                   f"{name}: fault applied register, PSR, RETS or continuation effects")
    memory=(directory/"state.sram").read_bytes()
    # alnk-probe cold-zeros unowned SRAM; normal reference fixtures retain INITIAL_INSPECTION.
    validate.check(len(memory)==0x80000 and struct.unpack_from("<12I",memory,0x8000)==tuple(MARKERS+[0]*9),
                   f"{name}: destination or neighboring memory changed")
    if "guard windows" in reason:
        validate.check(state["guards"]["debug_message"]&(1<<12),f"{name}: PC guard fault not latched")
    record={"command":command,"environment":settings,"returncode":result.returncode,
            "stderr":result.stderr,"state":state,
            "fixture_sha256":hashlib.sha256(image.read_bytes()).hexdigest(),
            "model_policy_only":True,"hardware_fault_state_validation":False}
    if oracle: record.update(reference_record(image,env))
    return directory,record


def low_byte_fault(low):
    name=f"canonical-admission-low-byte-{low:02x}"
    guest,expected=setup({3:1,1:1},seed=True)
    pc,count=guest.pc,guest.instructions
    guest.emit(0xEA13,0x0100|low)
    guest.emit(0xE048,0x1111)
    guest.emit(0xE04D,0x3344)
    directory,record=fault_snapshot(name,guest,expected,guest.pc,pc,count,
        "unsupported instruction 0xea13",{"address":pc,"size":4,"flags":2},oracle=True)
    oracle_expected=list(expected)
    oracle_expected[13]=0x3344
    selected=bool(low&128)  # Incoming AND is1, so the selector alone decides.
    if selected: oracle_expected[8]=0x1111
    validate.check(record["reference_returncode"]==0,f"{name}: reference byte behavior changed")
    state=record["reference_state"]
    validate.check(state["pc"]==guest.pc and state["instructions"]==count+2+selected and
                   state["registers"]==oracle_expected and state["specials"]==specials() and
                   state["inspection"]==MARKERS+INITIAL_INSPECTION[3:],
                   f"{name}: raw reference bit7-equivalent behavior changed")
    record.update(primary_constraint="imm1623=0 or0x80",noncanonical_low_byte=low,
                  admission="canonical low7 zero; hardware unused-bit behavior unverified",
                  reference_ignores_tested_low7=True,reference_selected=selected)
    (directory/"run.json").write_text(json.dumps(record,indent=2)+"\n")
    print(f"PASS {name}: canonical admission and full reference disagreement recorded")


def inherited_fixture(kind):
    registers={3:1,1:1,0:0}
    # The CALL target replaces r3 below; keep its AND predicate selected.
    if kind=="final-call": registers[1]=0xFFFFFFFF
    if kind=="taken-exit": registers.update({14:0,15:1})
    if kind=="final-ff0c": registers[15]=1
    if kind=="final-ff41": registers[0]=1  # Untaken NE; still guarded at final THEN+ELSE.
    guest,expected=setup(registers,seed=True)
    before=guest.instructions
    if kind=="nested":
        guest.emit(0xEA20,1)
        pc=guest.pc
        guest.emit(0xEA13,0x4180)
        guest.emit(0xE048,0x1111)
        guest.emit(0xE049,0x2222)
        reason="nested conditional block is unsupported"
        span=4
    else:
        guest.emit(0xEA13,0x1180)  # One selected THEN and one ELSE instruction.
        pc=guest.pc
        if kind=="final-call":
            guest.emit(0x00C3)
            reason="final THEN call with ELSE is unsupported"
            span=2
        elif kind=="final-ff0c":
            guest.emit(0xFF0C,0xF000,0)
            reason="final THEN signed-literal branch with ELSE is unsupported"
            span=6
        elif kind=="final-ff41":
            guest.emit(0xFF41,0x0100,0)
            reason="final THEN FF41 register branch with ELSE is unsupported"
            span=6
        elif kind=="taken-exit":
            guest.emit(0xEE0E,0xF004)  # Signed GT exits past ELSE/continuation to next IF.
            span=4
            reason="nested conditional block is unsupported"
        else: raise ValueError(kind)
        guest.emit(0xE049,0x2222)
    guest.emit(0xE04A,0x3333)
    if kind=="taken-exit":
        pc=guest.pc
        guest.emit(0xEA20,1)
        guest.emit(0xE04C,0x7777)
    guest.emit(0)
    stop=guest.pc
    if kind=="final-call":
        guest.emit(0)  # Callee is beyond the main checkpoint.
        callee=guest.pc
        guest.emit(0)
        guest.emit(0x0080)
        index=guest.words.index(0xFFC3)
        guest.words[index+1:index+3]=[callee&0xFFFF,callee>>16]
        expected[3]=callee
        assert expected[3]&expected[1]  # CALL must execute in the selected THEN arm.
    return guest,expected,stop,pc,before+(2 if kind=="taken-exit" else 1),reason,span


def inherited_fault(kind):
    name=f"inherited-{kind}"
    guest,expected,stop,pc,count,reason,span=inherited_fixture(kind)
    directory,record=fault_snapshot(name,guest,expected,stop,pc,count,reason,
        {"address":pc,"size":span,"flags":2},oracle=True)
    record.update(limitation=kind,raw_reference_outcome_retained=True,
                  reference_fault_state_available=False)
    if kind=="taken-exit":
        oracle_expected=list(expected)
        oracle_expected[12]=0x7777
        validate.check(record["reference_returncode"]==0,f"{name}: reference completion failed")
        state=record["reference_state"]
        validate.check(state["pc"]==stop and state["instructions"]==count+3 and
                       state["registers"]==oracle_expected and state["specials"]==specials() and
                       state["inspection"]==MARKERS+INITIAL_INSPECTION[3:],
                       f"{name}: reference completion disagreement changed")
        record.update(branch_already_retired=True,reference_full_completion_checked=True,
                      reference_completion_disagreement=True,
                      retained_predicate_irq_blocking="source inspected; not IRQ validation")
    else:
        record["reference_completion_semantics_verified"]=False
        record["raw_disagreement_scope"]="Inherited predicate/control-transfer behavior; no common helper expansion"
    (directory/"run.json").write_text(json.dumps(record,indent=2)+"\n")
    print(f"PASS {name}: inherited model stage and raw reference behavior recorded")


def guard_fault(stage):
    name=f"pc-guard-{stage}"
    guest,expected=setup({3:1,1:1},seed=True,guard=stage)
    header,before=guest.pc,guest.instructions
    guest.emit(0xEA13,0x0180)
    body=guest.pc
    guest.emit(0)
    guest.emit(0xE04D,0x3344)
    pc,count,span=(header,before,4) if stage=="header" else (body,before+1,2)
    directory,record=fault_snapshot(name,guest,expected,guest.pc,pc,count,
        "guest PC lies outside both configured guard windows",
        {"address":pc,"size":span,"flags":2},oracle=True)
    oracle_expected=list(expected)
    oracle_expected[13]=0x3344
    validate.check(record["reference_returncode"]==0,f"{name}: reference completion failed")
    state=record["reference_state"]
    validate.check(state["pc"]==guest.pc and state["instructions"]==guest.instructions and
                   state["registers"]==oracle_expected and state["specials"]==specials() and
                   state["inspection"]==MARKERS+INITIAL_INSPECTION[3:],
                   f"{name}: configured PC-guard reference disagreement changed")
    record.update(if_header_already_retired=stage=="body",reference_completion_disagreement=True,
                  reference_full_completion_checked=True,hardware_guard_policy_validation=False)
    (directory/"run.json").write_text(json.dumps(record,indent=2)+"\n")
    print(f"PASS {name}: modeled instruction guard at the exact retirement stage")


def store_fault(kind):
    addresses={"unaligned":INSPECTION+1,"unmapped":0x18000000,"read-only":ENTRY,"guarded":INSPECTION}
    reasons={"unaligned":"unaligned access","unmapped":"unmapped access at 0x18000000",
             "read-only":"write to read-only XIP (NOR)",
             "guarded":"CPU write intersects an enabled guest guard window"}
    categories={"unaligned":"unaligned access","unmapped":"unmapped",
                "read-only":"read-only","guarded":"CPU write protection violation"}
    name=f"selected-store-{kind}"
    address=addresses[kind]
    guest,expected=setup({3:1,1:1,5:0x89ABCDEF,7:address},seed=True,
                         guard="write" if kind=="guarded" else None)
    before=guest.instructions
    guest.emit(0xEA13,0x0180)
    pc=guest.pc
    guest.store(5,7)
    guest.emit(0xE04D,0x3344)
    directory,record=fault_snapshot(name,guest,expected,guest.pc,pc,before+1,reasons[kind],
                                   {"address":address,"size":4,"flags":1},oracle=True)
    validate.check(record["reference_returncode"]!=0 and
                   f'address: {address}, size: 4, operation: "write"' in record["reference_stderr"] and
                   categories[kind] in record["reference_stderr"],
                   f"{name}: reference access category/address/width/direction differs")
    record.update(if_header_already_retired=True,reference_fatal_category_checked=True,
                  reference_fault_state_available=False)
    (directory/"run.json").write_text(json.dumps(record,indent=2)+"\n")
    print(f"PASS {name}: selected body fault after IF retirement, before store effects")


def generic_replay(image,expected):
    directory=CACHE/"generic-replay"
    directory.mkdir(exist_ok=True)
    for filename in ("state.json","state.sram","state.alnk"):
        (directory/filename).unlink(missing_ok=True)
    env={key:value for key,value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings={"FM1_POC_STOP_PC":hex(expected["pc"]),"FM1_POC_MAX_INSTRUCTIONS":"100",
              "FM1_POC_STATE_DIR":str(directory)}
    env.update(settings)
    command=[*validate.COMMAND,"-kernel",str(image)]
    result=subprocess.run(command,cwd=validate.ROOT,env=env,text=True,capture_output=True,timeout=15)
    validate.check(result.returncode==0,f"generic-replay: {result.stderr}")
    state=json.loads((directory/"state.json").read_text())
    for field in ("pc","instructions","registers","specials"):
        validate.check(state[field]==expected[field],f"generic-replay: {field} differs")
    memory=(directory/"state.sram").read_bytes()
    validate.check(len(memory)==0x80000 and
                   struct.unpack_from("<12I",memory,0x8000)==tuple(MARKERS+[0]*9),
                   "generic-replay: explicitly owned memory or default-loader neighbors differ")
    (directory/"run.json").write_text(json.dumps({"command":command,"environment":settings,
        "returncode":result.returncode,"stderr":result.stderr,"state":state,
        "default_loader":True,"optional_observers_configured":True},indent=2)+"\n")
    print("PASS generic-replay: default loader matches captured zero-AND IF and owned memory")


def main():
    CACHE.mkdir(parents=True,exist_ok=True)
    isa.CACHE=CACHE
    cases=[]
    for nonzero in (False,True):
        for register in range(16):
            for selected in (False,True):
                cases.append(dict(name=f"fields-{int(nonzero)}-{register}-{int(selected)}",
                    leftreg=register,rightreg=(register+1)%16,
                    left=int(selected==nonzero),right=1,nonzero=nonzero))
            for value in (0,0x80000000):
                cases.append(dict(name=f"alias-{int(nonzero)}-{register}-{value:08x}",
                    leftreg=register,rightreg=register,left=value,right=value,nonzero=nonzero))
        for left,right in ((0,0),(0,0xFFFFFFFF),(0xFFFFFFFF,0),(1,1),(1,2),
                           (0x80000000,0x80000000),(0x80000000,0x7FFFFFFF),
                           (0xFFFFFFFF,0x80000000),(0x55555555,0xAAAAAAAA),
                           (0x55555555,0x55555555),(0x01234567,0xFEDCBA98),
                           (0x01234567,0x81234567)):
            cases.append(dict(name=f"patterns-{int(nonzero)}-{left:08x}-{right:08x}",
                leftreg=15,rightreg=14,left=left,right=right,nonzero=nonzero,
                otherwise=(("lit4",9,0x2222),)))
        for then_count in range(1,5):
            for else_count in range(4):
                for selected in (False,True):
                    cases.append(dict(name=f"counts-{int(nonzero)}-{then_count}-{else_count}-{int(selected)}",
                        nonzero=nonzero,left=int(selected==nonzero),right=1,
                        then=tuple(("lit4",8,0x1100+i) for i in range(then_count)),
                        otherwise=tuple(("lit4",9,0x2200+i) for i in range(else_count))))
        widths=[("nop",),("lit4",8,0x1111),("lit6",8,0x89ABCDEF),
                ("bundle4",),("bundle6",),("bundle8",)]
        for index,op in enumerate(widths):
            for selected in (False,True):
                cases.append(dict(name=f"width-{int(nonzero)}-{index}-{int(selected)}",
                    nonzero=nonzero,left=int(selected==nonzero),right=1,
                    then=(op,),otherwise=(("lit4",9,0x2222),)))
        for selected in (False,True):
            cases.append(dict(name=f"mixed-{int(nonzero)}-{int(selected)}",
                nonzero=nonzero,left=int(selected==nonzero),right=1,
                then=tuple(widths[:4]),otherwise=tuple(widths[3:])))
        for psr in (0,0xFFFFFFFF):
            cases.append(dict(name=f"PSR-{int(nonzero)}-{psr:08x}",
                nonzero=nonzero,left=0x80000000,right=0x80000000,psr=psr))
        # Both selectors skip an invalid memory operation; no body access occurs.
        cases.append(dict(name=f"skipped-unmapped-store-{int(nonzero)}",nonzero=nonzero,
            left=int(not nonzero),right=1,stack=0x18000000-16,
            registers={5:0x89ABCDEF},then=(("spstore",5,16),)))
    for selected in (False,True):
        for inputreg in (3,1):
            cases.append(dict(name=f"latched-input-{inputreg}-{int(selected)}",
                left=int(selected),right=1,
                then=(("lit4",inputreg,0),("mov",8,inputreg)),otherwise=(("lit4",9,0x2222),)))
        cases.append(dict(name=f"GPR14-specialSP-{int(selected)}",left=int(selected),rightreg=14,
            right=1,then=(("lit4",14,0x2222),),otherwise=(("lit4",9,0x3333),)))
        cases.append(dict(name=f"selected-store-{int(selected)}",left=int(selected),right=1,
            registers={5:0x89ABCDEF},then=(("spstore",5,16),)))
    cases.append(dict(name="actual-captured-zero-AND",left=1,right=0,registers={9:0},
                      then=(("lit4",9,2),)))
    cases.append(dict(name="reached-nonzero-discriminator",left=1,right=1,registers={9:0},
                      then=(("lit4",9,2),)))
    assert len(cases)==260
    replay=None
    for case in cases:
        result=success_case(**case)
        if case["name"]=="actual-captured-zero-AND": replay=result
    generic_replay(*replay)
    for selector in (0,128):
        for bits in (1,2,3,4,8,16,32,64,127): low_byte_fault(selector|bits)
    for kind in ("nested","final-call","final-ff0c","final-ff41","taken-exit"): inherited_fault(kind)
    for stage in ("header","body"): guard_fault(stage)
    for kind in ("unaligned","unmapped","read-only","guarded"): store_fault(kind)
    summary={"passed":True,"instruction":"exact EA10/FFF0 register-AND IF bit7 zero/nonzero selector",
             "reference_compared_cases":len(cases),"generic_replays":1,"total_model_faults":29,
             "canonical_low7_faults":18,"inherited_predicate_limits":5,"pc_guard_faults":2,
             "selected_body_access_faults":4,"canonical_policy_reference_full_completions":18,
             "pc_guard_reference_full_completions":2,"inherited_raw_reference_outcomes":5,
             "inherited_taken_exit_reference_full_completion":1,"reference_fatal_category_checks":4,
             "primary_if_blob":"4ee88595bc41e98cd2d58bcdde90bc19f4c19a57",
             "canonical_policy":"primary lowbyte00/80; reference ignores tested low7 bits; hardware validity unverified",
             "condition_latched_before_body_input_overwrites":True,
             "conditional_completion":"balanced nonnested arms verified; inherited control-transfer limits retained",
             "irq_limit":"retained predicate blocks IRQ by source inspection; not IRQ validation",
             "qemu_sha256":hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
             "hardware_validation":False,"hardware_fault_state_validation":False,
             "reference_fault_state_available":False}
    (CACHE/"validation.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(f"PASS EA10: {len(cases)} reference comparisons, generic replay and 29 model faults")


if __name__=="__main__":
    main()
