/** Real provider and three real browser sessions in the disposable fixture. */
const { assert, api, privateChat, sendMessage, poll, sleep } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

function inspect() {
  const value = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [
    path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'),
    'inspect', '--state', process.env.TWINTEAMS_E2E_STATE,
  ], { encoding: 'utf8' }));
  assert.equal(value.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  return value;
}
async function send(page, body) {
  return (await sendMessage(page, body)).getAttribute('data-message-id');
}
async function finish(page, chat, source) {
  return poll(async () => {
    const evidence = inspect(), request = evidence.requests.find(row => row.source_message_id === source);
    if (request?.status === 'failed') throw Error(`Model request failed: ${request.id}`);
    return request?.status === 'completed' ? { evidence, request, snapshot: await api(page, `/chats/${chat.id}?limit=100`) } : false;
  }, 'model request completion', { timeout: 180000, interval: 1500 });
}
function replies(result) {
  return result.snapshot.messages.filter(row => row.request_id === result.request.id && row.kind === 'twin').map(row => row.body)
    .concat((result.snapshot.questions || []).filter(row => row.origin_request_id === result.request.id).map(row => row.question));
}
function groupChats(bootstrap) { return bootstrap.chats.filter(row => row.kind === 'group'); }
async function submit(page, chat, text) {
  const source = await send(page, text), result = await finish(page, chat, source);
  return { source, ...result };
}
async function approval(page, chat, requestId) {
  return poll(async () => {
    const snapshot = await api(page, `/chats/${chat.id}?limit=100`);
    return (snapshot.approvals || []).find(row => row.status === 'pending' && row.sdk_reference?.request_id === requestId)
      || (snapshot.approvals || []).find(row => row.status === 'pending');
  }, 'pending exact group approval', { timeout: 45000, interval: 1000 });
}
async function decide(page, row, decision) {
  const tab = page.locator('.approval-tabs button').filter({ hasText: row.title });
  if (await tab.count()) await tab.first().click();
  const card = page.locator('.approval-card');
  await card.getByRole('button', { name: decision === 'approved' ? 'Approve once' : 'Reject once', exact: true }).click();
}
const cases = [{
  name: 'real model / grounded group proposal, consent, three human members and direct creation',
  timeout: 850000,
  run: async t => {
    const sessions = {};
    for (const [name, code] of [['alex', 'DEV-AAA'], ['maya', 'DEV-BBB'], ['jordan', 'DEV-CCC']]) {
      const { page } = await t.session(code, name === 'maya' ? { baseURL: t.secondaryURL } : {});
      sessions[name] = { page, private: await privateChat(page) };
    }
    const { page, private: chat } = sessions.alex;
    const missingPrompt = 'I am starting a Borealis procurement review. What is a sensible next step?';
    const missing = await submit(page, chat, missingPrompt);
    t.record('missing_context', { prompt: missingPrompt, source: missing.source, replies: replies(missing), calls: missing.evidence.tools.filter(row => row.request_id === missing.request.id), groups: groupChats(await api(page, '/bootstrap')) });
    assert.equal(groupChats(await api(page, '/bootstrap')).length, 0, 'no arbitrary group without project evidence');
    assert.ok(!missing.evidence.tools.some(row => row.request_id === missing.request.id && row.success &&
      (row.name === 'create_chat' || row.name === 'create_approval' && row.arguments.action?.tool === 'create_chat')),
      'no arbitrary group creation or proposal without context');

    const memoryPrompt = 'Please remember this for future Atlas launch work: BBB owns Atlas settlement implementation, CCC owns Atlas release QA, and I coordinate the Atlas launch. BBB and CCC are the people responsible for these specific parts of Atlas.';
    const memory = await submit(page, chat, memoryPrompt);
    const written = memory.evidence.tools.filter(row => row.request_id === memory.request.id && row.name === 'memory_write' && row.success);
    t.record('memory_setup', { prompt: memoryPrompt, source: memory.source, replies: replies(memory), calls: memory.evidence.tools.filter(row => row.request_id === memory.request.id) });
    assert.ok(written.length, 'real Duet persists owner-provided Atlas roles to durable memory');
    await page.reload(); await privateChat(page);

    const task = 'Atlas readiness details are scattered across separate conversations; implementation and release QA need one shared conversation to resolve blockers. What would you suggest?';
    const proposal = await submit(page, chat, task);
    const proposalCalls = proposal.evidence.tools.filter(row => row.request_id === proposal.request.id);
    t.record('proposal', { prompt: task, source: proposal.source, replies: replies(proposal), calls: proposalCalls, memoryContext: proposal.evidence.memory_context.filter(row => row.request_id === proposal.request.id), approvals: proposal.snapshot.approvals, groups: groupChats(await api(page, '/bootstrap')) });
    assert.equal(groupChats(await api(page, '/bootstrap')).length, 0, 'proposal does not create group before owner consent');
    const memoryEvidence = proposal.evidence.memory_context.filter(row => row.request_id === proposal.request.id).map(row => row.memory)
      .concat(proposalCalls.filter(row => ['memory_search', 'memory_read'].includes(row.name) && row.success).map(row => row.memory_result));
    t.record('role_provenance', { ownerSource: memory.source, writtenKeys: written.map(row => row.arguments.key), laterMemoryContext: memoryEvidence, earlierConversationRetained: true });
    const createApproval = proposalCalls.find(row => row.name === 'create_approval' && row.success && row.arguments.action?.tool === 'create_chat');
    assert.ok(createApproval, 'Duet proactively requests exact approval for a grounded group');
    assert.deepEqual([...new Set([...createApproval.arguments.action.arguments.owner_ids, 'owner-alex'])].sort(), ['owner-alex', 'owner-jordan', 'owner-maya']);
    const firstApproval = await approval(page, chat, proposal.request.id);
    await page.screenshot({ path: path.join(t.artifactDir, 'group-proposal.png'), fullPage: true });
    await decide(page, firstApproval, 'approved');
    const created = await poll(async () => groupChats(await api(page, '/bootstrap')).find(row => row.member_owner_ids.length === 3), 'approved group creation', { timeout: 180000, interval: 1500 });
    assert.deepEqual([...created.member_owner_ids].sort(), ['owner-alex', 'owner-jordan', 'owner-maya']);
    const before = inspect();
    const initial = await api(page, `/chats/${created.id}?limit=100`);
    assert.equal(initial.messages.length, 0, 'creation publishes no message or private task details');
    assert.equal(before.requests.filter(row => row.chat_id === created.id).length, 0, 'creation starts no Duet request');
    for (const [name, entry] of Object.entries(sessions)) {
      const row = entry.page.locator('.conversation-list .chat-item').filter({ has: entry.page.locator('.chat-name').filter({ hasText: created.title }) });
      await row.waitFor({ state: 'visible', timeout: 20000 });
      assert.equal(await row.locator('.chat-name').innerText(), created.title);
      await row.click();
      await poll(async () => (await row.getAttribute('aria-current')) === 'page' && (await entry.page.locator('.chat-header h2').innerText()) === created.title, 'selected group opens');
      const visible = await api(entry.page, `/chats/${created.id}?limit=100`);
      assert.deepEqual([...visible.chat.member_owner_ids].sort(), ['owner-alex', 'owner-jordan', 'owner-maya']);
      await entry.page.screenshot({ path: path.join(t.artifactDir, `group-created-${name}.png`), fullPage: true });
    }
    const humanMessages = { alex: 'I will coordinate Atlas readiness here.', maya: 'I can cover the settlement implementation.', jordan: 'I will bring the release QA checks.' };
    for (const [name, body] of Object.entries(humanMessages)) await send(sessions[name].page, body);
    for (const [name, entry] of Object.entries(sessions)) {
      for (const body of Object.values(humanMessages)) await entry.page.locator('article').filter({ hasText: body }).waitFor({ timeout: 20000 });
      await entry.page.screenshot({ path: path.join(t.artifactDir, `group-messages-${name}.png`), fullPage: true });
    }
    await sleep(3000);
    const after = inspect(), shared = await api(page, `/chats/${created.id}?limit=100`);
    assert.equal(shared.messages.filter(row => row.kind === 'twin').length, 0, 'human conversation does not automatically invoke Duet');
    assert.equal(after.requests.filter(row => row.chat_id === created.id).length, 0, 'human messages do not enqueue Duet requests');
    assert.ok(!after.tools.some(row => row.name === 'request_duet_conversation' && row.success), 'no Duet collaboration is created');
    assert.equal(shared.messages.length, 3, 'only three intentional human messages are published');
    t.record('approved_group', { approval: firstApproval.id, chat: created, messages: shared.messages.map(row => ({ id: row.id, author_id: row.author_id, kind: row.kind, body: row.body })), requests: after.requests.filter(row => row.chat_id === created.id), calls: after.tools.filter(row => ['create_chat', 'execute_approved_action'].includes(row.name)) });

    await privateChat(page);
    await poll(async () => (await page.locator('.chat-header h2').innerText()) === chat.title, 'private Duet opens');
    const directPrompt = 'Please create a new human group called Atlas release handoff with BBB and CCC.';
    const direct = await submit(page, chat, directPrompt);
    const directChat = await poll(async () => groupChats(await api(page, '/bootstrap')).find(row => row.title === 'Atlas release handoff'), 'explicitly requested group', { timeout: 120000, interval: 1500 });
    assert.deepEqual([...directChat.member_owner_ids].sort(), ['owner-alex', 'owner-jordan', 'owner-maya']);
    assert.equal((await api(page, `/chats/${directChat.id}?limit=100`)).messages.length, 0);
    assert.ok(direct.evidence.tools.some(row => row.request_id === direct.request.id && row.name === 'create_chat' && row.success));
    assert.ok(!direct.evidence.tools.some(row => row.request_id === direct.request.id && row.success &&
      (row.name === 'ask_question' || row.name === 'create_approval' && row.arguments.action?.tool === 'create_chat')),
      'explicit request creates without another consent prompt');
    t.record('direct_creation', { prompt: directPrompt, source: direct.source, chat: directChat, calls: direct.evidence.tools.filter(row => row.request_id === direct.request.id), replies: replies(direct) });
  },
}];
module.exports = { cases };
