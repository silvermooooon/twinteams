/** Real browser/product/PG/Redis/SDK lifecycle; existing deterministic OFFLINE provider.
 * No browser API interception, fake clock or asserted fabricated model reasoning.
 * Natural-language tool choice and autonomous notification belong to the live scenario.
 */
const { assert, api, openTwinTab, poll, privateChat } = require('./helpers.cjs');
const path = require('node:path');
const button = (page, name) => page.getByRole('button', { name, exact: true });
const dialog = page => page.locator('.modal[role="dialog"]');
const card = (page, id) => page.locator(`[data-task-id="${id}"]`);
const tasks = page => api(page, '/me/twin/scheduled-tasks');
async function taskByTitle(page, title) {
  return poll(async () => (await tasks(page)).items.find(task => task.title === title), 'saved scheduled task ' + title);
}
async function taskState(page, id, predicate) {
  return poll(async () => {
    const task = (await tasks(page)).items.find(item => item.id === id);
    return task && predicate(task) ? task : false;
  }, 'scheduled task state ' + id, { timeout: 30000 });
}
async function history(page, id) {
  return api(page, `/me/twin/scheduled-tasks/${encodeURIComponent(id)}/history?limit=20`);
}
async function newTask(page, title, instructions, { kind = 'once', zone = 'Asia/Tokyo', at = '2030-01-15T09:30' } = {}) {
  await button(page, 'New task').click();
  const form = dialog(page);
  await form.getByLabel('Task name', { exact: true }).fill(title);
  await form.getByLabel('Instructions', { exact: true }).fill(instructions);
  await form.getByLabel('Time zone', { exact: true }).fill(zone);
  await form.getByLabel('Schedule', { exact: true }).selectOption(kind);
  if (kind === 'once') await form.getByLabel('Date and time', { exact: true }).fill(at);
  if (kind === 'interval') {
    await form.getByLabel('Repeat every', { exact: true }).fill('2');
    await form.getByLabel('Interval unit', { exact: true }).selectOption('3600');
  }
  await save(page);
  return taskByTitle(page, title);
}
async function save(page) {
  await dialog(page).getByRole('button', { name: 'Save task', exact: true }).click();
  await dialog(page).waitFor({ state: 'hidden' });
}
async function edit(page, task) {
  await card(page, task.id).getByRole('button', { name: `Edit ${task.title}`, exact: true }).click();
  return dialog(page);
}
async function remove(page, task) {
  await card(page, task.id).getByRole('button', { name: `Delete ${task.title}`, exact: true }).click();
  await dialog(page).getByRole('button', { name: 'Delete task', exact: true }).click();
  await dialog(page).waitFor({ state: 'hidden' });
  await card(page, task.id).waitFor({ state: 'hidden' });
  assert.equal((await tasks(page)).items.some(item => item.id === task.id), false);
}
const cases = [
  {
    name: 'scheduled tasks / real CRUD, selected time zone, daily weekly interval and pause',
    run: async t => {
      const { page } = await t.session();
      await openTwinTab(page, 'Scheduled tasks');
      const title = t.unique('schedule-rules');
      let task = await newTask(page, title, 'Review the launch checklist and report a meaningful change.');
      assert.equal(task.kind, 'user');
      assert.equal(task.schedule.kind, 'once');
      assert.equal(new Date(task.schedule.at).toISOString(), '2030-01-15T00:30:00.000Z');
      assert.equal(new Date(task.next_run_at).toISOString(), '2030-01-15T00:30:00.000Z');
      let form = await edit(page, task);
      assert.equal(await form.getByLabel('Date and time', { exact: true }).inputValue(), '2030-01-15T09:30');
      await form.getByLabel('Time zone', { exact: true }).fill('America/New_York');
      await save(page);
      task = await taskState(page, task.id, item => item.timezone === 'America/New_York');
      assert.equal(new Date(task.schedule.at).toISOString(), '2030-01-15T14:30:00.000Z', 'selected zone converts the same wall time; browser local zone is not used');
      form = await edit(page, task);
      await form.getByLabel('Schedule', { exact: true }).selectOption('daily');
      await form.getByLabel('Time', { exact: true }).fill('08:45');
      await form.getByLabel('Time zone', { exact: true }).fill('Asia/Shanghai');
      await save(page);
      task = await taskState(page, task.id, item => item.schedule.kind === 'daily');
      assert.deepEqual(task.schedule, { kind: 'daily', time: '08:45' });
      form = await edit(page, task);
      await form.getByLabel('Schedule', { exact: true }).selectOption('weekly');
      await form.getByRole('button', { name: 'Mon', exact: true }).click();
      await form.getByRole('button', { name: 'Fri', exact: true }).click();
      await save(page);
      task = await taskState(page, task.id, item => item.schedule.kind === 'weekly');
      assert.deepEqual(task.schedule, { kind: 'weekly', time: '08:45', weekdays: [0, 4] });
      form = await edit(page, task);
      await form.getByLabel('Schedule', { exact: true }).selectOption('interval');
      await form.getByLabel('Repeat every', { exact: true }).fill('2');
      await form.getByLabel('Interval unit', { exact: true }).selectOption('3600');
      await save(page);
      task = await taskState(page, task.id, item => item.schedule.kind === 'interval');
      assert.deepEqual(task.schedule, { kind: 'interval', seconds: 7200 });
      await card(page, task.id).getByRole('button', { name: `Pause future runs for ${title}`, exact: true }).click();
      await taskState(page, task.id, item => !item.enabled && !item.next_run_at);
      await card(page, task.id).getByRole('button', { name: `Resume future runs for ${title}`, exact: true }).click();
      await taskState(page, task.id, item => item.enabled && !!item.next_run_at);
      await page.reload();
      await card(page, task.id).waitFor();
      assert.ok((await card(page, task.id).innerText()).includes('Every 2 hours'));
      await page.screenshot({ path: path.join(t.artifactDir, 'scheduled-task-rules.png'), fullPage: true });
      await remove(page, task);
      t.record('rules', { id: task.id, onceTokyoUTC: '2030-01-15T00:30:00Z', onceNewYorkUTC: '2030-01-15T14:30:00Z', daily: true, weeklyMondayFriday: true, intervalSeconds: 7200, persistedAfterReload: true, deleted: true });
    },
  },
  {
    name: 'scheduled tasks / system read-only instructions and live refresh preserves editor focus',
    run: async t => {
      // Observe actual frames without interception. A periodic worker heartbeat
      // legitimately refreshes Runtime/profile independently of a schedule hint.
      const frames = [];
      const { page } = await t.session('DEV-AAA', { contextSetup: context => {
        context.on('page', target => target.on('websocket', socket => socket.on('framereceived', event => {
          try {
            const frame = JSON.parse(String(event.payload));
            if (['personal_changed', 'worker_status', 'actor_changed'].includes(frame.type)) frames.push({ at: Date.now(), type: frame.type, section: frame.section || null });
          } catch { /* Ignore frames outside the JSON revision protocol. */ }
        })));
      } });
      const second = await t.session('DEV-AAA', { baseURL: t.secondaryURL });
      await openTwinTab(page, 'Scheduled tasks');
      await openTwinTab(second.page, 'Scheduled tasks');
      const system = (await tasks(page)).items.find(task => task.kind === 'system_review');
      assert.ok(system, 'built-in system task is present');
      assert.deepEqual(system.schedule, { kind: 'interval', seconds: 14400 });
      const systemCard = card(page, system.id);
      assert.equal(await systemCard.getByRole('button', { name: `Delete ${system.title}`, exact: true }).count(), 0);
      let form = await edit(page, system);
      for (const label of ['Task name', 'Instructions', 'Time zone']) assert.equal(await form.getByLabel(label, { exact: true }).getAttribute('readonly'), '');
      assert.equal(await form.getByLabel('Schedule', { exact: true }).isDisabled(), true);
      await form.getByRole('button', { name: 'Cancel', exact: true }).click();
      await systemCard.getByRole('button', { name: `${system.enabled ? 'Pause' : 'Resume'} future runs for ${system.title}`, exact: true }).click();
      await taskState(page, system.id, item => item.enabled !== system.enabled);
      await systemCard.getByRole('button', { name: `${system.enabled ? 'Resume' : 'Pause'} future runs for ${system.title}`, exact: true }).click();
      await taskState(page, system.id, item => item.enabled === system.enabled);
      const requests = [], observationStarted = Date.now();
      page.on('request', request => { if (request.method() === 'GET') requests.push({ path: new URL(request.url()).pathname, at: Date.now() }); });
      await button(page, 'New task').click();
      form = dialog(page);
      const draft = 'Draft retained ' + t.unique('focus');
      await form.getByLabel('Task name', { exact: true }).fill(draft);
      const remote = await newTask(second.page, t.unique('remote-task'), 'Check the release checklist.', { kind: 'interval' });
      await card(page, remote.id).waitFor({ state: 'attached' });
      assert.equal(await form.getByLabel('Task name', { exact: true }).inputValue(), draft);
      assert.equal(await form.getByLabel('Task name', { exact: true }).evaluate(element => element === document.activeElement), true);
      await page.waitForTimeout(350); // Real observation window; no browser clock overrides.
      const refreshes = requests.filter(value => value.path === '/api/me/twin/scheduled-tasks').length;
      assert.ok(refreshes >= 1 && refreshes <= 3, 'owner event refresh is bounded and does not form a refetch loop');
      const workerFrames = frames.filter(frame => frame.at >= observationStarted && frame.type === 'worker_status');
      const profileRequests = requests.filter(value => value.path === '/api/me/twin');
      assert.equal(requests.filter(value => value.path === '/api/bootstrap').length, 0, 'schedule hint does not reload bootstrap');
      assert.ok(profileRequests.length <= workerFrames.length, 'profile refreshes cannot exceed independent worker status events');
      assert.ok(profileRequests.every(request => workerFrames.some(frame => request.at >= frame.at && request.at - frame.at <= 250)), 'every profile refresh follows an actual worker status frame within 250 ms');
      t.record('refresh_observation', { startedAt: new Date(observationStarted).toISOString(), frames: frames.filter(frame => frame.at >= observationStarted), requests, scheduleFetches: refreshes, profileFetches: profileRequests.length, independentWorkerStatusFrames: workerFrames.length, bootstrapFetches: 0, unaccountedProfileFetches: 0 });
      await form.getByRole('button', { name: 'Cancel', exact: true }).click();
      await remove(second.page, remote);
      await card(page, remote.id).waitFor({ state: 'hidden' });
      t.record('live_refresh', { systemId: system.id, systemIntervalSeconds: 14400, instructionsReadOnly: true, draftPreserved: true, focusPreserved: true, scheduleFetches: refreshes, profileFetches: profileRequests.length, independentWorkerStatusFrames: workerFrames.length, bootstrapFetches: 0, unaccountedProfileFetches: 0 });
    },
  },
  {
    name: 'scheduled tasks / run-now journal, pause versus active cancellation and visible failure',
    run: async t => {
      const { page } = await t.session();
      await openTwinTab(page, 'Scheduled tasks');
      const title = t.unique('schedule-lifecycle');
      const observer = await t.session('DEV-AAA', { baseURL: t.secondaryURL });
      const chat = await privateChat(observer.page);
      await observer.page.evaluate(() => {
        const audit = window.__scheduledStatusAudit = { typing: [], failures: [], queueSeen: false, liveSeen: false };
        const scan = () => {
          for (const row of document.querySelectorAll('.typing-row[data-request-id]')) if (!audit.typing.includes(row.dataset.requestId)) audit.typing.push(row.dataset.requestId);
          for (const row of document.querySelectorAll('[data-failed-request]')) if (!audit.failures.includes(row.dataset.failedRequest)) audit.failures.push(row.dataset.failedRequest);
          audit.queueSeen ||= !!document.querySelector('.owner-queue');
          audit.liveSeen ||= !!document.querySelector('.live-message');
        };
        scan();
        window.__scheduledStatusObserver = new MutationObserver(scan);
        window.__scheduledStatusObserver.observe(document.body, { childList: true, subtree: true, attributes: true });
      });
      let task = await newTask(page, title, 'E2E SLOW ' + t.unique('scheduled-stop'), { kind: 'interval' });
      await card(page, task.id).getByRole('button', { name: `Run now ${title}`, exact: true }).click();
      const active = await taskState(page, task.id, item => !!item.active_request_id);
      assert.ok(['queued', 'running'].includes(active.last_status));
      await poll(async () => (await api(observer.page, '/chats/' + chat.id)).requests.some(request => request.id === active.active_request_id && request.quiet_background === true), 'background request remains in authorized API with quiet status metadata');
      await card(page, task.id).getByRole('button', { name: `Pause future runs for ${title}`, exact: true }).click();
      const paused = await taskState(page, task.id, item => !item.enabled);
      assert.equal(paused.active_request_id, active.active_request_id, 'pausing future occurrences does not cancel the current request');
      await card(page, task.id).getByRole('button', { name: `Stop active run for ${title}`, exact: true }).click();
      const cancelled = await taskState(page, task.id, item => item.last_status === 'cancelled' && !item.active_request_id);
      assert.equal(cancelled.enabled, false);
      const cancelledHistory = await history(page, task.id);
      assert.equal(cancelledHistory.items.find(run => run.request_id === active.active_request_id).status, 'cancelled');
      let form = await edit(page, task);
      await form.getByLabel('Instructions', { exact: true }).fill('E2E FAIL_ONCE ' + t.unique('scheduled-failure'));
      await save(page);
      await card(page, task.id).getByRole('button', { name: `Run now ${title}`, exact: true }).click();
      const failed = await taskState(page, task.id, item => item.last_status === 'failed');
      assert.ok(failed.last_error, 'real runtime failure appears on task');
      await card(page, task.id).getByRole('alert').waitFor();
      await card(page, task.id).getByRole('button', { name: `Run history for ${title}`, exact: true }).click();
      await dialog(page).locator(`[data-request-id="${failed.last_request_id}"]`).waitFor();
      assert.ok((await dialog(page).innerText()).includes(failed.last_error));
      await page.screenshot({ path: path.join(t.artifactDir, 'scheduled-task-history-failure.png'), fullPage: true });
      await dialog(page).getByRole('button', { name: 'Close', exact: true }).click();
      form = await edit(page, task);
      await form.getByLabel('Instructions', { exact: true }).fill('E2E ECHO ' + t.unique('scheduled-complete'));
      await save(page);
      await card(page, task.id).getByRole('button', { name: `Run now ${title}`, exact: true }).click();
      const completed = await taskState(page, task.id, item => item.last_status === 'completed');
      assert.equal(completed.enabled, false, 'manual run does not resume a paused schedule');
      // A completed scheduled root waiting for owner approval must not expose a bogus
      // Stop button. The real approval card remains usable in the ordinary chat UI.
      const approvalLabel = t.unique('scheduled-approval');
      form = await edit(page, task);
      await form.getByLabel('Instructions', { exact: true }).fill('E2E APPROVAL ' + approvalLabel);
      await save(page);
      await card(page, task.id).getByRole('button', { name: `Run now ${title}`, exact: true }).click();
      const waiting = await taskState(page, task.id, item => item.pending_work && !item.active_request_id && item.last_status === 'completed');
      await card(page, task.id).getByText('Waiting for pending work', { exact: true }).waitFor();
      assert.equal(await card(page, task.id).getByRole('button', { name: `Stop active run for ${title}`, exact: true }).count(), 0);
      assert.equal(await card(page, task.id).getByRole('button', { name: `Run now ${title}`, exact: true }).isDisabled(), true);
      assert.equal(await card(page, task.id).getByRole('button', { name: `Delete ${title}`, exact: true }).isDisabled(), true);
      await observer.page.locator('.approval-tabs button[title="E2E approval ' + approvalLabel + '"]').click();
      await observer.page.locator('.approval-card').filter({ hasText: approvalLabel }).getByRole('button', { name: 'Reject once', exact: true }).click();
      await taskState(page, task.id, item => !item.pending_work && !item.active_request_id);
      const journal = (await history(page, task.id)).items;
      assert.ok(journal.some(run => run.status === 'completed' && run.run_id));
      assert.ok(journal.some(run => run.status === 'failed' && run.error));
      assert.ok(journal.some(run => run.status === 'cancelled'));
      const state = await api(observer.page, '/chats/' + chat.id);
      const quietRequests = state.requests.filter(request => journal.some(run => run.request_id === request.id) || request.id === waiting.last_request_id);
      assert.ok(quietRequests.some(request => request.id === failed.last_request_id), 'failed scheduled root stays in authorized request data');
      assert.ok(quietRequests.every(request => request.quiet_background === true));
      const audit = await observer.page.evaluate(() => { window.__scheduledStatusObserver.disconnect(); return window.__scheduledStatusAudit; });
      const ids = new Set(journal.map(run => run.request_id));
      assert.deepEqual(audit.typing.filter(id => ids.has(id)), [], 'no transient preparing/thinking banners for scheduled work');
      assert.deepEqual(audit.failures.filter(id => ids.has(id)), [], 'scheduled failures remain in task UI, without chat failure banners');
      assert.equal(audit.queueSeen, false, 'background work does not add owner queue chips');
      assert.equal(audit.liveSeen, false, 'background work does not stream a transient chat bubble');
      const activity = await api(observer.page, '/chats/' + chat.id + '/activity');
      assert.ok(activity.items.some(run => journal.some(item => item.run_id && item.run_id === run.run_id)), 'scheduled SDK runs remain inspectable in Activity');
      await remove(page, task);
      t.record('quiet_background', { metadataConfirmed: true, transientTypingRequestIds: audit.typing.filter(id => ids.has(id)), failureBannerRequestIds: audit.failures.filter(id => ids.has(id)), ownerQueueSeen: audit.queueSeen, liveOutputSeen: audit.liveSeen, approvalsRetained: true, waitingWithoutBogusStop: true, activityRetained: true });
      t.record('offline_lifecycle', { model: 'deterministic-offline', assertedModelReasoning: false, taskId: task.id, requestIds: journal.map(run => run.request_id), statuses: journal.map(run => run.status), pauseKeptActiveRequest: true, stoppedViaExistingCancelEndpoint: true, failureVisible: true });
    },
  },
];
module.exports = { cases };
