import { useEffect, useState } from 'react';
import type { ChatAttachment } from './api/client';
import { MessageMarkdown } from './MessageMarkdown';
import { Icon, IconButton, Modal } from './ui';
import './FilePreview.css';

const TEXT_LIMIT = 1024 * 1024;
const PDF_LIMIT = 20 * 1024 * 1024;
// Scripts can run locally, but opaque sandboxed content gets no platform storage,
// network, forms, popups or top navigation. Policy precedes every untrusted byte.
const HTML_POLICY = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data: blob:; media-src data: blob:; connect-src 'none'; form-action 'none'; base-uri 'none'";
function authorizedURL(value: string | null | undefined) {
  if (!value) throw new Error('This file does not have a preview. Download it to view it.');
  const url = new URL(value, location.href);
  if (url.origin !== location.origin || !url.pathname.startsWith('/api/chats/')) throw new Error('This file link is not available.');
  return url.href;
}
async function previewBytes(attachment: ChatAttachment, signal: AbortSignal) {
  const response = await fetch(authorizedURL(attachment.preview_url), { credentials: 'same-origin', signal });
  if (!response.ok) {
    let message = `Could not load the preview (${response.status}). Download the file to view it.`;
    try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch { /* Keep the readable HTTP error. */ }
    throw new Error(message);
  }
  const limit = attachment.preview_kind === 'pdf' ? PDF_LIMIT : TEXT_LIMIT;
  if (Number(response.headers.get('Content-Length')) > limit) {
    await response.body?.cancel();
    throw new Error('This file is too large to preview. Download it to view it.');
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > limit) throw new Error('This file is too large to preview. Download it to view it.');
  return bytes;
}

/** Must be called directly from a user click: opening after fetch may be blocked.
 * Only trusted wrapper DOM is written here; file HTML is assigned to srcdoc. */
export function openHtmlPreview(attachment: ChatAttachment, onSettled: () => void): () => void {
  const downloadURL = authorizedURL(attachment.url);
  const popup = window.open('about:blank', '_blank');
  if (!popup) throw new Error('Allow pop-ups to open this HTML file in a new tab.');
  popup.opener = null;
  const controller = new AbortController();
  const doc = popup.document;
  doc.title = attachment.name;
  const style = doc.createElement('style');
  style.textContent = 'html,body{margin:0;height:100%;font:14px/1.5 system-ui,sans-serif;color:#242424;background:#fff}body{display:flex;flex-direction:column}header{display:flex;align-items:center;gap:20px;padding:12px 20px;border-bottom:1px solid #e0e0e0;background:#fafafa}header strong{flex:1;min-width:0;overflow-wrap:anywhere;font-size:14px;font-weight:600}a{color:#5b5fc7;text-decoration:none;border:1px solid #d1d1d1;border-radius:4px;padding:6px 12px;white-space:nowrap}a:hover{background:#eeedf7}a:focus-visible{outline:2px solid #5b5fc7;outline-offset:3px}main{flex:1;min-height:0;display:flex}iframe{display:block;width:100%;height:100%;border:0}p{padding:20px;margin:0;color:#616161}p[role=alert]{color:#a4262c}';
  doc.head.append(style);
  const header = doc.createElement('header');
  const title = doc.createElement('strong'); title.textContent = attachment.name;
  const download = doc.createElement('a'); download.textContent = 'Download'; download.download = attachment.name;
  download.href = downloadURL; download.rel = 'noopener noreferrer'; download.title = `Download ${attachment.name}`;
  header.append(title, download);
  const main = doc.createElement('main');
  const status = doc.createElement('p'); status.setAttribute('role', 'status'); status.textContent = 'Loading HTML preview…';
  main.append(status); doc.body.replaceChildren(header, main);
  popup.addEventListener('pagehide', () => controller.abort(), { once: true });
  void previewBytes(attachment, controller.signal).then(bytes => {
    if (controller.signal.aborted || popup.closed) return;
    const frame = doc.createElement('iframe');
    frame.title = `Interactive HTML · ${attachment.name}`;
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.referrerPolicy = 'no-referrer';
    frame.srcdoc = `<meta http-equiv="Content-Security-Policy" content="${HTML_POLICY}"><meta charset="utf-8">` + new TextDecoder('utf-8').decode(bytes);
    main.replaceChildren(frame);
  }).catch(failure => {
    if (popup.closed) return;
    status.setAttribute('role', 'alert');
    status.textContent = controller.signal.aborted ? 'Preview loading stopped. Open the file again from your conversation.' : failure instanceof Error ? failure.message : 'Could not load this HTML file. You can still download it.';
  }).finally(onSettled);
  return () => controller.abort();
}

export function FilePreview({ attachment, onClose }: { attachment: ChatAttachment; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null); const [pdf, setPdf] = useState<string | null>(null);
  const [error, setError] = useState(''); const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (attachment.kind === 'image') return;
    const controller = new AbortController(); let objectURL: string | null = null;
    setText(null); setPdf(null); setError('');
    previewBytes(attachment, controller.signal).then(bytes => {
      if (controller.signal.aborted) return;
      if (attachment.preview_kind === 'pdf') { objectURL = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' })); setPdf(objectURL); }
      else setText(new TextDecoder('utf-8').decode(bytes));
    }).catch(failure => { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not load the preview.'); });
    return () => { controller.abort(); if (objectURL) URL.revokeObjectURL(objectURL); };
  }, [attachment.id, attachment.preview_url, attachment.preview_kind, attachment.kind, retry]);
  return <Modal title={attachment.name} wide onClose={onClose}>
    <div className={`attachment-document-preview ${attachment.preview_kind || attachment.kind}`}>
      {attachment.kind === 'image' ? <div className="attachment-large-preview"><img src={attachment.url} alt={attachment.name} /></div> : error ? <div className="attachment-preview-error" role="alert"><Icon name="info" size={20} /><span>{error}</span><IconButton icon="refresh" label={`Retry preview ${attachment.name}`} onClick={() => setRetry(value => value + 1)} /></div> : pdf ? <iframe src={pdf} title={`PDF preview ${attachment.name}`} referrerPolicy="no-referrer" /> : text !== null ? attachment.preview_kind === 'markdown' ? <MessageMarkdown text={text} /> : <pre className="attachment-text-preview">{text}</pre> : <div className="attachment-preview-loading" role="status"><span className="spinner" />Loading preview…</div>}
    </div>
    <div className="modal-actions"><a href={attachment.url} download={attachment.name} className="secondary-button" title={`Download ${attachment.name}`}>Download</a></div>
  </Modal>;
}
