#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact ED10 signed-register GE IF in the existing predicate path.

Vendor ED13/4100 compares signed r3 >= r1 with two THEN instructions. Pinned
Apache ifthenelse.sinc:304-313 maps left GPR to first-word bits0:3, right GPR
to second-word bits8:11, explicitly requiring a zero low byte. The independent
reference ignores tested nonzero bytes; this gate retains canonical admission
and raw disagreement without claiming hardware reserved-bit behavior.
Then count is bits14:15 + 1; else count is bits12:13. Balanced nonnested
completion, full register fields/aliases and scalar/bundle widths are tested.
PSR and RETS stay unchanged when selected arms do not change them. Reached
E435/0031 signed-min remains deferred: its fault follows IF and literal
retirement, with the literal's r0 update preserved. Positive arm fixtures use
already-supported instructions. No existing register/literal IF, classifier,
scanner/helper or signed-min implementation changes.
Inherited nested/final-CALL/final-FF0C/taken-exit limits retain raw reference
behavior; exits are not IRQ/completion proof. Hardware faults and undocumented
predicate shadow state remain unverified. No ISA-invalid claim is made.
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
CACHE=HERE/".cache/signed-register-if-validation"
ENTRY=0x02000120
INSPECTION=0x01C08000
STACK=INSPECTION-16
PSR=0x89ABCDE5
RETS=0x12345678
GPRS=[0x10203040+i*0x01010101 for i in range(16)]
GPRS[0]=0x01C7FE08
MARKERS=[0x12345678,0x89ABCDEF,0x76543210]
INITIAL_INSPECTION=[0xA5A5A5A5]*5+[0]*3+[0xA5A5A5A5]*4


def signed(value):
    return value if value<0x80000000 else value-0x100000000


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

