/** Real forum APIs and owner controls; only model choices are scripted offline. */
const { assert, api, poll, privateChat, sendMessage } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

function fixture(role) {
  const result = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON,
    [path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'), role,
      '--state', process.env.TWINTEAMS_E2E_STATE], { encoding: 'utf8' }));
  assert.equal(result.browser_e2e, true);
  assert.equal(result.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  return result;
}

async function topics(page) {
  await privateChat(page);
  const tab = page.getByRole('tab', { name: 'Topics', exact: true });
  if (!(await tab.isVisible())) await page.getByRole('button', { name: 'Toggle sidebar', exact: true }).click();
  await tab.click();
  return page.locator('.duet-conversations');
}

async function allowInvitation(page, chatId) {
  const inbox = await privateChat(page);
  const approval = (await api(page, '/chats/' + inbox.id)).approvals.find(row =>
    row.chat_id === chatId && row.invitation && row.status === 'pending');
  assert.ok(approval, 'pending invitation is visible in the real owner inbox');
  assert.deepEqual(approval.invited_owner_ids, ['owner-jordan']);
  const card = page.locator('.approvals-drawer .duet-approval');
  await card.getByText('Invited Duets:', { exact: true }).waitFor();
  await card.getByText('Invitation request', { exact: true }).click();
  await card.getByText('Review the revised milestone and add delivery risks.', { exact: true }).waitFor();
  await card.getByRole('button', { name: 'Allow once', exact: true }).click();
  await poll(async () => !(await api(page, '/chats/' + inbox.id)).approvals.some(row =>
    row.id === approval.id && row.status === 'pending'), 'invitation approval persisted');
  return approval.id;
}

