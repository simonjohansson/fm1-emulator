#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Finite FA00/TBB state gate; offline by default, no reference launches/builds.

Research outcomes must be independently explained/frozen after raw collection.
The unfilled reference-expectation hash intentionally blocks final acceptance.
QEMU collection is parent-only and requires its accepted binary's explicit hash.
Common predicate/call/IRQ/parallel policies are preserved, not hardware claims.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess

MATRIX_SHA='4652c0182fd36b50464349e11f4ee33b3c40ebc94674b5d1c1f1f8849e832ffd'
REFERENCE_SHA='c96ed8b21d73bd2934127b72f1a21f31d82acdb6a0792ec2b130852e478dec94'
REFERENCE_EXPECTATIONS_SHA='f0935b638e5cf0b93a752e9b972147c054656d2bfa590c322b606dfdc194dd58'
DEFAULT=Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-c-2026-10-08/control')
ROOT=Path('/Users/simonjohansson/src/fm1-qemu-poc')

def check(condition,message):
    if not condition:raise SystemExit(message)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def identity(path):
    s=path.stat();return [s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns]

def matrix(evidence):
    path=evidence/'matrix.json';check(sha(path)==MATRIX_SHA,'Frozen matrix changed')
    data=json.loads(path.read_bytes());cases=data['cases']
    check(len(cases)==661 and len({c['name'] for c in cases})==661,'Finite fixture set differs')
    for c in cases:check(sha(Path(c['fixture']))==c['fixture_sha256'],f"{c['name']}: fixture changed")
    return cases

def reference_cases(cases):
    return [c for c in cases if not c.get('no_reference') and not c.get('exact_neighbor')]

def raw_preflight(evidence,cases,kind):
    # Validate ALL receipts/streams/state/memory hashes before parsing guest data.
    records={}
    for c in cases:
        name=c['name'];folder=evidence/kind/name
        check((folder/'launch-started.json').exists() and (folder/'run.json').exists(),f'{kind}/{name}: raw collection incomplete')
        r=json.loads((folder/'run.json').read_bytes())
        check(r['matrix_sha256']==MATRIX_SHA and r['fixture_sha256']==c['fixture_sha256'],f'{kind}/{name}: raw identity differs')
        if kind=='raw':check(r['reference_sha256']==REFERENCE_SHA,f'{name}: reference identity differs')
        check(not r['timed_out'],f'{kind}/{name}: timeout partials retained; collection incomplete')
        for stream in ('stdout','stderr'):
            check(sha(folder/f'{stream}.txt')==r[f'{stream}_sha256'],f'{kind}/{name}: raw stream changed')
        check(int((folder/'returncode.txt').read_text())==r['returncode'],f'{kind}/{name}: return code changed')
        if kind=='qemu':
            for file in ('state.json','state.sram'):
                check(sha(folder/file)==r['saved_files_sha256'][file],f'{name}: saved {file} changed')
        records[name]=r
    return records

def expected(case):
    f=case.get('model_fault')
    want=(f['state'] if f else case['expected']['primary'])
    if case['name']=='displacement--2':
        # Its stop observer equals the operation PC: intercept BEFORE FA00.
        # Keep the original prediction/raw divergence, and do not claim that
        # this fixture executes or validates the self-targeting branch.
        want=dict(want);want['instructions']=case['before_operation_count']
    return want,f

def SRAM(case,cold):
    data=bytearray(0x80000)
    if not cold:
        for start,length in ((0,0xB48),(0x8000,0x14),(0x8020,0x1CE0),(0x7FD80,0x80)):
            data[start:start+length]=bytes([0xA5])*length
    for addr,value in case['expected_owned_words'].items():
        offset=int(addr)-0x01C00000;check(0<=offset<=len(data)-4,f"{case['name']}: owned word outside SRAM")
        struct.pack_into('<I',data,offset,value)
    return bytes(data)

