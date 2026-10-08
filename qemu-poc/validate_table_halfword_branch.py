#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate exact compact TBH (op & FFF0 == 0110) table halfword branches.

Pinned Apache progflow139-144 establishes full low-nibble ordinary GPR index,
unsigned16 entry at PC+2+incoming byte offset, and PC+2+(entry<<1). No GPR,
special SP, PSR or RETS writes occur. The reached 0111/r1=8 table value0056
maps vendor PC02009bbc to02009c6a. Primary does not explicitly establish
alignment: aligned LE16 is the existing model policy supported by sampled
reference fault categories. Successful mapped target arithmetic, negative
byte indices and entryFFFF are discriminated; successful32-bit PC wrapping
cannot be executed in available mapped instruction space.

Table reads precede retirement; target fetch faults occur after retirement.
Reference fatals provide category, read address/width and operation PC only,
never CPU fault-state or hardware ordering. Balanced selected/skipped2-byte
branches beside6-byte literals and an independent following IF are bounded
completion checks. Taken exits beyond an arm retain the existing predicate
state, so a following IF faults after TBH retirement; reference completes it.
Retained predicate IRQ blocking is source-inspected, not validated here.

TBB unsigned-byte and odd-byte controls now execute as ordinary positives.
The genuine C111+NOP remains model-deferred. The exploratory
C111 reference completes with count53 rather than primary-candidate count38;
original mismatch is retained, with a separate full sampled-state check.
Fifteen padded NOPs are compatible with that difference, not an execution
trace, general parallel-TBH semantics or hardware validity proof. Scalar TBH
and TBB are admitted; helpers, scanner, classifiers and predicate logic stay fixed.

Normal references/diag poison unowned inspection bytes; alnk-probe/default
loader leave them cold-zero except explicit guest writes. Generic replay uses
requested observers; no observer-free or full firmware HOME claim is made.
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
CACHE=HERE/'.cache/table-halfword-branch-validation'
ENTRY=0x02000120;BRANCH=ENTRY+0x500;INSPECTION=0x01C08000
PSR=0x89ABCDE5;RETS=0x12345678;STACK=INSPECTION-16
GPRS=[0x10203040+i*0x01010101 for i in range(16)]
POISON=[0xA5A5A5A5]*5+[0]*3+[0xA5A5A5A5]*4
MARKERS={INSPECTION:0x12345678,INSPECTION+4:0x89ABCDEF,INSPECTION+8:0x76543210}
def setup(registers,psr=PSR,table_words=None,guard_high=None,write_guard=None):
 words={**MARKERS,**(table_words or {})};guest=Guest()
 for at,word in words.items():guest.write(at,word)
 if guard_high is not None:
  guest.write(0x01EEE240,0xE7);guest.write(0x01EEE380,guard_high);guest.write(0x01EEE384,ENTRY)
 if write_guard is not None:
  guest.write(0x01EEE240,0xE7);guest.write(0x01EEE2C0,write_guard);guest.write(0x01EEE280,write_guard);guest.write(0x01EEE348,1)
 guest.literal(4,psr);guest.emit(0xE064,0x4580)
 guest.literal(4,RETS);guest.emit(0xE064,0x4380);guest.literal(14,STACK,special=True)
 expected=list(GPRS)
 for reg,val in registers.items():expected[reg]=val&0xFFFFFFFF
 for reg,val in enumerate(expected):guest.literal(reg,val)
 return guest,expected,words

def specials(psr):
 a=[0]*16;a[3],a[5],a[14]=RETS,psr,STACK;return a

