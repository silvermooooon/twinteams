/** Independent acceptance: synthetic API/WS only; never sends a real message or model call. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5199';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const gate = () => { let release; const promise = new Promise(resolve => { release = resolve; }); return { promise, release }; };
const clone = value => JSON.parse(JSON.stringify(value));
let browser;
const report = [];

async function until(check, message, timeout = 5000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { if (await check()) return; await sleep(15); }
  throw new Error(message);
}

async function setup(options = {}) {
  const context = await browser.newContext({ viewport: { width: options.width || 1360, height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  page.setDefaultTimeout(6000);
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  if (options.fastTimeout) await page.addInitScript(() => {
    const original = window.setTimeout;
    window.setTimeout = (callback, delay, ...args) => original(callback, delay === 30000 ? 250 : delay, ...args);
  });
  const mock = await fixture(page, 'zh-CN', true);
  const chats = {
    'private-alex': { chat: mock.chat, messages: [], requests: [], revision: 1, has_more: false, before_seq: null },
    'group-shared': { chat: mock.group, messages: [], requests: [], revision: 1, has_more: false, before_seq: null },
  };
  const state = { chats, sockets: [], commands: [], committed: new Map(), detailCalls: [], inFlight: 0, maxInFlight: 0, detailDelay: 0, details: null, onSend: null, failures: {}, replyForMe: !!options.replyForMe, ownerId: 'owner-alex', signedIn: true };
  const answer = (route, value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
  function detail(chatId) {
    const record = chats[chatId];
    return { ...clone(record), approvals: [], tracking: [], members: chatId === 'private-alex' ? mock.actors.slice(0, 2) : mock.actors,
      settings: { reply_for_me_enabled: chatId === 'group-shared' && state.replyForMe, prompt_override: null, sidebar_open: false, sidebar_tab: 'workspace' } };
  }
  function hint(chatId = 'private-alex') {
    for (const socket of state.sockets) {
      try { socket.send(JSON.stringify({ v: 1, type: 'changed', chat_id: chatId, revision: chats[chatId].revision })); } catch { /* A replaced chat subscription may already be closed. */ }
    }
  }
  function commit(frame) {
    const key = frame.payload.client_request_key;
    if (state.committed.has(key)) return state.committed.get(key);
    const record = chats[frame.chat_id];
    const message = { id: `canonical-${state.committed.size + 1}`, chat_id: frame.chat_id, seq: Math.max(0, ...record.messages.map(item => item.seq)) + 1,
      author_id: 'owner-alex', kind: 'human', body: frame.payload.body, state: 'published', created_at: '2026-10-19T00:00:00Z', read_by: [], client_request_key: key,
      attachments: (frame.payload.attachment_ids || []).map(id => ({ id, name: 'synthetic.png', mime_type: 'image/png', kind: 'image', size: 68, width: 1, height: 1, url: '/synthetic-upload.png' })) };
    if (frame.payload.reply_to_message_id) { const source = record.messages.find(item => item.id === frame.payload.reply_to_message_id); message.reply_to = { id: source.id, body: source.body, author_name: 'AAA' }; }
    record.messages.push(message); record.revision++;
    state.committed.set(key, message);
    return message;
  }
  function ack(frame, socket, message = commit(frame), request = null) {
    try {
      socket.send(JSON.stringify({ v: 1, type: 'ack', command_id: frame.command_id, result: {
        message: message ? Object.fromEntries(['id', 'chat_id', 'seq', 'created_at', 'state', 'client_request_key'].map(key => [key, message[key]])) : null,
        request,
      } }));
    } catch { /* A deliberately replaced/logged-out socket cannot receive late callbacks. */ }
  }
  await page.routeWebSocket('**/api/ws', socket => {
    state.sockets.push(socket);
    socket.onMessage(raw => {
      const frame = JSON.parse(String(raw));
      if (frame.type !== 'command') return;
      if (frame.command !== 'send') return socket.send(JSON.stringify({ v: 1, type: 'ack', command_id: frame.command_id }));
      state.commands.push(clone(frame));
      if (state.onSend) Promise.resolve(state.onSend(frame, socket)).catch(error => errors.push(error.message));
      else { ack(frame, socket); hint(frame.chat_id); }
    });
  });
  await page.route('**/api/**', async route => {
    const req = route.request(); const url = new URL(req.url()); const path = url.pathname;
    if (path === '/api/auth/logout') { state.signedIn = false; return answer(route, { ok: true }); }
    if (path === '/api/auth/login') { state.signedIn = true; state.ownerId = req.postDataJSON().username === 'DEV-BBB' ? 'owner-maya' : 'owner-alex'; const owner = mock.actors.find(item => item.id === state.ownerId); return answer(route, { owner, current_owner_id: owner.id, development_mode: true }); }
    if (path === '/api/auth/me') { const owner = mock.actors.find(item => item.id === state.ownerId); return answer(route, { owner, current_owner_id: state.ownerId, development_mode: true }); }
    if (path === '/api/bootstrap') return answer(route, { current_owner_id: state.ownerId, actors: mock.actors, chats: [mock.chat, mock.group], reminders: [], model_configured: true, development_mode: true });
    if (path === '/api/synthetic-upload.png') return route.abort();
    const match = path.match(/^\/api\/chats\/([^/]+)$/);
    if (match && chats[match[1]]) {
      const chatId = match[1]; const call = { chatId, before: url.searchParams.get('before_seq'), snapshot: detail(chatId) };
      state.detailCalls.push(call); state.inFlight++; state.maxInFlight = Math.max(state.maxInFlight, state.inFlight);
      try {
        if (state.details) { const response = await state.details(call); if (response) return answer(route, response.body || response, response.status || 200); }
        if (state.detailDelay) await sleep(state.detailDelay);
        if (state.failures[chatId]) return answer(route, { detail: 'Synthetic authorization failure' }, state.failures[chatId]);
        return answer(route, call.snapshot);
      } finally { state.inFlight--; }
    }
    if (/\/attachments$/.test(path) && req.method() === 'POST') return answer(route, { id: 'upload-ready', name: 'synthetic.png', mime_type: 'image/png', kind: 'image', size: 68, width: 1, height: 1, url: '/synthetic-upload.png' });
    if (/\/settings$/.test(path) && req.method() === 'PATCH') { state.replyForMe = !!req.postDataJSON().reply_for_me_enabled; return answer(route, detail('group-shared').settings); }
    if (/\/read$/.test(path)) return answer(route, { ok: true });
    return route.fallback();
  });
  await page.route('**/synthetic-upload.png', route => route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDAAAAABJRU5ErkJggg==', 'base64') }));
  if (options.seed) options.seed(state);
  await page.goto(base + '/#chat');
  await page.locator('.composer textarea').waitFor({ state: 'attached' });
  if (options.width < 600) await page.locator('.conversation-list .chat-item').filter({ hasText: mock.chat.title }).click();
  await page.locator('.composer textarea').waitFor();
  await page.locator('.connection-state.connected').waitFor({ state: 'attached' });
  await until(() => state.inFlight === 0, 'initial chat requests did not settle');
  await sleep(120);
  if (options.kind === 'group') { await page.locator('.conversation-list .chat-item').filter({ hasText: 'Shared team' }).click(); await page.locator('.chat-header h2').filter({ hasText: 'Shared team' }).waitFor(); await until(() => state.inFlight === 0, 'group did not settle'); await sleep(120); }
  async function send(body) { const input = page.locator('.composer textarea'); await input.fill(body); await page.locator('.send-button').click(); }
  async function finish(name, metrics = {}) { assert.deepEqual(errors, [], `${name}: browser errors`); report.push({ name, ...metrics }); console.log('PASS', name, JSON.stringify(metrics)); await context.close(); }
  return { context, page, mock, state, detail, hint, commit, ack, send, finish };
}

