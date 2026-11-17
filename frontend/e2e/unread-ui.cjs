/** Real App and layout; explicitly mocked HTTP, WebSocket and Notification boundaries.
 * These checks do not claim PostgreSQL persistence or native OS notification delivery.
 */
const assert = require('node:assert/strict');

async function until(check, label) {
  const deadline = Date.now() + 7000;
  do {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 20));
  } while (Date.now() < deadline);
  throw Error(`Timed out: ${label}`);
}

const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

async function fixture(browser, origin, initialCount = 1, sidebarOpen = false) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 760 } });
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  const failures = [], requests = [], sockets = [], frames = [], reads = [], held = [];
  page.on('pageerror', error => failures.push(error.message));
  const baseline = Date.now();
  const actors = ['Alex', 'Blair', 'Casey'].map((name, index) => ({ id: `owner-${index}`, owner_id: `owner-${index}`, kind: 'human', name, initials: name[0], color: '#5264ad' }));
  const chats = ['Lobby', 'Unread group'].map((title, index) => ({ id: `unread-${index}`, kind: 'group', title, revision: 1, member_owner_ids: actors.map(actor => actor.id) }));
  const messages = new Map(chats.map(chat => [chat.id, []]));
  const receipts = new Set();
  const control = { holdRead: false, holdBootstrap: false, holdDetail: false, holdSummary: false, readStatus: 200 };
  const settings = { reply_for_me_enabled: false, prompt_override: null, sidebar_open: sidebarOpen, sidebar_tab: 'workspace' };
  function append(chatId, body, { fresh = true } = {}) {
    const rows = messages.get(chatId), seq = rows.length + 1;
    const message = { id: `${chatId}-message-${seq}`, chat_id: chatId, seq, author_id: 'owner-1', author_owner_id: 'owner-1', kind: 'human', body, created_at: new Date(baseline + (fresh ? 1000 + seq : -60000)).toISOString(), read_by: [], state: 'published' };
    rows.push(message);
    chats.find(chat => chat.id === chatId).revision++;
    return message;
  }
  for (let index = 1; index <= initialCount; index++) append(chats[1].id, `Unread message ${index}`, { fresh: false });
  function summary(chat) {
    const rows = messages.get(chat.id).filter(message => message.state === 'published'), latest = rows.at(-1);
    return { ...chat, unread_count: rows.filter(row => !receipts.has(row.id)).length, last_message: latest?.body || '', last_message_meta: latest ? { id: latest.id, seq: latest.seq, author_id: latest.author_id, kind: latest.kind, created_at: latest.created_at, unread: !receipts.has(latest.id) } : null };
  }
  function detail(chat, url) {
    const before = Number(url.searchParams.get('before_seq') || Infinity), limit = Number(url.searchParams.get('limit') || 30);
    const authorized = messages.get(chat.id).filter(message => message.state === 'published' && message.seq < before);
    const rows = authorized.slice(-limit);
    return { revision: chat.revision, chat: summary(chat), members: actors, messages: rows.map(message => ({ ...message, read_by: receipts.has(message.id) ? ['owner-0'] : [] })), requests: [], approvals: [], questions: [], settings, has_more: authorized.length > rows.length, before_seq: rows[0]?.seq || null };
  }
  async function completeRead(entry, { status = control.readStatus, bumpRevision = true } = {}) {
    if (status !== 200) { entry.status = status; return json(entry.route, { detail: 'Fixture read request failed' }, status); }
    const changed = entry.ids.some(id => !receipts.has(id));
    entry.ids.forEach(id => receipts.add(id));
    const chat = chats.find(chat => chat.id === entry.chatId);
    if (changed && bumpRevision) chat.revision++;
    entry.status = 200;
    return json(entry.route, { ok: true, message_ids: entry.ids, chat: summary(chat) });
  }
  await page.addInitScript(() => {
    window.__unreadProbe = { focused: true, notices: [], focusCalls: 0 };
    Object.defineProperty(document, 'hasFocus', { configurable: true, value: () => window.__unreadProbe.focused });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => window.__unreadProbe.focused ? 'visible' : 'hidden' });
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => !window.__unreadProbe.focused });
    window.focus = () => { window.__unreadProbe.focusCalls++; };
    class TestNotification {
      static permission = 'granted';
      static async requestPermission() { return 'granted'; }
      constructor(title, options) { this.title = title; this.options = options; this.closed = false; window.__unreadProbe.notices.push(this); }
      close() { this.closed = true; this.onclose?.(new Event('close')); }
    }
    window.Notification = TestNotification;
  });
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (!path.startsWith('/api/')) return route.continue();
    requests.push({ method, path, search: url.search });
    let body, hold;
    const chatId = path.split('/')[3], chat = chats.find(chat => chat.id === chatId);
    if (path === '/api/config') body = { development_mode: true };
    else if (path === '/api/auth/me') body = { current_owner_id: 'owner-0', owner: actors[0], development_mode: true };
    else if (path === '/api/bootstrap') {
      body = { actors, chats: chats.map(summary), bots: [], reminders: [], current_owner_id: 'owner-0', model_configured: true, development_mode: true, server_time: new Date(baseline).toISOString() };
      if (control.holdBootstrap) hold = 'bootstrap';
    } else if (path === '/api/me/twin/credentials') body = { configured: false, credentials: [] };
    else if (path === '/api/me/twin/credential-requests') body = { configured: false, requests: [] };
    else if (path === '/api/me/twin/reset') body = { status: 'idle', generation: 0, private_chat_id: 'private' };
    else if (path === '/api/me/twin') body = { actor: { id: 'twin-0', owner_id: 'owner-0', kind: 'twin', name: 'Duet', initials: 'D', color: '#5264ad' } };
    else if (chat && path.endsWith('/read')) {
      const entry = { route, chatId, ids: request.postDataJSON().message_ids };
      reads.push(entry);
      if (!control.holdRead) await completeRead(entry);
      return;
    } else if (chat && path.endsWith('/summary')) { body = summary(chat); if (control.holdSummary) hold = 'summary'; }
    else if (chat && path === `/api/chats/${chatId}`) { body = detail(chat, url); if (control.holdDetail) hold = 'detail'; }
    else if (chat && path.endsWith('/settings') && method === 'PATCH') { Object.assign(settings, request.postDataJSON()); body = settings; }
    else if (path.endsWith('/tracking')) body = { tracking: [] };
    else if (path.endsWith('/workspace')) body = [];
    else if (path.endsWith('/shared-files')) body = { files: [] };
    else if (path === '/api/presence') body = { ok: true };
    else { failures.push(`Unexpected unread fixture API request: ${method} ${path}`); return json(route, {}, 500); }
    if (hold) { held.push({ kind: hold, route, body: structuredClone(body) }); return; }
    await json(route, body);
  });
  await page.routeWebSocket('**/api/ws', socket => {
    sockets.push(socket);
    socket.onMessage(raw => frames.push(JSON.parse(raw)));
  });
  const event = value => sockets.at(-1).send(JSON.stringify({ v: 1, ...value }));
  const changed = (chat = chats[1], revision = chat.revision) => event({ type: 'changed', chat_id: chat.id, revision });
  const row = (index = 1) => page.locator('.chat-item').filter({ hasText: chats[index].title });
  const badge = (index = 1) => row(index).locator('.chat-unread');
  async function choose(index = 1) { await row(index).click(); await page.locator('.chat-header h2').filter({ hasText: chats[index].title }).waitFor(); }
  async function focus(value) {
    await page.evaluate(value => {
      window.__unreadProbe.focused = value;
      window.dispatchEvent(new Event(value ? 'focus' : 'blur'));
      document.dispatchEvent(new Event('visibilitychange'));
    }, value);
  }
  const notices = () => page.evaluate(() => window.__unreadProbe.notices.map(notice => ({ title: notice.title, body: notice.options.body, closed: notice.closed })));
  await page.goto(origin);
  await page.locator('.chat-header h2').filter({ hasText: 'Lobby' }).waitFor();
  await until(() => frames.some(frame => frame.command === 'presence'), 'connected fixture');
  return { page, chats, messages, receipts, control, reads, held, requests, failures, append, summary, completeRead, event, changed, row, badge, choose, focus, notices,
    async close() { await context.close(); assert.deepEqual(failures, []); } };
}

