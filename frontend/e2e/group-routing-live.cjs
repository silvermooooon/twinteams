/** Investigation of actual N-to-N routing. All mutations are normal browser UI.
 * Genuine model decisions are recorded even when they contradict expectations.
 * No route settlement, hidden business writes, interception or artificial time.
 */
const { assert, api, poll, sleep, sendMessage, openTwinTab } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const targeted = process.env.TWINTEAMS_E2E_SCENARIO === 'group-routing-follow-on';
const followOnPrompt = '@BBB（Beacon Duet）：这条消息只请你先回应。请你在本群给CCC的Cedar Duet发出下一条新问题：“CCC，9乘6等于多少？请只回复‘接力答案：54’，不要再提问。” 你现在只提出这个问题，不要自己计算或回答。CCC无需回答我这条消息，等你收到Beacon Duet的新问题再答；AAA不需要收到额外回复。';

async function sourceManifest() {
  const root = path.resolve(__dirname, '../..');
  const changed = ['diff', '--name-only', 'HEAD'];
  const untracked = ['ls-files', '--others', '--exclude-standard'];
  const paths = [...new Set([
    ...execFileSync('git', changed, { cwd: root, encoding: 'utf8' }).trim().split('\n'),
    ...execFileSync('git', untracked, { cwd: root, encoding: 'utf8' }).trim().split('\n'),
    'backend/checks/browser_e2e.py', 'backend/checks/browser_e2e_fixture.py',
    'backend/checks/browser_e2e_live.py', 'frontend/e2e/run.cjs',
    'frontend/e2e/group-routing-live.cjs',
  ])].filter(name => /^(?:backend\/(?:app|checks)\/.*\.py|frontend\/(?:src\/.*\.(?:tsx?|css)|e2e\/.*\.cjs))$/.test(name)).sort();
  return Object.fromEntries(await Promise.all(paths.map(async name => [name,
    createHash('sha256').update(await fs.readFile(path.join(root, name))).digest('hex')])));
}

function inspect(operation = 'inspect') {
  const value = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [
    path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'),
    operation, '--state', process.env.TWINTEAMS_E2E_STATE,
  ], { encoding: 'utf8' }));
  assert.equal(value.browser_e2e, true);
  assert.equal(value.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  return value;
}

async function profile(session, name) {
  await session.page.bringToFront();
  if (!await session.page.getByRole('tab', { name: 'Profile', exact: true }).isVisible())
    await openTwinTab(session.page, 'Profile');
  if (name) {
    await session.page.getByLabel('Duet name', { exact: true }).fill(name);
    await session.page.getByRole('button', { name: 'Save', exact: true }).click();
    await poll(async () => (await api(session.page, '/me/twin')).actor.name === name, 'real profile rename');
  }
  assert.equal(await session.page.locator('article[data-message-id]').count(), 0);
}

async function enter(session, chat) {
  const page = session.page;
  await page.bringToFront();
  if (await page.getByRole('button', { name: 'Back to chats', exact: true }).isVisible())
    await page.getByRole('button', { name: 'Back to chats', exact: true }).click();
  await poll(async () => (await api(page, '/bootstrap')).chats.some(row => row.id === chat.id), 'group reaches recipient bootstrap');
  const row = page.locator('.conversation-list .chat-item').filter({
    has: page.locator('.chat-name').and(page.getByText(chat.title, { exact: true })),
  });
  await row.waitFor(); await row.click();
  await page.locator('.composer textarea').click();
}