async function trackBody(page, body) {
  await page.evaluate(value => {
    const probe = { body: value, clicked: null, appeared: null, counts: [] };
    const inspect = () => { const count = [...document.querySelectorAll('article')].filter(node => [...node.querySelectorAll('.message-text')].some(text => text.textContent === value)).length; if (probe.clicked !== null) { probe.counts.push(count); if (count && probe.appeared === null) probe.appeared = performance.now(); } };
    document.querySelector('.send-button').addEventListener('click', () => { probe.clicked = performance.now(); }, { capture: true });
    new MutationObserver(inspect).observe(document.querySelector('.message-area'), { childList: true, subtree: true, characterData: true });
    window.__sendProbe = probe;
  }, body);
}

async function immediateAndParallel(kind = 'private', width = 1360) {
  const h = await setup({ kind, width }); const { page, state } = h; const held = gate();
  state.onSend = async (frame, socket) => { await held.promise; h.ack(frame, socket); h.hint(frame.chat_id); };
  await trackBody(page, 'First optimistic message');
  await h.send('First optimistic message');
  await page.locator('article[data-local-send]').filter({ hasText: 'First optimistic message' }).waitFor();
  const timing = await page.evaluate(() => window.__sendProbe.appeared - window.__sendProbe.clicked);
  assert.ok(timing >= 0 && timing < 100, `Optimistic bubble took ${timing}ms`);
  assert.equal(await page.locator('.composer textarea').inputValue(), '');
  assert.equal(await page.locator('article[data-local-send] .read-receipt').count(), 0, 'local pending must not imply delivery/read');
  assert.equal(await page.locator('article[data-local-send][data-message-id]').count(), 0, 'local content must not be treated as a canonical message');
  assert.equal(await page.locator('article[data-local-send] .message-actions').count(), 0, 'local pending must not offer canonical reply/reaction tools');
  await h.send('Second optimistic message');
  await until(() => state.commands.length >= 2, 'composer must permit another send while ACK is blocked');
  assert.equal(await page.locator('article[data-local-send]').count(), 2);
  const rows = page.locator('article[data-local-send]');
  const firstBox = await rows.nth(0).boundingBox(); const secondBox = await rows.nth(1).boundingBox();
  assert.ok(secondBox.y >= firstBox.y + firstBox.height, 'pending messages must remain vertically stacked in an initially empty chat');
  await page.screenshot({ path: `/tmp/twinteams-optimistic-${kind}-${width}.png`, fullPage: true });
  await page.locator('.composer textarea').fill('A third unsent draft');
  await sleep(1050); state.detailDelay = 1000; held.release();
  await sleep(150);
  assert.equal(await page.locator('article[data-local-send]').count(), 2, 'ACK must not remove content while GET is blocked');
  assert.equal(await page.locator('.composer textarea').inputValue(), 'A third unsent draft');
  await until(async () => await page.locator('article[data-local-send]').count() === 0, 'canonical GET did not replace optimistic entries', 6000);
  for (const body of ['First optimistic message', 'Second optimistic message']) assert.equal(await page.locator('article .message-text').filter({ hasText: body }).count(), 1);
  await h.finish(`immediate + parallel ${kind} ${width}px`, { render_ms: timing, ack_blocked_ms: 1050, get_delay_ms: 1000 });
}

