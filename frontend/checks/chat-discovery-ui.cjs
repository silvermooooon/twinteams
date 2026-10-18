/** Background chat discovery; all HTTP/WS synthetic, no business writes or model calls. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5199';
let browser;
async function scenario(kind) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const mock = await fixture(page, 'en', true);
  const chats = [mock.chat, mock.group]; let bootstrapReads = 0;
  await page.route('**/api/bootstrap', route => {
    bootstrapReads++;
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ current_owner_id: 'owner-alex', actors: mock.actors, chats, reminders: [], model_configured: true, development_mode: true }) });
  });
  await page.goto(base + '/#chat');
  await page.locator('.composer textarea').waitFor();
  if (kind === 'group') await page.locator('.conversation-list .chat-item').filter({ hasText: 'Shared team' }).click();
  const input = page.locator('.composer textarea');
  await input.fill('Unsent discovery draft'); await input.focus();
  await page.evaluate(() => { window.__discoveryInput = document.querySelector('.composer textarea'); });
  const selected = await page.locator('.conversation-list .chat-item[aria-current=page]').innerText();
  const header = await page.locator('.chat-header h2').innerText();
  const readsBefore = bootstrapReads, socketsBefore = mock.sockets.length;
  chats.push({ id: 'background-new', kind: 'group', title: 'Background created team', member_owner_ids: ['owner-alex', 'owner-maya', 'owner-lin'], unread_count: 0, can_rename: true });
  mock.sockets.at(-1).send(JSON.stringify({ v: 1, type: 'personal_changed', section: 'chats' }));
  await page.locator('.conversation-list .chat-item').filter({ hasText: 'Background created team' }).waitFor();
  await page.waitForTimeout(250);
  assert.ok(bootstrapReads > readsBefore, 'targeted hint reloads canonical bootstrap');
  assert.ok(mock.sockets.length > socketsBefore, 'new chat ID refreshes existing WS subscription');
  assert.equal(await input.inputValue(), 'Unsent discovery draft');
  assert.equal(await input.evaluate(el => el === window.__discoveryInput && el === document.activeElement), true, 'composer identity and focus remain stable');
  assert.equal(await page.locator('.conversation-list .chat-item[aria-current=page]').innerText(), selected);
  assert.equal(await page.locator('.chat-header h2').innerText(), header);
  assert.equal(mock.calls.filter(call => call.path.endsWith('/messages')).length, 0);
  const socketsAfterHint = mock.sockets.length;
  mock.sockets.at(-1).close({ code: 1001, reason: 'Synthetic connection loss' });
  chats.push({ id: 'offline-new', kind: 'group', title: 'Joined while offline', member_owner_ids: ['owner-alex', 'owner-maya', 'owner-lin'], unread_count: 0, can_rename: true });
  await page.locator('.conversation-list .chat-item').filter({ hasText: 'Joined while offline' }).waitFor();
  await page.waitForTimeout(250);
  assert.ok(mock.sockets.length > socketsAfterHint, 'reconnection recovers missed chat hints');
  assert.equal(await input.inputValue(), 'Unsent discovery draft');
  assert.equal(await input.evaluate(el => el === window.__discoveryInput && el === document.activeElement), true);
  assert.equal(await page.locator('.conversation-list .chat-item[aria-current=page]').innerText(), selected);
  assert.equal(await page.locator('.chat-header h2').innerText(), header);
  const settledSockets = mock.sockets.length;
  // Canonical list order changes must not change the subscribed chat-ID set.
  chats.reverse(); mock.sockets.at(-1).send(JSON.stringify({ v: 1, type: 'personal_changed', section: 'chats' }));
  await page.waitForTimeout(250);
  assert.equal(mock.sockets.length, settledSockets, 'chat order does not reconnect an unchanged subscription');
  assert.deepEqual(errors, []);
  await context.close(); console.log('PASS background chat discovery preserves ' + kind + ' selection/draft/focus and refreshes WS subscription');
}
async function run() {
  browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
  for (const kind of ['private', 'group']) await scenario(kind);
  await browser.close();
}
run().catch(async error => { console.error(error); await browser?.close(); process.exitCode = 1; });