def reference_discovery(evidence,cases,records):
    results=[]
    for c in cases:
        name=c['name'];folder=evidence/'raw'/name;r=records[name]
        output=(folder/'stdout.txt').read_bytes();stderr=(folder/'stderr.txt').read_text(errors='replace')
        row=dict(name=name,returncode=r['returncode'],stdout_sha256=r['stdout_sha256'],stderr_sha256=r['stderr_sha256'])
        fatal=c.get('reference_fatal')
        if r['returncode']:
            row['stderr']=stderr;row['known_fatal']=False
            if fatal:
                row['known_fatal']=(fatal['category'] in stderr and
                    f"Access {{ pc: {fatal['pc']}," in stderr and
                    f"address: {fatal['address']}, size: {fatal['size']}, operation: \"{fatal['operation']}\"" in stderr)
            row['CPU_fault_snapshot_available']=False
        else:
            actual=json.loads(output);prediction=c['expected']['primary']
            row['state']=actual;row['primary_prediction_absent']=prediction is None
            row['differences']={} if prediction is None else {k:dict(predicted=v,actual=actual.get(k)) for k,v in prediction.items() if actual.get(k)!=v}
        results.append(row)
    path=evidence/'reference-discovery.json'
    path.write_text(json.dumps(dict(matrix_sha256=MATRIX_SHA,reference_sha256=REFERENCE_SHA,
        calls=len(cases),semantic_acceptance=False,results=results),indent=2)+'\n')
    return results

def audit_reference(evidence,cases,records):
    check(REFERENCE_EXPECTATIONS_SHA is not None,'Reference outcomes await independent semantic freeze')
    path=evidence/'reference-expectations.json';check(sha(path)==REFERENCE_EXPECTATIONS_SHA,'Reviewed reference expectations changed')
    outcomes=json.loads(path.read_bytes());check(set(outcomes)=={c['name'] for c in cases},'Incomplete reference outcome freeze')
    for c in cases:
        name=c['name'];r=records[name];want=outcomes[name];folder=evidence/'raw'/name
        check(r['returncode']==want['returncode'],f'{name}: reference return code differs')
        if r['returncode']:
            check('fatal' in want,f'{name}: unexplained reference fatal')
            stderr=(folder/'stderr.txt').read_text(errors='replace')
            check(all(text in stderr for text in want['fatal']['required_fragments']),f'{name}: reference fatal differs')
        else:
            actual=json.loads((folder/'stdout.txt').read_bytes())
            for k,v in want['state'].items():check(actual.get(k)==v,f'{name}: retained reference {k} differs')

def collect_qemu(evidence,cases,qemu,qemu_sha):
    pinned=identity(qemu);check(sha(qemu)==qemu_sha,'Accepted QEMU binary hash differs')
    check(identity(qemu)==pinned,'QEMU changed during prehash')
    for c in cases:check(not (evidence/'qemu'/c['name']).exists(),f"{c['name']}: duplicate/partial QEMU run requires review")
    for index,c in enumerate(cases):
        name=c['name'];folder=evidence/'qemu'/name;folder.mkdir(parents=True,exist_ok=False)
        check(identity(qemu)==pinned,f'{name}: QEMU changed before case')
        check(sha(Path(c['fixture']))==c['fixture_sha256'],f'{name}: fixture changed before case')
        _,f=expected(c)
        settings={'FM1_POC_STOP_PC':hex(c['stop_pc']),'FM1_POC_MAX_INSTRUCTIONS':str(c['limit']),'FM1_POC_STATE_DIR':str(folder)}
        env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')};env.update(settings)
        command=[str(qemu),'-M','fm1-poc','-accel','tcg,thread=single','-icount','shift=3,align=off,sleep=off',
            '-display','none','-serial','none','-monitor','none','-nodefaults','-kernel',c['fixture'],'-append','alnk-probe']
        r=dict(command=command,environment=settings,qemu_identity=pinned,qemu_sha256=qemu_sha,
            matrix_sha256=MATRIX_SHA,fixture_sha256=c['fixture_sha256'],modeled_fault=bool(f))
        with (folder/'launch-started.json').open('x') as file:json.dump(r,file)
        timed_out=False
        try:
            result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,timeout=15)
            stdout,stderr,returncode=result.stdout,result.stderr,result.returncode
        except subprocess.TimeoutExpired as error:
            stdout,stderr,returncode=error.stdout or b'',error.stderr or b'',124;timed_out=True
        (folder/'stdout.txt').write_bytes(stdout);(folder/'stderr.txt').write_bytes(stderr)
        (folder/'returncode.txt').write_text(str(returncode)+'\n')
        r.update(returncode=returncode,timed_out=timed_out,stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr).hexdigest(),saved_files_sha256={x:sha(folder/x) for x in ('state.json','state.sram','state.alnk') if (folder/x).exists()})
        (folder/'run.json').write_text(json.dumps(r,indent=2)+'\n')
        check(identity(qemu)==pinned,f'{name}: QEMU changed; raw result retained')
        check(not timed_out,f'{name}: timeout partials retained; no state parsing')
        if (index+1)%50==0:print(f'Collected {index+1}/661 QEMU raw receipts',flush=True)

