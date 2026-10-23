/** Conversation preferences through actual SDK tools, with real scheduler/API
 * effects. The explicit due advance is isolated test metadata, never fake time. */
const { assert, api, openTwinTab, privateChat, sendMessage, poll } = require('./helpers.cjs');
const { advanceDue, taskIndex, taskHistory } = require('./scheduled-task-dispatch.cjs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
function inspect() {
  const result = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'), 'inspect', '--state', process.env.TWINTEAMS_E2E_STATE], { encoding: 'utf8' }));
  assert.equal(result.browser_e2e, true); assert.equal(result.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID); return result;
}
const detail = (page, chat) => api(page, '/chats/' + chat.id);
async function systemTask(page) { return (await taskIndex(page)).items.find(task => task.kind === 'system_review'); }
async function policy(t, page, chat, command) {
  const label = t.unique(command.toLowerCase()), marker = 'E2E proactivity readback ' + label + ': ';
  await sendMessage(page, 'E2E ' + command + ' ' + label);
  const message = await poll(async () => (await detail(page, chat)).messages.find(message => message.kind === 'twin' && message.body.startsWith(marker)), 'actual preference tool readback', { timeout: 45000 });
  const result = JSON.parse(message.body.slice(marker.length));
  const evidence = await poll(() => { const value = inspect(); return value.requests.some(request => request.id === message.request_id && request.status === 'completed') ? value : false; }, 'preference command completed');
  const calls = evidence.tools.filter(tool => tool.request_id === message.request_id && tool.success);
  assert.ok(calls.some(tool => tool.name === 'get_proactivity'), 'chat result reads the actual persisted policy');
  if (command !== 'PROACTIVITY_GET') assert.ok(calls.some(tool => tool.name === 'update_proactivity'), 'chat change uses the actual product mutation tool');
  assert.deepEqual((await systemTask(page)).proactivity, result);
  return { result, requestId: message.request_id, messageId: message.id, calls };
}
async function completed(page, task, trigger) {
  return poll(async () => {
    const rows = (await taskHistory(page, task.id)).items, failure = rows.find(row => row.status === 'failed');
    if (failure) throw new Error('Scheduled occurrence failed: ' + failure.error);
    return rows.find(row => row.status === 'completed' && row.run_id && row.trigger === trigger);
  }, 'actual ' + trigger + ' occurrence completed', { timeout: 45000 });
}
async function backToChat(page) { await page.getByRole('button', { name: 'Back to chats', exact: true }).click(); return privateChat(page); }
const cases = [
  {
    name: 'proactivity / conversation level and quiet-hour changes read back persisted owner policy without a new form',
    run: async t => {
      assert.equal(process.env.TWINTEAMS_E2E_SCENARIO, 'proactivity');
      const { page } = await t.session(); const chat = await privateChat(page);
      const baseline = await policy(t, page, chat, 'PROACTIVITY_GET');
      assert.deepEqual(baseline.result, { level: 'normal', quiet_hours: null, review_interval_seconds: 14400 });
      const levels = [];
      for (const level of ['off', 'low', 'normal', 'high']) {
        const value = await policy(t, page, chat, 'PROACTIVITY_' + level.toUpperCase()), task = await systemTask(page);
        assert.equal(value.result.level, level); assert.equal(task.enabled, level !== 'off');
        assert.equal(value.result.review_interval_seconds, 14400, 'changing initiative does not invent a new frequency');
        assert.deepEqual(task.schedule, { kind: 'interval', seconds: 14400 });
        levels.push({ level, requestId: value.requestId, messageId: value.messageId });
      }
      const quiet = await policy(t, page, chat, 'QUIET');
      assert.deepEqual(quiet.result.quiet_hours, { start: '22:00', end: '08:00', timezone: 'Asia/Shanghai' });
      assert.equal(quiet.result.level, 'high');
      await page.reload(); await privateChat(page);
      assert.deepEqual((await policy(t, page, chat, 'PROACTIVITY_GET')).result, quiet.result);
      const other = await t.session('DEV-BBB', { baseURL: t.secondaryURL }), otherChat = await privateChat(other.page);
      assert.deepEqual((await policy(t, other.page, otherChat, 'PROACTIVITY_GET')).result, baseline.result, 'preferences stay with their owner');
      const cleared = await policy(t, page, chat, 'QUIET_CLEAR'); assert.equal(cleared.result.quiet_hours, null); assert.equal(cleared.result.level, 'high');
      await page.screenshot({ path: path.join(t.artifactDir, 'proactivity-conversation.png'), fullPage: true });
      t.record('conversation_policy', { defaultPolicy: baseline.result, levels, storedQuietHours: quiet.result.quiet_hours, ownerIsolation: true, clearOnlyQuietHours: true, intervalUnchanged: true, modelMode: 'deterministic-offline' });
    },
  },
  {
    name: 'proactivity / actual quiet admission defers only system review while manual runs, explicit tasks and answers continue',
    timeout: 180000,
    run: async t => {
      const { page } = await t.session(); const chat = await privateChat(page);
      await policy(t, page, chat, 'PROACTIVITY_NORMAL');
      const quiet = await policy(t, page, chat, 'QUIET_NOW'), window = quiet.result.quiet_hours;
      assert.equal(window.timezone, 'UTC');
      const actual = new Date(), nowMinute = actual.getUTCHours() * 60 + actual.getUTCMinutes();
      const minutes = value => Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
      assert.equal(minutes(window.start) < minutes(window.end) ? minutes(window.start) <= nowMinute && nowMinute < minutes(window.end) : nowMinute >= minutes(window.start) || nowMinute < minutes(window.end), true, 'quiet window contains actual wall-clock time');
      const expectedEnd = new Date(actual); expectedEnd.setUTCHours(Number(window.end.slice(0, 2)), Number(window.end.slice(3)), 0, 0); if (expectedEnd <= actual) expectedEnd.setUTCDate(expectedEnd.getUTCDate() + 1);
      const review = await systemTask(page), before = inspect(), historyBefore = (await taskHistory(page, review.id)).items;
      assert.equal(review.enabled, true, 'this is quiet deferral, not off');
      const advanced = await advanceDue(review.id);
      await poll(async () => { const current = await systemTask(page); return current.next_run_at && new Date(current.next_run_at).getTime() === expectedEnd.getTime() ? current : false; }, 'real due system review deferred to quiet end', { timeout: 45000 });
      const after = inspect();
      assert.deepEqual((await taskHistory(page, review.id)).items, historyBefore, 'quiet admission creates no occurrence');
      assert.equal(after.requests.filter(request => request.source === 'system_review').length, before.requests.filter(request => request.source === 'system_review').length);
      const systemModels = evidence => evidence.model_calls.filter(call => evidence.requests.some(request => request.id === call.request_id && request.source === 'system_review')).length;
      assert.equal(systemModels(after), systemModels(before), 'quiet deferral does not call the model');
      t.record('quiet_admission', { configuredUsingActualClock: true, fakeClock: false, waitedForQuietEnd: false, window, fixtureDueAdvance: advanced, deferredUntil: expectedEnd.toISOString(), reviewStillEnabled: true, noOccurrenceOrModelCall: true });
      await openTwinTab(page, 'Scheduled tasks');
      await page.locator(`[data-task-id="${review.id}"]`).getByRole('button', { name: 'Run now ' + review.title, exact: true }).click();
      const manualReview = await completed(page, review, 'manual');
      assert.ok(inspect().model_calls.some(call => call.request_id === manualReview.request_id), 'manual review actually executes despite active quiet hours');
      assert.equal((await detail(page, chat)).messages.some(message => message.request_id === manualReview.request_id), false, 'offline empty review result stays quiet');
      await backToChat(page); await policy(t, page, chat, 'PROACTIVITY_OFF');
      const label = t.unique('explicit'), response = await page.request.post(new URL('/api/me/twin/scheduled-tasks', page.url()).href, { data: { title: label, instructions: 'E2E ECHO ' + label, timezone: 'UTC', schedule: { kind: 'interval', seconds: 3600 } } });
      assert.equal(response.status(), 200); const task = await response.json();
      const explicitAdvance = await advanceDue(task.id), scheduled = await completed(page, task, 'scheduled');
      assert.ok((await detail(page, chat)).messages.some(message => message.request_id === scheduled.request_id && message.body.includes(label)));
      await openTwinTab(page, 'Scheduled tasks');
      await page.locator(`[data-task-id="${task.id}"]`).getByRole('button', { name: 'Run now ' + task.title, exact: true }).click();
      const manualTask = await completed(page, task, 'manual'); assert.notEqual(manualTask.request_id, scheduled.request_id);
      assert.equal((await systemTask(page)).proactivity.level, 'off');
      await backToChat(page);
      const childLabel = t.unique('off-worker'); await sendMessage(page, 'E2E CHILD_QUESTION ' + childLabel);
      const question = await poll(async () => (await detail(page, chat)).questions?.find(question => question.status === 'pending' && question.question.includes(childLabel)), 'worker asks a question while review is off', { timeout: 45000 });
      const originalQuestion = inspect().questions.find(row => row.id === question.id);
      await page.locator(`[data-question-id="${question.id}"]`).getByRole('button', { name: question.options[0], exact: true }).click();
      const childContinuation = await poll(() => { const evidence = inspect(), current = evidence.questions.find(row => row.id === question.id); return evidence.requests.find(request => request.id === current.continuation_request_id && request.status === 'completed'); }, 'owner answer resumes worker while review is off', { timeout: 45000 });
      assert.equal(childContinuation.session_id, originalQuestion.runtime_session_id);
      await poll(async () => (await detail(page, chat)).messages.some(message => message.body.includes('E2E child question reviewed: ' + childLabel)), 'worker result reaches parent while review is off', { timeout: 45000 });
      assert.equal((await systemTask(page)).proactivity.level, 'off');
      t.record('unaffected_work', { manualReviewRequestId: manualReview.request_id, explicitTaskId: task.id, fixtureDueAdvance: explicitAdvance, scheduledRequestId: scheduled.request_id, manualTaskRequestId: manualTask.request_id, childQuestionId: question.id, resumedWorkerSessionId: childContinuation.session_id, parentReviewed: true, quietHoursAndOffDidNotCancelExplicitWork: true });
    },
  },
];
module.exports = { cases };
