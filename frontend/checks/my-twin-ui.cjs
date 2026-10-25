/** Offline browser acceptance. Intercepts every API/socket; never uses a model or real credential. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

const base = process.env.TWINTEAMS_UI_URL || 'http://127.0.0.1:5199';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDAAAAABJRU5ErkJggg==', 'base64');
let activePage; let activeBrowser;

async function fixture(page, locale = 'en', withGroup = false) {
  const owner = { id: 'owner-alex', owner_id: 'owner-alex', kind: 'human', name: 'AAA', initials: 'AA', color: '#7962bd' };
  let twin = { id: 'twin-alex', owner_id: 'owner-alex', kind: 'twin', name: "AAA's Duet", initials: 'AT', color: '#6dbdad', avatar_preset: null, avatar_url: null, profile_revision: 1 };
  let skills = [{ name: 'writer', description: 'Write clear documents', enabled: true, available: true, error: null }, { name: 'research', description: 'Gather context', enabled: false, available: true, error: null }];
  let credentials = [{ name: 'service-api', ref: 'service-api', description: 'Test service', keynames: ['TOKEN'] }];
  let requests = [{ id: 'request-1', owner_id: owner.id, chat_id: 'private-alex', origin_request_id: 'origin-1', name: 'service-api', ref: 'service-api', description: 'Service access', keynames: ['TOKEN'], reason: 'Connect the test service', status: 'pending', created_at: '2026-10-18T01:00:00Z' }];
  const memory = { key: 'MEMORY', content: 'Prefers concise answers.', version: '1'.padStart(40, '0'), updated_at: 1792285200000, sources: [{ session_id: 'session-reference', event_id: 'event-reference' }] };
  const memories = new Map([['MEMORY', memory]]); let memoryVersion = 1; let soul = { content: '', version: null }; let soulVersion = 0; let readFailure = false;
  const nextCommit = () => (++memoryVersion).toString(16).padStart(40, '0');
  let reset = {generation:0,status:'ready',reset_id:null,private_chat_id:'private-alex'}; let resetFailure = false; let resetDelay = 0; const resets = new Map(); let dreamStatus = 'scheduled';
  const calls = []; const sockets = []; let failSave = false;
  const peers = ['Maya', 'Lin'].flatMap((name,index) => [{ id:'owner-'+name.toLowerCase(),owner_id:'owner-'+name.toLowerCase(),kind:'human',name,initials:name.slice(0,1),color:index ? '#c7885b' : '#368b71' },{ id:'twin-'+name.toLowerCase(),owner_id:'owner-'+name.toLowerCase(),kind:'twin',name:name+' Duet',initials:name.slice(0,1)+'T',color:'#678bbb' }]);
  const group = {id:'group-shared',kind:'group',title:'Shared team',member_owner_ids:[owner.id,'owner-maya','owner-lin'],unread_count:0,last_message:'Group content kept'};
  const chat = () => ({ id: 'private-alex', kind: 'private', title: twin.name, member_owner_ids: [owner.id], unread_count: 0, revision: 1 });
  const reply = (route, data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
  await page.addInitScript(value => { localStorage.setItem('twinteams.locale', value); }, locale);
  await page.routeWebSocket('**/api/ws', socket => { sockets.push(socket); socket.onMessage(raw => { const frame = JSON.parse(String(raw)); if (frame.type === 'command') socket.send(JSON.stringify({ v: 1, type: 'ack', command_id: frame.command_id })); }); });
  await page.route('**/api/**', async route => {
    const request = route.request(); const url = new URL(request.url()); const path = url.pathname; if (!path.startsWith('/api/')) return route.continue(); const method = request.method(); const body = request.postData(); calls.push({ path, method, body });
    if (path === '/api/config') return reply(route, { development_mode: true, model_configured: true });
    if (path === '/api/auth/me') return reply(route, { owner, current_owner_id: owner.id, development_mode: true });
    if (path === '/api/local-provider-config') return reply(route, { available: false });
    if (path === '/api/bootstrap') return reply(route, { current_owner_id: owner.id, actors: [owner, twin, ...(withGroup ? peers : [])], chats: [chat(), ...(withGroup ? [group] : [])], reminders: [], model_configured: true, development_mode: true });
    if (path === '/api/chats/group-shared') return reply(route,{revision:1,chat:group,messages:[],requests:[],approvals:[],tracking:[],settings:{reply_for_me_enabled:false,prompt_override:null,sidebar_open:false,sidebar_tab:'workspace'},members:[owner,twin,...peers]});
    if (path === '/api/chats/private-alex') return reply(route, { revision: 1, chat: chat(), messages: [], requests: [], approvals: [], tracking: [], settings: { reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'activity' }, members: [owner, twin] });
    if (path === '/api/me/twin' && method === 'GET') return reply(route, { actor: twin });
    if (path === '/api/me/twin' && method === 'PATCH') { twin = { ...twin, ...JSON.parse(body), profile_revision: twin.profile_revision + 1 }; return reply(route, twin); }
    if (path === '/api/me/twin/avatar') { if (method === 'POST') { const bytes = request.postDataBuffer(); assert.equal(bytes.readUInt32BE(16), 256); assert.equal(bytes.readUInt32BE(20), 256); } twin = { ...twin, avatar_preset: null, avatar_url: method === 'POST' ? `/api/actors/${twin.id}/avatar?v=3` : null, profile_revision: twin.profile_revision + 1 }; return reply(route, twin); }
    if (path === `/api/actors/${twin.id}`) return reply(route, twin);
    if (path === `/api/actors/${twin.id}/avatar`) return route.fulfill({ contentType: 'image/png', body: png });
    if (path === '/api/me/twin/reset') { if (method === 'GET') return reply(route, reset); const data = JSON.parse(body); if (resetDelay) await new Promise(resolve => setTimeout(resolve, resetDelay)); if (resetFailure) return reply(route,{detail:'Synthetic reset failure'},503); if (reset.status === 'pending' && data.idempotency_key === reset.idempotency_key) {reset={...reset,status:'completed'};memories.clear();resets.set(data.idempotency_key,reset);return reply(route,reset);} if (resets.has(data.idempotency_key)) return reply(route,resets.get(data.idempotency_key)); if (data.expected_generation !== reset.generation) return reply(route,{detail:'reset_conflict',current:reset},409); reset={...reset,generation:reset.generation+1,status:'completed',reset_id:data.idempotency_key}; resets.set(data.idempotency_key,reset); memories.clear(); sockets.at(-1)?.send(JSON.stringify({v:1,type:'personal_changed',section:'reset'})); return reply(route,reset); }
    if (path === '/api/chats/private-alex/summary') return reply(route,chat());
    if (path === '/api/me/twin/soul') { if (method === 'GET') return reply(route, soul); const data = method === 'DELETE' ? { content: '', expected_version: url.searchParams.get('expected_version') } : JSON.parse(body); if (data.expected_version !== soul.version) return reply(route, { detail: 'soul_conflict', current: soul }, 409); soul = { content: data.content, version: method === 'DELETE' ? null : (++soulVersion).toString(16).padStart(64, '0') }; return reply(route, soul); }
    if (path === '/api/me/twin/runtime') return reply(route, { configured: true, provider: 'openai', model: 'gpt-6-luna', api: 'openai-responses', observed_at: 1792285200, worker: { online: true, expires_at: Date.now() / 1000 + 180 }, memory: { enabled: true, mode: 'git' } });
    if (path === '/api/me/twin/skills') return reply(route, { items: skills });
    if (path === '/api/me/twin/skills/detail') return reply(route, { name: url.searchParams.get('name'), description: 'Write clear documents', content: '# Writer\nFollow the requested style.', references: ['references/style.md'] });
    if (path.startsWith('/api/me/twin/skills/') && method === 'PATCH') { const name = decodeURIComponent(path.split('/').pop()); skills = skills.map(item => item.name === name ? { ...item, ...JSON.parse(body) } : item); return reply(route, skills.find(item => item.name === name)); }
    if (path === '/api/me/twin/memory/dream') { const status = dreamStatus === 'queued' ? 'already_queued' : 'queued'; dreamStatus = 'queued'; return reply(route,{status,request_id:'fake-dream'}); }
    if (path === '/api/me/twin/memory/status') return reply(route, { enabled: true, mode: 'git', dream: { status: dreamStatus, due_at: '2026-10-19T01:00:00Z', last_success_at: '2026-10-18T01:00:00Z', last_error: null } });
    if (path === '/api/me/twin/memory') return readFailure ? reply(route, { detail: 'Legacy memory requires explicit Git migration' }, 503) : reply(route, { enabled: true, items: [...memories.values()], next_cursor: null });
    if (path === '/api/me/twin/memory/search') return reply(route, { enabled: true, items: [...memories.values()].filter(item => (item.key + item.content).toLowerCase().includes((url.searchParams.get('q') || '').toLowerCase())).map(item => ({ ...item, snippet: item.content })) });
    if (path === '/api/me/twin/memory/entry') { const key = url.searchParams.get('key'); const current = memories.get(key) || null; if (method === 'GET') return current ? reply(route, current) : reply(route, { detail: 'Memory not found' }, 404); const data = method === 'DELETE' ? { expected_version: url.searchParams.get('expected_version') } : JSON.parse(body); if (data.expected_version !== (current?.version ?? null)) return reply(route, { detail: 'memory_conflict', current }, 409); if (method === 'DELETE') { memories.delete(key); return reply(route, { deleted: true, key }); } const updated = { key, content: data.content, version: nextCommit(), sources: current?.sources || [], updated_at: Date.now() }; memories.set(key, updated); return reply(route, updated); }
    if (path === '/api/me/twin/credentials' && method === 'GET') return reply(route, { configured: true, credentials });
    if (path.startsWith('/api/me/twin/credentials/') && method === 'PUT') {
      if (failSave) return reply(route, { detail: 'Offline test rejection' }, 503);
      const name = decodeURIComponent(path.split('/').pop()); const data = JSON.parse(body); assert.ok(data.values); const item = { name, ref: name, description: data.description || '', keynames: Object.keys(data.values) }; credentials = [...credentials.filter(value => value.name !== name), item]; return reply(route, item);
    }
    if (path.startsWith('/api/me/twin/credentials/') && method === 'DELETE') { credentials = credentials.filter(item => item.name !== decodeURIComponent(path.split('/').pop())); return reply(route, { ok: true }); }
    if (path === '/api/me/twin/credential-requests') return reply(route, { configured: true, requests });
    if (path.endsWith('/request-1/complete')) { const data = JSON.parse(body); assert.equal(data.existing_name, 'service-api'); assert.equal(data.values, undefined); requests = requests.map(item => ({ ...item, status: 'fulfilled' })); return reply(route, requests[0]); }
    if (path === '/api/presence') return reply(route, { ok: true });
    return reply(route, { detail: `Unexpected offline route: ${method} ${path}` }, 404);
  });
  return { actors: [owner,twin,...peers], group, chat: chat(), calls, sockets, resetFailure: value => { resetFailure = value; }, resetDelay: value => { resetDelay = value; }, pendingReset: () => {reset={...reset,generation:reset.generation+1,status:'pending',reset_id:'pending-operation',idempotency_key:'pending-original-key',expected_generation:reset.generation};}, remoteReset: () => { reset={...reset,generation:reset.generation+1,status:'completed',reset_id:'remote-reset'}; memories.clear(); sockets.at(-1)?.send(JSON.stringify({v:1,type:'personal_changed',section:'reset'})); }, readFailure: value => { readFailure = value; }, remoteSoul: (content, notify = false) => { soul = { content, version: (++soulVersion).toString(16).padStart(64, '0') }; if (notify) sockets.at(-1)?.send(JSON.stringify({ v: 1, type: 'personal_changed', section: 'soul' })); return soul; }, remoteMemory: (key, content, notify = false) => { const current = memories.get(key); const updated = { ...(current || memory), key, content, version: nextCommit() }; memories.set(key, updated); if (notify) sockets.at(-1)?.send(JSON.stringify({ v: 1, type: 'personal_changed', section: 'memory' })); return updated; }, getMemory: key => memories.get(key), rename: name => { twin = { ...twin, name, profile_revision: twin.profile_revision + 1 }; sockets.at(-1)?.send(JSON.stringify({ v: 1, type: 'actor_changed', actor_id: twin.id, profile_revision: twin.profile_revision })); }, failSave: value => { failSave = value; } };
}

