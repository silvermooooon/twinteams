/** Real browser receipts, real wall time, Redis presence, router and GPT-6-Luna.
 * Only read-only API/fixture inspection accompanies normal UI mutations.
 * No business interception, focus overrides, clock changes or route settlement.
 */
const { assert, api, poll, sleep, sendMessage, openTwinTab } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');

function inspect(operation = 'inspect') {
  const value = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [
    path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'),
    operation, '--state', process.env.TWINTEAMS_E2E_STATE,
  ], { encoding: 'utf8' }));
  assert.equal(value.browser_e2e, true);
  assert.equal(value.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  return value;
}

async function startChat(page, names, title) {
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  for (const name of names) {
    await page.locator('.people-picker-inline').getByRole('combobox', {
      name: 'Search people', exact: true,
    }).fill(name);
    await page.getByRole('option', { name, exact: true }).click();
  }
  if (title) await page.getByLabel('Group name (optional)', { exact: true }).fill(title);
  await page.getByRole('button', { name: 'Start chat', exact: true }).click();
  await page.locator('.new-chat-main').waitFor({ state: 'hidden' });
  await page.locator('.composer textarea').waitFor();
  return poll(async () => (await api(page, '/bootstrap')).chats.find(chat =>
    chat.kind === 'group' && (title ? chat.title === title :
      chat.member_owner_ids.length === 2 && chat.member_owner_ids.includes('owner-maya'))),
  'UI-created target chat');
}

async function enterChat(page, chatId) {
  // Return from My Twin through its real UI; a reload does not stand in for focus.
  if (await page.getByRole('button', { name: 'Back to chats', exact: true }).isVisible())
    await page.getByRole('button', { name: 'Back to chats', exact: true }).click();
  const chat = await poll(async () => (await api(page, '/bootstrap')).chats.find(c => c.id === chatId),
    'recipient bootstrap contains target chat');
  const row = page.locator('.conversation-list .chat-item').filter({
    has: page.locator('.chat-name').and(page.getByText(chat.title, { exact: true })),
  });
  await row.waitFor();
  await row.click();
  await page.locator('.composer textarea').click();
  await poll(() => page.evaluate(() => document.visibilityState === 'visible' && document.hasFocus()),
    'actual recipient page is visible and focused');
}

function evidence(value, sourceId) {
  const auto = value.automatic_reply_evidence;
  assert.ok(auto, 'isolated metadata inspector exposes persisted routing and receipts');
  return {
    route: auto.routes.find(row => row.message_id === sourceId),
    candidates: auto.candidates.filter(row => row.message_id === sourceId && row.target_twin_id === 'twin-maya'),
    receipts: auto.receipts.filter(row => row.message_id === sourceId && row.owner_id === 'owner-maya'),
    requests: value.requests.filter(row => row.source_message_id === sourceId && row.target_twin_id === 'twin-maya'),
    outputs: auto.messages.filter(row => row.author_id === 'twin-maya' && row.kind === 'twin' &&
      value.requests.some(request => request.source_message_id === sourceId && request.id === row.request_id)),
  };
}

async function waitBeyond(createdAt, milliseconds = 11500) {
  await sleep(Math.max(0, new Date(createdAt).getTime() + milliseconds - Date.now()));
}

