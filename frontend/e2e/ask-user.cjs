/** Actual SDK questions, canonical cards and real answer/resume APIs. Provider
 * choices are deterministic offline fixtures; business responses are not mocked. */
const { assert, api, login, logout, poll, privateChat, chooseChat, sendMessage } = require('./helpers.cjs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
function fixture(role = 'inspect', extra = []) {
  const result = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'), role, '--state', process.env.TWINTEAMS_E2E_STATE, ...extra], { encoding: 'utf8' }));
  assert.equal(result.browser_e2e, true); assert.equal(result.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID); return result;
}
const card = (page, id) => page.locator(`[data-question-id="${id}"]`);
const detail = (page, id) => api(page, '/chats/' + id + '?limit=100');
async function question(page, inbox, label, expected = 'pending') {
  return poll(async () => (await detail(page, inbox.id)).questions?.find(value => value.question.includes(label) && value.status === expected), 'canonical question ' + label + ' ' + expected, { timeout: 45000 });
}
async function create(page, inbox, label) {
  await sendMessage(page, 'E2E QUESTION ' + label);
  return question(page, inbox, label);
}
async function resumed(page, inbox, value) {
  const inboxDetail = await detail(page, inbox.id);
  const message = inboxDetail.messages.find(message => message.id === value.message_id);
  assert.ok(message, 'question belongs to a persisted Duet message');
  return poll(async () => {
    // ChatDetail intentionally projects only queued/running/failed requests.
    // The guarded fixture exposes the actual all-status journal for completion.
    const evidence = fixture(), current = evidence.questions.find(question => question.id === value.id);
    const origin = await detail(page, value.origin_chat_id);
    const initial = evidence.requests.find(request => request.id === current.origin_request_id);
    assert.ok(initial, 'original request remains linked');
    if (value.origin_chat_id === inbox.id) assert.equal(initial.id, message.request_id);
    else assert.equal(initial.chat_id, value.origin_chat_id, 'cross-chat origin is linked through the guarded question record, not leaked into the inbox request projection');
    const continuations = evidence.requests.filter(request => request.source === 'question_result' && request.chat_id === value.origin_chat_id && request.source_message_id === initial.source_message_id);
    assert.equal(continuations.length, 1, 'one answer resumes the original task once');
    const request = continuations[0];
    assert.equal(request.id, current.continuation_request_id);
    assert.notEqual(request.status, 'failed', request.error);
    return request.status === 'completed' && origin.messages.some(message => message.request_id === request.id && message.body) ? request : false;
  }, 'actual resumed original task', { timeout: 45000 });
}
const cases = [
  {
    name: 'questions / canonical question preserves drafts, suggestions resume once, reload and answer replay converge',
    run: async t => {
      assert.equal(process.env.TWINTEAMS_E2E_SCENARIO, 'ask-user');
      const { page } = await t.session(); const inbox = await privateChat(page);
      const second = await t.session('DEV-AAA', { baseURL: t.secondaryURL }); await privateChat(second.page);
      const label = t.unique('choice'), draft = t.unique('ongoing-work');
      const composer = page.locator('.composer textarea'); await composer.fill(draft);
      await sendMessage(second.page, 'E2E QUESTION ' + label);
      const value = await question(page, inbox, label);
      const active = card(page, value.id); await active.waitFor();
      assert.equal(value.options.length >= 2 && value.options.length <= 3, true);
      assert.equal(await active.getByRole('group', { name: 'Suggested answers', exact: true }).getByRole('button').count(), value.options.length);
      assert.equal(await composer.inputValue(), draft);
      assert.equal(await composer.evaluate(element => element === document.activeElement), true, 'incoming question does not steal focus');
      assert.equal(await page.getByRole('button', { name: 'Send message', exact: true }).isEnabled(), true, 'normal composer remains usable');
      assert.equal(await page.locator('[aria-modal="true"]').count(), 0);
      await active.getByRole('textbox', { name: 'Your answer', exact: true }).fill('Unsent answer draft');
      await second.page.reload();
      await card(second.page, value.id).waitFor();
      await card(second.page, value.id).getByRole('button', { name: value.options[0], exact: true }).click();
      const answered = await question(page, inbox, label, 'answered');
      await active.getByText('Answered', { exact: true }).waitFor();
      assert.equal(await active.getByRole('textbox').count(), 0);
      assert.equal(await active.locator('.ask-user-saved-answer p').innerText(), value.options[0]);
      assert.equal(await composer.inputValue(), draft, 'remote answer preserves the main draft');
      const continuation = await resumed(page, inbox, value);
      const replay = await page.request.post(new URL('/api/questions/' + value.id + '/answer', page.url()).href, { data: { answer: value.options[0] } });
      assert.equal(replay.status(), 200);
      assert.equal((await replay.json()).answer_message_id, answered.answer_message_id);
      const conflict = await page.request.post(new URL('/api/questions/' + value.id + '/answer', page.url()).href, { data: { answer: 'A different answer' } });
      assert.equal(conflict.status(), 409);
      assert.equal((await resumed(page, inbox, value)).id, continuation.id);
      await page.reload(); await card(page, value.id).getByText('Answered', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(t.artifactDir, 'question-answered.png'), fullPage: true });
      t.record('choice', { questionId: value.id, canonicalMessageId: value.message_id, ownerAnswerMessageId: answered.answer_message_id, continuationId: continuation.id, exactReplay: true, conflictStatus: conflict.status(), focusPreserved: true, modelMode: 'deterministic-offline' });
    },
  },
  {
    name: 'questions / multiline free text, conversational reply, dismissal and account isolation use canonical state',
    run: async t => {
      const { page } = await t.session(); const inbox = await privateChat(page);
      const label = t.unique('free'), value = await create(page, inbox, label), text = '  First line ' + label + '\nSecond line: keep this verbatim.  ';
      await card(page, value.id).getByRole('textbox', { name: 'Your answer', exact: true }).fill(text);
      await card(page, value.id).getByRole('button', { name: 'Send answer', exact: true }).click();
      const answered = await question(page, inbox, label, 'answered'); assert.equal(answered.answer, text);
      await card(page, value.id).getByText('Answered', { exact: true }).waitFor();
      assert.equal(await card(page, value.id).locator('.ask-user-saved-answer p').textContent(), text);
      await resumed(page, inbox, value);
      const naturalLabel = t.unique('reply'), natural = await create(page, inbox, naturalLabel);
      const row = page.locator(`article[data-message-id="${natural.message_id}"]`); await card(page, natural.id).waitFor();
      await row.getByTitle('Reply', { exact: true }).click();
      const naturalAnswer = 'Please use the later date ' + naturalLabel;
      await sendMessage(page, naturalAnswer);
      const replied = await question(page, inbox, naturalLabel, 'answered'); assert.equal(replied.answer, naturalAnswer);
      assert.ok((await detail(page, inbox.id)).messages.some(message => message.id === replied.answer_message_id && message.reply_to?.id === natural.message_id));
      await resumed(page, inbox, natural);
      const plainLabel = t.unique('plain'), plain = await create(page, inbox, plainLabel);
      await sendMessage(page, 'E2E ANSWER ' + plainLabel);
      const plainAnswer = await question(page, inbox, plainLabel, 'answered');
      assert.equal(plainAnswer.answer, 'E2E ANSWER ' + plainLabel);
      await resumed(page, inbox, plain);
      assert.ok(fixture().tools.some(tool => tool.name === 'answer_question' && tool.success && tool.arguments.question_id === plain.id && tool.arguments.message_id === plainAnswer.answer_message_id), 'unquoted chat input was associated by the actual SDK answer_question tool');
      const cancelLabel = t.unique('dismiss'), dismissed = await create(page, inbox, cancelLabel);
      await card(page, dismissed.id).getByRole('button', { name: 'Dismiss question', exact: true }).click();
      await question(page, inbox, cancelLabel, 'cancelled');
      await card(page, dismissed.id).getByText('Dismissed', { exact: true }).waitFor();
      await page.reload(); await card(page, dismissed.id).getByText('Dismissed', { exact: true }).waitFor();
      const privateLabel = t.unique('private'), privateQuestion = await create(page, inbox, privateLabel);
      await card(page, privateQuestion.id).getByRole('textbox', { name: 'Your answer', exact: true }).fill('Owner-only unsent draft');
      const other = await t.session('DEV-BBB', { baseURL: t.secondaryURL });
      const denied = await other.page.request.post(new URL('/api/questions/' + privateQuestion.id + '/answer', other.page.url()).href, { data: { answer: 'Foreign answer' } });
      assert.equal(denied.status(), 404);
      await logout(page); await login(page, 'DEV-BBB'); await privateChat(page);
      assert.equal(await card(page, privateQuestion.id).count(), 0);
      assert.equal(await page.getByRole('textbox', { name: 'Your answer', exact: true }).count(), 0);
      await logout(page); await login(page, 'DEV-AAA'); await privateChat(page);
      await card(page, privateQuestion.id).waitFor();
      assert.equal(await card(page, privateQuestion.id).getByRole('textbox', { name: 'Your answer', exact: true }).inputValue(), '', 'account switching clears unsent question input');
      await page.screenshot({ path: path.join(t.artifactDir, 'question-pending.png'), fullPage: true });
      await card(page, privateQuestion.id).getByRole('button', { name: 'Dismiss question', exact: true }).click();
      await question(page, inbox, privateLabel, 'cancelled');
      t.record('free_and_natural', { freeQuestionId: value.id, multilineVerbatim: true, naturalReplyQuestionId: natural.id, replyMessageId: replied.answer_message_id, dismissedQuestionId: dismissed.id, foreignAnswerStatus: denied.status(), switchedAccountDraftCleared: true });
    },
  },
  {
    name: 'questions / group origin stays private, worker answer resumes its actual SDK session and parent review',
    run: async t => {
      const { page } = await t.session(); const inbox = await privateChat(page);
      const group = (await api(page, '/bootstrap')).chats.find(chat => chat.kind === 'group'); assert.ok(group);
      await chooseChat(page, group.title);
      const toggle = page.getByRole('button', { name: 'Reply for me', exact: true });
      if (await toggle.getAttribute('aria-pressed') !== 'true') await toggle.click();
      const label = t.unique('group');
      await page.locator('.composer textarea').fill('E2E QUESTION ' + label); await page.getByRole('button', { name: 'Send message', exact: true }).click();
      const value = await question(page, inbox, label);
      assert.equal(value.origin_chat_id, group.id);
      const groupDetail = await detail(page, group.id); assert.deepEqual(groupDetail.questions, []);
      assert.equal(groupDetail.messages.some(message => message.id === value.message_id), false, 'question is not published into group');
      await privateChat(page); await card(page, value.id).waitFor();
      await card(page, value.id).getByRole('link', { name: 'From ' + value.origin_chat_title, exact: true }).click();
      await poll(() => page.locator('.chat-header h2').innerText().then(title => title === group.title), 'source chat opened');
      assert.equal(await card(page, value.id).count(), 0);
      await privateChat(page);
      await card(page, value.id).getByRole('button', { name: value.options[0], exact: true }).click();
      await question(page, inbox, label, 'answered'); await resumed(page, inbox, value);
      const childLabel = t.unique('worker');
      await sendMessage(page, 'E2E CHILD_QUESTION ' + childLabel);
      const child = await question(page, inbox, childLabel); await card(page, child.id).waitFor();
      const before = fixture(), childRecord = before.questions.find(row => row.id === child.id), original = before.requests.find(row => row.id === childRecord.origin_request_id);
      assert.notEqual(childRecord.runtime_session_id, original.session_id, 'question was asked inside the actual worker session');
      assert.ok(before.tools.some(tool => tool.name === 'ask_question' && tool.success && tool.session_id === childRecord.runtime_session_id && tool.run_id === childRecord.runtime_run_id));
      await page.screenshot({ path: path.join(t.artifactDir, 'question-worker-pending.png'), fullPage: true });
      await card(page, child.id).getByRole('button', { name: child.options[1], exact: true }).click();
      await question(page, inbox, childLabel, 'answered');
      const continuation = await poll(() => { const evidence = fixture(), current = evidence.questions.find(row => row.id === child.id); return evidence.requests.find(row => row.id === current.continuation_request_id && row.status === 'completed'); }, 'worker continuation completed', { timeout: 45000 });
      assert.equal(continuation.session_id, childRecord.runtime_session_id, 'answer resumes the actual question-asking worker');
      assert.notEqual(continuation.run_id, childRecord.runtime_run_id, 'resumption is a new turn in the same worker session');
      await poll(async () => (await detail(page, inbox.id)).messages.some(message => message.body.includes('E2E child question reviewed: ' + childLabel)), 'actual parent reviews the worker result', { timeout: 45000 });
      t.record('group_and_worker', { groupQuestionId: value.id, originChatId: group.id, privateMessageId: value.message_id, childQuestionId: child.id, originalWorkerSessionId: childRecord.runtime_session_id, resumedWorkerSessionId: continuation.session_id, resumedWorkerRunId: continuation.run_id, parentReviewed: true });
    },
  },
  {
    name: 'questions / older-page question keeps its answer draft and focus during canonical history reauthorization',
    run: async t => {
      const { page } = await t.session(); const inbox = await privateChat(page), label = t.unique('history'), value = await create(page, inbox, label);
      const seeded = fixture('question-history', ['--question-id', value.id]);
      assert.equal(seeded.test_only_canonical_history_seed, true); assert.equal(seeded.message_count, 101); assert.equal(seeded.model_runs_added, 0);
      await page.reload(); await page.getByRole('button', { name: 'Load earlier messages', exact: true }).waitFor();
      assert.equal(await card(page, value.id).count(), 0, 'question starts outside the latest page');
      await page.getByRole('button', { name: 'Load earlier messages', exact: true }).click(); await card(page, value.id).waitFor();
      const input = card(page, value.id).getByRole('textbox', { name: 'Your answer', exact: true }); const draft = 'Earlier question draft ' + label; await input.fill(draft);
      await input.evaluate(element => { window.__questionHistoryProbe = { element, disconnected: false }; const observer = new MutationObserver(() => { if (!element.isConnected) window.__questionHistoryProbe.disconnected = true; }); observer.observe(document.body, { childList: true, subtree: true }); window.__questionHistoryProbe.observer = observer; });
      const loads = [];
      page.on('requestfinished', request => { const url = new URL(request.url()); if (request.method() === 'GET' && url.pathname === '/api/chats/' + inbox.id && url.searchParams.has('before_seq')) loads.push(url.pathname + url.search); });
      const second = await t.session('DEV-AAA', { baseURL: t.secondaryURL }); await privateChat(second.page);
      const settings = (await detail(second.page, inbox.id)).settings;
      const changed = await second.page.request.patch(new URL('/api/chats/' + inbox.id + '/settings', second.page.url()).href, { data: { sidebar_open: !settings.sidebar_open } }); assert.equal(changed.status(), 200);
      await poll(() => loads.length, 'older history page was reauthorized after a real chat change');
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const observed = await input.evaluate(element => { const result = { sameElement: window.__questionHistoryProbe.element === element, disconnected: window.__questionHistoryProbe.disconnected, focused: document.activeElement === element }; window.__questionHistoryProbe.observer.disconnect(); delete window.__questionHistoryProbe; return result; });
      assert.deepEqual(observed, { sameElement: true, disconnected: false, focused: true }); assert.equal(await input.inputValue(), draft);
      await card(page, value.id).getByRole('button', { name: 'Dismiss question', exact: true }).click();
      await card(page, value.id).getByText('Dismissed', { exact: true }).waitFor();
      t.record('older_history', { questionId: value.id, explicitSeed: seeded, reauthorizedOlderPages: loads.length, sameInputElement: true, draftAndFocusPreserved: true });
    },
  },
];
module.exports = { cases };