async function run() {
  const browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
  activeBrowser = browser;
  const context = await browser.newContext({ viewport: { width: 1360, height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage(); activePage = page; const errors = []; page.on('pageerror', error => { errors.push(error.message); console.error('Browser error:', error.message); });
  const mock = await fixture(page);
  await page.goto(`${base}/#chat`);
  await page.getByRole('button', { name: 'Account', exact: true }).click();
  await page.getByRole('menuitem', { name: 'My Duet' }).click();
  await page.getByLabel('Duet name', { exact: true }).fill('Orbit Twin');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByText('Saved', { exact: true }).waitFor();
  assert.equal(await page.locator('.account-name').innerText(), 'AAA');
  await page.getByRole('button', { name: 'Choose avatar', exact: true }).click();
  await page.getByRole('button', { name: 'Default avatar 3', exact: true }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  assert.ok(mock.calls.some(call => call.method === 'PATCH' && call.body.includes('"avatar_preset":"wave"')));
  await page.getByRole('button', { name: 'Choose avatar', exact: true }).click();
  await page.getByRole('dialog').locator('input[type=file]').setInputFiles({ name: 'too-large.png', mimeType: 'image/png', buffer: Buffer.alloc(5 * 1024 * 1024 + 1) });
  await page.getByRole('dialog').getByText('Choose a supported static image up to 5 MB.', { exact: true }).waitFor();
  await page.getByRole('dialog').locator('input[type=file]').setInputFiles({ name: 'offline.png', mimeType: 'image/png', buffer: png });
  await page.getByLabel('Zoom', { exact: true }).waitFor();
  await page.getByLabel('Zoom', { exact: true }).fill('1.5');
  await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  assert.ok(mock.calls.some(call => call.path === '/api/me/twin/avatar' && call.method === 'POST'));
  await page.getByRole('button', { name: 'Remove avatar', exact: true }).click();
  await page.getByRole('button', { name: 'Remove avatar', exact: true }).waitFor({ state: 'hidden' });
  mock.rename('Live Twin'); await page.locator('.twin-page-title p').filter({ hasText: 'Live Twin' }).waitFor();
  await page.getByRole('tab', { name: 'Runtime', exact: true }).click();
  await page.getByText('gpt-6-luna', { exact: true }).waitFor();
  await page.getByText('openai-responses', { exact: true }).waitFor();
  assert.equal(await page.locator('.twin-detail-fields').innerText().then(text => text.includes('/Users/')), false);
  await page.getByRole('tab', { name: 'Skills', exact: true }).click();
  await page.getByRole('switch', { name: 'Enable writer' }).waitFor();
  const beforeSearch = mock.calls.filter(call => call.path === '/api/me/twin/skills').length;
  await page.getByLabel('Search Skills', { exact: true }).fill('gather');
  assert.equal(await page.getByRole('switch').count(), 1);
  assert.equal(mock.calls.filter(call => call.path === '/api/me/twin/skills').length, beforeSearch);
  await page.getByRole('switch', { name: 'Enable research' }).click();
  await page.getByRole('switch', { name: 'Enable research', checked: true }).waitFor();
  await page.getByRole('button', { name: 'Details research' }).click();
  await page.getByRole('dialog').getByText('references/style.md', { exact: true }).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('tab', { name: 'Memory', exact: true }).click();
  await page.getByRole('button').filter({ has: page.getByRole('heading', { name: 'MEMORY', exact: true }) }).click();
  await page.getByRole('dialog').getByText('session: session-reference', { exact: true }).waitFor();
  assert.equal(await page.getByRole('dialog').getByRole('link').count(), 0);
  await page.keyboard.press('Escape');
  await page.getByLabel('Search saved memory', { exact: true }).fill('concise');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.getByText('Prefers concise answers.', { exact: true }).waitFor();
  await page.getByRole('tab', { name: 'Credentials', exact: true }).click();
  await page.getByRole('button', { name: 'Provide credential', exact: true }).click();
  await page.getByRole('button', { name: 'Use saved credential', exact: true }).click();
  await page.getByLabel('Choose a saved credential', { exact: true }).selectOption('service-api');
  await page.getByRole('button', { name: 'Save and provide', exact: true }).click();
  await page.getByRole('dialog').waitFor({ state: 'hidden' });
  await page.getByRole('button', { name: 'Add credential', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('form>label input').first().fill('demo-service');
  await dialog.locator('.twin-secret-row input:not([type=password])').fill('TOKEN');
  await dialog.locator('input[type=password]').fill('OFFLINE_TEST_ONLY_VALUE');
  mock.failSave(true);
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.getByText('Offline test rejection', { exact: true }).waitFor();
  assert.equal(await dialog.locator('input[type=password]').inputValue(), '');
  mock.failSave(false);
  await dialog.locator('input[type=password]').fill('OFFLINE_TEST_ONLY_VALUE');
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  await page.getByRole('heading', { name: 'demo-service', exact: true }).waitFor();
  assert.equal(await page.evaluate(() => JSON.stringify(localStorage).includes('OFFLINE_TEST_ONLY_VALUE')), false);
  assert.equal(mock.calls.filter(call => call.path.includes('/messages')).length, 0);
  await page.getByRole('button', { name: 'Delete credential demo-service' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
  await page.getByRole('heading', { name: 'demo-service', exact: true }).waitFor({ state: 'hidden' });
  await page.getByRole('tab', { name: 'Credentials', exact: true }).focus(); await page.keyboard.press('Home');
  assert.equal(await page.getByRole('tab', { name: 'Profile', exact: true }).getAttribute('aria-selected'), 'true');
  await page.screenshot({ path: '/tmp/twinteams-my-twin-desktop.png', fullPage: true });
  const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' }); const mobilePage = await mobile.newPage(); await fixture(mobilePage, 'zh-CN');
  await mobilePage.goto(`${base}/#/my-twin/profile`); await mobilePage.getByLabel('Duet name', { exact: true }).waitFor();
  assert.equal(await mobilePage.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await mobilePage.getByRole('button', { name: 'Choose avatar', exact: true }).click();
  assert.equal(await mobilePage.getByRole('dialog').getByRole('button', { name: /^Default avatar/ }).count(), 6);
  const modalBounds = await mobilePage.getByRole('dialog').boundingBox(); assert.ok(modalBounds.x >= 0 && modalBounds.x + modalBounds.width <= 390);
  await mobilePage.screenshot({ path: '/tmp/twinteams-my-twin-mobile.png', fullPage: true });
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('PASS: offline profile/avatar presets/crop/upload/remove/size limit, actor notification, runtime, Skills local search/toggle/details, memory search/references, credential request/create/delete/value clearing, English with legacy locale preferences, keyboard and mobile layout.');
}
module.exports = { fixture };
if (require.main === module) run().catch(async error => { console.error(error); if (activePage) { console.error((await activePage.locator('body').innerText()).slice(0, 2000)); await activePage.screenshot({ path: '/tmp/twinteams-my-twin-error.png' }).catch(() => {}); } await activeBrowser?.close(); process.exitCode = 1; });