async function acknowledgements(browser, origin) {
  const f = await fixture(browser, origin);
  try {
    f.control.holdSummary = true;
    f.chats[1].revision++;
    f.changed();
    await until(() => f.held.some(entry => entry.kind === 'summary'), 'summary before opening');
    f.control.holdRead = true;
    await f.choose();
    await until(() => f.reads.length === 1, 'visible message read request');
    assert.equal(await f.badge().innerText(), '1', 'a pending read never clears the badge optimistically');
    f.control.holdDetail = true;
    f.control.holdBootstrap = true;
    f.chats[1].revision++;
    f.changed();
    f.event({ type: 'personal_changed', section: 'chats' });
    await until(() => ['detail', 'bootstrap'].every(kind => f.held.some(entry => entry.kind === kind)), 'pre-read snapshots in flight');
    const historyRequests = f.requests.filter(request => request.path === '/api/chats/unread-1').length;
    const bootstrapRequests = f.requests.filter(request => request.path === '/api/bootstrap').length;
    await f.completeRead(f.reads[0]);
    await f.badge().waitFor({ state: 'hidden' });
    assert.equal(f.requests.filter(request => request.path === '/api/chats/unread-1').length, historyRequests, 'the read ACK updates the badge without reloading history');
    assert.equal(f.requests.filter(request => request.path === '/api/bootstrap').length, bootstrapRequests, 'the read ACK does not require a bootstrap refresh');
    for (const kind of ['detail', 'bootstrap', 'summary']) {
      const snapshot = f.held.find(entry => entry.kind === kind);
      await json(snapshot.route, snapshot.body);
      await f.page.waitForTimeout(150);
      assert.equal(await f.badge().count(), 0, `a delayed ${kind} cannot restore an acknowledged unread badge`);
    }
    // Release a coalesced summary follow-up, if the older summary did not cover the last hint.
    for (const snapshot of f.held.slice(3)) await json(snapshot.route, f.summary(f.chats[1]));
    f.control.holdSummary = false;
    f.control.holdDetail = false;
    f.control.holdBootstrap = false;
    // A new message can commit while the visible-message receipt is in flight.
    // Its summary is in the ACK, but its body still needs the changed-hint fetch.
    const pendingIndex = f.reads.length;
    f.append(f.chats[1].id, 'A shown message whose receipt is pending');
    f.changed();
    await until(() => f.reads.length > pendingIndex, 'next visible receipt in flight');
    const next = f.append(f.chats[1].id, 'A new incoming message during the read');
    await f.completeRead(f.reads[pendingIndex]);
    await until(async () => Number(await f.badge().innerText()) === 1, 'read ACK preserves the newly arrived unread message');
    assert.equal(await f.page.locator(`article[data-message-id="${next.id}"]`).count(), 0);
    f.control.holdRead = false;
    f.changed();
    await f.page.locator(`article[data-message-id="${next.id}"]`).waitFor();
    await f.badge().waitFor({ state: 'hidden' });
    assert.ok(f.receipts.has(next.id));
  } finally { await f.close(); }
}

