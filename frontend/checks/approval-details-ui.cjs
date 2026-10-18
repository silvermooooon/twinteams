/** Exact frozen approval action visibility; synthetic API/WS, no decisions or model. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5199';
const tools = ['send_chat_message', 'create_chat', 'rename_chat', 'add_chat_members', 'create_reminder', 'update_reminder', 'create_tracking', 'update_tracking', 'delete_tracking', 'add_tracking_update', 'save_summary', 'cancel_reply_request', 'retry_reply_request'];
let browser;
async function scenario(locale, width) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage(); const errors = []; page.on('pageerror', error => errors.push(error.message));
  const mock = await fixture(page, locale);
  const approvals = tools.map((tool, index) => ({ id: 'approval-' + index, chat_id: mock.chat.id, approver_id: 'owner-alex', title: 'Review ' + tool, description: 'One exact platform action', target: 'Authorized target', impact: 'Private execution environment', status: 'pending', created_at: '2026-10-18T00:00:00Z', action: { tool, arguments: { id: 'frozen-' + index, body: '<img src=x onerror=alert(1)>', nested: { text: 'long-unbroken-value-'.repeat(45) } } } }));
  approvals.push({ ...approvals[0], id: 'legacy-approval', title: 'Legacy approval', action: undefined });
  approvals.push({ ...approvals[0], id: 'other-owner-approval', approver_id: 'owner-maya', title: 'OTHER OWNER MUST NOT RENDER' });
  await page.route('**/api/chats/private-alex?**', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ revision: 1, chat: mock.chat, messages: [], requests: [], approvals, tracking: [], settings: { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'workspace' }, members: mock.actors }) }));
  await page.goto(base + '/#chat');
  if (width < 600) await page.locator('.conversation-list .chat-item').filter({ hasText: mock.chat.title }).click();
  const drawer = page.locator('.approvals-drawer'); await drawer.waitFor();
  assert.equal(await drawer.getByRole('tab').count(), 14, 'nonapprover cards remain filtered');
  assert.equal(await page.getByText('OTHER OWNER MUST NOT RENDER', { exact: true }).count(), 0);
  for (let index = 0; index < tools.length; index++) {
    await drawer.getByRole('tab').nth(index).click();
    await drawer.getByRole('button', { name: 'View details', exact: true }).click();
    assert.equal(await drawer.locator('code').innerText(), tools[index]);
    assert.deepEqual(JSON.parse(await drawer.locator('.approval-arguments').innerText()), approvals[index].action.arguments);
    assert.equal(await drawer.locator('.approval-arguments img').count(), 0, 'arguments render as escaped React text');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'page remains within viewport');
    const bounds = await drawer.locator('.approval-arguments').boundingBox(); assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width);
  }
  await drawer.getByRole('tab').nth(13).click();
  await drawer.getByRole('button', { name: 'View details', exact: true }).click();
  assert.equal(await drawer.locator('h3').innerText(), 'Legacy approval'); assert.equal(await drawer.locator('.approval-arguments').count(), 0);
  assert.equal(mock.calls.filter(call => call.path.includes('/decision')).length, 0);
  assert.deepEqual(errors, []); await context.close();
  console.log('PASS ' + locale + ' ' + width + 'px: 13 exact action details, legacy compatibility, nonapprover exclusion, escaped text and no overflow');
}
async function run() { browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' }); await scenario('en', 1360); await scenario('zh-CN', 390); await browser.close(); }
run().catch(async error => { console.error(error); await browser?.close(); process.exitCode = 1; });
