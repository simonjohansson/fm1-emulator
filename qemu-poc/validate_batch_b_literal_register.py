#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Seven exact Batch B branches: cached full-state audit by default.

--run-qemu is reserved for the parent's serialized accepted build. No reference
process/build/source is invoked. Signed12 FF0B/FF0D and C-field FF4A are evidenced
supplemental resolutions of retained primary contradictions. Four-byte reference
IF scanning, final THEN marker/count differences, exit predicate clearing and
guard differences remain explicit; hardware conditional/fault/IRQ behavior is
unverified. Existing FF0C/FF41 policies are unchanged.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

MATRIX_SHA='467401391dee03e804d220d7985e795a24d8f62dc1c4e2210de98a6aac894670'
REFERENCE_SHA='c96ed8b21d73bd2934127b72f1a21f31d82acdb6a0792ec2b130852e478dec94'
DEFAULT=Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-b-2026-10-08/literal-register')
ROOT=Path('/Users/simonjohansson/src/fm1-qemu-poc')
MARKERS=[0x12345678,0x89ABCDEF,0x76543210]

def check(test,message):
    if not test:
        raise SystemExit(message)

def chosen(case):
    return ('signed12-candidate' if case['opcode'] in ('ff0b','ff0d') else
            'vendor-C-candidate' if case['opcode']=='ff4a' else 'primary')

def expectation(case):
    expected=case['expected'][chosen(case)]
    if case['group']=='conditional':
        return expected['state'],expected['fault']
    fault=case.get('model_fault')
    return (fault['state'],fault) if fault else (expected,None)

def reference_expectation(case):
    """Retained scanner/guard discrepancies, independent of cached match labels."""
    result=case['expected'][chosen(case)]
    if case['group']!='conditional':
        result=dict(result)
        if case['name'].endswith('-guard-target'):
            result.update(pc=case['stop_pc'],instructions=result['instructions']+1)
        return result
    result=dict(result['state'])
    result['registers']=list(result['registers'])
    if case['outside']:
        result.update(pc=case['stop_pc'],
                      instructions=result['instructions']+(3 if case['condition']==0 else 2))
        if case['condition']==0:
            result['registers'][9]=0x9999
    elif case['side']=='then':
        if not case['has_else']:
            result['instructions']+=int(case['condition']==1)
        elif case['position']=='final':
            result['instructions']+=1
            if case['condition']==0:
                result['registers'][7]=0x7777
        elif case['condition']==0:
            result['registers'][6]=case['incoming_registers'][6]
            result['registers'][7]=0x7777
        else:
            result['registers'][6]=0x6666
            result['instructions']+=1
    elif case['condition']==0:
        result['instructions']+=1
        if case['position']=='nonfinal':
            result['registers'][7]=0x7777
    return result

def audit_case(case,evidence):
    name=case['name']
    folder=evidence/'raw'/name
    record=json.loads((folder/'run.json').read_text())
    check(record['matrix_sha256']==MATRIX_SHA and record['reference_sha256']==REFERENCE_SHA,
          f'{name}: reference identity differs')
    stdout=(folder/'stdout.txt').read_bytes()
    stderr=(folder/'stderr.txt').read_bytes()
    check(hashlib.sha256(stdout).hexdigest()==record['stdout_sha256'] and
          hashlib.sha256(stderr).hexdigest()==record['stderr_sha256'],f'{name}: cached raw differs')
    check(hashlib.sha256(Path(case['fixture']).read_bytes()).hexdigest()==case['fixture_sha256'],
          f'{name}: fixture differs')
    if record['returncode']:
        check(case['group']=='conditional' and name.endswith('-d4') and
              f"Unsupported {{ pc: {case['operation_pc']+4}, word: 4 }}" in stderr.decode(),
              f'{name}: unexplained reference fatal result')
    else:
        check(record['state']==json.loads(stdout),f'{name}: parsed state differs from raw')
        expected=reference_expectation(case)
        for field,value in expected.items():
            check(record['state'].get(field)==value,
                  f'{name}: raw reference {field} differs from independent retained expectation')
    return record

def identity(path):
    info=path.stat()
    return (info.st_size,info.st_mtime_ns,info.st_ino)

