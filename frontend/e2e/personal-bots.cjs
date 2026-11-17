/** Durable personal BOTs through the real UI/API/worker/SDK, using the offline provider. */
const { assert, api, privateChat, sendMessage, poll } = require('./helpers.cjs');
const path = require('node:path');

async function createBot(page, name, assignment) {
  await page.getByRole('button', { name: 'Create BOT', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Create BOT', exact: true });
  await dialog.getByLabel('BOT name', { exact: true }).fill(name);
  await dialog.getByLabel('Assignment', { exact: true }).fill(assignment);
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bots' && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Create BOT', exact: true }).click();
  const response = await saved; assert.equal(response.status(), 200); const bot = await response.json();
  await page.locator('.chat-header h2').filter({ hasText: name }).waitFor();
  return bot;
}

async function echo(page, chatId, label, actorId) {
  const message = await poll(async () => {
    const detail = await api(page, `/chats/${chatId}`);
    const failure = detail.requests.find(request => request.status === 'failed');
    if (failure) throw new Error(`BOT execution failed: ${failure.error}`);
    return detail.messages.find(message => message.author_id === actorId && message.body === `E2E echo: ${label}`);
  }, 'BOT output from actual SDK execution', { timeout: 45000 });
  await page.locator(`article[data-message-id="${message.id}"]`).waitFor();
  return message;
}

