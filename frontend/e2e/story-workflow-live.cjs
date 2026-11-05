/** Isolated real-model BA -> Scrum Master -> developer coordination. */
const { assert, api, answerQuestionInChat, privateChat, sendMessage, poll, sleep } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
function intentionallyStoppedChildren(evidence) {
  return new Set(evidence.runs.filter(run => run.source === 'subagent' && run.status === 'stopped'
    && (evidence.tools || []).some(tool => tool.name === 'stop_agent' && tool.success && tool.arguments?.task_id === run.session_id
      && evidence.requests.some(request => request.id === tool.request_id && request.status === 'completed'))
    && evidence.commands.some(command => command.kind === 'child_result' && command.state === 'completed'
      && command.command_id?.startsWith(`subagent-result:${run.session_id}:${run.run_id}:`)))
    .map(run => `${run.session_id}/${run.run_id}`));
}

function inspect() {
  const value = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [
    path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'),
    'inspect', '--state', process.env.TWINTEAMS_E2E_STATE,
  ], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
  assert.equal(value.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  return value;
}
async function snapshot(session) { return api(session.page, `/chats/${session.chat.id}?limit=100`); }
async function send(session, body) {
  return (await sendMessage(session.page, body)).getAttribute('data-message-id');
}
function initialRequest(evidence, chatId, source) {
  return evidence.requests.find(row => row.source_message_id === source && row.source === 'private' && row.chat_id === chatId);
}
async function finish(session, source, approvalSessions = []) {
  return poll(async () => {
    for (const entry of approvalSessions) await approvePending(entry);
    const evidence = inspect(), request = initialRequest(evidence, session.chat.id, source);
    if (request?.status === 'failed') throw Error(`Request ${request.id} failed`);
    return request?.status === 'completed' ? { evidence, request, snapshot: await snapshot(session) } : false;
  }, 'model request completion', { timeout: 150000, interval: 1500 });
}
async function say(session, body, approvalSessions = []) { const source = await send(session, body); return { source, ...await finish(session, source, approvalSessions) }; }
async function answer(session, text, approvalSessions = [], questionId = null) {
  const current = await snapshot(session);
  const question = (current.questions || []).find(row => row.status === 'pending' && (!questionId || row.id === questionId));
  if (questionId && !question) throw Error('The intended Duet question is no longer pending');
  if (!question) return say(session, text, approvalSessions);
  await answerQuestionInChat(session.page, question, text);
  return poll(async () => {
    for (const entry of approvalSessions) await approvePending(entry);
    const evidence = inspect(), row = evidence.questions.find(item => item.id === question.id);
    const request = evidence.requests.find(item => item.id === row?.continuation_request_id);
    if (request?.status === 'failed') throw Error(`Question continuation ${request.id} failed`);
    return request?.status === 'completed' ? { question: row, evidence, request } : false;
  }, 'question answer completion', { timeout: 150000, interval: 1500 });
}
async function approvePending(session) {
  const current = await snapshot(session);
  const pending = (current.approvals || []).filter(row => row.status === 'pending');
  for (const row of pending) {
    const peers = row.peer_owner_ids || [];
    if (row.type === 'duet_conversation') {
      assert.ok(peers.length && peers.every(id => session.allowedPeers?.includes(id)), 'only explicitly authorized Duet peers');
      const conversation = inspect().workflow.chats.find(chat => chat.id === row.chat_id && chat.kind === 'duet');
      assert.ok(conversation);
      assert.deepEqual([...peers].sort(), conversation.owner_ids.filter(id => id !== row.approver_id).sort(), 'approval names every actual participant');
    } else {
      assert.equal(row.action?.tool, 'present_files', 'no unrelated approval is accepted');
      const evidence = inspect();
      const allowedFiles = session.allowedFiles || (session.assignmentBriefs
        ? evidence.files.filter(file => file.chat_id === session.chat.id && file.text && !session.assignmentBriefs.baHashes.includes(file.sha256)) : []);
      assert.ok(allowedFiles.length, 'file sharing has been explicitly authorized');
      const paths = row.action.arguments?.paths || [];
      assert.ok(paths.length && paths.every(item => allowedFiles.some(file => item === file.path || file.path.endsWith('/' + item))), 'only the selected story files or SM-authored briefs');
      const shares = evidence.approvals.find(item => item.id === row.id)?.file_shares || [];
      assert.equal(shares.length, paths.length);
      for (const share of shares) {
        if (session.assignmentBriefs) {
          assert.ok(share.peer_owner_ids.length && share.peer_owner_ids.every(id => ['owner-jordan', 'owner-riley'].includes(id)), 'brief recipients are the assigned developers');
          const file = allowedFiles.find(item => item.path === share.path && item.sha256 === share.sha256);
          assert.ok(file, 'brief approval covers the selected SM-owned file');
        } else {
          assert.deepEqual(share.peer_owner_ids, ['owner-maya'], 'only Scrum Master receives the files');
          assert.ok(allowedFiles.some(file => file.path === share.path && file.sha256 === share.sha256), 'approval covers only the selected versions');
        }
      }
    }
    const toggle = session.page.locator('.approval-toggle');
    if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
    const tab = session.page.locator(`.approval-tabs button[title=${JSON.stringify(row.title)}]`);
    if (await tab.count()) await tab.first().click();
    const card = session.page.locator('.approval-card');
    const duet = card.getByRole('button', { name: session.scope === 'always' ? 'Always allow' : 'Allow once', exact: true });
    const file = card.getByRole('button', { name: /allow file sharing/i });
    if (await duet.count()) await duet.click();
    else if (await file.count()) await file.click();
    else throw Error(`Unexpected approval UI: ${row.title}`);
    await sleep(500);
  }
}
async function waitFor(label, sessions, predicate, timeout = 180000) {
  return poll(async () => {
    for (const session of sessions) await approvePending(session);
    const evidence = inspect();
    const identityOwner = sessions.find(session => session.handleIdentity);
    if (identityOwner && await identityOwner.handleIdentity(evidence)) return false;
    const coordinator = sessions.find(session => session.continueHeld);
    if (coordinator && await coordinator.continueHeld(evidence)) return false;
    return predicate(evidence);
  }, label, { timeout, interval: 2000 });
}
function storyText(files) { return files.map(file => file.text || '').join('\n'); }
const marks = [/refund/i, /fail(?:ure|ed)/i, /report/i];
const oneDay = /24[\s-]*(?:hours?|h\b)|(?:one|a)[\s-]+day/i;
function includesStories(text) { return marks.every(mark => mark.test(text)); }
function consultationSettled(evidence) {
  const ignoredRuns = intentionallyStoppedChildren(evidence);
  const commands = evidence.commands.filter(row => ['prompt', 'child_result'].includes(row.kind)
    && !(row.state === 'stopped' && ignoredRuns.has(`${row.session_id}/${row.run_id}`)));
  return evidence.requests.every(row => row.status === 'completed')
    && evidence.runs.every(row => row.status === 'completed' || (row.status === 'stopped' && ignoredRuns.has(`${row.session_id}/${row.run_id}`))) && commands.every(row => row.state === 'completed');
}
async function reviewReports(t, stage, evidence) {
  const token = require('node:crypto').randomUUID();
  const request = path.join(t.artifactDir, `${stage}-review-request.json`);
  const approval = path.join(t.artifactDir, `${stage}-review-approved.json`);
  await require('node:fs/promises').writeFile(request, JSON.stringify({ token, ...evidence }, null, 2));
  await poll(async () => {
    try {
      const review = JSON.parse(await require('node:fs/promises').readFile(approval, 'utf8'));
      return review.approved === true && review.token === token;
    } catch (error) {
      if (error.code === 'ENOENT') return false;
      throw error;
    }
  }, `independent review of ${stage} owner answers and reports`, { timeout: 300000, interval: 2000 });
  t.record(`${stage}_review`, { request, approval });
}
function latestFiles(files) { return [...new Map(files.map(file => [file.path, file])).values()]; }
function selectStories(files) {
  const named = file => `${path.basename(file.path)}\n${file.text.split('\n')[0]}`;
  const failure = files.find(file => /(?:fail|error).{0,30}(?:reason|display|message)|(?:reason|display).{0,30}(?:fail|error)|customer.{0,30}(?:fail|error)/i.test(named(file)))
    || files.find(file => /fail|reason|error/i.test(named(file)) && !/duplicate|idempoten/i.test(named(file)));
  const report = files.find(file => file !== failure && /report/i.test(named(file)));
  const refund = files.find(file => file !== failure && file !== report && /refund|duplicate/i.test(named(file)));
  return [refund, failure, report].filter(Boolean);
}
function pairChats(evidence, peer) {
  return evidence.workflow.chats.filter(row => row.kind === 'duet'
    && row.owner_ids.includes('owner-maya') && row.owner_ids.includes(peer));
}
async function continueHeldConsultation(sm, t, grants, evidence, allowedChain) {
  const chats = [...new Map([...pairChats(evidence, 'owner-jordan'), ...pairChats(evidence, 'owner-riley')].map(chat => [chat.id, chat])).values()];
  for (const chat of chats) {
    assert.ok(chat.owner_ids.every(id => ['owner-maya', 'owner-jordan', 'owner-riley'].includes(id)), 'continuation is limited to the approved consultation');
    const detail = await api(sm.page, `/duet-conversations/${chat.id}?limit=100`);
    for (const budget of detail.send_budgets.filter(row => row.pending_count && row.can_continue && row.causal_chain_id === allowedChain)) {
      const held = inspect().workflow.messages.filter(row => row.state === 'pending' && row.causal_chain_id === budget.causal_chain_id);
      if (!held.length || held.filter(row => row.chat_id === chat.id).length !== budget.pending_count) return true;
      const required = held.some(row => /(?:key|idempoten).*(?:amount|conflict)|(?:amount|conflict).*(?:key|idempoten)|(?:unknown|fail|error).*(?:message|fallback|display)|(?:message|fallback|display).*(?:unknown|fail|error)/is.test(row.body));
      if (!required) continue;
      assert.ok(held.every(row => chats.some(item => item.id === row.chat_id)), 'all held messages belong to the approved consultation');
      assert.ok(held.every(row => ['twin-maya', 'twin-jordan', 'twin-riley'].includes(row.author_id)), 'held messages belong to the authorized participants');
      assert.ok(!grants.some(row => row.chain === budget.causal_chain_id), 'a second allowance extension requires review');
      if (!(await sm.page.getByRole('tab', { name: 'Duets', exact: true }).count())) {
        await sm.page.getByRole('button', { name: 'More tools', exact: true }).click();
        await sm.page.getByRole('menuitem', { name: 'Duets', exact: true }).click();
      }
      await sm.page.getByRole('tab', { name: 'Duets', exact: true }).click();
      const back = sm.page.getByRole('button', { name: 'Back to Duet conversations', exact: true });
      if (await back.count()) await back.click();
      await sm.page.locator('.duet-conversation-card').filter({ hasText: chat.title }).click();
      const sections = sm.page.locator('.duet-send-budget').filter({ hasText: `${budget.spent} of ${budget.allowance} messages sent` });
      await sections.first().waitFor({ state: 'visible', timeout: 20000 });
      assert.equal(await sections.count(), 1, 'the visible control uniquely matches this held exchange');
      const [response] = await Promise.all([
        sm.page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/duet-conversations/${chat.id}/chains/${budget.causal_chain_id}/continue`),
        sections.getByRole('button', { name: `Continue · ${budget.continuation_allowance} more messages`, exact: true }).click(),
      ]);
      assert.ok(response.ok(), 'the owner continuation succeeds');
      const updated = await response.json();
      assert.equal(updated.causal_chain_id, budget.causal_chain_id);
      assert.equal(updated.allowance, budget.allowance + budget.continuation_allowance);
      assert.ok(updated.published_message_ids.some(id => held.some(row => row.id === id)), 'a held message was released');
      grants.push({ chat: chat.id, chain: budget.causal_chain_id, before: budget, held, after: updated });
      t.record('owner_continuation_grants', grants);
      await privateChat(sm.page);
      return true;
    }
  }
  return false;
}
function peerQuestion(evidence, owner, priorQuestions, priorMessages) {
  function linked(request) {
    const source = evidence.workflow.messages.find(item => item.id === request?.source_message_id);
    if (!source || priorMessages.has(source.id)) return false;
    return source.author_id === 'twin-maya' || (source.causal_chain_id && evidence.workflow.messages.some(item =>
      item.author_id === 'twin-maya' && item.causal_chain_id === source.causal_chain_id && !priorMessages.has(item.id)));
  }
  const card = evidence.questions.find(row => row.owner_id === owner && row.status === 'pending' && !priorQuestions.has(row.id)
    && pairChats(evidence, owner).some(chat => chat.id === row.origin_chat_id)
    && linked(evidence.requests.find(item => item.id === row.origin_request_id)));
  if (card) return { ...card, prompt_kind: 'card' };
  const chat = evidence.workflow.chats.find(row => row.kind === 'private' && row.private_owner_id === owner);
  const message = evidence.workflow.messages.find(row => {
    const request = evidence.requests.find(item => item.id === row.request_id);
    return row.chat_id === chat?.id && row.kind === 'twin' && row.state === 'published' && !priorMessages.has(row.id)
      && /[?？]|please (?:confirm|tell|share|let me know)|let me know/i.test(row.body)
      && pairChats(evidence, owner).some(item => item.id === request?.chat_id) && linked(request);
  });
  return message && { ...message, prompt_kind: 'message' };
}
async function replyToPeer(session, text, prompt) {
  return prompt.prompt_kind === 'card' ? answer(session, text, [], prompt.id) : say(session, text);
}
function sharedReadHashes(evidence, owner) {
  return new Set(evidence.tools.filter(row => row.success && row.owner_id === owner).flatMap(row => {
    if (row.name === 'read_workspace_file' && typeof row.result?.content === 'string') {
      return [require('node:crypto').createHash('sha256').update(row.result.content).digest('hex')];
    }
    if (row.name === 'read') return (row.attachment_ids_read || []).map(id => evidence.workflow.attachments.find(item => item.id === id)?.sha256).filter(Boolean);
    return [];
  }));
}
function isSettled(evidence) {
  const ignoredRuns = intentionallyStoppedChildren(evidence);
  const failedRequests = evidence.requests.filter(row => ['failed', 'cancelled'].includes(row.status));
  const failedRuns = evidence.runs.filter(row => ['failed', 'stopped'].includes(row.status) && !ignoredRuns.has(`${row.session_id}/${row.run_id}`));
  const executableCommands = evidence.commands.filter(row => ['prompt', 'child_result'].includes(row.kind)
    && !(row.state === 'stopped' && ignoredRuns.has(`${row.session_id}/${row.run_id}`)));
  const failedCommands = executableCommands.filter(row => ['failed', 'stopped', 'rejected'].includes(row.state));
  const cancelledAttempts = evidence.live_budget.attempts.filter(row => row.error_type === 'CancelledError');
  const pendingApprovals = evidence.approvals.filter(row => row.status === 'pending');
  const pendingMessages = evidence.workflow.messages.filter(row => row.state === 'pending');
  const pendingQuestions = evidence.questions.filter(row => row.status === 'pending');
  if (failedRequests.length || failedRuns.length || failedCommands.length || cancelledAttempts.length) {
    throw Error(`Unexpected failed or cancelled work: ${JSON.stringify({ failedRequests, failedRuns, failedCommands, cancelledAttempts })}`);
  }
  const active = evidence.requests.some(row => ['queued', 'running'].includes(row.status))
    || executableCommands.some(row => ['accepted', 'applied'].includes(row.state));
  if (!active && pendingApprovals.length) {
    throw Error(`Work remains blocked for an owner decision: ${JSON.stringify({ pendingApprovals, pendingMessages, pendingQuestions })}`);
  }
  return evidence.requests.every(row => row.status === 'completed')
    && evidence.runs.every(row => row.status === 'completed' || (row.status === 'stopped' && ignoredRuns.has(`${row.session_id}/${row.run_id}`)))
    && executableCommands.every(row => row.state === 'completed')
    && evidence.center_results.every(row => row.state === 'processed' && row.result_sent)
    && (evidence.center.deliveries || []).every(row => row.status === 'processed')
    && !pendingApprovals.length
    && evidence.live_budget.attempts.every(row => row.response_at);
}
async function previewFile(session, file, artifact) {
  const page = session.page;
  if (!(await page.getByRole('tab', { name: 'Workspace', exact: true }).count())) {
    await page.getByRole('button', { name: 'More tools', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Workspace', exact: true }).click();
  }
  await page.getByRole('tab', { name: 'Workspace', exact: true }).click();
  const parts = file.path.split(`/workspaces/${session.chat.id}/`)[1].split('/');
  for (let i = 0; i < parts.length; i++) {
    const relative = parts.slice(0, i + 1).join('/');
    await page.locator('.workspace-tree button').filter({ hasText: parts[i] }).first().click();
    if (i < parts.length - 1) await page.locator(`.workspace-tree button[title=${JSON.stringify(relative)}]`).waitFor();
  }
  const modal = page.locator('.modal[role="dialog"]');
  const preview = modal.locator('.text-file-preview');
  await preview.waitFor();
  await poll(async () => (await preview.textContent()) === file.text, 'browser preview contains the saved text');
  const [download] = await Promise.all([
    page.waitForEvent('download'), modal.getByRole('link', { name: 'Download', exact: true }).click(),
  ]);
  const bytes = await require('node:fs/promises').readFile(await download.path());
  assert.equal(require('node:crypto').createHash('sha256').update(bytes).digest('hex'), file.sha256, 'browser download matches saved bytes');
  await page.screenshot({ path: artifact, fullPage: true });
  await modal.getByRole('button', { name: 'Close', exact: true }).click();
}

async function afterConsultation(t, sessions, { drafted, revised, consultation, beforeConsultationQuestions, beforeConsultationMessages }) {
    const { ba, sm, dev1, dev2 } = sessions;
    const capacityState = await waitFor('developer capacity questions or completed feasibility reviews', [sm, dev1, dev2], evidence => {
      const selected = Object.fromEntries(['owner-jordan', 'owner-riley'].map(owner => [owner, peerQuestion(evidence, owner, beforeConsultationQuestions, beforeConsultationMessages)]));
      if (Object.values(selected).every(Boolean)) return { selected };
      const peerReplies = evidence.workflow.messages.filter(row => !beforeConsultationMessages.has(row.id) && row.state === 'published'
        && ['twin-jordan', 'twin-riley'].includes(row.author_id)
        && pairChats(evidence, row.author_id.replace('twin-', 'owner-')).some(chat => chat.id === row.chat_id));
      return consultationSettled(evidence)
        && ['owner-jordan', 'owner-riley'].every(id => selected[id] || peerReplies.some(row => row.author_id === id.replace('owner-', 'twin-')))
        && { needsFollowup: true, peerReplies, selected };
    });
    let firstQuestions = capacityState.selected;
    if (capacityState.needsFollowup) {
      const baseline = inspect();
      const oldQuestions = new Set(baseline.questions.map(row => row.id));
      const oldMessages = new Set(baseline.workflow.messages.map(row => row.id));
      const missing = [['owner-jordan', 'Jordan'], ['owner-riley', 'Riley']].filter(([id]) => !capacityState.selected[id]).map(([, name]) => name);
      const target = missing.length === 2 ? 'Jordan and Riley’s Duets to confirm with their owners' : `${missing[0]}’s Duet to confirm with its owner`;
      const followup = await say(sm, `The feasibility reviews are useful, but I still need each person’s capacity before deciding. Please ask ${target} whether they can take these stories this sprint, then report the answers.`, [sm, dev1, dev2]);
      t.record('capacity_followup', { source: followup.source, initialPeerReplies: capacityState.peerReplies });
      firstQuestions = await waitFor('developers ask their owners about capacity', [sm, dev1, dev2], evidence => {
        const selected = Object.fromEntries(['owner-jordan', 'owner-riley'].map(owner => [owner, capacityState.selected[owner] || peerQuestion(evidence, owner, oldQuestions, oldMessages)]));
        return Object.values(selected).every(Boolean) && selected;
      });
    }
    const beforeAnswers = inspect();
    t.record('developer_first_questions', firstQuestions);
    const priorMessages = new Set(beforeAnswers.workflow.messages.map(row => row.id));
    const beforeCapacityAnswers = new Set(inspect().workflow.messages.map(row => row.id));
    await Promise.all([dev1, dev2].map(async (entry, index) => {
      await replyToPeer(entry, index === 0
        ? 'I can take the duplicate-refund prevention story. It is feasible this sprint if retries are idempotent and tested; tell BBB, the Scrum Master, in this group.'
        : 'I can take the customer failure-reason story this sprint. Custom reports need more discovery and should be deferred; tell BBB, the Scrum Master, in this group.', firstQuestions[index === 0 ? 'owner-jordan' : 'owner-riley']);
    }));
    const answers = await waitFor('developer answers delivered to Scrum Master', [sm, dev1, dev2], evidence => {
      const jordan = pairChats(evidence, 'owner-jordan').map(chat => chat.id);
      const riley = pairChats(evidence, 'owner-riley').map(chat => chat.id);
      const published = evidence.workflow.messages.filter(row => row.state === 'published');
      const left = published.filter(row => !priorMessages.has(row.id) && jordan.includes(row.chat_id) && row.author_id === 'twin-jordan');
      const right = published.filter(row => !priorMessages.has(row.id) && riley.includes(row.chat_id) && row.author_id === 'twin-riley');
      const reports = published.filter(row => !beforeCapacityAnswers.has(row.id) && row.chat_id === sm.chat.id && row.author_id === 'twin-maya');
      return left.length && right.length && reports.length && { evidence, left, right, reports };
    });
    let settledSamples = 0;
    const settledConsultation = await waitFor('initial Scrum Master consultation to settle', [sm, dev1, dev2], evidence => {
      const settled = consultationSettled(evidence);
      settledSamples = settled ? settledSamples + 1 : 0;
      return settledSamples >= 2 && evidence;
    });
    const capacityReports = settledConsultation.workflow.messages.filter(row => !beforeCapacityAnswers.has(row.id) && row.chat_id === sm.chat.id && row.author_id === 'twin-maya');
    t.record('developer_answers', { left: answers.left, right: answers.right, reports: capacityReports });
    await reviewReports(t, 'capacity', { questions: firstQuestions, peerReplies: [...answers.left, ...answers.right], privateReports: capacityReports,
      ownerMessages: settledConsultation.workflow.messages.filter(row => !beforeCapacityAnswers.has(row.id) && ['owner-jordan', 'owner-riley'].includes(row.author_id)),
      instruction: 'Check both real owner capacity answers and their subsequent private reports before approving.' });
    await afterCapacity(t, sessions, { drafted, revised, consultation });
}

async function afterCapacity(t, sessions, { drafted, revised, consultation }) {
    const { sm, dev1, dev2 } = sessions;
    const continuationGrants = [];
    const roundTwoBaseline = inspect();
    const priorQuestions = new Set(roundTwoBaseline.questions.map(row => row.id));
    const beforeRoundTwo = new Set(roundTwoBaseline.workflow.messages.map(row => row.id));
    const technicalRequest = await say(sm, 'Before I decide, please ask CCC to confirm with Jordan what should happen if the same refund key is reused with a different amount, and ask DDD to confirm with Riley what customers should see when a payment fails and the failure reason is unknown. Please report their owners’ answers.', [sm, dev1, dev2]);
    sm.continueHeld = evidence => continueHeldConsultation(sm, t, continuationGrants, evidence, technicalRequest.request.causal_chain_id);
    const secondQuestions = await waitFor('second owner questions', [sm, dev1, dev2], evidence => {
      const selected = {
        'owner-jordan': peerQuestion(evidence, 'owner-jordan', priorQuestions, beforeRoundTwo),
        'owner-riley': peerQuestion(evidence, 'owner-riley', priorQuestions, beforeRoundTwo),
      };
      return Object.values(selected).every(Boolean) && selected;
    });
    t.record('developer_second_questions', secondQuestions);
    const beforeClarifications = new Set(inspect().workflow.messages.map(row => row.id));
    await Promise.all([
      replyToPeer(dev1, 'Reject the request as a conflict when the same key has a different amount; never issue a refund for that conflicting request. Please tell BBB, the Scrum Master, in this group.', secondQuestions['owner-jordan']),
      replyToPeer(dev2, 'Show a generic safe message for an unknown failure and include an incident reference for support; never expose the internal error. Please tell BBB, the Scrum Master, in this group.', secondQuestions['owner-riley']),
    ]);
    const clarification = await waitFor('second developer answers reach Scrum Master', [sm, dev1, dev2], evidence => {
      const reports = evidence.workflow.messages.filter(row => row.chat_id === sm.chat.id && row.author_id === 'twin-maya' && !beforeClarifications.has(row.id));
      const responses = evidence.workflow.messages.filter(row => !beforeClarifications.has(row.id) && row.state === 'published');
      const left = responses.filter(row => pairChats(evidence, 'owner-jordan').some(chat => chat.id === row.chat_id) && row.author_id === 'twin-jordan');
      const right = responses.filter(row => pairChats(evidence, 'owner-riley').some(chat => chat.id === row.chat_id) && row.author_id === 'twin-riley');
      return left.length && right.length && reports.length && { evidence, left, right, reports };
    });
    let clarificationSamples = 0;
    const settledClarification = await waitFor('technical clarification to settle', [sm, dev1, dev2], evidence => {
      clarificationSamples = consultationSettled(evidence) ? clarificationSamples + 1 : 0;
      return clarificationSamples >= 2 && evidence;
    });
    const counts = new Map();
    const duetIds = new Set(clarification.evidence.workflow.chats.filter(row => row.kind === 'duet').map(row => row.id));
    for (const row of clarification.evidence.workflow.messages.filter(row => duetIds.has(row.chat_id) && row.kind === 'twin' && row.state === 'published' && row.causal_chain_id)) counts.set(row.causal_chain_id, (counts.get(row.causal_chain_id) || 0) + 1);
    assert.ok([...counts].every(([chain, count]) => count <= (continuationGrants.find(row => row.chain === chain)?.after.allowance || 6)), 'published messages respect each global chain allowance and explicit owner grants');
    const clarificationReports = settledClarification.workflow.messages.filter(row => row.chat_id === sm.chat.id && row.author_id === 'twin-maya' && !beforeClarifications.has(row.id));
    t.record('developer_clarifications', { left: clarification.left, right: clarification.right, reports: clarificationReports, continuationGrants });
    delete sm.continueHeld;
    await reviewReports(t, 'decision', {
      questions: secondQuestions,
      peerReplies: [...clarification.left, ...clarification.right],
      privateReports: clarificationReports,
      ownerMessages: settledClarification.workflow.messages.filter(row => !beforeClarifications.has(row.id) && ['owner-jordan', 'owner-riley'].includes(row.author_id)),
      instruction: 'Review actual capacity and technical owner answers and their private reports before approving the decision.',
    });
    const beforeDecision = new Set(clarification.evidence.workflow.messages.map(row => row.id));
    const beforeAssignments = new Set(inspect().workflow.messages.map(row => row.id));
    sm.assignmentBriefs = { baHashes: revised.files.map(file => file.sha256) };
    const decision = await say(sm, 'Accept duplicate-refund prevention for CCC and failure-reason display for DDD. Defer custom reports. The deadline for both accepted stories is November 20, 2026. Publish the assignments through Message Center to the respective developers, including the relevant story details. Ask them to prepare development documents, not code. My planning and assignment responsibility ends after the assignments are delivered; implementation remains open.', [sm, dev1, dev2]);
    const assignments = await waitFor('both developer assignments delivered', [sm, dev1, dev2], evidence => {
      const messages = evidence.center.messages.filter(row => row.recipients.some(id => ['owner-jordan', 'owner-riley'].includes(id)));
      const recipients = new Set(messages.flatMap(row => row.recipients));
      const deliveries = evidence.center.deliveries.filter(row => messages.some(message => message.id === row.message_id));
      const ownerReports = Object.fromEntries([[dev1, 'owner-jordan'], [dev2, 'owner-riley']].map(([entry, owner]) => [owner,
        evidence.workflow.messages.filter(row => row.chat_id === entry.chat.id && row.kind === 'twin' && !beforeAssignments.has(row.id))]));
      const ownerVisible = Object.values(ownerReports).every(rows => rows.length);
      return recipients.has('owner-jordan') && recipients.has('owner-riley') && deliveries.length >= 2 && deliveries.every(row => row.status === 'processed') && ownerVisible && { evidence, messages, deliveries, ownerReports };
    });
    assert.ok(assignments.messages.every(row => row.recipients.every(id => ['owner-jordan', 'owner-riley'].includes(id))), 'assignment recipients are limited to the authorized developers');
    for (const owner of ['owner-jordan', 'owner-riley']) {
      const scoped = assignments.messages.filter(row => row.recipients.includes(owner) && row.recipients.every(id => ['owner-jordan', 'owner-riley'].includes(id)));
      assert.ok(scoped.length, 'each developer receives a scoped assignment');
      for (const row of scoped) for (const id of row.metadata?.twinteams_attachment_ids || []) {
        const attachment = assignments.evidence.workflow.attachments.find(item => item.id === id);
        assert.ok(attachment && attachment.owner_id === 'owner-maya' && !revised.files.some(file => file.sha256 === attachment.sha256), 'only SM-authored briefs can accompany assignments');
        assert.ok(assignments.evidence.approvals.some(approval => approval.owner_id === 'owner-maya' && approval.status === 'approved'
          && approval.file_shares?.some(share => share.sha256 === attachment.sha256 && share.peer_owner_ids.includes(owner))), 'brief was approved for this developer');
      }
    }
    const decisionReports = assignments.evidence.workflow.messages.filter(row => !beforeDecision.has(row.id) && row.chat_id === sm.chat.id && row.author_id === 'twin-maya');
    t.record('assignments', { decision: decision.source || decision.question?.answer_message_id, decisionReports, messages: assignments.messages, deliveries: assignments.deliveries, ownerReports: assignments.ownerReports });
    const beforePlans = inspect().files;
    t.record('before_developer_plans', { files: beforePlans.map(({ path, sha256 }) => ({ path, sha256 })) });
    for (const [entry, name] of [[dev1, 'dev1'], [dev2, 'dev2']]) {
      const beforePlanning = inspect();
      const priorPlanningMessages = new Set(beforePlanning.workflow.messages.map(row => row.id));
      const priorPlanningQuestions = new Set(beforePlanning.questions.map(row => row.id));
      await say(entry, 'I accept responsibility for implementing the assignment I received through Message Center. First, prepare a development plan in a Markdown document in my workspace and discuss the plan with me; do not write code yet. My implementation responsibility remains unfinished after the plan is saved.');
      const discussion = await waitFor('developer discussion', [entry], evidence => {
        const replies = evidence.workflow.messages.filter(row => row.chat_id === entry.chat.id && row.kind === 'twin' && !priorPlanningMessages.has(row.id));
        const questions = evidence.questions.filter(row => row.chat_id === entry.chat.id && row.status === 'pending' && !priorPlanningQuestions.has(row.id));
        return (replies.length || questions.length) && { replies, questions };
      });
      const questions = discussion.questions;
      t.record(`${name}_discussion`, discussion);
      const clarification = name === 'dev1'
        ? 'Use the existing durable database for the key; an unknown outcome stays pending until reconciled.'
        : 'Use a generic safe message and support reference for an unknown failure; keep internal details in secure logs.';
      const followup = `${clarification} Leave other unresolved implementation choices open rather than assuming them. Use a concise design, implementation steps, edge cases, and test plan. Save the document now; stop before coding.`;
      if (questions.length) await answer(entry, followup, [], questions[0].id);
      else await say(entry, followup);
      await entry.page.screenshot({ path: path.join(t.artifactDir, `${name}-plan.png`), fullPage: true });
    }
    await afterDeveloperPlans(t, sessions, { drafted, consultation, beforePlans });
}

async function afterDeveloperPlans(t, sessions, { drafted, consultation, beforePlans }) {
    const { dev1, dev2 } = sessions;
    const final = await waitFor('developer documents', [dev1, dev2], evidence => {
      const files = latestFiles(evidence.files.filter(row => [dev1.chat.id, dev2.chat.id].includes(row.chat_id) && row.text && /\.md$/i.test(row.path)));
      const tracking = evidence.workflow.tracking;
      const plans = [dev1, dev2].map(entry => {
        const candidates = files.filter(row => row.chat_id === entry.chat.id && !beforePlans.some(old => old.path === row.path && old.sha256 === row.sha256));
        const presented = evidence.workflow.attachments.filter(row => row.chat_id === entry.chat.id
          && evidence.requests.some(request => request.id === row.request_id && request.status === 'completed'));
        return candidates.find(row => presented.some(attachment => attachment.sha256 === row.sha256)
          && /plan|design|implementation|solution/i.test(`${path.basename(row.path)} ${row.text.split('\n')[0]}`));
      });
      return plans.every(Boolean)
        && tracking.some(row => row.id === drafted.tracking[0].id && ['active', 'completed'].includes(row.status))
        && tracking.some(row => consultation.tracking.some(old => old.id === row.id) && ['active', 'completed'].includes(row.status))
        && [dev1.chat.id, dev2.chat.id].every(id => tracking.some(row => row.chat_id === id && row.status === 'active'))
        && isSettled(evidence) && { evidence, plans, files };
    });
    assert.ok(final.evidence.workflow.tracking.some(row => row.id === drafted.tracking[0].id), 'BA goal is retained for status review');
    for (const id of [dev1.chat.id, dev2.chat.id]) assert.ok(final.evidence.workflow.tracking.some(row => row.chat_id === id && row.status === 'active'), 'developer has an ongoing tracked topic for review');
    assert.ok(!final.evidence.workflow.workspace_files.some(file => [dev1.chat.id, dev2.chat.id].includes(file.chat_id) && /\.(?:py|js|jsx|ts|tsx|java|go|rs|cs|sh|sql|c|cpp|h|hpp|swift|kt|rb|php|scala)$/i.test(file.path)), 'developers stopped before creating code');
    for (const [index, entry] of [dev1, dev2].entries()) await previewFile(entry, final.plans[index], path.join(t.artifactDir, `dev${index + 1}-file-preview.png`));
    t.record('developer_documents', { files: final.files, previewedPlans: final.plans, tracking: final.evidence.workflow.tracking, trackingUpdates: final.evidence.workflow.tracking_updates });
    let quietSamples = 0;
    const settled = await poll(() => {
      const evidence = inspect();
      quietSamples = isSettled(evidence) ? quietSamples + 1 : 0;
      return quietSamples >= 3 && evidence;
    }, 'all requests, child results, Center acknowledgments and provider calls settle', { timeout: 180000, interval: 2000 });
    t.record('settled', { requests: settled.requests, runs: settled.runs, commands: settled.commands,
      centerResults: settled.center_results, questions: settled.questions, approvals: settled.approvals,
      pendingMessages: settled.workflow.messages.filter(row => row.state === 'pending'), attempts: settled.live_budget.attempts });
}

const cases = [{
  name: process.env.TWINTEAMS_E2E_DRAFT_DIAGNOSTIC === '1'
    ? 'diagnostic only / BA story drafting and tracking'
    : 'real model / BA stories through Center to Scrum Master and developers',
  timeout: 3550000,
  run: async t => {
    const sessions = {};
    for (const [name, code] of [['ba', 'DEV-AAA'], ['sm', 'DEV-BBB'], ['dev1', 'DEV-CCC'], ['dev2', 'DEV-DDD']]) {
      const { page } = await t.session(code);
      sessions[name] = { page, chat: await privateChat(page), scope: name === 'dev2' ? 'always' : 'once' };
    }
    const { ba, sm, dev1, dev2 } = sessions;
    const first = await say(ba, 'I am the business analyst for our ongoing payments improvement work. My responsibility is to refine and hand off Jira stories with acceptance criteria; others will implement them. My story work is not finished. First, customers sometimes submit the same refund twice. We must prevent duplicate refunds for the same transaction while allowing a legitimate retry after a failed attempt. Ask me if anything is unclear. Separately, what does HTTP 409 mean? That is just a one-off question. Do not send anything to anyone yet.');
    const second = await answer(ba, 'Use the transaction and refund request identity to distinguish duplicates; retries after failure must be safe. Also add two separate stories: show customers a useful failure reason without exposing internal details, and let managers create custom reports filtered by date and status. Save three separate Jira-ready Markdown story documents in my workspace, each with acceptance criteria. This is ongoing work. Do not publish them yet.');
    const drafted = await waitFor('three actual story files and ongoing tracking', [ba], evidence => {
      const files = selectStories(latestFiles(evidence.files.filter(row => row.chat_id === ba.chat.id && row.text)));
      const tracking = evidence.workflow.tracking.filter(row => row.chat_id === ba.chat.id && row.status === 'active');
      return marks.every(mark => files.some(file => mark.test(file.text))) && new Set(files.map(file => file.path)).size >= 3 && tracking.length && { evidence, files, tracking };
    }, 120000);
    assert.equal(drafted.evidence.center.messages.length, 0, 'no publication before owner authorization');
    assert.ok(!drafted.tracking.some(row => /HTTP 409/i.test(row.title)), 'one-off answer is not a separate ongoing goal');
    t.record('drafted', { initial: first.source, followup: second.source || second.question?.answer_message_id, files: drafted.files, tracking: drafted.tracking });
    await ba.page.screenshot({ path: path.join(t.artifactDir, 'ba-stories.png'), fullPage: true });
    if (process.env.TWINTEAMS_E2E_DRAFT_DIAGNOSTIC === '1') {
      t.record('diagnostic_only', 'Stopped after drafting; this is not an end-to-end acceptance.');
      return;
    }
    await say(ba, 'Please revise the existing stories: keep a refund idempotency key for 24 hours, and use UTC for the custom report date filter. Preserve the other requirements. Update the same documents; this work is still ongoing.');
    const revised = await waitFor('story revisions and continued tracking', [ba], evidence => {
      const files = latestFiles(evidence.files.filter(row => row.chat_id === ba.chat.id && row.text && drafted.files.some(old => old.path === row.path)));
      const changed = drafted.files.filter(old => files.some(file => file.path === old.path && file.sha256 !== old.sha256));
      const refund = files.find(file => file.path === drafted.files[0].path);
      const report = files.find(file => file.path === drafted.files[2].path);
      const tracking = evidence.workflow.tracking.filter(row => row.chat_id === ba.chat.id && row.status === 'active');
      return files.length === 3 && changed.some(file => file.path === refund?.path) && changed.some(file => file.path === report?.path)
        && oneDay.test(refund?.text || '') && /UTC/i.test(report?.text || '')
        && tracking.some(row => row.id === drafted.tracking[0].id) && { files, tracking, evidence };
    });
    assert.equal(revised.tracking.length, drafted.tracking.length, 'revision does not create duplicate goals');
    assert.ok(includesStories(storyText(revised.files)));
    t.record('revisions', { before: drafted.files, after: revised.files, tracking: revised.tracking });
    await previewFile(ba, revised.files[0], path.join(t.artifactDir, 'ba-file-preview.png'));
    ba.allowedPeers = ['owner-maya']; sm.allowedPeers = ['owner-alex'];
    ba.allowedFiles = revised.files;
    await say(ba, 'Please open an approved Duet conversation with BBB, our Scrum Master, and share the three revised story documents with BBB. I authorize sharing these specific files with BBB so BBB can read and plan them. Then publish three separate messages through Message Center, one per story, with its shared document attached. My analysis and handoff are finished only when BBB receives all three; the development work itself remains open.', [ba, sm]);
    const connection = await waitFor('BA and Scrum Master approve their Duet conversation', [ba, sm], evidence => {
      const chats = evidence.workflow.chats.filter(row => row.kind === 'duet' && row.duet_status === 'active'
        && row.owner_ids.length === 2 && row.owner_ids.includes('owner-alex') && row.owner_ids.includes('owner-maya'));
      const openings = evidence.workflow.messages.filter(row => chats.some(chat => chat.id === row.chat_id && chat.opening_message === row.body) && row.kind === 'twin' && row.author_id === 'twin-alex' && row.state === 'published');
      return chats.length === 1 && openings.length === 1 && { chat: chats[0], opening: openings[0] };
    });
    t.record('ba_sm_connection', connection);
    const shared = await waitFor('the three exact approved file versions are shared', [ba, sm], evidence => {
      const files = evidence.workflow.attachments.filter(row => row.chat_id === connection.chat.id && row.owner_id === 'owner-alex');
      return revised.files.every(file => files.some(item => item.sha256 === file.sha256)) && { evidence, files };
    });
    t.record('ba_shared_files', { files: shared.files, approvals: shared.evidence.approvals.filter(row => row.file_shares?.length) });
    const delivered = await waitFor('all stories published and delivered to Scrum Master', [ba, sm], evidence => {
      const messages = evidence.center.messages.filter(row => row.recipients.includes('owner-maya'));
      const text = messages.map(row => `${row.title}\n${row.body}`).join('\n');
      const deliveries = evidence.center.deliveries.filter(row => messages.some(message => message.id === row.message_id));
      const refs = messages.flatMap(row => row.metadata?.twinteams_attachment_ids || []);
      return messages.length >= 3 && includesStories(text) && new Set(refs).size >= 3 && deliveries.length === messages.length
        && deliveries.every(row => row.status === 'processed') && { evidence, messages, deliveries, refs };
    });
    const attachments = delivered.evidence.workflow.attachments.filter(row => delivered.refs.includes(row.id));
    assert.equal(attachments.length, 3, 'the three Center references resolve to published snapshots');
    for (const file of revised.files) assert.ok(attachments.some(item => item.sha256 === file.sha256), 'published snapshot matches revised story bytes');
    for (const file of revised.files) {
      const attachment = attachments.find(item => item.sha256 === file.sha256);
      assert.equal(delivered.messages.filter(message => (message.metadata?.twinteams_attachment_ids || []).includes(attachment.id)).length, 1, 'each story has its own Center message');
    }
    assert.ok(delivered.evidence.tools.some(row => row.name === 'present_files' && row.success && row.owner_id === 'owner-alex'));
    for (const message of delivered.messages) assert.deepEqual(message.recipients, ['owner-maya'], 'story handoff is scoped to the Scrum Master');
    const firstAttachment = attachments[0];
    const attachmentURL = `/api/chats/${firstAttachment.chat_id}/attachments/${firstAttachment.id}`;
    for (const entry of [dev1, dev2]) {
      const response = await entry.page.request.get(new URL(attachmentURL, entry.page.url()).href);
      assert.ok([403, 404].includes(response.status()), 'unapproved developers cannot read BA snapshots');
    }
    if (!(await sm.page.getByRole('tab', { name: 'Duets', exact: true }).count())) {
      await sm.page.getByRole('button', { name: 'More tools', exact: true }).click();
      await sm.page.getByRole('menuitem', { name: 'Workspace', exact: true }).click();
    }
    await sm.page.getByRole('tab', { name: 'Duets', exact: true }).click();
    await sm.page.locator('.duet-conversation-card').filter({ hasText: connection.chat.title }).click();
    const card = sm.page.locator(`[data-attachment-id="${firstAttachment.id}"]`);
    await card.getByRole('button', { name: `Preview ${firstAttachment.name}`, exact: true }).click();
    const modal = sm.page.locator('.modal[role="dialog"]');
    await modal.locator('.attachment-document-preview h1, .attachment-document-preview h2').first().waitFor();
    const [download] = await Promise.all([sm.page.waitForEvent('download'), modal.getByRole('link', { name: 'Download', exact: true }).click()]);
    const bytes = await require('node:fs/promises').readFile(await download.path());
    assert.equal(require('node:crypto').createHash('sha256').update(bytes).digest('hex'), firstAttachment.sha256, 'SM preview download is the approved snapshot');
    await sm.page.screenshot({ path: path.join(t.artifactDir, 'sm-shared-file-preview.png'), fullPage: true });
    await modal.getByRole('button', { name: 'Close', exact: true }).click();
    t.record('ba_publication', { messages: delivered.messages, deliveries: delivered.deliveries, calls: delivered.evidence.tools.filter(row => /center|workspace|present_files/i.test(row.name || '')) });
    await sm.page.screenshot({ path: path.join(t.artifactDir, 'sm-delivery.png'), fullPage: true });
    sm.allowedPeers = ['owner-jordan', 'owner-riley']; dev1.allowedPeers = ['owner-maya', 'owner-riley']; dev2.allowedPeers = ['owner-maya', 'owner-jordan'];
    const beforeConsultation = inspect();
    const beforeConsultationQuestions = new Set(beforeConsultation.questions.map(row => row.id));
    const beforeConsultationMessages = new Set(beforeConsultation.workflow.messages.map(row => row.id));
    await say(sm, 'I am the Scrum Master. Please read all three shared story files and synthesize the originals without dropping requirements. Discuss feasibility and capacity with CCC and DDD and report their answers to me before we decide what to accept. You may ask their Duets to collaborate; I authorize that discussion.', [sm, dev1, dev2]);
    sm.handleIdentity = async evidence => {
      const question = evidence.questions.find(row => row.owner_id === 'owner-maya' && row.chat_id === sm.chat.id && row.status === 'pending'
        && !beforeConsultationQuestions.has(row.id) && /who|names?|identit|platform ids?/i.test(row.question) && /ccc|ddd/i.test(row.question));
      if (!question) return false;
      await answer(sm, 'CCC is Jordan and DDD is Riley. Please consult their Duets about feasibility and capacity as requested.', [sm, dev1, dev2], question.id);
      t.record('sm_identity_answer', { question });
      return true;
    };
    const consultation = await waitFor('developer Duet consultations', [sm, dev1, dev2], evidence => {
      const calls = evidence.tools.filter(row => /duet_conversation/.test(row.name || '') && row.success && row.owner_id === 'owner-maya');
      const chats = [...new Map([...pairChats(evidence, 'owner-jordan'), ...pairChats(evidence, 'owner-riley')].map(row => [row.id, row])).values()];
      const reads = evidence.tools.filter(row => ['read_workspace_file', 'read'].includes(row.name) && row.success && row.owner_id === 'owner-maya');
      const readHashes = sharedReadHashes(evidence, 'owner-maya');
      const partialHashes = new Set(reads.flatMap(row => (row.attachment_ids_partially_read || [])
        .map(id => evidence.workflow.attachments.find(item => item.id === id)?.sha256).filter(Boolean)));
      const text = evidence.workflow.messages.filter(row => row.chat_id === sm.chat.id && row.author_id === 'twin-maya').map(row => row.body).join('\n');
      const tracking = evidence.workflow.tracking.filter(row => row.chat_id === sm.chat.id && row.status === 'active');
      return calls.length && pairChats(evidence, 'owner-jordan').length && pairChats(evidence, 'owner-riley').length
        && revised.files.every(file => readHashes.has(file.sha256) || partialHashes.has(file.sha256))
        && text.length >= 200 && tracking.length && { evidence, calls, chats, reads, readHashes: [...readHashes], partialHashes: [...partialHashes], tracking, synthesis: text };
    });
    for (const file of revised.files) {
      assert.ok(consultation.readHashes.includes(file.sha256) || consultation.partialHashes.includes(file.sha256), 'Scrum Master Duet accessed each actual shared story');
    }
    delete sm.handleIdentity;
    t.record('consultation', { calls: consultation.calls, chats: consultation.chats, reads: consultation.reads, fullReadHashes: consultation.readHashes, partialReadHashes: consultation.partialHashes, synthesis: consultation.synthesis });
    await afterConsultation(t, sessions, { drafted, revised, consultation, beforeConsultationQuestions, beforeConsultationMessages });
  },
}];
module.exports = { cases, selectStories, initialRequest, isSettled, peerQuestion, sharedReadHashes, consultationSettled, continueHeldConsultation, afterConsultation, afterCapacity, afterDeveloperPlans, previewFile, inspect, say, answer, waitFor, reviewReports };
