#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate compact unsigned-halfword loads as parallel tails.

Pinned Apache loadstore241-250 and slaspec403-458 plus vendor F040/0165+624A
establish load-only (op&E088)==6008, destinationbits0:2/base4:6, signed5-bit
byte offset scaled2 (-32..30), unsigned16/aligned access and no base writeback
or PSR update. Base==destination is permitted (only the loaded destination
changes). Primary prefix rules and the unchanged model do not provide this
compact group3 instruction a parallel-head encoding. Admitted roles are tails
of supported compact/extended heads, yielding4/6-byte bundles; no new8-byte
bundle or compact-load head role is promised. Scalar/memory/helper/classifier
neighbors, incoming capture/tail-first execution, sizing/predicates/IRQ stay
fixed. Incoming address and head-source overwrite discriminators are separate.

Conflicting destinations are reference-valid but conservatively model-rejected
before effects/count. Data faults occur in the tail before head result/flags or
bundle retirement; reference fatal records provide category/EA/width/read and
tail PC only, no CPU fault snapshot or hardware ordering. PC guard failures
check the full4/6-byte bundle before any effects; reference separately completes
those bounded fixtures. Write guards and XIP permit reads. Wrap fault addresses
are checked without a successful32-bit-wrap claim. Successful references poison
unowned inspection memory; alnk-probe/default-loader leave it cold-zero except
explicitly seeded words. Balanced predicates and a following IF are bounded
checks with inherited completion/IRQ limits unchanged. Default-loader replay
uses requested observers, not an observer-free equivalence claim.
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
CACHE=HERE/'.cache/parallel-compact-unsigned-halfword-validation'
ENTRY=0x02000120;INSPECTION=0x01C08000;STACK=INSPECTION-16;PSR=0x89ABCDE5;RETS=0x12345678
GPRS=[0x10203040+i*0x01010101 for i in range(16)]
POISON=[0xA5A5A5A5]*5+[0]*3+[0xA5A5A5A5]*4
VALUE=0x89AB
def compact(dest,base,offset):
 assert 0<=dest<8 and 0<=base<8 and -32<=offset<=30 and offset%2==0
 return 0x6008|dest|(base<<4)|(((offset//2)&31)<<8)
def half(words,address,value):
 if 0x01C00000<=address<=0x01C7FFFE and not address&1:
  at=address&~3;shift=(address&2)*8;old=words.get(at,0xA5A5A5A5)
  words[at]=(old&~(0xFFFF<<shift))|((value&0xFFFF)<<shift)
def setup(registers,address,value=VALUE,base_value=None,psr=PSR,guard=None):
 words={INSPECTION-4:0x13579BDF,INSPECTION:0x12345678,INSPECTION+4:0x89ABCDEF,
        INSPECTION+8:0x76543210,INSPECTION+12:0x0BADF00D}
 if base_value!=address and base_value is not None:half(words,base_value,0x1234)
 half(words,address,value)
 guest=Guest()
 for at,word in words.items():guest.write(at,word)
 if guard=='write':
  guest.write(0x01EEE240,0xE7);guest.write(0x01EEE2C0,address+1);guest.write(0x01EEE280,address+1);guest.write(0x01EEE348,1)
 guest.literal(4,psr);guest.emit(0xE064,0x4580)
 guest.literal(4,RETS);guest.emit(0xE064,0x4380);guest.literal(14,STACK,special=True)
 expected=list(GPRS)
 for reg,val in registers.items():expected[reg]=val&0xFFFFFFFF
 for reg,val in enumerate(expected):guest.literal(reg,val)
 return guest,expected,words

def specials(psr=PSR):
 a=[0]*16;a[3],a[5],a[14]=RETS,psr,STACK;return a

def inspection(words):
 a=list(POISON)
 for at,v in words.items():
  if INSPECTION<=at<INSPECTION+48:a[(at-INSPECTION)//4]=v
 return a

def head_apply(expected,head,incoming):
 op=head[0]
 if op>>13==6:
  scalar=op&0x1FFF
  assert scalar&0xFF00==0x1600
  expected[scalar&15]=incoming[(scalar>>4)&15];return PSR
 if op&0xFFF0==0xF040:
  expected[op&15]=head[1] if head[1]<0x8000 else head[1]|0xFFFF0000;return PSR
 assert head in ((0xF0E0,0x2001),(0xF0E0,0x0001))
 expected[0]=(incoming[(head[1]>>12)&15]+1)&0xFFFFFFFF
 # Bounded add controls:old7+1 flags0;oldFFFFFFFF+1 flags6.
 return (PSR&~15)|(6 if expected[0]==0 else 0)

def success_case(name,dest=2,base=4,offset=4,address=INSPECTION+4,value=VALUE,
             head=(0xF048,0x0165),scalar=False,registers=None,guard=None,psr=PSR):
 base_value=(address-offset)&0xFFFFFFFF
 guest,expected,words=setup({**(registers or {}),base:base_value},address,value,base_value,psr,guard)
 incoming=list(expected);tail=compact(dest,base,offset);before=guest.instructions;pc=guest.pc
 if scalar:guest.emit(tail)
 else:guest.emit(*head,tail)
 guest.emit(0)
 if ENTRY<=address<guest.pc:value=struct.unpack_from('<H',guest.bytes(),address-ENTRY)[0]
 expected[dest]=value
 final_psr=psr if scalar else head_apply(expected,head,incoming)
 if head[0]&0xFFF0==0xF040 or scalar or head[0]>>13==6:final_psr=psr
 return run_success(name,guest,expected,words,guest.instructions,final_psr,metadata=dict(head=head,tail=tail,
      dest=dest,base=base,offset=offset,EA=address,incoming_base=base_value,scalar=scalar,
      bundle_PC=pc,before=before,span=2 if scalar else len(head)*2+2,no_base_writeback=True))


def positive_specs():
 cases=[]
 for reg in range(8):
  cases.append(dict(name=f'destination-{reg}',dest=reg))
  cases.append(dict(name=f'base-{reg}',base=reg))
  cases.append(dict(name=f'base-destination-alias-{reg}',dest=reg,base=reg))
 for offset in (-32,-16,-2,0,2,4,8,16,30):cases.append(dict(name=f'offset-{offset}',offset=offset))
 for value in (0,1,0x7FFF,0x8000,0xFFFF,VALUE):cases.append(dict(name=f'unsigned-{value:04x}',value=value))
 for psr in (0,0xFFFFFFFF):cases.append(dict(name=f'PSR-{psr:08x}',psr=psr))
 actual_base=0x01C117D4;actual_value=59164
 cases.extend([
  dict(name='actual-F040-0165-624A',head=(0xF040,0x0165),address=actual_base+4,value=actual_value),
  dict(name='compact-head-incoming-zero-overwritten',dest=0,head=(0xD608,),registers={0:0x11223344}),
  dict(name='head-overwrites-incoming-address',head=(0xF044,0x0165)),
  dict(name='head-reads-incoming-loaded-destination',head=(0xF0E0,0x2001),registers={2:7}),
  dict(name='head-reads-incoming-address-then-tail-alias',dest=0,base=0,head=(0xD608,)),
  dict(name='head-add-flags-survive-tail',head=(0xF0E0,0x0001),registers={0:0xFFFFFFFF}),
  dict(name='disjoint-high-head',head=(0xF04F,0x0165)),
  dict(name='last-SRAM-halfword',offset=30,address=0x01C7FFFE,value=0xFFFF),
  dict(name='incoming-base-below-SRAM',offset=30,address=0x01C00000),
  dict(name='incoming-base-above-SRAM',offset=-32,address=0x01C7FFFE),
  dict(name='upper-halfword-position',offset=2,address=INSPECTION+2,value=0x8000),
  dict(name='read-only-XIP',address=ENTRY),
  dict(name='protected-window-read',guard='write'),
  dict(name='scalar-distinct-control',scalar=True),
  dict(name='scalar-alias-control',dest=4,base=4,scalar=True),
 ])
 assert len(cases)==56
 return cases


def run_success(name,guest,expected,words,retired,psr=PSR,metadata=None):
 image=save_image(name,guest)
 state=isa.compare(name,image,guest.pc,limit=100)
 validate.check(state['pc']==guest.pc and state['instructions']==retired,
                f'{name}: bundle sizing, conditional arm count or single retirement differs')
 validate.check(state['registers']==expected and state['specials']==specials(psr),
                f'{name}: unsigned halfword, incoming address/source, fields, PSR or RETS differs')
 validate.check(state['inspection']==inspection(words),f'{name}: owned halfwords/old base/neighbors changed')
 (CACHE/f'{name}-evidence.json').write_text(json.dumps(metadata,indent=2)+'\n')
 return image,state,words


def save_image(name,guest):
 image=CACHE/f'{name}.bin';image.write_bytes(guest.bytes()+bytes(16));return image


def check_cold_memory(memory,words,name):
 expected=[0]*12
 for at,word in words.items():
  if INSPECTION<=at<INSPECTION+48:expected[(at-INSPECTION)//4]=word
 validate.check(len(memory)==0x80000 and struct.unpack_from('<12I',memory,0x8000)==tuple(expected),
                f'{name}: cold-profile inspection/neighbor memory differs')
 for at,word in words.items():
  validate.check(struct.unpack_from('<I',memory,at-0x01C00000)[0]==word,
                 f'{name}: explicitly seeded word at{at:08x} changed')


def conditional_case(width,arm,position,condition):
 name=f'conditional-{width}-{arm}-{position}-{condition}'
 guest,expected,words=setup({4:INSPECTION,7:condition},INSPECTION+4)
 incoming=list(expected);n=1 if position=='final' else 2
 then,otherwise=(n,1) if arm=='then' else (1,n)
 guest.emit(0xEA27,((then-1)<<14)|(otherwise<<12)|1)
 for block in ('then','else'):
  if block==arm:
   head=(0xD608,) if width==4 else (0xF048,0x0165)
   guest.emit(*head,compact(2,4,4))
   if position=='nonfinal':guest.emit(0)
  else:guest.emit(0xE04D,0x1111)
 selected=condition==(0 if arm=='then' else 1)
 if selected:
  expected[2]=VALUE;head_apply(expected,head,incoming)
 else:expected[13]=0x1111
 guest.emit(0xEA20,1);guest.emit(0xE04E,0x2222)
 skipped=1 if selected else n
 if expected[0]&1:skipped+=1
 else:expected[14]=0x2222
 guest.emit(0)
 return run_success(name,guest,expected,words,guest.instructions-skipped,
                    metadata=dict(width=width,arm=arm,position=position,condition=condition,
                                  selected=selected,independent_followup_IF=True))


def skipped_access_case(address):
 name=f'skipped-bad-EA-{address:08x}'
 guest,expected,words=setup({4:address,7:1},address)
 guest.emit(0xEA27,1);guest.emit(0xF048,0x0165,compact(2,4,0))
 guest.emit(0xEA20,1);guest.emit(0xE04E,0x2222)
 if not expected[0]&1:expected[14]=0x2222
 guest.emit(0)
 return run_success(name,guest,expected,words,guest.instructions-1-int(bool(expected[0]&1)),
                    metadata=dict(skipped_access=True))


def reference_record(image,env):
 command=['mise','exec','--','cargo','run','--manifest-path',str(HERE/'reference/Cargo.toml'),
          '--locked','--offline','--','snapshot',str(image)]
 result=subprocess.run(command,env=env,cwd=validate.ROOT,capture_output=True,text=True,timeout=15)
 record=dict(reference_command=command,reference_returncode=result.returncode,
             reference_stdout=result.stdout,reference_stderr=result.stderr,reference_fault_state_available=False)
 if result.returncode==0:record['reference_state']=json.loads(result.stdout)
 return record


def run_fault(name,guest,expected,words,pc,before,span,reason,reference_expected=None,
              address=None,reference_category=None,pc_guard=False,head_width=None):
 image=save_image(name,guest);directory=CACHE/name;directory.mkdir(exist_ok=True)
 for filename in ('state.json','state.sram','state.alnk'):(directory/filename).unlink(missing_ok=True)
 env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
 observer=dict(FM1_POC_STOP_PC=hex(guest.pc),FM1_POC_MAX_INSTRUCTIONS='100',FM1_POC_STATE_DIR=str(directory))
 env.update(observer)
 command=[*validate.COMMAND,'-kernel',str(image),'-append','alnk-probe']
 result=subprocess.run(command,env=env,cwd=validate.ROOT,capture_output=True,text=True,timeout=15)
 validate.check((directory/'state.json').exists(),f'{name}: missing fault snapshot: {result.stderr}')
 state=json.loads((directory/'state.json').read_text())
 validate.check(result.returncode!=0 and state['reason']==reason and state['pc']==pc and
                state['instructions']==before,f'{name}: reason, bundle PC or pre-retirement count differs')
 attempted=dict(address=address,size=2,flags=0) if address is not None else dict(address=pc,size=span,flags=2)
 validate.check(state['last_access']==attempted,f'{name}: tail read width2 or complete fetch span differs')
 validate.check(state['registers']==expected and state['specials']==specials(),
                f'{name}: failed tail/head result, base writeback, PSR or RETS changed')
 check_cold_memory((directory/'state.sram').read_bytes(),words,name)
 if pc_guard:validate.check(state['guards']['debug_message']&(1<<12),f'{name}: PC guard not latched')
 record=dict(command=command,environment=observer,returncode=result.returncode,stderr=result.stderr,state=state,
             modeled_fault_phase='tail-read-before-head' if address is not None else 'precheck-before-effects',
             hardware_fault_state_validation=False,fixture_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
 record.update(reference_record(image,env))
 if reference_expected is not None:
  validate.check(record['reference_returncode']==0,f'{name}: separate reference completion failed')
  oracle=record['reference_state']
  validate.check(oracle['pc']==guest.pc and oracle['instructions']==guest.instructions and
                 oracle['registers']==reference_expected and oracle['specials']==specials() and
                 oracle['inspection']==inspection(words),f'{name}: separate sampled reference policy completion differs')
  record['reference_full_sampled_completion_checked']=True
  record['reference_model_policy_difference']=True
 else:
  validate.check(record['reference_returncode']!=0 and reference_category in record['reference_stderr'] and
                 f'Access {{ pc: {pc+head_width},' in record['reference_stderr'] and
                 f'address: {address}, size: 2, operation: "read"' in record['reference_stderr'],
                 f'{name}: fatal reference category/tail PC/EA/width/read differs')
  record.update(reference_expected_category=reference_category,
                reference_fatal_category_checked_without_CPU_snapshot=True)
 (directory/'run.json').write_text(json.dumps(record,indent=2)+'\n')
 print(f'PASS {name}: qualified model fault stage and separate reference outcome')


def conflict_case(name,head):
 guest,expected,words=setup({4:INSPECTION},INSPECTION+4)
 incoming=list(expected);pc,before=guest.pc,guest.instructions
 guest.emit(*head,compact(2,4,4));guest.emit(0)
 oracle=list(expected);oracle[2]=VALUE;head_apply(oracle,head,incoming)
 return run_fault(name,guest,expected,words,pc,before,len(head)*2+2,
                  f'unsupported instruction 0x{head[0]:04x}',reference_expected=oracle)


def access_specs():
 return [
  ('unaligned-extended-tail',INSPECTION+1,0,'unaligned access',(0xF048,0x0165),{}),
  ('unmapped-extended-tail',0x18000000,0,'unmapped',(0xF048,0x0165),{}),
  ('unaligned-compact-tail',INSPECTION+1,0,'unaligned access',(0xD608,),{}),
  ('unmapped-compact-tail',0x18000000,0,'unmapped',(0xD608,),{}),
  ('wrap-zero-tail',0,2,'unmapped',(0xF048,0x0165),{}),
  ('wrap-high-tail',0xFFFFFFFE,-2,'unmapped',(0xF048,0x0165),{}),
  ('past-SRAM-tail',0x01C80000,0,'unmapped',(0xF048,0x0165),{}),
  ('tail-read-prevents-head-flags',0x18000000,0,'unmapped',(0xF0E0,0x0001),{0:0xFFFFFFFF}),
 ]


def access_case(name,address,offset,category,head,registers):
 base_value=(address-offset)&0xFFFFFFFF
 guest,expected,words=setup({**registers,4:base_value},address,base_value=base_value)
 pc,before=guest.pc,guest.instructions
 guest.emit(*head,compact(2,4,offset));guest.emit(0)
 reason='unaligned access' if category=='unaligned access' else f'unmapped access at 0x{address:08x}'
 return run_fault(name,guest,expected,words,pc,before,len(head)*2+2,reason,address=address,
                  reference_category=category,head_width=len(head)*2)


def pc_guard_case(width,head):
 guest,expected,words=setup({4:INSPECTION},INSPECTION+4)
 guest.write(0x01EEE240,0xE7);guest.write(0x01EEE384,ENTRY)
 header=guest.pc+14;upper=header+width-2
 guest.write(0x01EEE380,upper);expected[0],expected[1]=upper,0x01EEE380
 incoming=list(expected);pc,before=guest.pc,guest.instructions
 guest.emit(*head,compact(2,4,4));guest.emit(0)
 oracle=list(expected);oracle[2]=VALUE;head_apply(oracle,head,incoming)
 return run_fault(f'PC-last-byte-{width}',guest,expected,words,pc,before,width,
                  'guest PC lies outside both configured guard windows',reference_expected=oracle,pc_guard=True)


def generic_replay(image,expected,words):
 directory=CACHE/'generic-replay';directory.mkdir(exist_ok=True)
 for filename in ('state.json','state.sram','state.alnk'):(directory/filename).unlink(missing_ok=True)
 env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
 observer=dict(FM1_POC_STOP_PC=hex(expected['pc']),FM1_POC_MAX_INSTRUCTIONS='100',FM1_POC_STATE_DIR=str(directory))
 env.update(observer);command=[*validate.COMMAND,'-kernel',str(image)]
 result=subprocess.run(command,env=env,cwd=validate.ROOT,capture_output=True,text=True,timeout=15)
 validate.check(result.returncode==0,f'generic-replay: {result.stderr}')
 state=json.loads((directory/'state.json').read_text())
 for field in ('pc','instructions','registers','specials'):
  validate.check(state[field]==expected[field],f'generic-replay: {field} differs')
 check_cold_memory((directory/'state.sram').read_bytes(),words,'generic-replay')
 (directory/'run.json').write_text(json.dumps(dict(command=command,environment=observer,
  returncode=result.returncode,stderr=result.stderr,state=state,default_loader=True,
  optional_observers_configured=True),indent=2)+'\n')
 print('PASS generic-replay: reached compact halfword tail and default-loader memory')


def main():
 CACHE.mkdir(parents=True,exist_ok=True);isa.CACHE=CACHE
 cases=positive_specs();replay=None
 for case in cases:
  result=success_case(**case)
  if case['name']=='actual-F040-0165-624A':replay=result
 for width in (4,6):
  for arm in ('then','else'):
   for position in ('final','nonfinal'):
    for condition in (0,1):conditional_case(width,arm,position,condition)
 for address in (INSPECTION+1,0x18000000):skipped_access_case(address)
 generic_replay(*replay)
 conflict_case('conflicting-extended-literal',(0xF042,7))
 conflict_case('conflicting-compact-move',(0xD602,))
 for settings in access_specs():access_case(*settings)
 for width,head in ((4,(0xD608,)),(6,(0xF048,0x0165))):pc_guard_case(width,head)
 summary=dict(passed=True,instruction='compact unsigned-halfword load-only parallel tail classification',
  reference_compared_cases=len(cases)+18,ordinary_cases=len(cases),balanced_conditional_cases=16,
  skipped_bad_access_cases=2,generic_replays=1,model_fault_cases=12,destination_conflict_faults=2,
  tail_read_faults_before_head=8,PC_guard_full_bundle_span_faults=2,
  separate_reference_policy_completions=4,reference_fatal_access_categories_without_CPU_snapshot=8,
  private_exact_fixture_records=86,private_full_sampled_completions=78,
  primary_loadstore_blob='b6b9ba01a407a86e613cdb8717bfb4f8d2d3de05',
  exact_classifier_predicate='(op & 0xe088)==0x6008',unsigned16=True,signed_byte_offset_range=[-32,30],
  compact_group3_head_unavailable=True,new_tail_bundle_widths=[4,6],no_new_eight_byte_bundle_role=True,
  conflict_model_policy_not_ISA_invalidity=True,
  fault_order='tail read before head/PSR/count; reference provides fatal category/access only, no fault state or hardware ordering',
  normal_reference_unowned_memory='diagnostic poison',fault_default_loader_unowned_memory='cold-zero except explicitly seeded words',
  inherited_completion_and_IRQ_limits_unchanged=True,default_loader_optional_observers=True,
  qemu_sha256=hashlib.sha256(validate.QEMU.read_bytes()).hexdigest(),
  hardware_validation=False,hardware_fault_state_validation=False,reference_fault_state_available=False)
 (CACHE/'validation.json').write_text(json.dumps(summary,indent=2)+'\n')
 print(f'PASS compact unsigned-halfword tail: {len(cases)+18} comparisons, generic replay and12 model faults')


if __name__=='__main__':main()
