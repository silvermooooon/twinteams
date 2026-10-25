/** Offline-only classifier failure through the actual SDK and normal UI retry.
 * No provider call, business interception or fabricated accepted reply request.
 */
const { assert, api, poll, sendMessage, chooseChat, openTwinTab } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

function inspect() {
  const value = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [
    path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'),
    'inspect', '--state', process.env.TWINTEAMS_E2E_STATE,
  ], { encoding: 'utf8' }));
  assert.equal(value.browser_e2e, true);
  assert.equal(value.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  assert.equal(value.live_budget, null, 'this failure case must not have a live provider budget');
  return value;
}

async function detail(page, chat) { return api(page, '/chats/' + chat.id + '?limit=100'); }

const cases = [{
  name: 'routing recovery / offline SDK classifier failure is visible, dismissible and retried once through source-owner UI',
  timeout: 150000,
  retainTrace: true,
  run: async t => {
    assert.equal(process.env.TWINTEAMS_E2E_MODEL_MODE, 'deterministic-offline');
    assert.equal(process.env.TWINTEAMS_E2E_SCENARIO, 'group-route-recovery');
    const sender = await t.session('DEV-AAA');
    const peer = await t.session('DEV-BBB', { baseURL: t.secondaryURL });
    await openTwinTab(peer.page, 'Profile');
    await sender.page.getByRole('button', { name: 'New chat', exact: true }).click();
    await sender.page.locator('.people-picker-inline').getByRole('combobox', { name: 'Search people', exact: true }).fill('BBB');
    await sender.page.getByRole('option', { name: 'BBB', exact: true }).click();
    await sender.page.getByRole('button', { name: 'Start chat', exact: true }).click();
    await sender.page.locator('.new-chat-main').waitFor({ state: 'hidden' });
    const chat = await poll(async () => (await api(sender.page, '/bootstrap')).chats.find(row =>
      row.kind === 'group' && row.member_owner_ids.length === 2 && row.member_owner_ids.includes('owner-maya')), 'UI-created failure test chat');
    const sendFailure = async label => {
      const body = '@BBB E2E ROUTE_FAIL_ONCE ' + label;
      const row = await sendMessage(sender.page, body);
      const id = await row.getAttribute('data-message-id');
      const failure = await poll(async () => (await detail(sender.page, chat)).messages.find(message => message.id === id)?.routing_failure,
        'actual SDK routing failure projected on the source message');
      assert.equal(failure.can_retry, true);
      assert.equal(failure.command_id, id, 'initial routing command uses the source identity');
      assert.equal((await detail(sender.page, chat)).requests.filter(request => request.source_message_id === id).length, 0);
      const evidence = inspect();
      assert.equal(evidence.automatic_reply_evidence.routes.find(route => route.id === failure.id).status, 'failed');
      const command = evidence.automatic_reply_evidence.sdk_commands.find(command => command.command_id === id);
      assert.ok(command, 'the classifier really executed through a durable SDK command');
      assert.equal(evidence.automatic_reply_evidence.sdk_runs.find(run => run.run_id === command.run_id && run.session_id === command.session_id).status, 'failed');
      return { id, body, failure, command };
    };

    const dismissed = await sendFailure(t.unique('dismiss-route'));
    const dismissNotice = sender.page.locator(`[data-failed-request="${dismissed.failure.id}"]`);
    await dismissNotice.getByText('Automatic reply could not start', { exact: false }).waitFor();
    await peer.page.getByRole('button', { name: 'Back to chats', exact: true }).click();
    const peerChat = await poll(async () => (await api(peer.page, '/bootstrap')).chats.find(row => row.id === chat.id), 'peer receives failure chat');
    await chooseChat(peer.page, peerChat.title);
    const peerNotice = peer.page.locator(`[data-failed-request="${dismissed.failure.id}"]`);
    await peerNotice.waitFor();
    assert.equal(await peerNotice.getByRole('button', { name: 'Retry', exact: true }).count(), 0, 'peer sees failure without source-owner retry control');
    assert.equal((await detail(peer.page, chat)).messages.find(row => row.id === dismissed.id).routing_failure.can_retry, false);
    await sender.page.bringToFront();
    await dismissNotice.getByRole('button', { name: 'Dismiss', exact: true }).click();
    await sender.page.reload(); await chooseChat(sender.page, chat.title);
    assert.equal(await sender.page.locator(`[data-failed-request="${dismissed.failure.id}"]`).count(), 0, 'normal dismissal survives browser reload');
    assert.ok((await detail(sender.page, chat)).messages.find(row => row.id === dismissed.id).routing_failure, 'dismissal preserves failed durable state');
    assert.equal(await peerNotice.count(), 1, 'sender local dismissal does not hide another browser notice');
    await openTwinTab(peer.page, 'Profile');

    const recovered = await sendFailure(t.unique('retry-route'));
    const retryNotice = sender.page.locator(`[data-failed-request="${recovered.failure.id}"]`);
    await retryNotice.waitFor();
    await sender.page.screenshot({ path: path.join(t.artifactDir, 'routing-failure-real-sdk-notice.png'), fullPage: true });
    const [response] = await Promise.all([
      sender.page.waitForResponse(response => response.request().method() === 'POST' &&
        new URL(response.url()).pathname === `/api/chats/${chat.id}/routing-decisions/${recovered.failure.id}/retry`),
      retryNotice.getByRole('button', { name: 'Retry', exact: true }).click(),
    ]);
    assert.equal(response.status(), 200);
    const submitted = response.request().postDataJSON(), retried = await response.json();
    assert.equal(submitted.expected_command_id, recovered.failure.command_id);
    assert.ok(submitted.client_request_key);
    assert.notEqual(retried.command_id, recovered.failure.command_id, 'explicit retry accepts a fresh durable classifier command');
    const settled = await poll(async () => {
      const state = await detail(sender.page, chat);
      // Chat detail intentionally lists only active/failed requests. Terminal
      // completion is verified by the guarded read-only durable inspector.
      const requests = inspect().requests.filter(request => request.source_message_id === recovered.id);
      if (requests.some(request => request.status === 'failed')) throw new Error('Recovered classifier created a failed reply request');
      const outputs = requests.length === 1 ? state.messages.filter(message => message.request_id === requests[0].id) : [];
      return requests.length === 1 && requests[0].status === 'completed' && outputs.length === 1 &&
        outputs[0].body.startsWith('E2E route recovered: ') && { ...state, recoveredRequests: requests };
    }, 'retry runs actual classifier and exactly one SDK reply', { timeout: 90000 });
    const outputs = settled.messages.filter(message => message.request_id === settled.recoveredRequests[0].id);
    assert.equal(outputs.length, 1);
    assert.equal(outputs[0].author_id, 'twin-maya');
    assert.ok(outputs[0].body.startsWith('E2E route recovered: '));
    assert.equal(settled.messages.filter(message => message.body === recovered.body).length, 1, 'Retry preserves a single canonical human input');
    await sender.page.reload(); await chooseChat(sender.page, chat.title);
    assert.equal(await sender.page.locator(`[data-failed-request="${recovered.failure.id}"]`).count(), 0, 'completed retry failure does not return after reload');
    const final = inspect(), auto = final.automatic_reply_evidence;
    const route = auto.routes.find(row => row.id === recovered.failure.id);
    assert.equal(route.status, 'completed'); assert.equal(route.command_id, retried.command_id);
    const command = auto.sdk_commands.find(row => row.command_id === retried.command_id);
    assert.ok(command);
    assert.equal(auto.sdk_runs.find(run => run.run_id === command.run_id && run.session_id === command.session_id).status, 'completed');
    assert.equal(final.requests.filter(request => request.source_message_id === dismissed.id).length, 0);
    assert.equal(final.requests.filter(request => request.source_message_id === recovered.id).length, 1);
    await sender.page.screenshot({ path: path.join(t.artifactDir, 'routing-retry-real-sdk-success.png'), fullPage: true });
    t.record('routing_recovery', { syntheticProvider: true, realSdkCommands: true, realBrowserUi: true,
      providerAttempts: 0, businessInterception: false, dismissed, recovered,
      retryCommand: retried.command_id, submitted, route, output: outputs[0],
      sourceOwnerRetryOnly: true, reloadVerified: true });
  },
}];
module.exports = { cases };