async function getBeforeAck() {
  const h = await setup(); const held = gate();
  h.state.onSend = async (frame, socket) => { h.commit(frame); h.hint(frame.chat_id); await held.promise; h.ack(frame, socket); };
  await trackBody(h.page, 'GET wins this race'); await h.send('GET wins this race');
  await h.page.locator('[data-message-id="canonical-1"]').waitFor();
  assert.equal(await h.page.locator('article[data-local-send]').count(), 0);
  held.release(); await sleep(200);
  assert.equal(await h.page.locator('article .message-text').filter({ hasText: 'GET wins this race' }).count(), 1);
  const counts = await h.page.evaluate(() => window.__sendProbe.counts);
  assert.ok(counts.every(count => count === 1), `GET-before-ACK caused disappearance/duplicate: ${counts}`);
  await h.finish('GET-before-ACK canonical convergence without flicker');
}

async function lostAckRetry() {
  const h = await setup({ fastTimeout: true });
  h.state.onSend = (frame, socket) => { const message = h.commit(frame); if (h.state.commands.length > 1) { h.ack(frame, socket, message); h.hint(frame.chat_id); } };
  await h.send('Frozen retry payload');
  await h.page.getByRole('button', { name: 'Retry send', exact: true }).waitFor();
  await h.page.locator('.composer textarea').fill('New draft must survive retry');
  await h.page.getByRole('button', { name: 'Retry send', exact: true }).click();
  await h.page.locator('[data-message-id="canonical-1"]').waitFor();
  assert.equal(h.state.committed.size, 1);
  assert.equal(h.state.commands.length, 2);
  assert.deepEqual(h.state.commands[1], h.state.commands[0], 'retry must freeze key and payload after uncertain commit');
  assert.equal(await h.page.locator('article .message-text').filter({ hasText: 'Frozen retry payload' }).count(), 1);
  assert.equal(await h.page.locator('.composer textarea').inputValue(), 'New draft must survive retry');
  await h.finish('lost ACK after commit retries frozen identity once');
}