const cases = [{
  name: 'personal bots / owner chats, supervisor handoffs, peer messages, shared room and transcript inspection',
  timeout: 240000,
  run: async t => {
    const { page } = await t.session(); const mainChat = await privateChat(page);
    const first = await createBot(page, t.unique('Research'), 'Research the tasks assigned by the owner or main Duet.');
    const direct = t.unique('bot-direct'); await sendMessage(page, `E2E ECHO ${direct}`); const directResult = await echo(page, first.chat_id, direct, first.id);
    assert.equal(await page.getByRole('button', { name: 'Add members', exact: true }).count(), 0, 'BOT conversation has no human invitation control');
    await page.getByRole('button', { name: 'Edit BOT', exact: true }).click();
    const edit = page.getByRole('dialog', { name: 'Edit BOT', exact: true });
    const renamed = t.unique('Research-edited'); await edit.getByLabel('BOT name', { exact: true }).fill(renamed);
    await edit.getByLabel('Assignment', { exact: true }).fill('Research assigned tasks and state the evidence.');
    await edit.getByRole('button', { name: 'Save', exact: true }).click();
    await page.locator('.chat-header h2').filter({ hasText: renamed }).waitFor();
    const second = await createBot(page, t.unique('Review'), 'Review research and report gaps.');
    const bootstrap = await api(page, '/bootstrap'); const main = bootstrap.actors.find(actor => actor.owner_id === bootstrap.current_owner_id && actor.kind === 'twin');
    assert.equal(bootstrap.chats.filter(chat => chat.kind === 'private').length, 1, 'primary Duet remains unique');
    assert.ok(bootstrap.bots.some(bot => bot.id === first.id && bot.name === renamed));
    await page.getByRole('button', { name: 'Open main Duet', exact: true }).click();
    await page.getByRole('button', { name: 'Assign to BOTs', exact: true }).click();
    const dispatch = page.getByRole('dialog', { name: 'Assign to BOTs', exact: true });
    await dispatch.getByRole('checkbox').nth(0).check();
    await dispatch.getByRole('checkbox').nth(1).check();
    const task = t.unique('assigned'); await dispatch.getByLabel('Task', { exact: true }).fill(`E2E ECHO ${task}`);
    await dispatch.getByRole('button', { name: 'Assign task', exact: true }).click();
    const handoffs = await poll(async () => {
      const detail = await api(page, `/chats/${mainChat.id}`);
      const results = detail.messages.filter(message => message.coordination?.kind === 'result' && [first.id, second.id].includes(message.author_id) && message.body === `E2E echo: ${task}`);
      return results.length === 2 ? results : false;
    }, 'both BOT results returned to main Duet', { timeout: 45000 });
    for (const result of handoffs) await page.locator(`article[data-message-id="${result.id}"]`).getByText('Result returned', { exact: true }).waitFor();
    assert.ok((await api(page, `/chats/${mainChat.id}`)).messages.some(message => message.coordination?.kind === 'assignment' && message.body.includes(task)), 'assignment is visible in main chat');
    t.record('dispatch', { botIds: [first.id, second.id], primaryChatId: mainChat.id, resultMessageIds: handoffs.map(message => message.id), directResultMessageId: directResult.id });

    await page.getByRole('button', { name: 'Agent message', exact: true }).click();
    const peer = page.getByRole('dialog', { name: 'Agent message', exact: true });
    await peer.getByLabel('From agent', { exact: true }).selectOption(first.id); await peer.getByLabel('To agent', { exact: true }).selectOption(second.id);
    const peerLabel = t.unique('peer'); await peer.getByLabel('Message', { exact: true }).fill(`E2E ECHO ${peerLabel}`);
    await peer.getByRole('button', { name: 'Send internal message', exact: true }).click();
    await page.locator('.chat-header h2').filter({ hasText: second.name }).waitFor();
    const peerResult = await echo(page, second.chat_id, peerLabel, second.id);
    assert.ok((await api(page, `/chats/${first.chat_id}`)).messages.some(message => message.author_id === first.id && message.coordination?.kind === 'message' && message.body.includes(peerLabel)), 'sending BOT retains the internal message');

    await page.getByRole('button', { name: 'New collaboration room', exact: true }).click();
    const roomDialog = page.getByRole('dialog', { name: 'New collaboration room', exact: true }); const roomName = t.unique('Workshop');
    await roomDialog.getByLabel('Room name', { exact: true }).fill(roomName); await roomDialog.getByRole('checkbox').nth(0).check(); await roomDialog.getByRole('checkbox').nth(1).check();
    const roomResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/bot-rooms' && response.request().method() === 'POST');
    await roomDialog.getByRole('button', { name: 'Create room', exact: true }).click(); const room = await (await roomResponse).json();
    await page.locator('.chat-header h2').filter({ hasText: roomName }).waitFor(); assert.equal(await page.getByLabel('Room members').locator(':scope > span').count(), 3);
    const recipients = page.getByRole('group', { name: 'Ask to respond', exact: true }); for (const checkbox of await recipients.getByRole('checkbox').all()) await checkbox.check();
    const roomLabel = t.unique('room'); await sendMessage(page, `E2E ECHO ${roomLabel}`);
    const roomResults = [];
    for (const actorId of [main.id, first.id, second.id]) roomResults.push(await echo(page, room.id, roomLabel, actorId));
    await page.screenshot({ path: path.join(t.artifactDir, 'personal-bots-room.png'), fullPage: true });
    t.record('internalCoordination', { peerResultMessageId: peerResult.id, roomId: room.id, memberActorIds: room.member_actor_ids, roomResultMessageIds: roomResults.map(message => message.id) });

    await privateChat(page); await page.getByRole('button', { name: 'Inspect BOT transcript', exact: true }).click();
    const transcript = page.getByRole('dialog', { name: 'Inspect BOT transcript', exact: true }); await transcript.getByLabel('BOT', { exact: true }).selectOption(first.id);
    await transcript.locator('.bot-transcript').getByText(`E2E echo: ${direct}`, { exact: true }).waitFor();
    await page.screenshot({ path: path.join(t.artifactDir, 'personal-bots-transcript.png'), fullPage: true });
    await transcript.getByRole('button', { name: 'Close', exact: true }).click();
    await page.reload(); await privateChat(page);
    assert.ok((await api(page, '/bots')).some(bot => bot.id === first.id && bot.assignment === 'Research assigned tasks and state the evidence.'), 'BOT assignment survives reload');
    const other = await t.session('DEV-BBB', { baseURL: t.secondaryURL }); const otherBootstrap = await api(other.page, '/bootstrap');
    assert.ok(!otherBootstrap.bots.some(bot => [first.id, second.id].includes(bot.id))); assert.ok(!otherBootstrap.actors.some(actor => [first.id, second.id].includes(actor.id))); assert.ok(!otherBootstrap.chats.some(chat => [first.chat_id, second.chat_id, room.id].includes(chat.id)));
    await api(other.page, `/bots/${first.id}/history`, { status: 404 }); await api(other.page, `/chats/${first.chat_id}`, { status: 403 }); await api(other.page, `/chats/${room.id}`, { status: 403 });
    t.record('inspectionAndIsolation', { transcriptMessageId: directResult.id, privateChatStillPrimary: true, otherOwnerCatalogAndReadsDenied: true, browserApiInterception: false, modelMode: 'deterministic-offline' });
  },
}];
module.exports = { cases };
