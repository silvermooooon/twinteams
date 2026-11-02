/** Offline smoke for the real scoped Continue browser helper. */
const { chromium } = require('playwright');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { assert, login, privateChat } = require('./helpers.cjs');
const { continueHeldConsultation } = require('./story-workflow-live.cjs');

async function main() {
  const instance = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const seed = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
  assert.equal(instance.TWINTEAMS_E2E_SCENARIO, 'continue-ui-smoke');
  assert.equal(instance.TWINTEAMS_E2E_MODEL_MODE, 'deterministic-offline');
  Object.assign(process.env, instance);
  function inspect() {
    return JSON.parse(execFileSync(instance.TWINTEAMS_E2E_PYTHON, [
      path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'),
      'inspect', '--state', instance.TWINTEAMS_E2E_STATE,
    ], { encoding: 'utf8' }));
  }
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(instance.TWINTEAMS_E2E_URL);
    await login(page, 'DEV-BBB');
    const chat = await privateChat(page);
    if (!(await page.getByRole('tab', { name: 'Duets', exact: true }).count())) {
      await page.getByRole('button', { name: 'More tools', exact: true }).click();
      await page.getByRole('menuitem', { name: 'Duets', exact: true }).click();
    }
    await page.getByRole('tab', { name: 'Duets', exact: true }).click();
    await page.locator('.duet-conversation-card').filter({ hasText: 'Previous BA collaboration' }).click();
    await page.getByRole('button', { name: 'Back to Duet conversations', exact: true }).waitFor();
    await page.screenshot({ path: path.join(instance.TWINTEAMS_E2E_ARTIFACT_DIR, 'before.png') });
    const grants = [];
    const t = { record(name, value) { fs.writeFileSync(path.join(instance.TWINTEAMS_E2E_ARTIFACT_DIR, `${name}.json`), JSON.stringify(value, null, 2)); } };
    const before = inspect();
    assert.equal(before.workflow.messages.find(row => row.id === seed.held)?.state, 'pending');
    assert.ok(await continueHeldConsultation({ page, chat }, t, grants, before, seed.chain));
    const after = inspect();
    assert.equal(after.workflow.messages.find(row => row.id === seed.held)?.state, 'published');
    assert.equal(grants.length, 1);
    assert.equal(grants[0].chat, seed.target);
    assert.equal(grants[0].after.allowance, 12);
    assert.ok(grants[0].after.published_message_ids.includes(seed.held));
    await page.screenshot({ path: path.join(instance.TWINTEAMS_E2E_ARTIFACT_DIR, 'after.png') });
    console.log('PASS: real owner Continue released the held service message in the intended chain.');
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