async function staleGetAndCoalescing() {
  const h = await setup(); const held = gate(); const ack = gate(); let blocked = false;
  h.state.details = async call => { if (!blocked) { blocked = true; await held.promise; return call.snapshot; } };
  h.state.chats['private-alex'].revision++;
  h.hint(); await until(() => blocked, 'old GET did not start');
  h.state.onSend = async (frame, socket) => { await ack.promise; h.ack(frame, socket); h.hint(frame.chat_id); };
  await h.send('Pending survives stale GET');
  for (let i = 0; i < 15; i++) h.hint();
  await sleep(180); assert.equal(h.state.inFlight, 1, 'refresh hints should coalesce behind one active request');
  held.release(); await sleep(120);
  assert.equal(await h.page.locator('article[data-local-send]').filter({ hasText: 'Pending survives stale GET' }).count(), 1);
  ack.release();
  await h.page.locator('[data-message-id="canonical-1"]').waitFor();
  await sleep(250); await until(() => h.state.inFlight === 0, 'coalesced follow-up did not finish');
  const calls = h.state.detailCalls.length; await sleep(500);
  assert.equal(h.state.detailCalls.length, calls, 'refresh loop must settle instead of running forever');
  h.state.chats['private-alex'].messages = []; h.state.chats['private-alex'].revision++;
  h.hint(); await h.page.locator('[data-message-id="canonical-1"]').waitFor({ state: 'hidden' });
  assert.equal(await h.page.locator('article[data-local-send]').count(), 0, 'a revoked canonical message must not resurrect its local entry');
  await h.finish('stale GET preserves pending; hints coalesce; canonical revocation stays removed', { settled_detail_calls: calls });
}

async function authorizationClears(status) {
  const h = await setup(); const held = gate();
  h.state.onSend = async (frame, socket) => { await held.promise; h.ack(frame, socket); };
  await h.send(`Private pending cleared by ${status}`);
  await h.page.locator('article[data-local-send]').waitFor();
  h.state.failures['private-alex'] = status; h.state.chats['private-alex'].revision++; h.hint();
  await h.page.locator('article[data-local-send]').waitFor({ state: 'hidden' });
  const commandsBeforeReconnect = h.state.commands.length;
  if (status === 401) {
    await h.page.getByPlaceholder('DEV-AAA').waitFor();
    await sleep(850);
  } else {
    const socketCount = h.state.sockets.length;
    h.state.sockets.at(-1).close({ code: 1011, reason: 'Synthetic reconnect after authorization loss' });
    await until(() => h.state.sockets.length > socketCount, 'client did not reconnect after authorization loss', 3500);
    await sleep(100);
  }
  assert.equal(h.state.commands.length, commandsBeforeReconnect, 'authorization loss must forget pending transport sends');
  held.release(); await sleep(150);
  assert.equal(await h.page.getByText(`Private pending cleared by ${status}`, { exact: true }).count(), 0);
  await h.finish(`${status} drops local content and late ACK cannot restore it`);
}

