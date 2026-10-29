/** Isolated, mocked component regression: no application DB, provider, or production server. */
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');
const net = require('node:net');
const path = require('node:path');
async function main() {
  const port = await new Promise(resolve => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const port = s.address().port; s.close(() => resolve(port)); }); });
  const origin = `http://127.0.0.1:${port}`;
  // A deliberately unusable backend makes accidental non-intercepted API access fail closed.
  const vite = spawn(process.execPath, [path.resolve(__dirname, '../node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: path.resolve(__dirname, '..'), env: { ...process.env, TWINTEAMS_API_URL: 'http://127.0.0.1:1' }, stdio: ['ignore', 'pipe', 'pipe'] });
  let browser;
  try {
    await new Promise((resolve, reject) => { const timer = setTimeout(() => reject(Error('Vite startup timed out')), 15000); vite.stdout.on('data', data => { if (data.toString().includes('Local:')) { clearTimeout(timer); resolve(); } }); vite.once('exit', code => { clearTimeout(timer); reject(Error(`Vite exited: ${code}`)); }); });
    browser = await chromium.launch({ headless: true, channel: process.env.TWINTEAMS_BROWSER_CHANNEL || 'chrome' });
    const page = await browser.newPage({ viewport: { width: 1280, height: 1100 } });
    const failures = []; page.on('pageerror', error => failures.push(error.message));
    let releaseCancel; let draftCancelled = false;
    let cancellations = [], decisions = [], continues = [], retries = [], revoked = false, closed = false, status = 'pending', reads = 0, summaryReads = 0;
    const now = '2026-10-26T08:00:00Z';
    const approvals = () => [0, 1, 2].map(i => ({ id: `approval-${i}`, type: 'duet_conversation', chat_id: 'duet-1', approver_id: `owner-${i}`, title: 'Approve planning collaboration', description: 'Coordinate the launch plan.', target: 'Planning', impact: 'Share the conversation with the recipient Duets.', status: i === 0 && decisions.length ? decisions.at(-1).decision : i === 1 ? 'approved' : 'pending', scope: i === 0 && decisions.length ? decisions.at(-1).scope : 'once', peer_owner_ids: [0, 1, 2].filter(peer => peer !== i).map(peer => `owner-${peer}`), created_at: now }));
    const conversation = () => ({ id: 'collaboration-1', chat_id: 'duet-1', kind: 'duet', title: 'Launch planning collaboration', origin_chat_id: 'pair', initiating_owner_id: 'owner-0', purpose: 'Agree on launch dates.', opening_message: 'Compare everyone’s **availability**.', status: closed ? 'closed' : status, participant_owner_ids: ['owner-0', 'owner-1', 'owner-2'], approvals: approvals(), created_at: now, can_close: true });
    const message = (id, seq) => ({ id, chat_id: 'duet-1', seq, author_id: id === 'latest' ? 'twin-0' : 'twin-1', author_owner_id: id === 'latest' ? 'owner-0' : 'owner-1', kind: 'twin', body: id === 'older' ? 'Earlier planning context' : 'A **shared** launch plan', created_at: now, read_by: [], state: 'published' });
    await page.route('**/api/**', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname; if (!p.startsWith('/api/')) return route.continue(); let body = {};
      if (p.startsWith('/api/reply-requests/') && p.endsWith('/cancel')) { cancellations.push(p); if (p.includes('/new-task/')) await new Promise(resolve => { releaseCancel = resolve; }); }
      else if (p === '/api/chats/duet-1/summary') { summaryReads++; body = { id: 'duet-1', kind: 'duet', title: 'Launch planning collaboration', member_owner_ids: ['owner-0','owner-1','owner-2'], unread_count: 0 }; }
      else if (p === '/api/duet-conversations') { reads++; body = { conversations: [conversation(), { ...conversation(), id: 'rejected', chat_id: 'rejected', title: 'Declined collaboration', status: 'rejected', approvals: [] }] }; }
      else if (p === '/api/duet-conversations/duet-1') body = { revision: 1, chat: { id: 'duet-1', kind: 'duet', title: 'Launch planning collaboration', member_owner_ids: ['owner-0', 'owner-1', 'owner-2'], unread_count: 0 }, conversation: conversation(), messages: url.searchParams.has('before_seq') ? [message('older',1)] : [message('latest',2),message('peer',3)], has_more: !url.searchParams.has('before_seq'), before_seq: url.searchParams.has('before_seq') ? 1 : 2, approvals: approvals(), members: [], requests: status === 'active' ? [0, 1].map(i => ({ id: `failed-${i}`, chat_id: 'duet-1', target_twin_id: `twin-${i}`, source: 'duet', status: 'failed', can_retry: true, can_cancel: false, error: 'Fixture failure', accepted_at: now })) : [], tracking: [], settings: { sidebar_open: true, sidebar_tab: 'duets' }, send_budgets: status === 'active' ? [{ causal_chain_id: 'chain-1', spent: 6, allowance: 6, remaining: 0, pending_count: 2, continuation_allowance: 6, can_continue: true }] : [], pending_messages: status === 'active' && !draftCancelled ? [{ ...message('draft',null), author_id: 'twin-0', body: 'Prepared private draft', state: 'pending', causal_chain_id: 'chain-1', can_cancel: true }] : [] };
      else if (p.startsWith('/api/approvals/')) { decisions.push(req.postDataJSON()); body = { status: 'approved' }; }
      else if (p === '/api/duet-conversations/duet-1/pending-messages/draft' && req.method() === 'DELETE') { draftCancelled = true; }
      else if (p.endsWith('/continue')) { continues.push({ path: p, body: req.postDataJSON() }); }
      else if (p.endsWith('/retry')) { retries.push(p); }
      else if (p.endsWith('/close')) { closed = true; body = conversation(); }
      else if (p === '/api/duet-authorizations') body = { authorizations: revoked ? [] : [{ peer_owner_id: 'owner-1', peer_twin_id: 'twin-1', peer_name: 'Blair Duet', created_at: now }] };
      else if (p === '/api/duet-authorizations/owner-1' && req.method() === 'DELETE') { revoked = true; body = { revoked: true, peer_owner_id: 'owner-1', closed_chat_ids: ['duet-1'] }; }
      else if (p.endsWith('/workspace')) body = [];
      else { failures.push(`Unexpected fixture API request: ${req.method()} ${p}`); return route.fulfill({ status: 500, body: '{}' }); }
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    });
    await page.goto(`${origin}/e2e/duet-ui.html?tasks=1`);
    const taskRows = page.locator('.private-task-status');
    await page.locator('[data-request-id="old-task"]').waitFor();
    assert.equal(await taskRows.count(), 5, 'Both running tasks and the queued task are visible; quiet background work stays in Activity');
    for (const id of ['old-task', 'new-task']) assert.equal(await page.locator(`[data-request-id="${id}"]`).getAttribute('data-reply-phase'), 'running');
    assert.equal(await page.locator('[data-request-id="waiting-task"]').getAttribute('data-reply-phase'), 'preparing');
    assert.ok((await page.locator('[data-request-id="old-task"]').innerText()).includes('Prepare the report'));
    assert.ok((await page.locator('[data-request-id="new-task"]').innerText()).includes('What is two plus two?'));
    assert.equal(await page.getByRole('textbox', { name: 'Message', exact: true }).isEnabled(), true);
    assert.ok((await page.locator('[data-request-id="dream-task"]').innerText()).includes('Memory review'));
    assert.equal(await page.locator('[data-request-id="dream-task"].background-task').count(), 1, 'Visible Dream keeps its own clock/status and Stop, without pretending to reply to a human input');
    assert.equal(await page.locator('[data-request-id="dream-task"]').getByRole('button', { name: 'Stop replying', exact: true }).isEnabled(), true);
    assert.ok((await page.locator('[data-request-id="background-continuation"]').innerText()).includes('Background task'));
    assert.ok(!(await page.locator('[data-request-id="background-continuation"]').innerText()).includes('What is two plus two?'), 'Explicit background metadata survives a generic continuation source');
    await page.getByRole('button', { name: 'Reorder tasks', exact: true }).click();
    await page.locator('[data-request-id="new-task"]').getByRole('button', { name: 'Stop replying', exact: true }).click();
    await page.locator('[data-request-id="new-task"][data-reply-phase="stopping"]').waitFor();
    assert.equal(await page.locator('[data-request-id="old-task"]').getByRole('button', { name: 'Stop replying', exact: true }).isEnabled(), true);
    assert.equal(await page.locator('[data-request-id="new-task"]').getByRole('button', { name: 'Stop replying', exact: true }).isEnabled(), false);
    while (!releaseCancel) await new Promise(resolve => setImmediate(resolve));
    releaseCancel();
    await page.locator('[data-request-id="new-task"]').waitFor({ state: 'hidden' });
    assert.deepEqual(cancellations, ['/api/reply-requests/new-task/cancel']);
    assert.equal(await page.locator('[data-request-id="old-task"]').getAttribute('data-reply-phase'), 'running');
    await page.locator('[data-request-id="waiting-task"]').getByRole('button', { name: 'Cancel this request', exact: true }).click();
    await page.locator('[data-request-id="waiting-task"]').waitFor({ state: 'hidden' });
    assert.deepEqual(cancellations, ['/api/reply-requests/new-task/cancel', '/api/reply-requests/waiting-task/cancel']);
    await page.goto(`${origin}/e2e/duet-ui.html?originlink=1`);
    await page.getByRole('link', { name: 'From Launch planning collaboration', exact: true }).click();
    await page.getByRole('heading', { name: 'Own private Duet', exact: true }).first().waitFor();
    await page.getByText('Owner view · Read only', { exact: true }).waitFor();
    assert.equal(await page.locator('main').getAttribute('data-main-chat-kind'), 'private');
    assert.equal(await page.locator('.duet-conversations input,.duet-conversations textarea').count(), 0);
    assert.equal(summaryReads, 1, 'An unknown source ID is authorized and resolved before main-chat selection');
    await page.getByRole('button', { name: 'Open human chat', exact: true }).click();
    await page.getByRole('heading', { name: 'Human planning', exact: true }).waitFor();
    assert.equal(summaryReads, 1, 'Known human destinations preserve the direct fast path');
    await page.goto(`${origin}/e2e/duet-ui.html?approval=1`);
    await page.getByText('Opening request', { exact: true }).click();
    await page.getByText('Prepared opening for recipients.', { exact: true }).waitFor();
    assert.equal(await page.locator('.duet-opening strong').innerText(), 'opening', 'Prepared opening renders Markdown inside the private approval card');
    for (const source of ['pair', 'multi', 'private']) {
      await page.goto(`${origin}/e2e/duet-ui.html?origin=${source}${source === 'private' ? '&kind=private' : ''}`);
      await page.getByRole('tab', { name: 'Duets', exact: true }).click();
      await page.getByRole('button', { name: /Launch planning collaboration/ }).click();
      await page.getByText('Owner view · Read only', { exact: true }).waitFor();
      assert.equal(await page.locator('.duet-conversations textarea, .duet-conversations input').count(), 0);
      await page.getByRole('button', { name: 'Allow once', exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: 'Allow once', exact: true }).count(), 1);
      assert.equal(await page.getByRole('button', { name: 'Always allow', exact: true }).count(), 1);
      await page.locator('.duet-approval p').filter({ hasText: 'Recipient Duets: Blair Duet, Casey Duet' }).waitFor();
      await page.getByRole('button', { name: 'Load earlier messages', exact: true }).click();
      await page.getByText('Earlier planning context', { exact: true }).waitFor();
    }
    await page.getByRole('button', { name: 'Reject', exact: true }).click();
    await page.getByRole('button', { name: 'Allow once', exact: true }).waitFor({ state: 'hidden' });
    assert.deepEqual(decisions.at(-1), { decision: 'rejected' });
    decisions = []; await page.getByRole('button', { name: 'Refresh Duet conversations', exact: true }).click();
    await page.getByRole('button', { name: 'Allow once', exact: true }).waitFor();
    // Once and permanent consent produce distinct, explicit server scopes.
    await page.getByRole('button', { name: 'Allow once', exact: true }).click();
    await page.getByText('Allowed once', { exact: true }).first().waitFor();
    assert.deepEqual(decisions.at(-1), { decision: 'approved', scope: 'once' });
    decisions = []; await page.getByRole('button', { name: 'Refresh Duet conversations', exact: true }).click();
    await page.getByRole('button', { name: 'Always allow', exact: true }).click();
    await page.getByText('Always allowed', { exact: true }).waitFor();
    assert.deepEqual(decisions.at(-1), { decision: 'approved', scope: 'always' });
    status = 'active'; await page.getByRole('button', { name: 'Refresh Duet conversations', exact: true }).click();
    await page.getByRole('button', { name: 'Continue · 6 more messages', exact: true }).click();
    assert.equal(continues.length, 1); assert.deepEqual(Object.keys(continues[0].body).sort(), ['client_request_key', 'expected_allowance']); assert.equal(continues[0].body.expected_allowance, 6); assert.equal(continues[0].path, '/api/duet-conversations/duet-1/chains/chain-1/continue');
    assert.equal(await page.locator('.duet-transcript [data-failed-request]').count(), 0);
    assert.equal(await page.locator('[data-pending-message-id="draft"]').count(), 1);
    assert.equal(await page.getByText('Waiting to send · Not sent', { exact: true }).count(), 1);
    await page.getByRole('button', { name: 'Cancel unsent message', exact: true }).click();
    await page.locator('[data-pending-message-id="draft"]').waitFor({ state: 'hidden' });
    assert.equal(draftCancelled, true);
    assert.equal(await page.locator('[data-failed-request="failed-0"]').count(), 1, 'Only own Duet exposes request retry');
    await page.locator('[data-failed-request="failed-0"]').getByRole('button', { name: 'Retry', exact: true }).click();
    assert.deepEqual(retries, ['/api/reply-requests/failed-0/retry']);
    await page.getByRole('button', { name: 'Close Duet conversation', exact: true }).click();
    await page.getByText('Closed', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: /Continue ·/ }).count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Allow once', exact: true }).count(), 0);
    assert.equal(await page.locator('.duet-transcript').getByRole('button', { name: 'Retry', exact: true }).count(), 0, 'Closed transcripts expose no route retry');
    await page.goto(`${origin}/e2e/duet-ui.html?trusted=1`);
    await page.getByRole('button', { name: 'Revoke access for Blair Duet', exact: true }).click();
    await page.getByText('No trusted Duets yet', { exact: true }).waitFor(); assert.equal(revoked, true);
    assert.equal(failures.length, 0, failures.join('\n')); assert.ok(reads > 0);
    console.log('Duet UI regression passed: concurrent private running/queued status, quiet background separation, inherited background label, per-task Stop/Cancel through reorder and in-flight cancel, plus pair/multi/private sidebar, read-only transcript/history, all-owner decisions, once/always consent, own-only retry, peer-message allowance and private pending draft, close, permanent permission revocation.');
  } finally { await browser?.close(); vite.kill('SIGTERM'); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
