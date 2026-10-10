// SPDX-License-Identifier: GPL-2.0-or-later
// Differential check of the WebAssembly build against the native emulator.
//
//     mise run test_wasm [FIRMWARE] [GUEST_SECONDS]
//
// Boots the firmware headless in both, stops each at the same guest time
// (FM1_POC_CAPTURE_NS) and requires identical machine state: registers, PC,
// instruction count, virtual time and device counters (state.json), SRAM, and
// the display. Icount makes a run deterministic, so any difference is a bug in
// one build. The task builds both first. Use a firmware
// that runs one core to completion; with two busy cores the interleaving is
// only mostly reproducible, even natively. Felucca qualifies.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const firmware = path.resolve(process.argv[2] ?? path.join(root, '.cache/felucca.fwsc'));
const seconds = Number(process.argv[3] ?? 2);
const emulator = path.join(root, 'emulator');
const dist = path.join(root, '.cache/wasm/dist/qemu-system-pi32v2.js');
const ARGUMENTS = ['-M', 'fm1-poc', '-smp', '2', '-accel', 'tcg,thread=single',
                   '-icount', 'shift=3,align=off,sleep=off', '-display', 'none',
                   '-chardev', 'null,id=console', '-serial', 'chardev:console',
                   '-monitor', 'none', '-nodefaults', '-no-user-config'];
const CAPTURE = {
  FM1_POC_CAPTURE_NS: String(Math.round(seconds * 1e9)),
  FM1_POC_MAX_INSTRUCTIONS: '10000000000000',
};
const FILES = ['state.json', 'state.sram', 'lcd.ppm'];

function fail(message) {
  console.error(`FAIL: ${message}`);
  process.exit(1);
}

for (const [what, file] of [['firmware', firmware], ['native ./emulator (mise run build)', emulator],
                            ['wasm build (mise run build_wasm)', dist]]) {
  if (!fs.existsSync(file)) fail(`${what} not found: ${file}`);
}

function runNative(directory) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('FM1_POC_')));
  const result = spawnSync(emulator, ['--qemu', ...ARGUMENTS, '-kernel', firmware, '-append', 'application'],
    { env: { ...env, ...CAPTURE, FM1_POC_STATE_DIR: directory }, encoding: 'utf8' });
  if (!/requested capture/.test(result.stderr)) fail(`native run did not reach the capture:\n${result.stderr}`);
  return Object.fromEntries(FILES.map((name) => [name, fs.readFileSync(path.join(directory, name))]));
}

async function runWasm() {
  const { default: createModule } = await import(pathToFileURL(dist));
  const extension = (firmware.match(/\.(fwsc|ufw|bin)$/i) ?? ['', 'bin'])[1].toLowerCase();
  const image = `/firmware.${extension}`;
  let reached;
  const done = new Promise((resolve) => { reached = resolve; });
  const module = await createModule({
    arguments: [...ARGUMENTS, '-kernel', image, '-append', 'application'],
    preRun: [(m) => {
      m.FS.mkdir('/out');
      m.FS.writeFile(image, fs.readFileSync(firmware));
      Object.assign(m.ENV, { ...CAPTURE, FM1_POC_STATE_DIR: '/out' });
    }],
    print: () => {},
    printErr: (line) => { if (/requested capture/.test(line)) reached(); },
  });
  await done;
  const files = Object.fromEntries(FILES.map((name) => [name, Buffer.from(module.FS.readFile(`/out/${name}`))]));
  // The module keeps its runtime alive after the guest stops; end the process explicitly.
  return files;
}

const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'fm1-wasm-boot-'));
console.log(`${path.basename(firmware)}: ${seconds} guest seconds, native then wasm`);
let started = Date.now();
const native = runNative(directory);
console.log(`native: ${((Date.now() - started) / 1000).toFixed(1)} s`);
started = Date.now();
const wasm = await runWasm();
console.log(`wasm:   ${((Date.now() - started) / 1000).toFixed(1)} s`);

const state = JSON.parse(native['state.json']);
console.log(`stopped at PC 0x${state.pc.toString(16)} after ${state.instructions} instructions`);
let failed = false;
for (const name of FILES) {
  if (Buffer.compare(native[name], wasm[name]) === 0) {
    console.log(`  ${name}: identical`);
    continue;
  }
  failed = true;
  if (name === 'state.json') {
    const other = JSON.parse(wasm[name]);
    for (const key of Object.keys(state)) {
      if (JSON.stringify(state[key]) !== JSON.stringify(other[key])) {
        console.error(`  state.json ${key}: native ${JSON.stringify(state[key])} wasm ${JSON.stringify(other[key])}`);
      }
    }
  } else {
    console.error(`  ${name}: differs`);
  }
}
fs.rmSync(directory, { recursive: true, force: true });
if (failed) fail('the WebAssembly build diverged from the native emulator');
console.log('PASS');
process.exit(0);