async function visibleGroupMessages(browser, origin) {
  const f = await fixture(browser, origin, 45);
  try {
    await f.focus(false);
    await f.choose();
    await f.page.waitForTimeout(200);
    assert.equal(f.reads.length, 0, 'loading a hidden conversation marks nothing read');
    assert.equal(await f.page.locator('article[data-message-id]').count(), 30);
    assert.equal(await f.page.locator('article[data-message-id="unread-1-message-1"]').count(), 0);
    await f.focus(true);
    await until(() => f.receipts.has('unread-1-message-45'), 'latest shown message read');
    assert.equal(f.receipts.has('unread-1-message-16'), false, 'loaded but offscreen group messages stay unread');
    assert.equal(f.receipts.has('unread-1-message-1'), false, 'unloaded group history stays unread');
    assert.ok(f.receipts.size > 0 && f.receipts.size < 30);
    await until(async () => Number(await f.badge().innerText()) === 45 - f.receipts.size, 'partial read count applied');
    await f.page.locator('.message-area').evaluate(area => { area.scrollTop = 0; });
    await until(() => f.receipts.has('unread-1-message-16'), 'scrolling reads newly shown messages');
    assert.equal(f.receipts.has('unread-1-message-1'), false);
    // Loading an earlier page preserves the current anchor. It does not imply
    // that every newly fetched record was shown.
    await f.page.getByRole('button', { name: 'Load earlier messages', exact: true }).click();
    await f.page.locator('article[data-message-id="unread-1-message-1"]').waitFor({ state: 'attached' });
    await f.page.waitForTimeout(150);
    assert.equal(f.receipts.has('unread-1-message-1'), false, 'pagination alone does not read an offscreen message');
    await f.page.locator('.message-area').evaluate(area => { area.scrollTop = 0; });
    await until(() => f.receipts.has('unread-1-message-1'), 'explicit viewing of the earliest page');
    assert.ok(Number(await f.badge().innerText()) > 0, 'remaining unshown middle messages keep their unread count');
  } finally { await f.close(); }
}

