#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parent-only serialized collection of the reviewed finite store matrix."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

HERE = Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/stores')
REPO = Path('/Users/simonjohansson/src/fm1-qemu-poc')
ACCEPTANCE_SHA = 'be72e957aa1db29067196ac5b8b2efa119479156fc6383eedb05110a1484e2b4'
ORIGINAL_MATRIX_SHA = 'd2cc857ca277617e4c3f8ac72764b34424e10be0b8c70388bd57bd0a28b2e9ed'
REFERENCE = HERE.parent/'reference-cli-c96'
REFERENCE_SHA = 'c96ed8b21d73bd2934127b72f1a21f31d82acdb6a0792ec2b130852e478dec94'
SOURCE_SHA = 'eaba7b300dac2e14dc8c45fa02822cbb2abd3dd682e7094d6d482d4c975907b3'
SOURCE = REPO/'qemu-poc/overlay/target/pi32v2/translate.c'


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value): path.write_text(json.dumps(value,indent=2)+'\n')
def identity(path):
    s=path.stat()
    if not stat.S_ISREG(s.st_mode): raise RuntimeError(f'not regular: {path}')
    return dict(size=s.st_size,mtime_ns=s.st_mtime_ns,inode=s.st_ino,mode=s.st_mode,
                sha256=digest(path))


