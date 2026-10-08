#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact EE90/FFF0 signed-register LE IF in the existing predicate path.

Pinned Apache ifthenelse325-341/slaspec174,214-219 and vendor EE94/5500
establish signed32 ordinaryGPR[op&15] <= ordinaryGPR[x8..11], canonical zero
lowbyte, THEN=x14..15+1 / ELSE=x12..13. Primary explicitly constrains lowbyte0;
all10 sampled nonzero bytes complete in the reference but remain canonical
model admission faults before helper/count. Hardware reserved-bit behavior
is unverified. Comparisons use the final initialized array for aliases.

Fields/sign/equality, every arm-count encoding, scalar2/4/6 and bundle4/6/8,
latched input overwrites, memory and nextIF completion are bounded tests. The
actual NEG/SMAX/compact-move body is exercised at captured and sign/overflow
values. IF itself preserves PSR; NEG flags follow an independent subtract
calculation and sampled reference, and existing SMAX preserves them. No new
flag/helper authority is inferred from the IF primary constructor.

Nested IF, final THEN+ELSE CALL and finalFF0C remain inherited selected-body
faults after IF retirement, before body effects/count. Taken exit→nextIF
faults after IF and branch retirement. Four raw reference completions are
separately full-state characterized; CALL updatesRETS/skipsELSE, FF0C executes
ELSE, and nested/nextIF complete. No common completion/IRQ widening is claimed.
Retained-predicate IRQ blocking is source-inspected, not tested here.

Fullfetch4/header and fetch2/body guards exercise existing model phases;
write4 fatal reference records expose error marker/bodyPC/EA/direction only,
never CPU fault state or hardware ordering. NOR/unmapped errors share generic
reference taxonomy: controlled EA plus marker is checked, not unique error
classification. Three explicit guest markers own SRAM; normal reference/diag
unowned inspection is poisoned, alnk-probe/default-loader is cold-zero. Generic
replay uses requested observers. Prior gates/helpers/scanner/schema stay fixed.
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
CACHE=HERE/'.cache/signed-register-le-if-validation'
ENTRY=0x02000120;INSPECTION=0x01C08000;STACK=INSPECTION-16;PSR=0x89ABCDE5;RETS=0x12345678
GPRS=[0x10203040+i*0x01010101 for i in range(16)]
GPRS[0]=0x01C7FE08
MARKERS=[0x12345678,0x89ABCDEF,0x76543210]
INITIAL_INSPECTION=[0xA5A5A5A5]*5+[0]*3+[0xA5A5A5A5]*4
POISON=INITIAL_INSPECTION
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

def emit_supported(guest,op):
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

