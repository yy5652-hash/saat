// Clean screenshots of the real dashboard after a full market day, for the README and the brief.
//   node scripts/shots.mjs   -> docs/*.png
import { spawn } from 'node:child_process';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SHELL = `${process.env.HOME}/Library/Caches/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-mac-arm64/chrome-headless-shell`;
const PORT = 5391, DEBUG = 9451;
const wait = ms => new Promise(r => setTimeout(r, ms));
const procs = [];
const start = (cmd, args, opts) => { const p = spawn(cmd, args, { stdio: 'ignore', ...opts }); procs.push(p); return p; };
process.on('exit', () => procs.forEach(p => p.kill()));
start(`${process.env.HOME}/.local/bin/uv`, ['run', 'uvicorn', 'app.server:app', '--port', String(PORT)], { cwd: ROOT, env: { ...process.env, PATH: `${process.env.HOME}/.foundry/bin:${process.env.PATH}` } });
start(SHELL, [`--remote-debugging-port=${DEBUG}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), 'saat-shots-'))}`, '--window-size=1600,1000', '--hide-scrollbars', 'about:blank']);
async function until(fn, what) { for (let i = 0; i < 400; i++) { try { const v = await fn(); if (v) return v; } catch {} await wait(250); } throw new Error(what); }
await until(async () => (await fetch(`http://127.0.0.1:${PORT}/api/state`)).ok, 'server');
const wsUrl = await until(async () => (await (await fetch(`http://127.0.0.1:${DEBUG}/json`)).json()).find(t => t.type === 'page')?.webSocketDebuggerUrl, 'chromium');
const ws = new WebSocket(wsUrl);
await new Promise(r => { ws.onopen = r; });
let id = 0; const pending = new Map();
ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const js = async expr => (await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
await send('Page.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 2, mobile: false });
await send('Page.navigate', { url: `http://127.0.0.1:${PORT}/` });
await until(() => js("typeof S !== 'undefined' && S.series !== null && document.querySelector('#ev-ver tr') !== null"), 'page');
await js('Chart.defaults.animation = false; drawGap(); true');
await js(`(async () => {
  document.getElementById('issue-tamper').value = 'night'; await verifyClick();
  await issueDevice(2); await issueDevice(1);
  document.getElementById('deliver-facts').innerHTML = factsDeliver(await api('/api/deliver', {}));
  await api('/api/list', {});
  await planClick(7); await api('/api/execute', { meter: 7 });
  await api('/api/plan', { meter: 8 }); await api('/api/execute', { meter: 8 });
  drawBook(await api('/api/book'));
  await settleClick(7); await mismatchClick(); await refresh();
  return true; })()`);
await wait(500);
mkdirSync(join(ROOT, 'docs'), { recursive: true });
async function shot(name, clip) {
  const r = await send('Page.captureScreenshot', { format: 'png', clip: { ...clip, scale: 1 }, captureBeyondViewport: true });
  writeFileSync(join(ROOT, 'docs', name), Buffer.from(r.result.data, 'base64'));
  console.log('docs/' + name);
}
const box = sel => js(`(() => { const r = document.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height }; })()`);
await shot('dashboard.png', { x: 0, y: 0, width: 1600, height: 1000 });
for (const [name, sel] of [['issue.png', '#s-issue'], ['market.png', '#s-market'], ['plan.png', '#s-plan'], ['claim.png', '#s-claim'], ['evidence.png', '#s-evidence']]) {
  const b = await box(sel);
  await shot(name, { x: b.x - 8, y: b.y - 8, width: b.width + 16, height: b.height + 16 });
}
process.exit(0);