def checks(row,state,memory=None):
    differences=[]
    for field,key in [('pc','expected_pc'),('instructions','expected_instructions'),
                      ('registers','expected_gprs'),('specials','expected_sprs')]:
        if state.get(field)!=row[key]: differences.append(field)
    if memory is None:
        # The separate executable publishes 12 inspection words only. Outside
        # bytes are independently expected but cannot be claimed observed here.
        sampled=bytes.fromhex(row['owned_bytes'])[:48]
        expected=[int.from_bytes(sampled[i:i+4],'little') for i in range(0,48,4)]
        if state.get('inspection')!=expected: differences.append('inspection12')
    else:
        lo=row['owned_address']-0x01C00000
        want=bytes.fromhex(row['owned_bytes'])
        if len(memory)!=0x80000: differences.append('sram-length')
        if memory[lo:lo+len(want)]!=want: differences.append('owned128')
        if row['memory_scope']=='whole cold SRAM524288' and hashlib.sha256(memory).hexdigest()!=row['expected_cold_sram_sha256']: differences.append('whole-cold-sram')
        for ext in row['external_writes']:
            if row['mmio'] and not 0x01C00000<=ext['address']<0x01C80000:
                continue  # Existing device counters/readback qualify these writes.
            lo=ext['address']-0x01C00000; want=bytes.fromhex(ext['bytes'])
            if not 0<=lo<len(memory) or memory[lo:lo+len(want)]!=want:
                differences.append('external-'+hex(ext['address']))
    if not row['fault'] and memory is not None and row['memory_scope']=='whole cold SRAM524288':
        if state.get('last_access')!=row['expected_last_access']: differences.append('success-final-fetch')
    if row['fault'] and memory is not None:
        if state.get('last_access')!=row['expected_last_access']: differences.append('last_access')
        if state.get('reason')!=row['expected_reason']: differences.append('fault_reason')
    return differences


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run',action='store_true',help='Explicit parent-only authorization to launch finite batch')
    p.add_argument('--phase',default='qemu',choices=['qemu'])
    p.add_argument('--approved-matrix-sha256')
    p.add_argument('--out',default=HERE/'qemu-raw',type=Path)
    p.add_argument('--qemu',type=Path)
    p.add_argument('--qemu-sha256')
    args=p.parse_args()
    if args.phase!='qemu': raise RuntimeError('Final acceptance is QEMU-only; reference raws are frozen')
    if args.run and args.approved_matrix_sha256 is None: raise RuntimeError('--run requires explicit acceptance hash')
    if args.approved_matrix_sha256 is None: args.approved_matrix_sha256=ACCEPTANCE_SHA
    if args.approved_matrix_sha256!=ACCEPTANCE_SHA: raise RuntimeError('unreviewed acceptance identity')
    if digest(HERE/'matrix.json')!=ORIGINAL_MATRIX_SHA: raise RuntimeError('frozen research matrix drift')
    matrix=HERE/'acceptance-matrix.json'
    if digest(matrix)!=args.approved_matrix_sha256: raise RuntimeError('unreviewed matrix bytes')
    rows=json.loads(matrix.read_text())['cases']
    if not args.run:
        qualify(args,rows)
        return
    # Every fixture is audited before the first process. Reject duplicates,
    # changed files, preexisting output or partially collected prior batches.
    names=set(); images=set(); fixture_stats={}
    for row in rows:
        path=Path(row['image'])
        if row['name'] in names or path in images: raise RuntimeError('duplicate case/image')
        if path.parent!=HERE/'fixtures': raise RuntimeError('fixture outside private slice')
        names.add(row['name']); images.add(path)
        s=identity(path)
        if s['sha256']!=row['sha256']: raise RuntimeError(f'fixture drift: {path}')
        if len(row['expected_gprs'])!=16 or len(row['expected_sprs'])!=16:
            raise RuntimeError('incomplete architecture expectation')
        if len(bytes.fromhex(row['owned_bytes']))!=128: raise RuntimeError('incomplete owned memory')
        fixture_stats[row['name']]=s
    executable=REFERENCE if args.phase=='reference' else args.qemu
    pin=REFERENCE_SHA if args.phase=='reference' else args.qemu_sha256
    if executable is None or pin is None: raise RuntimeError('missing executable pin')
    executable_stat=identity(executable)
    if executable_stat['sha256']!=pin or not os.access(executable,os.X_OK):
        raise RuntimeError('executable identity or permission mismatch')
    if args.phase=='reference' and digest(SOURCE)!=SOURCE_SHA:
        raise RuntimeError('production decoder changed before reference research')
    args.out.mkdir(parents=False,exist_ok=False)
    save(args.out/'started.json',dict(phase=args.phase,matrix_sha256=digest(matrix),
        collector_sha256=digest(Path(__file__)),executable=str(executable),
        executable_stat=executable_stat,fixtures=fixture_stats,
        reference_memory_scope='first48 bytes only; no reference fault snapshot'))
    results=[]
    for row in rows:
        # MMIO device equivalence is deliberately excluded from the Rust
        # reference. QEMU's existing success diagnostic USB observables suffice.
        if args.phase=='reference' and row['mmio']:
            results.append(dict(name=row['name'],skipped='QEMU device-model gate only'))
            continue
        image=Path(row['image'])
        if identity(image)!=fixture_stats[row['name']] or identity(executable)!=executable_stat:
            raise RuntimeError('input/executable stat drift during collection')
        directory=args.out/row['name']; directory.mkdir(exist_ok=False)
        settings=dict(FM1_POC_STOP_PC=hex(row['stop_pc']),FM1_POC_MAX_INSTRUCTIONS='512')
        env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
        env.update(settings)
        if args.phase=='reference': command=[str(executable),'snapshot',str(image)]
        else:
            diag=bool(row['mmio'] and row['mmio'].get('kind','usb')=='usb' and not row['fault'])
            settings['FM1_POC_STATE_DIR']=str(directory); env.update(settings)
            command=[str(executable),'-M','fm1-poc','-accel','tcg,thread=single',
                '-icount','shift=3,align=off,sleep=off','-display','none','-serial','none',
                '-monitor','none','-nodefaults','-kernel',str(image),'-append','diag' if diag else 'alnk-probe']
        save(directory/'launch.json',dict(command=command,environment=settings,
             fixture_sha256=row['sha256'],executable_sha256=pin))
        try:
            run=subprocess.run(command,cwd=REPO,env=env,capture_output=True,timeout=15)
        except subprocess.TimeoutExpired as ex:
            (directory/'stdout.bin').write_bytes(ex.stdout or b'')
            (directory/'stderr.bin').write_bytes(ex.stderr or b'')
            save(directory/'raw.json',dict(name=row['name'],command=command,environment=settings,
                timed_out=True,timeout_seconds=15,returncode=None,
                stdout_sha256=digest(directory/'stdout.bin'),stderr_sha256=digest(directory/'stderr.bin'),
                executable_before=executable_stat,executable_after=identity(executable),
                fixture_before=fixture_stats[row['name']],fixture_after=identity(image),retry=False,
                captures_sha256={p.name:digest(p) for p in directory.glob('state*') if p.is_file()}))
            raise RuntimeError(f'timeout abort without retry: {row["name"]}')
        # Save every raw stream before parsing or asserting any result.
        (directory/'stdout.bin').write_bytes(run.stdout)
        (directory/'stderr.bin').write_bytes(run.stderr)
        receipt=dict(name=row['name'],command=command,environment=settings,
            timed_out=False,timeout_seconds=15,returncode=run.returncode,
            stdout_sha256=digest(directory/'stdout.bin'),stderr_sha256=digest(directory/'stderr.bin'),
            executable_before=executable_stat,executable_after=identity(executable),
            fixture_before=fixture_stats[row['name']],fixture_after=identity(image))
        receipt['captures_sha256']={p.name:digest(p) for p in directory.glob('state*') if p.is_file()}
        save(directory/'raw.json',receipt)
        if receipt['executable_before']!=receipt['executable_after'] or receipt['fixture_before']!=receipt['fixture_after']:
            raise RuntimeError('input stat drift after launch')
        results.append(receipt)
    save(args.out/'complete-raw.json',dict(phase=args.phase,matrix_sha256=digest(matrix),results=results,no_retries=True))
    print('Raw finite batch preserved; use a separate offline invocation to qualify.')