def success_case(name,leftreg=3,rightreg=1,left=0,right=0xFFFF8000,then=(('lit4',8,0x1111),),otherwise=(),psr=PSR,stack=STACK,registers=None):
    incoming=dict(registers or {})
    incoming[leftreg]=left
    incoming[rightreg]=right
    guest,expected=setup(incoming,psr,stack)
    inspection=list(INITIAL_INSPECTION)
    assert 1<=len(then)<=4 and 0<=len(otherwise)<=3
    encoded=(rightreg<<8)|((len(then)-1)<<14)|(len(otherwise)<<12)
    header=guest.pc
    guest.emit(0xed10|leftreg,encoded)
    for op in (*then,*otherwise): emit(guest,op)
    actualleft,actualright=expected[leftreg],expected[rightreg]
    selected=signed(actualleft)>=signed(actualright)
    for op in then if selected else otherwise: apply(expected,inspection,op,stack)
    skipped=len(otherwise) if selected else len(then)
    # The second IF explicitly exercises balanced completion of the first.
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
                   f"{name}: signed predicate, selected effects, PSR or RETS differ")
    validate.check(state["inspection"]==inspection,
                   f"{name}: selected memory effects or neighbors differ")
    evidence={"opcode":0xED10|leftreg,"second_word":encoded,
              "incoming_left":actualleft,"incoming_right":actualright,"selected":selected,
              "canonical_low_byte":0,"then_count":len(then),"else_count":len(otherwise),
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
    validate.check(len(memory)==0x80000 and memory[0x8000:0x800C]==struct.pack("<III",*MARKERS),
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
    guest,expected=setup({3:1,1:0},seed=True)
    pc,count=guest.pc,guest.instructions
    guest.emit(0xED13,0x0100|low)
    guest.emit(0)
    guest.emit(0xE04D,0x3344)
    directory,record=fault_snapshot(name,guest,expected,guest.pc,pc,count,
        "unsupported instruction 0xed13",{"address":pc,"size":4,"flags":2},oracle=True)
    oracle_expected=list(expected)
    oracle_expected[13]=0x3344
    validate.check(record["reference_returncode"]==0,f"{name}: reference byte behavior changed")
    state=record["reference_state"]
    validate.check(state["pc"]==guest.pc and state["instructions"]==count+3 and
                   state["registers"]==oracle_expected and state["specials"]==specials() and
                   state["inspection"]==MARKERS+INITIAL_INSPECTION[3:],
                   f"{name}: raw reference canonical-equivalent behavior changed")
    record.update(primary_constraint="imm1623=0",nonzero_low_byte=low,
                  admission="canonical zero byte; hardware reserved-bit behavior unverified",
                  reference_ignores_tested_byte=True)
    (directory/"run.json").write_text(json.dumps(record,indent=2)+"\n")
    print(f"PASS {name}: canonical admission and reference disagreement recorded")


def deferred_reached_min():
    name="deferred-reached-signed-min"
    guest,expected=setup({3:0,1:0xFFFF8000},seed=True)
    before=guest.instructions
    guest.emit(0xED13,0x4100)
    guest.emit(0xE040,0x7FFF)
    pc=guest.pc
    guest.emit(0xE435,0x0031)
    guest.emit(0xE04D,0x3344)
    expected[0]=32767
    directory,record=fault_snapshot(name,guest,expected,guest.pc,pc,before+2,
        "unsupported instruction 0xe435",{"address":pc,"size":4,"flags":2})
    record.update(if_header_already_retired=True,literal_already_retired=True,
                  signed_min_deferred=True)
    (directory/"run.json").write_text(json.dumps(record,indent=2)+"\n")
    print("PASS deferred-reached-signed-min: fault preserves retired IF/literal effects")


def inherited_fixture(kind):
    registers={3:0,1:0xFFFF8000}
    if kind=="nested": registers[0]=0
    if kind=="taken-exit": registers.update({14:0,15:1})
    if kind=="final-ff0c": registers[15]=1
    guest,expected=setup(registers,seed=True)
    before=guest.instructions
    if kind=="nested":
        guest.emit(0xEA20,1)
        pc=guest.pc
        guest.emit(0xED13,0x4100)
        guest.emit(0xE048,0x1111)
        guest.emit(0xE049,0x2222)
        reason="nested conditional block is unsupported"
        span=4
    else:
        guest.emit(0xED13,0x1100)  # One THEN and one ELSE instruction.
        pc=guest.pc
        if kind=="final-call":
            guest.emit(0x00C3)
            reason="final THEN call with ELSE is unsupported"
            span=2
        elif kind=="final-ff0c":
            guest.emit(0xFF0C,0xF000,0)
            reason="final THEN signed-literal branch with ELSE is unsupported"
            span=6
        elif kind=="taken-exit":
            guest.emit(0xEE0E,0xF004)  # Signed GT jumps past ELSE/continuation to next IF.
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
        guest.emit(0x0080)  # NOP followed by RET; neither executes in the model fault.
        index=guest.words.index(0xFFC3)
        guest.words[index+1:index+3]=[callee&0xFFFF,callee>>16]
        expected[3]=callee
    return guest,expected,stop,pc,before+(2 if kind=="taken-exit" else 1),reason,span


def inherited_fault(kind):
    name=f"inherited-{kind}"
    guest,expected,stop,pc,count,reason,span=inherited_fixture(kind)
    directory,record=fault_snapshot(name,guest,expected,stop,pc,count,reason,
        {"address":pc,"size":span,"flags":2},oracle=True)
    record["limitation"]=kind
    if kind=="taken-exit":
        oracle_expected=list(expected)
        oracle_expected[12]=0x7777
        validate.check(record["reference_returncode"]==0,f"{name}: reference completion failed")
        state=record["reference_state"]
        validate.check(state["pc"]==stop and state["instructions"]==count+3 and
                       state["registers"]==oracle_expected and state["specials"]==specials() and
                       state["inspection"]==MARKERS+INITIAL_INSPECTION[3:],
                       f"{name}: reference completion disagreement changed")
        record.update(branch_already_retired=True,reference_completion_disagreement=True,
                      retained_predicate_irq_blocking="source inspected; not IRQ validation")
    (directory/"run.json").write_text(json.dumps(record,indent=2)+"\n")
    print(f"PASS {name}: inherited model limit and raw reference behavior recorded")


def guard_fault(stage):
    name=f"pc-guard-{stage}"
    guest,expected=setup({3:0,1:0xFFFF8000},seed=True,guard=stage)
    header,before=guest.pc,guest.instructions
    guest.emit(0xED13,0x0100)
    body=guest.pc
    guest.emit(0)
    guest.emit(0xE04D,0x3344)
    pc,count,span=(header,before,4) if stage=="header" else (body,before+1,2)
    directory,record=fault_snapshot(name,guest,expected,guest.pc,pc,count,
        "guest PC lies outside both configured guard windows",
        {"address":pc,"size":span,"flags":2})
    record["if_header_already_retired"]=stage=="body"
    (directory/"run.json").write_text(json.dumps(record,indent=2)+"\n")
    print(f"PASS {name}: instruction guard at the exact retirement stage")


def store_fault(kind):
    addresses={"unaligned":INSPECTION+1,"unmapped":0x18000000,"read-only":ENTRY,"guarded":INSPECTION}
    reasons={"unaligned":"unaligned access","unmapped":"unmapped access at 0x18000000",
             "read-only":"write to read-only XIP (NOR)",
             "guarded":"CPU write intersects an enabled guest guard window"}
    name=f"selected-store-{kind}"
    address=addresses[kind]
    guest,expected=setup({3:0,1:0xFFFF8000,5:0x89ABCDEF,7:address},seed=True,
                         guard="write" if kind=="guarded" else None)
    before=guest.instructions
    guest.emit(0xED13,0x0100)
    pc=guest.pc
    guest.store(5,7)
    guest.emit(0xE04D,0x3344)
    directory,record=fault_snapshot(name,guest,expected,guest.pc,pc,before+1,reasons[kind],
                                   {"address":address,"size":4,"flags":1})
    record["if_header_already_retired"]=True
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
    validate.check(struct.unpack_from("<IIIII",memory,0x8000)==(32767,0,0,0,0),
                   "generic-replay: selected store or neighboring memory differs")
    (directory/"run.json").write_text(json.dumps({"command":command,"environment":settings,
        "returncode":result.returncode,"stderr":result.stderr,"state":state},indent=2)+"\n")
    print("PASS generic-replay: default loader matches signed IF state and selected store")


def main():
    CACHE.mkdir(parents=True,exist_ok=True)
    isa.CACHE=CACHE
    cases=[]
    for register in range(16):
        cases.extend([
            dict(name=f"fields-selected-{register}",leftreg=register,rightreg=(register+1)%16,
                 left=0x7FFFFFFF,right=0x80000000),
            dict(name=f"fields-skipped-{register}",leftreg=register,rightreg=(register+1)%16,
                 left=0x80000000,right=0x7FFFFFFF),
        ])
        for value in (0,0xFFFFFFFF,0x80000000,0x7FFFFFFF):
            cases.append(dict(name=f"equal-alias-{register}-{value:08x}",leftreg=register,
                              rightreg=register,left=value,right=value))
    for left,right in ((0,0),(1,0),(0,1),(0xFFFFFFFF,0),(0,0xFFFFFFFF),
                       (0x7FFFFFFF,0x80000000),(0x80000000,0x7FFFFFFF),
                       (0x80000000,0xFFFFFFFF),(0xFFFFFFFF,0x80000000),
                       (0x80000000,0x80000000),(0x7FFFFFFF,0x7FFFFFFF)):
        cases.append(dict(name=f"signed-boundary-{left:08x}-{right:08x}",leftreg=15,rightreg=14,
                          left=left,right=right,otherwise=(("lit4",9,0x2222),)))
    for then_count in range(1,5):
        for else_count in range(4):
            for selected in (False,True):
                cases.append(dict(name=f"counts-{then_count}-{else_count}-{selected}",
                    left=1 if selected else 0,right=1,
                    then=tuple(("lit4",8,0x1100+i) for i in range(then_count)),
                    otherwise=tuple(("lit4",9,0x2200+i) for i in range(else_count))))
    widths=[("nop",),("lit4",8,0x1111),("lit6",8,0x89ABCDEF),
            ("bundle4",),("bundle6",),("bundle8",)]
    for index,op in enumerate(widths):
        for selected in (False,True):
            cases.append(dict(name=f"width-{index}-{selected}",left=1 if selected else 0,right=1,
                              then=(op,),otherwise=(("lit4",9,0x2222),)))
    for selected in (False,True):
        cases.append(dict(name=f"mixed-{selected}",left=1 if selected else 0,right=1,
                          then=tuple(widths[:4]),otherwise=tuple(widths[3:])))
        # Preserve actual reached header/register fields; use an accepted
        # store as the second disposable test operation. Actual smin is below.
        cases.append(dict(name=f"reached-header-supported-body-{selected}",
                          left=0 if selected else 0x80000000,right=0xFFFF8000,
                          then=(("lit4",0,32767),("spstore",0,16))))
    for psr in (0,0xFFFFFFFF):
        cases.append(dict(name=f"psr-{psr:08x}",left=0x80000000,right=0x80000000,psr=psr))
    cases.append(dict(name="skipped-unmapped-store",left=0x80000000,right=0xFFFF8000,
                      registers={5:0x89ABCDEF,7:0x18000000},then=(("store",5,7,0),)))
    replay=None
    for case in cases:
        result=success_case(**case)
        if case["name"]=="reached-header-supported-body-True": replay=result
    generic_replay(*replay)
    for low in (1,2,4,8,16,32,64,128,255): low_byte_fault(low)
    deferred_reached_min()
    for kind in ("nested","final-call","final-ff0c","taken-exit"): inherited_fault(kind)
    for stage in ("header","body"): guard_fault(stage)
    for kind in ("unaligned","unmapped","read-only","guarded"): store_fault(kind)
    summary={"passed":True,"instruction":"exact ED10/FFF0 signed register GE IF",
             "reference_compared_cases":len(cases),"generic_replays":1,"total_model_faults":20,
             "canonical_low_byte_faults":9,"deferred_reached_body_faults":1,
             "inherited_predicate_limits":4,"pc_guard_faults":2,"selected_body_access_faults":4,
             "primary_if_blob":"4ee88595bc41e98cd2d58bcdde90bc19f4c19a57",
             "low_byte_policy":"primary imm1623=0; reference ignores tested nonzero bytes; hardware bits unverified",
             "conditional_completion":"balanced nonnested arms verified; inherited control-transfer limits retained",
             "irq_limit":"retained predicate blocks IRQ by source inspection; not IRQ validation",
             "qemu_sha256":hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
             "hardware_validation":False,"hardware_fault_state_validation":False}
    (CACHE/"validation.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(f"PASS ED10: {len(cases)} reference comparisons, generic replay and twenty model faults")


if __name__=="__main__":
    main()
