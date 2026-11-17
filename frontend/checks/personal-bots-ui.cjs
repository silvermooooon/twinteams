/** Synthetic HTTP/WS browser coverage; no real owners, model calls or business writes. */
const assert = require('node:assert/strict');
const { mkdirSync } = require('node:fs');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5199';
const artifacts = process.env.TWINTEAMS_UI_ARTIFACT_DIR || '/tmp/duet-bots';
mkdirSync(artifacts, { recursive: true });
let browser; let activePage;

async function botFixture(page) {
  const mock = await fixture(page, 'en', true);
  const owner = mock.actors.find(actor => actor.kind === 'human' && actor.id === 'owner-alex');
  const main = mock.actors.find(actor => actor.kind === 'twin' && actor.owner_id === owner.id);
  const chats = [mock.chat, mock.group]; const bots = []; const messages = new Map(chats.map(chat => [chat.id, []])); const requests = new Map();
  const calls = []; let socket; let bootstrapReads = 0; let failCreate = true; let failHistory = false; let hiddenChat; let historyDelay = 0;
  const reply = (route, data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
  const actors = () => [...mock.actors, ...bots.map(bot => bot.actor)];
  const details = chat => ({ revision: chat.revision || 1, chat, messages: messages.get(chat.id) || [], has_more: false, before_seq: 1, requests: requests.get(chat.id) || [], approvals: [], questions: [], members: actors().filter(actor => actor.owner_id === owner.id), settings: { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: chat.kind === 'private' ? 'activity' : 'workspace' } });
  function append(chatId, authorId, body, options = {}) {
    const list = messages.get(chatId); const id = `message-${chatId}-${list.length + 1}`;
    const message = { id, chat_id: chatId, seq: list.length + 1, author_id: authorId, kind: authorId === owner.id ? 'human' : 'twin', body, state: 'published', created_at: '2026-11-17T05:00:00Z', read_by: [], ...options };
    list.push(message); const chat = chats.find(chat => chat.id === chatId); chat.revision = (chat.revision || 1) + 1; chat.last_message = body; chat.last_message_meta = { id, seq: message.seq, author_id: authorId, kind: message.kind, created_at: message.created_at };
    return message;
  }
  function hint(chatId) { socket.send(JSON.stringify({ v: 1, type: 'changed', chat_id: chatId, revision: chats.find(chat => chat.id === chatId).revision })); }
  function addBot(name, assignment) {
    const id = `bot-${bots.length + 1}`; const actor = { id, owner_id: owner.id, kind: 'bot', name, initials: name.slice(0, 2), color: '#666', profile_revision: 1 };
    const chat = { id: `chat-${id}`, bot_id: id, kind: 'bot', title: name, member_owner_ids: [owner.id], member_actor_ids: [id], unread_count: 0, revision: 1 };
    const bot = { id, chat_id: chat.id, name, assignment, actor, chat }; bots.push(bot); chats.push(chat); messages.set(chat.id, []); return bot;
  }
  await page.routeWebSocket('**/api/ws', ws => {
    socket = ws;
    ws.onMessage(raw => {
      const frame = JSON.parse(String(raw)); if (frame.type !== 'command') return;
      calls.push({ path: `/api/chats/${frame.chat_id}/messages`, command: frame.command, body: frame.payload });
      if (frame.command !== 'send') return ws.send(JSON.stringify({ v: 1, type: 'ack', command_id: frame.command_id }));
      const message = append(frame.chat_id, owner.id, frame.payload.body, { client_request_key: frame.payload.client_request_key });
      const chat = chats.find(chat => chat.id === frame.chat_id);
      const target = frame.payload.recipient_actor_ids?.[0] || chat.bot_id || main.id;
      const request = { id: `request-${message.id}`, target_twin_id: target, chat_id: chat.id, source_message_id: message.id, requester_id: owner.id, source: 'message', status: 'running', can_cancel: true, can_retry: false, accepted_at: message.created_at, client_request_key: frame.command_id };
      requests.set(chat.id, [request]);
      ws.send(JSON.stringify({ v: 1, type: 'ack', command_id: frame.command_id, result: { message, request } })); hint(chat.id);
    });
  });
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname; const method = request.method(); const body = request.postData() ? JSON.parse(request.postData()) : null;
    if (!path.startsWith('/api/bots') && !path.startsWith('/api/bot-rooms') && path !== '/api/bootstrap' && !path.startsWith('/api/chats/')) return route.fallback();
    calls.push({ path, method, body });
    if (path === '/api/bootstrap') { bootstrapReads++; return reply(route, { current_owner_id: owner.id, actors: actors(), chats, bots, reminders: [], model_configured: true, development_mode: true }); }
    if (path === '/api/bots' && method === 'GET') return reply(route, bots);
    if (path === '/api/bots' && method === 'POST') {
      if (failCreate) return reply(route, { detail: 'Synthetic create failure' }, 503);
      return reply(route, addBot(body.name, body.assignment));
    }
    if (path === '/api/bots/dispatch') {
      for (const id of body.bot_ids) { const bot = bots.find(bot => bot.id === id); const coordination = { kind: 'assignment', source_chat_id: mock.chat.id, target_chat_id: bot.chat_id, actor_ids: [main.id, id] }; append(mock.chat.id, main.id, body.body, { coordination }); append(bot.chat_id, main.id, body.body, { coordination }); hint(bot.chat_id); }
      hint(mock.chat.id); return reply(route, { chat_id: mock.chat.id });
    }
    if (path === '/api/bots/messages') {
      const source = body.sender_actor_id === main.id ? mock.chat.id : bots.find(bot => bot.id === body.sender_actor_id).chat_id;
      const target = body.recipient_actor_id === main.id ? mock.chat.id : bots.find(bot => bot.id === body.recipient_actor_id).chat_id;
      const coordination = { kind: 'message', source_chat_id: source, target_chat_id: target, actor_ids: [body.sender_actor_id, body.recipient_actor_id] };
      append(source, body.sender_actor_id, body.body, { coordination }); append(target, body.sender_actor_id, body.body, { coordination }); hint(source); hint(target); return reply(route, { chat_id: target, source_chat_id: source });
    }
    if (path.startsWith('/api/bots/')) {
      const bot = bots.find(bot => bot.id === path.split('/')[3]);
      if (!bot || failHistory && path.endsWith('/history')) return reply(route, { detail: 'BOT not available to this owner' }, 404);
      if (method === 'PATCH') { Object.assign(bot, body); Object.assign(bot.actor, { name: body.name, profile_revision: 2 }); bot.chat.title = body.name; return reply(route, bot); }
      if (historyDelay) await new Promise(resolve => setTimeout(resolve, historyDelay));
      return reply(route, details(bot.chat));
    }
    if (path === '/api/bot-rooms') {
      const chat = { id: 'room-1', kind: 'internal', title: body.title, member_owner_ids: [owner.id], member_actor_ids: [main.id, ...body.bot_ids], unread_count: 0, revision: 1 }; chats.push(chat); messages.set(chat.id, []); return reply(route, chat);
    }
    const chat = chats.find(chat => chat.id === path.split('/')[3]);
    if (!chat || hiddenChat === chat.id) return reply(route, { detail: 'This conversation is not available to your account.' }, 403);
    if (path.endsWith('/summary')) return reply(route, chat);
    if (path.endsWith('/read') || path.endsWith('/settings')) return reply(route, body);
    if (path.endsWith('/workspace')) return reply(route, []);
    if (path.endsWith('/activity')) return reply(route, { items: [] });
    if (path.endsWith('/tracking')) return reply(route, { tracking: [] });
    if (/\/chats\/[^/]+$/.test(path)) return reply(route, details(chat));
    return route.fallback();
  });
  return { bots, chats, main, calls, append, hint, createFromDuet(name, assignment) { const bot = addBot(name, assignment); socket.send(JSON.stringify({ v: 1, type: 'personal_changed', section: 'chats' })); return bot; }, startWork(chatId, actorId, source, status = 'running', quiet = false) { const chat = chats.find(chat => chat.id === chatId); requests.set(chatId, [{ id: `work-${source}`, chat_id: chatId, source_message_id: messages.get(chatId).at(-1)?.id, target_twin_id: actorId, requester_id: owner.id, source, status, background: true, quiet_background: quiet, can_cancel: true, can_retry: false, accepted_at: '2026-11-17T05:00:00Z' }]); chat.revision++; hint(chatId); }, finish(chatId, body, authorId) { requests.set(chatId, []); append(chatId, authorId, body); hint(chatId); }, setFailCreate(value) { failCreate = value; }, setFailHistory(value) { failHistory = value; }, setHiddenChat(value) { hiddenChat = value; }, setHistoryDelay(value) { historyDelay = value; }, bootstrapReads: () => bootstrapReads };
}