def apply_supported(expected,inspection,op,stack):
    kind,*args=op
    if kind in ('lit4','lit6'): expected[args[0]]=args[1]&0xffffffff
    elif kind=='mov': expected[args[0]]=expected[args[1]]
    elif kind=='bundle4': expected[8]=expected[9]=expected[0]
    elif kind=='bundle6': expected[8]=expected[0]; expected[9]=0x2222
    elif kind=='bundle8': expected[8]=0x1111; expected[9]=0x2222
    elif kind=='spstore': inspection[(stack+args[1]-INSPECTION)//4]=expected[args[0]]
    elif kind=='store': inspection[(expected[args[1]]+args[2]-INSPECTION)//4]=expected[args[0]]

def signed(x):return (x&0xFFFFFFFF) if not x&0x80000000 else (x&0xFFFFFFFF)-0x100000000

def emit(guest,op):
 if op[0]=='neg':guest.emit(0xE0A0|op[1],op[2]<<12)
 elif op[0]=='smax':guest.emit(0xE434,(op[1]<<12)|(op[3]<<8)|(op[2]<<4)|1)
 else:emit_supported(guest,op)

def apply(expected,memory,op,psr,stack):
 if op[0]=='neg':
  old=expected[op[2]];value=(-old)&0xFFFFFFFF;expected[op[1]]=value
  flags=int(old==0x80000000)|(int(old==0)<<1)|(int(value==0)<<2)|((value>>31)<<3)
  return (psr&~15)|flags
 if op[0]=='smax':
  left,right=expected[op[2]],expected[op[3]];expected[op[1]]=left if signed(left)>=signed(right) else right
 else:apply_supported(expected,memory,op,stack)
 return psr

def success_fixture(leftreg=3,rightreg=1,left=0,right=1,then=(('lit4',8,0x1111),),otherwise=(('lit4',9,0x2222),),
                    psr=PSR,registers=None,low=0,guard=None):
 initial=dict(registers or {});initial[leftreg]=left;initial[rightreg]=right
 guest,expected=setup(initial,psr,seed=True,guard=guard)
 incoming=list(expected);memory=MARKERS+POISON[3:]
 selected=signed(expected[leftreg])<=signed(expected[rightreg])
 assert 1<=len(then)<=4 and 0<=len(otherwise)<=3
 x=(rightreg<<8)|((len(then)-1)<<14)|(len(otherwise)<<12)|low
 pc,before=guest.pc,guest.instructions;guest.emit(0xEE90|leftreg,x)
 for op in (*then,*otherwise):emit(guest,op)
 finalpsr=psr
 for op in (then if selected else otherwise):finalpsr=apply(expected,memory,op,finalpsr,STACK)
 skipped=len(otherwise) if selected else len(then)
 guest.emit(0xE04A,0x3333);expected[10]=0x3333
 guest.emit(0xEA20,1);guest.emit(0xE04C,0x7777)
 if expected[0]&1:skipped+=1
 else:expected[12]=0x7777
 guest.emit(0)
 return guest,expected,memory,guest.pc,guest.instructions-skipped,specials(finalpsr),dict(
  operation_PC=pc,before=before,opcode=0xEE90|leftreg,operand=x,leftreg=leftreg,rightreg=rightreg,
  incoming_left=incoming[leftreg],incoming_right=incoming[rightreg],incoming_registers=incoming,
  selected=selected,then_count=len(then),else_count=len(otherwise),low_byte=low,initial_PSR=psr,final_PSR=finalpsr,
  then_body=then,else_body=otherwise,following_independent_IF=True,guard=guard)

def inherited_fixture(kind):
 registers={3:0xFFFFFFFF,1:0,0:0,15:1,14:0}
 if kind=='final-call':registers[1]=0x7FFFFFFF
 guest,expected=setup(registers,seed=True);before=guest.instructions
 memory=MARKERS+POISON[3:];initial=list(expected);special_expected=specials()
 if kind=='nested':
  guest.emit(0xEA20,1);pc=guest.pc;guest.emit(0xEE93,0x4100)
  guest.emit(0xE048,0x1111);guest.emit(0xE049,0x2222)
  reason='nested conditional block is unsupported';span=4;modelcount=before+1
 else:
  guest.emit(0xEE93,0x1100);pc=guest.pc;modelcount=before+1
  if kind=='final-call':guest.emit(0x00C3);reason='final THEN call with ELSE is unsupported';span=2
  elif kind=='final-ff0c':
   guest.emit(0xFF0C,0xF000,0);reason='final THEN signed-literal branch with ELSE is unsupported';span=6
  elif kind=='taken-exit':guest.emit(0xEE0E,0xF004);reason='nested conditional block is unsupported';span=4
  else:raise ValueError(kind)
  guest.emit(0xE049,0x2222)
 guest.emit(0xE04A,0x3333)
 if kind=='taken-exit':
  pc=guest.pc;modelcount=before+2;guest.emit(0xEA20,1);guest.emit(0xE04C,0x7777)
 guest.emit(0);stop=guest.pc
 if kind=='final-call':
  guest.emit(0);callee=guest.pc;guest.emit(0);guest.emit(0x0080)
  literal_index=guest.words.index(0xFFC3)
  guest.words[literal_index+1:literal_index+3]=[callee&0xFFFF,callee>>16]
  expected[3]=initial[3]=callee
 assert signed(initial[3])<=signed(initial[1]),'LE inherited body must be selected after every patch'
 metadata=dict(kind=kind,operation_PC=pc,before=before,model_fault_PC=pc,model_fault_count=modelcount,
  model_reason=reason,model_span=span,incoming_registers=initial,model_registers=list(expected),
  final_comparison_left=initial[3],final_comparison_right=initial[1],selected_LE_asserted=True,
  inherited_raw_completion_requires_separate_characterization=True)
 return guest,expected,memory,stop,None,special_expected,metadata

def guard_fixture(stage):
 guest,expected=setup({3:0,1:1},seed=True,guard=stage)
 before=guest.instructions;header=guest.pc;guest.emit(0xEE93,0x0100)
 body=guest.pc;guest.emit(0);guest.emit(0xE04D,0x3344);guest.emit(0)
 initial=list(expected);expected[13]=0x3344
 return guest,expected,MARKERS+POISON[3:],guest.pc,guest.instructions,specials(),dict(
  operation_PC=header,before=before,model_fault_PC=header if stage=='header' else body,
  model_fault_count=before+(stage=='body'),model_reason='guest PC lies outside both configured guard windows',
  model_span=4 if stage=='header' else 2,model_registers=initial,selected_LE_asserted=True)

def store_fault_fixture(kind):
 addresses=dict(unaligned=INSPECTION+1,unmapped=0x18000000,readonly=ENTRY,guarded=INSPECTION)
 markers=dict(unaligned='unaligned access',unmapped='unmapped',readonly='read-only XIP',guarded='CPU write protection violation')
 reasons=dict(unaligned='unaligned access',unmapped='unmapped access at 0x18000000',readonly='write to read-only XIP (NOR)',
              guarded='CPU write intersects an enabled guest guard window')
 EA=addresses[kind];guest,expected=setup({3:0,1:1,5:0x89ABCDEF,7:EA},seed=True,guard='write' if kind=='guarded' else None)
 before=guest.instructions;header=guest.pc;guest.emit(0xEE93,0x0100);pc=guest.pc;guest.store(5,7);guest.emit(0xE04D,0x3344);guest.emit(0)
 assert signed(expected[3])<=signed(expected[1])
 return guest,expected,MARKERS+POISON[3:],guest.pc,None,specials(),dict(
  operation_PC=header,before=before,EA=EA,fatal_category=markers[kind],model_fault_PC=pc,model_fault_count=before+1,
  model_reason=reasons[kind],model_span=4,model_registers=list(expected),selected_LE_asserted=True)

def make_fixtures():
 rows=[]
 for reg in range(16):
  for selected in (False,True):
   rows.append((f'fields-{reg}-{selected}','canonical',success_fixture(leftreg=reg,rightreg=(reg+1)%16,
    left=0x80000000 if selected else 0x7FFFFFFF,right=0x7FFFFFFF if selected else 0x80000000)))
  rows.append((f'alias-{reg}','canonical',success_fixture(leftreg=reg,rightreg=reg,left=0x80000000,right=0x80000000)))
 for left,right in ((0,0),(1,0),(0,1),(0xFFFFFFFF,0),(0,0xFFFFFFFF),(0x7FFFFFFF,0x80000000),(0x80000000,0x7FFFFFFF),
                    (0x80000000,0xFFFFFFFF),(0xFFFFFFFF,0x80000000),(0x80000000,0x80000000),(0x7FFFFFFF,0x7FFFFFFF)):
  rows.append((f'sign-{left:08x}-{right:08x}','canonical',success_fixture(leftreg=15,rightreg=14,left=left,right=right)))
 for nt in range(1,5):
  for ne in range(4):
   for selected in (False,True):
    rows.append((f'counts-{nt}-{ne}-{selected}','canonical',success_fixture(left=0 if selected else 2,right=1,
     then=tuple(('lit4',8,0x1100+i) for i in range(nt)),otherwise=tuple(('lit4',9,0x2200+i) for i in range(ne)))))
 widths=[('nop',),('lit4',8,0x1111),('lit6',8,0x89ABCDEF),('bundle4',),('bundle6',),('bundle8',)]
 for i,op in enumerate(widths):
  for selected in (False,True):rows.append((f'width-{i}-{selected}','canonical',success_fixture(left=0 if selected else 2,right=1,then=(op,))))
 for selected in (False,True):rows.append((f'mixed-{selected}','canonical',success_fixture(left=0 if selected else 2,right=1,then=tuple(widths[:4]),otherwise=tuple(widths[3:]))))
 for psr in (0,0xFFFFFFFF):rows.append((f'PSR-{psr:08x}','canonical',success_fixture(psr=psr)))
 for name,left,right in (('captured',0,1500),('negative',0xFFFFFFFF,0),('positive',1,2),('else',5,4),('overflow',0x80000000,0)):
  rows.append((f'actual-EE94-5500-{name}','canonical',success_fixture(leftreg=4,rightreg=5,left=left,right=right,
   then=(('neg',4,4),('smax',5,5,4)),otherwise=(('mov',5,4),))))
 for selected in (False,True):
  rows.append((f'latch-{selected}','canonical',success_fixture(left=0 if selected else 2,right=1,
   then=(('lit4',3,2),('lit4',8,0x1111)),otherwise=(('lit4',3,0),('lit4',9,0x2222)))))
 for selected in (False,True):rows.append((f'owned-store-{selected}','canonical',success_fixture(left=0 if selected else 2,right=1,
  registers={5:0x89ABCDEF},then=(('spstore',5,16),))))
 rows.append(('skipped-unmapped-store','canonical',success_fixture(left=2,right=1,registers={5:0x89ABCDEF,7:0x18000000},then=(('store',5,7,0),))))
 for low in (1,2,4,8,16,32,64,128,129,255):
  rows.append((f'noncanonical-{low:02x}','lowbyte-policy',success_fixture(low=low)))
 for kind in ('nested','final-call','final-ff0c','taken-exit'):rows.append((f'inherited-{kind}','inherited-raw',inherited_fixture(kind)))
 for stage in ('header','body'):rows.append((f'PC-{stage}','PC-policy',guard_fixture(stage)))
 for kind in ('unaligned','unmapped','readonly','guarded'):rows.append((f'write-{kind}','fatal-write',store_fault_fixture(kind)))
 assert len(rows)==137 and sum(category=='canonical' for _,category,_ in rows)==117,(len(rows),sum(category=='canonical' for _,category,_ in rows))
 return rows


def save_image(name,guest):
 image=CACHE/f'{name}.bin';image.write_bytes(guest.bytes()+bytes(16));return image


def reference_expectation(fixture):
 guest,expected,memory,stop,count,special,metadata=fixture
 expected=list(expected);special=list(special)
 if 'kind' in metadata:
  kind=metadata['kind'];expected=list(metadata['incoming_registers'])
  if kind=='nested':expected[8],expected[9],expected[10]=0x1111,0x2222,0x3333;count=metadata['before']+6
  elif kind=='final-call':expected[10]=0x3333;special[3]=metadata['model_fault_PC']+2;count=metadata['before']+6
  elif kind=='final-ff0c':expected[9],expected[10]=0x2222,0x3333;count=metadata['before']+5
  else:
   assert kind=='taken-exit';expected[12]=0x7777;count=metadata['before']+5
 return dict(pc=stop,instructions=count,registers=expected,specials=special,inspection=memory)


def check_reference_state(name,state,fixture):
 expected=reference_expectation(fixture)
 for field in ('pc','instructions','registers','specials','inspection'):
  validate.check(state[field]==expected[field],f'{name}: bounded signed predicate/body/{field} differs')


def success_case(name,fixture):
 guest,expected,memory,stop,count,special,metadata=fixture
 image=save_image(name,guest);state=isa.compare(name,image,stop,limit=100)
 check_reference_state(name,state,fixture)
 (CACHE/f'{name}-evidence.json').write_text(json.dumps(metadata,indent=2)+'\n')
 return image,state


def check_cold_memory(name,memory):
 validate.check(len(memory)==0x80000 and struct.unpack_from('<12I',memory,0x8000)==tuple(MARKERS+[0]*9),
                f'{name}: cold-profile explicit markers or unowned neighbors changed')


def model_fault_expectation(category,fixture):
 guest,expected,memory,stop,count,special,metadata=fixture
 if category=='lowbyte-policy':
  pc=metadata['operation_PC'];count=metadata['before'];registers=metadata['incoming_registers']
  reason=f"unsupported instruction 0x{metadata['opcode']:04x}";span=4;phase='canonical admission before IF helper/count'
 else:
  pc=metadata['model_fault_PC'];count=metadata['model_fault_count'];registers=metadata['model_registers']
  reason=metadata['model_reason'];span=metadata['model_span']
  phase=('selected body after IF retirement' if category in ('fatal-write','inherited-raw')
         else 'IF header fetch before retirement' if metadata['model_fault_count']==metadata['before']
         else 'selected body fetch after IF retirement')
  if metadata.get('kind')=='taken-exit':phase='following IF after outer IF and taken branch retired'
 access=dict(address=metadata['EA'],size=4,flags=1) if category=='fatal-write' else dict(address=pc,size=span,flags=2)
 return dict(pc=pc,instructions=count,registers=registers,specials=specials(),reason=reason,last_access=access,phase=phase)


def reference_record(image,stop):
 env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
 settings=dict(FM1_POC_STOP_PC=hex(stop),FM1_POC_MAX_INSTRUCTIONS='100');env.update(settings)
 command=['mise','exec','--','cargo','run','--manifest-path',str(HERE/'reference/Cargo.toml'),
          '--locked','--offline','--','snapshot',str(image)]
 result=subprocess.run(command,env=env,cwd=validate.ROOT,capture_output=True,text=True,timeout=15)
 record=dict(reference_command=command,reference_environment=settings,reference_returncode=result.returncode,
             reference_stdout=result.stdout,reference_stderr=result.stderr,reference_fault_state_available=False)
 if result.returncode==0:record['reference_state']=json.loads(result.stdout)
 return record


def fault_case(name,category,fixture):
 guest,expected,memory,stop,count,special,metadata=fixture
 modeled=model_fault_expectation(category,fixture)
 image=save_image(name,guest);directory=CACHE/name;directory.mkdir(exist_ok=True)
 for filename in ('state.json','state.sram','state.alnk'):(directory/filename).unlink(missing_ok=True)
 env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
 settings=dict(FM1_POC_STOP_PC=hex(stop),FM1_POC_MAX_INSTRUCTIONS='100',FM1_POC_STATE_DIR=str(directory))
 env.update(settings);command=[*validate.COMMAND,'-kernel',str(image),'-append','alnk-probe']
 result=subprocess.run(command,env=env,cwd=validate.ROOT,capture_output=True,text=True,timeout=15)
 statefile=directory/'state.json'
 validate.check(statefile.exists(),f'{name}: missing model fault snapshot: {result.stderr}')
 state=json.loads(statefile.read_text())
 validate.check(result.returncode!=0 and state['reason']==modeled['reason'],f'{name}: modeled fault reason differs')
 for field in ('pc','instructions','registers','specials','last_access'):
  validate.check(state[field]==modeled[field],f'{name}: exact fault-stage/{field} differs')
 check_cold_memory(name,(directory/'state.sram').read_bytes())
 if category=='PC-policy':validate.check(state['guards']['debug_message']&(1<<12),f'{name}: PC guard not latched')
 record=dict(command=command,environment=settings,returncode=result.returncode,stderr=result.stderr,state=state,
  fixture_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),modeled_fault_expectation=modeled,metadata=metadata,
  hardware_validation=False,hardware_fault_state_validation=False)
 record.update(reference_record(image,stop))
 if category=='fatal-write':
  marker=metadata['fatal_category'];EA=metadata['EA'];pc=metadata['model_fault_PC']
  validate.check(record['reference_returncode']!=0 and marker in record['reference_stderr'] and
                 f'Access {{ pc: {pc},' in record['reference_stderr'] and
                 f'address: {EA}, size: 4, operation: "write"' in record['reference_stderr'],
                 f'{name}: reference controlled EA/write4/bodyPC/error marker differs')
  record.update(reference_expected_marker=marker,reference_fatal_access_marker_checked_without_CPU_snapshot=True,
                NOR_and_unmapped_share_generic_reference_error_taxonomy=True)
 else:
  validate.check(record['reference_returncode']==0,f'{name}: separate reference completion failed')
  check_reference_state(name+'/reference',record['reference_state'],fixture)
  record.update(reference_full_sampled_completion_checked=True,reference_model_policy_difference=True)
  if category=='lowbyte-policy':
   record.update(primary_constraint='imm1623=0',reference_ignores_sampled_byte=True,
                 rejection_scope='canonical admission; hardware reserved-bit contract unverified')
  elif category=='inherited-raw':
   record.update(separately_characterized_inherited_reference_completion=True,original_raw_outcome_retained=True,
                 model_helper_policy_unchanged=True)
   if metadata['kind']=='taken-exit':record.update(branch_already_retired=True,fault_belongs_to_next_IF=True,
      retained_predicate_IRQ_blocking='source inspected; no IRQ validation')
 (directory/'run.json').write_text(json.dumps(record,indent=2)+'\n')
 print(f'PASS {name}: exact inherited/admission/access fault stage and separate reference outcome')


def generic_replay(image,expected):
 directory=CACHE/'generic-replay';directory.mkdir(exist_ok=True)
 for filename in ('state.json','state.sram','state.alnk'):(directory/filename).unlink(missing_ok=True)
 env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
 settings=dict(FM1_POC_STOP_PC=hex(expected['pc']),FM1_POC_MAX_INSTRUCTIONS='100',FM1_POC_STATE_DIR=str(directory))
 env.update(settings);command=[*validate.COMMAND,'-kernel',str(image)]
 result=subprocess.run(command,env=env,cwd=validate.ROOT,capture_output=True,text=True,timeout=15)
 statefile=directory/'state.json'
 validate.check(result.returncode==0 and statefile.exists(),f'generic-replay: missing completion: {result.stderr}')
 state=json.loads(statefile.read_text())
 for field in ('pc','instructions','registers','specials'):
  validate.check(state[field]==expected[field],f'generic-replay: {field} differs')
 check_cold_memory('generic-replay',(directory/'state.sram').read_bytes())
 (directory/'run.json').write_text(json.dumps(dict(command=command,environment=settings,returncode=result.returncode,
  stderr=result.stderr,state=state,default_loader=True,optional_observers_configured=True),indent=2)+'\n')
 print('PASS generic-replay: reached EE94 NEG/SMAX body and default-loader cold memory')


def main():
 CACHE.mkdir(parents=True,exist_ok=True);isa.CACHE=CACHE
 fixtures=make_fixtures();positives=[x for x in fixtures if x[1]=='canonical'];faults=[x for x in fixtures if x[1]!='canonical']
 assert len(positives)==117 and len(faults)==20
 replay=None
 for name,category,fixture in positives:
  result=success_case(name,fixture)
  if name=='actual-EE94-5500-captured':replay=result
 generic_replay(*replay)
 for name,category,fixture in faults:fault_case(name,category,fixture)
 summary=dict(passed=True,instruction='exact EE90/FFF0 signed register LE IF',reference_compared_cases=117,generic_replays=1,
  model_fault_cases=20,canonical_lowbyte_policy_faults=10,inherited_helper_completion_faults=4,PC_guard_faults=2,
  selected_write4_faults=4,separate_reference_policy_completions=16,fatal_reference_access_markers_without_CPU_snapshot=4,
  private_reference_calls=137,private_original_expected_full_matches=129,private_separately_characterized_inherited_completions=4,
  private_fatal_access_markers=4,private_expected_case_mismatches=0,
  maximum_instructions_per_process=100,comparison_process_timeout_seconds=60,fault_generic_process_timeout_seconds=15,
  primary_IF_blob='4ee88595bc41e98cd2d58bcdde90bc19f4c19a57',canonical_zero_lowbyte_primary_constraint=True,
  selected_IF_uses_final_initialized_GPR_array=True,ordinaryGPR14_separate_specialSP=True,THEN_count_range=[1,4],ELSE_count_range=[0,3],
  supported_body_widths=[2,4,6,8],NEG_flags_authority='independent subtract expectation plus existing model and separate oracle; not IF primary flags',
  SMAX_preserves_NEG_flags=True,actual_body_predicate_latch=True,inherited_predicate_IRQ_limits_unchanged=True,
  retained_predicate_IRQ_blocking='source inspected; no IRQ validation',NOR_unmapped_reference_taxonomy_shared=True,
  normal_reference_memory='diag poison except3explicit guestMARKERS',fault_generic_memory='cold-zero except3explicit guestMARKERS',
  default_loader_optional_observers=True,old_validators_modified=False,hardware_validation=False,
  hardware_fault_state_validation=False,reference_fault_state_available=False,qemu_sha256=hashlib.sha256(validate.QEMU.read_bytes()).hexdigest())
 (CACHE/'validation.json').write_text(json.dumps(summary,indent=2)+'\n')
 print('PASS signed-register LE IF:117 comparisons, generic replay and20 qualified model faults')


if __name__=='__main__':main()
