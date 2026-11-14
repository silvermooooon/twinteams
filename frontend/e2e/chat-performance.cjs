/** Real Chrome timings against canonical isolated history. CDP changes network
 * latency only; responses, authorization and WebSocket events remain real. */
const { assert, chooseChat, poll, sleep } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

const cases = [{
  name: 'performance / large private history cold open, revisit, refresh and rapid switching',
  timeout: 600000,
  run: async t => {
    const seed = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [
      path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'), 'chat-performance-history',
      '--state', process.env.TWINTEAMS_E2E_STATE,
    ], { encoding: 'utf8' }));
    assert.equal(seed.test_only_canonical_history_seed, true);
    assert.equal(seed.model_runs_added, 0);
    t.record('seed', seed);
    const { page, context } = await t.session(null);
    page.setDefaultTimeout(120000);
    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
    const requests = [], pending = new Set(), notifications = [];
    page.on('websocket', socket => socket.on('framereceived', event => {
      try {
        const { type, chat_id, revision, section } = JSON.parse(String(event.payload));
        notifications.push({ at: Date.now(), type, chat_id, revision, section });
      } catch {}
    }));
    page.on('request', request => {
      const url = new URL(request.url());
      if (!url.pathname.startsWith('/api/') || request.method() !== 'GET' && !url.pathname.endsWith('/read')) return;
      const entry = { method: request.method(), path: url.pathname + url.search, start: Date.now(), request };
      requests.push(entry); pending.add(request);
    });
    page.on('response', async response => {
      const row = requests.find(row => row.request === response.request());
      if (row) {
        row.status = response.status();
        if (/\/chats\/[^/]+\?/.test(row.path)) {
          try { const snapshot = await response.json(); row.revision = snapshot.revision; row.messages = snapshot.messages?.length; } catch {}
        }
      }
    });
    page.on('requestfinished', request => {
      pending.delete(request);
      const row = requests.find(row => row.request === request);
      if (row) { row.end = Date.now(); row.timing = request.timing(); }
    });
    page.on('requestfailed', request => {
      pending.delete(request);
      const row = requests.find(row => row.request === request);
      if (row) { row.end = Date.now(); row.failure = request.failure()?.errorText; }
    });
    const latest = page.locator(`article[data-message-id="${seed.latest_message_id}"]`);
    const measurements = [];
    async function measure(name, action, { maxMessages = 30, expectCache = false } = {}) {
      const from = requests.length, start = Date.now();
      await action();
      await latest.waitFor();
      const visibleMs = Date.now() - start;
      if (expectCache) {
        await poll(() => requests.slice(from).some(row => row.path === `/api/chats/${seed.private_chat_id}?limit=30`), 'background chat synchronization started');
        assert.ok(requests.slice(from).some(row => row.path === `/api/chats/${seed.private_chat_id}?limit=30` && !row.end), 'cached messages are visible while HTTP synchronization is still pending');
        assert.ok(visibleMs < 700, 'a warm switch does not wait for the simulated 700 ms network round trip');
      }
      await poll(() => !pending.size, 'visible page requests settle', { timeout: 120000 });
      await sleep(150);
      const rows = requests.slice(from).map(({ request, start: began, end, timing, ...rest }) => ({
        ...rest, startedMs: began - start, durationMs: end ? end - began : null,
        serverMs: timing?.responseStart > 0 ? Math.round(timing.responseStart - timing.requestStart) : null,
      }));
      const messages = await page.locator('article[data-message-id]').count();
      assert.ok(messages <= maxMessages, 'the displayed history window remains bounded');
      measurements.push({ name, visibleMs, messages, visibleBeforeSync: expectCache, requests: rows });
      t.record('measurements', measurements);
      assert.ok(rows.every(row => row.failure || row.status >= 200 && row.status < 300), 'fast API errors are not successful latency results');
      const slow = rows.filter(row => !row.failure && row.durationMs !== null && row.durationMs >= 3000);
      assert.deepEqual(slow.map(row => ({ path: row.path, durationMs: row.durationMs })), [], 'every successful non-AI page API must respond in under three seconds');
      assert.ok(!rows.some(row => row.path.endsWith('/tracking')), 'chat history does not fetch Tracking before its tab is opened');
      if (!name.includes('700ms')) assert.ok(visibleMs < 3000, 'normal navigation must display the conversation in under three seconds');
      if (name === 'revisit') assert.equal(rows.filter(row => row.path === `/api/chats/${seed.private_chat_id}/activity`).length, 1, 'restoring a cached sidebar and reading the same chat revision loads Activity only once');
    }
    await measure('cold_sign_in', async () => {
      await page.getByPlaceholder('DEV-AAA').fill('DEV-AAA');
      await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    });
    await chooseChat(page, seed.other_chat_title);
    await page.locator('.chat-header h2').filter({ hasText: seed.other_chat_title }).waitFor();
    await measure('revisit', () => chooseChat(page, seed.private_chat_title));
    await measure('refresh', () => page.reload());
    await page.getByRole('button', { name: 'Load earlier messages', exact: true }).click();
    await poll(() => page.locator('article[data-message-id]').count().then(count => count > 30), 'earlier history loaded');
    const expanded = await page.locator('article[data-message-id]').count();
    const anchor = await page.locator('.message-area').evaluate(area => {
      area.scrollTop = Math.min(700, area.scrollHeight - area.clientHeight);
      const bounds = area.getBoundingClientRect();
      const message = [...area.querySelectorAll('article[data-message-id]')].find(row => row.getBoundingClientRect().top >= bounds.top);
      return { id: message.dataset.messageId, offset: message.getBoundingClientRect().top - bounds.top };
    });
    await sleep(100);
    t.record('anchor_before_revisit', { anchor, offset: await page.locator(`article[data-message-id="${anchor.id}"]`).evaluate(message => message.getBoundingClientRect().top - document.querySelector('.message-area').getBoundingClientRect().top) });
    t.record('expanded_window', { expanded, displayed: await page.locator('article[data-message-id]').count(), notifications: [...notifications],
      requests: requests.map(({ request, timing, ...row }) => row) });
    await chooseChat(page, seed.other_chat_title);
    await page.locator('.chat-header h2').filter({ hasText: seed.other_chat_title }).waitFor();
    await measure('revisit_after_older_page', () => chooseChat(page, seed.private_chat_title), { maxMessages: 130 });
    const restored = await page.locator(`article[data-message-id="${anchor.id}"]`).evaluate(message => message.getBoundingClientRect().top - document.querySelector('.message-area').getBoundingClientRect().top);
    t.record('anchor_after_revisit', { expected: anchor.offset, restored });
    assert.ok(Math.abs(restored - anchor.offset) < 3, 'returning to the bounded older window preserves its visible scroll anchor');
    await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 700, downloadThroughput: -1, uploadThroughput: -1 });
    const beforeRead = notifications.length;
    const receipt = await page.request.post(`/api/chats/${seed.private_chat_id}/read`, { data: { message_ids: ['performance-message-4901'] } });
    assert.equal(receipt.status(), 200);
    const receiptNotice = await poll(() => notifications.slice(beforeRead).find(row => row.type === 'changed' && row.chat_id === seed.private_chat_id), 'real read receipt invalidates the current revision', { interval: 5 });
    await chooseChat(page, seed.other_chat_title);
    await page.locator('.chat-header h2').filter({ hasText: seed.other_chat_title }).waitFor();
    await measure('revisit_during_read_receipt_700ms_latency', () => chooseChat(page, seed.private_chat_title), { maxMessages: 130 });
    assert.equal(await page.locator('article[data-message-id]').count(), 130, 'an invalidated body keeps its bounded window position for reauthorization');
    const receiptRestored = await page.locator(`article[data-message-id="${anchor.id}"]`).evaluate(message => message.getBoundingClientRect().top - document.querySelector('.message-area').getBoundingClientRect().top);
    assert.ok(Math.abs(receiptRestored - anchor.offset) < 3, 'read receipt synchronization cannot discard the scroll anchor');
    t.record('receipt_window', { receiptRevision: receiptNotice.revision, restoredOffset: receiptRestored, messages: 130 });
    await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await page.getByRole('button', { name: 'Load earlier messages', exact: true }).click();
    await poll(() => page.locator('article[data-message-id]').count().then(count => count > 130), 'history exceeds the cache window');
    await chooseChat(page, seed.other_chat_title);
    await page.locator('.chat-header h2').filter({ hasText: seed.other_chat_title }).waitFor();
    await measure('revisit_after_cache_cap', () => chooseChat(page, seed.private_chat_title));
    await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 700, downloadThroughput: -1, uploadThroughput: -1 });
    await measure('refresh_700ms_latency', () => page.reload());
    await chooseChat(page, seed.other_chat_title);
    await page.locator('.chat-header h2').filter({ hasText: seed.other_chat_title }).waitFor();
    await measure('revisit_700ms_latency', () => chooseChat(page, seed.private_chat_title), { expectCache: true });
    await measure('rapid_switch_700ms_latency', async () => {
      const other = page.locator('.conversation-list .chat-item').filter({ hasText: seed.other_chat_title }).first();
      const own = page.locator('.conversation-list .chat-item').filter({ hasText: seed.private_chat_title }).first();
      await other.click(); await own.click(); await other.click(); await own.click();
    }, { expectCache: true });
    assert.equal(await page.locator('.chat-header h2').innerText(), seed.private_chat_title);
    await page.screenshot({ path: path.join(t.artifactDir, 'large-history-private.png'), fullPage: true });
    t.record('history', { expanded, scrollAnchor: anchor, restoredOffset: restored, over130ReturnsLatest30: true, latestMessageId: seed.latest_message_id, networkLatencyMs: 700, persistentPrivateCacheAdded: false });
  },
}];
module.exports = { cases };
