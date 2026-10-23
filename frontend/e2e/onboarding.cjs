/** Dedicated fresh-instance onboarding scenario. Real UI/API/PG/Redis; no welcome
 * bubbles, API responses, sockets, clocks or model decisions are fabricated here. */
const { assert, api, login, logout, openTwinTab, privateChat, chooseChat, sendMessage, poll } = require('./helpers.cjs');
const path = require('node:path');
const button = (page, name) => page.getByRole('button', { name, exact: true });
const state = page => api(page, '/me/twin/onboarding');
const detail = (page, id) => api(page, '/chats/' + encodeURIComponent(id));
async function status(page, expected) {
  return poll(async () => { const value = await state(page); return value.status === expected ? value : false; }, 'persisted onboarding status ' + expected);
}
async function welcome(page) {
  const value = await status(page, 'active');
  assert.equal(value.enabled, true);
  assert.ok(value.welcome_message_id);
  const chat = await detail(page, value.chat_id);
  const message = chat.messages.find(item => item.id === value.welcome_message_id);
  assert.ok(message, 'welcome is persisted in canonical chat history');
  assert.equal(message.kind, 'system');
  assert.ok(message.body.length > 0 && message.body.length <= 600, 'opening stays short');
  assert.ok((message.body.match(/\?/g) || []).length <= 1, 'opening asks at most one optional question');
  const bootstrap = await api(page, '/bootstrap');
  const ownTwin = bootstrap.actors.find(actor => actor.kind === 'twin' && actor.owner_id === bootstrap.current_owner_id);
  assert.equal(message.author_id, ownTwin.id);
  await page.locator(`[data-message-id="${message.id}"]`).waitFor();
  return { state: value, message, chat };
}
async function resetPrivate(page) {
  await openTwinTab(page, 'Profile');
  await button(page, 'Reset').click();
  await page.locator('.modal[role="dialog"]').getByRole('button', { name: 'Confirm reset', exact: true }).click();
  await page.locator('.modal[role="dialog"]').waitFor({ state: 'hidden' });
  await button(page, 'Back to chats').click();
  await page.locator('.composer textarea').waitFor();
}
const cases = [
  {
    name: 'onboarding / persisted welcome is unique across reload and APIs, skip stays optional and groups stay local',
    run: async t => {
      assert.equal(process.env.TWINTEAMS_E2E_SCENARIO, 'personal-onboarding', 'fresh onboarding scenario is required');
      const { page } = await t.session();
      const input = page.locator('.composer textarea');
      await input.waitFor();
      const draft = t.unique('optional-draft');
      await input.fill(draft);
      const first = await welcome(page);
      assert.equal(await input.inputValue(), draft, 'introduction does not replace a natural draft');
      assert.equal(await input.evaluate(element => element === document.activeElement), true, 'introduction does not steal composer focus');
      assert.equal(await page.locator('[aria-modal="true"]').count(), 0, 'no questionnaire or naming gate');
      assert.equal((await api(page, '/chats/' + first.state.chat_id + '/activity')).items.length, 0, 'persisting the opening does not create an SDK run');
      assert.equal(first.chat.requests.length, 0, 'opening does not enqueue a model reply');
      await page.screenshot({ path: path.join(t.artifactDir, 'onboarding-persisted-welcome.png'), fullPage: true });
      await input.fill('');
      await page.reload();
      const reloaded = await welcome(page);
      assert.equal(reloaded.state.welcome_message_id, first.message.id);
      assert.equal(reloaded.chat.messages.filter(message => message.kind === 'system').length, 1);
      const second = await t.session('DEV-AAA', { baseURL: t.secondaryURL });
      assert.equal((await welcome(second.page)).message.id, first.message.id, 'concurrent API replica reuses the persisted opening');
      await input.fill(draft);
      await second.page.getByRole('button', { name: 'Skip introduction', exact: true }).click();
      await status(page, 'skipped');
      await page.getByRole('button', { name: 'Skip introduction', exact: true }).waitFor({ state: 'hidden' });
      assert.equal(await input.inputValue(), draft);
      assert.equal(await input.evaluate(element => element === document.activeElement), true, 'remote skip does not steal composer focus');
      await page.reload();
      assert.equal((await status(page, 'skipped')).welcome_message_id, first.message.id);
      await page.locator(`[data-message-id="${first.message.id}"]`).waitFor();
      assert.equal(await page.getByRole('button', { name: 'Skip introduction', exact: true }).count(), 0);
      const bootstrap = await api(page, '/bootstrap');
      const group = bootstrap.chats.find(chat => chat.kind === 'group');
      assert.ok(group);
      const groupBefore = await detail(page, group.id);
      const starts = [];
      page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/me/twin/onboarding/start') starts.push(request.url()); });
      await chooseChat(page, group.title);
      assert.equal(await page.locator('.twin-onboarding').count(), 0, 'guidance is never mounted in a group');
      const groupAfter = await detail(page, group.id);
      assert.deepEqual(groupAfter.messages.map(message => message.id), groupBefore.messages.map(message => message.id));
      assert.ok(!groupAfter.messages.some(message => message.id === first.message.id));
      assert.equal(starts.length, 0, 'viewing the group never starts private onboarding');
      const foreign = await page.request.get(new URL('/api/chats/private-maya', page.url()).href);
      assert.ok([403, 404].includes(foreign.status()), 'another owner private history remains unavailable');
      await page.screenshot({ path: path.join(t.artifactDir, 'onboarding-group-scope.png'), fullPage: true });
      t.record('persisted_opening', { ownerId: bootstrap.current_owner_id, chatId: first.state.chat_id, messageId: first.message.id, kind: first.message.kind, systemMessagesAfterReload: reloaded.chat.messages.filter(message => message.kind === 'system').length, activityRuns: 0, skippedAcrossApis: true, draftAndFocusPreserved: true, groupStartRequests: starts.length, foreignPrivateStatus: foreign.status() });
    },
  },
  {
    name: 'onboarding / actual task input bypasses the introduction and account switch and reset preserve owner scope',
    run: async t => {
      const { page } = await t.session('DEV-BBB');
      const first = await welcome(page);
      const task = 'Help me draft a concise project update for ' + t.unique('first-task') + '.';
      await sendMessage(page, task);
      const accepted = await poll(async () => (await detail(page, first.state.chat_id)).messages.find(message => message.body === task && message.kind === 'human'), 'natural first task accepted through the usual composer');
      assert.equal(await page.locator('[aria-modal="true"]').count(), 0);
      assert.equal(await page.locator('.composer textarea').isEnabled(), true);
      // The offline adapter does not pretend to choose finish_onboarding. The
      // server's ordinary second-input fallback closes this brief first meeting.
      const followup = 'Use a clear, professional tone for ' + t.unique('followup') + '.';
      await sendMessage(page, followup);
      await status(page, 'completed');
      await page.getByRole('button', { name: 'Skip introduction', exact: true }).waitFor({ state: 'hidden' });
      const privateState = await detail(page, first.state.chat_id);
      assert.equal(privateState.messages.filter(message => message.kind === 'system').length, 1);
      assert.equal(privateState.messages.filter(message => message.body === task && message.kind === 'human').length, 1);
      await logout(page);
      await login(page, 'DEV-AAA');
      const own = await privateChat(page);
      const switched = await status(page, 'skipped');
      assert.equal(switched.chat_id, own.id);
      assert.notEqual(switched.chat_id, first.state.chat_id);
      assert.notEqual(switched.welcome_message_id, first.message.id);
      assert.equal(await page.locator(`[data-message-id="${first.message.id}"]`).count(), 0, 'previous account opening cannot leak into the next owner view');
      const priorWelcome = switched.welcome_message_id;
      await resetPrivate(page);
      const afterReset = await status(page, 'skipped');
      const resetChat = await poll(async () => { const value = await detail(page, own.id); return value.messages.length === 0 ? value : false; }, 'reset private history stays empty without a new introduction');
      assert.equal(afterReset.welcome_message_id, priorWelcome, 'one-time onboarding progress survives content reset');
      assert.equal(resetChat.messages.length, 0);
      assert.equal(await page.locator('.twin-onboarding').count(), 0);
      t.record('task_first', { ownerChatId: first.state.chat_id, welcomeMessageId: first.message.id, acceptedTaskMessageId: accepted.id, naturalInputCount: 2, completion: 'server-second-input-fallback', modelToolChoiceAsserted: false, ownerSwitchIsolated: true, resetPreservedStatus: afterReset.status, resetMessageCount: resetChat.messages.length });
    },
  },
  {
    name: 'onboarding / existing private conversation is preserved without enrollment',
    run: async t => {
      const { page } = await t.session(null);
      // Enter an existing non-chat view before login, then create genuine prior
      // history through the product API. No welcome/message JSON is intercepted.
      await page.goto(t.baseURL + '/#/my-twin/profile');
      await login(page, 'DEV-CCC');
      await button(page, 'Back to chats').waitFor();
      const initial = await state(page);
      assert.equal(initial.enabled, true);
      assert.equal(initial.status, 'new');
      assert.equal(initial.welcome_message_id, null);
      const body = 'Existing project context ' + t.unique('prior-history') + '.';
      const response = await page.request.post(new URL('/api/chats/' + initial.chat_id + '/messages', page.url()).href, { data: { body, client_request_key: t.unique('prior-send') } });
      assert.equal(response.status(), 200);
      const prior = await poll(async () => (await detail(page, initial.chat_id)).messages.find(message => message.body === body && message.kind === 'human'), 'prior conversation persisted by the real send API');
      await button(page, 'Back to chats').click();
      await page.locator(`[data-message-id="${prior.id}"]`).waitFor();
      const unavailable = await status(page, 'unavailable');
      assert.equal(unavailable.welcome_message_id, null);
      assert.equal(await page.locator('.twin-onboarding').count(), 0);
      await page.reload();
      await page.locator(`[data-message-id="${prior.id}"]`).waitFor();
      const existing = await detail(page, initial.chat_id);
      assert.equal(existing.messages.filter(message => message.kind === 'system').length, 0, 'opening is not inserted into an existing conversation');
      assert.equal(existing.messages.filter(message => message.id === prior.id && message.body === body).length, 1);
      t.record('existing_history', { chatId: initial.chat_id, fixtureSetup: 'real-product-send-before-first-chat-view', priorMessageId: prior.id, status: unavailable.status, welcomeMessageId: unavailable.welcome_message_id, priorMessagePreserved: true, systemMessageCount: 0 });
    },
  },
];
module.exports = { cases };
