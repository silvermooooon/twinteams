/** Actual SDK writes/present_files, canonical messages, authorized byte previews.
 * Existing offline provider supplies deterministic tool choices, not fake UI data. */
const { assert, api, privateChat, sendMessage, poll } = require('./helpers.cjs');
const fs = require('node:fs/promises');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const modal = page => page.locator('.modal[role="dialog"]');
const fileCard = (page, id) => page.locator(`[data-attachment-id="${id}"]`);
async function download(page, link, name) {
  const [result] = await Promise.all([page.waitForEvent('download'), link.click()]);
  assert.equal(result.suggestedFilename(), name);
  return fs.readFile(await result.path());
}
async function bytes(page, url) {
  const response = await page.request.get(new URL(url, page.url()).href);
  assert.equal(response.status(), 200);
  assert.equal(response.headers()['x-content-type-options'], 'nosniff');
  return response.body();
}
function inspect() {
  const result = JSON.parse(execFileSync(process.env.TWINTEAMS_E2E_PYTHON, [path.resolve(__dirname, '../../backend/checks/browser_e2e_fixture.py'), 'inspect', '--state', process.env.TWINTEAMS_E2E_STATE], { encoding: 'utf8' }));
  assert.equal(result.browser_e2e, true);
  assert.equal(result.e2e_instance_id, process.env.TWINTEAMS_E2E_INSTANCE_ID);
  return result;
}
async function closePreview(page) {
  await modal(page).getByRole('button', { name: 'Close', exact: true }).click();
  await modal(page).waitFor({ state: 'hidden' });
}
const cases = [
  {
    name: 'file delivery / real SDK files preview safely, interactive HTML runs isolated, snapshots survive source edits',
    run: async t => {
      assert.equal(process.env.TWINTEAMS_E2E_SCENARIO, 'file-delivery-preview');
      const { page } = await t.session();
      const chat = await privateChat(page), label = t.unique('delivery');
      const previewRequests = [];
      page.on('request', request => { if (request.method() === 'GET' && new URL(request.url()).pathname.endsWith('/preview')) previewRequests.push(new URL(request.url()).pathname); });
      await sendMessage(page, `E2E FILE_PREVIEW ${label}`);
      const delivered = await poll(async () => (await api(page, '/chats/' + chat.id)).messages.find(message => message.kind === 'twin' && message.attachments?.length === 4 && message.attachments.some(file => file.name === label + '.html')), 'published Duet file delivery', { timeout: 45000 });
      const row = page.locator(`article[data-message-id="${delivered.id}"]`);
      await row.waitFor();
      const byExtension = Object.fromEntries(delivered.attachments.map(file => [file.name.split('.').pop(), file]));
      assert.deepEqual(Object.fromEntries(Object.entries(byExtension).map(([extension, file]) => [extension, file.preview_kind])), { md: 'markdown', txt: 'text', pdf: 'pdf', html: 'html' });
      for (const file of delivered.attachments) { assert.ok(file.preview_url); await fileCard(page, file.id).waitFor(); }
      assert.equal(previewRequests.length, 0, 'file byte previews load only on a user action');
      const operation = await poll(() => inspect().tools.find(tool => tool.name === 'present_files' && tool.success && tool.request_id === delivered.request_id && tool.result_ids.message_id === delivered.id), 'actual successful SDK present_files operation');
      const originalMD = await bytes(page, byExtension.md.url);
      await fileCard(page, byExtension.md.id).getByRole('button', { name: `Preview ${byExtension.md.name}`, exact: true }).click();
      await modal(page).getByRole('heading', { name: 'Delivery ' + label, exact: true }).waitFor();
      await modal(page).locator('strong').filter({ hasText: 'Verified content' }).waitFor();
      assert.equal(await modal(page).locator('script').count(), 0);
      assert.equal(await page.evaluate(() => window.__previewInjected), undefined);
      assert.deepEqual(await download(page, modal(page).getByRole('link', { name: 'Download', exact: true }), byExtension.md.name), originalMD);
      await page.screenshot({ path: path.join(t.artifactDir, 'file-preview-markdown.png'), fullPage: true });
      await closePreview(page);
      await fileCard(page, byExtension.txt.id).getByRole('button', { name: `Preview ${byExtension.txt.name}`, exact: true }).click();
      const text = modal(page).locator('.attachment-text-preview');
      await text.filter({ hasText: 'TEXT_' + label }).waitFor();
      assert.ok((await text.innerText()).includes('<script>window.__previewInjected=true</script>'));
      assert.equal(await modal(page).locator('script').count(), 0);
      assert.equal(await page.evaluate(() => window.__previewInjected), undefined);
      await closePreview(page);
      await fileCard(page, byExtension.pdf.id).getByRole('button', { name: `Preview ${byExtension.pdf.name}`, exact: true }).click();
      const pdf = modal(page).getByTitle(`PDF preview ${byExtension.pdf.name}`, { exact: true });
      await pdf.waitFor({ state: 'visible' });
      const pdfURL = await pdf.getAttribute('src');
      assert.ok(pdfURL.startsWith('blob:'), 'PDF uses authorized fetched bytes');
      const loadedPDF = await page.evaluate(async url => { const response = await fetch(url); return { mime: response.headers.get('content-type'), body: await response.text() }; }, pdfURL);
      assert.equal(loadedPDF.mime, 'application/pdf');
      assert.ok(loadedPDF.body.startsWith('%PDF-') && loadedPDF.body.includes('PDF_' + label));
      const pdfBytes = await download(page, modal(page).getByRole('link', { name: 'Download', exact: true }), byExtension.pdf.name);
      assert.ok(pdfBytes.toString().includes('PDF_' + label));
      const pdfFrame = await (await pdf.elementHandle()).contentFrame();
      assert.ok(pdfFrame, 'Chrome opened the PDF frame');
      await pdfFrame.waitForLoadState('load');
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.screenshot({ path: path.join(t.artifactDir, 'file-preview-pdf.png'), fullPage: true });
      await page.keyboard.press('Escape');
      await modal(page).waitFor({ state: 'hidden' });
      assert.equal(await page.evaluate(async url => { try { await fetch(url); return false; } catch { return true; } }, pdfURL), true, 'closing PDF preview revokes its object URL');
      assert.equal(await fileCard(page, byExtension.pdf.id).getByRole('button', { name: `Preview ${byExtension.pdf.name}`, exact: true }).evaluate(element => element === document.activeElement), true, 'closing returns to the preview action rather than moving to the composer');
      const [viewer] = await Promise.all([page.waitForEvent('popup'), fileCard(page, byExtension.html.id).getByRole('button', { name: `Open ${byExtension.html.name} in new tab`, exact: true }).click()]);
      assert.equal(await viewer.evaluate(() => window.opener === null), true, 'wrapper is detached from the platform opener');
      const frameElement = await viewer.locator('iframe').elementHandle();
      const frame = await frameElement.contentFrame();
      await frame.getByRole('heading', { name: 'HTML_' + label, exact: true }).waitFor();
      await frame.getByRole('button', { name: 'Count: 0', exact: true }).click();
      await frame.getByRole('button', { name: 'Count: 1', exact: true }).waitFor();
      const cspDenials = [];
      viewer.on('console', message => { if (message.type() === 'error' && message.text().includes('Content Security Policy')) cspDenials.push(message.text()); });
      const boundary = await frame.evaluate(async origin => {
        const read = operation => { try { return { readable: true, value: operation() }; } catch (error) { return { readable: false, error: error.name }; } };
        const violations = [];
        document.addEventListener('securitypolicyviolation', event => violations.push({ directive: event.effectiveDirective, blocked: event.blockedURI }));
        const result = { origin: window.origin, cookie: read(() => document.cookie), localStorage: read(() => localStorage.length), sessionStorage: read(() => sessionStorage.length), parentDOM: read(() => parent.document.title), topStorage: read(() => top.localStorage.length), authFetch: null, violations };
        try { const response = await fetch(origin + '/api/auth/me', { credentials: 'include' }); result.authFetch = { readable: true, status: response.status, body: await response.text() }; } catch (error) { result.authFetch = { readable: false, error: error.name }; }
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        return result;
      }, t.baseURL);
      assert.equal(boundary.origin, 'null');
      for (const key of ['cookie', 'localStorage', 'sessionStorage', 'parentDOM', 'topStorage', 'authFetch']) assert.equal(boundary[key].readable, false, key + ' is inaccessible to the interactive document');
      // Chrome logs this enforced denial for opaque sandbox documents without
      // dispatching a securitypolicyviolation event into the document itself.
      assert.ok(cspDenials.some(message => message.includes(t.baseURL + '/api/auth/me') && message.includes("connect-src 'none'") && message.includes('blocked')), 'Chrome reports the actual auth fetch blocked by the enforced CSP');
      boundary.browserCSPDenials = cspDenials;
      assert.deepEqual(await download(viewer, viewer.getByRole('link', { name: 'Download', exact: true }), byExtension.html.name), await bytes(page, byExtension.html.url));
      await viewer.screenshot({ path: path.join(t.artifactDir, 'file-preview-html-interactive.png'), fullPage: true });
      await viewer.close();
      await sendMessage(page, `E2E FILE_PREVIEW_OVERWRITE ${label}`);
      await poll(async () => (await api(page, '/chats/' + chat.id)).messages.some(message => message.kind === 'twin' && message.body.includes('E2E file preview source overwritten: ' + label)), 'actual SDK source overwrite');
      const generated = await api(page, '/chats/' + chat.id + '/workspace?path=generated');
      const mutable = generated.find(file => file.name === byExtension.md.name);
      assert.ok(mutable);
      const source = await page.request.get(new URL(`/api/chats/${chat.id}/workspace/files/${encodeURIComponent(mutable.id)}/preview`, page.url()).href);
      assert.equal(source.status(), 200);
      assert.equal(await source.text(), 'Changed source: ' + label);
      await page.reload();
      await page.locator(`article[data-message-id="${delivered.id}"]`).waitFor();
      assert.deepEqual(await download(page, fileCard(page, byExtension.md.id).getByRole('link', { name: byExtension.md.name, exact: true }), byExtension.md.name), originalMD, 'published attachment remains the original snapshot after workspace rewrite and refresh');
      await fileCard(page, byExtension.md.id).getByRole('button', { name: `Preview ${byExtension.md.name}`, exact: true }).click();
      await modal(page).getByRole('heading', { name: 'Delivery ' + label, exact: true }).waitFor();
      await closePreview(page);
      const other = await t.session('DEV-BBB', { baseURL: t.secondaryURL });
      const denied = await other.page.request.get(new URL(byExtension.md.preview_url, t.secondaryURL).href);
      assert.ok([403, 404].includes(denied.status()), 'preview rechecks private access for another owner');
      t.record('delivery', { messageId: delivered.id, requestId: delivered.request_id, sdkOperation: operation.call_id, attachments: delivered.attachments.map(file => ({ id: file.id, name: file.name, previewKind: file.preview_kind })), initialPreviewRequests: 0, escapedTextAndMarkdown: true, pdfAuthorizedBlob: true, pdfURLRevoked: true, originalSnapshotAfterSourceEdit: true, foreignPreviewStatus: denied.status(), modelMode: 'deterministic-offline' });
      t.record('interactive_html_boundary', boundary);
    },
  },
  {
    name: 'file preview / real oversized uploads show errors with downloads, HTML errors are visible, ordinary files and images remain usable',
    run: async t => {
      const { page } = await t.session(); const chat = await privateChat(page), label = t.unique('fallback');
      const largeText = Buffer.from('TEXT_' + label + '\n' + 'a'.repeat(1024 * 1024 + 64));
      const largeHTML = Buffer.from('<!doctype html><h1>Large HTML</h1>' + 'a'.repeat(1024 * 1024 + 64));
      const archive = Buffer.from('Offline archive fixture ' + label);
      const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGO0CdjCwMDAxAAGAA6yAUTVYc/JAAAAAElFTkSuQmCC', 'base64');
      const files = [{ name: label + '.txt', mimeType: 'text/plain', buffer: largeText }, { name: label + '.html', mimeType: 'text/html', buffer: largeHTML }, { name: label + '.zip', mimeType: 'application/zip', buffer: archive }, { name: label + '.png', mimeType: 'image/png', buffer: png }];
      await page.locator('.chat-main > input[type=file]').first().setInputFiles(files);
      await poll(async () => (await page.locator('.draft-attachment.ready').count()) === 4, 'four real upload responses');
      await page.getByRole('button', { name: 'Send message', exact: true }).click();
      const message = await poll(async () => (await api(page, '/chats/' + chat.id)).messages.find(item => item.kind === 'human' && item.attachments?.some(file => file.name === files[0].name)), 'canonical uploaded files');
      const attachments = Object.fromEntries(message.attachments.map(file => [file.name.split('.').pop(), file]));
      await fileCard(page, attachments.txt.id).getByRole('button', { name: `Preview ${attachments.txt.name}`, exact: true }).click();
      await modal(page).getByRole('alert').waitFor();
      assert.ok(/download/i.test(await modal(page).getByRole('alert').innerText()));
      assert.deepEqual(await download(page, modal(page).getByRole('link', { name: 'Download', exact: true }), attachments.txt.name), largeText);
      await page.screenshot({ path: path.join(t.artifactDir, 'file-preview-size-error.png'), fullPage: true });
      await closePreview(page);
      const [viewer] = await Promise.all([page.waitForEvent('popup'), fileCard(page, attachments.html.id).getByRole('button', { name: `Open ${attachments.html.name} in new tab`, exact: true }).click()]);
      await viewer.getByRole('alert').waitFor();
      assert.ok(/download/i.test(await viewer.getByRole('alert').innerText()));
      assert.equal(await viewer.locator('iframe').count(), 0);
      assert.deepEqual(await download(viewer, viewer.getByRole('link', { name: 'Download', exact: true }), attachments.html.name), largeHTML);
      await viewer.screenshot({ path: path.join(t.artifactDir, 'file-preview-html-error.png'), fullPage: true });
      await viewer.close();
      assert.equal(attachments.zip.preview_kind, null);
      assert.equal(await fileCard(page, attachments.zip.id).getByRole('button').count(), 0);
      assert.deepEqual(await download(page, fileCard(page, attachments.zip.id).getByRole('link', { name: attachments.zip.name, exact: true }), attachments.zip.name), archive);
      await page.locator(`article[data-message-id="${message.id}"]`).getByTitle(`Preview ${attachments.png.name}`, { exact: true }).click();
      await poll(() => modal(page).locator('img').evaluate(image => image.complete && image.naturalWidth > 0), 'existing authorized image preview');
      await closePreview(page);
      t.record('fallbacks', { uploadMessageId: message.id, oversizedTextBytes: largeText.length, oversizedHTMLBytes: largeHTML.length, serverPreviewErrorsVisible: true, originalDownloadsVerified: true, htmlErrorNotBlank: true, ordinaryArchiveDownloadOnly: true, existingImagePreview: true });
    },
  },
];
module.exports = { cases };
