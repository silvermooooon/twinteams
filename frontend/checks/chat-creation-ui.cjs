/** Recipient-count naming/avatar acceptance using synthetic HTTP/WS only. */
const assert = require('node:assert/strict'); const { chromium } = require('playwright'); const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:8000'; let browser; let page;
async function run() {
  browser = await chromium.launch({ headless: true, channel: 'chrome' }); const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce' }); page = await context.newPage();
  const mock = await fixture(page, 'en', true); const errors = []; page.on('pageerror', error => errors.push(error.message)); const created = [];
  const pair = { id: 'pair-maya', kind: 'group', title: 'Maya', member_owner_ids: ['owner-alex', 'owner-maya'], unread_count: 0, can_rename: false };
  const chats = [mock.chat, pair, mock.group]; const reply = (route, data) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
  await page.route('**/api/bootstrap', route => reply(route, { current_owner_id: 'owner-alex', actors: mock.actors, chats, reminders: [], model_configured: true, development_mode: true }));
  await page.route('**/api/people?**', route => reply(route, mock.actors.filter(actor => actor.kind === 'human' && actor.id !== 'owner-alex')));
  await page.route('**/api/chats/pair-maya?**', route => reply(route, { revision: 1, chat: pair, messages: [], requests: [], approvals: [], tracking: [], members: mock.actors.filter(actor => pair.member_owner_ids.includes(actor.owner_id)), settings: { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'workspace' } }));
  await page.route('**/api/chats', async route => { if (route.request().method() !== 'POST') return route.fallback(); const data = JSON.parse(route.request().postData()); created.push(data); if (data.owner_ids.length === 1) return reply(route, pair); mock.group.title = data.title || 'AAA, Maya, Lin'; return reply(route, mock.group); });
  await page.goto(base + '/#chat'); await page.locator('.conversation-list .chat-item').filter({ hasText: 'Maya' }).click();
  await page.locator('.chat-header h2').filter({ hasText: 'Maya' }).waitFor();
  assert.equal(await page.locator('.chat-header>.avatar').innerText(), 'M'); assert.equal(await page.locator('.chat-header>.avatar .twin-badge').count(), 0); assert.equal(await page.locator('.chat-header>.group-avatar').count(), 0);
  const pairRow = page.locator('.conversation-list .chat-item').filter({ hasText: 'Maya' }); assert.equal(await pairRow.locator('.avatar').innerText(), 'M');
  await page.getByRole('button', { name: 'Group chats', exact: true }).click(); assert.equal(await page.locator('.conversation-list .chat-item').filter({ hasText: 'Maya' }).count(), 0);
  await page.locator('.conversation-list .chat-item').filter({ hasText: 'Shared team' }).click();
  await page.getByRole('button', { name: 'Rename group', exact: true }).waitFor(); assert.equal(await page.locator('.chat-header>.group-avatar').count(), 1); assert.equal(await page.locator('.chat-header>.avatar .twin-badge').count(), 0);
  assert.equal(await page.locator('.conversation-list .chat-item').filter({ hasText: 'Shared team' }).locator('.twin-badge').count(), 0);
  await page.getByRole('button', { name: 'Group chats', exact: true }).click();
  async function start() { await page.getByRole('button', { name: 'New chat', exact: true }).click(); await page.getByRole('option', { name: 'Maya', exact: true }).waitFor(); }
  await start(); await page.getByRole('option', { name: 'Maya', exact: true }).click(); assert.equal(await page.getByLabel('Group name (optional)', { exact: true }).count(), 0);
  await page.getByRole('option', { name: 'Lin', exact: true }).click(); const title = page.getByLabel('Group name (optional)', { exact: true }); await title.fill('Hidden title draft'); assert.equal(await title.getAttribute('maxlength'), '256');
  await page.getByRole('button', { name: 'Close Lin', exact: true }).click(); assert.equal(await title.count(), 0); await page.getByRole('button', { name: 'Start chat', exact: true }).click();
  await page.locator('.chat-header h2').filter({ hasText: 'Maya' }).waitFor(); assert.deepEqual(created[0].owner_ids, ['owner-maya']); assert.equal(Object.hasOwn(created[0], 'title'), false);
  await start(); await page.getByRole('option', { name: 'Maya', exact: true }).click(); await page.getByRole('button', { name: 'Start chat', exact: true }).click();
  await page.locator('.chat-header h2').filter({ hasText: 'Maya' }).waitFor(); assert.deepEqual(created[1].owner_ids, ['owner-maya']); assert.equal(Object.hasOwn(created[1], 'title'), false);
  const reusedPair = page.locator('.conversation-list .chat-item').filter({ hasText: 'Maya' }); assert.equal(await reusedPair.count(), 1); assert.equal(await reusedPair.getAttribute('aria-current'), 'page'); assert.equal(await page.locator('.new-chat-main').count(), 0);
  await start(); await page.getByRole('option', { name: 'Maya', exact: true }).click(); await page.getByRole('option', { name: 'Lin', exact: true }).click(); await page.getByLabel('Group name (optional)', { exact: true }).fill('  Planning team  '); await page.getByRole('button', { name: 'Start chat', exact: true }).click();
  await page.locator('.chat-header h2').filter({ hasText: 'Planning team' }).waitFor(); assert.equal(created[2].title, 'Planning team'); assert.equal(created[2].owner_ids.length, 2);
  assert.deepEqual(errors, []); await browser.close(); console.log('PASS picker 1→2→1 title rules, repeated same-person start reuses selected pair, two recipients submit trimmed title, human/group avatars and Groups filter; synthetic API/WS only.');
}
run().catch(async error => { console.error(error); if (page) { console.error((await page.locator('body').innerText()).slice(0, 2500)); await page.screenshot({ path: '/tmp/twinteams-chat-creation-error.png' }).catch(() => {}); } await browser?.close(); process.exitCode = 1; });
