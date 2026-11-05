/** Real browser cache acceptance: canonical fixtures and real Redis invalidation,
 * with latency applied by Chrome rather than replacement HTTP responses. */
const { assert, chooseChat, login, logout, poll, sleep } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
function fixture(role) {
  return JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [
    path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'), role,
    '--state', process.env.TWINTEAMS_E2E_STATE,
  ], { encoding: 'utf8' }));
}
const cases = [{
  name: 'cache / five recent chats, source withdrawal, reconnect and account boundaries',
  timeout: 180000,
  run: async t => {
    const seed = fixture('chat-cache-seed'); assert.equal(seed.test_only_cache_seed, true);
    const pending = new Set(), requests = [], frames = [], timings = []; let closes = 0;
    const { page, context } = await t.session('DEV-AAA', { contextSetup: async context => {
      context.on('page', page => {
        page.on('request', request => {
          const url = new URL(request.url());
          if (url.pathname.startsWith('/api/') && (request.method() === 'GET' || url.pathname.endsWith('/read'))) {
            pending.add(request); requests.push({ request, path: url.pathname + url.search, completed: false });
          }
        });
        const complete = request => { pending.delete(request); const entry = requests.find(row => row.request === request); if (entry) entry.completed = true; };
        page.on('requestfinished', complete); page.on('requestfailed', complete);
        page.on('websocket', socket => {
          socket.on('close', () => { closes++; });
          socket.on('framereceived', event => { try { frames.push(JSON.parse(String(event.payload))); } catch {} });
        });
      });
    } });
    const cdp = await context.newCDPSession(page); await cdp.send('Network.enable');
    const latency = offline => cdp.send('Network.emulateNetworkConditions', { offline, latency: 700, downloadThroughput: -1, uploadThroughput: -1 });
    const output = page.locator(`article[data-message-id="${seed.private_output_id}"]`);
    await output.waitFor();
    async function settled() { await sleep(200); await poll(() => !pending.size, 'chat requests settle'); }
    async function open(title, messageId, cached) {
      const from = requests.length, began = Date.now();
      await chooseChat(page, title);
      await page.locator(`article[data-message-id="${messageId}"]`).waitFor();
      const visibleMs = Date.now() - began;
      timings.push({ title, visibleMs, expectedCached: cached ?? null, pendingRequests: requests.slice(from).filter(row => !row.completed).map(row => row.path) });
      t.record('navigation', timings);
      if (cached) {
        assert.ok(visibleMs < 700, 'cached history is visible before a network round trip');
        assert.ok(requests.slice(from).some(row => !row.completed && /\/chats\/[^/]+\?limit=30$/.test(row.path)), 'canonical synchronization continues behind the cached page');
      } else if (cached === false) assert.ok(visibleMs >= 650, 'evicted history is reauthorized before display');
      await settled();
      return visibleMs;
    }
    await settled(); await latency(false);
    await open(seed.groups[1].title, seed.groups[1].latest_message_id);
    const initialWarm = await open(seed.private_chat_title, seed.private_output_id, true);
    for (const group of seed.groups) await open(group.title, group.latest_message_id);
    const recentWarm = await open(seed.groups[4].title, seed.groups[4].latest_message_id, true);
    const evictedPrivate = await open(seed.private_chat_title, seed.private_output_id, false);
    await open(seed.groups[4].title, seed.groups[4].latest_message_id, true);
    const beforeClose = closes, beforeReconnect = frames.length;
    // Offline emulation can leave an established socket alive. Close the real
    // isolated connection; no frames or business responses are replaced.
    const prototype = await cdp.send('Runtime.evaluate', { expression: 'WebSocket.prototype' });
    const sockets = await cdp.send('Runtime.queryObjects', { prototypeObjectId: prototype.result.objectId });
    const closed = await cdp.send('Runtime.callFunctionOn', { objectId: sockets.objects.objectId, returnByValue: true, functionDeclaration: 'function () { const active = this.filter(socket => socket.readyState === WebSocket.OPEN); for (const socket of active) socket.close(1000, "Isolated acceptance disconnect"); return active.length; }' });
    assert.equal(closed.exceptionDetails, undefined);
    assert.ok(closed.result.value > 0, 'the fault injection closed a live browser WebSocket');
    await poll(() => closes > beforeClose, 'real WebSocket disconnect');
    await poll(() => frames.slice(beforeReconnect).some(frame => frame.type === 'changed'), 'real subscriptions resumed after reconnect');
    await settled();
    const reconnectPrivate = await open(seed.private_chat_title, seed.private_output_id, false);
    await open(seed.groups[4].title, seed.groups[4].latest_message_id, true);
    const synchronization = page.waitForRequest(request => new URL(request.url()).pathname === `/api/chats/${seed.private_chat_id}` && request.method() === 'GET');
    await chooseChat(page, seed.private_chat_title); await output.waitFor(); await synchronization;
    await sleep(150);
    const priorFrames = frames.length, withdrawn = fixture('chat-cache-withdraw');
    assert.equal(withdrawn.tracking_invalidation_published, true);
    await poll(() => frames.slice(priorFrames).some(frame => frame.type === 'personal_changed' && frame.section === 'tracking'), 'real cross-source invalidation reached this browser');
    await output.waitFor({ state: 'detached' }); await settled();
    await open(seed.groups[2].title, seed.groups[2].latest_message_id);
    await page.evaluate(id => {
      window.__cacheLeak = false;
      window.__cacheObserver = new MutationObserver(() => { if (document.querySelector(`article[data-message-id="${id}"]`)) window.__cacheLeak = true; });
      window.__cacheObserver.observe(document.body, { subtree: true, childList: true });
    }, seed.private_output_id);
    await chooseChat(page, seed.private_chat_title); await settled();
    assert.equal(await output.count(), 0, 'withdrawn sources cannot return through a cached private review');
    assert.equal(await page.evaluate(() => { window.__cacheObserver.disconnect(); return window.__cacheLeak; }), false, 'withdrawn private output never flashed during navigation');
    await logout(page); await login(page, 'DEV-BBB'); await settled();
    assert.equal(await output.count(), 0, 'switching account cannot reuse the previous owner snapshot');
    await logout(page); await login(page, 'DEV-AAA'); await settled();
    assert.equal(await output.count(), 0, 'signing back in reauthorizes the withdrawn source');
    t.record('cache', { maximumRecentChats: 5, initialWarmMs: initialWarm, recentWarmMs: recentWarm, evictedPrivateMs: evictedPrivate, afterReconnectMs: reconnectPrivate, realCrossSourceInvalidation: true, oldInflightSnapshotNotReused: true, withdrawnSnapshotNeverFlashed: true, accountIsolation: true, networkLatencyMs: 700, modelMode: 'deterministic-offline' });
  },
}];
module.exports = { cases };
