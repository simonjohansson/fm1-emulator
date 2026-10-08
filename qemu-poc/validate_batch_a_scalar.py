#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Focused batch A gate using frozen fixtures/cached separate CLI evidence.

Default is offline complete-state audit. --run-qemu is for parent serialized
execution only. No reference process, build, firmware source or Cargo calls.
04C8 remains unsupported; successful skipped cases are not push validation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import sys

ENTRY=0x02000120
INSPECTION=0x01C08000
MATRIX_SHA='cf1f588db7b7b7e0379ad1aa5bbd24199f5b15ff68971953bde3797899113b52'
REFERENCE_SHA='c96ed8b21d73bd2934127b72f1a21f31d82acdb6a0792ec2b130852e478dec94'
ROOT=Path('/Users/simonjohansson/src/fm1-qemu-poc')
DEFAULT_EVIDENCE=Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-a-2026-10-08/research/scalar')

MARKERS=[0x12345678,0x89ABCDEF,0x76543210,0x0BADF00D]
PSR=0x89ABCDE5
RETS=0x12345678
GPRS=[0x10203040+i*0x01010101 for i in range(16)]
GPRS[0]=0x01C7FE08

class Guest:
    def __init__(self):
        self.words=[]
        self.instructions=0
    @property
    def pc(self):
        return ENTRY+2*len(self.words)
    def emit(self,*words):
        assert all(0<=word<=0xFFFF for word in words)
        self.words.extend(words)
        self.instructions+=1
    def literal(self,reg,value,special=False):
        value&=0xFFFFFFFF
        self.emit((0xFFE0 if special else 0xFFC0)|reg,value&0xFFFF,value>>16)
    def write(self,address,value):
        self.literal(0,value)
        self.literal(1,address)
        self.emit(0x6090)
    def bytes(self):
        return struct.pack('<'+'H'*len(self.words),*self.words)+bytes(16)

def signed(value):
    return value-0x100000000 if value&0x80000000 else value

def packed(x):
    mode=(x>>10)&3
    return ((x&255)*(1,0x00010001,0x01000100,0x01010101)[(x>>8)&3] if not mode else
            ((0x80|(x&127))<<(32-mode*8))>>((x>>7)&7))

def alu(a,b,psr,operation):
    carry=(psr>>1)&1
    subtract=operation in ('sub','sbc')
    extra=1-carry if operation=='sbc' else carry if operation=='adc' else 0
    wide=a-b-extra if subtract else a+b+extra
    r=wide&0xFFFFFFFF
    mathematical=signed(a)-signed(b)-extra if subtract else signed(a)+signed(b)+extra
    overflow=mathematical < -0x80000000 or mathematical > 0x7FFFFFFF
    out_carry=a>=b+extra if subtract else wide>0xFFFFFFFF
    flags=int(overflow)|(int(out_carry)<<1)|(int(r==0)<<2)|((r>>31)<<3)
    return r,(psr&0xFFFFFFF0)|flags

def selected(case):
    if case.get('selected') is False:return False
    if 'arm' not in case:return True
    return (case['condition']==0)==(case['arm']=='then')

def outcome(meta):
    c=meta['case'];form=c['form'];words=meta['operation_words']
    if c.get('stack_guard_fault'):return 'stack-guard'
    if c.get('group')=='fetch-protection':return 'pc-guard'
    if not selected(c):return 'success'
    if form=='compact-push-rets':return 'unsupported'
    if form=='compact-register-asr-1a88' and c.get('role')=='tail':return 'unsupported'
    if form.startswith('register-') and (words[1]&15) not in (0,2):return 'unsupported'
    if form=='subtract-packed-e0f0' and words[0]&0xFFF0!=0xE0F0:return 'unsupported'
    if form=='special-sp-immediate-add-e8f0' and words[1]&0xE003:return 'unsupported'
    return 'success'

