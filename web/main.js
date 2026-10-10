// Loads a firmware from disk and boots it in the WebAssembly emulator.
const $ = (id) => document.getElementById(id);
const els = { file: $('file'), drop: $('drop'), label: $('drop-label'), boot: $('boot'), stop: $('stop'),
              notice: $('notice'), status: $('status'), lcd: $('lcd'), console: $('console') };
const ctx = els.lcd.getContext('2d');
const LCD_BYTES = 240 * 240 * 4;
const MAX_LINES = 400;
let firmware = null;      // { name, bytes }
let started = 0;
let module = null;
let timers = [];

// 64-bit WebAssembly memory: QEMU's Emscripten host is wasm64.
const hasMemory64 = WebAssembly.validate(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0, 5, 3, 1, 4, 0]));

function notify(text, kind = '') {
  els.notice.hidden = !text;
  els.notice.textContent = text;
  els.notice.className = kind;
}

function log(line) {
  if (/unsupported syscall/.test(line) || !line.trim()) return;
  const lines = els.console.textContent.split('\n');
  lines.push(line);
  els.console.textContent = lines.slice(-MAX_LINES).join('\n');
  els.console.scrollTop = els.console.scrollHeight;
}

async function choose(file) {
  if (!file) return;
  firmware = { name: file.name, bytes: new Uint8Array(await file.arrayBuffer()) };
  els.label.textContent = file.name;
  els.boot.disabled = !!module;
  notify('');
}

els.file.addEventListener('change', () => choose(els.file.files[0]));
els.drop.addEventListener('dragover', (e) => { e.preventDefault(); els.drop.classList.add('over'); });
els.drop.addEventListener('dragleave', () => els.drop.classList.remove('over'));
els.drop.addEventListener('drop', (e) => {
  e.preventDefault();
  els.drop.classList.remove('over');
  choose(e.dataTransfer.files[0]);
});
els.stop.addEventListener('click', () => location.reload());

function clock() {
  const seconds = Math.floor((performance.now() - started) / 1000);
  els.status.textContent = `Running, ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

function paint() {
  const pointer = Number(module._fm1_web_lcd_frame());
  if (!pointer) return;
  const frame = module.HEAPU8.slice(pointer, pointer + LCD_BYTES);
  ctx.putImageData(new ImageData(new Uint8ClampedArray(frame.buffer), 240, 240), 0, 0);
}

async function boot() {
  if (!hasMemory64) {
    notify('This browser has no 64-bit WebAssembly. Use a current Chrome or Firefox.', 'error');
    return;
  }
  if (!self.crossOriginIsolated) {
    notify('Not cross-origin isolated, so threads are unavailable. Reload the page once; if that does not help, serve it with COOP and COEP headers.', 'error');
    return;
  }
  els.boot.disabled = true;
  els.file.disabled = true;
  els.stop.disabled = false;
  els.console.textContent = '';
  els.status.textContent = 'Loading the emulator…';
  // The package kind is chosen from the file extension.
  const extension = (firmware.name.match(/\.(fwsc|ufw|bin)$/i) || ['', 'bin'])[1].toLowerCase();
  const path = `/firmware.${extension}`;
  try {
    const { default: createModule } = await import('./qemu-system-pi32v2.js');
    module = await createModule({
      arguments: ['-M', 'fm1-poc', '-smp', '2', '-accel', 'tcg,thread=single',
                  '-icount', 'shift=3,align=off,sleep=off', '-display', 'none',
                  '-serial', 'stdio', '-monitor', 'none', '-nodefaults', '-no-user-config',
                  '-kernel', path, '-append', 'application'],
      preRun: [(m) => m.FS.writeFile(path, firmware.bytes)],
      print: log,
      printErr: log,
    });
  } catch (error) {
    notify(`Could not start the emulator: ${error.message ?? error}`, 'error');
    els.status.textContent = 'Stopped';
    return;
  }
  started = performance.now();
  clock();
  timers = [setInterval(clock, 1000), setInterval(paint, 250)];
}

els.boot.addEventListener('click', boot);
if (!hasMemory64) notify('This browser has no 64-bit WebAssembly. Use a current Chrome or Firefox.', 'error');