async function failedReadRecovery(browser, origin) {
  const f = await fixture(browser, origin);
  try {
    f.control.readStatus = 503;
    await f.choose();
    await f.page.locator('.read-error[role="alert"]').waitFor();
    assert.equal(await f.badge().innerText(), '1');
    assert.equal(f.receipts.size, 0, 'a failed write cannot make the message read');
    if (process.env.TWINTEAMS_UNREAD_UI_SCREENSHOT) await f.page.screenshot({ path: process.env.TWINTEAMS_UNREAD_UI_SCREENSHOT, fullPage: true });
    const attempts = f.reads.length;
    await f.page.waitForTimeout(4200);
    assert.equal(f.reads.length, attempts, 'presence heartbeats do not poll a failing read endpoint');
    f.control.readStatus = 200;
    await f.page.locator('.read-error').getByRole('button', { name: 'Retry', exact: true }).click();
    await f.badge().waitFor({ state: 'hidden' });
    await f.page.locator('.read-error').waitFor({ state: 'hidden' });
    assert.equal(f.receipts.size, 1);
    assert.equal(f.reads.length, attempts + 1, 'explicit retry recovers without a reload or another message');

    f.control.holdRead = true;
    const pendingIndex = f.reads.length;
    const uncertain = f.append(f.chats[1].id, 'Receipt committed before the response failed');
    f.changed();
    await until(() => f.reads.length > pendingIndex, 'read before a simulated response failure');
    // The server committed, but the client did not receive a successful ACK.
    // An idempotent retry returns the canonical summary without another revision.
    f.receipts.add(uncertain.id);
    f.chats[1].revision++;
    const committedRevision = f.chats[1].revision;
    await f.completeRead(f.reads[pendingIndex], { status: 503 });
    await f.page.locator('.read-error').waitFor();
    assert.equal(await f.badge().innerText(), '1', 'an uncertain response preserves the last confirmed badge');
    f.control.holdRead = false;
    await f.page.locator('.read-error').getByRole('button', { name: 'Retry', exact: true }).click();
    await f.badge().waitFor({ state: 'hidden' });
    await f.page.locator('.read-error').waitFor({ state: 'hidden' });
    assert.equal(f.chats[1].revision, committedRevision, 'an already-persisted receipt is reconciled without requiring a new revision hint');
    assert.deepEqual(f.reads.at(-1).ids, f.reads[pendingIndex].ids);
  } finally { await f.close(); }
}

async function retryAfterScrolling(browser, origin) {
  const f = await fixture(browser, origin, 45);
  try {
    for (let seq = 16; seq <= 35; seq++) f.receipts.add(`unread-1-message-${seq}`);
    f.chats[1].revision++;
    f.control.readStatus = 503;
    await f.choose();
    await f.page.locator('.read-error').waitFor();
    await f.page.locator('.message-area').evaluate(area => { area.scrollTop = 0; });
    await f.page.waitForTimeout(150);
    const beforeRetry = f.reads.length;
    f.control.readStatus = 200;
    await f.page.locator('.read-error').getByRole('button', { name: 'Retry', exact: true }).click();
    await f.page.locator('.read-error').waitFor({ state: 'hidden' });
    assert.equal(f.reads.length, beforeRetry, 'Retry on already-read visible rows must not submit an empty or offscreen read');
    assert.equal(f.receipts.has('unread-1-message-45'), false);
    await f.page.locator('.message-area').evaluate(area => { area.scrollTop = area.scrollHeight; });
    await until(() => f.receipts.has('unread-1-message-45'), 'viewing failed messages after Retry with no candidates');
    assert.equal(f.reads.length, beforeRetry + 1, 'Retry rearms visible reads after scrolling without a permanent failed-state block');
  } finally { await f.close(); }
}

