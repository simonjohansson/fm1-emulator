#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate the nonalias ECD0..7 kind2 pre-indexed immediate word-load constructor.

Pinned Apache loadstore.sinc:131-135 and vendor ECD0/684E establish signed
aligned byte displacement: sext(oplow3,3)*256+x8..11*16+x2..3*4, from-1024
to+1020. Destination is x12..15; base is x4..7 (ordinary GPRs, including r14).
Low2 kind2 selects pre-index load; existing kinds0/1 and doubleword decoder
remain unchanged and kind3 stores stay deferred. No helper/scanner/classifier
or public state change is made. The new load uses incoming base, writes its
computed address, then reads aligned LE32; PSR/RETS/specialSP stay unchanged.

Destination==base is unresolved: primary writeback then load implies load
wins, but all48 standalone reference cases (16regs at0/+140/-4) leave the
address. Original mismatches and separate reference characterizations are
retained. QEMU rejects this alias before effects/access/count; skipped aliases
remain unevaluated. This is conservative model admission, not ISA invalidity.
Faults retain the existing modeled writeback-before-data-access order without
retirement/result; PC/admission faults precede writeback. Fatal oracle records
have category/EA/width/direction only, no CPU fault state or hardware ordering.
QEMU alnk-probe cold-zeros unowned SRAM, while normal reference fixtures retain
diag poisoning. Owned words are seeded explicitly and both profiles are checked.
Balanced selected/skipped mixed-width arms followed by another IF are bounded
completion tests, not general predicate/IRQ completion proof.
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
CACHE=HERE/".cache/preindexed-word-immediate-validation"
ENTRY=0x02000120
INSPECTION=0x01C08000
PSR=0x89ABCDE5
RETS=0x12345678
STACK=INSPECTION-16
GPRS=[0x10203040+i*0x01010101 for i in range(16)]
GPRS[0]=0x01C7FE08
INITIAL_INSPECTION=[0xA5A5A5A5]*5+[0]*3+[0xA5A5A5A5]*4
VALUE=0x89ABCDEF
OLD_VALUE=0x6BADF00D


def operand(dest,base,offset,kind=2):
    assert -1024<=offset<=1020 and offset%4==0
    bits=offset&2047
    return (0xECD0|((bits>>8)&7),
            (dest<<12)|(((bits>>4)&15)<<8)|(base<<4)|(((bits>>2)&3)<<2)|kind)


def specials(psr=PSR):
    expected=[0]*16
    expected[3],expected[5],expected[14]=RETS,psr,STACK
    return expected


def setup(registers,address,value,base_value,psr=PSR,guard=None):
    guest=Guest()
    words={INSPECTION-4:0x13579BDF,INSPECTION:value,INSPECTION+4:0x2468ACE0,
           INSPECTION+8:0x76543210,INSPECTION+12:0x0BADF00D}
    if 0x01C00000<=address<=0x01C7FFFC and not address&3:
        words[address]=value
    if 0x01C00000<=base_value<=0x01C7FFFC and not base_value&3 and base_value!=address:
        words[base_value]=OLD_VALUE
    for at,word in words.items(): guest.write(at,word)
    if guard=="write":
        guest.write(0x01EEE240,0xE7)
        guest.write(0x01EEE2C0,address+3)
        guest.write(0x01EEE280,address+3)
        guest.write(0x01EEE348,1)
    guest.literal(4,psr)
    guest.emit(0xE064,0x4580)
    guest.literal(4,RETS)
    guest.emit(0xE064,0x4380)
    guest.literal(14,STACK,special=True)
    expected=list(GPRS)
    for register,initial in registers.items(): expected[register]=initial&0xFFFFFFFF
    for register,initial in enumerate(expected): guest.literal(register,initial)
    if guard in ("pc-before","pc-last-byte"):
        # These observer writes deliberately replace GPR0/1, tracked below.
        guest.write(0x01EEE240,0xE7)
        guest.write(0x01EEE384,ENTRY)
        header=guest.pc+14
        upper=header-1 if guard=="pc-before" else header+2
        guest.write(0x01EEE380,upper)
        expected[0],expected[1]=upper,0x01EEE380
    return guest,expected,words