def inspection(words):
 a=list(POISON)
 for at,value in words.items():
  if INSPECTION<=at<INSPECTION+48:a[(at-INSPECTION)//4]=value
 return a

def goto(guest,target):
 delta=(target-(guest.pc+4))//2;guest.emit(0xEAC0|((delta>>16)&63),delta&0xFFFF)

def pad_to(guest,address):
 assert address>=guest.pc
 guest.words.extend([0]*((address-guest.pc)//2))

def place(guest,address,values):
 assert not address&1 and address>=ENTRY
 end=(address-ENTRY)//2+len(values)
 if len(guest.words)<end:guest.words.extend([0]*(end-len(guest.words)))
 guest.words[(address-ENTRY)//2:end]=values

def table_seed(address,value,size=2):
 if 0x01C00000<=address<=0x01C7FFFE and not address&1:
  aligned=address&~3;shift=(address&2)*8;mask=(1<<(size*8))-1
  return {aligned:(0xA5A5A5A5&~(mask<<shift))|((value&mask)<<shift)}
 return {}

def ordinary_fixture(reg=1,index=8,value=13,psr=PSR,EA=None,guard=None,tbb=False):
 op=(0x0100 if tbb else 0x0110)|reg;size=1 if tbb else 2;nextpc=BRANCH+2
 address=(nextpc+index)&0xFFFFFFFF if EA is None else EA
 index=(address-nextpc)&0xFFFFFFFF
 actual_value=op if address==BRANCH and not tbb else value
 table_words=table_seed(address,actual_value,size)
 high=BRANCH if guard=='branch' else BRANCH+1 if guard=='target' else None
 guest,expected,words=setup({reg:index},psr,table_words,high,address+1 if guard=='write' else None)
 goto(guest,BRANCH);pad_to(guest,BRANCH);before=guest.instructions;guest.emit(op)
 target=(nextpc+((actual_value&((1<<(size*8))-1))<<1))&0xFFFFFFFF
 stop=target+2
 assert ENTRY<=target<ENTRY+0x40000
 pad_to(guest,max(target+4,BRANCH+80))
 # Competing positions distinguish PC versus PC+2 and byte versus scaled index.
 competing={}
 for at,half in ((address-2,0x21),(nextpc+index*2,0x31)):
  if ENTRY<=at<BRANCH+80 and not at&1 and at not in (BRANCH,address,target):
   place(guest,at,[half]);competing[at]=half
 if ENTRY<=address<guest.pc and (tbb or not address&1) and address!=BRANCH:
  if tbb:
   loc=address-ENTRY;payload=bytearray(guest.bytes());payload[loc]=value&255
   guest.words=list(struct.unpack('<'+'H'*(len(payload)//2),payload))
  else:place(guest,address,[value])
 place(guest,target,[0])
 if ENTRY<=address<guest.pc:
  actual_value=int.from_bytes(guest.bytes()[address-ENTRY:address-ENTRY+size],'little')
  assert target==(nextpc+(actual_value<<1))&0xFFFFFFFF,(hex(address),actual_value,target)
 return guest,expected,words,stop,before+2,dict(reg=reg,incoming_index=index,opcode=op,operation_PC=BRANCH,
  table_EA=address,requested_table_value=value,actual_table_value=actual_value,table_width=size,target=target,
  before=before,guard=guard,psr=psr,competing_seed_map=competing,actual_owned_SRAM_words=words)

def conditional_fixture(side,position,condition,variant='zero',outside=False,bad_EA=None):
 psr=0xFFFFFFFF if variant=='end' and position=='final' else PSR
 count=2;entry=3 if variant=='end' and position=='nonfinal' else 0
 table=INSPECTION+16
 # Table value for a taken exit is resolved from emitted layout below.
 tablewords={table:0xA5A50000|entry}
 registers={7:condition,5:0,1:0}
 guest,expected,words=setup(registers,psr,tablewords)
 before=guest.instructions;literal_index={}
 # OrdinaryGPR literals are the final16 instructions and each has width6.
 first_literal=len(guest.words)-48
 for reg in range(16):literal_index[reg]=first_literal+reg*3
 marker=(6 if side=='then' else 8,0x6666 if side=='then' else 0x8888)
 arm=[('marker',*marker),('branch',)] if position=='final' else [('branch',),('marker',*marker)]
 then=arm if side=='then' else [('marker',6,0x6666)]
 otherwise=arm if side=='else' else [('marker',8,0x8888)]
 guest.emit(0xEA27,((len(then)-1)<<14)|(len(otherwise)<<12)|1)
 branch_pc=None
 for sequence in (then,otherwise):
  for item in sequence:
   if item[0]=='marker':guest.literal(item[1],item[2])
   else:branch_pc=guest.pc;guest.emit(0x0111)
 after_arm=guest.pc;guest.literal(10,0xAAAA);next_if=guest.pc
 guest.emit(0xEA25,1);guest.literal(11,0xBBBB);guest.emit(0)
 if outside:entry=(next_if-(branch_pc+2))//2;words[table]=0xA5A50000|entry
 address=table if bad_EA is None else bad_EA
 index=(address-(branch_pc+2))&0xFFFFFFFF
 expected[1]=index
 li=literal_index[1];guest.words[li+1]=index&0xFFFF;guest.words[li+2]=index>>16
 # Patch the exact guest table-write literal, maintaining seed/write order.
 tablewrite=3 # Three marker word writes precede the table word write.
 # Guest.write emits addressliteral,value literal,store:7 words.
 valuepos=tablewrite*7+3
 guest.words[valuepos+1]=words[table]&0xFFFF;guest.words[valuepos+2]=words[table]>>16
 selected=condition==(0 if side=='then' else 1)
 selected_arm=then if condition==0 else otherwise
 if selected and outside:
  if position=='final':expected[marker[0]]=marker[1]
  expected[11]=0xBBBB
  retired=before+1+1+int(position=='final')+3
 else:
  for item in selected_arm:
   if item[0]=='marker':
    if not (selected and variant=='end' and position=='nonfinal'):expected[item[1]]=item[2]
  expected[10]=0xAAAA;expected[11]=0xBBBB
  selected_count=len(selected_arm)-int(selected and variant=='end' and position=='nonfinal')
  retired=before+1+selected_count+4
 return guest,expected,words,guest.pc,retired,dict(side=side,position=position,condition=condition,variant=variant,
  selected=selected,table_EA=address,actual_table_value=entry,operation_PC=branch_pc,target=branch_pc+2+entry*2,
  before=before,next_IF=next_if,outside=outside,psr=psr,actual_owned_SRAM_words=words)

def make_fixtures():
 fixtures=[]
 for reg in range(16):fixtures.append((f'field-{reg}','ordinary',ordinary_fixture(reg=reg)))
 for index in (0,2,4,8,16,30,-2,-16,-32):fixtures.append((f'index-{index}','ordinary',ordinary_fixture(index=index)))
 for value in (0,1,0x0102,0x7FFF,0x8000,0xFFFF):fixtures.append((f'entry-{value:04x}','ordinary',ordinary_fixture(index=0,value=value)))
 for psr in (0,0xFFFFFFFF):fixtures.append((f'PSR-{psr:08x}','ordinary',ordinary_fixture(psr=psr)))
 for label,EA,value,guard in [('SRAM-table',INSPECTION+16,0x0102,None),('last-SRAM-half',0x01C7FFFE,0x8000,None),
                             ('protected-table-read',INSPECTION+16,0x0102,'write')]:
  fixtures.append((label,'ordinary',ordinary_fixture(EA=EA,value=value,guard=guard)))
 fixtures.append(('actual-0111-index8-value0056','ordinary',ordinary_fixture(value=0x56)))
 for side in ('then','else'):
  for position in ('final','nonfinal'):
   for condition in (0,1):
    for variant in ('zero','end'):
     name=f'conditional-{side}-{position}-{condition}-{variant}'
     fixtures.append((name,'conditional',conditional_fixture(side,position,condition,variant)))
 for name,EA in [('odd',INSPECTION+1),('unmapped',0x18000000)]:
  fixtures.append((f'skipped-{name}-table','conditional',conditional_fixture('then','final',1,bad_EA=EA)))
 for side in ('then','else'):
  for position in ('final','nonfinal'):
   fixtures.append((f'exit-{side}-{position}','exit-characterization',conditional_fixture(side,position,0 if side=='then' else 1,outside=True)))
 # Two scalar TBB controls retain unsigned-byte and odd-byte address assertions.
 fixtures.append(('TBB-unsigned-byte','ordinary',ordinary_fixture(tbb=True,index=8,value=0x80)))
 fixtures.append(('TBB-odd-byte','ordinary',ordinary_fixture(tbb=True,index=9,value=0x7F)))
 for label,EA,marker in [('unaligned',INSPECTION+1,'unaligned access'),('unmapped',0x18000000,'unmapped'),
                        ('wrap-zero',0,'unmapped'),('wrap-high',0xFFFFFFFE,'unmapped'),('past-SRAM',0x01C80000,'unmapped')]:
  fixture=ordinary_fixture(EA=EA);fixture[-1]['fatal_category']=marker
  fixtures.append((f'table-read-{label}','fatal-read',fixture))
 for stage in ('branch','target'):fixtures.append((f'PC-guard-{stage}','PC-policy',ordinary_fixture(guard=stage)))
 # Genuine parallelencoding: discriminate headnext+2 versus bundlenext+4 without claiming support.
 TABLE=INSPECTION+16
 index=(TABLE-(BRANCH+2))&0xFFFFFFFF
 guest,expected,words=setup({1:index},table_words={TABLE:0x00280010})
 goto(guest,BRANCH);pad_to(guest,BRANCH);before=guest.instructions;guest.emit(0xC111,0)
 join=BRANCH+0x100
 for target,marker in ((BRANCH+2+16*2,0x1111),(BRANCH+4+40*2,0x2222)):
  at=Guest(target);at.literal(12,marker);goto(at,join);place(guest,target,at.words)
 place(guest,join,[0]);expected[12]=0x1111
 fixtures.append(('deferred-C111-NOP','bundle-characterization',(guest,expected,words,join+2,before+4,
  dict(operation_PC=BRANCH,table_EA=TABLE,before=before,psr=PSR,primary_candidate_target=BRANCH+34,
       bundle_next_candidate_target=BRANCH+84,join=join,actual_owned_SRAM_words=words))))
 assert len(fixtures)==69,len(fixtures)
 return fixtures


def save_image(name,guest):
 image=CACHE/f'{name}.bin';image.write_bytes(guest.bytes()+bytes(16));return image


def check_reference_state(name,state,fixture,count=None):
 guest,expected,words,stop,retired,metadata=fixture
 validate.check(state['pc']==stop and state['instructions']==(retired if count is None else count),
                f'{name}: table target, entry scaling, predicate width or retirement differs')
 validate.check(state['registers']==expected and state['specials']==specials(metadata['psr']),
                f'{name}: byte-index GPR fields, special SP, PSR or RETS changed')
 validate.check(state['inspection']==inspection(words),f'{name}: table/neighbor memory changed')


def success_case(name,fixture):
 guest,expected,words,stop,retired,metadata=fixture
 image=save_image(name,guest);state=isa.compare(name,image,stop,limit=100)
 check_reference_state(name,state,fixture)
 (CACHE/f'{name}-evidence.json').write_text(json.dumps(metadata,indent=2)+'\n')
 return image,state,words


def check_cold_memory(name,memory,words):
 expected=[0]*12
 for at,word in words.items():
  if INSPECTION<=at<INSPECTION+48:expected[(at-INSPECTION)//4]=word
 validate.check(len(memory)==0x80000 and struct.unpack_from('<12I',memory,0x8000)==tuple(expected),
                f'{name}: cold-profile inspection/neighbor memory changed')
 for at,word in words.items():
  validate.check(struct.unpack_from('<I',memory,at-0x01C00000)[0]==word,
                 f'{name}: explicitly seeded table word at{at:08x} changed')


def model_fault_expectation(category,fixture):
 guest,expected,words,stop,retired,metadata=fixture
 expected=list(expected);pc=metadata['operation_PC'];count=metadata['before'];span=2
 phase='admission before table read and retirement';address=pc;flags=2
 if category=='fatal-read':
  address=metadata['table_EA'];flags=0;phase='table read before retirement'
  reason=('unaligned access' if metadata['fatal_category']=='unaligned access'
          else f'unmapped access at 0x{address:08x}')
 elif category=='PC-policy':
  reason='guest PC lies outside both configured guard windows'
  if metadata['guard']=='target':
   pc=address=metadata['target'];count+=1;phase='target fetch after table read and TBH retirement'
  else:phase='TBH fetch before table read and retirement'
 elif category=='exit-characterization':
  pc=address=metadata['next_IF'];count+=2+int(metadata['position']=='final');span=4
  expected[11]=GPRS[11]
  reason='nested conditional block is unsupported';phase='following IF after taken TBH exit and retirement'
 elif category=='deferred-TBB':reason=f"unsupported instruction 0x{metadata['opcode']:04x}"
 else:
  assert category=='bundle-characterization';span=4;expected[12]=GPRS[12]
  reason='unsupported instruction 0xc111'
 return dict(pc=pc,count=count,registers=expected,reason=reason,phase=phase,
             last_access=dict(address=address,size=span,flags=flags))


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
 guest,expected,words,stop,retired,metadata=fixture
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
 validate.check(result.returncode!=0 and state['reason']==modeled['reason'] and
                state['pc']==modeled['pc'] and state['instructions']==modeled['count'],
                f'{name}: exact modeled fault PC/phase/retirement differs')
 validate.check(state['last_access']==modeled['last_access'],f'{name}: table read2 or fetch2/4 address/span differs')
 validate.check(state['registers']==modeled['registers'] and state['specials']==specials(metadata['psr']),
                f'{name}: modeled fault changed GPR, PSR, RETS or executed continuation')
 check_cold_memory(name,(directory/'state.sram').read_bytes(),words)
 if category=='PC-policy':validate.check(state['guards']['debug_message']&(1<<12),f'{name}: PC guard not latched')
 record=dict(command=command,environment=settings,returncode=result.returncode,stderr=result.stderr,state=state,
             fixture_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),modeled_fault_expectation=modeled,
             hardware_validation=False,hardware_fault_state_validation=False,metadata=metadata)
 record.update(reference_record(image,stop))
 if category=='fatal-read':
  marker=metadata['fatal_category'];address=metadata['table_EA'];operation_pc=metadata['operation_PC']
  validate.check(record['reference_returncode']!=0 and marker in record['reference_stderr'] and
                 f'Access {{ pc: {operation_pc},' in record['reference_stderr'] and
                 f'address: {address}, size: 2, operation: "read"' in record['reference_stderr'],
                 f'{name}: reference fatal category/operation PC/EA/read2 differs')
  record.update(reference_expected_category=marker,reference_fatal_category_checked_without_CPU_snapshot=True)
 else:
  validate.check(record['reference_returncode']==0,f'{name}: separate reference completion failed')
  reference_count=53 if category=='bundle-characterization' else retired
  check_reference_state(name+'/reference',record['reference_state'],fixture,reference_count)
  record['reference_full_sampled_completion_checked']=True
  record['reference_model_policy_difference']=True
  if category=='bundle-characterization':
   # Preserve the initial primary-candidate prediction separately from observed count53.
   assert retired==38
   walk=guest.bytes()[BRANCH+4-ENTRY:metadata['primary_candidate_target']-ENTRY]
   validate.check(walk==bytes(30),f'{name}: constructed15-NOP padding receipt differs')
   record.update(original_primary_candidate_count=retired,observed_reference_count=reference_count,
                 original_expected_count_mismatch_retained=True,constructed_NOP_walk_bytes=walk.hex(),
                 C111_qualification='15extra retirements are compatible with padding, not an execution trace or general parallel/hardware contract')
  elif category=='exit-characterization':
   record.update(branch_already_retired=True,fault_belongs_to_following_IF=True,
                 retained_predicate_IRQ_blocking='source inspected; no IRQ validation')
 (directory/'run.json').write_text(json.dumps(record,indent=2)+'\n')
 print(f'PASS {name}: qualified model fault phase and separate reference outcome')


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
 print('PASS generic-replay: reached table entry/index and default-loader cold memory')


def main():
 CACHE.mkdir(parents=True,exist_ok=True);isa.CACHE=CACHE
 fixtures=make_fixtures();positives=[x for x in fixtures if x[1] in ('ordinary','conditional')]
 faults=[x for x in fixtures if x[1] not in ('ordinary','conditional')]
 assert len(positives)==57 and len(faults)==12
 replay=None
 for name,category,fixture in positives:
  result=success_case(name,fixture)
  if name=='actual-0111-index8-value0056':replay=result
 generic_replay(*replay)
 for name,category,fixture in faults:fault_case(name,category,fixture)
 summary=dict(passed=True,instruction='exact compact TBH0110/FFF0 and two scalar TBB0100 controls',reference_compared_cases=57,
  ordinary_reference_cases=39,balanced_conditional_cases=16,skipped_bad_table_read_cases=2,
  generic_replays=1,model_fault_cases=12,table_read_faults_before_retirement=5,PC_guard_faults=2,
  inherited_taken_exit_following_IF_faults=4,deferred_TBB_policy_faults=0,deferred_C111_classifier_faults=1,
  separate_reference_policy_completions=7,reference_fatal_categories_without_CPU_snapshot=5,
  private_reference_calls=69,private_original_primary_expected_full_matches=63,
  private_separately_characterized_C111_completion=1,private_fatal_categories=5,
  C111_original_count_mismatch=dict(primary_candidate=38,observed=53,retained=True),
  C111_extra15_NOPs='compatible with constructed padding; not an executed trace or hardware/general parallel contract',
  primary_blob='622d767fceb3ad46972ae821394226ff1e6117b2',index='full low4 ordinaryGPR byte offset',
  entry='unsignedLE16 doubled',PC_base='PC+2',alignment='existing model policy; primary not explicit',
  table_read_before_retirement=True,target_fetch_after_retirement=True,inherited_completion_IRQ_limits_unchanged=True,
  normal_reference_memory='diagnostic poison except explicitly owned guest writes',
  fault_generic_memory='cold-zero except explicitly owned guest writes',default_loader_optional_observers=True,
  successful_PC32_wrap_validation=False,hardware_validation=False,hardware_fault_state_validation=False,
  reference_fault_state_available=False,qemu_sha256=hashlib.sha256(validate.QEMU.read_bytes()).hexdigest())
 (CACHE/'validation.json').write_text(json.dumps(summary,indent=2)+'\n')
 print('PASS table halfword branch:55 comparisons, generic replay and14 qualified model faults')


if __name__=='__main__':main()
