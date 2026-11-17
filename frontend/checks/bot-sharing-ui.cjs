/** Browser checks of rendered sharing/install/skills flows with synthetic HTTP fixtures. */
const assert = require('node:assert/strict');
const { mkdirSync, mkdtempSync, writeFileSync, rmSync } = require('node:fs');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { chromium } = require('playwright');
const { fixture } = require('./my-twin-ui.cjs');
const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5217';
const artifacts = process.env.TWINTEAMS_UI_ARTIFACT_DIR || '/tmp/duet-bot-share/ui';
mkdirSync(artifacts, { recursive: true });
let browser; let activePage;
const state = { snapshots: [], tokens: [], installs: new Map(), next: 1 };
const copy = value => JSON.parse(JSON.stringify(value));
const clock = '2026-11-17T09:00:00Z';

async function setup(page, ownerId, signedIn = true, withBot = false) {
  const common = await fixture(page, 'en', true);
  const owner = common.actors.find(actor => actor.id === ownerId);
  const main = common.actors.find(actor => actor.kind === 'twin' && actor.owner_id === ownerId);
  const privateChat = { id: `private-${ownerId}`, kind: 'private', title: main.name, member_owner_ids: [ownerId], unread_count: 0, revision: 1 };
  const bots = []; const chats = [privateChat]; const calls = []; const skills = new Map();
  let authenticated = signedIn; let listFailure = false; let freezeFailure = true; let revokeFailure = true; let installFailure = true; let previewDelay = 250; let shareDelay = 250;
  const respond = (route, data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
  function makeBot(name, assignment, installedSnapshot = null) {
    const id = `bot-${ownerId}-${bots.length + 1}`;
    const actor = { id, owner_id: ownerId, kind: 'bot', name, initials: name.slice(0, 2), color: '#666', profile_revision: 1 };
    const chat = { id: `chat-${id}`, kind: 'bot', bot_id: id, title: name, member_owner_ids: [ownerId], member_actor_ids: [id], unread_count: 0, revision: 1 };
    const bot = { id, chat_id: chat.id, name, assignment, actor, chat, installed_snapshot_id: installedSnapshot };
    bots.push(bot); chats.push(chat); skills.set(id, [{ name: 'writer', description: 'Write clear reports', enabled: true, available: true, error: null, content: '---\nname: writer\ndescription: Write clear reports\n---\nWrite clearly.', references: ['references/style.md'] }]); return bot;
  }
  if (withBot) makeBot('Research partner', 'Research product questions and cite sources.');
  function tokenFor(snapshot) { const id = `link-${state.next++}`; const token = { id, token: `${id}-unguessable-fixture`, snapshot_id: snapshot.id, owner_id: ownerId, created_at: clock, revoked_at: null }; state.tokens.push(token); return token; }
  const snapshotFor = id => state.snapshots.find(snapshot => snapshot.id === id);
  const withTokens = snapshot => ({ ...snapshot, tokens: state.tokens.filter(token => token.snapshot_id === snapshot.id && token.owner_id === ownerId).map(copy) });
  await page.route('**/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname; const method = request.method();
    const body = request.postData() && request.headers()['content-type']?.includes('application/json') ? JSON.parse(request.postData()) : null;
    calls.push({ path, method, body });
    if (path === '/api/auth/me') return respond(route, authenticated ? { owner, current_owner_id: ownerId, development_mode: true } : { detail: 'Sign in required' }, authenticated ? 200 : 401);
    if (path === '/api/auth/login') { authenticated = true; return respond(route, { owner, current_owner_id: ownerId, development_mode: true }); }
    if (path === '/api/bootstrap') return respond(route, { current_owner_id: ownerId, actors: [owner, main, ...bots.map(bot => bot.actor)], chats, bots, reminders: [], model_configured: true, development_mode: true });
    if (path.startsWith('/api/chats/')) {
      const chat = chats.find(chat => chat.id === path.split('/')[3]); if (!chat) return respond(route, { detail: 'Unavailable chat' }, 404);
      if (path.endsWith('/summary')) return respond(route, chat);
      if (path.endsWith('/activity')) return respond(route, { items: [] });
      if (path.endsWith('/workspace')) return respond(route, []);
      if (path.endsWith('/read') || path.endsWith('/settings')) return respond(route, body || {});
      return respond(route, { revision: 1, chat, messages: [], requests: [], approvals: [], questions: [], has_more: false, members: [owner, main, ...bots.map(bot => bot.actor)], settings: { sidebar_open: false, sidebar_tab: 'workspace' } });
    }
    if (path === '/api/bots') return respond(route, bots);
    if (path === '/api/me/twin/onboarding') return respond(route, { show_prompt: false, state: 'completed' });
    if (path.startsWith('/api/bot-shares/')) {
      assert.equal(method, 'POST');
      assert.ok(['/api/bot-shares/preview', '/api/bot-shares/install'].includes(path), 'Share tokens belong in the request body, never in access-log URLs');
      if (!authenticated) return respond(route, { detail: 'Sign in required' }, 401);
      const token = state.tokens.find(token => token.token === body.token);
      if (!token || token.revoked_at && !state.installs.has(`${ownerId}:${token.snapshot_id}`)) return respond(route, { detail: 'Share link revoked' }, 404);
      const snapshot = snapshotFor(token.snapshot_id);
      const installed = state.installs.get(`${ownerId}:${snapshot.id}`);
      if (path.endsWith('/install')) {
        if (installFailure) return respond(route, { detail: 'Synthetic install failure' }, 503);
        const bot = installed || makeBot(snapshot.name, snapshot.assignment, snapshot.id); state.installs.set(`${ownerId}:${snapshot.id}`, bot); return respond(route, bot);
      }
      await new Promise(resolve => setTimeout(resolve, previewDelay));
      return respond(route, { snapshot, installed_bot: installed || null, source_explanation: 'An independent copy of a snapshot shared by another owner.' });
    }
    if (path.startsWith('/api/bots/')) {
      const id = path.split('/')[3]; const bot = bots.find(bot => bot.id === id); if (!bot) return respond(route, { detail: 'Unavailable BOT' }, 404);
      if (path.includes('/shares/tokens/')) {
        if (revokeFailure) return respond(route, { detail: 'Synthetic revoke failure' }, 503);
        state.tokens.find(token => token.id === path.split('/').pop() && token.owner_id === ownerId).revoked_at = clock; return respond(route, { ok: true });
      }
      if (path.endsWith('/tokens')) return respond(route, tokenFor(snapshotFor(path.split('/')[5])));
      if (path.endsWith('/shares')) {
        if (method === 'GET') { await new Promise(resolve => setTimeout(resolve, shareDelay)); return respond(route, listFailure ? { detail: 'Synthetic snapshot list failure' } : { snapshots: state.snapshots.filter(snapshot => snapshot.source_bot_id === id).map(withTokens), installed_snapshot: bot.installed_snapshot_id ? withTokens(snapshotFor(bot.installed_snapshot_id)) : null }, listFailure ? 503 : 200); }
        if (freezeFailure) return respond(route, { detail: 'Synthetic freeze failure' }, 503);
        const snapshot = { id: `snapshot-${state.next++}`, parent_snapshot_id: bot.installed_snapshot_id, source_bot_id: id, source_owner_id: ownerId, name: bot.name, assignment: bot.assignment, skill_count: skills.get(id).filter(skill => skill.enabled).length, memory_included: true, created_at: clock };
        state.snapshots.push(snapshot); const token = tokenFor(snapshot); return respond(route, { snapshot: withTokens(snapshot), token });
      }
      if (path.includes('/skills')) {
        const items = skills.get(id);
        if (path.endsWith('/skills')) return respond(route, { items });
        if (path.endsWith('/upload')) { assert.match(request.headers()['content-type'], /^multipart\/form-data/); assert.match(request.postData(), /uploader\/SKILL.md/); items.push({ name: 'uploader', description: 'Uploaded BOT skill', enabled: false, available: true, error: null, content: '---\nname: uploader\ndescription: Uploaded BOT skill\n---\nUploaded content.', references: [] }); return respond(route, { items }); }
        const name = decodeURIComponent(path.split('/').pop()); const skill = items.find(skill => skill.name === name);
        if (method === 'DELETE') { skills.set(id, items.filter(skill => skill.name !== name)); return respond(route, { items: skills.get(id) }); }
        if (method === 'PATCH') Object.assign(skill, body);
        if (method === 'PUT') skill.content = body.content;
        return respond(route, skill);
      }
      if (method === 'PATCH') { Object.assign(bot, body); Object.assign(bot.actor, { name: body.name, profile_revision: 2 }); bot.chat.title = body.name; return respond(route, bot); }
    }
    return route.fallback();
  });
  return { bots, calls, skills, freezeFailure: value => { freezeFailure = value; }, listFailure: value => { listFailure = value; }, revokeFailure: value => { revokeFailure = value; }, installFailure: value => { installFailure = value; }, previewDelay: value => { previewDelay = value; } };
}

