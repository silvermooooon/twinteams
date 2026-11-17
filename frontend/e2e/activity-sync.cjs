/** Real App rendering and revision handling with synthetic HTTP/WS transport.
 * Backend title generation and request publication are tested separately. */
const assert = require('node:assert/strict');

async function activitySyncRegression(browser, origin) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
  const failures = [], reads = [], heldDetails = [], activityCancellations = [];
  page.on('pageerror', error => failures.push(error.message));
  page.on('requestfailed', request => { if (new URL(request.url()).pathname === '/api/chats/private/activity') activityCancellations.push(request.failure()?.errorText); });
  const now = '2026-11-17T08:00:00Z';
  const owner = { id: 'owner-0', kind: 'human', name: 'Alex', initials: 'A', color: '#5264ad' };
  const twin = { id: 'twin-0', owner_id: owner.id, kind: 'twin', name: 'Alex Duet', initials: 'AD', color: '#5264ad' };
  const peer = { id: 'owner-1', kind: 'human', name: 'Blair', initials: 'B', color: '#5264ad' };
  const peerTwin = { id: 'twin-1', owner_id: peer.id, kind: 'twin', name: 'Blair Duet', initials: 'BD', color: '#5264ad' };
  const third = { id: 'owner-2', kind: 'human', name: 'Casey', initials: 'C', color: '#5264ad' };
  const actors = [owner, twin, peer, peerTwin, third];
  const chats = [
    { id: 'private', kind: 'private', title: twin.name, revision: 1, member_owner_ids: [owner.id], unread_count: 0 },
    { id: 'pair', kind: 'group', title: peer.name, revision: 1, member_owner_ids: [owner.id, peer.id], unread_count: 0 },
    { id: 'multi', kind: 'group', title: 'Release team', revision: 1, member_owner_ids: [owner.id, peer.id, third.id], unread_count: 0 },
  ];
  const peerPrivate = { id: 'blair-private', kind: 'private', title: peerTwin.name, revision: 1, member_owner_ids: [peer.id], unread_count: 0 };
  // Include a stale Activity preference on human chats to exercise its redirect.
  const settings = { reply_for_me_enabled: false, prompt_override: null, sidebar_open: true, sidebar_tab: 'activity' };
  const privateSettings = { ...settings };
  const messages = [], requests = [], activity = [];
  let socket, acceptedAt, revisionBurst, holdActivity, account = owner, holdDetail = false;
  let sent;
  const receivedSend = new Promise(resolve => { sent = resolve; });
  let detailHeld;
  const overlappingRead = new Promise(resolve => { detailHeld = resolve; });
  const detail = chat => ({ revision: chat.revision, chat, members: actors.filter(actor => chat.member_owner_ids.includes(actor.owner_id || actor.id)), messages: messages.filter(message => message.chat_id === chat.id), requests: requests.filter(request => request.chat_id === chat.id), approvals: [], questions: [], settings: chat.id === 'private' ? privateSettings : settings, has_more: false, before_seq: null });
  const reply = (route, body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) return route.continue();
    reads.push({ path, method: request.method() });
    let body;
    if (path === '/api/config') body = { development_mode: true };
    else if (path === '/api/auth/me') body = { current_owner_id: account.id, owner: account, development_mode: true };
    else if (path === '/api/auth/logout') body = {};
    else if (path === '/api/auth/login') { account = peer; body = { current_owner_id: account.id, owner: account, development_mode: true }; }
    else if (path === '/api/bootstrap') body = { actors, chats: account === owner ? chats : [peerPrivate], bots: [], reminders: [], current_owner_id: account.id, model_configured: true, development_mode: true, server_time: now };
    else if (path === '/api/me/twin/credentials') body = { configured: false, credentials: [] };
    else if (path === '/api/me/twin/credential-requests') body = { configured: false, requests: [] };
    else if (path === '/api/me/twin/reset') body = { status: 'idle', generation: 0, private_chat_id: account === owner ? 'private' : peerPrivate.id };
    else if (path === '/api/me/twin/onboarding') body = { enabled: false, status: 'disabled', chat_id: account === owner ? 'private' : peerPrivate.id, welcome_message_id: null };
    else if (path === '/api/me/twin') body = { actor: account === owner ? twin : peerTwin };
    else if (/^\/api\/chats\/[^/]+$/.test(path)) {
      const chat = [...chats, peerPrivate].find(chat => path.endsWith(`/${chat.id}`));
      body = detail(chat);
      if (holdDetail && chat.id === 'private') { heldDetails.push({ route, body: structuredClone(body) }); detailHeld(); return; }
    } else if (path === '/api/chats/private/activity') {
      body = { items: structuredClone(activity), has_more: false, next_cursor: null };
      if (holdActivity) { const held = holdActivity; holdActivity = undefined; held({ route, body }); return; }
      if (activity.length) await new Promise(resolve => setTimeout(resolve, 350));
    }
    else if (path === `/api/chats/${peerPrivate.id}/activity`) body = { items: [], has_more: false, next_cursor: null };
    else if (path === '/api/chats/private/activity/session/run') body = { run: activity[0], events: [] };
    else if (path === '/api/chats/private/settings') { Object.assign(privateSettings, request.postDataJSON()); body = privateSettings; }
    else if (path.endsWith('/summary')) body = chats.find(chat => path === `/api/chats/${chat.id}/summary`);
    else if (path.endsWith('/workspace')) body = [];
    else if (path.endsWith('/shared-files')) body = { files: [] };
    else if (path === '/api/presence' || path.endsWith('/read')) body = {};
    else { failures.push(`Unexpected activity fixture API request: ${request.method()} ${path}`); return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: failures.at(-1) }) }); }
    await reply(route, body);
  });
  await page.routeWebSocket('**/api/ws', connection => {
    socket = connection;
    connection.onMessage(raw => {
      const frame = JSON.parse(raw);
      if (frame.command !== 'send') return;
      assert.equal(frame.chat_id, 'private');
      const message = { id: 'owner-message', chat_id: 'private', seq: 1, author_id: owner.id, kind: 'human', body: frame.payload.body, created_at: now, read_by: [owner.id], state: 'published', client_request_key: frame.payload.client_request_key };
      const request = { id: 'request', chat_id: 'private', source_message_id: message.id, target_twin_id: twin.id, requester_id: owner.id, source: 'message', status: 'running', accepted_at: now, can_cancel: true, can_retry: false, session_id: 'session', run_id: 'run', client_request_key: frame.payload.client_request_key };
      messages.push(message); requests.push(request);
      activity.push({ session_id: request.session_id, run_id: request.run_id, source: 'user', status: 'running', started_at: now, title: 'Duet response' });
      chats[0].revision = 2;
      acceptedAt = Date.now();
      revisionBurst = setInterval(() => { chats[0].revision++; connection.send(JSON.stringify({ v: 1, type: 'changed', chat_id: 'private', revision: chats[0].revision })); }, 110);
      connection.send(JSON.stringify({ v: 1, type: 'ack', command_id: frame.command_id, accepted: 'business_persisted', result: { message, request } }));
      connection.send(JSON.stringify({ v: 1, type: 'changed', chat_id: 'private', revision: 2 }));
      sent();
    });
  });
  const changed = revision => { chats[0].revision = revision; socket.send(JSON.stringify({ v: 1, type: 'changed', chat_id: 'private', revision })); };
  try {
    await page.goto(origin);
    await page.getByRole('tab', { name: 'Activity', exact: true }).waitFor();
    await page.locator('.detail-content .empty-state').waitFor();
    const activityReadsBeforeSend = reads.filter(read => read.path.endsWith('/activity')).length;
    activityCancellations.length = 0;
    const rawInput = 'Review the release plan, investigate the outstanding rollout dependencies, and prepare a concise recommendation covering owners, dates, and delivery risks.';
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill(rawInput);
    await page.getByRole('button', { name: 'Send message', exact: true }).click();
    await receivedSend;
    const row = page.locator('.run-card');
    await row.getByRole('heading', { name: 'Duet response', exact: true }).waitFor({ timeout: 1500 });
    const visibleMs = Date.now() - acceptedAt;
    clearInterval(revisionBurst);
    assert.equal(await row.count(), 1);
    assert.equal(await row.locator('.run-status.running').count(), 1);
    assert.ok(!(await row.innerText()).includes(rawInput), 'the running Activity title never substitutes raw owner input');
    assert.ok(visibleMs < 1500, 'the row appears before a delayed title generation could finish');
    assert.ok(chats[0].revision > 2, 'ordinary revisions overlap the 350 ms Activity response');
    assert.ok(reads.filter(read => read.path.endsWith('/activity')).length - activityReadsBeforeSend <= 2, 'Activity reads coalesce while the first response is in flight');
    assert.deepEqual(activityCancellations, [], 'same-chat revisions cannot repeatedly cancel the first Activity response');
    await row.click();
    await page.locator('.side-drawer').getByRole('heading', { name: 'Duet response', exact: true }).waitFor();

    // An opening reply causes a refresh while its title is still pending.
    messages.push({ id: 'opening', chat_id: 'private', seq: 2, author_id: twin.id, kind: 'twin', body: 'I will review the plan and report the delivery risks.', created_at: now, read_by: [owner.id], state: 'published', request_id: requests[0].id });
    holdDetail = true;
    const openingRevision = chats[0].revision + 1;
    changed(openingRevision);
    await overlappingRead;
    assert.equal(heldDetails[0].body.revision, openingRevision);
    activity[0].title = 'Review release risks';
    changed(openingRevision + 1);
    // Allow the existing 80 ms debounce to register the newer revision while
    // the previous authorized chat read is deliberately held in the harness.
    await page.waitForTimeout(120);
    holdDetail = false;
    const privateReads = reads.filter(read => read.path === '/api/chats/private').length;
    await reply(heldDetails[0].route, heldDetails[0].body);
    await page.locator('article[data-message-id="opening"]').waitFor();
    await row.getByRole('heading', { name: 'Review release risks', exact: true }).waitFor();
    await page.locator('.side-drawer').getByRole('heading', { name: 'Review release risks', exact: true }).waitFor();
    assert.ok(reads.filter(read => read.path === '/api/chats/private').length > privateReads, 'a revision arriving during a chat read causes a follow-up read');
    assert.equal(await row.count(), 1, 'asynchronous title update preserves the same Activity row');
    assert.equal(await row.locator('.run-status.running').count(), 1);
    assert.ok(!(await row.innerText()).includes(rawInput));
    await page.locator('.side-drawer').getByRole('button', { name: 'Close', exact: true }).click();
    await page.locator('.side-drawer').waitFor({ state: 'hidden' });

    async function holdRefresh() {
      const held = new Promise(resolve => { holdActivity = resolve; });
      changed(chats[0].revision + 1);
      return held;
    }
    const failedRead = await holdRefresh();
    activity[0].summary = 'Reviewed rollout dependencies';
    changed(chats[0].revision + 1);
    await page.waitForTimeout(120);
    await failedRead.route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Synthetic Activity read failed' }) });
    await page.locator('.error-toast').getByText('Synthetic Activity read failed', { exact: true }).waitFor();
    await row.getByText('Reviewed rollout dependencies', { exact: true }).waitFor({ timeout: 2000 });
    await page.locator('.error-toast').getByRole('button', { name: 'Dismiss', exact: true }).click();

    // A response from an old tab/chat/account must not repopulate its successor.
    const heldTab = await holdRefresh();
    const tabAbort = page.waitForEvent('requestfailed', request => new URL(request.url()).pathname === '/api/chats/private/activity');
    await page.getByRole('tab', { name: 'Workspace', exact: true }).click();
    await tabAbort;
    const savedRun = activity.pop();
    await page.getByRole('tab', { name: 'Activity', exact: true }).click();
    await page.locator('.detail-content').getByText('No runs yet', { exact: true }).waitFor();
    await reply(heldTab.route, heldTab.body);
    assert.equal(await row.count(), 0, 'an aborted previous-tab response cannot restore removed rows');
    activity.push(savedRun);
    changed(chats[0].revision + 1);
    await row.getByRole('heading', { name: 'Review release risks', exact: true }).waitFor();

    const heldChat = await holdRefresh();
    const chatAbort = page.waitForEvent('requestfailed', request => new URL(request.url()).pathname === '/api/chats/private/activity');

    for (const chat of chats.slice(1)) {
      await page.locator('.conversation-list .chat-item').filter({ hasText: chat.title }).click();
      await page.locator('.chat-header h2').filter({ hasText: chat.title }).waitFor();
      await page.getByRole('tab', { name: 'Shared files', exact: true }).waitFor();
      assert.equal(await page.getByRole('tab', { name: 'Activity', exact: true }).count(), 0, 'human pair and group chats have no Activity tab');
      assert.equal(await page.locator('.run-card').count(), 0);
      assert.equal(reads.filter(read => read.path === `/api/chats/${chat.id}/activity`).length, 0, 'human chats never request Activity even with a stale Activity setting');
    }
    await chatAbort;
    activity.pop();
    await page.locator('.conversation-list .chat-item').filter({ hasText: twin.name }).click();
    await page.locator('.detail-content').getByText('No runs yet', { exact: true }).waitFor();
    await reply(heldChat.route, heldChat.body);
    assert.equal(await row.count(), 0, 'an aborted previous-chat response cannot restore removed rows');
    activity.push(savedRun);
    changed(chats[0].revision + 1);
    await row.getByRole('heading', { name: 'Review release risks', exact: true }).waitFor();

    const heldAccount = await holdRefresh();
    const accountAbort = page.waitForEvent('requestfailed', request => new URL(request.url()).pathname === '/api/chats/private/activity');
    await page.getByRole('button', { name: 'Account', exact: true }).click();
    await page.getByRole('button', { name: 'Sign out / switch account', exact: true }).click();
    await accountAbort;
    await page.getByPlaceholder('DEV-AAA').fill('DEV-BBB');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    await page.locator('.chat-header h2').filter({ hasText: peerTwin.name }).waitFor();
    await page.locator('.detail-content').getByText('No runs yet', { exact: true }).waitFor();
    await reply(heldAccount.route, heldAccount.body);
    assert.equal(await row.count(), 0, 'a late account A response cannot populate account B Activity');
    assert.equal(await page.locator('article[data-message-id="owner-message"]').count(), 0);
    assert.deepEqual(failures, []);
    console.log(`Activity sync regression passed: synthetic ACK-to-visible running row ${visibleMs} ms with 350 ms Activity HTTP latency and 110 ms revision hints; asynchronous list/drawer title update and pending-revision recovery after a failed read; tab/chat/account cancellation; and no Activity in human pair/group chats.`);
  } finally { clearInterval(revisionBurst); await page.close(); }
}

module.exports = { activitySyncRegression };