async function hiddenMobileTranscript(browser, origin) {
  const f = await fixture(browser, origin, 1, true);
  try {
    await f.focus(false);
    await f.choose();
    await f.page.setViewportSize({ width: 390, height: 760 });
    await f.page.locator('.sidebar:not(.collapsed)').waitFor();
    await f.focus(true);
    await f.page.waitForTimeout(150);
    assert.equal(f.reads.length, 0, 'the mobile chat-list overlay obscures loaded messages, so they remain unread');
    await f.page.getByRole('button', { name: 'Show or hide chat list', exact: true }).click();
    await f.page.locator('.detail-sidebar').waitFor();
    await f.page.waitForTimeout(150);
    assert.equal(f.reads.length, 0, 'the default open Shared files drawer still obscures the mobile transcript');
    await f.page.locator('.detail-sidebar').getByRole('button', { name: 'Close', exact: true }).click();
    await until(() => f.receipts.has('unread-1-message-1'), 'closing the mobile drawer reveals and reads the message');
    await f.badge().waitFor({ state: 'hidden' });
    await f.focus(false);
    await f.page.getByRole('button', { name: 'Find in chat', exact: true }).click();
    const hiddenBySearch = f.append(f.chats[1].id, 'Incoming message behind the search overlay');
    f.changed();
    await f.page.locator(`article[data-message-id="${hiddenBySearch.id}"]`).waitFor({ state: 'attached' });
    await f.focus(true);
    await f.page.waitForTimeout(150);
    assert.equal(f.receipts.has(hiddenBySearch.id), false, 'the mobile search overlay does not count as seeing the transcript');
    await f.page.locator('.chat-find-panel').getByRole('button', { name: 'Close', exact: true }).click();
    await until(() => f.receipts.has(hiddenBySearch.id), 'closing search resumes visible read detection');
  } finally { await f.close(); }
}

async function unavailableReadRecovery(browser, origin) {
  const f = await fixture(browser, origin);
  try {
    f.control.holdRead = true;
    await f.choose();
    await until(() => f.reads.length === 1, 'visible message awaiting a receipt');
    const beforeReload = f.requests.filter(request => request.path === '/api/chats/unread-1').length;
    f.messages.get(f.chats[1].id)[0].state = 'withdrawn';
    f.chats[1].revision++;
    await f.completeRead(f.reads[0], { status: 404 });
    await f.page.locator('article[data-message-id="unread-1-message-1"]').waitFor({ state: 'detached' });
    await f.badge().waitFor({ state: 'hidden' });
    await f.page.locator('.read-error').waitFor({ state: 'hidden' });
    assert.equal(f.requests.filter(request => request.path === '/api/chats/unread-1').length, beforeReload + 1, 'an unavailable receipt reauthorizes the current conversation once');
    assert.equal(f.receipts.has('unread-1-message-1'), false, 'an unavailable message is not declared read');
    f.control.holdRead = false;
    const next = f.append(f.chats[1].id, 'A visible message after the withdrawal');
    f.changed();
    await until(() => f.receipts.has(next.id), 'new visible messages remain readable after a rejected stale ID');
  } finally { await f.close(); }
}