def run_case(case,evidence,qemu,qemu_sha,qemu_identity):
    name=case['name']
    folder=evidence/'qemu'/name
    folder.mkdir(parents=True,exist_ok=True)
    expected,fault=expectation(case)
    settings={'FM1_POC_STOP_PC':hex(case['stop_pc']),'FM1_POC_MAX_INSTRUCTIONS':str(case['limit']),
              'FM1_POC_STATE_DIR':str(folder)}
    env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
    env.update(settings)
    command=[str(qemu),'-M','fm1-poc','-accel','tcg,thread=single',
             '-icount','shift=3,align=off,sleep=off','-display','none','-serial','none',
             '-monitor','none','-nodefaults','-kernel',case['fixture'],
             '-append','alnk-probe' if fault else 'diag']
    check(not (folder/'launch-started.json').exists(),f'{name}: earlier run requires audit before repeat')
    check(identity(qemu)==qemu_identity,f'{name}: QEMU changed before launch')
    receipt={'command':command,'environment':settings,'qemu_sha256':qemu_sha,
             'fixture_sha256':case['fixture_sha256'],'matrix_sha256':MATRIX_SHA,
             'modeled_fault':bool(fault),'qemu_identity':qemu_identity}
    (folder/'launch-started.json').write_text(json.dumps(receipt)+'\n')
    timed_out=False
    try:
        result=subprocess.run(command,env=env,cwd=ROOT,capture_output=True,timeout=15)
        stdout,stderr,returncode=result.stdout,result.stderr,result.returncode
    except subprocess.TimeoutExpired as error:
        stdout,stderr,returncode=error.stdout or b'',error.stderr or b'',124
        timed_out=True
    (folder/'stdout.txt').write_bytes(stdout)
    (folder/'stderr.txt').write_bytes(stderr)
    (folder/'returncode.txt').write_text(str(returncode)+'\n')
    receipt.update(returncode=returncode,timed_out=timed_out,
                   stdout_sha256=hashlib.sha256(stdout).hexdigest(),
                   stderr_sha256=hashlib.sha256(stderr).hexdigest())
    (folder/'run.json').write_text(json.dumps(receipt,indent=2)+'\n')
    check(not timed_out,f'{name}: timeout; partial raw outputs retained')
    check(identity(qemu)==qemu_identity,f'{name}: QEMU changed during launch')
    actual=json.loads((folder/'state.json').read_text()) if fault else json.loads(stdout)
    for field,value in expected.items():
        if fault and field=='inspection':
            continue
        check(actual.get(field)==value,f'{name}: {field} differs: expected {value}, got {actual.get(field)}')
    if fault:
        check(returncode!=0 and actual['reason']==fault['reason'],f'{name}: modeled fault differs')
        check(actual['last_access']=={'address':fault['pc'],'size':fault['size'],'flags':2},
              f'{name}: exact fetch span/stage differs')
        memory=(folder/'state.sram').read_bytes()
        check(len(memory)==0x80000 and memory[0x7FF8:0x8030]==bytes(8)+struct.pack('<III',*MARKERS)+bytes(36),
              f'{name}: owned memory or adjacent bytes differ at fault')
        if 'guard windows' in fault['reason']:
            check(actual['guards']['debug_message']&(1<<12),f'{name}: PC guard is not latched')
    else:
        check(returncode==0,f'{name}: unexpected fault: {stderr.decode()}')
    receipt['state']=actual
    (folder/'run.json').write_text(json.dumps(receipt,indent=2)+'\n')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--evidence',type=Path,default=DEFAULT)
    parser.add_argument('--run-qemu',action='store_true')
    parser.add_argument('--qemu',type=Path,default=ROOT/'qemu-poc/.cache/build/qemu-system-pi32v2')
    args=parser.parse_args()
    raw=(args.evidence/'matrix.json').read_bytes()
    check(hashlib.sha256(raw).hexdigest()==MATRIX_SHA,'Frozen research matrix differs')
    matrix=json.loads(raw)
    qemu_identity=identity(args.qemu) if args.run_qemu else None
    qemu_sha=hashlib.sha256(args.qemu.read_bytes()).hexdigest() if args.run_qemu else None
    if args.run_qemu:
        check(identity(args.qemu)==qemu_identity,'QEMU changed while pinning build hash')
    positives=faults=reference_fatals=0
    for index,case in enumerate(matrix['cases']):
        record=audit_case(case,args.evidence)
        reference_fatals+=bool(record['returncode'])
        expected,fault=expectation(case)
        faults+=bool(fault)
        positives+=not bool(fault)
        if args.run_qemu:
            run_case(case,args.evidence,args.qemu,qemu_sha,qemu_identity)
        if args.run_qemu and (index+1)%50==0:
            print(f'Validated {index+1}/888 exact branch cases',flush=True)
    summary={'passed':True,'matrix_sha256':MATRIX_SHA,'reference_sha256':REFERENCE_SHA,
             'cached_reference_records':len(matrix['cases']),'reference_fatals':reference_fatals,
             'independent_model_positives':positives,'model_faults':faults,
             'qemu_cases':len(matrix['cases']) if args.run_qemu else 0,
             'fresh_reference_calls':0,'hardware_conditional_fault_irq_validation':False}
    if args.run_qemu:
        summary['qemu_sha256']=qemu_sha
    (args.evidence/'validation.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    main()
