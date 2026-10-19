/** Independent compiled-UI Markdown acceptance. All API/WS content is synthetic. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5198';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser;
const results = [];
const longCell = 'wide_table_cell_'.repeat(20);
const longCode = `const html = '<img src=x onerror="window.__markdownXss=true">';\nconst longValue = '${'long_code_value_'.repeat(50)}';`;
const rich = [
  '## Markdown works', '', '**Bold value** and *emphasis* and ~~deleted value~~.', '',
  '- First list item', '- Second list item', '', '3. Ordered third', '4. Ordered fourth', '',
  '- [x] Finished task', '- [ ] Pending task', '', '> A quoted paragraph', '',
  'Inline `a < b && x > y` stays literal.', '', '```javascript', longCode, '```', '',
  '| Column A | Column B | Column C | Column D | Column E | Column F |',
  '| --- | --- | --- | --- | --- | --- |',
  `| ${longCell} | ${longCell} | ${longCell} | ${longCell} | ${longCell} | ${longCell} |`, '',
  '[Safe external](https://example.test/reference) and https://example.test/autolink', '',
  '![Explicit image link](https://markdown.test/explicit-image.png)', '',
  '[![Linked image alt](https://markdown.test/linked-image.png)](https://example.test/image-destination)', '',
  '**中文原文保留**',
].join('\n');
const unsafe = [
  '<script>window.__markdownXss = true</script>', '',
  '<img src="https://markdown.test/forbidden-raw.png" onerror="window.__markdownXss=true">', '',
  '<iframe src="javascript:window.__markdownXss=true"></iframe>', '',
  '[Unsafe JS](javascript:window.__markdownXss=true)',
  '[Unsafe mixed](JaVaScRiPt:window.__markdownXss=true)',
  '[Unsafe data](data:text/html;base64,PHNjcmlwdD53aW5kb3cuX194c3M9dHJ1ZTwvc2NyaXB0Pg==)',
  '[Unsafe VB](vbscript:window.__markdownXss=true)', '',
  '![Unsafe data image](data:image/svg+xml;base64,PHN2Zy8+)',
].join('\n');

async function scenario(kind, width) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce', locale: 'zh-CN' });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  const errors = []; const forbiddenRawRequests = []; const frames = []; const sockets = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => { window.__markdownXss = false; });
  const mock = await fixture(page, 'zh-CN', true);
  const chatId = kind === 'private' ? 'private-alex' : 'group-shared';
  const chat = kind === 'private' ? mock.chat : mock.group;
  let revision = 1;
  const message = (id, author, messageKind, body, extra = {}) => ({ id, chat_id: chatId, seq: 1, author_id: author, kind: messageKind, body, read_by: [], state: 'published', created_at: '2026-10-19T00:00:00Z', ...extra });
  const messages = [
    message('markdown-human', 'owner-alex', 'human', '**Human strong** and `human-code`', { seq: 1 }),
    message('markdown-twin', 'twin-alex', 'twin', rich, { seq: 2 }),
    message('markdown-generated', 'twin-alex', 'reply_for_me', '### Generated heading\n\n**Generated strong**', { seq: 3, raw_owner_input: '**raw source stays literal**\n<script>window.__markdownXss=true</script>', reply_to: { id: 'markdown-human', author_name: 'AAA', body: '**Quoted source stays literal**' } }),
    message('markdown-unsafe', 'owner-alex', 'human', unsafe, { seq: 4 }),
  ];
  await page.routeWebSocket('**/api/ws', socket => {
    sockets.push(socket);
    socket.onMessage(raw => { const frame = JSON.parse(String(raw)); if (frame.type !== 'command') return; if (frame.command === 'send') frames.push(frame); else socket.send(JSON.stringify({ v: 1, type: 'ack', command_id: frame.command_id })); });
  });
  await page.route('https://markdown.test/**', route => { forbiddenRawRequests.push(route.request().url()); return route.abort(); });
  await page.route('**/api/chats/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === `/api/chats/${chatId}`) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ revision, chat, messages, requests: [], approvals: [], tracking: [], members: kind === 'private' ? mock.actors.slice(0, 2) : mock.actors, settings: { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'workspace' } }) });
    return route.fallback();
  });
  await page.goto(base + '/#chat');
  await page.locator('.composer textarea').waitFor({ state: 'attached' });
  if (kind === 'group' || width < 600) await page.locator('.conversation-list .chat-item').filter({ hasText: chat.title }).click();
  await page.locator('.composer textarea').waitFor();
  await page.locator('.connection-state.connected').waitFor({ state: 'attached' });
  const human = page.locator('[data-message-id=markdown-human]');
  const twin = page.locator('[data-message-id=markdown-twin]');
  const generated = page.locator('[data-message-id=markdown-generated]');
  const malicious = page.locator('[data-message-id=markdown-unsafe]');
  await twin.locator('h2').getByText('Markdown works', { exact: true }).waitFor();
  assert.equal(await twin.locator('.markdown-content').evaluate(node => getComputedStyle(node).whiteSpace), 'normal', 'the plain-text message style must not preserve parser layout whitespace between Markdown blocks');
  assert.equal(await human.locator('.markdown-content strong').innerText(), 'Human strong');
  assert.equal(await human.locator('.markdown-content code').innerText(), 'human-code');
  assert.equal(await twin.locator('.markdown-content strong').getByText('Bold value', { exact: true }).count(), 1);
  assert.equal(await twin.locator('em').innerText(), 'emphasis');
  assert.equal(await twin.locator('del').innerText(), 'deleted value');
  assert.equal((await twin.locator('blockquote').innerText()).trim(), 'A quoted paragraph');
  assert.equal(await twin.locator('ol').getAttribute('start'), '3');
  assert.equal(await twin.locator('input[type=checkbox]').count(), 2);
  assert.equal(await twin.locator('input[type=checkbox]:disabled').count(), 2);
  assert.equal(await twin.locator('input[type=checkbox]:checked').count(), 1);
  assert.equal(await twin.locator('pre > code').textContent(), longCode + '\n');
  assert.equal(await twin.locator('pre img').count(), 0);
  assert.equal(await twin.locator('table thead th').count(), 6);
  const table = twin.locator('.markdown-table-scroll');
  assert.equal(await table.getAttribute('aria-label'), 'Message table');
  await table.focus(); assert.equal(await table.evaluate(node => document.activeElement === node), true);
  assert.equal(await table.evaluate(node => getComputedStyle(node).overflowX), 'auto');
  if (width < 600) assert.equal(await table.evaluate(node => node.scrollWidth > node.clientWidth), true);
  await twin.locator('pre').focus(); assert.equal(await twin.locator('pre').evaluate(node => document.activeElement === node), true);
  assert.equal(await twin.locator('pre').evaluate(node => node.scrollWidth > node.clientWidth), true);
  const link = twin.getByRole('link', { name: 'Safe external', exact: true });
  assert.equal(await link.getAttribute('href'), 'https://example.test/reference');
  assert.equal(await link.getAttribute('target'), '_blank');
  assert.match(await link.getAttribute('rel'), /noopener/); assert.match(await link.getAttribute('rel'), /noreferrer/);
  assert.equal(await twin.getByRole('link', { name: 'https://example.test/autolink', exact: true }).count(), 1);
  assert.equal(await twin.getByRole('link', { name: 'Explicit image link', exact: true }).getAttribute('href'), 'https://markdown.test/explicit-image.png');
  assert.equal(await twin.locator('a a').count(), 0, 'linked-image Markdown must not nest interactive anchors');
  assert.equal(await twin.getByRole('link', { name: 'Linked image alt', exact: true }).getAttribute('href'), 'https://example.test/image-destination');
  assert.equal(await twin.locator('.markdown-content img').count(), 0, 'Markdown images must stay explicit links instead of auto-fetching resources');
  assert.equal(await twin.locator('strong').getByText('中文原文保留', { exact: true }).count(), 1);
  assert.equal(await generated.locator('h3').innerText(), 'Generated heading');
  assert.equal(await generated.locator('.raw-input p').textContent(), '**raw source stays literal**\n<script>window.__markdownXss=true</script>');
  assert.equal(await generated.locator('.raw-input strong, .raw-input script').count(), 0);
  assert.equal(await generated.locator('.source-quote span').textContent(), '**Quoted source stays literal**');
  assert.equal(await generated.locator('.source-quote span strong').count(), 0);
  assert.equal(await malicious.locator('script, iframe, img[onerror], [onclick], [onload]').count(), 0);
  const unsafeUrls = await malicious.locator('[href], [src]').evaluateAll(nodes => nodes.map(node => node.getAttribute('href') || node.getAttribute('src') || '').filter(value => /^(javascript|vbscript|data):/i.test(value)));
  assert.deepEqual(unsafeUrls, []);
  assert.equal(await malicious.getByRole('link', { name: /^Unsafe/ }).count(), 0);
  assert.equal(await page.evaluate(() => window.__markdownXss), false);
  assert.deepEqual(forbiddenRawRequests, [], 'literal HTML must not initiate image or iframe requests');
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: `/tmp/twinteams-markdown-${kind}-${width}.png`, fullPage: true });
  await twin.locator('h2').evaluate(node => node.scrollIntoView({ block: 'start' }));
  await page.screenshot({ path: `/tmp/twinteams-markdown-${kind}-${width}-overview.png`, fullPage: true });

  const pendingBody = '**Pending Markdown**\n\n- Local item\n\n```ts\nconst local = true;\n```';
  const input = page.locator('.composer textarea'); await input.fill(pendingBody); await page.locator('.send-button').click();
  const pending = page.locator('article[data-local-send]');
  await pending.locator('strong').getByText('Pending Markdown', { exact: true }).waitFor();
  assert.equal(await pending.locator('li').innerText(), 'Local item');
  assert.equal(await pending.locator('pre > code').textContent(), 'const local = true;\n');
  assert.equal(await pending.locator('.read-receipt, .message-actions').count(), 0);
  assert.equal(await input.inputValue(), ''); await input.fill('Preserved later draft');
  for (let i = 0; !frames.length && i < 30; i++) await sleep(20);
  assert.equal(frames.length, 1);
  assert.equal(frames[0].payload.body, pendingBody, 'rendering must not mutate transmitted Markdown');
  const canonical = message('markdown-local-canonical', 'owner-alex', 'human', pendingBody, { seq: 5, client_request_key: frames[0].payload.client_request_key });
  messages.push(canonical); revision++;
  const socket = sockets.at(-1);
  socket.send(JSON.stringify({ v: 1, type: 'ack', command_id: frames[0].command_id, result: { message: { id: canonical.id, chat_id: chatId, seq: canonical.seq, created_at: canonical.created_at, state: canonical.state, client_request_key: canonical.client_request_key }, request: null } }));
  socket.send(JSON.stringify({ v: 1, type: 'changed', chat_id: chatId, revision }));
  await page.locator('[data-message-id=markdown-local-canonical]').waitFor();
  assert.equal(await pending.count(), 0);
  assert.equal(await page.locator('.message-text strong').getByText('Pending Markdown', { exact: true }).count(), 1);
  assert.equal(await input.inputValue(), 'Preserved later draft');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.deepEqual(errors, []);
  const assets = await page.locator('script[src]').evaluateAll(nodes => nodes.map(node => new URL(node.src).pathname));
  results.push({ kind, width, canonical_human_twin_generated: true, local_echo_and_replacement: true, raw_and_quotes_literal: true, safe_html_and_urls: true, keyboard_scrollable_code_table: true, overflow: false, assets, errors });
  await context.close(); console.log(`PASS Markdown ${kind} ${width}px: semantics, safe HTML/URLs, literal source, code/table scrolling, local→canonical, English UI.`);
}
async function run() {
  browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
  for (const kind of ['private', 'group']) for (const width of [1360, 390]) await scenario(kind, width);
  fs.writeFileSync('/tmp/twinteams-markdown-browser-report.json', JSON.stringify({ base, synthetic_api_ws: true, production_messages: false, results }, null, 2));
  await browser.close();
}
run().catch(async error => { console.error(error); const page = browser?.contexts().at(-1)?.pages().at(-1); await page?.screenshot({ path: '/tmp/twinteams-markdown-failure.png', fullPage: true }).catch(() => {}); await browser?.close(); process.exitCode = 1; });
