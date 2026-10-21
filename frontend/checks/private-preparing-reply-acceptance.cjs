/** Independent private queued→running UI acceptance. All API/socket data is synthetic. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5198';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const report = []; let browser;
async function until(check, message) {
  for (let i = 0; i < 100; i++) { if (await check()) return; await sleep(20); }
  throw new Error(message);
}
async function setup(width, kind = 'private') {
  const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce', locale: 'zh-CN' });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const mock = await fixture(page, 'zh-CN', true);
  const chat = kind === 'private' ? mock.chat : mock.group;
  const messages = [1, 2, 3].map(number => ({ id: `m${number}`, chat_id: chat.id, seq: number,
    author_id: 'owner-alex', kind: 'human', body: `Synthetic owner message ${number}`, state: 'published', read_by: [], created_at: '2026-10-21T00:00:00Z' }));
  const request = (id, source, accepted) => ({ id, chat_id: chat.id, source_message_id: source,
    target_twin_id: 'twin-alex', requester_id: 'owner-alex', source: 'private', status: 'queued',
    accepted_at: accepted, cancel_requested: false, can_cancel: true, can_retry: false });
  const first = request('r1', 'm1', '2026-10-21T00:00:01Z');
  const second = request('r2', 'm2', '2026-10-21T00:00:02Z');
  const third = request('r3', 'm3', '2026-10-21T00:00:02Z');
  const state = { requests: [first], revision: 1, sockets: [], stops: [], releaseStop: null };
  await page.routeWebSocket('**/api/ws', socket => {
    state.sockets.push(socket);
    socket.onMessage(raw => {
      const frame = JSON.parse(String(raw)); if (frame.type !== 'command') return;
      const ack = () => socket.send(JSON.stringify({ v: 1, type: 'ack', command_id: frame.command_id }));
      if (frame.command === 'stop') {
        state.stops.push(frame.payload.request_id);
        state.releaseStop = () => { state.requests = state.requests.filter(item => item.id !== frame.payload.request_id); state.revision++; ack(); };
      } else ack();
    });
  });
  await page.route('**/api/chats/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === `/api/chats/${chat.id}`) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({
      revision: state.revision, chat, messages, requests: state.requests, approvals: [], tracking: [], members: mock.actors,
      settings: { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'activity' } }) });
    return route.fallback();
  });
  await page.goto(base + '/#chat'); await page.locator('.composer textarea').waitFor({ state: 'attached' });
  if (kind !== 'private' || width < 600) await page.locator('.conversation-list .chat-item').filter({ hasText: chat.title }).click();
  await page.locator('[data-message-id=m1]').waitFor(); await page.locator('.connection-state.connected').waitFor({ state: 'attached' });
  const refresh = async () => {
    state.revision++;
    state.sockets.at(-1).send(JSON.stringify({ v: 1, type: 'changed', chat_id: chat.id, revision: state.revision }));
  };
  const finish = async name => {
    assert.deepEqual(errors, []); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const assets = await page.locator('script[src]').evaluateAll(nodes => nodes.map(node => new URL(node.src).pathname));
    await page.screenshot({ path: `/tmp/twinteams-private-preparing-${name}-${width}.png` });
    report.push({ name, width, assets, errors }); await context.close(); console.log(`PASS ${name} ${width}px`);
  };
  return { page, state, first, second, third, messages, refresh, finish };
}

async function lifecycle(width) {
  const { page, state, first, messages, refresh, finish } = await setup(width);
  const row = page.locator('.typing-row');
  await row.getByText('Preparing a reply…', { exact: true }).waitFor();
  assert.equal(await row.getAttribute('data-request-id'), first.id);
  assert.equal(await row.getAttribute('data-reply-phase'), 'preparing');
  assert.equal(await row.getAttribute('title'), 'Accepted. Waiting to start.');
  assert.equal(await row.locator('.typing-dots').count(), 0);
  assert.equal(await row.getByRole('button', { name: 'Cancel this request', exact: true }).count(), 1);
  assert.equal(await page.locator('.owner-queue, .message-request-chips .queue-chip').count(), 0);
  const input = page.locator('.composer textarea'); await input.fill('Keep this private draft'); await input.focus();
  await input.evaluate(node => node.setSelectionRange(5, 9));
  await page.screenshot({ path: `/tmp/twinteams-private-preparing-preparing-${width}.png` });
  const before = await input.boundingBox();
  await row.evaluate(node => { window.__privateStatusRow = node; });
  state.requests = [{ ...first, status: 'running' }];
  const started = Date.now(); await refresh(); await row.getByText('Your twin is typing', { exact: true }).waitFor();
  assert.ok(Date.now() - started < 1500, 'private status must not introduce a 10-second delay');
  assert.equal(await row.getAttribute('data-reply-phase'), 'running');
  assert.equal(await row.evaluate(node => window.__privateStatusRow === node), true, 'queued→running must preserve the status row');
  assert.equal(await row.locator('.typing-dots').count(), 1);
  assert.equal(await page.locator('.message-request-chips .queue-chip').count(), 0);
  const after = await input.boundingBox(); assert.ok(Math.abs(before.y - after.y) < 1, 'queued→running must retain composer placement');
  assert.equal(await input.evaluate(node => document.activeElement === node && node.selectionStart === 5 && node.selectionEnd === 9), true);
  assert.equal(await input.inputValue(), 'Keep this private draft');
  await page.screenshot({ path: `/tmp/twinteams-private-preparing-running-${width}.png` });
  const focusedMessage = page.locator('[data-message-id=m1]'); await focusedMessage.focus();
  state.requests = []; messages.push({ ...messages[0], id: 'completed-output', seq: 4, author_id: 'twin-alex', kind: 'twin', body: 'Verified synthetic completed reply' });
  await refresh(); await page.locator('[data-message-id=completed-output]').waitFor(); await row.waitFor({ state: 'hidden' });
  assert.equal(await focusedMessage.evaluate(node => document.activeElement === node), true, 'completion must not steal focus from another message');
  assert.equal(await input.inputValue(), 'Keep this private draft');
  await finish('lifecycle');
}

