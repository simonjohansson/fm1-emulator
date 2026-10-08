#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate nonalias ED58..5B unsigned pre-indexed immediate halfword loads.

Vendor ED58/3E44 at0200aa44 and separate executable probes establish an
unsigned16 result and signed even byte displacement:
sext(oplow2,2)*256+x8..11*16+(x&14), from-512 to+510. Destination x12..15
and base x4..7 are ordinary GPRs, including r14 distinct from special SP.
Operand bit1 is offset data; bit0 selects deferred stores. No exact pre-indexed
halfword constructor exists in pinned Apache SLEIGH; plain ED50 fields and
word pre-index order are analogues, not this family's direct primary contract.

All48 reference aliases (16fields at0/+228/-2) complete with address-wins.
An exact primary/hardware alias contract is absent, so selected dest==base
is conservatively rejected before EA/writeback/access/count. This is model
admission policy, not ISA invalidity or a primary/reference contradiction.
Skipped aliases remain unevaluated. Stores and signed ED5C..F loads and the
C000+ED58 extended-tail role stay deferred. In the sampled ED5A/B stores,
high2 offset bits act unsigned; outside-inspection target data is not directly
observed. Original exploratory store mismatch and seed-map correction remain
retained separately from their later characterizations.

Data faults retain the existing modeled base-writeback-before-read order,
without a loaded result/retirement. Fetch/admission faults precede writeback.
Fatal reference records establish read category/EA/width only, with no CPU
fault snapshot, hardware ordering or rollback proof. Reads in write-protected
windows/XIP are permitted. No successful true32-bit data-wrap claim is made.
QEMU alnk-probe/default-loader unowned memory is cold-zero; normal standalone
reference fixtures retain diagnostic poisoning. Owned words are seeded and
checked explicitly. Balanced mixed-width predicates followed by another IF
are bounded completion checks; inherited helper/predicate/IRQ limits remain.
"""
import hashlib
import json
import os
import struct
import subprocess
from pathlib import Path

import validate
import validate_isa as isa
from validate_peripherals import Guest
HERE=Path(__file__).resolve().parent
CACHE=HERE/".cache/preindexed-unsigned-halfword-immediate-validation"
ENTRY=0x02000120
INSPECTION=0x01C08000
STACK=INSPECTION-16
PSR=0x89ABCDE5
RETS=0x12345678
GPRS=[0x10203040+i*0x01010101 for i in range(16)]
INITIAL_INSPECTION=[0xA5A5A5A5]*5+[0]*3+[0xA5A5A5A5]*4
VALUE=0x89AB
OLD_VALUE=0x1234

def operand(dest,base,offset=228,store=False,opcode=None):
    assert -512<=offset<=510 and offset%2==0
    bits=offset&1023
    op=(0xED58|((bits>>8)&3)) if opcode is None else opcode
    return op,(dest<<12)|(((bits>>4)&15)<<8)|(base<<4)|(bits&14)|store

def put_half(words,address,value):
    if 0x01C00000<=address<=0x01C7FFFE and not address&1:
        aligned=address&~3
        shift=(address&2)*8
        word=words.get(aligned,0xA5A5A5A5)
        words[aligned]=(word&~(0xFFFF<<shift))|((value&0xFFFF)<<shift)

def setup(registers,address,value,base_value,psr=PSR,extra=None,guard=None):
    words={INSPECTION-4:0x13579BDF,INSPECTION:0x2468ACE0,INSPECTION+4:0x76543210,
           INSPECTION+8:0x0BADF00D,INSPECTION+12:0xDEADBEEF}
    if base_value!=address:put_half(words,base_value,OLD_VALUE)
    for at,half in (extra or {}).items():put_half(words,at,half)
    put_half(words,address,value)
    guest=Guest()
    for at,word in words.items():guest.write(at,word)
    if guard=='write':
        guest.write(0x01EEE240,0xE7)
        guest.write(0x01EEE2C0,address+1)
        guest.write(0x01EEE280,address+1)
        guest.write(0x01EEE348,1)
    guest.literal(4,psr);guest.emit(0xE064,0x4580)
    guest.literal(4,RETS);guest.emit(0xE064,0x4380)
    guest.literal(14,STACK,special=True)
    expected=list(GPRS)
    for reg,val in registers.items():expected[reg]=val&0xFFFFFFFF
    for reg,val in enumerate(expected):guest.literal(reg,val)
    if guard in ('pc-before','pc-last-byte'):
        guest.write(0x01EEE240,0xE7);guest.write(0x01EEE384,ENTRY)
        header=guest.pc+14
        upper=header-1 if guard=='pc-before' else header+2
        guest.write(0x01EEE380,upper)
        expected[0],expected[1]=upper,0x01EEE380
    return guest,expected,words

def specials(psr=PSR):
    expected=[0]*16
    expected[3],expected[5],expected[14]=RETS,psr,STACK
    return expected

def inspection(words,poison=True):
    expected=list(INITIAL_INSPECTION) if poison else [0]*12
    for at,word in words.items():
        if INSPECTION<=at<INSPECTION+48:expected[(at-INSPECTION)//4]=word
    return expected

def fixture(dest=3,base=4,offset=228,value=VALUE,address=INSPECTION,psr=PSR,
            opcode=None,store=False,extra=None,base_value=None,guard=None,registers=None):
    base_value=(address-offset)&0xFFFFFFFF if base_value is None else base_value
    guest,expected,words=setup({**(registers or {}),base:base_value},address,value,base_value,psr,extra,guard)
    op,x=operand(dest,base,offset,store,opcode)
    pc,before=guest.pc,guest.instructions
    guest.emit(op,x);guest.emit(0)
    return guest,expected,words,pc,before,base_value,op,x


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


def check_memory(memory,words,name):
    validate.check(len(memory)==0x80000 and
                   struct.unpack_from('<12I',memory,0x8000)==tuple(inspection(words,poison=False)),
                   f'{name}: cold-profile inspection or neighboring memory differs')
    for at,word in words.items():
        validate.check(struct.unpack_from('<I',memory,at-0x01C00000)[0]==word,
                       f'{name}: explicitly seeded word at{at:08x} changed')


def save_image(name,guest):
    image=CACHE/f'{name}.bin'
    image.write_bytes(guest.bytes()+bytes(16))
    return image


def positive_specs():
    cases=[]
    for high in range(4):
        cases.append(dict(name=f'signed-upper-{high}',offset=(high if high<2 else high-4)*256+84))
    for off in (-512,-256,-2,0,2,4,8,14,16,32,64,128,228,248,254,256,510):
        cases.append(dict(name=f'offset-{off}',offset=off))
    for reg in range(16):
        cases.append(dict(name=f'destination-{reg}',dest=reg,base=(reg+1)%16))
        cases.append(dict(name=f'base-{reg}',dest=(reg+1)%16,base=reg))
    for value in (0,1,0x7FFF,0x8000,0xFFFF,VALUE):
        cases.append(dict(name=f'unsigned-data-{value:04x}',value=value))
    for psr in (0,0xFFFFFFFF): cases.append(dict(name=f'PSR-{psr:08x}',psr=psr))
    cases.extend([
        dict(name='last-SRAM-halfword',dest=15,base=14,offset=254,address=0x01C7FFFE,value=0xFFFF),
        dict(name='incoming-base-below-SRAM',offset=510,address=0x01C00000),
        dict(name='incoming-base-above-SRAM',offset=-512,address=0x01C7FFFE),
        dict(name='upper-halfword-same-old-word',offset=2,address=INSPECTION+2,value=0x8000),
        dict(name='read-only-XIP-halfword',address=ENTRY),
        dict(name='write-guard-permits-halfword-read',guard='write'),
        dict(name='actual-ED58-3E44',address=0x01C117D4,value=33808),
        dict(name='mixed-width-selected',conditional=0),dict(name='mixed-width-skipped',conditional=1),
        dict(name='skipped-unresolved-alias-228',dest=4,conditional=1),
        dict(name='skipped-unresolved-alias-minus2',dest=4,offset=-2,conditional=1),
        dict(name='unchanged-unsigned-plain-control',opcode=0xED50),
        dict(name='unchanged-signed-plain-control',opcode=0xED54),
    ])
    assert len(cases)==74
    return cases


def success_fixture(name,dest=3,base=4,offset=228,value=VALUE,address=INSPECTION,
                    psr=PSR,conditional=None,guard=None,opcode=None):
    base_value=(address-offset)&0xFFFFFFFF
    selected=conditional is None or conditional==0
    validate.check(dest!=base or not selected,f'{name}: selected unresolved alias in positive fixture')
    if conditional is None:
        guest,expected,words,pc,before,bvalue,op,x=fixture(dest,base,offset,value,address,psr,
                                                        opcode=opcode,guard=guard)
        if ENTRY<=address<guest.pc:
            value=struct.unpack_from('<H',guest.bytes(),address-ENTRY)[0]
        if opcode==0xED50: expected[dest]=value
        elif opcode==0xED54: expected[dest]=value if value<0x8000 else value|0xFFFF0000
        else: expected[base]=address; expected[dest]=value
        retired=guest.instructions
    else:
        guest,expected,words=setup({base:base_value,7:conditional},address,value,base_value,psr)
        op,x=operand(dest,base,offset)
        guest.emit(0xEA27,0xF001)  # THEN4/ELSE3, scalar2/4/6 and bundle4/6/8.
        then=(('nop',),('operation',op,x),('lit6',8,0x11223344),('bundle8',))
        otherwise=(('bundle4',),('bundle6',),('lit4',11,0x5566))
        for item in (*then,*otherwise):
            if item[0]=='operation': guest.emit(item[1],item[2])
            else: emit(guest,item)
        if selected:
            expected[base]=address; expected[dest]=value
            for item in then[2:]: apply(expected,item)
        else:
            for item in otherwise: apply(expected,item)
        skipped=3 if selected else 4
        guest.emit(0xEA20,1); guest.emit(0xE04C,0x7777)
        if expected[0]&1: skipped+=1
        else: expected[12]=0x7777
        guest.emit(0)
        retired=guest.instructions-skipped
    metadata=dict(first_word=op,second_word=x,dest=dest,base=base,incoming_base=base_value,
                  signed_byte_offset=offset,EA=address,unsigned_halfword=value,selected=selected,
                  skipped_unresolved_alias=dest==base,bounded_followup_IF=conditional is not None)
    return guest,expected,words,retired,metadata


def success_case(**settings):
    name=settings['name']
    guest,expected,words,retired,metadata=success_fixture(**settings)
    image=save_image(name,guest)
    state=isa.compare(name,image,guest.pc,limit=100)
    validate.check(state['pc']==guest.pc and state['instructions']==retired,
                   f'{name}: operation sizing, arm count or retirement differs')
    validate.check(state['registers']==expected and state['specials']==specials(settings.get('psr',PSR)),
                   f'{name}: signed offset, unsigned result, fields/writeback, PSR or RETS differs')
    validate.check(state['inspection']==inspection(words),f'{name}: seeded old/target halfwords or neighbors changed')
    (CACHE/f'{name}-evidence.json').write_text(json.dumps(metadata,indent=2)+'\n')
    return image,state,words


def fault_specs():
    cases=[]
    for reg in range(16):
        off=(0,228,-2)[reg%3]
        cases.append(dict(name=f'alias-{reg}-{off}',stage='alias',dest=reg,base=reg,offset=off))
    cases.extend([
        dict(name='selected-alias-after-IF',stage='alias',dest=4,base=4,conditional=True),
        dict(name='bad-EA-alias',stage='alias',dest=14,base=14,offset=2,address=0,
             reference_category='unmapped'),
        dict(name='unaligned-read',stage='data',address=INSPECTION+1,reason='unaligned access',
             reference_category='unaligned access'),
        dict(name='unmapped-read',stage='data',address=0x18000000,reason='unmapped access at 0x18000000',
             reference_category='unmapped'),
        dict(name='wrap-zero-read',stage='data',offset=2,address=0,reason='unmapped access at 0x00000000',
             reference_category='unmapped'),
        dict(name='wrap-high-read',stage='data',offset=-2,address=0xFFFFFFFE,
             reason='unmapped access at 0xfffffffe',reference_category='unmapped'),
        dict(name='past-SRAM-read',stage='data',address=0x01C80000,reason='unmapped access at 0x01c80000',
             reference_category='unmapped'),
    ])
    for guard in ('pc-before','pc-last-byte'):
        cases.append(dict(name=guard,stage='pc',guard=guard,
                          reason='guest PC lies outside both configured guard windows'))
    for high in range(4):
        off=(high if high<2 else high-4)*256+84
        cases.append(dict(name=f'deferred-store-{high}',stage='store',offset=off,opcode=0xED58|high,
                          store=True,registers={3:0x89ABCDEF}))
        cases.append(dict(name=f'deferred-signed-load-{high}',stage='signed',offset=off,opcode=0xED5C|high,
                          registers={3:0x89ABCDEF}))
    cases.append(dict(name='deferred-store-source-base-alias',stage='store',store=True,dest=4,base=4))
    cases.append(dict(name='deferred-compact-NOP-head-load-tail',stage='parallel'))
    assert len(cases)==35
    return cases


def fault_fixture(name,stage,dest=3,base=4,offset=228,address=INSPECTION,guard=None,
                  conditional=False,opcode=None,store=False,registers=None,reason=None,
                  reference_category=None):
    base_value=(address-offset)&0xFFFFFFFF
    if not conditional and stage!='parallel':
        guest,expected,words,pc,before,bvalue,op,x=fixture(dest,base,offset,address=address,
            guard=guard,opcode=opcode,store=store,registers=registers)
    else:
        guest,expected,words=setup({**(registers or {}),base:base_value,**({7:0} if conditional else {})},
                                   address,VALUE,base_value,guard=guard)
        before=guest.instructions
        if conditional: guest.emit(0xEA27,1)
        pc=guest.pc
        op,x=operand(dest,base,offset,store,opcode)
        if stage=='parallel': guest.emit(0xC000,op,x)
        else: guest.emit(op,x)
        guest.emit(0)
    metadata=dict(first_word=op,second_word=x,dest=dest,base=base,stage=stage,EA=address,
                  incoming_base=base_value,signed_load_byte_offset=offset,operation_PC=pc,
                  retired_before_operation=before+int(conditional),fetch_span=6 if stage=='parallel' else 4,
                  IF_header_already_retired=conditional,model_writeback_before_read=stage=='data')
    return guest,expected,words,metadata


def reference_expectation(guest,original,words,metadata):
    expected=list(original); expected_words=dict(words)
    base,dest,address=metadata['base'],metadata['dest'],metadata['EA']
    stage,op=metadata['stage'],metadata['first_word']
    qualification={}
    if stage=='store':
        # This is separately sampled deferred-store behavior, not admitted semantics.
        address=(address+(1024 if op&2 else 0))&0xFFFFFFFF
        put_half(expected_words,address,original[dest])
        expected[base]=address
        sampled=INSPECTION<=address<INSPECTION+48
        qualification=dict(observed_reference_store_EA=address,
                           store_target_data_directly_sampled=sampled,
                           store_high2_unsigned_qualification=True,
                           outside_target_data_qualification=None if sampled else
                           'Only sampled PC/count/GPR/special/inspection is proved; target store data outside inspection is not directly observed.')
    else:
        expected[dest]=(VALUE|0xFFFF0000) if stage=='signed' else VALUE
        expected[base]=address  # Observed address-wins aliases; no exact primary contract.
        if stage=='alias':
            qualification=dict(alias_admission_policy_only=True,reference_alias_final_value=address,
                               exact_primary_alias_contract_absent=True,hardware_alias_rule_established=False)
    return expected,expected_words,qualification


def reference_record(image,stop):
    env={key:value for key,value in os.environ.items() if not key.startswith('FM1_POC_')}
    env.update(FM1_POC_STOP_PC=hex(stop),FM1_POC_MAX_INSTRUCTIONS='100')
    command=['mise','exec','--','cargo','run','--manifest-path',str(HERE/'reference/Cargo.toml'),
             '--locked','--offline','--','snapshot',str(image)]
    result=subprocess.run(command,cwd=validate.ROOT,env=env,capture_output=True,text=True,timeout=15)
    record=dict(reference_command=command,reference_returncode=result.returncode,
                reference_stdout=result.stdout,reference_stderr=result.stderr,
                reference_fault_state_available=False)
    if result.returncode==0: record['reference_state']=json.loads(result.stdout)
    return record


def check_reference_outcome(record,guest,original,words,metadata,name,reference_category=None):
    if reference_category:
        validate.check(record['reference_returncode']!=0 and
                       reference_category in record['reference_stderr'] and
                       f'address: {metadata["EA"]}, size: 2, operation: "read"' in record['reference_stderr'],
                       f'{name}: fatal reference read category/EA/width/direction differs')
        record.update(reference_fatal_access_category_checked=True,
                      reference_fatal_access_expected_category=reference_category)
    else:
        expected,expected_words,qualification=reference_expectation(guest,original,words,metadata)
        validate.check(record['reference_returncode']==0,f'{name}: reference did not complete')
        state=record['reference_state']
        validate.check(state['pc']==guest.pc and state['instructions']==guest.instructions and
                       state['registers']==expected and state['specials']==specials() and
                       state['inspection']==inspection(expected_words),
                       f'{name}: separately qualified reference sampled completion differs')
        record.update(qualification)
        record['reference_full_sampled_completion_checked']=True


def fault_case(**settings):
    name=settings['name']
    guest,expected,words,metadata=fault_fixture(**settings)
    image=save_image(name,guest)
    directory=CACHE/name
    directory.mkdir(exist_ok=True)
    for filename in ('state.json','state.sram','state.alnk'):
        (directory/filename).unlink(missing_ok=True)
    env={key:value for key,value in os.environ.items() if not key.startswith('FM1_POC_')}
    observer=dict(FM1_POC_STOP_PC=hex(guest.pc),FM1_POC_MAX_INSTRUCTIONS='100',
                  FM1_POC_STATE_DIR=str(directory))
    env.update(observer)
    command=[*validate.COMMAND,'-kernel',str(image),'-append','alnk-probe']
    result=subprocess.run(command,cwd=validate.ROOT,env=env,capture_output=True,text=True,timeout=15)
    validate.check((directory/'state.json').exists(),f'{name}: fault snapshot missing: {result.stderr}')
    state=json.loads((directory/'state.json').read_text())
    stage=metadata['stage']; pc=metadata['operation_PC']
    original=list(expected)
    if stage=='data': expected[metadata['base']]=metadata['EA']  # Partial modeled WB, no loaded result.
    attempted=dict(address=metadata['EA'],size=2,flags=0) if stage=='data' else dict(
        address=pc,size=metadata['fetch_span'],flags=2)
    reason=settings.get('reason') or f'unsupported instruction 0x{0xC000 if stage=="parallel" else metadata["first_word"]:04x}'
    validate.check(result.returncode!=0 and state['reason']==reason and state['pc']==pc and
                   state['instructions']==metadata['retired_before_operation'],
                   f'{name}: reason/operation PC/fault retirement differs: {result.stderr}')
    validate.check(state['last_access']==attempted,f'{name}: data width2/read or complete fetch span differs')
    validate.check(state['registers']==expected and state['specials']==specials(),
                   f'{name}: pre-effect rejection or partial base writeback/result/PSR/RETS differs')
    check_memory((directory/'state.sram').read_bytes(),words,name)
    if stage=='pc':
        validate.check(state['guards']['debug_message']&(1<<12),f'{name}: PC guard not latched')
    record=dict(command=command,environment=observer,returncode=result.returncode,stderr=result.stderr,state=state,
                **metadata,model_fault_policy_only=True,hardware_fault_state_validation=False,
                fixture_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
    record.update(reference_record(image,guest.pc))
    check_reference_outcome(record,guest,original,words,metadata,name,settings.get('reference_category'))
    (directory/'run.json').write_text(json.dumps(record,indent=2)+'\n')
    print(f'PASS {name}: qualified model fault stage and separate reference outcome')


def generic_replay(image,expected,words):
    directory=CACHE/'generic-replay'
    directory.mkdir(exist_ok=True)
    for filename in ('state.json','state.sram','state.alnk'):
        (directory/filename).unlink(missing_ok=True)
    env={key:value for key,value in os.environ.items() if not key.startswith('FM1_POC_')}
    observer=dict(FM1_POC_STOP_PC=hex(expected['pc']),FM1_POC_MAX_INSTRUCTIONS='100',
                  FM1_POC_STATE_DIR=str(directory))
    env.update(observer)
    command=[*validate.COMMAND,'-kernel',str(image)]
    result=subprocess.run(command,cwd=validate.ROOT,env=env,capture_output=True,text=True,timeout=15)
    validate.check(result.returncode==0,f'generic-replay: {result.stderr}')
    state=json.loads((directory/'state.json').read_text())
    for field in ('pc','instructions','registers','specials'):
        validate.check(state[field]==expected[field],f'generic-replay: {field} differs')
    check_memory((directory/'state.sram').read_bytes(),words,'generic-replay')
    (directory/'run.json').write_text(json.dumps(dict(command=command,environment=observer,
        returncode=result.returncode,stderr=result.stderr,state=state,default_loader=True,
        optional_observers_configured=True),indent=2)+'\n')
    print('PASS generic-replay: actual reached load state and owned/default-loader memory')


def promoted_original_control(**settings):
    # fault_fixture is unchanged: setup, opcode, continuation and bytes retain
    # the original legacy image. Only its now-obsolete fault assertion moves.
    name = settings['name']
    guest, original, words, metadata = fault_fixture(**settings)
    expected, expected_words, qualification = reference_expectation(guest, original, words, metadata)
    image = save_image(name, guest)
    state = isa.compare(name, image, guest.pc, limit=100)
    validate.check(state['registers'] == expected and state['specials'] == specials() and
                   state['pc'] == guest.pc and state['instructions'] == guest.instructions and
                   state['inspection'] == inspection(expected_words),
                   f'{name}: promoted full state or owned inspection differs')
    (CACHE / f'{name}-promoted.json').write_text(json.dumps(dict(
        fixture_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
        original_fixture_construction_unchanged=True,
        outside_inspection_store_data_not_reference_observed=settings['stage']=='store' and bool(metadata['first_word']&2),
        hardware_validation=False, **qualification), indent=2)+'\n')


def main():
    CACHE.mkdir(parents=True,exist_ok=True)
    isa.CACHE=CACHE
    cases=positive_specs()
    replay=None
    for case in cases:
        result=success_case(**case)
        if case['name']=='actual-ED58-3E44': replay=result
    generic_replay(*replay)
    faults=fault_specs()
    promoted = [case for case in faults if
                (case['stage'] == 'signed' and case.get('opcode') == 0xED5C) or
                (case['stage'] == 'store' and case.get('dest', 3) != case.get('base', 4))]
    faults = [case for case in faults if case not in promoted]
    for case in promoted: promoted_original_control(**case)
    for case in faults: fault_case(**case)
    summary=dict(passed=True,instruction='ED58..5B nonalias unsigned pre-indexed immediate halfword load',
        reference_compared_cases=len(cases)+len(promoted),promoted_original_fixture_controls=len(promoted),generic_replays=1,model_fault_cases=len(faults),
        static_alias_faults_before_effects=18,data_read_faults_after_modeled_writeback=5,
        PC_guard_fetch4_before_writeback=2,deferred_store_faults=1,deferred_signed_load_faults=3,
        deferred_C000_extended_load_tail_faults=1,separate_reference_full_sampled_fault_completions=24,
        reference_fatal_access_categories_without_CPU_snapshot=6,
        exact_primary_constructor_present=False,
        authority='vendor witness and independent executable discrimination; primary plain-load/preindex analogues only',
        observed_alias='48 address-wins reference characterizations; absent exact primary/hardware alias contract',
        alias_policy='conservative rejection before effects/access/count; not ISA invalidity or a primary/reference contradiction',
        fault_policy='existing model baseWB before dataread; fetch/admission before WB; no reference/hardware fault-state proof',
        store_qualification='Distinct stores admitted by independent Batch D unsigned-offset matrix; these exact original ED5A/B fixtures retain outside-inspection reference-data qualification; source-base alias deferred',
        original_research_calls=150,original_research_alias_characterizations=48,
        private_preparation_historical_calls=151,final_private_exact_fixture_records=109,
        final_private_full_sampled_state_checks=103,final_private_fatal_categories=6,
        original_store_expectation_mismatch_retained=True,initial_neighbor_seed_metadata_correction_retained=True,
        normal_reference_unowned_memory='diagnostic poison',QEMU_fault_default_loader_unowned_memory='cold-zero except explicitly seeded words',
        conditional_scope='balanced nonnested mixedwidths/nextIF only; common helper/completion/IRQ limitations unchanged',
        qemu_sha256=hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
        reference_fault_state_available=False,hardware_fault_state_validation=False,hardware_validation=False)
    (CACHE/'validation.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(f'PASS pre-indexed halfword: {len(cases)+len(promoted)} comparisons, generic replay and{len(faults)} model faults')


if __name__=='__main__':
    main()