const cases = [{
  name: 'live read/unread / actual receipts suppress M1 and M3 while unread M2 replies once after ten real seconds in pair and group',
  timeout: 540000,
  retainTrace: true,
  run: async t => {
    assert.equal(process.env.TWINTEAMS_E2E_MODEL_MODE, 'live-gpt-6-luna');
    assert.equal(process.env.TWINTEAMS_E2E_SCENARIO, 'read-unread-live');
    const observed = { reads: [], presence: [] };
    const sender = await t.session('DEV-AAA');
    const recipient = await t.session('DEV-BBB', { baseURL: t.secondaryURL,
      contextSetup: context => context.on('page', page => page.on('websocket', socket => {
        const pendingPresence = new Map();
        socket.on('framesent', event => {
          try {
            const frame = JSON.parse(String(event.payload));
            if (frame.type === 'command' && frame.command === 'presence')
              pendingPresence.set(frame.command_id, frame.payload);
          } catch { /* Observe only normal JSON command frames. */ }
        });
        socket.on('framereceived', event => {
          try {
            const frame = JSON.parse(String(event.payload));
            if (frame.type === 'ack' && frame.accepted === 'presence_renewed' && pendingPresence.has(frame.command_id)) {
              observed.presence.push({ at: new Date().toISOString(), transport: 'real-websocket-ack',
                ...pendingPresence.get(frame.command_id) });
              pendingPresence.delete(frame.command_id);
            }
          } catch { /* No WebSocket interception or payload changes. */ }
        });
      })),
    });
    const config = await api(sender.page, '/config');
    assert.equal(config.automatic_response_delay_seconds, 10);
    recipient.page.on('response', response => {
      const request = response.request();
      if (request.method() !== 'POST' || response.status() !== 200) return;
      const pathname = new URL(response.url()).pathname;
      if (/\/chats\/[^/]+\/read$/.test(pathname))
        observed.reads.push({ at: new Date().toISOString(), path: pathname, ...request.postDataJSON() });
      if (pathname === '/api/presence')
        observed.presence.push({ at: new Date().toISOString(), transport: 'real-http-200', ...request.postDataJSON() });
    });
    const results = [];
    t.record('boundaries', { realModel: 'gpt-6-luna', realWaitSeconds: 10,
      productionAccounts: false, fakeClock: false, routeSettlement: false,
      directReadApi: false, focusVisibilityOverrides: false,
      unreadSetup: 'Recipient uses real My Twin Profile UI; target-chat messages are not mounted, owner remains online.' });

    for (const kind of ['pair', 'group']) {
      const label = t.unique(kind);
      const chat = await startChat(sender.page, kind === 'pair' ? ['BBB'] : ['BBB', 'CCC'],
        kind === 'group' ? 'Read unread ' + label : undefined);
      await recipient.page.bringToFront();
      await enterChat(recipient.page, chat.id);
      const send = async stage => {
        const marker = label + '-' + stage;
        const body = `@BBB 请你回答这个新问题：7加5等于多少？请只回复“${marker}：12”，不要反问。只请BBB回应，其他成员不需要回应。`;
        await sender.page.bringToFront();
        const row = await sendMessage(sender.page, body);
        const id = await row.getAttribute('data-message-id');
        const message = (await api(sender.page, '/chats/' + chat.id)).messages.find(m => m.id === id);
        assert.ok(message);
        return { id, marker, createdAt: message.created_at, body };
      };
      const readOnScreen = async message => {
        await recipient.page.bringToFront();
        await recipient.page.locator(`article[data-message-id="${message.id}"]`).waitFor();
        await recipient.page.locator(`article[data-message-id="${message.id}"]`).scrollIntoViewIfNeeded();
        await recipient.page.locator('.composer textarea').click();
        await poll(async () => (await api(sender.page, '/chats/' + chat.id)).messages
          .find(m => m.id === message.id)?.read_by.includes('owner-maya'),
        'real UI generated a persisted per-message read receipt', { timeout: 8500 });
        assert.ok(observed.reads.some(read => read.path.includes(chat.id) && read.message_ids.includes(message.id)),
          'receipt came from the app, not a test POST');
      };
      const leave = async () => {
        const leftAt = Date.now();
        await openTwinTab(recipient.page, 'Profile');
        assert.equal(await recipient.page.locator('article[data-message-id]').count(), 0,
          'target message DOM is unmounted while owner is elsewhere');
        await poll(() => observed.presence.some(row => row.focused_chat_id === null && row.visible === true &&
          new Date(row.at).getTime() >= leftAt), 'real online presence outside chat');
        return recipient.page.evaluate(() => ({ visibility: document.visibilityState, focused: document.hasFocus(), hash: location.hash }));
      };

      const first = await send('M1');
      await readOnScreen(first);
      const firstRead = inspect();
      const firstReceipt = evidence(firstRead, first.id).receipts[0];
      assert.ok(firstReceipt);
      assert.ok(new Date(firstReceipt.seen_at) - new Date(first.createdAt) < 10000,
        'M1 really read within the response window');
      const leftM1 = await leave();
      await sender.page.bringToFront();
      await waitBeyond(first.createdAt);
      const firstState = await poll(() => {
        const result = evidence(inspect(), first.id);
        return result.route?.status === 'completed' && result;
      }, 'real router settles M1', { timeout: 45000, interval: 1000 });
      assert.equal(firstState.route.needs_response, true, 'M1 suppression is not a no-response router decision');
      assert.equal(firstState.requests.length, 0);
      assert.equal(firstState.outputs.length, 0);
      assert.equal(firstState.candidates.length, 1);
      assert.equal(firstState.candidates[0].state, 'suppressed');
      await recipient.page.screenshot({ path: path.join(t.artifactDir, `${kind}-M1-left-read.png`), fullPage: true });
      t.record(kind + '_M1', { ...first, left: leftM1, receipt: firstReceipt, actual: firstState,
        checkedAfterMs: Date.now() - new Date(first.createdAt).getTime() });

      const presence = inspect('presence');
      assert.equal(presence.owners['owner-maya'].online, true);
      const second = await send('M2');
      await waitBeyond(second.createdAt, 8500);
      const beforeDeadline = evidence(inspect(), second.id);
      assert.equal(beforeDeadline.receipts.length, 0, 'new M2 has no receipt inherited from M1');
      assert.equal(beforeDeadline.requests.length, 0, 'online owner gets the full actual ten-second response window');
      const accepted = await poll(() => {
        const value = evidence(inspect(), second.id);
        if (value.route?.status === 'failed') {
          t.record(kind + '_M2_failed_route', { source: second, persisted: value });
          throw new Error('The real model router failed before M2 could enter the automatic reply gate');
        }
        if (value.requests.some(request => request.status === 'failed'))
          throw new Error('M2 automatic request failed');
        return value.requests.length && value;
      }, 'actual automatic request after wall-clock deadline', { timeout: 90000, interval: 700 });
      assert.equal(accepted.requests.length, 1);
      const request = accepted.requests[0];
      assert.ok(new Date(request.accepted_at) - new Date(second.createdAt) >= 10000,
        'durable acceptance time is at least ten real seconds after source');
      const replyState = await poll(() => {
        const value = evidence(inspect(), second.id);
        if (value.requests.some(request => request.status === 'failed')) throw new Error('M2 model task failed');
        return value.outputs.length && value.requests.every(request => request.status === 'completed') && value;
      }, 'actual GPT-6-Luna reply persisted', { timeout: 150000, interval: 1000 });
      assert.equal(replyState.outputs.length, 1);
      const reply = (await api(sender.page, '/chats/' + chat.id)).messages.find(m => m.id === replyState.outputs[0].id);
      assert.ok(reply.body.includes(second.marker), 'real response addresses this unique new message');
      assert.ok(reply.body.includes('12'));
      await recipient.page.bringToFront();
      await enterChat(recipient.page, chat.id);
      await recipient.page.locator(`article[data-message-id="${reply.id}"] .message-text`).filter({ hasText: second.marker }).waitFor();
      await recipient.page.screenshot({ path: path.join(t.artifactDir, `${kind}-M2-reopened-actual-reply.png`), fullPage: true });
      const secondResult = { ...second, presence, beforeDeadline, accepted: replyState,
        acceptanceDelayMs: new Date(request.accepted_at) - new Date(second.createdAt),
        modelReplyDelayMs: new Date(reply.created_at) - new Date(second.createdAt),
        reply: { id: reply.id, body: reply.body, createdAt: reply.created_at }, reopenedUiVerified: true };
      t.record(kind + '_M2', secondResult);

      const third = await send('M3');
      await readOnScreen(third);
      const thirdReceipt = evidence(inspect(), third.id).receipts[0];
      assert.ok(new Date(thirdReceipt.seen_at) - new Date(third.createdAt) < 10000);
      const leftM3 = await leave();
      await sender.page.bringToFront();
      await waitBeyond(third.createdAt);
      const thirdState = await poll(() => {
        const value = evidence(inspect(), third.id);
        return value.route?.status === 'completed' && value;
      }, 'real router settles M3', { timeout: 45000, interval: 1000 });
      assert.equal(thirdState.route.needs_response, true);
      assert.equal(thirdState.requests.length, 0);
      assert.equal(thirdState.outputs.length, 0);
      assert.equal(thirdState.candidates[0]?.state, 'suppressed');
      const finalSnapshot = inspect();
      const finalFirst = evidence(finalSnapshot, first.id);
      assert.equal(finalFirst.requests.length, 0, 'M1 remains suppressed after later messages');
      assert.equal(finalFirst.outputs.length, 0);
      const finalSecond = evidence(finalSnapshot, second.id);
      assert.equal(finalSecond.requests.length, 1, 'M2 remains exactly one request after another full response window');
      assert.equal(finalSecond.outputs.length, 1, 'M2 remains exactly one canonical Twin reply');
      t.record(kind + '_M3', { ...third, left: leftM3, receipt: thirdReceipt, actual: thirdState,
        checkedAfterMs: Date.now() - new Date(third.createdAt).getTime() });
      results.push({ kind, chatId: chat.id, M1: first.id, M2: second.id, M3: third.id,
        acceptanceDelayMs: secondResult.acceptanceDelayMs, replyDelayMs: secondResult.modelReplyDelayMs });
    }
    await fs.writeFile(path.join(t.artifactDir, 'read-unread-ui-network.json'), JSON.stringify(observed, null, 2));
    t.record('summary', { flows: results, actualModel: 'gpt-6-luna', directReadCalls: 0,
      fakeClock: false, routeSettlement: false, businessInterception: false, trueBrowserNavigation: true });
  },
}];

module.exports = { cases };
