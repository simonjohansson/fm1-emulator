#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact E9DC unsigned SP-relative byte loads.

Pinned Apache stack474-478/slaspec179 and vendor E9DC/7034 establish one
ordinary destination in x12..15 and all unsigned low12 bits as byte offset
from special SP. There is no direction/reserved bit, scaling, base writeback
or PSR/RETS change. Byte EAs may be odd. GPR14 and special SP14 are independent.
Focused fixtures cover all destination/offset fields, zero extension80/FF,
competing unoffset/scaled/signed addresses, four byte positions, first/last
SRAM and XIP, full-state preservation, protected reads and exact fetch4 span.
Final owned seed words account for overlapping competing and actual bytes.
Boundary word readbacks verify neighboring bytes beyond sampled inspection.

Balanced selected/skipped THEN/ELSE beside2/4/6-byte instructions and a next
IF preserve inherited predicate/IRQ limits. Selected loads overwrite the IF
input; a nonfinal marker proves its predicate stays latched. Default-loader
replay uses requested observers. Normal reference/diag unowned inspection is
poisoned; alnk-probe/default-loader is cold-zero except explicit guest writes.

Data read1 faults precede destination/retirement. PC guards cover all4 fetch
bytes before access/effects. Reference fatal records establish category, EA,
width/read and operation PC only, never CPU fault-state or hardware ordering.
Wrapped-to-zero unmapped cases do not establish successful32-bit wrap access.
Signed E9DD and genuine C000+extended E9DC tails separately complete in the
reference but stay model-deferred before effects/count; no parallel admission
or hardware-invalidity claim. Existing helpers/scanner/classifiers remain fixed.
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
CACHE=HERE/'.cache/sp-relative-byte-load-validation'
ENTRY=0x02000120;INSPECTION=0x01C08000;PSR=0x89ABCDE5;RETS=0x12345678
GPRS=[0x10203040+i*0x01010101 for i in range(16)]
MARKERS={INSPECTION:0x12345678,INSPECTION+4:0x89ABCDEF,INSPECTION+8:0x76543210,INSPECTION+12:0x0BADF00D}
POISON=[0xA5A5A5A5]*5+[0]*3+[0xA5A5A5A5]*4

def seed_byte(words,address,value):
 if 0x01C00000<=address<=0x01C7FFFF:
  at=address&~3;shift=(address&3)*8;old=words.get(at,0xA5B6C7D8)
  words[at]=(old&~(255<<shift))|((value&255)<<shift)

def setup(dest,offset,address,value=67,psr=PSR,guard=None,registers=None,table=True):
 stack=(address-offset)&0xFFFFFFFF;words=dict(MARKERS);competitors=[]
 if table:
  # Actual byte is written last; overlapping competing bytes share one final word.
  for at,val,label in ((stack,0x31,'unoffset-SP'),((stack+2*offset)&0xFFFFFFFF,0x2E,'scaled-offset'),
                      ((address-4096)&0xFFFFFFFF,0x7F,'signed12-offset')):
   if at!=address and 0x01C00000<=at<=0x01C7FFFF:
    seed_byte(words,at,val);competitors.append(dict(EA=at,value=val,label=label))
  seed_byte(words,address,value)
 guest=Guest()
 for at,word in words.items():guest.write(at,word)
 if guard=='write':
  guest.write(0x01EEE240,0xE7);guest.write(0x01EEE2C0,address);guest.write(0x01EEE280,address);guest.write(0x01EEE348,1)
 upper_pos=None
 if guard in ('pc-allowed','pc-rejected'):
  guest.write(0x01EEE240,0xE7);upper_pos=len(guest.words);guest.write(0x01EEE380,0);guest.write(0x01EEE384,ENTRY)
 guest.literal(4,psr);guest.emit(0xE064,0x4580)
 guest.literal(4,RETS);guest.emit(0xE064,0x4380);guest.literal(14,stack,special=True)
 expected=list(GPRS);expected[dest]=0xFFFFFFFF
 for reg,val in (registers or {}).items():expected[reg]=val&0xFFFFFFFF
 for reg,val in enumerate(expected):guest.literal(reg,val)
 if upper_pos is not None:
  high=guest.pc+(3 if guard=='pc-allowed' else 2)
  guest.words[upper_pos+4]=high&0xFFFF;guest.words[upper_pos+5]=high>>16
 return guest,expected,words,dict(dest=dest,offset=offset,special_SP=stack,EA=address,psr=psr,guard=guard,
  incoming_registers=list(expected),actual_owned_SRAM_words=words,competing_byte_seeds=competitors,
  actual_final_seed_words={hex(k):hex(v) for k,v in words.items()})

def specials(stack,psr):
 a=[0]*16;a[3],a[5],a[14]=RETS,psr,stack;return a