async function notifications(browser, origin) {
  const f = await fixture(browser, origin, 0);
  try {
    await f.focus(false);
    const clicked = f.append(f.chats[1].id, 'Click this unread reminder');
    f.changed();
    await until(async () => (await f.notices()).length === 1, 'first incoming desktop notification');
    await f.focus(true);
    await f.page.waitForTimeout(150);
    assert.equal((await f.notices())[0].closed, false, 'focusing an unrelated conversation does not dismiss an unread reminder');
    assert.equal(f.receipts.has(clicked.id), false);
    await f.focus(false);
    await f.page.evaluate(() => window.__unreadProbe.notices[0].onclick(new Event('click')));
    await f.page.locator('.chat-header h2').filter({ hasText: 'Unread group' }).waitFor();
    await f.page.locator(`article[data-message-id="${clicked.id}"]`).waitFor();
    assert.equal((await f.notices())[0].closed, true, 'click immediately dismisses that reminder');
    assert.equal(await f.page.evaluate(() => window.__unreadProbe.focusCalls), 1);
    assert.equal(f.receipts.has(clicked.id), false, 'notification navigation alone does not read a hidden page');
    await f.focus(true);
    await f.badge().waitFor({ state: 'hidden' });
    assert.equal(f.receipts.has(clicked.id), true);
    f.changed();
    await f.page.waitForTimeout(200);
    assert.equal((await f.notices()).length, 1, 'read-state refresh never replays the clicked message');

    // Fill a real scrollable transcript, then leave its viewport on older rows.
    for (let index = 2; index <= 45; index++) f.append(f.chats[1].id, `Older unread row ${index}`);
    f.changed();
    await f.page.locator('article[data-message-id="unread-1-message-45"]').waitFor();
    await f.page.locator('.message-area').evaluate(area => { area.scrollTop = 0; });
    await f.page.waitForTimeout(150);
    await f.focus(false);
    const unseen = f.append(f.chats[1].id, 'This newest message is below the current viewport');
    f.changed();
    await until(async () => (await f.notices()).length === 2, 'notification for the unshown newest message');
    await f.page.locator(`article[data-message-id="${unseen.id}"]`).waitFor({ state: 'attached' });
    await f.focus(true);
    await f.page.waitForTimeout(200);
    assert.equal(f.receipts.has(unseen.id), false, 'looking at earlier messages does not read the latest message');
    assert.equal((await f.notices())[1].closed, false, 'a partial read cannot dismiss a different unshown message');
    await f.page.locator('.message-area').evaluate(area => { area.scrollTop = area.scrollHeight; });
    await until(() => f.receipts.has(unseen.id), 'scroll to the notification message');
    await until(async () => (await f.notices())[1].closed, 'read acknowledgement closes the matching notification');
    assert.ok(f.summary(f.chats[1]).unread_count > 0, 'older unread messages do not keep a now-read latest reminder open');
    await f.focus(false);
    f.changed();
    await f.page.waitForTimeout(200);
    assert.equal((await f.notices()).length, 2, 'no replay for the now-read message despite remaining earlier unread messages');

    const elsewhere = f.append(f.chats[1].id, 'Read in another tab');
    f.changed();
    await until(async () => (await f.notices()).length === 3, 'another unread notification');
    f.receipts.add(elsewhere.id);
    f.chats[1].revision++;
    f.changed();
    await until(async () => (await f.notices())[2].closed, 'authorized summary closes the message read in another tab');
    assert.ok(f.summary(f.chats[1]).unread_count > 0);
    const alreadyRead = f.append(f.chats[1].id, 'Already read before this tab received its summary');
    f.receipts.add(alreadyRead.id);
    f.chats[1].revision++;
    f.changed();
    await f.page.locator(`article[data-message-id="${alreadyRead.id}"]`).waitFor({ state: 'attached' });
    await f.page.waitForTimeout(150);
    assert.equal((await f.notices()).length, 3, 'a fresh latest ID already read elsewhere must not notify for older unread messages');
  } finally { await f.close(); }
}

