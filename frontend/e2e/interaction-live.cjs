/** One bounded real-model interaction. No scripted E2E model command or business
 * interception: the owner asks naturally, answers a real card, and policy persists. */
const { assert, api, privateChat, sendMessage, poll } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs/promises');
function inspect() {
  const result = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'), 'inspect', '--state', process.env.TWINTEAMS_E2E_STATE], { encoding: 'utf8' }));
  assert.equal(result.browser_e2e, true); assert.equal(result.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID); return result;
}
const cases = [{
  name: 'live interaction / natural owner question and answer persist initiative and quiet-hour preferences',
  timeout: 360000,
  run: async t => {
    assert.equal(process.env.TWINTEAMS_E2E_MODEL_MODE, 'live-gpt-6-luna');
    assert.equal(process.env.TWINTEAMS_E2E_SCENARIO, 'interaction-live');
    const { page } = await t.session(); const chat = await privateChat(page);
    const prompt = 'Only proactively contact me for important results, blockers, or decisions. Please ask me to choose between quiet hours 22:00–08:00 or 23:00–07:00 in Asia/Shanghai, with those two choices. After I answer, save my initiative and quiet-hour preferences.';
    await fs.writeFile(path.join(t.artifactDir, 'interaction-live-input.json'), JSON.stringify({ prompt, desiredChoice: '22:00–08:00 Asia/Shanghai', actualModel: 'gpt-6-luna', scriptedCommands: false }, null, 2));
    const prior = new Set((await api(page, '/chats/' + chat.id)).questions?.map(question => question.id) || []);
    const sent = await sendMessage(page, prompt); const sourceMessageId = await sent.getAttribute('data-message-id');
    const question = await poll(async () => {
      const state = await api(page, '/chats/' + chat.id);
      const failed = state.requests.find(request => request.status === 'failed' && request.source_message_id === sourceMessageId);
      if (failed) throw new Error('Live initial task failed: ' + failed.error);
      return state.questions?.find(question => question.status === 'pending' && !prior.has(question.id));
    }, 'real model asks a persisted owner question', { timeout: 150000, interval: 500 });
    assert.equal(question.options.length, 2, 'owner requested two suggested choices');
    const choice = question.options.find(option => /22(?:[:：]00)?|10\s*p\.?\s*m\.?/i.test(option));
    assert.ok(choice, 'the suggested choices identify the requested22:00 quiet window');
    const card = page.locator(`[data-question-id="${question.id}"]`); await card.waitFor(); await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(t.artifactDir, 'interaction-live-question.png'), fullPage: true });
    await card.getByRole('button', { name: choice, exact: true }).click();
    const expected = { level: 'low', quiet_hours: { start: '22:00', end: '08:00', timezone: 'Asia/Shanghai' }, review_interval_seconds: 14400 };
    const saved = await poll(async () => {
      const state = await api(page, '/chats/' + chat.id), current = state.questions?.find(row => row.id === question.id);
      const task = (await api(page, '/me/twin/scheduled-tasks')).items.find(task => task.kind === 'system_review');
      const value = task?.proactivity;
      if (current?.status !== 'answered' || value?.level !== expected.level || value.review_interval_seconds !== expected.review_interval_seconds || value.quiet_hours?.start !== '22:00' || value.quiet_hours?.end !== '08:00' || value.quiet_hours?.timezone !== 'Asia/Shanghai') return false;
      return { question: current, task };
    }, 'real model saves the chosen preferences after the owner answer', { timeout: 180000, interval: 500 });
    assert.equal(saved.question.answer, choice);
    assert.ok(saved.question.answer_message_id); assert.equal(saved.task.enabled, true);
    const evidence = await poll(() => {
      const value = inspect(), current = value.questions.find(row => row.id === question.id), request = value.requests.find(row => row.id === current?.continuation_request_id);
      if (request?.status === 'failed') throw new Error('Live owner-answer continuation failed');
      return request?.status === 'completed' ? value : false;
    }, 'real model completes the resumed owner task', { timeout: 120000, interval: 500 });
    const calls = evidence.tools.filter(tool => tool.success);
    const ask = calls.find(tool => tool.name === 'ask_question' && tool.result_ids.id === question.id);
    assert.ok(ask, 'question is the actual SDK ask_question result');
    const updates = calls.filter(tool => tool.name === 'update_proactivity');
    assert.ok(updates.some(tool => tool.arguments.level === 'low'));
    assert.ok(updates.some(tool => tool.arguments.quiet_hours?.start === '22:00' && tool.arguments.quiet_hours?.end === '08:00' && tool.arguments.quiet_hours?.timezone === 'Asia/Shanghai'));
    await card.getByText('Answered', { exact: true }).waitFor(); await card.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(t.artifactDir, 'interaction-live-answered.png'), fullPage: true });
    await page.reload(); await privateChat(page);
    assert.deepEqual((await api(page, '/me/twin/scheduled-tasks')).items.find(task => task.kind === 'system_review').proactivity, expected);
    t.record('real_interaction', { actualModel: 'gpt-6-luna', sourceMessageId, questionId: question.id, canonicalQuestionMessageId: question.message_id, choice, ownerAnswerMessageId: saved.question.answer_message_id, persistedPolicy: saved.task.proactivity, askOperation: ask.call_id, updateOperations: updates.map(tool => ({ callId: tool.call_id, requestId: tool.request_id, arguments: tool.arguments })), getOperationCount: calls.filter(tool => tool.name === 'get_proactivity').length, noScriptedModelCommands: true, reloadVerified: true });
  },
}];
module.exports = { cases };
