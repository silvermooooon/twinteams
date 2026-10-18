/** Actual disposable PG/Git API browser checks. Refuses normal application ports. */
const assert = require('node:assert/strict'); const fs = require('node:fs'); const { chromium } = require('playwright');
const marker = process.env.TWINTEAMS_ISOLATED_FIXTURE;
if (!marker) throw new Error('An acceptance-owned isolated fixture marker is required.');
const fixture = JSON.parse(fs.readFileSync(marker, 'utf8')); const base = fixture.urls[0]; const address = new URL(base);
if (address.hostname !== '127.0.0.1' || !address.port || ['8000', '8001', '5199', '5173'].includes(address.port)) throw new Error('Refusing a non-isolated application port.');
let browser; let page;
async function run() {
  browser = await chromium.launch({ channel: 'chrome', headless: true }); const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce' });
  await context.addInitScript(() => localStorage.setItem('twinteams.locale', 'en')); page = await context.newPage();
  const errors = []; const failures = []; const resetCalls = []; const chatWrites = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => { if (response.url().includes('/api/me/twin') && response.status() >= 500) failures.push([response.status(), response.url()]); });
  page.on('request', request => { if (new URL(request.url()).pathname === '/api/me/twin/reset' && request.method() === 'POST') resetCalls.push(JSON.parse(request.postData())); if (request.method() === 'POST' && /\/api\/chats\/[^/]+\/messages/.test(request.url())) chatWrites.push(request.url()); });
  await page.goto(base + '/#/my-twin/soul'); await page.getByPlaceholder('DEV-AAA').fill(fixture.username || 'DEV-AAA'); await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByLabel('Soul content', { exact: true }).waitFor();
  const api = context.request;
  async function json(path) { const result = await api.get(base + '/api' + path); assert.equal(result.status(), 200, await result.text()); return result.json(); }
  const initialProfile = (await json('/me/twin')).actor; const initialSoul = await json('/me/twin/soul');
  const note = '\n\nOwner custom note: keep this paragraph.';
  await page.getByLabel('Soul content', { exact: true }).fill(initialSoul.content + note); await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.getByText('Saved', { exact: true }).waitFor();
  const saved = await json('/me/twin/soul'); assert.match(saved.version, /^[0-9a-f]{64}$/);
  const newName = initialProfile.name + ' UI'; const renamed = await api.patch(base + '/api/me/twin', { data: { name: newName } }); assert.equal(renamed.status(), 200);
  const renamedSoul = await json('/me/twin/soul'); assert.ok(renamedSoul.content.includes('Owner custom note: keep this paragraph.')); if (initialSoul.content.includes(initialProfile.name)) assert.ok(renamedSoul.content.includes(newName));
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await page.waitForFunction(content => document.querySelector('textarea[aria-label="Soul content"]')?.value === content, renamedSoul.content);
  await page.getByLabel('Soul content', { exact: true }).fill(renamedSoul.content + '\nLocal merge note.');
  const external = await api.put(base + '/api/me/twin/soul', { data: { content: renamedSoul.content + '\nExternal file edit.', expected_version: renamedSoul.version } }); assert.equal(external.status(), 200);
  await page.getByRole('button', { name: 'Keep draft and use current version', exact: true }).waitFor();
  await page.getByRole('button', { name: 'Keep draft and use current version', exact: true }).click();
  await page.getByLabel('Soul content', { exact: true }).fill(renamedSoul.content + '\nExternal file edit.\nLocal merge note.');
  await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.getByText('Saved', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Delete SOUL.md', exact: true }).click(); await page.getByRole('dialog').getByRole('button', { name: 'Delete SOUL.md', exact: true }).click(); await page.getByRole('dialog').waitFor({ state: 'hidden' });
  assert.deepEqual(await json('/me/twin/soul'), { content: '', version: null }); assert.deepEqual(await json('/me/twin/soul'), { content: '', version: null });
  await page.getByLabel('Soul content', { exact: true }).fill('Custom SOUL.md retained through reset.'); await page.getByRole('button', { name: 'Save', exact: true }).click(); await page.getByText('Saved', { exact: true }).waitFor();
  const beforeSoul = await json('/me/twin/soul'); const beforeProfile = (await json('/me/twin')).actor; const beforeSkills = await json('/me/twin/skills'); const beforeCredentials = await json('/me/twin/credentials');
  const bootstrap = await json('/bootstrap'); const group = bootstrap.chats.find(chat => chat.kind === 'group'); const beforeGroup = group ? (await json('/chats/' + group.id)).messages : [];
  await page.getByRole('tab', { name: 'Memory', exact: true }).click();
  const dreamButton = page.getByRole('button', { name: 'Dream now', exact: true }); let dreamResult;
  if (await dreamButton.isEnabled()) {
    const dreamResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/me/twin/memory/dream' && response.request().method() === 'POST');
    await dreamButton.click(); dreamResult = await (await dreamResponse).json(); assert.ok(['queued', 'already_queued', 'nothing_to_process'].includes(dreamResult.status));
    await page.getByText(dreamResult.status === 'nothing_to_process' ? 'No new conversation experiences to process.' : dreamResult.status === 'already_queued' ? 'A memory task is already queued.' : 'Memory maintenance queued.', { exact: true }).waitFor();
  } else { dreamResult = { status: (await json('/me/twin/memory/status')).dream.status }; assert.equal(dreamResult.status, 'queued'); }
  await page.getByRole('tab', { name: 'Profile', exact: true }).click(); await page.getByRole('button', { name: 'Reset', exact: true }).click();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click(); assert.equal(resetCalls.length, 0);
  const beforeReset = await json('/me/twin/reset'); await page.getByRole('button', { name: 'Reset', exact: true }).click();
  await page.screenshot({ path: '/tmp/twinteams-reset-real-desktop.png', fullPage: true });
  await page.getByRole('button', { name: 'Confirm reset', exact: true }).click(); await page.getByText('Private conversation and Memory reset.', { exact: true }).waitFor();
  const afterReset = await json('/me/twin/reset'); assert.equal(afterReset.generation, beforeReset.generation + 1);
  assert.deepEqual((await json('/chats/' + afterReset.private_chat_id)).messages, []); assert.deepEqual((await json('/me/twin/memory')).items, []);
  assert.deepEqual(await json('/me/twin/soul'), beforeSoul); assert.deepEqual((await json('/me/twin')).actor, beforeProfile); assert.deepEqual(await json('/me/twin/skills'), beforeSkills); assert.deepEqual(await json('/me/twin/credentials'), beforeCredentials);
  if (group) assert.deepEqual((await json('/chats/' + group.id)).messages, beforeGroup);
  const key = 'post-reset-' + Date.now(); const entryPath = '/me/twin/memory/entry?key=' + key;
  assert.equal((await api.put(base + '/api' + entryPath, { data: { content: 'New memory must survive replay.', expected_version: null } })).status(), 200);
  assert.equal((await api.post(base + '/api/me/twin/reset', { data: resetCalls[0] })).status(), 200); assert.equal((await json(entryPath)).content, 'New memory must survive replay.');
  await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole('button', { name: 'Reset', exact: true }).click();
  const box = await page.getByRole('dialog').boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= 390); await page.screenshot({ path: '/tmp/twinteams-reset-real-mobile.png', fullPage: true }); await page.keyboard.press('Escape');
  assert.deepEqual(errors, []); assert.deepEqual(failures, []); assert.deepEqual(chatWrites, []); await api.post(base + '/api/auth/logout'); await browser.close();
  fs.writeFileSync('/tmp/twinteams-reset-real-report.json', JSON.stringify({ isolated_base: base, mock_routes: false, generation_before: beforeReset.generation, generation_after: afterReset.generation, group_preserved: true, configuration_preserved: true, soul_hash: beforeSoul.version, dream: dreamResult.status, replay_preserves_new_memory: true, errors, failures, chatWrites, result: 'passed' }, null, 2));
  console.log('PASS isolated actual Reset/private+GitMemory cleanup and replay, group/config preservation, SOUL.md file hash/delete/rename/merge, manual Dream queue/dedup, desktop/mobile; no mocks/worker/LLM.');
}
run().catch(async error => { console.error(error); if (page) { console.error((await page.locator('body').innerText()).slice(0, 3500)); await page.screenshot({ path: '/tmp/twinteams-reset-real-error.png' }).catch(() => {}); } await browser?.close(); process.exitCode = 1; });
