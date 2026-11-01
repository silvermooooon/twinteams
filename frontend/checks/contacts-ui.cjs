/** Offline person-card acceptance using the existing app and synthetic API fixture. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5199';
let browser;
async function run() {
  browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  const mock = await fixture(page, 'en', true);
  const maya = mock.actors.find(actor => actor.id === 'owner-maya');
  let contact = { owner_id: maya.id, human: maya, tags: ['Website project'], last_interaction_at: '2026-11-01T00:00:00Z', revision: 1 };
  let failSave = false; const writes = [];
  await page.route('**/api/people?*', route => route.fulfill({ json: mock.actors }));
  await page.route('**/api/me/twin/contacts/**', route => {
    const request = route.request();
    if (request.method() === 'GET') return route.fulfill({ json: { contact: request.url().endsWith('/owner-maya') ? contact : null } });
    const body = request.postDataJSON(); writes.push(body);
    if (failSave) return route.fulfill({ status: 503, json: { detail: 'Could not save tags' } });
    contact = { ...contact, tags: body.tags, revision: contact.revision + 1 };
    return route.fulfill({ json: contact });
  });
  await page.goto(base + '/#chat');
  const composer = page.locator('.composer textarea'); await composer.waitFor();
  await composer.fill('Keep my draft');
  const search = page.getByRole('textbox', { name: 'Search people', exact: true });
  await search.fill('Maya');
  await page.locator('.search-results').getByRole('button', { name: 'Person details for Maya', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Person details', exact: true });
  await dialog.getByText('Website project', { exact: true }).waitFor();
  assert.match(await dialog.innerText(), /Private to your account/);
  await dialog.getByRole('button', { name: 'Edit tags', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Tag 1', exact: true }).fill('Launch project');
  await dialog.getByRole('button', { name: 'Add tag', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Tag 2', exact: true }).fill('Design lead');
  assert.equal(await dialog.getByRole('textbox', { name: 'Tag 2', exact: true }).getAttribute('maxlength'), '64');
  failSave = true;
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.getByRole('alert').filter({ hasText: 'Could not save tags' }).waitFor();
  assert.equal(await dialog.getByRole('textbox', { name: 'Tag 2', exact: true }).inputValue(), 'Design lead');
  failSave = false;
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.getByText('Tags saved.', { exact: true }).waitFor();
  assert.deepEqual(contact.tags, ['Launch project', 'Design lead']);
  await dialog.getByRole('button', { name: 'Edit tags', exact: true }).click();
  await dialog.getByRole('button', { name: 'Remove tag 1', exact: true }).click();
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.getByText('Tags saved.', { exact: true }).waitFor();
  assert.deepEqual(contact.tags, ['Design lead']);
  await dialog.getByRole('button', { name: 'Edit tags', exact: true }).click();
  await dialog.getByRole('button', { name: 'Remove tag 1', exact: true }).click();
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.getByText('No tags yet.', { exact: true }).waitFor();
  assert.deepEqual(contact.tags, []);
  await page.keyboard.press('Escape');
  assert.equal(await composer.inputValue(), 'Keep my draft');
  await search.focus();
  await page.locator('.search-results').getByRole('button', { name: 'Person details for Lin', exact: true }).click();
  await dialog.getByText('Tags become available after a direct interaction.', { exact: true }).waitFor();
  assert.equal(await dialog.getByRole('button', { name: 'Edit tags', exact: true }).count(), 0);
  await page.keyboard.press('Escape');
  await search.focus();
  assert.equal(await page.locator('.search-results').getByRole('button', { name: 'Person details for AAA', exact: true }).count(), 0);
  assert.equal(await page.locator('.search-results').getByRole('button', { name: 'Person details for Maya Duet', exact: true }).count(), 0);
  assert.equal(await page.locator('.search-results').innerText().then(text => text.includes('Design lead')), false);
  await page.locator('.person-search-chat').filter({ hasText: 'Maya' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Start chat', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  assert.equal(mock.calls.filter(call => call.path.endsWith('/messages')).length, 0);
  assert.equal(writes.length, 4);
  await page.route('**/api/chats/group-shared?*', route => route.fulfill({ json: {
    revision: 1, chat: mock.group, requests: [], approvals: [], tracking: [],
    settings: { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'workspace' },
    members: mock.actors,
    messages: [{ id: 'maya-message', chat_id: mock.group.id, seq: 1, author_id: maya.id, kind: 'human', body: 'Project update', created_at: '2026-11-01T00:00:00Z', read_by: [], state: 'published' }],
  } }));
  await page.locator('.conversation-list .chat-item').filter({ hasText: 'Shared team' }).click();
  await page.locator('.message-meta').getByRole('button', { name: 'Person details for Maya', exact: true }).click();
  await dialog.getByText('No tags yet.', { exact: true }).waitFor();
  await page.screenshot({ path: '/tmp/twinteams-contacts-desktop.png', fullPage: true });
  await page.keyboard.press('Escape');
  await page.setViewportSize({ width: 390, height: 844 });
  await search.focus();
  await page.locator('.search-results').getByRole('button', { name: 'Person details for Maya', exact: true }).click();
  await dialog.getByText('No tags yet.', { exact: true }).waitFor();
  await dialog.getByRole('button', { name: 'Edit tags', exact: true }).click();
  await dialog.getByRole('textbox', { name: 'Tag 1', exact: true }).fill('A'.repeat(64));
  await page.screenshot({ path: '/tmp/twinteams-contacts-mobile.png', fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('PASS person card add/edit/remove/clear tags, failure retry, private display, eligibility, existing start-chat and draft, chat sender entry, mobile layout');
}
run().catch(async error => { console.error(error); await browser?.close(); process.exitCode = 1; });
