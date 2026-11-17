/** Snapshot sharing through the actual browser/API/worker/SDK; no HTTP/WS interception. */
const { assert, api, chooseChat, login, poll, sendMessage } = require('./helpers.cjs');

function responseFor(page, path, method) {
  return page.waitForResponse(response => new URL(response.url()).pathname === `/api${path}` && response.request().method() === method);
}

async function createBot(page, name, assignment) {
  await page.getByRole('button', { name: 'Create BOT', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Create BOT', exact: true });
  await dialog.getByLabel('BOT name', { exact: true }).fill(name);
  await dialog.getByLabel('Assignment', { exact: true }).fill(assignment);
  const saved = responseFor(page, '/bots', 'POST');
  await dialog.getByRole('button', { name: 'Create BOT', exact: true }).click();
  const response = await saved; assert.equal(response.status(), 200); const bot = await response.json();
  await page.locator('.chat-header h2').filter({ hasText: name }).waitFor();
  return bot;
}

async function openShare(page, name) {
  const row = page.locator('.chat-list-row').filter({ has: page.locator('.chat-name').filter({ hasText: name }) });
  await row.getByRole('button', { name: 'Chat options', exact: true }).click();
  await page.getByRole('menuitem', { name: 'Share BOT', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Share BOT', exact: true });
  await dialog.getByRole('button', { name: 'Share my current version', exact: true }).waitFor();
  return dialog;
}

async function editBot(page, name, assignment) {
  await page.getByRole('button', { name: 'Edit BOT', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit BOT', exact: true });
  await dialog.getByLabel('BOT name', { exact: true }).fill(name);
  await dialog.getByLabel('Assignment', { exact: true }).fill(assignment);
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  await page.locator('.chat-header h2').filter({ hasText: name }).waitFor();
}

async function echo(page, bot, label) {
  await sendMessage(page, `E2E ECHO ${label}`);
  const message = await poll(async () => {
    const detail = await api(page, `/chats/${bot.chat_id}`);
    const failure = detail.requests.find(request => request.status === 'failed');
    if (failure) throw new Error(`BOT execution failed: ${failure.error}`);
    return detail.messages.find(message => message.author_id === bot.id && message.body === `E2E echo: ${label}`);
  }, 'installed BOT output from real SDK execution', { timeout: 45000 });
  await page.locator(`article[data-message-id="${message.id}"]`).waitFor();
  return message.id;
}

const cases = [{
  name: 'bot sharing / snapshot install, independent copies, original-node re-share and token revocation',
  sensitive: true, // Share links are bearer capabilities: omit traces and screenshots.
  timeout: 240000,
  run: async t => {
    const tokens = [];
    try {
      const source = await t.session('DEV-AAA');
      const name = t.unique('Shared-research'); const assignment = 'Research assigned questions and provide evidence.';
      const bot = await createBot(source.page, name, assignment);
      const sourceMessageId = await echo(source.page, bot, t.unique('source-before-share'));
      let dialog = await openShare(source.page, name);
      await dialog.getByRole('heading', { name: 'No snapshots created yet', exact: true }).waitFor();
      const frozenResponse = responseFor(source.page, `/bots/${bot.id}/shares`, 'POST');
      await dialog.getByRole('button', { name: 'Share my current version', exact: true }).click();
      const frozenHTTP = await frozenResponse; assert.equal(frozenHTTP.status(), 200);
      const frozen = await frozenHTTP.json(); tokens.push(frozen.token.token);
      const input = dialog.getByLabel('Share link', { exact: true }); await input.waitFor();
      assert.ok(new URL(await input.inputValue()).hash === `#/bots/share/${frozen.token.token}`, 'Share UI exposes the generated hash link');
      await dialog.getByRole('button', { name: 'Close', exact: true }).click();
      const sourceName = t.unique('Source-evolved'); const sourceAssignment = 'Source copy: review source material for completeness.';
      await editBot(source.page, sourceName, sourceAssignment);
      t.record('freeze', { snapshotId: frozen.snapshot.id, sourceBotId: bot.id, sourceMessageId, originalAssignmentFrozen: true });

      const recipient = await t.session(null, { baseURL: t.secondaryURL });
      await recipient.page.evaluate(token => { location.hash = `/bots/share/${token}`; }, frozen.token.token);
      const recipientAuth = await login(recipient.page, 'DEV-BBB');
      await recipient.page.getByRole('heading', { name, exact: true }).waitFor();
      assert.ok(new URL(recipient.page.url()).hash.startsWith('#/bots/share/'), 'Login returns to the share preview');
      const installedResponse = responseFor(recipient.page, '/bot-shares/install', 'POST');
      await recipient.page.getByRole('button', { name: 'Add to my BOTs', exact: true }).click();
      const installedHTTP = await installedResponse; assert.equal(installedHTTP.status(), 200);
      const installed = await installedHTTP.json();
      assert.notEqual(installed.id, bot.id); assert.equal(installed.actor.owner_id, recipientAuth.current_owner_id);
      assert.equal(installed.installed_snapshot_id, frozen.snapshot.id); assert.equal(installed.assignment, assignment);
      await recipient.page.locator('.chat-header h2').filter({ hasText: name }).waitFor();
      await recipient.page.locator('.conversation-list .chat-name').filter({ hasText: name }).waitFor();
      assert.deepEqual((await api(recipient.page, `/chats/${installed.chat_id}`)).messages, [], 'Install excludes the source transcript');
      const installedMessageId = await echo(recipient.page, installed, t.unique('installed-runtime'));

      await api(source.page, `/chats/${installed.chat_id}`, { status: 403 });
      await api(source.page, `/bots/${installed.id}/history`, { status: 404 });
      await api(recipient.page, `/chats/${bot.chat_id}`, { status: 403 });
      await api(recipient.page, `/bots/${bot.id}/history`, { status: 404 });
      const recipientName = t.unique('Recipient-evolved'); const recipientAssignment = 'Recipient copy: prioritize concise independent reviews.';
      await editBot(recipient.page, recipientName, recipientAssignment);
      const sourceNow = (await api(source.page, '/bots')).find(item => item.id === bot.id);
      assert.equal(sourceNow.name, sourceName); assert.equal(sourceNow.assignment, sourceAssignment);
      const recipientNow = (await api(recipient.page, '/bots')).find(item => item.id === installed.id);
      assert.equal(recipientNow.name, recipientName); assert.equal(recipientNow.assignment, recipientAssignment);

      dialog = await openShare(recipient.page, recipientName);
      await dialog.getByRole('heading', { name: 'Originally installed version', exact: true }).waitFor();
      const originalResponse = responseFor(recipient.page, `/bots/${installed.id}/shares/${frozen.snapshot.id}/tokens`, 'POST');
      await dialog.getByRole('button', { name: 'Share original snapshot', exact: true }).click();
      const originalHTTP = await originalResponse; assert.equal(originalHTTP.status(), 200);
      const originalLink = await originalHTTP.json(); tokens.push(originalLink.token);
      const originalPreview = await recipient.page.request.post('/api/bot-shares/preview', { data: { token: originalLink.token } });
      assert.equal(originalPreview.status(), 200); const originalNode = (await originalPreview.json()).snapshot;
      assert.equal(originalNode.id, frozen.snapshot.id); assert.equal(originalNode.assignment, assignment);
      const evolvedResponse = responseFor(recipient.page, `/bots/${installed.id}/shares`, 'POST');
      await dialog.getByRole('button', { name: 'Share my current version', exact: true }).click();
      const evolvedHTTP = await evolvedResponse; assert.equal(evolvedHTTP.status(), 200);
      const evolved = await evolvedHTTP.json(); tokens.push(evolved.token.token);
      assert.equal(evolved.snapshot.parent_snapshot_id, frozen.snapshot.id); assert.equal(evolved.snapshot.assignment, recipientAssignment);
      assert.notEqual(evolved.snapshot.id, frozen.snapshot.id);
      await dialog.getByRole('button', { name: 'Close', exact: true }).click();
      t.record('installAndLineage', { recipientBotId: installed.id, installedMessageId, evolvedSnapshotId: evolved.snapshot.id, parentSnapshotId: frozen.snapshot.id, originalNodeReshared: true, ownerReadsIsolated: true, transcriptExcluded: true });

      dialog = await openShare(source.page, sourceName);
      const revokedResponse = responseFor(source.page, `/bots/${bot.id}/shares/tokens/${frozen.token.id}`, 'DELETE');
      await dialog.getByRole('button', { name: 'Revoke link', exact: true }).click();
      assert.equal((await revokedResponse).status(), 200);
      await dialog.getByRole('status').filter({ hasText: 'Already installed BOTs are unchanged.' }).waitFor();
      await dialog.getByRole('button', { name: 'Close', exact: true }).click();
      const third = await t.session('DEV-CCC');
      await third.page.evaluate(token => { location.hash = `/bots/share/${token}`; }, frozen.token.token);
      await third.page.getByRole('heading', { name: 'Share link unavailable', exact: true }).waitFor();
      assert.equal(await third.page.getByRole('button', { name: 'Add to my BOTs', exact: true }).count(), 0);
      const rejected = await third.page.request.post('/api/bot-shares/install', { data: { token: frozen.token.token } });
      assert.equal(rejected.status(), 404, 'Revoked source link blocks a new owner install');
      assert.ok(!(await api(third.page, '/bots')).some(item => item.installed_snapshot_id === frozen.snapshot.id));
      const survivingPreview = await third.page.request.post('/api/bot-shares/preview', { data: { token: originalLink.token } });
      assert.equal(survivingPreview.status(), 200, 'An independent token for the original node stays active');
      await recipient.page.evaluate(token => { location.hash = `/bots/share/${token}`; }, frozen.token.token);
      await recipient.page.getByRole('button', { name: 'Open my BOT', exact: true }).click();
      await recipient.page.locator('.chat-header h2').filter({ hasText: recipientName }).waitFor();
      await recipient.page.reload(); await chooseChat(recipient.page, recipientName);
      const afterRevokeMessageId = await echo(recipient.page, installed, t.unique('installed-after-revoke'));
      const afterRevoke = (await api(recipient.page, '/bots')).find(item => item.id === installed.id);
      assert.equal(afterRevoke.assignment, recipientAssignment);
      const sourceDetail = await api(source.page, `/chats/${bot.chat_id}`);
      assert.ok(!sourceDetail.messages.some(message => [installedMessageId, afterRevokeMessageId].includes(message.id)));
      t.record('revocation', { revokedTokenId: frozen.token.id, newInstallStatus: rejected.status(), installedCopyStillRunnable: true, afterRevokeMessageId, sourceAndRecipientTranscriptsSeparate: true, businessApiInterception: false, websocketInterception: false, modelMode: 'deterministic-offline' });
    } catch (error) {
      let detail = String(error.stack || error);
      for (const token of tokens) detail = detail.replaceAll(token, '[share-token]');
      const sanitized = new Error('BOT sharing browser scenario failed'); sanitized.stack = detail; throw sanitized;
    }
  },
}];
module.exports = { cases };
