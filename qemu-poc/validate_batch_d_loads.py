#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parent-only finite focused QEMU gate. Never build; offline audit by default.

Cold state/SRAM checks use frozen independent predictions. This does not call
the reference, read implementation sources, or adopt reference poststates.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time

DEFAULT_EVIDENCE=Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-d-2026-10-08/loads')
HERE=DEFAULT_EVIDENCE
MATRIX_SHA='268a187cbe51120e37a8efdb489d92b4d6fee3aa93d67291b4fa94448dac1e08'
ACCEPTANCE_SHA='e4aaa501a37df13012e2b130481d63391d9e0ba5519dab555c714ff4741f9d49'
QEMU=Path('/Users/simonjohansson/src/fm1-qemu-poc/qemu-poc/.cache/build/qemu-system-pi32v2')
COMMAND=['-M','fm1-poc','-accel','tcg,thread=single','-icount','shift=3,align=off,sleep=off',
         '-display','none','-serial','none','-monitor','none','-nodefaults']


def sha(data):return hashlib.sha256(data).hexdigest()
def identity(path):
 s=path.stat();return dict(device=s.st_dev,inode=s.st_ino,size=s.st_size,mtime_ns=s.st_mtime_ns,ctime_ns=s.st_ctime_ns)
def write(path,value):
 with path.open('x') as f:f.write(json.dumps(value,indent=2)+'\n')
def require(ok,message):
 if not ok:raise AssertionError(message)


def verify(c,state,memory,returncode):
 fault=c['fault'];expected=c['expected']
 require((returncode!=0) if fault else (returncode==0),f"{c['name']}: return code")
 if fault:
  require(state['reason']==fault['reason'],f"{c['name']}: fault reason")
  if 'debug_message' in fault:require(state['guards']['debug_message']==fault['debug_message'],f"{c['name']}: guard message")
 for field in ('registers','specials','pc','instructions','last_access'):
  require(state.get(field)==expected[field],f"{c['name']}: independent {field} differs: expected {expected[field]}, got {state.get(field)}")
 require(len(memory)==0x80000,f"{c['name']}: SRAM length")
 require(list(struct.unpack_from('<12I',memory,0x8000))==expected['cold_inspection'],f"{c['name']}: cold inspection/neighbors")
 for addr,value in c['expected_owned_words'].items():
  address=int(addr)
  require(struct.unpack_from('<I',memory,address-0x01C00000)[0]==value,f"{c['name']}: owned/neighbors at {address:08x}")
 if 'expected_alnk_control1' in c:
  require(state['alnk']['control1']==c['expected_alnk_control1'],f"{c['name']}: ALNK CON1 replay discriminator")


