/** Resume a preserved story workflow using the same browser scenario tail. */
const { chromium } = require('playwright');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { assert, login, privateChat } = require('./helpers.cjs');
const { afterCapacity, afterDeveloperPlans, consultationSettled } = require('./story-workflow-live.cjs');

async function main() {
  const artifactDir = process.argv[2];
  const instance = JSON.parse(fs.readFileSync(path.join(artifactDir, 'instance.json'), 'utf8'));
  const progressName = fs.readdirSync(artifactDir).find(name => name.endsWith('-progress.json') && name !== 'continuation-progress.json');
  assert.ok(progressName, 'the original scenario progress is required');
  const original = JSON.parse(fs.readFileSync(path.join(artifactDir, progressName), 'utf8'));
  assert.ok(['--reviewed-capacity', '--reviewed-plans'].includes(process.argv[3]), 'select a reviewed continuation stage');
  const reviewedCapacity = process.argv[3] === '--reviewed-capacity';
  const reviewedPlans = process.argv[3] === '--reviewed-plans';
  assert.equal(instance.TWINTEAMS_E2E_SCENARIO, 'story-workflow-live');
  assert.equal(instance.TWINTEAMS_E2E_MODEL_MODE, 'live-gpt-6-luna');
  Object.assign(process.env, instance);
  const evidence = JSON.parse(execFileSync(instance.TWINTEAMS_E2E_PYTHON, [
    path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'), 'inspect', '--state', instance.TWINTEAMS_E2E_STATE,
  ], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }));
  assert.equal(evidence.e2e_instance_id, instance.TWINTEAMS_E2E_INSTANCE_ID);
  const url = new URL(instance.TWINTEAMS_E2E_URL);
  assert.ok(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
    && !['8000', '8001'].includes(url.port) && !url.username && !url.password
    && url.pathname === '/' && !url.search && !url.hash, 'continuation requires an isolated local API');
  const health = await fetch(new URL('/health', url), { signal: AbortSignal.timeout(10000) });
  assert.ok(health.ok, 'isolated API health check failed');
  const marker = await health.json();
  assert.equal(marker.browser_e2e, true);
  assert.equal(marker.e2e_instance_id, instance.TWINTEAMS_E2E_INSTANCE_ID);
  assert.equal(marker.model_mode, instance.TWINTEAMS_E2E_MODEL_MODE);
  assert.equal(marker.scenario, instance.TWINTEAMS_E2E_SCENARIO);
  const continuationPath = path.join(artifactDir, 'continuation-progress.json');
  const notes = reviewedPlans ? JSON.parse(fs.readFileSync(continuationPath, 'utf8')) : { originalProgress: progressName, checkpoints: {} };
  const t = { artifactDir, record(name, value) { notes.checkpoints[name] = value; fs.writeFileSync(path.join(artifactDir, 'continuation-progress.json'), JSON.stringify(notes, null, 2)); } };
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const sessions = {};
    for (const [name, code] of [['ba', 'DEV-AAA'], ['sm', 'DEV-BBB'], ['dev1', 'DEV-CCC'], ['dev2', 'DEV-DDD']]) {
      const page = await browser.newPage();
      await page.goto(instance.TWINTEAMS_E2E_URL);
      await login(page, code);
      sessions[name] = { page, chat: await privateChat(page), scope: name === 'dev2' ? 'always' : 'once' };
    }
    sessions.sm.allowedPeers = ['owner-jordan', 'owner-riley'];
    sessions.dev1.allowedPeers = ['owner-maya', 'owner-riley'];
    sessions.dev2.allowedPeers = ['owner-maya', 'owner-jordan'];
    const drafted = original.details.drafted;
    const revised = { files: original.details.revisions.after };
    const consultation = { tracking: evidence.workflow.tracking.filter(row => row.chat_id === sessions.sm.chat.id && ['active', 'completed'].includes(row.status)) };
    assert.ok(consultation.tracking.length, 'the original SM tracking topic remains present');
    if (reviewedPlans) {
      assert.ok(notes.checkpoints.assignments && notes.checkpoints.dev1_discussion && notes.checkpoints.dev2_discussion
        && Array.isArray(notes.checkpoints.before_developer_plans?.files), 'the prior continuation recorded both discussions and the original plan baseline');
      const ownerMessages = evidence.workflow.messages.filter(row => ['owner-jordan', 'owner-riley'].includes(row.author_id));
      assert.ok(['owner-jordan', 'owner-riley'].every(owner => ownerMessages.some(row => row.author_id === owner && row.body.includes('I accept responsibility for implementing'))), 'both owners accepted their assignments');
      const beforePlans = notes.checkpoints.before_developer_plans.files;
      t.record('resumed_developer_plans', { ownerMessages: ownerMessages.slice(-6) });
      await afterDeveloperPlans(t, sessions, { drafted, consultation, beforePlans });
    } else if (reviewedCapacity) {
      const review = JSON.parse(fs.readFileSync(path.join(artifactDir, 'reviewed-capacity.json'), 'utf8'));
      assert.equal(review.approved, true);
      assert.equal(review.report_ids.length, 2);
      const reports = review.report_ids.map(id => evidence.workflow.messages.find(row => row.id === id && row.chat_id === sessions.sm.chat.id && row.author_id === 'twin-maya'));
      assert.ok(reports.every(Boolean), 'both reviewed reports belong to the SM private chat');
      assert.ok(consultationSettled(evidence), 'the original consultation has settled');
      t.record('reviewed_capacity', { review, reports });
      await afterCapacity(t, sessions, { drafted, revised, consultation });
    }
    notes.status = 'completed';
    fs.writeFileSync(path.join(artifactDir, 'continuation-progress.json'), JSON.stringify(notes, null, 2));
    console.log('Completed the preserved story workflow continuation.');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error.stack || error.message); process.exitCode = 1; });
