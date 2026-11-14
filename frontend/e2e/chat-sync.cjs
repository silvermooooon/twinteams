/** Mocked transport regression against the real App, without an API or worker. */
const assert = require('node:assert/strict');

async function until(check, label) {
  const deadline = Date.now() + 5000;
  while (!check()) {
    if (Date.now() > deadline) throw Error(`Timed out: ${label}`);
    await new Promise(resolve => setTimeout(resolve, 20));
  }
}

async function chatSyncRegression(browser, origin) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
  const failures = [], requests = [], sockets = [], frames = [], summaries = [], heldDetails = [];
  page.on('pageerror', error => failures.push(error.message));
  const now = '2026-11-14T08:00:00Z';
  const actors = ['Alex', 'Blair', 'Casey'].map((name, index) => ({ id: `owner-${index}`, kind: 'human', name, initials: name[0], color: '#5264ad' }));
  const chats = ['Active group', 'Other group'].map((title, index) => ({ id: `group-${index}`, kind: 'group', title, revision: 1, member_owner_ids: actors.map(actor => actor.id), unread_count: 0, last_message: 'Initial summary' }));
  const settings = { reply_for_me_enabled: false, prompt_override: null, sidebar_open: true, sidebar_tab: 'tracking' };
  let topic, holdDetail = false, messages = [];
  const chatDetail = chat => ({ revision: chat.revision, chat, members: actors, messages: messages.filter(message => message.chat_id === chat.id), requests: [], approvals: [], questions: [], settings, has_more: false, before_seq: null });
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname, method = request.method();
    if (!path.startsWith('/api/')) return route.continue();
    requests.push(`${method} ${path}`);
    let body;
    if (path === '/api/config') body = { development_mode: true };
    else if (path === '/api/auth/me') body = { current_owner_id: actors[0].id, owner: actors[0], development_mode: true };
    else if (path === '/api/bootstrap') body = { actors, chats, reminders: [], current_owner_id: actors[0].id, model_configured: true, development_mode: true, server_time: now };
    else if (path === '/api/me/twin/credentials') body = { configured: false, credentials: [] };
    else if (path === '/api/me/twin/credential-requests') body = { configured: false, requests: [] };
    else if (path === '/api/me/twin/reset') body = { status: 'idle', generation: 0, private_chat_id: 'private' };
    else if (path === '/api/me/twin') body = { actor: { id: 'twin-0', owner_id: 'owner-0', kind: 'twin', name: 'Duet', initials: 'D', color: '#5264ad' } };
    else if (path.endsWith('/summary')) { summaries.push(route); return; }
    else if (/^\/api\/chats\/group-[01]$/.test(path)) { if (holdDetail) { heldDetails.push(route); return; } body = chatDetail(chats.find(chat => path.endsWith(chat.id))); }
    else if (path.endsWith('/tracking') && method === 'POST') { topic = { ...request.postDataJSON(), id: 'topic', chat_id: 'group-0', status: 'active', created_at: now, updated_at: now, updates: [] }; body = topic; }
    else if (path.endsWith('/tracking')) body = { tracking: topic ? [topic] : [] };
    else if (path.endsWith('/tracking/topic')) body = topic;
    else if (path === '/api/presence' || path.endsWith('/read')) body = {};
    else { failures.push(`Unexpected sync fixture API request: ${method} ${path}`); return route.fulfill({ status: 500, body: '{}' }); }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.routeWebSocket('**/api/ws', socket => {
    const index = sockets.push(socket) - 1;
    socket.onMessage(raw => frames.push({ socket: index, ...JSON.parse(raw) }));
  });
  const count = path => requests.filter(request => request === `GET ${path}`).length;
  const changed = (chat, revision) => sockets.at(-1).send(JSON.stringify({ v: 1, type: 'changed', chat_id: chat, revision }));
  const summary = async (route, revision, last_message) => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...chats[1], revision, last_message }) });
  try {
    await page.goto(origin);
    await page.locator('.chat-header h2').filter({ hasText: 'Active group' }).waitFor();
    await page.getByText('No tracked topics yet', { exact: true }).waitFor();
    await until(() => frames.some(frame => frame.command === 'presence' && frame.payload.focused_chat_id === 'group-0'), 'initial current presence');

    const bootstrapBefore = count('/api/bootstrap'), chatBefore = count('/api/chats/group-0');
    await page.getByRole('button', { name: 'New topic', exact: true }).click();
    const editor = page.locator('.modal');
    await editor.getByLabel('Topic', { exact: true }).fill('Local tracking edit');
    await editor.getByLabel('Description', { exact: true }).fill('Saved locally');
    await editor.getByRole('button', { name: 'Save', exact: true }).click();
    await page.locator('.tracking-card').filter({ hasText: 'Local tracking edit' }).waitFor();
    await page.locator('.side-drawer').getByText('Saved locally', { exact: true }).waitFor();
    assert.equal(count('/api/bootstrap'), bootstrapBefore, 'local Tracking saves do not refresh bootstrap');
    assert.equal(count('/api/chats/group-0'), chatBefore, 'local Tracking saves do not reload chat history');
    topic.description = 'Recovered through generic revision'; chats[0].revision = 2;
    changed('group-0', 2);
    await page.locator('.side-drawer').getByText(topic.description, { exact: true }).waitFor();
    await page.locator('.side-drawer').getByRole('button', { name: 'Close', exact: true }).click();

    changed('group-1', 2);
    await until(() => summaries.length === 1, 'first offscreen summary');
    for (const revision of [3, 4, 4]) changed('group-1', revision);
    await page.waitForTimeout(100);
    assert.equal(summaries.length, 1, 'offscreen revisions share the in-flight summary');
    await summary(summaries[0], 4, 'Latest summary');
    await page.locator('.chat-preview').filter({ hasText: 'Latest summary' }).waitFor();
    await page.waitForTimeout(100);
    assert.equal(summaries.length, 1, 'a summary that covers all observed revisions needs no retry');

    changed('group-1', 5);
    await until(() => summaries.length === 2, 'summary requiring a follow-up');
    changed('group-1', 6);
    await summary(summaries[1], 5, 'Intermediate summary');
    await until(() => summaries.length === 3, 'one summary follow-up for the newer revision');
    await summary(summaries[2], 6, 'Caught up summary');
    await page.locator('.chat-preview').filter({ hasText: 'Caught up summary' }).waitFor();

    changed('group-1', 7);
    await until(() => summaries.length === 4, 'summary before switching chats');
    chats[1] = { ...chats[1], revision: 8, last_message: 'Newer detail summary' };
    await page.getByRole('button', { name: /Other group.*Caught up summary/ }).click();
    await page.locator('.chat-header h2').filter({ hasText: 'Other group' }).waitFor();
    await summary(summaries[3], 7, 'Obsolete summary');
    await page.waitForTimeout(100);
    assert.equal(await page.locator('.chat-preview').filter({ hasText: 'Obsolete summary' }).count(), 0, 'a delayed summary cannot overwrite newer detail');
    await page.locator('.chat-preview').filter({ hasText: 'Newer detail summary' }).waitFor();

    messages = Array.from({ length: 30 }, (_, index) => ({ id: `scroll-${index}`, chat_id: 'group-1', seq: index + 1, author_id: 'owner-1', kind: 'human', body: `History message ${index}`, created_at: now, read_by: ['owner-0'], state: 'published' }));
    chats[1].revision = 9; changed('group-1', 9);
    await page.locator('article[data-message-id="scroll-29"]').waitFor();
    await page.locator('.message-area').evaluate(area => { area.scrollTop = 400; });
    holdDetail = true; chats[1].revision = 10; changed('group-1', 10);
    await until(() => heldDetails.length === 1, 'history refresh waiting for HTTP');
    const scrollTop = await page.locator('.message-area').evaluate(area => { area.scrollTop = 900; return area.scrollTop; });
    holdDetail = false;
    await heldDetails[0].fulfill({ contentType: 'application/json', body: JSON.stringify(chatDetail(chats[1])) });
    await page.waitForTimeout(150);
    assert.ok(Math.abs(await page.locator('.message-area').evaluate(area => area.scrollTop) - scrollTop) < 3, 'history refresh preserves scrolling performed while HTTP was pending');

    await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Replay keeps this submission key');
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await until(() => frames.some(frame => frame.command === 'send'), 'unacknowledged business send');
    const send = frames.find(frame => frame.command === 'send'), previousSocket = sockets.length - 1;
    await sockets.at(-1).close({ code: 1001, reason: 'Synthetic reconnect' });
    await page.getByRole('button', { name: /Active group/ }).first().click();
    await page.locator('.chat-header h2').filter({ hasText: 'Active group' }).waitFor();
    await until(() => sockets.length > previousSocket + 1 && frames.some(frame => frame.socket > previousSocket && frame.command === 'presence'), 'current presence on reconnect');
    const reconnected = frames.filter(frame => frame.socket > previousSocket);
    const heartbeats = reconnected.filter(frame => frame.command === 'presence');
    assert.ok(heartbeats.every(frame => frame.payload.focused_chat_id === 'group-0'), 'reconnect sends current presence without replaying previous chat focus');
    assert.equal(heartbeats.length, 1, 'unacknowledged heartbeats are not business retries');
    assert.equal(reconnected.find(frame => frame.command === 'send')?.command_id, send.command_id, 'business sends still replay with their original idempotency key');
    sockets.at(-1).send(JSON.stringify({ v: 1, type: 'ack', command_id: send.command_id, result: {} }));
    assert.deepEqual(failures, []);
    console.log('Chat sync regression passed: coalesced offscreen revisions and catch-up, stale summary rejected, Tracking save avoids global reload while generic revision recovery works, in-flight history refresh preserves scrolling, reconnect sends only current presence and preserves business send retries.');
  } finally { await page.close(); }
}

module.exports = { chatSyncRegression };