def main():
 global HERE
 p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--qemu-sha256')
 p.add_argument('--approved-acceptance-sha256');p.add_argument('--evidence',type=Path,default=DEFAULT_EVIDENCE)
 p.add_argument('--check-cached',action='store_true');a=p.parse_args()
 require(not(a.run and a.check_cached),'run and cached modes are mutually exclusive')
 HERE=a.evidence.resolve()
 raw=(HERE/'acceptance.json').read_bytes();accepted=json.loads(raw);accepted_sha=sha(raw)
 require(accepted_sha==ACCEPTANCE_SHA,'accepted prediction manifest identity differs')
 require(sha((HERE/'matrix.json').read_bytes())==MATRIX_SHA==accepted['matrix_sha256'],'finite matrix changed')
 cases=accepted['cases'];require(len(cases)==accepted['total_cases'],'count differs')
 require(len({c['name'] for c in cases})==len(cases),'duplicate names')
 if a.run:
  require(a.approved_acceptance_sha256==accepted_sha,'exact reviewed acceptance approval required')
  require(a.qemu_sha256 and len(a.qemu_sha256)==64,'explicit accepted QEMU identity required')
  qemu_identity=identity(QEMU);require(sha(QEMU.read_bytes())==a.qemu_sha256,'QEMU hash differs')
  require(identity(QEMU)==qemu_identity,'QEMU changed during hash')
 fixture_pins={}
 for c in cases:
  fixture=Path(c['fixture']);pin=identity(fixture)
  require(sha(fixture.read_bytes())==c['fixture_sha256'],c['name']+' fixture hash')
  require(identity(fixture)==pin,c['name']+' fixture changed during hash')
  fixture_pins[c['name']]=pin
  if a.run:require(not (HERE/'focused'/c['name']).exists(),'partial/pre-existing focused output refuses whole run')
 if a.check_cached:
  summary=json.loads((HERE/'focused-summary.json').read_bytes())
  require(summary['passed'] and summary['cases']==len(cases),'cached finite run incomplete')
  require(summary['acceptance_sha256']==ACCEPTANCE_SHA and summary['matrix_sha256']==MATRIX_SHA,'cached run identity differs')
  if a.qemu_sha256:require(summary['qemu_sha256']==a.qemu_sha256,'cached QEMU identity differs')
  cached_receipts=[]
  for c in cases:
   output=HERE/'focused'/c['name'];receipt=json.loads((output/'raw.json').read_bytes())
   require(receipt['acceptance_sha256']==ACCEPTANCE_SHA and receipt['fixture_sha256']==c['fixture_sha256'],'cached receipt identity')
   require(receipt['qemu_sha256']==summary['qemu_sha256'] and not receipt['timed_out'],'cached receipt incomplete')
   require(receipt['qemu_identity_before']==receipt['qemu_identity_after'],'cached QEMU changed')
   require(receipt['fixture_identity_before']==receipt['fixture_identity_after'],'cached fixture changed')
   for filename,field in [('stdout.bin','stdout_sha256'),('stderr.bin','stderr_sha256'),('state.json','state_json_sha256'),('state.sram','state_sram_sha256')]:
    require(sha((output/filename).read_bytes())==receipt[field],c['name']+' cached raw/capture hash')
   cached_receipts.append(receipt)
  for c,receipt in zip(cases,cached_receipts):
   output=HERE/'focused'/c['name']
   verify(c,json.loads((output/'state.json').read_bytes()),(output/'state.sram').read_bytes(),receipt['returncode'])
  print(json.dumps(dict(passed=True,cached_cases=len(cases),qemu_sha256=summary['qemu_sha256'],new_guest_launches=0),indent=2));return
 if not a.run:
  print(json.dumps(dict(verified_fixtures=len(cases),acceptance_sha256=accepted_sha,runtime_executed=False),indent=2));return
 write(HERE/'focused-launch-started.json',dict(acceptance_sha256=accepted_sha,qemu_sha256=a.qemu_sha256,
   qemu_identity=qemu_identity,cases=len(cases),started_unix_seconds=time.time()))
 receipts=[];counts=Counter()
 for c in cases:
  require(sha((HERE/'acceptance.json').read_bytes())==accepted_sha,'acceptance changed')
  require(identity(QEMU)==qemu_identity,'QEMU identity changed')
  fixture=Path(c['fixture']);require(identity(fixture)==fixture_pins[c['name']],'fixture identity changed')
  require(sha(fixture.read_bytes())==c['fixture_sha256'],'fixture hash changed before launch')
  output=HERE/'focused'/c['name'];output.mkdir(parents=True,exist_ok=False)
  env={k:v for k,v in os.environ.items() if not k.startswith('FM1_POC_')}
  observer=dict(FM1_POC_STOP_PC=hex(c['stop_pc']),FM1_POC_MAX_INSTRUCTIONS=str(c['limit']),FM1_POC_STATE_DIR=str(output))
  env.update(observer);command=[str(QEMU),*COMMAND,'-kernel',str(fixture),'-append','alnk-probe']
  started=time.monotonic();timed_out=False
  try:
   r=subprocess.run(command,env=env,capture_output=True,timeout=15)
   stdout,stderr,returncode=r.stdout,r.stderr,r.returncode
  except subprocess.TimeoutExpired as error:
   stdout,stderr,returncode=error.stdout or b'',error.stderr or b'',None;timed_out=True
  (output/'stdout.bin').write_bytes(stdout);(output/'stderr.bin').write_bytes(stderr)
  receipt=dict(name=c['name'],command=command,environment=observer,returncode=returncode,timed_out=timed_out,
   elapsed_seconds=time.monotonic()-started,acceptance_sha256=accepted_sha,qemu_sha256=a.qemu_sha256,
   fixture_sha256=c['fixture_sha256'],stdout_sha256=sha(stdout),stderr_sha256=sha(stderr),
   state_json_sha256=sha((output/'state.json').read_bytes()) if (output/'state.json').exists() else None,
   state_sram_sha256=sha((output/'state.sram').read_bytes()) if (output/'state.sram').exists() else None,
   fixture_identity_before=fixture_pins[c['name']],fixture_identity_after=identity(fixture),
   qemu_identity_before=qemu_identity,qemu_identity_after=identity(QEMU))
  write(output/'raw.json',receipt);receipts.append(receipt)
  require(not timed_out,'Timeout aborted focused gate; raw evidence retained; no retries')
  require(identity(QEMU)==qemu_identity,'QEMU changed; raw evidence retained')
  require(identity(fixture)==fixture_pins[c['name']],'fixture changed; raw evidence retained')
  if len(receipts)%100==0:print(f"Collected {len(receipts)}/{len(cases)} focused load captures",flush=True)
 require(sha(QEMU.read_bytes())==a.qemu_sha256,'QEMU final hash differs')
 require(identity(QEMU)==qemu_identity,'QEMU final identity differs')
 # Save every finite raw result before semantic parsing or acceptance. Audit
 # every receipt/raw/capture hash as a separate whole-batch preflight first.
 require(len(receipts)==len(cases),'finite raw collection incomplete')
 for c,receipt in zip(cases,receipts):
  output=HERE/'focused'/c['name']
  require(json.loads((output/'raw.json').read_bytes())==receipt,'receipt changed')
  for filename,field in [('stdout.bin','stdout_sha256'),('stderr.bin','stderr_sha256'),('state.json','state_json_sha256'),('state.sram','state_sram_sha256')]:
   require((output/filename).exists(),c['name']+' missing raw/capture file')
   require(sha((output/filename).read_bytes())==receipt[field],c['name']+' raw/capture identity differs')
 for c,receipt in zip(cases,receipts):
  output=HERE/'focused'/c['name']
  verify(c,json.loads((output/'state.json').read_bytes()),(output/'state.sram').read_bytes(),receipt['returncode'])
  write(output/'verified.json',dict(independent_full_state=True,owned_sram_and_neighbors=True,
    model_fault_policy_only=bool(c['fault']),hardware_validation=False))
  counts['fault' if c['fault'] else 'success']+=1
 write(HERE/'focused-summary.json',dict(passed=True,cases=len(receipts),outcomes=dict(counts),
   acceptance_sha256=accepted_sha,matrix_sha256=accepted['matrix_sha256'],qemu_sha256=a.qemu_sha256,
   reference_calls=0,hardware_validation=False))
 print(json.dumps(dict(passed=True,cases=len(receipts),outcomes=dict(counts)),indent=2))


if __name__=='__main__':main()