async function chatResetAndOwnerIsolation() {
  const h = await setup(); const held = gate();
  h.state.onSend = async (frame, socket) => { await held.promise; h.ack(frame, socket); };
  await h.send('Private local only');
  await h.page.locator('.conversation-list .chat-item').filter({ hasText: 'Shared team' }).click();
  await h.page.locator('.chat-header h2').filter({ hasText: 'Shared team' }).waitFor();
  assert.equal(await h.page.getByText('Private local only', { exact: true }).count(), 0);
  await h.send('Owner A group local only');
  await h.page.locator('.conversation-list .chat-item').filter({ hasText: h.mock.chat.title }).click();
  await h.page.getByText('Private local only', { exact: true }).waitFor();
  h.mock.remoteReset();
  h.state.sockets.at(-1).send(JSON.stringify({ v: 1, type: 'personal_changed', section: 'reset' }));
  await h.page.getByText('Private local only', { exact: true }).waitFor({ state: 'hidden' });
  const privateKey = h.state.commands.find(frame => frame.chat_id === 'private-alex').command_id;
  const privateAttempts = h.state.commands.filter(frame => frame.command_id === privateKey).length;
  await h.page.locator('.conversation-list .chat-item').filter({ hasText: 'Shared team' }).click();
  await h.page.getByText('Owner A group local only', { exact: true }).waitFor();
  await sleep(120);
  assert.equal(h.state.commands.filter(frame => frame.command_id === privateKey).length, privateAttempts, 'reset private sends must not replay when subscriptions change');
  await h.page.getByRole('button', { name: 'Account', exact: true }).click();
  await h.page.getByRole('button', { name: 'Sign out / switch account', exact: true }).click();
  await h.page.getByPlaceholder('DEV-AAA').waitFor();
  await h.page.getByPlaceholder('DEV-AAA').fill('DEV-BBB'); await h.page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await h.page.locator('.composer textarea').waitFor();
  await h.page.locator('.conversation-list .chat-item').filter({ hasText: 'Shared team' }).click();
  await h.page.locator('.chat-header h2').filter({ hasText: 'Shared team' }).waitFor();
  assert.equal(await h.page.getByText('Owner A group local only', { exact: true }).count(), 0);
  // A stale owner's network callbacks must not create owner B's local entries.
  held.release(); await sleep(150);
  assert.equal(await h.page.locator('article[data-local-send]').count(), 0);
  await h.finish('chat switch, private reset, logout and owner switch isolate pending sends');
}

async function attachmentQuote(kind) {
  const chatId = kind === 'group' ? 'group-shared' : 'private-alex';
  const h = await setup({ kind, seed: state => state.chats[chatId].messages.push({ id: 'quote-source', chat_id: chatId, seq: 1, author_id: 'owner-alex', kind: 'human', body: 'Quoted authorized context', created_at: '2026-10-18T00:00:00Z', read_by: [], state: 'published' }) });
  const held = gate(); h.state.onSend = async (frame, socket) => { await held.promise; h.ack(frame, socket); h.hint(frame.chat_id); };
  await h.page.locator('[data-message-id=quote-source]').hover();
  await h.page.locator('[data-message-id=quote-source]').getByTitle('Reply', { exact: true }).click();
  await h.page.locator('.composer-reply').getByText('Quoted authorized context', { exact: true }).waitFor();
  await h.page.locator('input[type=file]').first().setInputFiles({ name: 'synthetic.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDAAAAABJRU5ErkJggg==', 'base64') });
  await h.page.getByText('Ready to send', { exact: true }).waitFor();
  await h.send(`Quoted ${kind} attachment`);
  const pending = h.page.locator('article[data-local-send]'); await pending.waitFor();
  await pending.getByText('Quoted authorized context', { exact: true }).waitFor();
  await pending.getByText('synthetic.png', { exact: true }).waitFor();
  await until(() => h.state.commands.length > 0, 'send command missing');
  assert.deepEqual(h.state.commands[0].payload.attachment_ids, ['upload-ready']);
  assert.equal(h.state.commands[0].payload.reply_to_message_id, 'quote-source');
  assert.equal(await pending.locator('.read-receipt').count(), 0);
  held.release(); await h.page.locator('[data-message-id=canonical-1]').waitFor();
  assert.equal(await h.page.locator('article[data-local-send]').count(), 0);
  await h.finish(`${kind} ready attachment + quoted reply survives canonical replacement`);
}

