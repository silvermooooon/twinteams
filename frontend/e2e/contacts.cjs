/** Owner-private contacts through real browser, PostgreSQL, worker and SDK tools. */
const { assert, api, poll, sendMessage, privateChat, chooseChat } = require('./helpers.cjs');
const { execFileSync } = require('node:child_process');
const path = require('node:path');

function fixture(operation = 'inspect', args = []) {
  const result = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'), operation, '--state', process.env.TWINTEAMS_E2E_STATE, ...args], { encoding: 'utf8' }));
  assert.equal(result.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  return result;
}

async function personCard(page, name) {
  const search = page.getByRole('textbox', { name: 'Search people', exact: true });
  await search.fill(name);
  await search.focus();
  await page.locator('.search-results').getByRole('button', { name: 'Person details for ' + name, exact: true }).click();
  return page.getByRole('dialog', { name: 'Person details', exact: true });
}

const cases = [{
  name: 'contacts / real human chat, published Dream, tool lookup, manual tags, reload and owner isolation',
  retainTrace: true,
  timeout: 180000,
  run: async t => {
    const { page: a } = await t.session();
    const { page: b } = await t.session('DEV-BBB', { baseURL: t.secondaryURL });
    const people = await api(a, '/people');
    const names = Object.fromEntries(people.filter(row => row.kind === 'human').map(row => [row.id, row.name]));
    assert.equal((await api(a, '/me/twin/contacts/owner-jordan')).contact, null);
    let card = await personCard(a, names['owner-jordan']);
    await card.getByText('Tags become available after a direct interaction.', { exact: true }).waitFor();
    assert.equal(await card.getByRole('button', { name: 'Edit tags', exact: true }).count(), 0);
    await a.keyboard.press('Escape');

    await a.getByRole('button', { name: 'New chat', exact: true }).click();
    await a.locator('.people-picker-inline').getByRole('combobox', { name: 'Search people', exact: true }).fill(names['owner-maya']);
    await a.getByRole('option', { name: names['owner-maya'], exact: true }).click();
    await a.getByRole('button', { name: 'Start chat', exact: true }).click();
    await a.locator('.new-chat-main').waitFor({ state: 'hidden' });
    const chat = await poll(async () => (await api(a, '/bootstrap')).chats.find(row => row.kind === 'group' && row.member_owner_ids.length === 2 && row.member_owner_ids.includes('owner-alex') && row.member_owner_ids.includes('owner-maya')), 'real A/B direct chat');
    const label = t.unique('contact');
    await sendMessage(a, 'For Atlas, which development responsibility do you own? ' + label);
    const peerChat = await poll(async () => (await api(b, '/bootstrap')).chats.find(row => row.id === chat.id), 'B receives real chat');
    await chooseChat(b, peerChat.title);
    const sourceBody = 'I own Atlas settlement development. ' + label;
    await sendMessage(b, sourceBody);
    const source = await poll(async () => (await api(a, '/chats/' + chat.id)).messages.find(row => row.author_id === 'owner-maya' && row.body === sourceBody), 'B published human responsibility evidence');
    await a.locator('article[data-message-id="' + source.id + '"]').waitFor();
    assert.deepEqual((await api(a, '/me/twin/contacts/owner-maya')).contact.tags, []);

    const advance = fixture('advance-contact-dream', ['--owner-id', 'owner-alex']);
    await poll(async () => (await api(a, '/me/twin/contacts/owner-maya')).contact.tags.includes('Atlas · settlement development'), 'worker publishes Dream contact tags', { timeout: 45000 });
    const dreamRequest = await poll(() => fixture().requests.find(row => row.source === 'memory_dream' && row.requester_id === 'owner-alex' && row.status === 'completed'), 'automatically scheduled Dream completes');
    const dream = { request_id: dreamRequest.id };
    card = await personCard(b, names['owner-alex']);
    await card.getByText('No tags yet.', { exact: true }).waitFor();
    assert.deepEqual((await api(b, '/me/twin/contacts?q=' + encodeURIComponent('settlement development'))).items, []);
    await b.keyboard.press('Escape');
    const personal = await privateChat(a);
    await sendMessage(a, 'E2E CONTACTS ' + label);
    const prefix = 'E2E contacts readback ' + label + ': ';
    const lookup = await poll(async () => (await api(a, '/chats/' + personal.id)).messages.find(row => row.kind === 'twin' && row.body.startsWith(prefix)), 'actual search_contacts tool result', { timeout: 45000 });
    const readback = JSON.parse(lookup.body.slice(prefix.length));
    assert.ok(JSON.stringify(readback).includes('Atlas · settlement development'));
    card = await personCard(a, names['owner-maya']);
    await card.getByText('Atlas · settlement development', { exact: true }).waitFor();
    await card.getByRole('button', { name: 'Edit tags', exact: true }).click();
    const manual = ['Atlas · frontend owner ' + label, 'Launch · release coordinator'];
    await card.getByRole('textbox', { name: 'Tag 1', exact: true }).fill(manual[0]);
    await card.getByRole('button', { name: 'Add tag', exact: true }).click();
    await card.getByRole('textbox', { name: 'Tag 2', exact: true }).fill(manual[1]);
    await card.getByRole('button', { name: 'Save', exact: true }).click();
    await card.getByText('Tags saved.', { exact: true }).waitFor();
    await a.keyboard.press('Escape');
    await a.reload();
    await a.locator('.composer textarea').waitFor();
    card = await personCard(a, names['owner-maya']);
    for (const tag of manual) await card.getByText(tag, { exact: true }).waitFor();
    const saved = (await api(a, '/me/twin/contacts/owner-maya')).contact;
    assert.deepEqual(saved.tags, manual);
    assert.equal(saved.manual_revision, 1);
    await a.screenshot({ path: path.join(t.artifactDir, 'contacts-manual-persisted.png'), fullPage: true });
    await a.keyboard.press('Escape');

    card = await personCard(b, names['owner-alex']);
    await card.getByText('No tags yet.', { exact: true }).waitFor();
    for (const tag of manual) assert.equal((await card.innerText()).includes(tag), false);
    assert.deepEqual((await api(b, '/me/twin/contacts/owner-alex')).contact.tags, []);
    assert.deepEqual((await api(b, '/me/twin/contacts?q=' + encodeURIComponent(label))).items, []);
    await b.screenshot({ path: path.join(t.artifactDir, 'contacts-peer-private.png'), fullPage: true });
    const denied = await a.request.put(new URL('/api/me/twin/contacts/owner-jordan/tags', a.url()).href, { data: { tags: ['Unsupported'] } });
    assert.equal(denied.status(), 404);
    assert.equal((await api(a, '/me/twin/contacts/owner-jordan')).contact, null);

    const missingLabel = t.unique('missing-contact');
    await sendMessage(a, 'E2E CONTACTS_MISSING ' + missingLabel);
    const question = await poll(async () => (await api(a, '/chats/' + personal.id)).questions?.find(row => row.status === 'pending' && row.question.includes(missingLabel)), 'empty contacts lookup asks owner through the actual question tool', { timeout: 45000 });
    await a.locator('[data-question-id="' + question.id + '"]').getByText(question.question, { exact: true }).waitFor();
    const missingPrefix = 'E2E contacts readback ' + missingLabel + ': ';
    const missingLookup = await poll(async () => (await api(a, '/chats/' + personal.id)).messages.find(row => row.kind === 'twin' && row.body.startsWith(missingPrefix)), 'empty search_contacts readback');
    assert.deepEqual(JSON.parse(missingLookup.body.slice(missingPrefix.length)).items, []);

    const evidence = fixture();
    assert.ok(evidence.requests.some(row => row.id === dream.request_id && row.status === 'completed'));
    for (const tool of ['stage_contact_tags', 'memory_publish']) assert.ok(evidence.tools.some(row => row.request_id === dream.request_id && row.name === tool && row.success));
    assert.ok(evidence.tools.some(row => row.request_id === lookup.request_id && row.name === 'search_contacts' && row.success));
    for (const tool of ['search_contacts', 'ask_question']) assert.ok(evidence.tools.some(row => row.request_id === missingLookup.request_id && row.name === tool && row.success));
    assert.equal(evidence.tools.some(row => row.request_id === dream.request_id && ['memory_write', 'memory_delete'].includes(row.name)), false);
    t.record('contact_acceptance', { chat_id: chat.id, human_source_message_id: source.id, dream_request_id: dream.request_id, lookup_request_id: lookup.request_id, automatic_tags: ['Atlas · settlement development'], manual_tags: manual, revision: saved.revision, manual_revision: saved.manual_revision, owner_isolation: true, automatic_tags_private_to_owner: true, unrelated_person_uneditable: true, dream_used_sdk_tools: true, fixture_due_advance: advance, contact_evidence_not_written_to_memory: true, empty_lookup_question_id: question.id, provider_decisions_scripted: true, business_api_interception: false });
  },
}];
module.exports = { cases };
