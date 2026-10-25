/** Owner-local quick preferences, tone, gallery keyboard and touch; API/WS synthetic. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5199';
let browser;
async function setup(width, locale = 'en', reducedMotion = 'reduce') {
  const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion, hasTouch: width < 600 });
  const page = await context.newPage(); const mock = await fixture(page, locale, true); const errors = []; page.on('pageerror', error => errors.push(error.message));
  const message = { id: 'prefs-message', chat_id: mock.chat.id, seq: 1, author_id: 'twin-alex', kind: 'twin', body: 'Synthetic reaction reference', state: 'published', created_at: '2026-10-18T00:00:00Z', read_by: [], reactions: [] };
  const requests = [];
  await page.route('**/api/chats/private-alex?**', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ revision: 1, chat: mock.chat, messages: [message], requests: [], approvals: [], tracking: [], members: mock.actors, settings: { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'workspace' } }) }));
  await page.route('**/api/chats/private-alex/messages/prefs-message/reactions', async route => {
    const data = route.request().postDataJSON(); requests.push(data);
    message.reactions = data.active ? [{ emoji: data.emoji, count: 1, actors: [mock.actors[0]], mine: true }] : [];
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ chat_id: mock.chat.id, message_id: message.id, reactions: message.reactions, revision: 1 }) });
  });
  await page.goto(base + '/#chat'); await page.locator('[data-message-id=prefs-message]').waitFor();
  if (width < 600) await page.locator('.conversation-list .chat-item').filter({ hasText: mock.chat.title }).click();
  const row = page.locator('[data-message-id=prefs-message]');
  if (width < 600) {
    const body = row.locator('.message-bubble'); await body.tap();
    assert.equal(await row.evaluate(el => el.classList.contains('touch-actions')), false, 'normal touch tap must not open reactions');
    const box = await body.boundingBox(); const x = box.x + box.width / 2, y = box.y + box.height / 2;
    const cdp = await context.newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
    await page.waitForTimeout(500); await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await page.waitForTimeout(50); assert.equal(await row.evaluate(el => el.classList.contains('touch-actions')), true);
  } else await row.focus();
  return { context, page, row, mock, requests, errors };
}
async function desktop() {
  const { context, page, row, requests, errors } = await setup(1360);
  assert.equal(await row.getByRole('button', { name: 'Ask my Duet', exact: true }).count(), 0);
  await row.getByRole('button', { name: 'More reactions', exact: true }).click();
  const picker = page.locator('.reaction-picker'); await picker.getByLabel('Search emoji', { exact: true }).waitFor();
  assert.equal(await picker.getByRole('tab').count(), 10);
  await picker.getByLabel('Search emoji', { exact: true }).fill('拇指向上');
  await picker.locator('.emoji-option').waitFor(); assert.equal(await picker.locator('.emoji-option').count(), 1);
  await picker.locator('.emoji-option').focus(); await page.keyboard.press('Enter'); await picker.waitFor({ state: 'hidden' });
  assert.equal(requests.at(-1).emoji, '👍');
  await row.getByRole('button', { name: 'More reactions', exact: true }).click();
  await picker.getByRole('button', { name: 'Customize default reactions', exact: true }).click();
  let dialog = page.getByRole('dialog'); await dialog.getByRole('button', { name: 'Quick reaction 5', exact: true }).click();
  await dialog.getByLabel('Search emoji', { exact: true }).fill('party popper'); await dialog.locator('.emoji-option').click();
  await dialog.getByRole('button', { name: 'Skin tone 3', exact: true }).click();
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.equal(await row.locator('.reaction-quick-button').count(), 4, 'cancel must not mutate preferences');
  await picker.getByRole('button', { name: 'Customize default reactions', exact: true }).click();
  dialog = page.getByRole('dialog'); await dialog.getByRole('button', { name: 'Quick reaction 5', exact: true }).click();
  await dialog.getByLabel('Search emoji', { exact: true }).fill('party popper'); await dialog.locator('.emoji-option').click();
  await dialog.getByRole('button', { name: 'Skin tone 3', exact: true }).click(); await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await page.keyboard.press('Escape'); await picker.waitFor({ state: 'hidden' });
  assert.equal(await row.locator('.reaction-quick-button').count(), 5);
  const preference = await page.evaluate(() => JSON.parse(localStorage.getItem('twinteams.reactions:owner-alex')));
  assert.equal(preference.quick.length, 5); assert.equal(preference.tone, 3); assert.equal(preference.quick[4], '🎉');
  await row.getByRole('button', { name: 'React 👍🏽', exact: true }).click(); assert.equal(requests.at(-1).emoji, '👍🏽');
  await page.reload(); await row.waitFor(); assert.equal(await row.locator('.reaction-quick-button').count(), 5);
  const input = page.locator('.composer textarea'); await input.fill('Keep this draft'); await input.focus();
  await page.evaluate(() => { window.__reactionFocus = document.activeElement; });
  assert.equal(await input.evaluate(el => document.activeElement === el), true);
  assert.deepEqual(errors, []); await context.close();
  console.log('PASS full gallery English/Chinese search, keyboard selection, 10 categories, cancel/save 5 quick slots and tone, owner-local persistence');
}
async function mobile() {
  const { context, page, row, errors } = await setup(390, 'zh-CN');
  const bar = row.locator('.reaction-actions'); const bounds = await bar.boundingBox(); assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
  await row.getByRole('button', { name: 'More reactions', exact: true }).tap();
  const picker = page.locator('.reaction-picker'); await picker.getByLabel('Search emoji', { exact: true }).fill('红心');
  await picker.locator('.emoji-option').first().waitFor(); const popupBounds = await picker.boundingBox(); assert.ok(popupBounds.x >= 0 && popupBounds.x + popupBounds.width <= 390);
  await picker.locator('.emoji-option').first().tap(); await picker.waitFor({ state: 'hidden' });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.equal(await row.locator('.reaction-chip img').evaluate(el => el.currentSrc.includes('/animated-')), false, 'reduced motion uses static source');
  await page.screenshot({ path: '/tmp/twinteams-reactions-mobile.png', fullPage: true });
  assert.deepEqual(errors, []); await context.close(); console.log('PASS 390px English touch toolbar/picker with a legacy locale preference, static reduced-motion reaction and no overflow');
}
async function run() { browser = await chromium.launch({ headless: true, channel: 'chrome' }); await desktop(); await mobile(); await browser.close(); }
run().catch(async error => { console.error(error); await browser?.close(); process.exitCode = 1; });
