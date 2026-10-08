#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline full fixture audit, or parent-only serialized QEMU focused gate.

No reference launches, Cargo, builds, source generation or firmware imports.
Raw separate CLI contradictions remain under reference/, and do not become
architectural expectations. Every QEMU case checks an independently modeled
full CPU state, retirement and owned memory; faults check exact access span.
"""
import argparse,hashlib,json,os,struct,subprocess
from pathlib import Path
DEFAULT_EVIDENCE=Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-b-2026-10-08/packed')
ROOT=Path('/Users/simonjohansson/src/fm1-qemu-poc')
DEFAULT_QEMU=ROOT/'qemu-poc/.cache/build/qemu-system-pi32v2'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def equal(actual,want,label):
    for k,v in want.items():
        assert actual.get(k)==v,(label,k,actual.get(k),v)

def qualify(meta,raw,state,stderr):
    c=meta['case'];g=c['group'];want=json.loads(json.dumps(meta['expected']))
    assert not raw.get('timeout')
    if raw['returncode']!=0:
        assert raw['returncode']==101 and g=='conditional' and c['displacement']==4
        assert (c['arm']=='then' and c['condition']==1 and c['position']=='final') or (c['arm']=='else' and c['condition']==0 and c['position']=='final')
        fatal=f'Unsupported {{ pc: {meta["operation_pc"]+4}, word: 4 }}'
        assert fatal in stderr and state is None,(c['id'],fatal,stderr)
        return 'fatal-third-word-four',None
    assert state is not None
    category='full-model-state-match'
    if g in ('classifier-policy','neighbor-policy'):
        want.pop('reason');want.pop('last_access');want['pc']=meta['stop_pc'];want['instructions']+=1
        category='reference-valid-model-rejected-'+g
    elif c.get('outside'):
        want.pop('reason');want.pop('last_access');want['pc']=meta['stop_pc'];want['instructions']+=3 if c['condition']==0 else 2
        if c['condition']==0:want['registers'][9]=0x9999
        category='reference-completes-inherited-predicate-exit'
    elif g=='conditional' and c['has_else']:
        if c['arm']=='then' and c['condition']==0:
            want['registers'][7]=0x7777
            if c['position']=='final':want['instructions']+=1
            else:want['registers'][6]=meta['incoming_registers'][6]
            category='reference-four-byte-scanner-then-'+c['position']
        elif c['arm']=='then' and c['condition']==1 and c['position']=='nonfinal':
            want['registers'][6]=0x6666;want['instructions']+=1;category='reference-four-byte-scanner-skipped-nonfinal-then'
        elif c['arm']=='else' and c['condition']==0 and c['position']=='nonfinal':
            want['registers'][7]=0x7777;want['instructions']+=1;category='reference-four-byte-scanner-skipped-nonfinal-else'
    for field,v in want.items():assert state.get(field)==v,(c['id'],category,field,state.get(field),v)
    return category,want

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-qemu',action='store_true')
    p.add_argument('--approved-matrix-sha256');p.add_argument('--qemu',type=Path,default=DEFAULT_QEMU)
    p.add_argument('--evidence',type=Path,default=DEFAULT_EVIDENCE);p.add_argument('--output',type=Path);p.add_argument('--case-id')
    args=p.parse_args();evidence=args.evidence;args.output=args.output or evidence/'qemu'
    matrix=json.loads((evidence/'matrix.json').read_text());msha=sha(evidence/'matrix.json')
    if args.run_qemu and args.approved_matrix_sha256!=msha:p.error('Parent approved frozen matrix SHA required.')
    if args.run_qemu:
        for row in matrix['cases']:
            if row['reference_calls']:
                assert (evidence/'reference'/row['id']/'raw.json').exists(),('Acceptance requires every approved cached CLI call',row['id'])
                assert (evidence/'reference'/row['id']/'parsed.json').exists(),('Acceptance requires cached CLI parse',row['id'])
        qemu_sha=sha(args.qemu);qemu_identity=(args.qemu.stat().st_size,args.qemu.stat().st_mtime_ns)
    counts=dict(fixtures=0,reference_full_state_matches=0,reference_state_disagreements=0,reference_fatal_or_timeout=0,reference_not_yet_run=0,qemu_success=0,qemu_fault=0)
    rows=[]
    for row in matrix['cases']:
        if args.case_id and row['id']!=args.case_id:continue
        d=evidence/'fixtures'/row['id'];meta=json.loads((d/'fixture.json').read_text());image=d/'fixture.bin'
        assert sha(image)==row['fixture_sha256']==meta['fixture_sha256'];assert sha(d/'fixture.json')==row['metadata_sha256']
        want=meta['expected'];counts['fixtures']+=1
        assert len(want['registers'])==len(want['specials'])==16 and len(want['inspection'])==12
        assert all(0<=v<=0xFFFFFFFF for v in want['registers']+want['specials'])
        assert want['specials'][3]==0x12345678 and want['specials'][5]==meta['case'].get('psr',0x89ABCDE5)
        rawref=evidence/'reference'/row['id']/'raw.json';parsedref=rawref.with_name('parsed.json')
        rlabel='not-run'
        if row['reference_calls']:
            if not rawref.exists():counts['reference_not_yet_run']+=1
            else:
                receipt=json.loads(rawref.read_text());assert receipt['matrix_sha256']==msha
                assert receipt['reference_sha256']==matrix['reference_sha256'] and receipt['fixture_sha256']==row['fixture_sha256']
                assert sha(rawref.with_name('stdout.bin'))==receipt['stdout_sha256'];assert sha(rawref.with_name('stderr.bin'))==receipt['stderr_sha256']
                parsed=json.loads(parsedref.read_text())
                try:rawstate=json.loads(rawref.with_name('stdout.bin').read_bytes())
                except (json.JSONDecodeError,UnicodeDecodeError):rawstate=None
                assert parsed.get('state')==rawstate,(row['id'],'Cached parsed state differs from raw stdout')
                reference_category,_=qualify(meta,receipt,rawstate,rawref.with_name('stderr.bin').read_text())
                if not parsed.get('state'):counts['reference_fatal_or_timeout']+=1;rlabel='fatal-or-timeout'
                elif parsed.get('independent_differences'):counts['reference_state_disagreements']+=1;rlabel='state-disagreement'
                else:equal(parsed['state'],want,row['id']+'/reference');counts['reference_full_state_matches']+=1;rlabel='full-state-match'
        if not args.run_qemu:continue
        out=args.output/row['id'];out.mkdir(parents=True,exist_ok=True)
        for name in ('state.json','state.sram','state.alnk'): (out/name).unlink(missing_ok=True)
        fault='reason' in want
        settings={'FM1_POC_STOP_PC':hex(meta['stop_pc']),'FM1_POC_MAX_INSTRUCTIONS':'100'}
        if fault:settings['FM1_POC_STATE_DIR']=str(out)
        env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')};env.update(settings)
        cmd=[str(args.qemu),'-M','fm1-poc','-accel','tcg,thread=single','-icount','shift=3,align=off,sleep=off','-display','none','-serial','none','-monitor','none','-nodefaults','-kernel',str(image),'-append','alnk-probe' if fault else 'diag']
        timeout=False
        assert (args.qemu.stat().st_size,args.qemu.stat().st_mtime_ns)==qemu_identity,'QEMU executable changed during focused run'
        try:
            run=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,timeout=15)
            stdout,stderr,returncode=run.stdout,run.stderr,run.returncode
        except subprocess.TimeoutExpired as exc:
            timeout=True;stdout,stderr,returncode=exc.stdout or b'',exc.stderr or b'',None
        (out/'stdout.bin').write_bytes(stdout);(out/'stderr.bin').write_bytes(stderr)
        receipt=dict(case_id=row['id'],command=cmd,environment=settings,returncode=returncode,timeout=timeout,matrix_sha256=msha,fixture_sha256=row['fixture_sha256'],qemu_sha256=qemu_sha,reference_category=rlabel,hardware_validation=False,qualified_reference_category=reference_category if row['reference_calls'] else 'QEMU-only-mapped-PC-guard')
        (out/'raw.json').write_text(json.dumps(receipt,indent=2)+'\n')
        if timeout:raise AssertionError((row['id'],'QEMU timeout; raw partial bytes and receipt saved'))
        assert (returncode!=0)==fault,(row['id'],returncode,stderr.decode())
        state=json.loads((out/'state.json').read_text()) if fault else json.loads(stdout)
        expected=dict(want)
        if fault:
            expected.pop('inspection');memory=(out/'state.sram').read_bytes()
            assert len(memory)==0x80000
            assert memory[0x7FF8:0x8030]==bytes(8)+struct.pack('<III',0x12345678,0x89ABCDEF,0x76543210)+bytes(36)
            if 'guard windows' in want['reason']:assert state['guards']['debug_message']==1<<12
        equal(state,expected,row['id']+'/independent-QEMU')
        receipt['state']=state;(out/'parsed.json').write_text(json.dumps(receipt,indent=2)+'\n');rows.append(receipt)
        counts['qemu_fault' if fault else 'qemu_success']+=1
        print('PASS',row['id'],rlabel,flush=True)
    report=dict(passed=True,prepared_only=not args.run_qemu,matrix_sha256=msha,counts=counts,hardware_validation=False,irq_validation=False,qemu_results=rows)
    dest=args.output/'summary.json' if args.run_qemu else evidence/'offline-audit.json';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='qemu_results'}))
if __name__=='__main__':main()
