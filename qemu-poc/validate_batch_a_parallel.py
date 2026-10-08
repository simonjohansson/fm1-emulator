#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Frozen private Batch A parallel matrix. Preparation never launches probes.

Run only by coordinator with --run --approved-matrix-sha256=<reviewed hash>.
Separate pinned copied CLI; no Cargo/build/reference implementation imports.
Fault state/order are preserved QEMU policy, not hardware measurements.
"""
import argparse,hashlib,json,os,struct,subprocess,sys
from pathlib import Path
ROOT=Path('/Users/simonjohansson/src/fm1-qemu-poc')
sys.path.insert(0,str(ROOT/'qemu-poc'))
from validate_peripherals import Guest
ENTRY=0x02000120; INSPECTION=0x01c08000; STACK=INSPECTION-24
PSR=0x89abcde5; RETS=0x12345678
REFERENCE_SHA='c96ed8b21d73bd2934127b72f1a21f31d82acdb6a0792ec2b130852e478dec94'
MASK=0xffffffff

def signed(x):return x-0x100000000 if x&0x80000000 else x

def alu(a,b,sub,psr):
 value=(a-b if sub else a+b)&MASK
 carry=a>=b if sub else a+b>MASK
 z=signed(a)-signed(b) if sub else signed(a)+signed(b)
 flags=int(not -0x80000000<=z<=0x7fffffff)|(int(carry)<<1)|(int(value==0)<<2)|((value>>31)<<3)
 return value,(psr&~15)|flags

def literal(x):
 # Independent facts plus explicitly inherited disputed repeat encodings.
 low=x&0xff; mode=(x>>10)&3
 if mode==0:return low*(1,0x00010001,0x01000100,0x01010101)[(x>>8)&3]
 # Mask shift is low7 plus leading one, placed at 24/16/8 then right shifted.
 return ((0x80|(x&0x7f)) << (32-mode*8)) >> ((x>>7)&7)

def write_mem(words,address,value,size):
 assert not address&(size-1)
 at=address&~3;shift=(address&3)*8;mask=((1<<(size*8))-1)<<shift
 words[at]=(words.get(at,0xA5A5A5A5)&~mask)|((value<<shift)&mask)

def read_mem(words,address,size):
 return (words[address&~3]>>((address&3)*8))&((1<<(size*8))-1)

def apply(opwords,registers,psr,words,inputs=None):
 """Small explicit expected-state evaluator for frozen accepted fixture words."""
 read=registers.copy() if inputs is None else inputs
 op=opwords[0];x=opwords[1] if len(opwords)>1 else 0
 if op==0:return psr
 if op==0xe070:registers[x>>12]=int.from_bytes(read[(x>>8)&15].to_bytes(4,'little'),'big');return psr
 if op&0xfff0 in (0xe0a0,0xe0e0,0xe0f0,0xe150):
  d=op&15;a=read[x>>12];b=literal(x)
  if op&0xfff0==0xe150:registers[d]=a^b;return psr
  if op&0xfff0==0xe0a0:a,b=b,a
  registers[d],psr=alu(a,b,(op&0xfff0)!=0xe0e0,psr);return psr
 if op in (0xe1f4,0xe435,0xe434):
  a,b=read[(x>>4)&15],read[(x>>8)&15];mode=x&15;assert mode in (0,1)
  if op==0xe1f4:
   if not b:raise ZeroDivisionError
   if mode:a,b=signed(a),signed(b);v=(abs(a)//abs(b))*(-1 if (a<0)^(b<0) else 1)
   else:v=a//b
  elif op==0xe435:v=a if (signed(a)<=signed(b) if mode else a<=b) else b
  else:v=a if (signed(a)>=signed(b) if mode else a>=b) else b
  registers[x>>12]=v&MASK;return psr
 if op&0xfff0==0xe040:registers[op&15]=(x if x<0x8000 else x|0xffff0000);return psr
 if op&0xff00==0x1600:registers[op&15]=read[(op>>4)&15];return psr
 if op&0xff00==0x1800:registers[op&15],psr=alu(read[op&15],read[(op>>4)&15],False,psr);return psr
 if op&0xff88==0x0680:
  a,b=op&7,(op>>4)&7;write_mem(words,read[b],read[a]&0xffff,2);registers[b]=(read[b]+2)&MASK;return psr
 if op&0xe000==0x6000:
  a,b=op&7,(op>>4)&7;n=(op>>8)&31;n=n-32 if n&16 else n;size=2 if op&8 else 4;at=(read[b]+n*size)&MASK
  if op&128:write_mem(words,at,read[a],size)
  else:registers[a]=read_mem(words,at,size)
  return psr
 raise AssertionError(f'unmodeled fixture operation {opwords}')

def components(row):
 operation=row['operation']; family=row['family']
 if row.get('scalar'):return operation,None
 if row.get('placement')=='tail':head=row['head'];tail=operation
 elif row.get('placement')=='head-already-prefixed':head=operation;tail=row.get('tail',[0])
 elif row.get('conditional') and family=='halfstore':
  head=[0xd67f] if row['span']==4 else [0xf04f,7];tail=operation
 else:head=operation;tail=row.get('tail',[0])
 if family=='halfstore' and not row.get('placement') and not row.get('conditional'):head=[0xf04f,7];tail=operation
 if row.get('conditional') and family=='rev8' and row['span']==8:tail=[0xe043,7]
 if row.get('guard')=='pc-last-byte':
  if family=='halfstore':head=[0xd67f];tail=operation
  elif row['span']==8:tail=[0xe043,7]
 prefix=head[0]
 if prefix>>13 not in (6,7) or prefix&0xf800!=0xf000 and prefix>>13!=6:
  prefix=(prefix|0x1000) if prefix>>13==7 else prefix|0xc000
 return [prefix,*head[1:]],tail

def normalize(head):return [head[0]&0x1fff if head[0]>>13==6 else head[0]&~0x1000,*head[1:]]

def prepare(row,directory):
 directory.mkdir(parents=True,exist_ok=True);guest=Guest()
 memory={INSPECTION+i*4:v for i,v in enumerate([0x12345678,0x89abcdef,0x76543210,0x0badf00d])}
 memory[0x01c7fffc]=0x55aa66bb
 for at,v in memory.items():guest.write(at,v)
 if row.get('guard')=='write':
  for at,v in [(0x01eee240,0xe7),(0x01eee2c0,INSPECTION+1),(0x01eee280,INSPECTION+1),(0x01eee348,1)]:guest.write(at,v)
 psr=row.get('initial_psr',PSR)
 guest.literal(4,psr);guest.emit(0xe064,0x4580)
 guest.literal(4,RETS);guest.emit(0xe064,0x4380);guest.literal(14,STACK,special=True)
 registers=[0x10203040+i*0x01010101 for i in range(16)]
 if row['family']=='halfstore':registers[1],registers[3]=INSPECTION,0x12345678
 for key,value in row.get('initial_overrides',{}).items():registers[int(key)]=value&MASK
 if row.get('conditional'):registers[7]=0 if (row['conditional']['selected']==(row['conditional']['arm']=='then')) else 1
 for reg,val in enumerate(registers):guest.literal(reg,val)
 head,tail=components(row);span=(len(head)+len(tail or []))*2
 if 'span' in row:assert span==row['span'],f"{row['id']}: span{span} !=declared{row['span']}"
 if row.get('guard')=='pc-last-byte':
  # The final14-byte configuration write uses only r0/r1.
  guest.write(0x01eee240,0xe7);guest.write(0x01eee384,ENTRY)
  bundle_pc=guest.pc+14;upper=bundle_pc+span-2
  guest.write(0x01eee380,upper);registers[0],registers[1]=upper,0x01eee380
 before=guest.instructions
 fault_pc=guest.pc
 expected_registers=registers.copy();expected_words=memory.copy();expected_psr=psr
 if row.get('conditional'):
  cond=row['conditional'];n=1 if cond['position']=='final' else 2
  then,otherwise=(n,1) if cond['arm']=='then' else (1,n)
  guest.emit(0xea27,((then-1)<<14)|(otherwise<<12)|1)
  for arm in ('then','else'):
   if arm==cond['arm']:
    guest.emit(*head,*(tail or []))
    if cond['position']=='nonfinal':guest.emit(0)
   else:guest.emit(0xe04d,0x1111)
  skipped=1 if cond['selected'] else n
  if cond['selected']:
   incoming=expected_registers.copy();expected_psr=apply(tail,expected_registers,expected_psr,expected_words) if tail else expected_psr
   expected_psr=apply(normalize(head) if tail else head,expected_registers,expected_psr,expected_words,incoming if tail else None)
  else:expected_registers[13]=0x1111
  # Independent follow-up IF condition based on unchanged predicate r7.
  guest.emit(0xea27,1);guest.emit(0xe04e,0x2222)
  if registers[7]&1:skipped+=1
  else:expected_registers[14]=0x2222
  guest.emit(0);retired=guest.instructions-skipped
 else:
  guest.emit(*head,*(tail or []));guest.emit(0);retired=guest.instructions
  if row['kind']!='model_fault':
   incoming=expected_registers.copy()
   if tail:expected_psr=apply(tail,expected_registers,expected_psr,expected_words)
   expected_psr=apply(normalize(head) if tail else head,expected_registers,expected_psr,expected_words,incoming if tail else None)
  elif row.get('fault_phase')=='head-helper-after-tail-effects':
   expected_psr=apply(tail,expected_registers,expected_psr,expected_words);retired=before
  else:retired=before
 specials=[0]*16;specials[3],specials[5],specials[14]=RETS,expected_psr,STACK
 image=directory/'fixture.bin';image.write_bytes(guest.bytes()+bytes(16))
 fault=row['kind']=='model_fault';pc=fault_pc if fault else guest.pc
 expected=dict(pc=pc,instructions=retired,registers=expected_registers,specials=specials,words={str(k):v for k,v in expected_words.items()})
 record=dict(id=row['id'],image=str(image),sha256=hashlib.sha256(image.read_bytes()).hexdigest(),stop=guest.pc,expected=expected,span=span,head=head,tail=tail,initial_registers=registers,initial_psr=psr,before=before,completion_instructions=guest.instructions,fault_phase=row.get('fault_phase'),spec=row)
 (directory/'prepared.json').write_text(json.dumps(record,indent=2)+'\n');return record

def require(condition,message):
 if not condition:raise AssertionError(message)

def check_snapshot(state,expected,name):
 for field in ('pc','instructions','registers','specials'):require(state[field]==expected[field],f'{name}: {field} differs')

def expected_reason(record):
 row=record['spec'];access=row.get('access')
 if row.get('guard')=='pc-last-byte':return 'guest PC lies outside both configured guard windows'
 if row.get('guard')=='write':return 'CPU write intersects an enabled guest guard window'
 if row.get('fault_phase')=='precheck':return f"unsupported instruction 0x{record['head'][0]:04x}"
 if row.get('fault_phase')=='head-helper-after-tail-effects':return 'divide-by-zero behavior is unsupported'
 address=access['address'];size=access['size']
 if address&(size-1):return 'unaligned access'
 if address==ENTRY:return 'write to read-only XIP (NOR)'
 return f'unmapped access at 0x{address:08x}'

def run(record,qemu,reference,root):
 directory=Path(record['image']).parent;row=record['spec']
 env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
 env.update(FM1_POC_STOP_PC=hex(record['stop']),FM1_POC_MAX_INSTRUCTIONS='100',FM1_POC_STATE_DIR=str(directory))
 command=[str(qemu),'-M','fm1-poc','-accel','tcg,thread=single','-icount','shift=3,align=off,sleep=off','-display','none','-serial','none','-monitor','none','-nodefaults','-kernel',record['image']]
 if row['kind']!='generic_replay':command+=['-append','diag' if row['kind']=='success' else 'alnk-probe']
 diagnostic=row['kind']=='success'
 memory_file=directory/(f"state-{record['stop']:08x}.sram" if diagnostic else 'state.sram')
 for filename in ('state.json','state.sram','state.alnk',f"state-{record['stop']:08x}.sram",f"state-{record['stop']:08x}.ppm"):(directory/filename).unlink(missing_ok=True)
 result=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=15)
 raw=dict(command=command,environment={k:v for k,v in env.items() if k.startswith('FM1_POC_')},returncode=result.returncode,stdout=result.stdout,stderr=result.stderr,
          snapshot_source='stdout' if diagnostic else str(directory/'state.json'),memory_capture=str(memory_file))
 # Preserve process evidence even when a profile fails or emits an invalid
 # snapshot. Diagnostic success captures JSON stdout and PC-suffixed memory;
 # optional generic/fault observers retain fixed state.json/state.sram names.
 (directory/'qemu-run.json').write_text(json.dumps(raw,indent=2)+'\n')
 fault=row['kind']=='model_fault'
 require((result.returncode!=0)==fault,f"{row['id']}: completion/fault differs")
 state=json.loads(result.stdout if diagnostic else (directory/'state.json').read_text())
 raw['state']=state;(directory/'qemu-run.json').write_text(json.dumps(raw,indent=2)+'\n')
 check_snapshot(state,record['expected'],row['id'])
 memory=memory_file.read_bytes();require(len(memory)==0x80000,f"{row['id']}: SRAM length")
 for address,value in record['expected']['words'].items():require(struct.unpack_from('<I',memory,int(address)-0x01c00000)[0]==value,f"{row['id']}: memory/neighbor at{int(address):08x}")
 if fault:
  require(state['reason']==expected_reason(record),f"{row['id']}: fault reason")
  access=row.get('access')
  if row.get('fault_phase')=='head-helper-after-tail-effects' and row['id'].endswith('store'):access=dict(address=INSPECTION,size=4,flags=1)
  if access is None:access=dict(address=state['pc'],size=record['span'],flags=2)
  require(state['last_access']==access,f"{row['id']}: last access/fault order")
 if row['reference_calls']:run_reference(record,reference,root)
 print('PASS',row['id'])

def expected_inspection(words):
 values=[0xA5A5A5A5]*5+[0]*3+[0xA5A5A5A5]*4
 for address,value in words.items():
  if INSPECTION<=int(address)<INSPECTION+48:values[(int(address)-INSPECTION)//4]=value
 return values

def completion_expected(record):
 regs=record['initial_registers'].copy();words={int(k):v for k,v in record['expected']['words'].items()};psr=record['initial_psr'];incoming=regs.copy()
 psr=apply(record['tail'],regs,psr,words) if record['tail'] else psr
 psr=apply(normalize(record['head']) if record['tail'] else record['head'],regs,psr,words,incoming if record['tail'] else None)
 specials=[0]*16;specials[3],specials[5],specials[14]=RETS,psr,STACK
 return dict(pc=record['stop'],instructions=record['completion_instructions'],registers=regs,specials=specials,words={str(k):v for k,v in words.items()})

def run_reference(record,reference,root):
 row=record['spec'];directory=Path(record['image']).parent;cache=directory/'reference-run.json'
 env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
 env.update(FM1_POC_STOP_PC=hex(record['stop']),FM1_POC_MAX_INSTRUCTIONS='100')
 identity=dict(fixture_sha256=record['sha256'],reference_sha256=REFERENCE_SHA,stop=record['stop'],limit=100)
 if cache.exists():
  raw=json.loads(cache.read_text());require(raw.get('identity')==identity,f"{row['id']}: stale copied-reference record")
 else:
  command=[str(reference),'snapshot',record['image']]
  oracle=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=15)
  raw=dict(command=command,identity=identity,returncode=oracle.returncode,stdout=oracle.stdout,stderr=oracle.stderr,reference_fault_state_available=False)
  cache.write_text(json.dumps(raw,indent=2)+'\n')
 fault=row['kind']=='model_fault'
 if not fault:
  require(raw['returncode']==0,f"{row['id']}: copied reference failed")
  other=json.loads(raw['stdout']);check_snapshot(other,record['expected'],row['id']+'/reference')
  require(other['inspection']==expected_inspection(record['expected']['words']),f"{row['id']}: copied-reference owned inspection/neighbor memory")
  raw['comparison_scope']='full sampled completion: PC/count/16GPR/16specials/12inspection words; outside-inspection SRAM unavailable'
 elif raw['returncode']==0:
  # Invalid field/mode outcomes retain raw disagreement without inventing CPU
  # semantics absent from primary evidence. Valid conflict/guard/deferredmode0
  # completions have full sampled state checks against independent calculation.
  if row.get('fault_phase') in ('precheck','full-bundle-fetch-before-effects') and not ('noncanonical' in row['id']):
   expected=completion_expected(record);other=json.loads(raw['stdout'])
   check_snapshot(other,expected,row['id']+'/policy-completion')
   require(other['inspection']==expected_inspection(expected['words']),f"{row['id']}: policy completion inspection")
   raw['comparison_scope']='qualified full sampled policy completion; QEMU precheck intentionally rejects; no hardware fault state'
  else:raw['comparison_scope']='raw reference completion retained; unsupported model fields/faultstate not equated'
 else:
  # Match fatal category and exact bounded fault location/attempt where public
  # CLI output exposes them; unrelated crashes are not accepted as evidence.
  row_phase=row.get('fault_phase');stderr=raw['stderr']
  if row_phase in ('precheck','head-helper-after-tail-effects'):
   needle=f"Unsupported {{ pc: {record['expected']['pc']}, word: {normalize(record['head'])[0]} }}"
   require(needle in stderr,f"{row['id']}: reference unsupported category/PC/normalized word differs")
   raw['fatal_category']='Unsupported'
  else:
   access=row['access'];pc=record['expected']['pc']+len(record['head'])*2
   operation='write' if access['flags'] else 'read'
   require(f'Access {{ pc: {pc},' in stderr and f"address: {access['address']}, size: {access['size']}, operation: \"{operation}\"" in stderr,
           f"{row['id']}: reference access category/tail PC/EA/width/direction differs")
   category=('CPU write protection violation' if row.get('guard')=='write' else 'unaligned access' if access['address']&(access['size']-1) else 'read-only XIP' if access['address']==ENTRY else 'unmapped')
   require(category in stderr,f"{row['id']}: reference access reason category differs")
   raw['fatal_category']='Access';raw['fatal_attempt']=dict(pc=pc,**access,operation=operation,reason_category=category)
  raw['comparison_scope']='fatal category/PC/attempt checked; no CPU fault snapshot; no matched full state claim'
 cache.write_text(json.dumps(raw,indent=2)+'\n')
 return raw

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--matrix',type=Path,default=Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-a-2026-10-08/review/parallel-matrix.json'));ap.add_argument('--output',type=Path,default=Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-a-2026-10-08/research/parallel'));ap.add_argument('--run',action='store_true');ap.add_argument('--reference-only',action='store_true');ap.add_argument('--approved-matrix-sha256');ap.add_argument('--qemu',type=Path,default=ROOT/'qemu-poc/.cache/build/qemu-system-pi32v2');ap.add_argument('--reference',type=Path,default=Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-a-2026-10-08/reference-cli-c96'));ap.add_argument('--family');args=ap.parse_args()
 matrix=json.loads(args.matrix.read_text());digest=hashlib.sha256(args.matrix.read_bytes()).hexdigest()
 rows=[r for r in matrix['rows'] if not args.family or r['family']==args.family]
 if args.run or args.reference_only:
  require(args.approved_matrix_sha256==digest,'No matching coordinator-approved matrix hash')
  require(hashlib.sha256(args.reference.read_bytes()).hexdigest()==REFERENCE_SHA,'Copied reference identity differs')
 prepared=[prepare(row,args.output/row['id']) for row in rows]
 manifest=dict(matrix_sha256=digest,total_cases=len(rows),qemu_budget=sum(r['qemu_calls'] for r in rows),reference_budget=sum(r['reference_calls'] for r in rows),fixtures=[dict(id=r['id'],sha256=r['sha256']) for r in prepared])
 (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print(f'Prepared {len(prepared)} fixtures; no probes launched' if not args.run else f'Executing approved finite{len(prepared)} fixtures')
 if args.reference_only:
  require(not args.run,'Choose --run or --reference-only')
  for record in prepared:
   if record['spec']['reference_calls']:run_reference(record,args.reference,ROOT)
  print('PASS copied-reference finite slice; raw qualified failures preserved')
 if args.run:
  for record in prepared:run(record,args.qemu,args.reference,ROOT)
  (args.output/'validation.json').write_text(json.dumps(dict(passed=True,**manifest),indent=2)+'\n')
if __name__=='__main__':main()