def expected(meta):
    c=meta['case'];form=c['form'];words=meta['operation_words']
    regs=list(meta['incoming_registers']);psr=meta['incoming_psr'];stack=meta['incoming_stack']
    rets=meta['incoming_rets'];count=meta['fixture_instruction_count']
    kind=outcome(meta)
    if kind!='success':
        count=meta['before_operation_count']+(1 if 'arm' in c else 0)
        final_pc=meta['operation_pc']
        if kind=='stack-guard':stack=meta['candidate']['new_stack']
    else:
        final_pc=meta['stop_pc']
        if 'arm' in c:
            form_count=1 if c['position']=='final' else 2
            count-=1 if selected(c) else form_count
            if not selected(c):regs[12]=0x1111
            regs[14]=0x2222
        elif c.get('selected') is False:
            count-=1
            regs[14]=0x2222
        if selected(c):
            if form=='subtract-packed-e0f0':
                dest=words[0]&15;source=words[1]>>12
                regs[dest],psr=alu(regs[source],packed(words[1]),psr,'sub')
            elif form=='compact-register-asr-1a88':
                dest=words[0]&7;count_reg=(words[0]>>4)&7
                regs[dest]=(signed(regs[dest])>>min(regs[count_reg],31))&0xFFFFFFFF
            elif form.startswith('register-'):
                x=words[1];dest=x>>12;left=(x>>4)&15;right=(x>>8)&15
                operation='sbc' if x&15==2 else 'adc'
                regs[dest],psr=alu(regs[left],regs[right],psr,operation)
                if c.get('second_instruction_same_form'):
                    regs[10]=psr
                    regs[dest],psr=alu(regs[left],regs[right],psr,operation)
            elif form=='special-sp-immediate-add-e8f0':
                x=words[1]&0x1FFF;delta=x-0x2000 if x&0x1000 else x
                stack=(stack+delta)&0xFFFFFFFF
    specials=[0]*16;specials[3]=rets;specials[5]=psr;specials[14]=stack
    inspection=[0x12345678,0x89ABCDEF,0x76543210,0x0BADF00D,0xA5A5A5A5,0,0,0]+[0xA5A5A5A5]*4
    return dict(pc=final_pc,instructions=count,registers=regs,specials=specials,inspection=inspection)

def equal(actual,want,label):
    for field,value in want.items():
        if actual.get(field)!=value:
            raise AssertionError(f'{label}: {field}: actual={actual.get(field)} expected={value}')

def load_records(evidence):
    summary=json.loads((evidence/'summary.json').read_text())
    records=[]
    for row in summary['results']:
        directory=evidence/row['case_id']
        meta=json.loads((directory/'fixture.json').read_text())
        image=directory/'fixture.bin'
        assert hashlib.sha256(image.read_bytes()).hexdigest()==meta['fixture_sha256']==row['fixture_sha256']
        assert row['reference_sha256']==REFERENCE_SHA
        assert meta['fixture_instruction_count']<=100
        records.append((image,meta,row))
    return records

def offline_audit(records):
    counts={'positive':0,'fault':0,'raw_reference_fatal':0,'policy_difference':0}
    for image,meta,row in records:
        label=meta['case']['id'];want=expected(meta)
        if row.get('qemu_only'):
            counts['positive' if outcome(meta)=='success' else 'fault']+=1
            continue
        if outcome(meta)=='success':
            assert row.get('returncode')==0,(label,'reference did not complete')
            equal(row['state'],want,label+'/independent-cached-reference')
            counts['positive']+=1
        else:
            counts['fault']+=1
            if row.get('returncode')!=0:
                fatal=f"Unsupported {{ pc: {meta['operation_pc']}, word: {meta['operation_words'][0]} }}"
                assert fatal in row.get('stderr',''),(label,'unexpected reference fatal PC/word/category')
                counts['raw_reference_fatal']+=1
            else:
                completion=json.loads(json.dumps(meta))
                case=completion['case']
                if case.get('group')=='fetch-protection':case['group']='qualified-reference-PC-policy'
                if case.get('role')=='tail':
                    completion['operation_words']=[completion['operation_words'][-1]]
                    case.pop('role')
                assert outcome(completion)=='success',label
                equal(row['state'],expected(completion),label+'/qualified-full-reference-policy')
                counts['policy_difference']+=1
    return counts

