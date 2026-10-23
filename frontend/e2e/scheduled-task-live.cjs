/** Bounded actual-model acceptance; setup uses real API, time advance is explicit. */
const { assert, api, poll, privateChat, openTwinTab, sleep } = require('./helpers.cjs');
const { send, waitTwin, inspect, state } = require('./continuous-workflow.cjs');
const { advanceDue, taskIndex, taskHistory } = require('./scheduled-task-dispatch.cjs');
const { randomInt } = require('node:crypto');
const path = require('node:path');
async function post(page, endpoint, data) {
  return page.evaluate(async ({ endpoint, data }) => {
    const response = await fetch('/api' + endpoint, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    if (!response.ok) throw new Error(`Setup API ${endpoint}: ${response.status}`);
    return response.json();
  }, { endpoint, data });
}
async function completed(page, id, previous = []) {
  return poll(async () => {
    const values = (await taskHistory(page, id)).items.filter(x => !previous.includes(x.request_id));
    const failure = values.find(x => ['failed', 'cancelled'].includes(x.status));
    if (failure) throw new Error(`Scheduled occurrence ${failure.status}: ${failure.error || failure.request_id}`);
    const finished = values.find(x => x.status === 'completed' && x.trigger === 'scheduled' && x.run_id);
    if (!finished) return false;
    const task = (await taskIndex(page)).items.find(x => x.id === id);
    if (task.pending_work || task.active_request_id) return false;
    const evidence = await inspect(), root = evidence.requests.find(x => x.id === finished.request_id);
    const chain = evidence.requests.filter(x => x.causal_chain_id === root.causal_chain_id);
    if (chain.some(x => ['queued', 'running'].includes(x.status))) return false;
    return { ...finished, chain_request_ids: chain.map(x => x.id), evidence };
  }, 'actual due occurrence completed', { timeout: 180000, interval: 1000 });
}
function published(snapshot, occurrence) {
  return snapshot.messages.filter(x => x.kind === 'twin' && occurrence.chain_request_ids.includes(x.request_id));
}
const cases = [{
  name: 'live scheduled task / natural recurring request, due action, changed review then unchanged silence',
  timeout: 900000,
  run: async t => {
    const { page } = await t.session();
    const chat = await privateChat(page), project = t.unique('Delivery'), quantity = randomInt(110, 340), fee = randomInt(41, 89);
    const topic = await post(page, '/chats/group-team/tracking', { title: project, description: `项目${project}：Maya负责验证，Jordan负责验收；具体数量和最新状态以进展更新为准。` });
    const initialUpdate = await post(page, `/chats/group-team/tracking/${topic.id}/updates`, { body: `当前样品${quantity}件已齐备，下一步由Jordan验收；目前没有待批准费用。` });
    t.record('setup', { method: 'real authenticated API', seeded_by_human_test_setup: true, agent_created_tracking_claimed: false, group_chat_id: 'group-team', topic_id: topic.id, updated_topic_id: initialUpdate.id, latest_state_only_in_update: true, project, quantity });
    const ask = await send(page, chat.id, `请保存一个周期任务：按Asia/Shanghai时区，每天09:30检查${project}的最新项目记录，向我简短汇报当前状态、样品数量、负责人和下一步。只检查和汇报，不替我批准费用。本次先保存安排，不用立即检查，也不要修改项目进度。`);
    await waitTwin(page, chat.id, ask.id);
    const task = await poll(async () => (await taskIndex(page)).items.find(x => x.kind === 'user' && x.instructions.includes(project)), 'model-created recurring task', { timeout: 30000 });
    assert.equal(task.timezone, 'Asia/Shanghai');
    assert.deepEqual(task.schedule, { kind: 'daily', time: '09:30' });
    const created = await inspect();
    assert.ok(created.tools.some(x => x.name === 'create_scheduled_task' && x.success && x.owner_id === 'owner-alex' && x.result_ids.id === task.id), 'actual SDK tool created the task');
    await openTwinTab(page, 'Scheduled tasks');
    const card = page.locator(`[data-task-id="${task.id}"]`);
    await card.waitFor();
    assert.ok((await card.innerText()).includes('Asia/Shanghai'));
    await page.screenshot({ path: path.join(t.artifactDir, 'live-scheduled-task-created.png'), fullPage: true });
    t.record('model_created_schedule', { id: task.id, timezone: task.timezone, schedule: task.schedule, next_run_at: task.next_run_at, screenshot: 'live-scheduled-task-created.png' });
    const due = await advanceDue(task.id); t.record('recurring_fixture_due', due);
    const occurrence = await completed(page, task.id);
    const owner = await state(page, chat.id), output = published(owner, occurrence);
    assert.ok(output.length >= 1, 'settled occurrence has an owner report');
    assert.ok(output.some(x => x.body.includes(String(quantity))), 'scheduled work used the actual project quantity');
    const evidence = await inspect();
    assert.ok(evidence.tools.some(x => occurrence.chain_request_ids.includes(x.request_id) && x.name === 'get_tracking' && x.success && x.tracking_id === topic.id), 'scheduled run read the actual group topic');
    t.record('automatic_execution', { task_id: task.id, request_id: occurrence.request_id, run_id: occurrence.run_id, message_id: output[0].id, scheduled_trigger: occurrence.trigger, output_count: output.length, group_topic_read: topic.id });
    // A real new group update introduces an owner decision after the task's report.
    const change = await post(page, `/chats/group-team/tracking/${topic.id}/updates`, { body: `新变更：${project}的防潮包装需要追加${fee}元，需要Alex决定是否接受，目前尚未批准。` });
    const system = (await taskIndex(page)).items.find(x => x.kind === 'system_review');
    assert.deepEqual(system.schedule, { kind: 'interval', seconds: 14400 });
    const firstPrevious = (await taskHistory(page, system.id)).items.map(x => x.request_id);
    t.record('material_change', { updated_topic_id: change.id, topic_id: topic.id, fee, approval_granted: false });
    t.record('review_fixture_due', await advanceDue(system.id));
    const review = await completed(page, system.id, firstPrevious);
    const reviewed = await state(page, chat.id), notices = published(reviewed, review);
    assert.equal(notices.length, 1, 'a meaningful owner decision is reported once');
    assert.ok(notices[0].body.includes(String(fee)), 'notice identifies the actual new fee');
    t.record('meaningful_review', { request_id: review.request_id, message_id: notices[0].id, output_count: notices.length });
    const previousIds = (await taskHistory(page, system.id)).items.map(x => x.request_id);
    t.record('unchanged_fixture_due', await advanceDue(system.id));
    const silentRun = await completed(page, system.id, previousIds);
    await sleep(600);
    const final = await state(page, chat.id), duplicate = published(final, silentRun);
    assert.equal(duplicate.length, 0, 'unchanged review publishes no routine or repeated notice');
    const all = await inspect();
    assert.ok(all.live_budget && all.live_budget.max_attempts === 40);
    assert.ok(all.live_budget.attempts.length <= 40);
    assert.ok(all.live_budget.attempts.every(x => x.model === 'gpt-6-luna' && x.max_output_tokens <= 4096));
    const reviewCalls = all.model_calls.filter(x => silentRun.chain_request_ids.includes(x.request_id));
    assert.ok(reviewCalls.length > 0, 'ongoing unchanged Tracking is actually reviewed by the model');
    t.record('unchanged_silent_review', { new_request_ids: silentRun.chain_request_ids, root_request_id: silentRun.request_id, owner_outputs: duplicate.length, actual_model_calls: reviewCalls.length });
    t.record('live_model_budget', { actual_attempts: all.live_budget.attempts.length, maximum_attempts: 40, model: 'gpt-6-luna', max_output_tokens: 4096 });
    t.record('boundaries', { real_four_hour_wait_claimed: false, fixture_due_metadata_advanced: true, group_tracking_read: true, live_peer_Twin_conversation_claimed: false, external_effects: false });
  },
}];
module.exports = { cases };