def qualify(args,rows):
    raw_batch=json.loads((args.out/'complete-raw.json').read_text())
    if raw_batch['matrix_sha256']!=args.approved_matrix_sha256:
        raise RuntimeError('raw matrix differs from reviewed matrix')
    # Verify the entire finite raw batch before parsing any state snapshot.
    expected_names={r['name'] for r in rows}
    transports=raw_batch['results']
    transport_names=[r['name'] for r in transports]
    if len(transport_names)!=len(set(transport_names)) or set(transport_names)!=expected_names:
        raise RuntimeError('missing/extra/duplicate raw cases')
    if {p.name for p in args.out.iterdir() if p.is_dir()}!=expected_names:
        raise RuntimeError('missing/extra raw case directories')
    by_name={r['name']:r for r in rows}
    for transport in transports:
        directory=args.out/transport['name']
        receipt=json.loads((directory/'raw.json').read_text())
        if receipt!=transport or receipt['timed_out']:
            raise RuntimeError('complete raw manifest disagrees with receipt')
        if receipt['fixture_before']!=receipt['fixture_after'] or receipt['executable_before']!=receipt['executable_after']:
            raise RuntimeError('raw receipt input/executable drift')
        if receipt['fixture_before']['sha256']!=by_name[receipt['name']]['sha256']:
            raise RuntimeError('raw fixture pin differs from frozen case')
        if digest(directory/'stdout.bin')!=receipt['stdout_sha256'] or digest(directory/'stderr.bin')!=receipt['stderr_sha256']:
            raise RuntimeError('raw stream drift')
        if {p.name for p in directory.glob('state*') if p.is_file()}!=set(receipt['captures_sha256']):
            raise RuntimeError('missing/extra raw capture files')
        for name,pin in receipt['captures_sha256'].items():
            if digest(directory/name)!=pin: raise RuntimeError('raw capture drift')
    results=[]
    for row in rows:
        if args.phase=='reference' and row['mmio']:
            results.append(dict(name=row['name'],skipped='QEMU device-model gate only'))
            continue
        directory=args.out/row['name']
        receipt=json.loads((directory/'raw.json').read_text())
        if receipt['timed_out']: raise RuntimeError('timeout batch cannot be qualified')
        if digest(directory/'stdout.bin')!=receipt['stdout_sha256'] or digest(directory/'stderr.bin')!=receipt['stderr_sha256']:
            raise RuntimeError('raw output drift')
        for name,pin in receipt['captures_sha256'].items():
            if digest(directory/name)!=pin: raise RuntimeError('raw capture drift')
        result=dict(name=row['name'],returncode=receipt['returncode'],
             raw_discrimination_only=row['raw_discrimination_only'],differences=[])
        stdout=(directory/'stdout.bin').read_bytes().decode('utf-8',errors='replace')
        stderr=(directory/'stderr.bin').read_bytes().decode('utf-8',errors='replace')
        (directory/'stdout.txt').write_text(stdout); (directory/'stderr.txt').write_text(stderr)
        state=None
        if args.phase=='qemu' and not(row['mmio'] and row['mmio'].get('kind','usb')=='usb' and not row['fault']):
            state_path=directory/'state.json'
            if state_path.exists(): state=json.loads(state_path.read_text())
            else: result['differences'].append('missing-cold-capture')
        elif receipt['returncode']==0:
            try: state=json.loads(stdout)
            except json.JSONDecodeError: result['differences'].append('invalid-state-json')
        if args.phase=='reference' and row['fault']:
            # Model-policy faults (guards and alias admission) may complete in
            # the reference. Keep raw success/fatal output; never equate them.
            result['reference_fault_snapshot_available']=False
            result['reference_completion']=state
            result['model_fault_policy_comparison_applied']=False
        elif state is not None:
            memory=None
            if args.phase=='qemu':
                mp=directory/(f'state-{row["stop_pc"]:08x}.sram' if row['mmio'] and row['mmio'].get('kind','usb')=='usb' and not row['fault'] else 'state.sram')
                if mp.exists(): memory=mp.read_bytes()
                else: result['differences'].append('missing-sram-capture')
            result['differences'].extend(checks(row,state,memory))
            if row['mmio'] and row['mmio'].get('kind','usb')=='alnk' and not row['fault']:
                for k in ('control1','control3'):
                    if state.get('alnk',{}).get(k)!=row['mmio'][k]:
                        result['differences'].append('alnk-'+k)
                result['model_only_mmio']=True
            if row['mmio'] and row['mmio'].get('kind','usb')=='usb' and not row['fault']:
                u=state.get('usb',{})
                polls=row['mmio'].get('poll_reads',1)
                want=dict(control=4,requests=2,poll_reads=polls,abandoned_requests=1,dma_packets=0,
                          recent_requests=[row['mmio']['initial'],row['mmio']['result'],0,0,0,0],
                          recent_polls=[polls,0,0,0,0,0])
                for k,v in want.items():
                    if u.get(k)!=v: result['differences'].append('usb-'+k)
                result['model_only_mmio']=True
        if args.phase=='qemu' and row['fault'] and receipt['returncode']==0:
            result['differences'].append('expected-nonzero-fault-exit')
        if not row['fault'] and receipt['returncode']!=0: result['differences'].append('expected-success-exit')
        save(directory/'result.json',result); results.append(result)
    save(args.out/'complete.json',dict(phase=args.phase,results=results,
        canonical_conflicts=[r['name'] for r in results if r.get('differences') and not r.get('raw_discrimination_only')],
        raw_discriminations=[r['name'] for r in results if r.get('differences') and r.get('raw_discrimination_only')],
        no_retries=True))
    if any(r.get('differences') for r in results): raise SystemExit('Focused store validation failed; raw evidence preserved')


if __name__=='__main__': main()