async function crossTabOlderNotification(browser, origin) {
  const f = await fixture(browser, origin, 0);
  let other;
  try {
    await f.focus(false);
    const older = f.append(f.chats[1].id, 'Older desktop alert still awaiting reading');
    f.changed();
    await until(async () => (await f.notices()).length === 1, 'older desktop alert');
    await f.focus(true);
    const newer = f.append(f.chats[1].id, 'Newer unread message while this tab is focused');
    f.changed();
    await f.row().locator('.chat-preview').filter({ hasText: newer.body }).waitFor();
    assert.equal((await f.notices()).length, 1, 'the newer foreground message does not replace the older desktop alert');
    assert.equal((await f.notices())[0].closed, false);
    // A real same-origin second page produces the storage event. Only the
    // confirmed-read metadata normally written by its App is supplied here.
    other = await f.page.context().newPage();
    await other.route('**/*', route => route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Read acknowledgement boundary</title>' }));
    await other.goto(`${origin}/read-acknowledgement-boundary`);
    const broadcast = (ownerId, messageId = older.id) => other.evaluate(({ ownerId, chatId, messageId }) => {
      localStorage.setItem(`twinteams.notifications.read:${encodeURIComponent(ownerId)}`, JSON.stringify({ chat_id: chatId, message_ids: [messageId] }));
    }, { ownerId, chatId: f.chats[1].id, messageId });
    await broadcast('owner-1');
    await f.page.waitForTimeout(150);
    assert.equal((await f.notices())[0].closed, false, 'another owner cannot dismiss this owner’s notification');
    f.receipts.add(older.id);
    f.chats[1].revision++;
    await broadcast('owner-0');
    await until(async () => (await f.notices())[0].closed, 'same-owner exact receipt closes the older alert');
    assert.equal(f.receipts.has(newer.id), false);
    f.changed();
    await until(async () => Number(await f.badge().innerText()) === 1, 'the newer unread message retains its badge');
    assert.equal((await f.notices()).length, 1);

    await f.focus(false);
    f.control.holdSummary = true;
    const delayed = f.append(f.chats[1].id, 'Read elsewhere before the stale summary arrives');
    f.changed();
    await until(() => f.held.some(entry => entry.kind === 'summary'), 'unread summary awaiting delivery');
    const snapshot = f.held.find(entry => entry.kind === 'summary');
    f.receipts.add(delayed.id);
    f.chats[1].revision++;
    await broadcast('owner-0', delayed.id);
    await f.page.waitForTimeout(100);
    await json(snapshot.route, snapshot.body);
    await f.row().locator('.chat-preview').filter({ hasText: delayed.body }).waitFor();
    await f.page.waitForTimeout(150);
    assert.equal((await f.notices()).length, 1, 'an exact cross-tab receipt prevents a delayed unread summary from constructing a stale notification');
    f.control.holdSummary = false;
    f.changed();
    await until(async () => Number(await f.badge().innerText()) === 1, 'canonical count after a delayed summary');
  } finally { await other?.close(); await f.close(); }
}

async function unreadUiRegression(browser, origin) {
  const cases = [
    ['read ACK, delayed snapshots and concurrent new message', acknowledgements],
    ['visible-only paginated group receipts', visibleGroupMessages],
    ['failed and uncertain read retry without polling', failedReadRecovery],
    ['Retry with no visible unread candidates', retryAfterScrolling],
    ['mobile chat-list, details and search overlays hide loaded messages', hiddenMobileTranscript],
    ['withdrawn-message read rejection reauthorizes history', unavailableReadRecovery],
    ['notification click, exact viewing and cross-tab summary', notifications],
    ['exact cross-tab read metadata and delayed summary', crossTabOlderNotification],
  ];
  for (const [label, run] of cases) {
    try { await run(browser, origin); }
    catch (error) { error.message = `${label}: ${error.message}`; throw error; }
    console.log(`PASS unread UI / ${label}`);
  }
  console.log(`Unread UI: ${cases.length}/${cases.length} cases passed. Real App/layout with mocked HTTP, WebSocket and Notification boundaries; no OS toast or backend persistence claim.`);
}

module.exports = { unreadUiRegression };

if (require.main === module) {
  const { spawn } = require('node:child_process');
  const net = require('node:net');
  const path = require('node:path');
  const { chromium } = require('playwright');
  (async () => {
    const port = await new Promise((resolve, reject) => {
      const server = net.createServer();
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(() => resolve(port)); });
    });
    const vite = spawn(process.execPath, [path.resolve(__dirname, '../node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
      cwd: path.resolve(__dirname, '..'), env: { ...process.env, TWINTEAMS_API_URL: 'http://127.0.0.1:1' }, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let browser;
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(Error('Vite startup timed out')), 15000);
        vite.stdout.on('data', data => { if (data.toString().includes('Local:')) { clearTimeout(timer); resolve(); } });
        vite.once('error', error => { clearTimeout(timer); reject(error); });
        vite.once('exit', code => { clearTimeout(timer); reject(Error(`Vite exited: ${code}`)); });
      });
      browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
      await unreadUiRegression(browser, `http://127.0.0.1:${port}`);
    } finally { await browser?.close(); vite.kill('SIGTERM'); }
  })().catch(error => { console.error(error); process.exitCode = 1; });
}