async function createGroup(session, title, names = ['BBB', 'CCC']) {
  const page = session.page;
  const owner = (await api(page, '/auth/me')).current_owner_id;
  const peerIds = [];
  for (const name of names) {
    const person = (await api(page, '/people?q=' + encodeURIComponent(name)))
      .find(actor => actor.kind === 'human' && actor.name === name);
    assert.ok(person);
    peerIds.push(person.id);
  }
  const memberKey = [owner, ...peerIds].sort().join('|');
  await page.getByRole('button', { name: 'Back to chats', exact: true }).click();
  await page.getByRole('button', { name: 'New chat', exact: true }).click();
  for (const name of names) {
    await page.locator('.people-picker-inline').getByRole('combobox', { name: 'Search people', exact: true }).fill(name);
    await page.getByRole('option', { name, exact: true }).click();
  }
  if (names.length > 1)
    await page.getByLabel('Group name (optional)', { exact: true }).fill(title);
  await page.getByRole('button', { name: 'Start chat', exact: true }).click();
  await page.locator('.new-chat-main').waitFor({ state: 'hidden' });
  return poll(async () => (await api(page, '/bootstrap')).chats.find(row =>
    row.kind === 'group' && (names.length > 1 ? row.title === title :
      [...row.member_owner_ids].sort().join('|') === memberKey)), 'UI-created target group');
}

function chainEvidence(snapshot, sourceId) {
  const auto = snapshot.automatic_reply_evidence;
  const source = auto.messages.find(row => row.id === sourceId);
  const messages = auto.messages.filter(row => row.causal_chain_id === source.causal_chain_id);
  const ids = new Set(messages.map(row => row.id));
  const requests = snapshot.requests.filter(row => row.causal_chain_id === source.causal_chain_id);
  const routes = auto.routes.filter(row => ids.has(row.message_id));
  const routeCommands = new Set([...ids, ...routes.map(row => row.command_id).filter(Boolean)]);
  const candidates = auto.candidates.filter(row => ids.has(row.message_id));
  return { source, messages, requests, routes, candidates,
    receipts: auto.receipts.filter(row => ids.has(row.message_id)),
    chain: auto.chains.find(row => row.id === source.causal_chain_id),
    reservations: auto.reservations.filter(row => row.causal_chain_id === source.causal_chain_id),
    sdkCommands: auto.sdk_commands.filter(row => routeCommands.has(row.command_id) || requests.some(request => request.id === row.command_id)),
    routingOutputs: snapshot.routing_outputs.filter(output => auto.sdk_commands.some(command =>
      routeCommands.has(command.command_id) && command.session_id === output.session_id && command.run_id === output.run_id)),
    providerAttempts: snapshot.live_budget.attempts.length };
}