def inspection(words):
 a=list(POISON)
 for at,val in words.items():
  if INSPECTION<=at<INSPECTION+48:a[(at-INSPECTION)//4]=val
 return a

def ordinary_fixture(dest=7,offset=52,address=INSPECTION+2,value=67,psr=PSR,guard=None,
                     opcode=0xE9DC,bundle=False,boundary=False,continuation=False):
 guest,expected,words,metadata=setup(dest,offset,address,value,psr,guard)
 pc,before=guest.pc,guest.instructions
 x=(dest<<12)|offset
 guest.emit(*((0xC000,opcode,x) if bundle else (opcode,x)))
 actual=value&255
 if ENTRY<=address<guest.pc:actual=guest.bytes()[address-ENTRY]
 if opcode==0xE9DD and actual&128:actual|=0xFFFFFF00
 expected[dest]=actual
 if boundary:
  aligned=address&~3;guest.literal(6,aligned);guest.load(5,6);expected[6]=aligned;expected[5]=words[aligned]
 if continuation:guest.literal(13,0x33445566);expected[13]=0x33445566
 if guard!='pc-allowed':guest.emit(0)
 metadata.update(operation_PC=pc,before=before,opcode=opcode,operand=x,span=6 if bundle else 4,
                 bundle=bundle,actual_loaded_value=actual,boundary_word_readback=boundary,STOP_PC=guest.pc)
 return guest,expected,words,guest.instructions,metadata

def conditional_fixture(arm,position,condition):
 guest,expected,words,metadata=setup(4,52,INSPECTION+1,67,registers={4:condition,6:0})
 count=1 if position=='final' else 2;then,otherwise=(count,1) if arm=='then' else (1,count)
 guest.emit(0xEA24,((then-1)<<14)|(otherwise<<12)|1)
 selected=condition==(0 if arm=='then' else 1)
 pc=None
 for block in ('then','else'):
  if block==arm:
   pc=guest.pc;guest.emit(0xE9DC,0x4034)
   if position=='nonfinal':guest.literal(12,0xCCCC)
  else:guest.literal(13,0x1111)
 guest.emit(0xEA26,1);guest.emit(0xE04E,0x2222);guest.emit(0)
 if selected:
  expected[4]=67
  if position=='nonfinal':expected[12]=0xCCCC
 else:expected[13]=0x1111
 expected[14]=0x2222
 metadata.update(arm=arm,position=position,condition=condition,selected=selected,operation_PC=pc,
  predicate_input_overwritten_in_selected_arm=True,mixed_instruction_widths=[2,4,6],following_independent_IF=True)
 return guest,expected,words,guest.instructions-(1 if selected else count),metadata

def skipped_fixture(address):
 guest,expected,words,metadata=setup(7,0,address,table=False,registers={4:1,6:0})
 guest.emit(0xEA24,1);pc=guest.pc;guest.emit(0xE9DC,0x7000)
 guest.emit(0xEA26,1);guest.emit(0xE04E,0x2222);guest.emit(0)
 expected[14]=0x2222
 metadata.update(operation_PC=pc,skipped_unmapped_read=True,following_independent_IF=True)
 return guest,expected,words,guest.instructions-1,metadata

def make_fixtures():
 rows=[]
 for dest in range(16):rows.append((f'destination-{dest}','canonical',ordinary_fixture(dest=dest)))
 for offset in (0,1,2,4,8,16,32,52,64,128,256,512,1024,2048,4094,4095):
  rows.append((f'offset-{offset}','canonical',ordinary_fixture(offset=offset)))
 for value in (0,1,0x7F,0x80,0xFE,0xFF):rows.append((f'unsigned-{value:02x}','canonical',ordinary_fixture(value=value)))
 for byte in range(4):rows.append((f'byte-position-{byte}','canonical',ordinary_fixture(address=INSPECTION+byte,value=0x80+byte)))
 for psr in (0,0xFFFFFFFF):rows.append((f'PSR-{psr:08x}','canonical',ordinary_fixture(psr=psr)))
 rows.append(('actual-E9DC-7034-SP01c79cec-byte43','canonical',ordinary_fixture(address=0x01C79D20,value=67,boundary=True)))
 for name,address,offset in (('first-SRAM-from-unmapped-SP',0x01C00000,4095),('last-SRAM-byte',0x01C7FFFF,0),('odd-SP-last-byte',0x01C7FFFF,2)):
  rows.append((name,'canonical',ordinary_fixture(address=address,offset=offset,value=0xFF,boundary=True)))
 rows.append(('protected-byte-read-permitted','canonical',ordinary_fixture(guard='write')))
 rows.append(('PC-final-byte-allowed','canonical',ordinary_fixture(guard='pc-allowed')))
 rows.append(('XIP-byte-read','canonical',ordinary_fixture(address=ENTRY)))
 for arm in ('then','else'):
  for position in ('final','nonfinal'):
   for condition in (0,1):rows.append((f'conditional-{arm}-{position}-{condition}','canonical',conditional_fixture(arm,position,condition)))
 for address in (0x18000000,0x18000001):rows.append((f'skipped-unmapped-{address:08x}','canonical',skipped_fixture(address)))
 for name,address,offset in (('unmapped',0x18000000,0),('unmapped-odd',0x18000001,0),('wrapped-zero',0,1),
                            ('wrapped-max-offset-zero',0,4095),('before-SRAM',0x01BFFFFF,0),('past-SRAM',0x01C80000,0)):
  rows.append((name,'fatal-read',ordinary_fixture(address=address,offset=offset,continuation=True)))
 rows.append(('PC-last-byte-rejected','PC-policy',ordinary_fixture(guard='pc-rejected',continuation=True)))
 rows.append(('deferred-signed-E9DD','signed-policy',ordinary_fixture(opcode=0xE9DD,value=0x80,continuation=True)))
 rows.append(('deferred-C000-E9DC-tail','parallel-policy',ordinary_fixture(bundle=True,continuation=True)))
 assert len(rows)==70 and sum(c=='canonical' for _,c,_ in rows)==61
 return rows


def save_image(name,guest):
 image=CACHE/f'{name}.bin';image.write_bytes(guest.bytes()+bytes(16));return image


def check_reference_state(name,state,fixture):
 guest,expected,words,retired,metadata=fixture
 validate.check(state['pc']==guest.pc and state['instructions']==retired,
                f'{name}: four/six-byte sizing, conditional count or retirement differs')
 validate.check(state['registers']==expected and state['specials']==specials(metadata['special_SP'],metadata['psr']),
                f'{name}: unsigned byte, ordinary destination versus special SP, PSR or RETS differs')
 validate.check(state['inspection']==inspection(words),f'{name}: final seed layout/table neighbors changed')


def success_case(name,fixture):
 guest,expected,words,retired,metadata=fixture
 image=save_image(name,guest);state=isa.compare(name,image,guest.pc,limit=100)
 check_reference_state(name,state,fixture)
 (CACHE/f'{name}-evidence.json').write_text(json.dumps(metadata,indent=2)+'\n')
 return image,state,words


def check_cold_memory(name,memory,words):
 expected=[0]*12
 for at,word in words.items():
  if INSPECTION<=at<INSPECTION+48:expected[(at-INSPECTION)//4]=word
 validate.check(len(memory)==0x80000 and struct.unpack_from('<12I',memory,0x8000)==tuple(expected),
                f'{name}: cold-profile inspection/neighbor memory differs')
 for at,word in words.items():
  validate.check(struct.unpack_from('<I',memory,at-0x01C00000)[0]==word,
                 f'{name}: explicitly seeded word at{at:08x} changed')


def model_fault_expectation(category,fixture):
 guest,expected,words,retired,metadata=fixture
 pc=metadata['operation_PC'];last_access=dict(address=pc,size=metadata['span'],flags=2)
 phase='admission before destination/access/count'
 if category=='fatal-read':
  address=metadata['EA'];last_access=dict(address=address,size=1,flags=0)
  reason=f'unmapped access at 0x{address:08x}';phase='data read1 before destination/count'
 elif category=='PC-policy':
  reason='guest PC lies outside both configured guard windows';phase='fullfetch4 before data/destination/count'
 elif category=='signed-policy':reason='unsupported instruction 0xe9dd'
 else:
  assert category=='parallel-policy';reason='unsupported instruction 0xc000'
 return dict(pc=pc,count=metadata['before'],registers=metadata['incoming_registers'],last_access=last_access,
             reason=reason,phase=phase)


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
 guest,expected,words,retired,metadata=fixture
 modeled=model_fault_expectation(category,fixture)
 image=save_image(name,guest);directory=CACHE/name;directory.mkdir(exist_ok=True)
 for filename in ('state.json','state.sram','state.alnk'):(directory/filename).unlink(missing_ok=True)
 env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
 settings=dict(FM1_POC_STOP_PC=hex(guest.pc),FM1_POC_MAX_INSTRUCTIONS='100',FM1_POC_STATE_DIR=str(directory))
 env.update(settings);command=[*validate.COMMAND,'-kernel',str(image),'-append','alnk-probe']
 result=subprocess.run(command,env=env,cwd=validate.ROOT,capture_output=True,text=True,timeout=15)
 statefile=directory/'state.json'
 validate.check(statefile.exists(),f'{name}: missing model fault snapshot: {result.stderr}')
 state=json.loads(statefile.read_text())
 validate.check(result.returncode!=0 and state['reason']==modeled['reason'] and state['pc']==modeled['pc'] and
                state['instructions']==modeled['count'],f'{name}: exact fault stage/PC/retirement differs')
 validate.check(state['last_access']==modeled['last_access'],f'{name}: data read1 or fullfetch4/6 differs')
 validate.check(state['registers']==modeled['registers'] and state['specials']==specials(metadata['special_SP'],metadata['psr']),
                f'{name}: failed destination, SP, PSR, RETS or continuation changed')
 check_cold_memory(name,(directory/'state.sram').read_bytes(),words)
 if category=='PC-policy':validate.check(state['guards']['debug_message']&(1<<12),f'{name}: PC guard not latched')
 record=dict(command=command,environment=settings,returncode=result.returncode,stderr=result.stderr,state=state,
  fixture_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),modeled_fault_expectation=modeled,metadata=metadata,
  hardware_validation=False,hardware_fault_state_validation=False)
 record.update(reference_record(image,guest.pc))
 if category=='fatal-read':
  address=metadata['EA'];pc=metadata['operation_PC']
  validate.check(record['reference_returncode']!=0 and 'unmapped' in record['reference_stderr'] and
                 f'Access {{ pc: {pc},' in record['reference_stderr'] and
                 f'address: {address}, size: 1, operation: "read"' in record['reference_stderr'],
                 f'{name}: reference fatal category/operation PC/EA/width/read differs')
  record.update(reference_expected_category='unmapped',reference_fatal_access_category_checked_without_CPU_snapshot=True)
 else:
  validate.check(record['reference_returncode']==0,f'{name}: separate reference completion failed')
  check_reference_state(name+'/reference',record['reference_state'],fixture)
  record.update(reference_full_sampled_completion_checked=True,reference_model_policy_difference=True,
                rejection_scope='existing fetch guard policy' if category=='PC-policy' else 'deferred exact opcode/parallel role, not hardware invalidity')
 (directory/'run.json').write_text(json.dumps(record,indent=2)+'\n')
 print(f'PASS {name}: qualified model fault before effects and separate reference outcome')


def generic_replay(image,expected,words):
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
 check_cold_memory('generic-replay',(directory/'state.sram').read_bytes(),words)
 (directory/'run.json').write_text(json.dumps(dict(command=command,environment=settings,returncode=result.returncode,
  stderr=result.stderr,state=state,default_loader=True,optional_observers_configured=True),indent=2)+'\n')
 print('PASS generic-replay: reached SP/offset/byte and default-loader cold memory')


def main():
 CACHE.mkdir(parents=True,exist_ok=True);isa.CACHE=CACHE
 fixtures=make_fixtures();positives=[x for x in fixtures if x[1]=='canonical'];faults=[x for x in fixtures if x[1]!='canonical']
 assert len(positives)==61 and len(faults)==9
 replay=None
 for name,category,fixture in positives:
  result=success_case(name,fixture)
  if name=='actual-E9DC-7034-SP01c79cec-byte43':replay=result
 generic_replay(*replay)
 for name,category,fixture in faults:fault_case(name,category,fixture)
 summary=dict(passed=True,instruction='exact E9DC unsigned SP-relative byte load',reference_compared_cases=61,
  ordinary_cases=51,balanced_conditional_cases=8,skipped_unmapped_cases=2,generic_replays=1,model_fault_cases=9,
  fatal_read1_before_effects_cases=6,PC_guard_fullfetch4_cases=1,deferred_signed_E9DD_cases=1,deferred_C000_tail_cases=1,
  separate_reference_full_policy_completions=3,reference_fatal_categories_without_CPU_snapshot=6,
  private_reference_calls=70,private_full_expected_sampled_completions=64,private_raw_mismatches=0,
  primary_stack_blob='6efa433503fa134bac981a20ebd33cdc09cfe82e',all_low12_unsigned_byte_offset=True,
  high4_ordinary_destination=True,specialSP_unchanged=True,PSR_RETS_unchanged=True,odd_byte_EA_allowed=True,
  boundary_neighbor_word_readback=True,selected_input_overwrite_predicate_latch=True,
  inherited_predicate_IRQ_limits_unchanged=True,default_loader_optional_observers=True,
  normal_reference_memory='diagnostic poison except explicitly owned guest writes',
  fault_generic_memory='cold-zero except explicitly owned guest writes',successful_address32_wrap_validation=False,
  hardware_validation=False,hardware_fault_state_validation=False,reference_fault_state_available=False,
  qemu_sha256=hashlib.sha256(validate.QEMU.read_bytes()).hexdigest())
 (CACHE/'validation.json').write_text(json.dumps(summary,indent=2)+'\n')
 print('PASS SP-relative byte load:61 comparisons, generic replay and9 qualified model faults')


if __name__=='__main__':main()