async function run() {
  browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage(); activePage = page; page.setDefaultTimeout(10000); const errors = []; page.on('pageerror', error => errors.push(error.message));
  const mock = await botFixture(page); await page.goto(base + '/#chat'); await page.locator('.composer textarea').waitFor();
  await page.getByText('Create a personal BOT with a responsibility of its own.', { exact: true }).waitFor();
  const reads = mock.bootstrapReads();
  await page.getByRole('button', { name: 'Create BOT', exact: true }).click();
  let dialog = page.getByRole('dialog'); await dialog.getByLabel('BOT name', { exact: true }).fill('Research'); await dialog.getByLabel('Assignment', { exact: true }).fill('Research assigned product questions.');
  await dialog.getByRole('button', { name: 'Create BOT', exact: true }).click(); await dialog.getByRole('alert').filter({ hasText: 'Synthetic create failure' }).waitFor();
  assert.equal(await dialog.getByLabel('BOT name', { exact: true }).inputValue(), 'Research'); mock.setFailCreate(false);
  await dialog.getByRole('button', { name: 'Create BOT', exact: true }).click(); await page.locator('.chat-header h2').filter({ hasText: 'Research' }).waitFor();
  await page.getByText('Start with your BOT’s assignment or give it a task.', { exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: 'Add members', exact: true }).count(), 0);
  await page.locator('.composer textarea').fill('Explain the task.'); await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.getByRole('status', { name: 'Research is typing…', exact: true }).waitFor();
  mock.finish(mock.bots[0].chat_id, 'I will research assigned product questions.', mock.bots[0].id);
  await page.locator('.message-area').getByText('I will research assigned product questions.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Edit BOT', exact: true }).click(); dialog = page.getByRole('dialog'); await dialog.getByLabel('BOT name', { exact: true }).fill('Research partner'); await dialog.getByLabel('Assignment', { exact: true }).fill('Research questions and cite sources.'); await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await page.locator('.chat-header h2').filter({ hasText: 'Research partner' }).waitFor();
  await page.getByRole('button', { name: 'Create BOT', exact: true }).click(); dialog = page.getByRole('dialog'); await dialog.getByLabel('BOT name', { exact: true }).fill('Review'); await dialog.getByLabel('Assignment', { exact: true }).fill('Review research for gaps.'); await dialog.getByRole('button', { name: 'Create BOT', exact: true }).click(); await page.locator('.chat-header h2').filter({ hasText: 'Review' }).waitFor();
  await page.getByRole('button', { name: 'Open main Duet', exact: true }).click();
  await page.getByRole('button', { name: 'Assign to BOTs', exact: true }).click(); dialog = page.getByRole('dialog'); await dialog.getByRole('checkbox').nth(0).check(); await dialog.getByRole('checkbox').nth(1).check(); await dialog.getByLabel('Task', { exact: true }).fill('Compare two proposals.'); await dialog.getByRole('button', { name: 'Assign task', exact: true }).click();
  await page.locator('.coordination-links').filter({ hasText: 'Task assigned to' }).nth(1).waitFor();
  const assignment = mock.calls.find(call => call.path === '/api/bots/dispatch'); assert.equal(assignment.body.bot_ids.length, 2);
  mock.startWork(mock.bots[0].chat_id, mock.bots[0].id, 'bot_dispatch');
  await page.locator('.coordination-links').filter({ hasText: 'Task assigned to Research partner' }).getByRole('button', { name: 'Open destination conversation', exact: true }).click();
  await page.getByRole('status', { name: 'Research partner is typing…', exact: true }).waitFor();
  mock.startWork(mock.bots[0].chat_id, mock.bots[0].id, 'subagent_result', 'queued');
  await page.locator('.typing-row[data-reply-phase=preparing]').waitFor();
  mock.finish(mock.bots[0].chat_id, 'Task completed with a reviewed result.', mock.bots[0].id);
  await page.getByRole('button', { name: 'Open main Duet', exact: true }).click();
  mock.append('private-alex', mock.bots[0].id, 'Proposal A is easier to maintain.', { coordination: { kind: 'result', source_chat_id: mock.bots[0].chat_id, target_chat_id: 'private-alex', status: 'completed', has_result: true } }); mock.hint('private-alex');
  await page.locator('.message-area').getByText('Proposal A is easier to maintain.', { exact: true }).waitFor(); await page.getByText('Result returned', { exact: true }).waitFor();
  mock.startWork('private-alex', mock.main.id, 'bot_result');
  await page.getByRole('status', { name: "AAA's Duet is typing…", exact: true }).waitFor();
  mock.startWork('private-alex', mock.main.id, 'subagent_result', 'queued');
  await page.locator('.typing-row[data-reply-phase=preparing]').waitFor();
  mock.startWork('private-alex', mock.main.id, 'system_review', 'running', true);
  await page.locator('.typing-row').waitFor({ state: 'hidden' });
  mock.append('private-alex', mock.bots[1].id, '', { coordination: { kind: 'result', source_chat_id: mock.bots[1].chat_id, target_chat_id: 'private-alex', status: 'failed', has_result: false } }); mock.hint('private-alex');
  await page.getByText('Task failed. Open source conversation for details.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Agent message', exact: true }).click(); dialog = page.getByRole('dialog'); await dialog.getByLabel('To agent', { exact: true }).selectOption(mock.bots[0].id); await dialog.getByLabel('Message', { exact: true }).fill('Please compare maintenance cost.'); await dialog.getByRole('button', { name: 'Send internal message', exact: true }).click();
  await page.locator('.chat-header h2').filter({ hasText: 'Research partner' }).waitFor(); await page.locator('.message-area').getByText('Please compare maintenance cost.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Agent message', exact: true }).click(); dialog = page.getByRole('dialog'); await dialog.getByLabel('To agent', { exact: true }).selectOption(mock.bots[1].id); await dialog.getByLabel('Message', { exact: true }).fill('Review my maintenance findings.'); await dialog.getByRole('button', { name: 'Send internal message', exact: true }).click(); await page.locator('.chat-header h2').filter({ hasText: 'Review' }).waitFor(); await page.locator('.message-area').getByText('Review my maintenance findings.', { exact: true }).waitFor();
  assert.equal(mock.calls.filter(call => call.path === '/api/bots/messages').at(-1).body.sender_actor_id, mock.bots[0].id);
  await page.getByRole('button', { name: 'New collaboration room', exact: true }).click(); dialog = page.getByRole('dialog'); await dialog.getByLabel('Room name', { exact: true }).fill('Proposal workshop'); await dialog.getByRole('checkbox').nth(0).check(); await dialog.getByRole('checkbox').nth(1).check(); await dialog.getByRole('button', { name: 'Create room', exact: true }).click(); await page.locator('.chat-header h2').filter({ hasText: 'Proposal workshop' }).waitFor();
  assert.equal(await page.getByLabel('Room members').locator(':scope > span').count(), 3);
  await page.getByRole('group', { name: 'Ask to respond', exact: true }).getByRole('checkbox').nth(1).check(); await page.getByRole('group', { name: 'Ask to respond', exact: true }).getByRole('checkbox').nth(2).check(); await page.locator('.composer textarea').fill('Discuss both proposals together.'); await page.getByRole('button', { name: 'Send message', exact: true }).click();
  await page.locator('.message-area').getByText('Discuss both proposals together.', { exact: true }).waitFor(); assert.equal(mock.calls.filter(call => call.command === 'send').at(-1).body.recipient_actor_ids.length, 3);
  mock.finish('room-1', 'Here is my review in the room.', mock.bots[1].id); await page.locator('.message-area').getByText('Here is my review in the room.', { exact: true }).waitFor(); await page.screenshot({ path: `${artifacts}/ui-room.png`, fullPage: true });
  await page.locator('.conversation-list .chat-item').filter({ hasText: "AAA's Duet" }).click();
  for (const status of ['failed', 'cancelled', 'completed']) mock.append(mock.bots[0].chat_id, mock.bots[1].id, '', { coordination: { kind: 'result', source_chat_id: mock.bots[1].chat_id, target_chat_id: mock.bots[0].chat_id, actor_ids: [mock.bots[1].id, mock.bots[0].id], status, has_result: false } });
  mock.setHistoryDelay(250); await page.getByRole('button', { name: 'Inspect BOT transcript', exact: true }).click(); dialog = page.getByRole('dialog'); await dialog.getByRole('status').filter({ hasText: 'Loading' }).waitFor(); await dialog.getByText('I will research assigned product questions.', { exact: true }).waitFor();
  for (const label of ['Task failed. Open source conversation for details.', 'Task cancelled', 'Task finished without a written result']) await dialog.getByText(label, { exact: true }).waitFor();
  await dialog.locator('.coordination-links').filter({ hasText: 'Task cancelled' }).getByRole('button', { name: 'Open source conversation', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' }); await page.locator('.chat-header h2').filter({ hasText: 'Review' }).waitFor();
  await page.getByRole('button', { name: 'Open main Duet', exact: true }).click(); await page.getByRole('button', { name: 'Inspect BOT transcript', exact: true }).click(); dialog = page.getByRole('dialog'); await dialog.getByText('Task cancelled', { exact: true }).waitFor();
  mock.setFailHistory(true); await dialog.getByRole('button', { name: 'Refresh', exact: true }).click(); await dialog.getByRole('heading', { name: 'Transcript unavailable', exact: true }).waitFor(); await dialog.getByRole('alert').filter({ hasText: 'Only your own BOTs and rooms can be opened here.' }).waitFor();
  mock.setFailHistory(false); await dialog.getByRole('button', { name: 'Refresh', exact: true }).click(); await dialog.getByText('I will research assigned product questions.', { exact: true }).waitFor(); await dialog.getByRole('button', { name: 'Ask Duet to review', exact: true }).click(); await dialog.waitFor({ state: 'hidden' });
  assert.match(mock.calls.filter(call => call.command === 'send').at(-1).body.body, /Review the conversation with Research partner/);
  assert.equal(mock.bootstrapReads(), reads, 'BOT actions and messages use local merges and targeted chat reads');
  const creates = mock.calls.filter(call => call.path === '/api/bots' && call.method === 'POST').length;
  await page.locator('.composer textarea').fill('Keep this draft while Duet creates a BOT.');
  const created = mock.createFromDuet('CEAIA Bug Investigator', 'Investigate CEAIA bugs using authorized evidence.');
  const newEntry = page.locator('.conversation-list .chat-item').filter({ has: page.locator('.chat-name').filter({ hasText: /^CEAIA Bug Investigator$/ }) });
  await newEntry.waitFor();
  assert.equal(await page.locator('.chat-header h2').innerText(), "AAA's Duet");
  assert.equal(await page.locator('.composer textarea').inputValue(), 'Keep this draft while Duet creates a BOT.');
  assert.ok(await page.locator('.composer textarea').evaluate(element => element === document.activeElement));
  assert.equal(mock.bootstrapReads(), reads + 1, 'The existing membership hint refreshes the BOT catalog');
  assert.equal(mock.calls.filter(call => call.path === '/api/bots' && call.method === 'POST').length, creates, 'No manual creation request is needed');
  await newEntry.click(); await page.locator('.chat-header h2').filter({ hasText: created.name }).waitFor();
  await page.getByText('Start with your BOT’s assignment or give it a task.', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Edit BOT', exact: true }).click(); dialog = page.getByRole('dialog');
  assert.equal(await dialog.getByLabel('Assignment', { exact: true }).inputValue(), created.assignment);
  await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.locator('.conversation-list .chat-item').filter({ hasText: 'Shared team' }).click(); await page.locator('.chat-header h2').filter({ hasText: 'Shared team' }).waitFor(); assert.equal(await page.getByRole('button', { name: 'Assign to BOTs', exact: true }).count(), 0);
  mock.setHiddenChat(mock.bots[0].chat_id); await page.locator('.conversation-list .chat-item').filter({ has: page.locator('.chat-name').filter({ hasText: /^Research partner$/ }) }).click(); await page.getByRole('alert').filter({ hasText: 'This conversation is not available to your account.' }).waitFor(); assert.equal(await page.locator('.composer textarea').count(), 0);
  await page.setViewportSize({ width: 390, height: 844 }); await page.getByRole('button', { name: 'Create BOT', exact: true }).click(); dialog = page.getByRole('dialog'); const bounds = await dialog.boundingBox(); assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390); await page.screenshot({ path: `${artifacts}/ui-mobile-create.png`, fullPage: true }); await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.deepEqual(errors, []);
  await context.close(); await browser.close();
  console.log('PASS personal BOT UI: create/edit/error, direct chat/status, background dispatch/child/result visibility, quiet review hidden, multi-dispatch/results, agent messaging, room members/recipients, transcript loading/unavailable/review and empty-body handoff status/navigation, local refresh, Duet creation hint preserves chat/draft/focus and opens BOT, group navigation, mobile modal');
}
run().catch(async error => { console.error(error); if (activePage) await activePage.screenshot({ path: `${artifacts}/ui-failure.png`, fullPage: true }).catch(() => {}); await browser?.close(); process.exitCode = 1; });