def run_case(command,root,out,image,meta,row,generic=False):
    label=meta['case']['id'];kind=outcome(meta)
    directory=out/('generic' if generic else 'focused')/label;directory.mkdir(parents=True,exist_ok=True)
    for filename in ('state.json','state.sram','state.alnk'):(directory/filename).unlink(missing_ok=True)
    env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
    settings={'FM1_POC_STOP_PC':hex(meta['stop_pc']),'FM1_POC_MAX_INSTRUCTIONS':'100'}
    if kind!='success' or generic:settings['FM1_POC_STATE_DIR']=str(directory)
    env.update(settings)
    cmd=[*command,'-kernel',str(image)]
    if not generic:cmd+=['-append','diag' if kind=='success' else 'alnk-probe']
    result=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True,timeout=15)
    record=dict(command=cmd,environment=settings,fixture_sha256=meta['fixture_sha256'],reference_sha256=REFERENCE_SHA,
        cached_reference_returncode=row.get('returncode'),returncode=result.returncode,stdout=result.stdout,stderr=result.stderr,
        hardware_fault_state_validation=False,reference_fault_state_available=False)
    if kind=='success':
        assert result.returncode==0,(label,result.stderr)
        state=json.loads((directory/'state.json').read_text() if generic else result.stdout)
        want=expected(meta)
        if generic:
            want.pop('inspection')
            equal(state,want,label+'/independent-generic')
            memory=(directory/'state.sram').read_bytes()
            assert len(memory)==0x80000
            assert struct.unpack_from('<IIII',memory,0x8000)==tuple(expected(meta)['inspection'][:4])
        else:equal(state,want,label+'/independent')
        if not row.get('qemu_only'):
            for field in ('pc','instructions','registers','specials'):
                assert state[field]==row['state'][field],(label,'cached-reference',field)
            if not generic:assert state['inspection']==row['state']['inspection']
    else:
        assert result.returncode!=0,(label,'expected explicit model fault')
        state=json.loads((directory/'state.json').read_text())
        want=expected(meta);want.pop('inspection')
        equal(state,want,label+'/model-fault')
        op=meta['operation_words'][0]
        reason=('guest stack pointer lies outside its configured guard window' if kind=='stack-guard' else
                'guest PC lies outside both configured guard windows' if kind=='pc-guard'
                else f'unsupported instruction 0x{op:04x}')
        assert state['reason']==reason,(label,state['reason'],reason)
        access=(dict(address=meta['candidate']['new_stack'],size=0,flags=4) if kind=='stack-guard' else
                dict(address=meta['operation_pc'],size=len(meta['operation_words'])*2,flags=2))
        assert state['last_access']==access
        memory=(directory/'state.sram').read_bytes()
        assert len(memory)==0x80000 and memory[0x8000:0x8010]==struct.pack('<IIII',0x12345678,0x89ABCDEF,0x76543210,0x0BADF00D)
        if kind=='pc-guard':assert state['guards']['debug_message']==1<<12
        if kind=='stack-guard':assert state['guards']['emu_message']==8
    record['state']=state
    (directory/'run.json').write_text(json.dumps(record,indent=2)+'\n')
    return kind

