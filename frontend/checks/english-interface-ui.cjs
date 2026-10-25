/** Independent English-copy acceptance; every API/WS uses synthetic fixtures. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5199';
const chineseMessage = '用户原始消息保持中文';
const chineseSoul = '用户自己的偏好保持中文';
const han = /\p{Script=Han}/u;
let browser;

async function englishSurface(page, allowed = []) {
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  let text = await page.locator('body').innerText();
  for (const value of allowed) text = text.replaceAll(value, '');
  assert.equal(han.test(text), false, `Unexpected non-English product text: ${text}`);
  const attributes = await page.locator('[aria-label], [title], [placeholder], img[alt]').evaluateAll(nodes =>
    nodes.flatMap(node => ['aria-label', 'title', 'placeholder', 'alt'].map(key => node.getAttribute(key) || '')));
  for (const value of attributes) assert.equal(han.test(value), false, `Unexpected product attribute: ${value}`);
}

async function run() {
  browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
  for (const width of [1360, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, locale: 'zh-CN', timezoneId: 'UTC', reducedMotion: 'reduce' });
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const mock = await fixture(page, 'zh-CN', true);
    mock.remoteSoul(chineseSoul);
    const message = { id: 'english-copy-message', chat_id: mock.chat.id, seq: 1, author_id: 'twin-alex', kind: 'twin', body: chineseMessage, state: 'published', created_at: '2026-10-18T00:00:00Z', read_by: [], reactions: [] };
    await page.route('**/api/chats/private-alex?**', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ revision: 1, chat: mock.chat, messages: [message], requests: [], approvals: [], tracking: [], members: mock.actors, settings: { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'workspace' } }) }));
    await page.goto(base + '/#chat');
    await page.locator('[data-message-id=english-copy-message]').waitFor();
    if (width < 600) await page.locator('.conversation-list .chat-item').filter({ hasText: mock.chat.title }).click();
    await page.getByText(chineseMessage, { exact: true }).waitFor();
    await englishSurface(page, [chineseMessage]);
    assert.equal(await page.locator('.date-divider').innerText(), 'October 18');
    const row = page.locator('[data-message-id=english-copy-message]');
    await row.focus();
    await row.getByRole('button', { name: 'More reactions', exact: true }).click({ force: true });
    const picker = page.locator('.reaction-picker');
    await picker.getByLabel('Search emoji', { exact: true }).fill('拇指向上');
    const emoji = picker.locator('.emoji-option'); await emoji.waitFor();
    assert.equal(await emoji.count(), 1);
    assert.equal(await emoji.getAttribute('title'), 'thumbs up');
    assert.equal(await emoji.getAttribute('aria-label'), 'thumbs up 👍');
    await englishSurface(page, [chineseMessage]);
    await page.keyboard.press('Escape');
    for (const [route, name] of [['profile', 'Profile'], ['soul', 'Soul'], ['runtime', 'Runtime'], ['skills', 'Skills'], ['memory', 'Memory'], ['credentials', 'Credentials']]) {
      await page.goto(`${base}/#/my-twin/${route}`);
      await page.getByRole('tab', { name, exact: true }).waitFor();
      assert.equal(await page.getByRole('tab', { name, exact: true }).getAttribute('aria-selected'), 'true');
      await englishSurface(page, [chineseSoul]);
      if (route === 'soul') assert.equal(await page.getByLabel('Soul content', { exact: true }).inputValue(), chineseSoul);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    }
    const writes = mock.calls.filter(call => !['GET', 'HEAD'].includes(call.method) && call.path !== '/api/presence' && !call.path.endsWith('/read'));
    assert.deepEqual(writes.map(call => ({ method: call.method, path: call.path })), [], 'acceptance only reads fixture content and updates read receipts');
    assert.deepEqual(errors, []);
    await context.close();
    console.log(`PASS English interface ${width}px: old locale ignored, language/dates, six Duet tabs, English emoji names with Chinese search, Chinese user content preserved; synthetic reads only.`);
  }
  await browser.close();
}
run().catch(async error => { console.error(error); await browser?.close(); process.exitCode = 1; });