const cases = [{
  name: targeted ? 'live group routing / targeted fresh Duet follow-on with at most one source-owner retry after incidental ConnectError' :
    'live group routing / three humans and Duets, targets, follow-on, receipts and real presence expiry',
  timeout: 810000,
  retainTrace: true,
  run: async t => {
    assert.equal(process.env.TWINTEAMS_E2E_MODEL_MODE, 'live-gpt-6-luna');
    assert.ok(['group-routing-live', 'group-routing-follow-on'].includes(process.env.TWINTEAMS_E2E_SCENARIO));
    const budget = inspect().live_budget;
    assert.ok(budget.max_attempts >= 1 && budget.max_attempts <= 40,
      'post-fix acceptance requires an explicitly frozen ceiling of at most40 calls');
    const sourceHashes = await sourceManifest();
    await fs.writeFile(path.join(t.artifactDir, 'group-routing-source-manifest.json'), JSON.stringify(sourceHashes, null, 2));
    t.record('source_manifest', sourceHashes);
    const mismatches = [], phases = [], observed = { reads: [], presence: [] };
    const check = (ok, text) => { if (!ok) mismatches.push(text); };
    const sessions = [];
    for (const code of ['DEV-AAA', 'DEV-BBB', 'DEV-CCC']) {
      const session = await t.session(code, { baseURL: code === 'DEV-BBB' ? t.secondaryURL : t.baseURL });
      session.page.on('response', response => {
        const request = response.request();
        if (request.method() !== 'POST' || response.status() !== 200) return;
        const pathname = new URL(response.url()).pathname;
        if (/\/chats\/[^/]+\/read$/.test(pathname))
          observed.reads.push({ code, at: new Date().toISOString(), path: pathname, ...request.postDataJSON() });
      });
      sessions.push(session);
      await profile(session, ['Atlas Duet', 'Beacon Duet', 'Cedar Duet'][sessions.length - 1]);
    }
    const [a, b, c] = sessions;
    const config = await api(a.page, '/config');
    assert.equal(config.automatic_response_delay_seconds, 10);
    const chat = await createGroup(a, 'Group routing ' + t.unique('live'));
    assert.equal(chat.member_owner_ids.length, 3);
    await profile(a);
    const actors = inspect().automatic_reply_evidence.actors;
    assert.equal(actors.filter(row => row.kind === 'human').length, 3);
    assert.equal(actors.filter(row => row.kind === 'twin').length, 3);
    t.record('boundaries', { chat, actors, actualModel: 'gpt-6-luna', maxProviderAttempts: inspect().live_budget.max_attempts,
      businessWrites: 'normal browser UI only', syntheticRouter: false, fakeClock: false,
      focusOverrides: false, presenceTtlOverride: false, directReadWrites: false });

    async function send(session, body, targetChat = chat) {
      assert.ok(inspect().live_budget.attempts.length <= budget.max_attempts - 8,
        'reserve eight-call headroom before a new independent input');
      await enter(session, targetChat);
      const row = await sendMessage(session.page, body);
      const id = await row.getAttribute('data-message-id');
      const value = (await api(session.page, '/chats/' + targetChat.id + '?limit=100')).messages.find(row => row.id === id);
      assert.ok(value);
      await profile(session);
      return value;
    }

    async function settled(label, source, targetChat = chat, { allowRouteFailure = false } = {}) {
      let final, stableSince = null, signature = '';
      const deadline = Date.now() + 150000;
      while (Date.now() < deadline) {
        const snapshot = inspect();
        final = chainEvidence(snapshot, source.id);
        const pending = final.routes.some(row => row.status === 'pending') ||
          final.requests.some(row => ['queued', 'running'].includes(row.status)) ||
          final.candidates.some(row => row.state === 'waiting');
        const nextSignature = JSON.stringify([final.messages.map(row => row.id), final.routes, final.requests.map(row => [row.id, row.status]), final.candidates]);
        if (!pending && nextSignature === signature) stableSince ??= Date.now(); else stableSince = null;
        signature = nextSignature;
        if (stableSince && Date.now() - stableSince >= 3500) break;
        await sleep(1000);
      }
      const bodies = (await api(a.page, '/chats/' + targetChat.id + '?limit=100')).messages
        .filter(row => row.causal_chain_id === source.causal_chain_id);
      const phase = { label, source, ...final, bodies, observedAt: new Date().toISOString() };
      phases.push(phase); t.record(label, phase);
      for (const message of final.messages) {
        const sourceOwner = actors.find(actor => actor.id === message.author_id)?.owner_id;
        check(!final.requests.some(request => request.source_message_id === message.id &&
          actors.find(actor => actor.id === request.target_twin_id)?.owner_id === sourceOwner), label + ': source owner triggered its own Duet');
      }
      check(allowRouteFailure || !final.routes.some(row => row.status === 'failed'), label + ': real router failed');
      check(!final.requests.some(row => row.status === 'failed'), label + ': actual SDK reply failed');
      check(!final.routes.some(row => row.status === 'pending') &&
        !final.requests.some(row => ['queued', 'running'].includes(row.status)) &&
        !final.candidates.some(row => row.state === 'waiting'), label + ': did not settle');
      check(final.chain.spent <= 6 && final.chain.allowance === 6, label + ': product chain allowance changed/exceeded');
      return phase;
    }

    function oneAnswer(phase, twinId, marker, answer) {
      const output = phase.bodies.filter(message => message.kind === 'twin' && message.author_id === twinId);
      check(output.length === 1 && output[0]?.body.includes(marker) && output[0]?.body.includes(answer),
        phase.label + ': expected exactly one visible answer from ' + twinId);
    }

    if (targeted) {
      assert.ok(budget.max_attempts <= 17, 'targeted follow-on uses only the remaining17-attempt ceiling');
      const source = await send(a, followOnPrompt);
      let result = await settled('targeted_follow_on_initial', source, chat, { allowRouteFailure: true });
      const failedDuetRoute = result.routes.find(route => route.status === 'failed' &&
        result.messages.some(message => message.id === route.message_id && message.author_id === 'twin-maya'));
      let retry = null;
      if (failedDuetRoute) {
        const currentBudget = inspect().live_budget;
        assert.ok(currentBudget.attempts.some(attempt => attempt.error_type === 'ConnectError'),
          'only an incidental real ConnectError authorizes the one normal UI retry');
        assert.ok(currentBudget.attempts.length <= budget.max_attempts - 4, 'retain four-call retry headroom');
        await enter(b, chat);
        const failure = (await api(b.page, '/chats/' + chat.id + '?limit=100')).messages
          .find(message => message.id === failedDuetRoute.message_id)?.routing_failure;
        assert.ok(failure?.can_retry, 'actual Duet source-owner BBB has Retry authority');
        const notice = b.page.locator(`[data-failed-request="${failure.id}"]`);
        await notice.waitFor();
        await b.page.screenshot({ path: path.join(t.artifactDir, 'targeted-incidental-connecterror-before-retry.png'), fullPage: true });
        const [response] = await Promise.all([
          b.page.waitForResponse(response => response.request().method() === 'POST' &&
            new URL(response.url()).pathname === `/api/chats/${chat.id}/routing-decisions/${failure.id}/retry`),
          notice.getByRole('button', { name: 'Retry', exact: true }).click(),
        ]);
        assert.equal(response.status(), 200);
        const accepted = await response.json();
        assert.notEqual(accepted.command_id, failure.command_id);
        retry = { sourceOwner: 'owner-maya', failedMessageId: failedDuetRoute.message_id,
          previousCommandId: failure.command_id, acceptedCommandId: accepted.command_id,
          ordinaryBrowserUi: true, providerError: 'ConnectError', explicitRetryCount: 1 };
        t.record('targeted_real_retry', retry);
        await profile(b);
        result = await settled('targeted_follow_on_after_one_ui_retry', source);
      }
      const first = result.requests.filter(request => request.source_message_id === source.id);
      check(first.length === 1 && first[0]?.target_twin_id === 'twin-maya', 'targeted initial request did not select exactly BBB');
      const handoff = result.messages.find(message => message.author_id === 'twin-maya' &&
        result.requests.some(request => request.source_message_id === message.id && request.target_twin_id === 'twin-jordan'));
      check(!!handoff, 'targeted genuine Beacon message did not trigger Cedar');
      check(result.requests.length === 2 && result.requests.every(request => request.status === 'completed'),
        'targeted follow-on did not complete exactly two tasks');
      check(!result.routes.some(route => ['failed', 'pending'].includes(route.status)), 'targeted classifier did not complete');
      oneAnswer(result, 'twin-jordan', '接力答案', '54');
      await enter(a, chat);
      await a.page.screenshot({ path: path.join(t.artifactDir, 'targeted-follow-on-final-real-browser.png'), fullPage: true });
      assert.deepEqual(await sourceManifest(), sourceHashes, 'targeted frozen source changed during the campaign');
      t.record('summary', { targetedFollowOnOnly: true, sourceId: source.id,
        actualHandoffSourceId: handoff?.id || null, requests: result.requests.length,
        chain: result.chain, retryUsed: !!retry, explicitUiRetryCount: retry ? 1 : 0,
        providerAttempts: inspect().live_budget.attempts.length, priorCampaignAttempts: 23,
        combinedProviderAttempts: 23 + inspect().live_budget.attempts.length, mismatches });
      assert.deepEqual(mismatches, [], 'targeted follow-on evidence did not satisfy acceptance');
      return;
    }

    const initialPresence = inspect('presence');
    check(initialPresence.owners['owner-maya'].online && initialPresence.owners['owner-jordan'].online, 'online Profile precondition missing');
    t.record('online_profile_presence', initialPresence);
    const alias = await send(a, '@Beacon Duet，请回答7加5等于多少？请只回复“别名答案：12”，不要再提问，其他成员无需回复。');
    const aliasResult = await settled('first_message_custom_duet_alias_only', alias);
    const aliasRequests = aliasResult.requests.filter(row => row.source_message_id === alias.id);
    check(aliasRequests.length === 1 && aliasRequests[0]?.target_twin_id === 'twin-maya', 'first custom Duet alias-only target did not produce exactly BBB Duet request');
    check(aliasResult.requests.length === 1, 'terminal alias answer triggered extra automation');
    oneAnswer(aliasResult, 'twin-maya', '别名答案', '12');
    const single = await send(a, '@BBB（Beacon Duet），请只回答这个问题：7加5等于多少？只回复“单目标答案：12”，不要再提问，其他成员无需回复。');
    const singleResult = await settled('single_owner_name', single);
    const singleRequests = singleResult.requests.filter(row => row.source_message_id === single.id);
    check(singleRequests.length === 1 && singleRequests[0]?.target_twin_id === 'twin-maya', 'single target did not produce exactly BBB Duet request');
    check(singleResult.receipts.every(row => row.message_id !== single.id || row.owner_id !== 'owner-maya'), 'single target was accidentally read by recipient');
    check(singleRequests.every(row => new Date(row.accepted_at) - new Date(single.created_at) >= 10000), 'online unread reply accepted before ten real seconds');
    check(singleResult.requests.length === 1, 'terminal single-target answer triggered more automation');
    oneAnswer(singleResult, 'twin-maya', '单目标答案', '12');

    const pair = await createGroup(a, 'Own alias pair ' + t.unique('pair'), ['BBB']);
    await profile(a);
    const pairPresence = inspect('presence');
    check(pairPresence.owners['owner-maya'].online, 'ordinary pair recipient was not online in Profile');
    const ordinaryPair = await send(a, '请问8加9等于多少？请只回答“双人答案：17”，不要继续提问。', pair);
    const ordinaryPairResult = await settled('pair_ordinary_question_without_mention', ordinaryPair, pair);
    const ordinaryPairRequests = ordinaryPairResult.requests.filter(row => row.source_message_id === ordinaryPair.id);
    check(ordinaryPairRequests.length === 1 && ordinaryPairRequests[0]?.target_twin_id === 'twin-maya',
      'ordinary two-owner question without mention did not request exactly the peer Duet');
    check(ordinaryPairRequests.every(row => new Date(row.accepted_at) - new Date(ordinaryPair.created_at) >= 10000),
      'ordinary online pair reply accepted before ten real seconds');
    check(ordinaryPairResult.requests.length === 1, 'ordinary pair terminal answer triggered more automation');
    oneAnswer(ordinaryPairResult, 'twin-maya', '双人答案', '17');
    const ownAlias = await send(a, '@Atlas Duet，请你算一下6加7等于多少。只请我的Atlas Duet回答，BBB和Beacon Duet无需回复。', pair);
    const ownAliasResult = await settled('pair_source_owner_custom_duet_only', ownAlias, pair);
    check(ownAliasResult.requests.length === 0, 'pair own-Duet-only address incorrectly triggered another owner Duet');
    check(ownAliasResult.candidates.length === 0 && ownAliasResult.reservations.length === 0 &&
      ownAliasResult.chain.spent === 0, 'pair own-Duet-only address created automatic work or spent allowance');
    check(ownAliasResult.messages.length === 1, 'pair own-Duet-only address published an unsolicited Duet message');

    const multiple = await send(b, '@AAA 和 @owner-jordan：请分别回答各自的新问题。AAA（Atlas Duet）只回复“多目标AAA：3加4等于7”；CCC（Cedar Duet）只回复“多目标CCC：8减3等于5”。请各自直接给答案，不要互相提问；BBB无需回复。');
    const multiResult = await settled('multiple_owner_and_id', multiple);
    const multiTargets = multiResult.requests.filter(row => row.source_message_id === multiple.id).map(row => row.target_twin_id).sort();
    check(JSON.stringify(multiTargets) === JSON.stringify(['twin-alex', 'twin-jordan']), 'multi target did not request exactly AAA and CCC Duets');
    check(multiResult.requests.length === 2, 'terminal multi-target answers triggered more automation');
    oneAnswer(multiResult, 'twin-alex', '多目标AAA', '7');
    oneAnswer(multiResult, 'twin-jordan', '多目标CCC', '5');

    const follow = await send(a, followOnPrompt);
    const followResult = await settled('duet_to_duet_follow_on', follow);
    const firstRequests = followResult.requests.filter(row => row.source_message_id === follow.id);
    check(firstRequests.length === 1 && firstRequests[0]?.target_twin_id === 'twin-maya', 'follow-on initial routing did not select only BBB');
    const actualHandoff = followResult.messages.find(message => message.author_id === 'twin-maya' &&
      followResult.requests.some(request => request.source_message_id === message.id && request.target_twin_id === 'twin-jordan'));
    check(!!actualHandoff, 'no genuine BBB Duet message triggered CCC Duet');
    check(followResult.bodies.some(message => message.author_id === 'twin-jordan' && /54/.test(message.body)), 'CCC Duet did not publish the requested arithmetic answer');
    check(followResult.requests.length === 2, 'Duet follow-on did not stop after its two intended tasks');
    oneAnswer(followResult, 'twin-jordan', '接力答案', '54');
    t.record('follow_on_outcome', { genuineDuetSourceMessageId: actualHandoff?.id || null,
      chainCapNaturallyReached: followResult.chain.spent === 6,
      pausedAtCap: followResult.routes.filter(row => row.status === 'paused') });

    // Genuine per-message read before the fixed ten-second gate, then Profile.
    await enter(c, chat);
    await enter(a, chat);
    const readBody = '@CCC：这次请你回答11加2等于多少？只回复“已读问题答案：13”，不要继续提问。';
    const readRow = await sendMessage(a.page, readBody);
    const readId = await readRow.getAttribute('data-message-id');
    const readSource = (await api(a.page, '/chats/' + chat.id + '?limit=100')).messages.find(row => row.id === readId);
    await c.page.bringToFront();
    await c.page.locator(`article[data-message-id="${readId}"]`).waitFor();
    await c.page.locator(`article[data-message-id="${readId}"]`).scrollIntoViewIfNeeded();
    await c.page.locator('.composer textarea').click();
    await poll(async () => (await api(a.page, '/chats/' + chat.id + '?limit=100')).messages.find(row => row.id === readId).read_by.includes('owner-jordan'), 'CCC real browser read receipt', { timeout: 8500 });
    await c.page.screenshot({ path: path.join(t.artifactDir, 'group-CCC-read-real-message.png'), fullPage: true });
    await profile(c); await profile(a);
    await sleep(Math.max(0, new Date(readSource.created_at).getTime() + 11500 - Date.now()));
    const readResult = await settled('read_then_profile', readSource);
    const receipt = readResult.receipts.find(row => row.message_id === readId && row.owner_id === 'owner-jordan');
    check(!!receipt && new Date(receipt.seen_at) - new Date(readSource.created_at) < 10000, 'CCC receipt not persisted within actual gate');
    check(observed.reads.some(row => row.code === 'DEV-CCC' && row.message_ids.includes(readId)), 'CCC read was not observed as a normal browser request');
    check(readResult.routes.find(row => row.message_id === readId)?.needs_response === true, 'read test router did not recognize a new question');
    check(readResult.candidates.some(row => row.message_id === readId &&
      row.target_twin_id === 'twin-jordan' && row.state === 'suppressed' &&
      row.evidence.suppression === 'owner_read' && row.accepted_request_id === null),
    'CCC read candidate was not durably suppressed with owner_read');
    check(readResult.requests.length === 0, 'read question still triggered a Duet');

    const beforeClose = inspect('presence');
    check(beforeClose.owners['owner-jordan'].online, 'CCC was not online before real context close');
    const closedAt = new Date().toISOString();
    await c.context.close();
    const offline = await poll(() => {
      const presence = inspect('presence');
      observed.presence.push(presence);
      return !presence.owners['owner-jordan'].online && presence;
    }, 'real CCC presence TTL expires after browser close', { timeout: 65000, interval: 1500 });
    t.record('real_presence_expiry', { beforeClose, closedAt, offline, elapsedMs: new Date(offline.observed_at) - new Date(closedAt) });
    const offlineSource = await send(a, '@owner-jordan（CCC的Cedar Duet）：请只回答20除以4等于多少？只回复“离线答案：5”，不要再提问，其他成员无需回复。');
    const offlineResult = await settled('offline_owner_id', offlineSource);
    const offlineRequests = offlineResult.requests.filter(row => row.source_message_id === offlineSource.id);
    check(offlineRequests.length === 1 && offlineRequests[0]?.target_twin_id === 'twin-jordan', 'offline explicit owner ID did not request exactly CCC Duet');
    check(offlineRequests.length === 1 &&
      new Date(offlineRequests[0].accepted_at) - new Date(offlineSource.created_at) >= 0 &&
      new Date(offlineRequests[0].accepted_at) - new Date(offlineSource.created_at) < 10000,
    'bounded offline check did not accept before the online ten-second grace window');
    // Candidate creation can retain online=null before the worker refreshes its
    // presence cache. The real Redis observation above proves offline state;
    // the accepted candidate and request prove the resulting application action.
    check(offlineResult.candidates.some(row => row.message_id === offlineSource.id && row.state === 'accepted'), 'offline target candidate was not accepted');
    check(offlineResult.requests.length === 1, 'offline terminal answer triggered more automation');
    oneAnswer(offlineResult, 'twin-jordan', '离线答案', '5');
    await enter(a, chat);
    await a.page.screenshot({ path: path.join(t.artifactDir, 'group-routing-final-real-browser.png'), fullPage: true });
    await fs.writeFile(path.join(t.artifactDir, 'group-routing-ui-network.json'), JSON.stringify(observed, null, 2));
    const finalSnapshot = inspect();
    const finalAudit = phases.map(phase => {
      const final = chainEvidence(finalSnapshot, phase.source.id);
      check(JSON.stringify(final.requests.map(row => row.id).sort()) ===
        JSON.stringify(phase.requests.map(row => row.id).sort()), phase.label + ': later unexpected automatic request');
      check(!final.routes.some(row => ['pending', 'failed'].includes(row.status)) &&
        !final.requests.some(row => ['queued', 'running', 'failed'].includes(row.status)) &&
        !final.candidates.some(row => row.state === 'waiting'), phase.label + ': final audit found unsettled work');
      return { label: phase.label, requestIds: final.requests.map(row => row.id), chain: final.chain };
    });
    t.record('final_phase_audit', finalAudit);
    assert.deepEqual(await sourceManifest(), sourceHashes, 'acceptance source files changed during the frozen campaign');
    t.record('summary', { phases: phases.map(row => ({ label: row.label, sourceId: row.source.id, requests: row.requests.length, spent: row.chain.spent })),
      mismatches, providerAttempts: inspect().live_budget.attempts.length, businessInterception: false,
      syntheticRouting: false, allWritesThroughNormalBrowserUI: true });
    assert.deepEqual(mismatches, [], 'actual behavior mismatches; retained evidence is authoritative');
  },
}];
module.exports = { cases };