async function replyForMe() {
  const h = await setup({ kind: 'group', replyForMe: true }); const held = gate(); let sent;
  h.state.onSend = async (frame, socket) => { sent = frame; await held.promise; const request = { id: 'request-local-reply', chat_id: frame.chat_id, status: 'queued', source: 'reply_for_me', source_message_id: null, target_twin_id: 'twin-alex', requester_id: 'owner-alex', accepted_at: '2026-10-19T00:00:00Z', can_cancel: true, can_retry: false, client_request_key: frame.payload.client_request_key }; h.state.chats['group-shared'].requests = [request]; h.ack(frame, socket, null, request); h.hint('group-shared'); };
  await h.send('Private raw writing directions');
  const local = h.page.locator('article[data-local-send]'); await local.waitFor();
  await local.getByText('Preparing reply…', { exact: true }).waitFor();
  assert.equal(h.state.chats['group-shared'].messages.length, 0, 'raw writing input must not become a published message');
  held.release(); await sleep(250);
  assert.equal(await local.count(), 1, 'queued request is not a completed generated message');
  const request = h.state.chats['group-shared'].requests[0]; request.status = 'completed';
  h.state.chats['group-shared'].messages = [{ id: 'generated-reply', chat_id: 'group-shared', seq: 1, author_id: 'twin-alex', kind: 'reply_for_me', body: 'Generated ready-to-send message', raw_owner_input: sent.payload.body, read_by: [], created_at: '2026-10-19T00:00:00Z', state: 'published', request_id: request.id, client_request_key: sent.payload.client_request_key }];
  h.state.chats['group-shared'].revision++; h.hint('group-shared');
  await h.page.locator('[data-message-id=generated-reply]').waitFor();
  assert.equal(await local.count(), 0);
  assert.equal(await h.page.getByText('Generated ready-to-send message', { exact: true }).count(), 1);
  assert.equal(h.state.chats['group-shared'].messages.filter(item => item.kind === 'human').length, 0);
  await h.finish('reply-for-me stays local Preparing reply until generated canonical message');
}

async function quoteRevocation() {
  const h = await setup({ kind: 'group', seed: state => state.chats['group-shared'].messages.push({ id: 'revocable-quote', chat_id: 'group-shared', seq: 1, author_id: 'owner-alex', kind: 'human', body: 'Source quote must be redacted after withdrawal', created_at: '2026-10-18T00:00:00Z', read_by: [], state: 'published' }) });
  h.state.onSend = () => {};
  const source = h.page.locator('[data-message-id=revocable-quote]');
  await source.hover(); await source.getByTitle('Reply', { exact: true }).click();
  await h.page.locator('.composer-reply').waitFor();
  await h.send('My still-pending group reply');
  const local = h.page.locator('article[data-local-send]');
  await local.getByText('Source quote must be redacted after withdrawal', { exact: true }).waitFor();
  await until(() => h.state.commands.length > 0, 'pending quote send command missing');
  const frozen = clone(h.state.commands[0]);
  h.state.chats['group-shared'].messages[0].state = 'withdrawn';
  h.state.chats['group-shared'].revision++; h.hint('group-shared');
  await local.getByText('Original message unavailable', { exact: true }).waitFor();
  assert.equal(await h.page.getByText('Source quote must be redacted after withdrawal', { exact: true }).count(), 0);
  assert.deepEqual(h.state.commands[0], frozen, 'redacting a local quote must not mutate retry identity or payload');
  await h.page.screenshot({ path: '/tmp/twinteams-optimistic-quote-revoked.png', fullPage: true });
  await h.finish('authorized source withdrawal redacts local quote while pending payload stays frozen');
}

