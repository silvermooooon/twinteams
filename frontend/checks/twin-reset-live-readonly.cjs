/** English deployed-stack read-only smoke. A transport guard rejects owner mutations. */
const assert = require('node:assert/strict'); const fs = require('node:fs'); const { createHash } = require('node:crypto'); const { chromium } = require('playwright');
if (process.env.TWINTEAMS_LIVE_READONLY !== '1') throw new Error('Deployment readiness must be confirmed before this smoke.');
const base = 'http://127.0.0.1:8000'; let browser; let context; let page;
async function run() {
  browser = await chromium.launch({ headless: true, channel: 'chrome' }); context = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce' });
  await context.addInitScript(() => localStorage.setItem('twinteams.locale', 'zh-CN'));
  const forbiddenWrites = []; const blockedPresence = []; const socketCommands = []; const errors = []; const failures = [];
  await context.route('**/api/**', route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (request.method() === 'GET' || path === '/api/auth/login' || path === '/api/auth/logout') return route.continue();
    if (path === '/api/presence') blockedPresence.push(path); else forbiddenWrites.push([request.method(), path]);
    return route.abort('blockedbyclient');
  });
  page = await context.newPage(); page.on('pageerror', error => errors.push(error.message));
  page.on('websocket', socket => socket.on('framesent', frame => { try { const value = JSON.parse(String(frame.payload)); if (value.type === 'command') { socketCommands.push(value.command); if (value.command !== 'presence') forbiddenWrites.push(['WS', value.command]); } } catch { /* Subscription/pong control frames carry no content writes. */ } }));
  page.on('response', response => { if (response.url().includes('/api/me/twin') && response.status() >= 400) failures.push([response.status(), response.url()]); });
  await page.goto(base + '/#/my-twin/soul'); await page.getByPlaceholder('DEV-AAA').fill('DEV-AAA'); await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByLabel('Soul content', { exact: true }).waitFor();
  async function json(path) { const response = await context.request.get(base + '/api' + path); assert.equal(response.status(), 200); return response.json(); }
  const profile = (await json('/me/twin')).actor; const soul = await json('/me/twin/soul'); const memory = await json('/me/twin/memory?limit=100'); const reset = await json('/me/twin/reset');
  assert.match(soul.version, /^[0-9a-f]{64}$/); assert.equal(createHash('sha256').update(soul.content).digest('hex'), soul.version); assert.ok(soul.content.includes(profile.name));
  assert.equal(await page.getByLabel('Soul content', { exact: true }).inputValue(), soul.content);
  await page.screenshot({ path: '/tmp/twinteams-reset-live-soul.png', fullPage: true });
  const tabs = ['Profile', 'Soul', 'Runtime', 'Skills', 'Memory', 'Credentials'];
  for (const name of tabs) { await page.getByRole('tab', { name, exact: true }).click(); await page.locator('.twin-panel .twin-loading').waitFor({ state: 'hidden' }); assert.equal(await page.getByRole('tab', { name, exact: true }).getAttribute('aria-selected'), 'true'); }
  await page.getByRole('tab', { name: 'Profile', exact: true }).click(); const resetButton = page.getByRole('button', { name: 'Reset', exact: true });
  const background = await resetButton.evaluate(element => getComputedStyle(element).backgroundColor); const rgb = background.match(/\d+/g).map(Number); assert.ok(rgb[0] > rgb[1] && rgb[0] > rgb[2]);
  await resetButton.click(); await page.getByRole('button', { name: 'Confirm reset', exact: true }).waitFor(); assert.equal(await page.getByRole('dialog').getByRole('textbox').count(), 0);
  await page.getByText('This clears your private Twin conversation and saved Memory. Personal scheduled tasks are paused.', { exact: true }).waitFor();
  await page.screenshot({ path: '/tmp/twinteams-reset-live-desktop.png', fullPage: true }); await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('tab', { name: 'Memory', exact: true }).click(); await page.getByRole('button', { name: 'Dream now', exact: true }).waitFor();
  await page.getByText('Enabled · Git', { exact: true }).waitFor(); await page.screenshot({ path: '/tmp/twinteams-reset-live-memory.png', fullPage: true });
  const runtime = await json('/me/twin/runtime'); const memoryStatus = await json('/me/twin/memory/status'); assert.equal(runtime.model, 'gpt-6-luna'); assert.equal(runtime.worker.online, true); assert.equal(runtime.memory.mode, 'git'); assert.equal(memoryStatus.mode, 'git');
  await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole('tab', { name: 'Profile', exact: true }).click(); await resetButton.click();
  const box = await page.getByRole('dialog').boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= 390); await page.screenshot({ path: '/tmp/twinteams-reset-live-mobile.png', fullPage: true }); await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(await json('/me/twin/soul'), soul); assert.deepEqual(await json('/me/twin/memory?limit=100'), memory); assert.deepEqual(await json('/me/twin/reset'), reset); assert.deepEqual((await json('/me/twin')).actor, profile);
  assert.deepEqual(forbiddenWrites, []); assert.deepEqual(errors, []); assert.deepEqual(failures, []);
  await context.request.post(base + '/api/auth/logout'); await browser.close();
  fs.writeFileSync('/tmp/twinteams-reset-live-report.json', JSON.stringify({ base, readonly: true, api_data_mocked: false, transport_guard: 'HTTP GET and authentication only; HTTP presence aborted, WebSocket commands checked for presence only', blocked_http_presence_count: blockedPresence.length, socket_commands: socketCommands, tabs, reset_button_background: background, reset_generation: reset.generation, reset_dialog_cancelled: true, dream_clicked: false, soul_hash: soul.version, soul_contains_twin_name: true, runtime, memory_status: memoryStatus, forbiddenWrites, errors, failures, result: 'passed' }, null, 2));
  console.log('PASS actual English Reset with legacy locale preference dialog cancel/no owner writes, Dream visible/not clicked, named SOUL.md SHA-256, six tabs/Git/desktop/390px; logged out, no model calls.');
}
run().catch(async error => { console.error(error); await page?.screenshot({ path: '/tmp/twinteams-reset-live-error.png' }).catch(() => {}); await context?.request.post(base + '/api/auth/logout').catch(() => {}); await browser?.close(); process.exitCode = 1; });