async function queuedCancelFail(width) {
  const { page, state, first, second, third, refresh, finish } = await setup(width);
  state.requests = [third, second, first]; await refresh();
  const row = page.locator('.typing-row'); await row.getByText('Preparing a reply…', { exact: true }).waitFor();
  await until(async () => await page.locator('.owner-queue .queue-chip').count() === 2, 'two real successors must remain queued');
  assert.equal(await row.getAttribute('data-request-id'), 'r1');
  assert.equal(await page.locator('.message-request-chips .queue-chip').count(), 0);
  await page.locator('.owner-queue .queue-chip').first().getByRole('button', { name: 'Cancel this request' }).click();
  await until(() => state.stops.length === 1, 'queue cancellation must send a stop command');
  assert.equal(state.stops[0], 'r2', 'equal timestamps must preserve backend FIFO ordering');
  state.releaseStop(); await until(async () => await page.locator('.owner-queue .queue-chip').count() === 1, 'cancelled successor must disappear');
  await row.getByRole('button', { name: 'Cancel this request', exact: true }).click();
  await until(() => state.stops.length === 2, 'preparing cancellation must send stop');
  assert.equal(state.stops[1], 'r1'); await row.getByText('Stopping…', { exact: true }).waitFor();
  state.releaseStop(); await until(async () => await row.getAttribute('data-request-id') === 'r3', 'next queued request must become the preparing head');
  state.requests = [{ ...third, status: 'failed', can_retry: true, can_cancel: false, error: 'Synthetic failure' }];
  await refresh(); await row.waitFor({ state: 'hidden' });
  await page.locator('[data-message-id=m3] .failed-chip').getByRole('button', { name: 'Retry', exact: true }).waitFor();
  assert.equal(await page.locator('.owner-queue .queue-chip').count(), 0);
  await finish('queue-cancel-fail');
}

async function groupUnchanged(width) {
  const { page, state, first, refresh, finish } = await setup(width, 'group');
  await page.locator('.owner-queue .queue-chip').waitFor();
  assert.equal(await page.locator('.typing-row').count(), 0);
  assert.equal(await page.getByText('Preparing a reply…', { exact: true }).count(), 0);
  assert.equal(await page.locator('[data-message-id=m1] .queue-chip').count(), 1);
  state.requests = [{ ...first, status: 'running' }]; await refresh();
  await page.locator('.typing-row').getByText('Your twin is typing', { exact: true }).waitFor();
  await page.locator('.typing-row').getByRole('button', { name: 'Stop replying', exact: true }).waitFor();
  await finish('group-unchanged');
}
async function memoryHead(width) {
  const { page, state, first, second, refresh, finish } = await setup(width);
  const dream = { ...first, source: 'memory_dream', source_message_id: null };
  state.requests = [second, dream]; await refresh();
  const row = page.locator('.typing-row');
  await row.getByText('Preparing memory review…', { exact: true }).waitFor();
  assert.equal(await row.getAttribute('data-request-id'), 'r1');
  assert.equal(await page.getByText('Preparing a reply…', { exact: true }).count(), 0);
  assert.equal(await page.locator('.owner-queue .queue-chip').count(), 1);
  const input = page.locator('.composer textarea'); await input.fill('Draft during memory review'); await input.focus();
  await row.evaluate(node => { window.__memoryStatusRow = node; });
  await page.screenshot({ path: `/tmp/twinteams-private-preparing-memory-queued-${width}.png` });
  state.requests = [second, { ...dream, status: 'running' }]; await refresh();
  await row.getByText('Organizing memory…', { exact: true }).waitFor();
  assert.equal(await row.evaluate(node => window.__memoryStatusRow === node), true);
  assert.equal(await page.getByText('Your twin is typing', { exact: true }).count(), 0);
  state.requests = [second]; await refresh();
  await row.getByText('Preparing a reply…', { exact: true }).waitFor();
  assert.equal(await row.getAttribute('data-request-id'), 'r2');
  assert.equal(await page.locator('.owner-queue .queue-chip').count(), 0);
  assert.equal(await input.inputValue(), 'Draft during memory review');
  assert.equal(await input.evaluate(node => document.activeElement === node), true);
  await finish('memory-to-private');
}
async function run() {
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  for (const width of [1360, 390]) { await lifecycle(width); await queuedCancelFail(width); await groupUnchanged(width); await memoryHead(width); }
  fs.writeFileSync('/tmp/twinteams-private-preparing-report.json', JSON.stringify({ base, synthetic_only: true, report }, null, 2));
  await browser.close();
}
run().catch(async error => { console.error(error); await browser?.contexts().at(-1)?.pages().at(-1)?.screenshot({ path: '/tmp/twinteams-private-preparing-failure.png' }).catch(() => {}); await browser?.close(); process.exitCode = 1; });
