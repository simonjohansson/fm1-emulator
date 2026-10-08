# SPDX-License-Identifier: GPL-2.0-or-later
"""Offline by default; parent alone launches the reviewed exact QEMU subset."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time

DEFAULT_EVIDENCE = Path('/Users/simonjohansson/src/fm1-emulator/.deps/qemu-batch-c-2026-10-08/if')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stat_identity(path):
    state = path.stat()
    return (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)


def capture_assertions(case, meta, expected, folder, receipt):
    stdout = (folder / 'stdout.bin').read_bytes()
    stderr = (folder / 'stderr.bin').read_bytes()
    assert sha(stdout) == receipt['stdout_sha256'] and sha(stderr) == receipt['stderr_sha256']
    assert not receipt['timed_out'], f"{case['id']}: timeout; partial evidence retained"
    assert stdout == b'', f"{case['id']}: alnk-probe capture unexpectedly wrote stdout"
    state_bytes = (folder / 'state.json').read_bytes()
    memory = (folder / 'state.sram').read_bytes()
    assert sha(state_bytes) == receipt['state_sha256'] and sha(memory) == receipt['sram_sha256']
    state = json.loads(state_bytes)
    for field in ['pc', 'instructions', 'registers', 'specials', 'last_access']:
        assert state[field] == expected[field], f"{case['id']}: complete {field} differs: {state[field]} expected {expected[field]}"
    # alnk-probe starts with cold SRAM; all twelve owned words are initialized.
    assert len(memory) == 0x80000
    full_memory = bytearray(0x80000)
    full_memory[0x8000:0x8030] = struct.pack('<12I', *expected['inspection'])
    assert memory == full_memory, f"{case['id']}: owned memory or an unowned neighbor changed"
    assert state['profile'] == 'alnk-probe'
    if expected['fault']:
        assert receipt['returncode'] != 0 and expected['reason_contains'] in state['reason'], f"{case['id']}: modeled fault reason/return differs"
    else:
        assert receipt['returncode'] == 0 and state['reason'] == 'checkpoint reached', f"{case['id']}: success checkpoint differs"
    for field in ['irq_entries', 'rti_count', 'timer_expirations', 'acknowledgments']:
        assert state[field] == 0, f"{case['id']}: unrelated IRQ/timer activity"
    assert state['in_irq'] is False and state['pending'] is False
    if meta['mutation'].get('guard_windows'):
        assert state['guards']['pc_windows'] == meta['mutation']['guard_windows']
        assert state['guards']['debug_unlocked'] is False
        if expected['fault'] == 'PC guard rejection':
            assert state['guards']['debug_message'] & (1 << 12)
    # Private predicate and branch-notification expectations are qualified
    # model/source facts, not direct runtime fields in the unchanged capture.
    return {'id': case['id'], 'fault': bool(expected['fault']), 'snapshot_sha256': sha(state_bytes),
            'sram_sha256': sha(memory), 'full_registers_and_specials_checked': True,
            'full_cold_sram_checked': True, 'private_predicate_directly_captured': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--check-cached', action='store_true')
    parser.add_argument('--approved-subset-sha256')
    parser.add_argument('--qemu', type=Path)
    parser.add_argument('--approved-qemu-sha256')
    args = parser.parse_args()
    ROOT = args.evidence.resolve()
    assert not (args.run and args.check_cached)
    subset_bytes = (ROOT / 'acceptance-subset.json').read_bytes()
    subset = json.loads(subset_bytes)
    subset_sha = sha(subset_bytes)
    assert sha((ROOT / 'matrix.json').read_bytes()) == subset['research_matrix_sha256']
    assert sha((ROOT / 'reference-qualification.json').read_bytes()) == subset['qualification_sha256']
    independent_bytes = (ROOT.parent / 'review/if-independent-reference-review.json').read_bytes()
    assert sha(independent_bytes) == 'c640cee5d48b8aa9f4e021f82a1676371c4672f920d84540708d664a6af1d5ea'
    independent = json.loads(independent_bytes)
    assert independent['matrix_sha256'] == subset['research_matrix_sha256'] and independent['cases'] == 9861
    assert independent['counts'] == {'complete_states': 8314, 'exact_access_fields': 96,
                                     'exact_available_loop_limit_fields_software_pattern_only': 256, 'exact_unsupported_pc_word': 1195}
    assert len(independent['records']) == 9861
    assert len({record['id'] for record in independent['records']}) == 9861
    for record in independent['records']:
        assert sha((ROOT / 'reference' / record['id'] / 'raw.json').read_bytes()) == record['raw_receipt_sha256']
    assert sha((ROOT / 'decoder.patch').read_bytes()) == subset['candidate_patch_sha256']
    variants_bytes = (ROOT / 'candidate-expectation-variants.json').read_bytes()
    assert sha(variants_bytes) == subset['candidate_expectation_variants_sha256']
    variants = json.loads(variants_bytes)
    assert len(subset['cases']) == subset['total_cases'] == 9115
    assert len({case['id'] for case in subset['cases']}) == 9115
    executable_stat = None
    if args.run:
        assert args.approved_subset_sha256 == subset_sha and args.qemu and args.approved_qemu_sha256
        executable_stat = stat_identity(args.qemu)
        assert sha(args.qemu.read_bytes()) == args.approved_qemu_sha256
        assert stat_identity(args.qemu) == executable_stat
    cache_runtime_sha = None
    if args.check_cached:
        assert args.approved_qemu_sha256, 'cached validation requires an explicitly approved runtime SHA256'
        marker = json.loads((ROOT / 'qemu-launch-started.json').read_bytes())
        assert marker['subset_sha256'] == subset_sha
        cache_runtime_sha = marker['qemu_sha256']
        assert cache_runtime_sha == args.approved_qemu_sha256
    metadata_cache = {}
    for case in subset['cases']:
        folder = ROOT / 'fixtures' / case['id']
        assert sha((folder / 'fixture.bin').read_bytes()) == case['fixture_sha256']
        metadata_bytes = (folder / 'fixture.json').read_bytes()
        assert sha(metadata_bytes) == case['metadata_sha256']
        meta = json.loads(metadata_bytes)
        expected = variants[case['id']] if case['expectation_key'] != 'frozen_original_model' else meta['expected_model']
        assert len(expected['registers']) == len(expected['specials']) == 16
        assert len(expected['inspection']) == 12
        metadata_cache[case['id']] = (meta, expected)
        if args.run:
            assert not (ROOT / 'qemu' / case['id']).exists(), 'pre-existing/partial output forbids the entire launch'
    if args.run:
        marker = {'subset_sha256': subset_sha, 'qemu_sha256': args.approved_qemu_sha256,
                  'executable_stat': executable_stat, 'cases': len(subset['cases']), 'started_unix_seconds': time.time()}
        with (ROOT / 'qemu-launch-started.json').open('x') as stream:
            stream.write(json.dumps(marker, indent=2) + '\n')
    records = []
    for case in subset['cases']:
        if not (args.run or args.check_cached):
            continue
        meta, expected = metadata_cache[case['id']]
        output = ROOT / 'qemu' / case['id']
        if args.run:
            fixture_folder = ROOT / 'fixtures' / case['id']
            fixture = fixture_folder / 'fixture.bin'
            assert sha(fixture.read_bytes()) == case['fixture_sha256']
            assert sha((fixture_folder / 'fixture.json').read_bytes()) == case['metadata_sha256']
            assert stat_identity(args.qemu) == executable_stat
            output.mkdir(parents=True, exist_ok=False)
            command = [str(args.qemu), '-M', 'fm1-poc', '-accel', 'tcg,thread=single',
                       '-icount', 'shift=3,align=off,sleep=off', '-display', 'none',
                       '-serial', 'none', '-monitor', 'none', '-nodefaults',
                       '-kernel', str(fixture.resolve()), '-append', 'alnk-probe']
            settings = {'FM1_POC_STOP_PC': hex(meta['stop_pc']),
                        'FM1_POC_MAX_INSTRUCTIONS': str(meta['mutation'].get('budget', 100)),
                        'FM1_POC_STATE_DIR': str(output.resolve())}
            clean_env = {key: value for key, value in os.environ.items() if not key.startswith('FM1_POC_')}
            started = time.monotonic()
            try:
                result = subprocess.run(command, env={**clean_env, **settings}, capture_output=True, timeout=15)
                stdout, stderr, returncode, timed_out = result.stdout, result.stderr, result.returncode, False
            except subprocess.TimeoutExpired as error:
                stdout, stderr, returncode, timed_out = error.stdout or b'', error.stderr or b'', None, True
            (output / 'stdout.bin').write_bytes(stdout)
            (output / 'stderr.bin').write_bytes(stderr)
            receipt = {'id': case['id'], 'command': command, 'environment': settings, 'subset_sha256': subset_sha,
                       'qemu_sha256': args.approved_qemu_sha256, 'fixture_sha256': case['fixture_sha256'],
                       'metadata_sha256': case['metadata_sha256'], 'returncode': returncode, 'timed_out': timed_out,
                       'elapsed_seconds': time.monotonic() - started, 'stdout_sha256': sha(stdout), 'stderr_sha256': sha(stderr),
                       'executable_stat_before': executable_stat, 'executable_stat_after': stat_identity(args.qemu),
                       'state_sha256': sha((output / 'state.json').read_bytes()) if (output / 'state.json').exists() else None,
                       'sram_sha256': sha((output / 'state.sram').read_bytes()) if (output / 'state.sram').exists() else None}
            (output / 'raw.json').write_text(json.dumps(receipt, indent=2) + '\n')
            assert tuple(receipt['executable_stat_after']) == executable_stat
        else:
            receipt = json.loads((output / 'raw.json').read_text())
        assert receipt['subset_sha256'] == subset_sha and receipt['fixture_sha256'] == case['fixture_sha256']
        assert receipt['metadata_sha256'] == case['metadata_sha256']
        assert receipt['qemu_sha256'] == args.approved_qemu_sha256
        if args.check_cached:
            assert receipt['qemu_sha256'] == cache_runtime_sha
        records.append(capture_assertions(case, meta, expected, output, receipt))
        if len(records) % 100 == 0:
            print(f"Validated {len(records)}/{subset['total_cases']} frozen full-state IF cases", flush=True)
    summary = {'subset_sha256': subset_sha, 'fixture_audit_cases': len(subset['cases']), 'executed_or_cached_validated': len(records),
               'full_state_checks_passed': bool(records), 'run_authorized': args.run,
               'successes': sum(not r['fault'] for r in records), 'model_faults': sum(r['fault'] for r in records),
               'private_predicate_runtime_capture': False, 'hardware_validation': False, 'records': records}
    destination = 'validation.json' if records else 'focused-offline-audit.json'
    (ROOT / destination).write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k != 'records'}, indent=2))


if __name__ == '__main__':
    main()