async function oldPageCannotBlockLatest() {
  const h = await setup({ seed: state => { Object.assign(state.chats['private-alex'], { has_more: true, before_seq: 201 }); state.chats['private-alex'].messages = [{ id: 'latest-seed', chat_id: 'private-alex', seq: 201, author_id: 'owner-alex', kind: 'human', body: 'Latest authorized seed', created_at: '2026-10-18T00:00:00Z', read_by: [], state: 'published' }]; } });
  const held = gate(); let oldStarted = false;
  h.state.details = async call => { if (call.before) { oldStarted = true; await held.promise; return { ...call.snapshot, messages: [{ id: 'revoked-old-page', chat_id: 'private-alex', seq: 100, author_id: 'owner-alex', kind: 'human', body: 'Stale old page must not return', created_at: '2026-10-18T00:00:00Z', read_by: [], state: 'published' }], has_more: false, before_seq: 100 }; } };
  await h.page.getByRole('button', { name: 'Load earlier messages', exact: true }).click();
  await until(() => oldStarted, 'old page load did not begin');
  await h.send('Newest message bypasses old page');
  await h.page.locator('[data-message-id=canonical-1]').waitFor({ timeout: 1500 });
  assert.equal(oldStarted, true);
  held.release(); await sleep(150);
  assert.equal(await h.page.getByText('Stale old page must not return', { exact: true }).count(), 0, 'late older revision page must not reintroduce content after fresh detail');
  await h.finish('slow older-page load cannot block latest canonical send or restore stale content');
}

async function run() {
  browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
  const cases = [
    ['immediate-private', () => immediateAndParallel('private')],
    ['immediate-group', () => immediateAndParallel('group')],
    ['immediate-mobile', () => immediateAndParallel('private', 390)],
    ['get-before-ack', getBeforeAck], ['lost-ack-retry', lostAckRetry],
    ['stale-get-coalescing', staleGetAndCoalescing],
    ['auth-401', () => authorizationClears(401)], ['auth-403', () => authorizationClears(403)],
    ['scope-isolation', chatResetAndOwnerIsolation],
    ['attachment-private', () => attachmentQuote('private')], ['attachment-group', () => attachmentQuote('group')],
    ['reply-for-me', replyForMe], ['quote-revocation', quoteRevocation], ['older-page', oldPageCannotBlockLatest],
  ];
  const failures = [];
  for (const [name, execute] of cases) {
    if (process.env.TWINTEAMS_OPTIMISTIC_CASE && !new RegExp(process.env.TWINTEAMS_OPTIMISTIC_CASE).test(name)) continue;
    try { await execute(); }
    catch (error) {
      failures.push({ name, error: error.message }); console.error('FAIL', name, error.message);
      const page = browser.contexts().at(-1)?.pages().at(-1);
      await page?.screenshot({ path: `/tmp/twinteams-optimistic-${name}-failure.png`, fullPage: true }).catch(() => {});
    } finally { for (const context of browser.contexts()) await context.close(); }
  }
  fs.writeFileSync('/tmp/twinteams-optimistic-send-report.json', JSON.stringify({ base, fixture_only: true, real_messages: false, tests: report, failures }, null, 2));
  await browser.close();
  assert.deepEqual(failures, [], 'optimistic-send acceptance failures');
}
run().catch(async error => { console.error(error); await browser?.close(); process.exitCode = 1; });