def stack_guard_records(output):
    """Three explicit parent-approved QEMU-only EMU window discriminators."""
    records=[];stack=0x01C08040
    for name,delta,skip in (('window-selected-minus8',-8,False),('window-boundary-minus4',-4,False),('window-skipped-minus8',-8,True)):
        guest=Guest()
        for i,marker in enumerate(MARKERS):guest.write(INSPECTION+i*4,marker)
        guest.literal(4,PSR);guest.emit(0xE064,0x4580)
        guest.literal(4,RETS);guest.emit(0xE064,0x4380)
        guest.literal(14,stack,special=True)
        guest.write(0x01EEF0E0,stack+4);guest.write(0x01EEF0E4,stack-4)
        guest.write(0x01EEF0D0,8)
        regs=list(GPRS)
        if skip:regs[4]=1;regs[6]=0
        for reg,value in enumerate(regs):guest.literal(reg,value)
        before=guest.instructions
        if skip:guest.emit(0xEA24,1)
        op_pc=guest.pc;words=[0xE8F0,delta&0x1FFF]
        guest.emit(*words)
        if skip:guest.emit(0xEA26,1);guest.emit(0xE04E,0x2222)
        guest.emit(0)
        case=dict(id='special-sp-immediate-add-e8f0/'+name,form='special-sp-immediate-add-e8f0',
            group='actual-emu-stack-window',delta=delta,stack_guard_fault=not skip and delta==-8)
        if skip:case['selected']=False
        data=guest.bytes();directory=output/'model-fixtures'/case['id'];directory.mkdir(parents=True,exist_ok=True)
        image=directory/'fixture.bin';image.write_bytes(data)
        meta=dict(case=case,operation_words=words,operation_pc=op_pc,before_operation_count=before,
            stop_pc=guest.pc,fixture_instruction_count=guest.instructions,incoming_registers=regs,
            incoming_psr=PSR,incoming_rets=RETS,incoming_stack=stack,
            candidate=dict(new_stack=(stack+delta)&0xFFFFFFFF),fixture_sha256=hashlib.sha256(data).hexdigest())
        assert guest.instructions<=100
        (directory/'fixture.json').write_text(json.dumps(meta,indent=2)+'\n')
        records.append((image,meta,dict(qemu_only=True,fixture_sha256=meta['fixture_sha256'],reference_calls=0)))
    return records

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--evidence',type=Path,default=DEFAULT_EVIDENCE)
    parser.add_argument('--matrix',type=Path,help='Optional relocated frozen scalar matrix; SHA must match approved matrix.')
    parser.add_argument('--supplement',type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'qemu-validation')
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--qemu',type=Path)
    parser.add_argument('--run-qemu',action='store_true')
    parser.add_argument('--stack-window-cases',action='store_true')
    args=parser.parse_args()
    if args.matrix:assert hashlib.sha256(args.matrix.read_bytes()).hexdigest()==MATRIX_SHA
    records=load_records(args.evidence)
    assert len(records)==475
    assert json.loads((args.evidence/'summary.json').read_text())['matrix_sha256']==MATRIX_SHA
    if args.supplement:
        supplement=json.loads((args.supplement/"summary.json").read_text())
        assert supplement["matrix_sha256"]=="22190e1384d2b0de7fac7f8a307d71fa204e9ee92df11a7b0d797cac4aca1b15"
        assert len(supplement["results"])==5
        records+=load_records(args.supplement)
    if args.stack_window_cases:records+=stack_guard_records(args.output)
    counts=offline_audit(records)
    if args.run_qemu:
        sys.path.insert(0,str(args.root/'qemu-poc'))
        import validate
        command=list(validate.COMMAND)
        if args.qemu:command[0]=str(args.qemu)
        args.output.mkdir(parents=True,exist_ok=True)
        replays={}
        for image,meta,row in records:
            kind=run_case(command,args.root,args.output,image,meta,row)
            if kind=='success' and selected(meta['case']) and meta['case']['form'] not in replays:
                replays[meta['case']['form']]=(image,meta,row)
        for image,meta,row in replays.values():
            # Generic loader has different initial SRAM. All four inspection
            # marker words are explicit guest writes; compare only these and
            # CPU state for generic replay, leaving loader poison out of scope.
            run_case(command,args.root,args.output,image,meta,row,generic=True)
        counts['generic_replays']=len(replays)
    counts.update(reference_calls=0,qemu_executed=args.run_qemu,retained_deferred_opcode='04C8',
        hardware_validation=False,full_state_expected_independently=True,
        focused_case_count=len(records),maximum_qemu_calls=len(records)+5,
        configured_stack_guard_cases=3 if args.stack_window_cases else 0,
        stack_check_scope='Configured EMU stack window only; no generic mapped or alignment SP validation',
        retained_original_sp_write_guard_case='No data write, hence no write-guard fault; not an EMU stack-window test')
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'summary.json').write_text(json.dumps(counts,indent=2)+'\n')
    print(json.dumps(counts,indent=2))

if __name__=='__main__':main()
