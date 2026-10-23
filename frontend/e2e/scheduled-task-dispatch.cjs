/** Real due dispatch. The CLI changes only one disposable task's due metadata. */
const { assert, api, openTwinTab, poll, sleep } = require('./helpers.cjs');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const path = require('node:path');
const execute = promisify(execFile);
async function advanceDue(taskId) {
  const { stdout } = await execute(process.env.TWINTEAMS_E2E_PYTHON, [
    path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'),
    'advance-schedule', '--state', process.env.TWINTEAMS_E2E_STATE, '--task-id', taskId,
  ], { timeout: 25000 });
  const value = JSON.parse(stdout);
  assert.equal(value.browser_e2e, true);
  assert.equal(value.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  assert.equal(value.task_id, taskId);
  assert.equal(value.clock_advanced, false);
  return value;
}
const taskIndex = page => api(page, '/me/twin/scheduled-tasks');
const taskHistory = (page, id) => api(page, `/me/twin/scheduled-tasks/${encodeURIComponent(id)}/history`);
const cases = [{
  name: 'scheduled due dispatch / isolated due advance wakes the real worker exactly once without Run now',
  run: async t => {
    const { page } = await t.session();
    await openTwinTab(page, 'Scheduled tasks');
    await page.getByRole('button', { name: 'New task', exact: true }).click();
    const form = page.locator('.modal[role="dialog"]'), title = t.unique('due-dispatch'), label = t.unique('due-output');
    await form.getByLabel('Task name', { exact: true }).fill(title);
    await form.getByLabel('Instructions', { exact: true }).fill(`E2E ECHO ${label}`);
    await form.getByLabel('Time zone', { exact: true }).fill('Asia/Shanghai');
    await form.getByLabel('Schedule', { exact: true }).selectOption('interval');
    await form.getByLabel('Repeat every', { exact: true }).fill('1');
    await form.getByLabel('Interval unit', { exact: true }).selectOption('3600');
    await form.getByRole('button', { name: 'Save task', exact: true }).click();
    await form.waitFor({ state: 'hidden' });
    const task = await poll(async () => (await taskIndex(page)).items.find(x => x.title === title), 'future task persisted');
    assert.equal((await taskHistory(page, task.id)).items.length, 0);
    const advanced = await advanceDue(task.id);
    assert.equal(advanced.revision_unchanged, task.revision);
    t.record('fixture_due_advance', advanced);
    const completed = await poll(async () => {
      const items = (await taskHistory(page, task.id)).items;
      const failure = items.find(x => x.status === 'failed');
      if (failure) throw new Error(`Due task failed: ${failure.error}`);
      return items.find(x => x.status === 'completed' && x.trigger === 'scheduled' && x.run_id);
    }, 'real scheduled occurrence completed', { timeout: 45000, interval: 250 });
    const snapshot = await api(page, '/chats/private-alex');
    const output = snapshot.messages.filter(x => x.request_id === completed.request_id);
    assert.equal(output.length, 1);
    assert.ok(output[0].body.includes(label));
    await sleep(600);
    assert.equal((await taskHistory(page, task.id)).items.length, 1, 'one due occurrence is not admitted twice');
    assert.ok(new Date((await taskIndex(page)).items.find(x => x.id === task.id).next_run_at).getTime() > Date.now(), 'recurring due time advances into the future');
    t.record('real_due_result', { task_id: task.id, request_id: completed.request_id, run_id: completed.run_id, trigger: completed.trigger, message_id: output[0].id, output_count: output.length, elapsed_interval_claimed: false });
    const row = page.locator(`[data-task-id="${task.id}"]`);
    await row.getByRole('button', { name: `Delete ${title}`, exact: true }).click();
    await form.getByRole('button', { name: 'Delete task', exact: true }).click();
    await form.waitFor({ state: 'hidden' });
  },
}];
module.exports = { cases, advanceDue, taskIndex, taskHistory };