async function openShare(page, name) {
  const row = page.locator('.chat-list-row').filter({ has: page.locator('.chat-name').filter({ hasText: name }) });
  await row.getByRole('button', { name: 'Chat options', exact: true }).click(); await page.getByRole('menuitem', { name: 'Share BOT', exact: true }).click();
  return page.getByRole('dialog', { name: 'Share BOT', exact: true });
}

async function run() {
  browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
  const sourceContext = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce', permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await sourceContext.newPage(); activePage = page; page.setDefaultTimeout(12000); const errors = []; page.on('pageerror', error => errors.push(error.message));
  const source = await setup(page, 'owner-alex', true, true);
  await page.goto(base + '/#chat'); let dialog = await openShare(page, /^Research partner$/);
  await dialog.getByRole('status').filter({ hasText: 'Loading' }).waitFor(); await dialog.getByRole('heading', { name: 'No snapshots created yet', exact: true }).waitFor();
  await dialog.getByRole('button', { name: 'Share my current version', exact: true }).click(); await dialog.getByRole('alert').filter({ hasText: 'Synthetic freeze failure' }).waitFor();
  source.freezeFailure(false); await dialog.getByRole('button', { name: 'Share my current version', exact: true }).click();
  await dialog.getByRole('status').filter({ hasText: 'Snapshot created' }).waitFor(); const first = state.snapshots[0];
  const freezes = source.calls.filter(call => call.path.endsWith('/shares') && call.method === 'POST'); assert.equal(freezes[0].body.client_request_key, freezes[1].body.client_request_key);
  await dialog.getByRole('button', { name: 'Copy link', exact: true }).click(); await dialog.getByRole('status').filter({ hasText: 'Link copied to clipboard.' }).waitFor();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), `${base}/#/bots/share/${state.tokens[0].token}`);
  await dialog.getByRole('button', { name: 'Create another link', exact: true }).click(); await dialog.getByLabel('Share link', { exact: true }).nth(1).waitFor();
  await dialog.getByRole('button', { name: 'Revoke link', exact: true }).first().click(); await dialog.getByRole('alert').filter({ hasText: 'Synthetic revoke failure' }).waitFor(); assert.equal(await dialog.getByLabel('Share link', { exact: true }).count(), 2);
  source.revokeFailure(false); await dialog.getByRole('button', { name: 'Revoke link', exact: true }).first().click(); await dialog.getByRole('status').filter({ hasText: 'Already installed BOTs are unchanged.' }).waitFor(); assert.equal(await dialog.getByLabel('Share link', { exact: true }).count(), 1);
  await page.screenshot({ path: join(artifacts, 'share-desktop.png'), fullPage: true });
  await dialog.getByRole('button', { name: 'Close', exact: true }).click(); source.listFailure(true); dialog = await openShare(page, /^Research partner$/); await dialog.getByRole('alert').filter({ hasText: 'Synthetic snapshot list failure' }).waitFor();
  source.listFailure(false); await dialog.getByRole('button', { name: 'Retry', exact: true }).click(); await dialog.getByLabel('Share link', { exact: true }).waitFor(); await dialog.getByRole('button', { name: 'Close', exact: true }).click();
  await page.locator('.chat-item').filter({ hasText: 'Research partner' }).click(); await page.getByRole('button', { name: 'Edit BOT', exact: true }).click(); dialog = page.getByRole('dialog'); await dialog.getByLabel('Assignment', { exact: true }).fill('Review research for maintainability.'); await dialog.getByRole('button', { name: 'Save and share BOT', exact: true }).click();
  dialog = page.getByRole('dialog', { name: 'Share BOT', exact: true }); await dialog.getByRole('button', { name: 'Share my current version', exact: true }).click(); await dialog.getByRole('status').filter({ hasText: 'Snapshot created' }).waitFor(); assert.equal(state.snapshots[1].assignment, 'Review research for maintainability.'); assert.equal(first.assignment, 'Research product questions and cite sources.'); await dialog.getByRole('button', { name: 'Close', exact: true }).click();

  const recipientContext = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce' });
  const recipientPage = await recipientContext.newPage(); activePage = recipientPage; recipientPage.setDefaultTimeout(12000); recipientPage.on('pageerror', error => errors.push(error.message)); const recipient = await setup(recipientPage, 'owner-maya', false);
  const originalLink = state.tokens[1].token;
  await recipientPage.goto(`${base}/#/bots/share/${originalLink}`); await recipientPage.getByLabel('Username', { exact: true }).fill('DEV-BBB'); await recipientPage.getByRole('button', { name: 'Sign in', exact: true }).click();
  await recipientPage.getByRole('status').filter({ hasText: 'Loading shared BOT' }).waitFor(); await recipientPage.getByRole('heading', { name: 'Research partner', exact: true }).waitFor(); assert.equal(new URL(recipientPage.url()).hash, `#/bots/share/${originalLink}`);
  await recipientPage.getByText('1 enabled skill', { exact: true }).waitFor(); await recipientPage.getByText('Memory included', { exact: true }).waitFor();
  await recipientPage.getByRole('button', { name: 'Add to my BOTs', exact: true }).click(); await recipientPage.getByRole('alert').filter({ hasText: 'Synthetic install failure' }).waitFor();
  recipient.installFailure(false); await recipientPage.getByRole('button', { name: 'Add to my BOTs', exact: true }).click(); await recipientPage.locator('.chat-header h2').filter({ hasText: 'Research partner' }).waitFor(); assert.equal(recipient.bots.length, 1);
  await recipientPage.goto(`${base}/#/bots/share/${originalLink}`); await recipientPage.getByRole('button', { name: 'Open my BOT', exact: true }).click(); await recipientPage.locator('.chat-header h2').filter({ hasText: 'Research partner' }).waitFor(); assert.equal(recipient.bots.length, 1);
  dialog = await openShare(recipientPage, /^Research partner$/); await dialog.getByRole('heading', { name: 'Originally installed version', exact: true }).waitFor(); await dialog.getByRole('button', { name: 'Share original snapshot', exact: true }).click(); await dialog.getByLabel('Share link', { exact: true }).waitFor(); assert.equal(state.tokens.at(-1).snapshot_id, first.id);
  recipient.freezeFailure(false); await dialog.getByRole('button', { name: 'Share my current version', exact: true }).click(); await dialog.getByRole('status').filter({ hasText: 'Snapshot created' }).waitFor(); assert.equal(state.snapshots.at(-1).parent_snapshot_id, first.id);
  await recipientPage.setViewportSize({ width: 390, height: 844 }); const bounds = await dialog.boundingBox(); assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390); await recipientPage.screenshot({ path: join(artifacts, 'share-mobile.png'), fullPage: true });
  await dialog.getByRole('button', { name: 'Close', exact: true }).click(); await recipientPage.setViewportSize({ width: 1360, height: 900 }); await recipientPage.getByRole('button', { name: 'Edit BOT', exact: true }).click(); dialog = recipientPage.getByRole('dialog'); await dialog.getByRole('button', { name: 'Manage BOT skills', exact: true }).click(); dialog = recipientPage.getByRole('dialog');
  await dialog.getByRole('switch', { name: 'Enable writer', exact: true }).waitFor(); await dialog.getByRole('switch', { name: 'Enable writer', exact: true }).click(); await dialog.getByRole('switch', { name: 'Enable writer', exact: true, checked: false }).waitFor();
  await dialog.getByRole('button', { name: 'Edit writer', exact: true }).click(); await dialog.getByLabel('SKILL.md', { exact: true }).fill('---\nname: writer\ndescription: Write clear reports\n---\nWrite concise reviews.'); await dialog.getByRole('button', { name: 'Save', exact: true }).click(); await dialog.getByRole('status').filter({ hasText: 'BOT skill saved.' }).waitFor(); assert.match(recipient.skills.get(recipient.bots[0].id)[0].content, /Write concise reviews/); assert.doesNotMatch(source.skills.get(source.bots[0].id)[0].content, /Write concise reviews/);
  await dialog.getByRole('button', { name: 'Remove skill', exact: true }).click(); await dialog.getByRole('button', { name: 'Remove from this BOT', exact: true }).click(); await dialog.getByRole('heading', { name: 'No BOT skills yet', exact: true }).waitFor();
  const temporary = mkdtempSync(join(tmpdir(), 'duet-bot-skill-ui-')); const folder = join(temporary, 'uploader'); mkdirSync(folder); writeFileSync(join(folder, 'SKILL.md'), '---\nname: uploader\ndescription: Uploaded BOT skill\n---\nUploaded content.');
  try { const chooserPromise = recipientPage.waitForEvent('filechooser'); await dialog.getByRole('button', { name: 'Upload Skill folder', exact: true }).click(); await (await chooserPromise).setFiles(folder); await dialog.getByRole('status').filter({ hasText: 'Skill uploaded.' }).waitFor(); } finally { rmSync(temporary, { recursive: true, force: true }); }
  await dialog.getByRole('switch', { name: 'Enable uploader', exact: true, checked: false }).waitFor(); assert.equal(recipient.calls.filter(call => call.path.startsWith('/api/me/twin/skills') && call.method !== 'GET').length, 0);
  await dialog.getByRole('button', { name: 'Close', exact: true }).click(); await recipientPage.goto(`${base}/#/bots/share/${state.tokens[0].token}`); await recipientPage.getByRole('button', { name: 'Open my BOT', exact: true }).waitFor(); assert.equal(await recipientPage.getByRole('button', { name: 'Add to my BOTs', exact: true }).count(), 0);
  const neverInstalled = state.tokens.find(token => token.snapshot_id === state.snapshots[1].id); neverInstalled.revoked_at = clock; await recipientPage.goto(`${base}/#/bots/share/${neverInstalled.token}`); await recipientPage.getByRole('heading', { name: 'Share link unavailable', exact: true }).waitFor();
  await recipientPage.goto(`${base}/#/bots/share/`); await recipientPage.getByRole('heading', { name: 'Share link unavailable', exact: true }).waitFor();
  assert.deepEqual(errors, []); await sourceContext.close(); await recipientContext.close(); await browser.close();
  console.log('PASS BOT sharing UI: share/edit entry, loading/empty/error/retry, stable freeze key, copy, mint, revoke failure/success, login return, preview/install/retry/idempotent open, original-node and new-node share, mobile, BOT skill toggle/edit/delete/upload without main Duet writes, unavailable/revoked links.');
}
run().catch(async error => { console.error(error); if (activePage) await activePage.screenshot({ path: join(artifacts, 'failure.png'), fullPage: true }).catch(() => {}); await browser?.close(); process.exitCode = 1; });