def audit_qemu(evidence,cases,records,qemu_sha=None):
    for c in cases:
        name=c['name'];folder=evidence/'qemu'/name;r=records[name]
        if qemu_sha:check(r['qemu_sha256']==qemu_sha,f'{name}: QEMU hash differs across cases')
        actual=json.loads((folder/'state.json').read_bytes());want,f=expected(c)
        check(want is not None,f'{name}: missing independent model expectation')
        for k,v in want.items():
            if k!='inspection':check(actual.get(k)==v,f'{name}: full-state {k} differs: wanted {v}, got {actual.get(k)}')
        memory=(folder/'state.sram').read_bytes()
        check(memory==SRAM(c,True),f'{name}: full cold owned SRAM and all neighbor bytes differ')
        if f:
            check(r['returncode']!=0 and actual['reason']==f['reason'],f'{name}: fault category/retirement differs')
            check(actual['last_access']==f['last_access'],f'{name}: exact fetch/load fault stage differs')
            if 'guard windows' in f['reason']:check(actual['guards']['debug_message']&(1<<12),f'{name}: PC guard not latched')
        else:
            check(r['returncode']==0,f'{name}: unexpected QEMU failure')
            check(actual['reason']=='checkpoint reached',f'{name}: positive capture reason differs')
            check((folder/'stdout.txt').read_bytes()==b'',f'{name}: unexpected cold-capture stdout')
            if name=='displacement--2':access=dict(address=0x020001C4,size=4,flags=2)
            elif c['group']=='mask':access=dict(address=c['operation_pc'],size=4,flags=2)
            elif c['group']=='table':access=dict(address=c['table_EA'],size=1,flags=0)
            else:access=dict(address=c['stop_pc']-2,size=2,flags=2)
            check(actual['last_access']==access,f'{name}: successful final fetch/read address/extent differs')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',type=Path,default=DEFAULT)
    parser.add_argument('--discover-reference',action='store_true');parser.add_argument('--audit-reference',action='store_true')
    parser.add_argument('--run-qemu',action='store_true');parser.add_argument('--audit-qemu',action='store_true')
    parser.add_argument('--qemu',type=Path);parser.add_argument('--qemu-sha256')
    args=parser.parse_args();cases=matrix(args.evidence);refs=reference_cases(cases)
    check(len(refs)==657,'Finite reference call count differs')
    if args.discover_reference or args.audit_reference or args.run_qemu:
        records=raw_preflight(args.evidence,refs,'raw')
        if args.discover_reference:reference_discovery(args.evidence,refs,records)
        if args.audit_reference or args.run_qemu:audit_reference(args.evidence,refs,records)
    if args.run_qemu:
        check(args.qemu is not None and args.qemu_sha256 is not None,'Accepted QEMU path/hash must be supplied explicitly')
        collect_qemu(args.evidence,cases,args.qemu,args.qemu_sha256)
    if args.audit_qemu or args.run_qemu:
        check(args.qemu_sha256 is not None,'Expected accepted QEMU hash required for cached audit')
        records=raw_preflight(args.evidence,cases,'qemu');audit_qemu(args.evidence,cases,records,args.qemu_sha256)
    print(json.dumps(dict(matrix_sha256=MATRIX_SHA,finite_cases=661,reference_calls=657,
        fresh_reference_calls=0,fresh_qemu_calls=661 if args.run_qemu else 0,
        model_cases_validated=661 if args.audit_qemu or args.run_qemu else 0,
        reference_outcomes_frozen=REFERENCE_EXPECTATIONS_SHA is not None,
        hardware_validation=False),indent=2))

if __name__=='__main__':main()
