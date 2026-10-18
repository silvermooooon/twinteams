/** Deployed-stack smoke: no API mocks, profile/memory writes, chats or model calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
if (process.env.TWINTEAMS_LIVE_READONLY !== '1') throw new Error('Set TWINTEAMS_LIVE_READONLY=1 after deployment readiness confirmation.');
const base = 'http://127.0.0.1:8000'; let browser; let page;
async function run() {
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce' });
  await context.addInitScript(() => localStorage.setItem('twinteams.locale', 'en'));
  page = await context.newPage(); const errors = []; const failures = []; const forbiddenWrites = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => { if (response.url().includes('/api/me/twin') && response.status() >= 400) failures.push([response.status(), response.url()]); });
  page.on('request', request => { const path = new URL(request.url()).pathname; if (request.method() !== 'GET' && (path.startsWith('/api/me/twin') || /\/api\/chats\/[^/]+\/(messages|requests)/.test(path))) forbiddenWrites.push([request.method(), path]); });
  await page.goto(base + '/#/my-twin/soul'); await page.getByPlaceholder('DEV-AAA').fill('DEV-AAA');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByLabel('Soul content', { exact: true }).waitFor();
  async function json(path) { const response = await context.request.get(base + '/api' + path); assert.equal(response.status(), 200); return response.json(); }
  const beforeSoul = await json('/me/twin/soul'); const beforeMemory = await json('/me/twin/memory?limit=100');
  assert.equal(await page.getByLabel('Soul content', { exact: true }).inputValue(), beforeSoul.content);
  assert.equal(await page.getByLabel('Soul content', { exact: true }).getAttribute('maxlength'), '50000');
  assert.equal(await page.getByText('Platform prompt', { exact: true }).count(), 0);
  await page.screenshot({ path: '/tmp/twinteams-soul-memory-live-soul.png', fullPage: true });
  const tabs = ['Profile', 'Runtime', 'Skills', 'Credentials', 'Memory'];
  for (const name of tabs) {
    await page.getByRole('tab', { name, exact: true }).click();
    await page.locator('.twin-panel .twin-loading').waitFor({ state: 'hidden' });
    assert.equal(await page.getByRole('tab', { name, exact: true }).getAttribute('aria-selected'), 'true');
  }
  await page.getByRole('tab', { name: 'Profile', exact: true }).click();
  await page.getByRole('button', { name: 'Reset', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm reset', exact: true }).waitFor();
  await page.screenshot({ path: '/tmp/twinteams-reset-live-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByRole('tab', { name: 'Memory', exact: true }).click();
  const runtime = await json('/me/twin/runtime'); const memoryStatus = await json('/me/twin/memory/status');
  assert.equal(runtime.model, 'gpt-6-luna'); assert.equal(runtime.worker.online, true); assert.equal(runtime.configured, true);
  assert.equal(runtime.memory.mode, 'git'); assert.equal(memoryStatus.mode, 'git'); assert.equal(memoryStatus.enabled, true);
  await page.getByText('Enabled · Git', { exact: true }).waitFor();
  await page.getByText('Memory maintenance', { exact: true }).waitFor();
  await page.screenshot({ path: '/tmp/twinteams-soul-memory-live-desktop.png', fullPage: true });
  const rows = page.locator('.twin-memory-row'); let existingEditor = false;
  if (await rows.count()) {
    await rows.first().click(); await page.getByRole('button', { name: 'Edit memory', exact: true }).click();
    await page.getByLabel('Memory content', { exact: true }).waitFor(); existingEditor = true;
  } else await page.getByRole('button', { name: 'Add memory', exact: true }).click();
  await page.getByRole('dialog').waitFor(); await page.keyboard.press('Escape');
  await page.getByRole('tab', { name: 'Runtime', exact: true }).click();
  await page.getByText('gpt-6-luna', { exact: true }).waitFor();
  await page.getByText(runtime.provider, { exact: true }).waitFor(); await page.getByText(runtime.api, { exact: true }).waitFor();
  await page.screenshot({ path: '/tmp/twinteams-soul-memory-live-runtime.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('tab', { name: 'Memory', exact: true }).click();
  await page.getByRole('button', { name: 'Add memory', exact: true }).click();
  const modal = await page.getByRole('dialog').boundingBox(); assert.ok(modal.x >= 0 && modal.x + modal.width <= 390);
  await page.screenshot({ path: '/tmp/twinteams-soul-memory-live-mobile.png', fullPage: true });
  await page.keyboard.press('Escape');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  assert.deepEqual(await json('/me/twin/soul'), beforeSoul); assert.deepEqual(await json('/me/twin/memory?limit=100'), beforeMemory);
  assert.deepEqual(errors, []); assert.deepEqual(failures, []); assert.deepEqual(forbiddenWrites, []);
  await context.request.post(base + '/api/auth/logout'); await browser.close();
  fs.writeFileSync('/tmp/twinteams-soul-memory-live-report.json', JSON.stringify({ base, mock_routes: false, readonly: true, tabs: ['Soul', ...tabs], runtime, memory_status: memoryStatus, existing_memory_editor_checked: existingEditor, soul_version: beforeSoul.version, memory_count: beforeMemory.items.length, errors, failures, forbiddenWrites, result: 'passed' }, null, 2));
  console.log('PASS deployed read-only Soul/Git Memory/Dream and original tabs, actual gpt-6-luna worker, desktop/mobile; unchanged content, no mocks, configuration writes, chat writes or model calls.');
}
run().catch(async error => { console.error(error); await page?.screenshot({ path: '/tmp/twinteams-soul-memory-live-error.png' }).catch(() => {}); await browser?.close(); process.exitCode = 1; });
