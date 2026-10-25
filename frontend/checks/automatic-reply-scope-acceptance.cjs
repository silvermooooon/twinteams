/** Independent compiled UI checks; every API and socket is synthetic. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5198';
const results = []; let browser;

async function scenario(kind, width) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, locale: 'zh-CN', reducedMotion: 'reduce' });
  const page = await context.newPage(); page.setDefaultTimeout(6000);
  const errors = []; const frames = []; const writes = [];
  page.on('pageerror', error => errors.push(error.message));
  const mock = await fixture(page, 'zh-CN', true);
  const privateChat = mock.chat;
  const pair = { ...mock.group, id: 'group-pair', title: 'Maya', member_owner_ids: ['owner-alex', 'owner-maya'] };
  const chats = [privateChat, pair, mock.group];
  const chat = kind === 'private' ? privateChat : kind === 'pair' ? pair : mock.group;
  let settings = { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'tracking' };
  const message = { id: 'source-' + kind, chat_id: chat.id, seq: 1, author_id: 'owner-alex', kind: 'human', body: 'Synthetic message · 中文原文', state: 'published', read_by: [], created_at: '2026-10-20T00:00:00Z' };
  const requests = [
    { id: 'own-failed', chat_id: chat.id, source_message_id: message.id, target_twin_id: 'twin-alex', requester_id: 'owner-maya', status: 'failed', source: 'auto', can_retry: true, can_cancel: false, accepted_at: message.created_at },
    ...(kind === 'private' ? [] : [{ id: 'peer-failed', chat_id: chat.id, source_message_id: message.id, target_twin_id: 'twin-maya', requester_id: 'owner-alex', status: 'failed', source: 'auto', can_retry: false, can_cancel: false, accepted_at: message.created_at }]),
  ];
  const paused = kind === 'private' ? [] : [{ id: 'chain', causal_chain_id: 'chain', status: 'paused', reason: 'budget_exhausted', spent: 6, allowance: 6, remaining: 0, paused_pending_count: 1, continuation_allowance: 6, can_continue: true, source_owner_id: 'owner-alex' }];
  await page.routeWebSocket('**/api/ws', socket => socket.onMessage(raw => {
    const frame = JSON.parse(String(raw)); if (frame.type !== 'command') return;
    frames.push(frame); socket.send(JSON.stringify({ v: 1, type: 'ack', command_id: frame.command_id }));
  }));
  await page.route('**/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    const reply = value => route.fulfill({ contentType: 'application/json', body: JSON.stringify(value) });
    if (request.method() !== 'GET') writes.push({ path, method: request.method(), body: request.postDataJSON() });
    if (path === '/api/bootstrap') return reply({ current_owner_id: 'owner-alex', actors: mock.actors, chats, reminders: [], model_configured: true, development_mode: true, acceptance_fixture: true });
    if (path === `/api/chats/${chat.id}`) return reply({ revision: 1, chat, messages: [message], requests, approvals: [], tracking: [], members: mock.actors, settings, automatic_conversations: paused });
    if (path === `/api/chats/${chat.id}/settings`) { settings = { ...settings, ...request.postDataJSON() }; return reply(settings); }
    if (path.endsWith('/automatic-conversations/chain/continue')) return reply({ id: 'chain', allowance: 12, spent: 6, remaining: 6 });
    return route.fallback();
  });
  await page.goto(base + '/#chat');
  await page.locator('.composer textarea').waitFor({ state: 'attached' });
  if (kind !== 'private' || width < 600) await page.locator('.conversation-list .chat-item').filter({ hasText: chat.title }).click();
  const row = page.locator(`[data-message-id="${message.id}"]`); await row.waitFor();
  await page.locator('.connection-state.connected').waitFor({ state: 'attached' });
  await row.focus();
  assert.equal(await page.getByRole('button', { name: /Ask my Duet|Ask another Duet|Choose a Duet/i }).count(), 0);
  assert.equal(await page.locator('.target-action, .target-menu').count(), 0);
  assert.equal(await row.getByTitle('Reply', { exact: true }).count(), 1);
  assert.equal(await row.getByTitle('Track this message', { exact: true }).count(), 1);
  assert.equal(await row.locator('.failed-chip').filter({ hasText: "AAA's Duet" }).getByRole('button', { name: 'Retry', exact: true }).count(), 1);
  if (kind !== 'private') {
    assert.equal(await row.locator('.failed-chip').filter({ hasText: 'Maya Duet' }).getByRole('button', { name: 'Retry', exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Continue automatically · 6 more replies', exact: true }).click();
    await page.getByRole('button', { name: 'Continuation authorized', exact: true }).waitFor();
    const continued = writes.find(call => call.path.endsWith('/automatic-conversations/chain/continue'));
    assert.ok(continued); assert.equal(continued.body.additional_replies, 6); assert.equal(continued.body.expected_allowance, 6);
    const toggle = page.getByRole('button', { name: 'Reply for me', exact: true });
    await toggle.click(); await page.waitForFunction(() => document.querySelector('.twin-toggle')?.getAttribute('aria-pressed') === 'true');
  } else assert.equal(await page.getByRole('button', { name: 'Reply for me', exact: true }).count(), 0);
  await row.hover(); await row.getByTitle('Reply', { exact: true }).click(); await page.locator('.composer-reply').waitFor();
  await page.locator('.composer textarea').fill('Synthetic own composer request');
  await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.composer textarea')?.value === '');
  const sent = frames.find(frame => frame.command === 'send'); assert.ok(sent);
  assert.equal(sent.payload.body, 'Synthetic own composer request');
  assert.equal(sent.payload.reply_for_me, kind !== 'private');
  assert.equal(sent.payload.reply_to_message_id, message.id);
  assert.equal(Object.hasOwn(sent.payload, 'target_twin_id'), false);
  assert.equal(frames.filter(frame => frame.command === 'reply').length, 0);
  assert.equal(writes.filter(call => /\/chats\/[^/]+\/reply-requests$/.test(call.path)).length, 0);
  assert.equal((await page.locator('body').innerText()).includes('ask a Duet manually'), false);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.deepEqual(errors, []);
  const assets = await page.locator('script[src]').evaluateAll(nodes => nodes.map(node => new URL(node.src).pathname));
  await page.screenshot({ path: `/tmp/twinteams-automatic-reply-${kind}-${width}.png` });
  results.push({ kind, width, assets, no_manual_controls: true, owner_retry_only: true, composer: true, continue_budget: kind !== 'private', errors });
  await context.close(); console.log(`PASS automatic reply UI ${kind} ${width}px: no Ask/target controls; own Retry/composer and quote preserved.`);
}
async function run() {
  browser = await chromium.launch({ headless: true, channel: 'chrome' });
  for (const kind of ['private', 'pair', 'group']) for (const width of [1360, 390]) await scenario(kind, width);
  fs.writeFileSync('/tmp/twinteams-automatic-reply-ui-report.json', JSON.stringify({ base, synthetic_only: true, results }, null, 2));
  await browser.close();
}
run().catch(async error => { console.error(error); await browser?.contexts().at(-1)?.pages().at(-1)?.screenshot({ path: '/tmp/twinteams-automatic-reply-ui-failure.png' }).catch(() => {}); await browser?.close(); process.exitCode = 1; });