const cases = [{
  name: 'forum topic / real history, explicit mentions and invitation consent without a send cap',
  run: async t => {
    assert.equal(process.env.TWINTEAMS_E2E_SCENARIO, 'forum-topic');
    assert.equal(process.env.TWINTEAMS_E2E_MODEL_MODE, 'deterministic-offline');
    const seed = fixture('forum-topic-state');
    assert.equal(seed.test_only_product_tool_seed, true);
    const { page } = await t.session();
    const sidebar = await topics(page);
    let historyRequests = 0;
    page.on('request', request => { if (/\/posts\/[^/]+\/history/.test(request.url())) historyRequests++; });
    await sidebar.locator('.duet-conversation-card').filter({ hasText: seed.title }).click();
    await sidebar.getByRole('heading', { name: seed.title, exact: true }).waitFor();
    const detail = await api(page, '/duet-conversations/' + seed.chat_id);
    assert.equal(detail.conversation.admin_owner_id, 'owner-alex');
    assert.equal(detail.messages.length, seed.post_count);
    assert.equal(detail.pending_messages.length, 0);
    assert.equal('send_budgets' in detail, false);
    assert.equal(await sidebar.locator('.duet-transcript-message').count(), seed.post_count);
    assert.ok((await sidebar.locator('.duet-topic-admin').innerText()).startsWith('Administrator:'));
    assert.equal(await sidebar.locator('textarea').count(), 0, 'topic owner view has no composer');
    assert.equal(await sidebar.getByRole('button', { name: /Continue/i }).count(), 0);
    const edited = sidebar.locator(`[data-message-id="${seed.edited_message_id}"]`);
    await edited.getByText('Edited', { exact: true }).waitFor();
    const mention = edited.getByLabel('Mentioned Duets');
    assert.ok((await mention.innerText()).startsWith('@'));
    assert.deepEqual(detail.messages.find(row => row.id === seed.edited_message_id).mention_owner_ids, ['owner-maya']);
    assert.equal(historyRequests, 0, 'history is not fetched until explicitly opened');
    await edited.getByRole('button', { name: 'View edit history', exact: true }).click();
    const history = edited.getByRole('region', { name: 'Post edit history', exact: true });
    await history.getByText('Version 2', { exact: true }).waitFor();
    await history.getByText('Version 1', { exact: true }).waitFor();
    await history.getByText('Delivery checkpoint 2.', { exact: true }).waitFor();
    await history.getByText('Delivery checkpoint 2: revised milestone.', { exact: true }).waitFor();
    assert.ok(historyRequests >= 1);
    await page.screenshot({ path: path.join(t.artifactDir, 'forum-topic-history.png'), fullPage: true });

    const jordan = await t.session('DEV-CCC', { baseURL: t.secondaryURL });
    const jordanTopics = await topics(jordan.page);
    assert.equal(await jordan.page.locator('.approvals-drawer').count(), 0);
    await sendMessage(page, 'E2E FORUM_INVITE');
    await jordan.page.locator('.approvals-drawer .duet-approval').getByText('Invited Duets:', { exact: true }).waitFor();
    const before = await api(jordan.page, '/duet-conversations');
    assert.equal(before.conversations.some(row => row.chat_id === seed.chat_id), false);
    const denied = await jordan.page.request.get(new URL('/api/duet-conversations/' + seed.chat_id, t.secondaryURL).href);
    assert.ok([403, 404].includes(denied.status()), 'pending invitee cannot read existing posts');
    await jordanTopics.getByText('No Duet topics yet', { exact: true }).waitFor();
    await jordan.page.screenshot({ path: path.join(t.artifactDir, 'forum-topic-pending-invitation.png'), fullPage: true });
    const jordanApproval = await allowInvitation(jordan.page, seed.chat_id);
    assert.equal((await api(jordan.page, '/duet-conversations')).conversations.length, 0,
      'one invited owner decision does not bypass the existing owners');
    const alexApproval = await allowInvitation(page, seed.chat_id);
    const maya = await t.session('DEV-BBB');
    const mayaApproval = await allowInvitation(maya.page, seed.chat_id);
    await poll(async () => (await api(jordan.page, '/duet-conversations')).conversations.some(row =>
      row.chat_id === seed.chat_id && row.status === 'active'), 'new membership after all three decisions');
    await jordanTopics.getByRole('button', { name: 'Refresh Duet topics', exact: true }).click();
    await jordanTopics.locator('.duet-conversation-card').filter({ hasText: seed.title }).click();
    await jordanTopics.getByRole('heading', { name: seed.title, exact: true }).waitFor();
    await jordanTopics.locator('.duet-transcript').getByText('Review the revised milestone and add delivery risks.', { exact: true }).waitFor();
    const admitted = await api(jordan.page, '/duet-conversations/' + seed.chat_id);
    assert.equal(admitted.messages.length, seed.post_count + 1, 'only explicit invitation requirements added a post');
    assert.deepEqual(new Set(admitted.conversation.participant_owner_ids), new Set(['owner-alex', 'owner-maya', 'owner-jordan']));
    assert.ok(admitted.messages.some(row => row.id === seed.edited_message_id));
    const evidence = await poll(() => {
      const state = fixture('inspect');
      const requests = state.requests.filter(row => row.chat_id === seed.chat_id);
      assert.ok(requests.length > 6);
      assert.equal(requests.some(row => row.status === 'failed'), false);
      return requests.every(row => row.status === 'completed') ? state : false;
    }, 'offline topic notification executions complete silently', { timeout: 45000, interval: 750 });
    const notices = evidence.requests.filter(row => row.chat_id === seed.chat_id);
    assert.equal((await api(jordan.page, '/duet-conversations/' + seed.chat_id)).messages.length, seed.post_count + 1);
    assert.ok(notices.every(row => row.source_message_id === null && row.topic_notification?.topic_id === seed.chat_id));
    t.record('forum', { chatId: seed.chat_id, initialPosts: seed.post_count, finalPosts: admitted.messages.length,
      admin: 'owner-alex', mentions: ['owner-maya'], lazyHistoryRequests: historyRequests,
      versions: [2, 1], pendingInviteReadStatus: denied.status(), ownerDecisions: [jordanApproval, alexApproval, mayaApproval],
      invitationAppearedWithoutReload: true, completedMetadataOnlyNotices: notices.length, noAutomaticPosts: true, noContinue: true,
      businessAPIInterception: false, provider: 'deterministic-offline' });
    await jordan.page.screenshot({ path: path.join(t.artifactDir, 'forum-topic-admitted.png'), fullPage: true });
  },
}];
module.exports = { cases };