def inspection(words,poison=True):
    expected=list(INITIAL_INSPECTION) if poison else [0]*12
    for address,value in words.items():
        if INSPECTION<=address<INSPECTION+48:
            expected[(address-INSPECTION)//4]=value
    return expected


def check_memory(memory,words,name):
    # alnk-probe/default-loader unowned words are cold-zero, not diag poison.
    validate.check(len(memory)==0x80000 and
                   struct.unpack_from("<12I",memory,0x8000)==tuple(inspection(words,poison=False)),
                   f"{name}: cold-profile inspection or neighboring memory differs")
    for address,value in words.items():
        validate.check(struct.unpack_from("<I",memory,address-0x01C00000)[0]==value,
                       f"{name}: owned word at{address:08x} differs")


def save_image(name,guest):
    image=CACHE/f"{name}.bin"
    image.write_bytes(guest.bytes()+bytes(16))
    return image


# Finite nonflagging vocabulary mirrors the reviewed register-IF width fixtures.
def emit(guest,op):
    kind,*args=op
    if kind=="nop": guest.emit(0)
    elif kind=="lit4": guest.emit(0xe040|args[0],args[1])
    elif kind=="lit6": guest.literal(*args)
    elif kind=="bundle4": guest.emit(0xd608,0x1609)
    elif kind=="bundle6": guest.emit(0xd608,0xe049,0x2222)
    elif kind=="bundle8": guest.emit(0xf048,0x1111,0xe049,0x2222)
    else: raise ValueError(op)


def apply(expected,op):
    kind,*args=op
    if kind in ("lit4","lit6"): expected[args[0]]=args[1]&0xFFFFFFFF
    elif kind=="bundle4": expected[8]=expected[9]=expected[0]
    elif kind=="bundle6": expected[8]=expected[0]; expected[9]=0x2222
    elif kind=="bundle8": expected[8]=0x1111; expected[9]=0x2222


def success_case(name,dest=6,base=4,offset=140,value=VALUE,address=INSPECTION,
                 psr=PSR,condition=None,guard=None,kind=2):
    base_value=(address-offset)&0xFFFFFFFF
    incoming={base:base_value}
    if condition is not None: incoming[7]=condition
    guest,expected,words=setup(incoming,address,value,base_value,psr,guard)
    op,x=operand(dest,base,offset,kind)
    selected=condition is None or condition==0
    validate.check(kind!=2 or dest!=base or not selected,f"{name}: selected unresolved alias in positive fixture")
    if condition is not None:
        guest.emit(0xEA27,0xF001)  # THEN4/ELSE3, test selected/skipped mixed widths.
        then=(("nop",),("operation",op,x),("lit6",8,0x11223344),("bundle8",))
        otherwise=(("bundle4",),("bundle6",),("lit4",11,0x5566))
        for item in (*then,*otherwise):
            if item[0]=="operation": guest.emit(item[1],item[2])
            else: emit(guest,item)
        if selected:
            expected[base]=address
            expected[dest]=value
            for item in then[2:]: apply(expected,item)
        else:
            for item in otherwise: apply(expected,item)
        skipped=3 if selected else 4
        guest.emit(0xEA20,1)
        guest.emit(0xE04C,0x7777)
        if expected[0]&1: skipped+=1
        else: expected[12]=0x7777
    else:
        guest.emit(op,x)
        skipped=0
        if kind in (0,2):
            if ENTRY<=address<guest.pc:
                value=struct.unpack_from("<I",guest.bytes(),address-ENTRY)[0]
            if kind==2: expected[base]=address
            expected[dest]=value
        else:
            validate.check(kind==1,f"{name}: unsupported store in positive fixture")
            words[address]=expected[dest]
    guest.emit(0)
    image=save_image(name,guest)
    state=isa.compare(name,image,guest.pc,limit=100)
    validate.check(state["pc"]==guest.pc and state["instructions"]==guest.instructions-skipped,
                   f"{name}: sizing, arm count or word retirement differs")
    validate.check(state["registers"]==expected and state["specials"]==specials(psr),
                   f"{name}: signed offset, incoming base/writeback, word, field, PSR or RETS differs")
    validate.check(state["inspection"]==inspection(words),f"{name}: word/old-address/neighbors changed")
    (CACHE/f"{name}-evidence.json").write_text(json.dumps({"first_word":op,"second_word":x,
        "incoming_base":base_value,"signed_byte_offset":offset,"EA":address,
        "dest":dest,"base":base,"selected":selected,"kind":kind,
        "skipped_unresolved_alias":dest==base and kind==2,
        "bounded_nonnested_followup_IF":condition is not None},indent=2)+"\n")
    return image,state,words


def reference_record(image,stop):
    env={key:value for key,value in os.environ.items() if not key.startswith("FM1_POC_")}
    env.update(FM1_POC_STOP_PC=hex(stop),FM1_POC_MAX_INSTRUCTIONS="100")
    command=["mise","exec","--","cargo","run","--manifest-path",
             str(HERE/"reference/Cargo.toml"),"--locked","--offline","--","snapshot",str(image)]
    result=subprocess.run(command,cwd=validate.ROOT,env=env,capture_output=True,text=True,timeout=15)
    record={"reference_command":command,"reference_returncode":result.returncode,
            "reference_stdout":result.stdout,"reference_stderr":result.stderr,
            "reference_fault_state_available":False}
    if result.returncode==0: record["reference_state"]=json.loads(result.stdout)
    return record


def check_reference_completion(record,guest,expected,words,name):
    validate.check(record["reference_returncode"]==0,f"{name}: reference completion failed")
    state=record["reference_state"]
    validate.check(state["pc"]==guest.pc and state["instructions"]==guest.instructions and
                   state["registers"]==expected and state["specials"]==specials() and
                   state["inspection"]==inspection(words),
                   f"{name}: separately qualified reference completion differs")
    record["reference_full_completion_checked"]=True


def fault_case(name,dest=6,base=4,offset=140,address=INSPECTION,kind=2,
               reason=None,guard=None,conditional=False,opcode=None,reference_category=None):
    base_value=(address-offset)&0xFFFFFFFF
    incoming={base:base_value}
    if conditional: incoming[7]=0
    guest,expected,words=setup(incoming,address,VALUE,base_value,guard=guard)
    before=guest.instructions
    if conditional: guest.emit(0xEA27,1)  # Selected one-operation THEN.
    pc=guest.pc
    op,x=operand(dest,base,offset,kind)
    if opcode is not None: op=opcode
    guest.emit(op,x)
    guest.emit(0)
    image=save_image(name,guest)
    directory=CACHE/name
    directory.mkdir(exist_ok=True)
    for filename in ("state.json","state.sram","state.alnk"):
        (directory/filename).unlink(missing_ok=True)
    env={key:value for key,value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings={"FM1_POC_STOP_PC":hex(guest.pc),"FM1_POC_MAX_INSTRUCTIONS":"100",
              "FM1_POC_STATE_DIR":str(directory)}
    env.update(settings)
    command=[*validate.COMMAND,"-kernel",str(image),"-append","alnk-probe"]
    result=subprocess.run(command,cwd=validate.ROOT,env=env,capture_output=True,text=True,timeout=15)
    validate.check((directory/"state.json").exists(),f"{name}: missing fault snapshot: {result.stderr}")
    state=json.loads((directory/"state.json").read_text())
    data_fault=dest!=base and kind==2 and guard not in ("pc-before","pc-last-byte") and opcode is None
    attempted={"address":address,"size":4,"flags":0} if data_fault else {"address":pc,"size":4,"flags":2}
    original=list(expected)
    if data_fault: expected[base]=address  # Partial modeled writeback; no loaded result.
    reason=reason or f"unsupported instruction 0x{op:04x}"
    validate.check(result.returncode!=0 and state["reason"]==reason and
                   state["pc"]==pc and state["instructions"]==before+conditional,
                   f"{name}: reason, instruction PC or fault retirement differs: {result.stderr}")
    validate.check(state["last_access"]==attempted,f"{name}: access EA/width/direction or fetch span differs")
    validate.check(state["registers"]==expected and state["specials"]==specials(),
                   f"{name}: fault stage, partial base writeback, destination result, PSR or RETS differs")
    check_memory((directory/"state.sram").read_bytes(),words,name)
    if guard in ("pc-before","pc-last-byte"):
        validate.check(state["guards"]["debug_message"]&(1<<12),f"{name}: PC guard not latched")
    record={"command":command,"environment":settings,"returncode":result.returncode,
            "stderr":result.stderr,"state":state,"model_fault_policy_only":True,
            "modeled_base_writeback_applied":data_fault,"if_header_already_retired":conditional,
            "hardware_fault_state_validation":False,
            "fixture_sha256":hashlib.sha256(image.read_bytes()).hexdigest()}
    record.update(reference_record(image,guest.pc))
    if guard in ("pc-before","pc-last-byte") or (dest==base and address==INSPECTION) or kind==3:
        oracle_expected=list(original)
        oracle_words=dict(words)
        if kind==3:
            # Public reference captures incoming source even when source==base;
            # primary stores after writeback. Keep the alias discrepancy raw.
            oracle_words[address]=original[dest]
            oracle_expected[base]=address
            record.update(reference_completed_deferred_store=True,
                          primary_store_alias_disagreement=dest==base and offset!=0)
        else:
            oracle_expected[dest]=VALUE
            oracle_expected[base]=address  # Reference writeback-wins, not primary alias truth.
            if dest==base:
                record.update(alias_admission_policy_only=True,
                              primary_alias_final_value=VALUE,reference_alias_final_value=address,
                              original48_primary_alias_mismatches_retained=True)
        check_reference_completion(record,guest,oracle_expected,oracle_words,name)
    elif reference_category:
        validate.check(record["reference_returncode"]!=0 and
                       f'address: {address}, size: 4, operation: "read"' in record["reference_stderr"] and
                       reference_category in record["reference_stderr"],
                       f"{name}: standalone read category/address/width/direction differs")
        record["reference_fatal_access_expected_category"]=reference_category
        record["reference_fatal_access_category_checked"]=True
    elif opcode==0xEC50:
        validate.check(record["reference_returncode"]!=0 and
                       f'Unsupported {{ pc: {pc}, word: {opcode} }}' in record["reference_stderr"],
                       f"{name}: raw EC50 unsupported outcome changed")
        record["reference_rejected_opcode_without_hardware_validity_claim"]=True
    (directory/"run.json").write_text(json.dumps(record,indent=2)+"\n")
    print(f"PASS {name}: modeled fault stage and separate reference outcome")


def generic_replay(image,expected,words):
    directory=CACHE/"generic-replay"
    directory.mkdir(exist_ok=True)
    for filename in ("state.json","state.sram","state.alnk"):
        (directory/filename).unlink(missing_ok=True)
    env={key:value for key,value in os.environ.items() if not key.startswith("FM1_POC_")}
    settings={"FM1_POC_STOP_PC":hex(expected["pc"]),"FM1_POC_MAX_INSTRUCTIONS":"100",
              "FM1_POC_STATE_DIR":str(directory)}
    env.update(settings)
    command=[*validate.COMMAND,"-kernel",str(image)]
    result=subprocess.run(command,cwd=validate.ROOT,env=env,capture_output=True,text=True,timeout=15)
    validate.check(result.returncode==0,f"generic-replay: {result.stderr}")
    state=json.loads((directory/"state.json").read_text())
    for field in ("pc","instructions","registers","specials"):
        validate.check(state[field]==expected[field],f"generic-replay: {field} differs")
    check_memory((directory/"state.sram").read_bytes(),words,"generic-replay")
    (directory/"run.json").write_text(json.dumps({"command":command,"environment":settings,
        "returncode":result.returncode,"stderr":result.stderr,"state":state,
        "default_loader":True,"optional_observers_configured":True},indent=2)+"\n")
    print("PASS generic-replay: actual reached load state and owned/default-loader memory")


def main():
    CACHE.mkdir(parents=True,exist_ok=True)
    isa.CACHE=CACHE
    cases=[]
    for high in range(8):
        offset=(high if high<4 else high-8)*256+84
        cases.append(dict(name=f"signed-upper-field-{high}",offset=offset))
    for offset in (-1024,-256,-4,0,4,8,12,16,32,64,128,140,248,252,256,1020):
        cases.append(dict(name=f"offset-{offset}",offset=offset))
    for register in range(16):
        cases.append(dict(name=f"destination-field-{register}",dest=register,base=(register+1)%16))
        cases.append(dict(name=f"base-field-{register}",dest=(register+1)%16,base=register))
    for value in (0,1,0x12345678,0x7FFF,0x8000,0x7FFFFFFF,0x80000000,0xFFFFFFFF,VALUE):
        cases.append(dict(name=f"word-data-{value:08x}",value=value))
    for psr in (0,0xFFFFFFFF): cases.append(dict(name=f"PSR-{psr:08x}",psr=psr))
    cases.extend([
        dict(name="last-SRAM-word",dest=15,base=14,offset=252,address=0x01C7FFFC,value=0xFFFFFFFF),
        dict(name="incoming-base-below-SRAM",address=0x01C00000,offset=1020),
        dict(name="incoming-base-above-SRAM",address=0x01C7FFFC,offset=-1024),
        dict(name="read-only-XIP-word",address=ENTRY),
        dict(name="write-guard-permits-read",guard="write"),
        dict(name="actual-ECD0-684E",address=0x01C1177C,value=240),
    ])
    for condition in (0,1): cases.append(dict(name=f"mixed-width-conditional-{condition}",condition=condition))
    for kind in (0,1):
        cases.append(dict(name=f"existing-kind-{kind}-distinct",kind=kind))
        cases.append(dict(name=f"existing-kind-{kind}-alias",kind=kind,dest=4))
    for offset in (140,-4):
        cases.append(dict(name=f"skipped-unresolved-alias-{offset}",dest=4,offset=offset,condition=1))
    assert len(cases)==81
    replay=None
    for case in cases:
        result=success_case(**case)
        if case["name"]=="actual-ECD0-684E": replay=result
    generic_replay(*replay)
    alias_offsets=(-1024,-4,0,140,1020)
    for register in range(16):
        fault_case(f"alias-before-effects-{register}",dest=register,base=register,
                   offset=alias_offsets[register%len(alias_offsets)])
    fault_case("selected-alias-after-IF",dest=4,base=4,conditional=True)
    fault_case("bad-EA-alias-before-access",dest=14,base=14,offset=4,address=0,
               reference_category="unmapped")
    fault_case("read-unaligned",address=INSPECTION+1,reason="unaligned access",reference_category="unaligned access")
    fault_case("read-unmapped",address=0x18000000,reason="unmapped access at 0x18000000",reference_category="unmapped")
    fault_case("read-wrap-zero",offset=4,address=0,reason="unmapped access at 0x00000000",reference_category="unmapped")
    fault_case("read-wrap-high",offset=-4,address=0xFFFFFFFC,
               reason="unmapped access at 0xfffffffc",reference_category="unmapped")
    fault_case("read-past-SRAM",address=0x01C80000,reason="unmapped access at 0x01c80000",reference_category="unmapped")
    for guard in ("pc-before","pc-last-byte"):
        fault_case(guard,guard=guard,reason="guest PC lies outside both configured guard windows")
    fault_case("deferred-kind3-distinct",kind=3)
    fault_case("deferred-kind3-source-base-alias",kind=3,dest=4)
    fault_case("unchanged-EC50-kind2",opcode=0xEC50)
    summary={"passed":True,"instruction":"ECD0..7 kind2 signed11 nonalias pre-indexed word immediate",
             "reference_compared_cases":len(cases),"generic_replays":1,"model_fault_cases":28,
             "alias_before_effects_faults":18,"data_read_faults_after_modeled_writeback":5,
             "PC_guard_faults_before_writeback":2,"deferred_kind3_faults":2,"unchanged_EC50_boundary_faults":1,
             "separate_reference_full_completions":21,"reference_fatal_access_categories":6,
             "reference_rejected_unverified_EC50":1,"original_research_primary_alias_mismatches_retained":48,
             "supplemental_alias_characterization_does_not_relabel_originals":True,
             "primary_loadstore_blob":"b6b9ba01a407a86e613cdb8717bfb4f8d2d3de05",
             "alias_policy":"primaryloadwins vsoraclewritebackwins; conservativereject beforeeffects, noISAinvalidclaim",
             "fault_policy":"modeledbaseWB beforedataread; PC/admission beforeWB; nofaultstateoracle/hardwareproof",
             "normal_reference_unowned_memory":"diagpoison",
             "QEMU_fault_default_loader_unowned_memory":"coldzero with explicitly seeded words",
             "conditional_scope":"bounded balancednonnested mixedwidths/nextIF; inheritedhelpers/IRQ unchanged",
             "qemu_sha256":hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
             "reference_fault_state_available":False,"hardware_fault_state_validation":False,"hardware_validation":False}
    (CACHE/"validation.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(f"PASS pre-indexed word immediate: {len(cases)} comparisons, generic replay and28 model faults")


if __name__=="__main__":
    main()
