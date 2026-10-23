import { useCallback, useEffect, useRef, useState } from 'react';
import type { ApiClient, ChatAttachment } from './api/client';
import { t } from './i18n';
import { Icon, IconButton } from './ui';
import { FilePreview, openHtmlPreview } from './FilePreview';

export type AttachmentMetadata = ChatAttachment;
export type DraftAttachment = { localId: string; file: File; preview?: string; status: 'uploading' | 'ready' | 'failed'; attachment?: AttachmentMetadata; error?: string };
const MAX_FILES = 4; const MAX_BYTES = 20 * 1024 * 1024;
const previewable = (file: File) => ['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type);

/** Byte uploads stay on HTTP. Drafts and in-flight responses belong to their original owner/chat. */
export function useAttachmentDrafts(api: ApiClient, ownerId: string, chatId: string) {
  const [drafts, setDrafts] = useState<Record<string, DraftAttachment[]>>({}); const [error, setError] = useState('');
  const state = useRef(drafts); const controllers = useRef(new Map<string, AbortController>()); const previews = useRef(new Set<string>()); const activeOwner = useRef(ownerId); activeOwner.current = ownerId;
  const scope = `${ownerId}:${chatId}`;
  const update = useCallback((key: string, transform: (items: DraftAttachment[]) => DraftAttachment[]) => { const next = { ...state.current, [key]: transform(state.current[key] || []) }; state.current = next; setDrafts(next); }, []);
  const revoke = useCallback((item: DraftAttachment) => { if (item.preview) { URL.revokeObjectURL(item.preview); previews.current.delete(item.preview); } controllers.current.get(item.localId)?.abort(); controllers.current.delete(item.localId); }, []);
  useEffect(() => {
    setError(''); controllers.current.forEach(controller => controller.abort()); controllers.current.clear(); previews.current.forEach(url => URL.revokeObjectURL(url)); previews.current.clear(); state.current = {}; setDrafts({});
    return () => { controllers.current.forEach(controller => controller.abort()); controllers.current.clear(); previews.current.forEach(url => URL.revokeObjectURL(url)); previews.current.clear(); };
  }, [ownerId]);
  useEffect(() => { setError(''); }, [scope]);
  const upload = useCallback(async (key: string, targetChat: string, item: DraftAttachment) => {
    const owner = api.ownerId; const controller = new AbortController(); controllers.current.set(item.localId, controller);
    try {
      const attachment = await api.uploadAttachment(targetChat, item.file, controller.signal);
      if (controller.signal.aborted || activeOwner.current !== owner) return;
      update(key, items => items.map(previous => previous.localId === item.localId ? { ...previous, status: 'ready', attachment, error: undefined } : previous));
    } catch (cause) {
      if (controller.signal.aborted || activeOwner.current !== owner) return;
      update(key, items => items.map(previous => previous.localId === item.localId ? { ...previous, status: 'failed', error: cause instanceof Error ? cause.message : String(cause) } : previous));
    } finally { if (controllers.current.get(item.localId) === controller) controllers.current.delete(item.localId); }
  }, [api, update]);
  const ingest = useCallback((files: File[]) => {
    if (!ownerId || !chatId || !files.length) return;
    const existing = state.current[scope] || []; const fresh: DraftAttachment[] = []; let bytes = existing.reduce((sum, item) => sum + item.file.size, 0); let rejected = false;
    for (const file of files) {
      if ([...existing, ...fresh].some(item => item.file.name === file.name && item.file.size === file.size && item.file.lastModified === file.lastModified && item.file.type === file.type)) continue;
      if (!file.size || existing.length + fresh.length >= MAX_FILES || bytes + file.size > MAX_BYTES) { rejected = true; continue; }
      const preview = previewable(file) ? URL.createObjectURL(file) : undefined; if (preview) previews.current.add(preview);
      fresh.push({ localId: crypto.randomUUID(), file, preview, status: 'uploading' }); bytes += file.size;
    }
    setError(rejected ? t('attachments.limit') : '');
    if (!fresh.length) return;
    update(scope, items => [...items, ...fresh]); fresh.forEach(item => { void upload(scope, chatId, item); });
  }, [ownerId, chatId, scope, update, upload]);
  const remove = useCallback((id: string) => { const item = state.current[scope]?.find(item => item.localId === id); if (item) revoke(item); update(scope, items => items.filter(item => item.localId !== id)); }, [scope, revoke, update]);
  const retry = useCallback((id: string) => { const item = state.current[scope]?.find(item => item.localId === id); if (!item || item.status !== 'failed') return; update(scope, items => items.map(previous => previous.localId === id ? { ...previous, status: 'uploading', error: undefined } : previous)); void upload(scope, chatId, item); }, [scope, chatId, upload, update]);
  const clear = useCallback((ids: string[], key = scope) => { update(key, items => { items.filter(item => ids.includes(item.localId)).forEach(revoke); return items.filter(item => !ids.includes(item.localId)); }); }, [scope, revoke, update]);
  const clearScope = useCallback((key: string) => { update(key, items => { items.forEach(revoke); return []; }); }, [update, revoke]);
  return { items: drafts[scope] || [], ingest, remove, retry, clear, clearScope, scope, error, dismissError: () => setError('') };
}
function fileSize(bytes: number) { return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`; }
export function DraftAttachmentList({ items, error, onRemove, onRetry, onDismissError }: { items: DraftAttachment[]; error: string; onRemove: (id: string) => void; onRetry: (id: string) => void; onDismissError: () => void }) {
  return <>{error && <div className="attachment-ingest-error" role="alert"><Icon name="info" size={16} /><span>{error}</span><IconButton icon="close" size={14} label={t('common.dismiss')} onClick={onDismissError} /></div>}{!!items.length && <div className="draft-attachments" aria-label={t('attachments.drafts')}>{items.map(item => <div key={item.localId} className={`draft-attachment ${item.status}`}>
    {(item.preview && !item.attachment) || item.attachment?.kind === 'image' ? <img src={item.preview || item.attachment?.url} alt={item.file.name} className="attachment-thumbnail" /> : <span className="attachment-file-icon"><Icon name="file" size={28} /></span>}
    <div className="attachment-card-copy"><strong title={item.file.name}>{item.file.name}</strong><small>{fileSize(item.file.size)}</small><span className="attachment-upload-status" role="status">{item.status === 'uploading' ? <><span className="spinner" />{t('attachments.uploading')}</> : item.status === 'failed' ? <span className="attachment-error" title={item.error}>{t('attachments.uploadFailed')}</span> : t('attachments.ready')}</span>{item.status === 'failed' && <button className="text-button" onClick={() => onRetry(item.localId)}>{t('common.retry')}</button>}</div>
    <IconButton icon="close" size={14} label={t('attachments.remove', { name: item.file.name })} onClick={() => onRemove(item.localId)} />
  </div>)}</div>}</>;
}
export function MessageAttachments({ attachments }: { attachments: AttachmentMetadata[] }) {
  const [preview, setPreview] = useState<AttachmentMetadata | null>(null);
  const [error, setError] = useState(''); const htmlLoads = useRef(new Set<() => void>());
  useEffect(() => () => { htmlLoads.current.forEach(cancel => cancel()); htmlLoads.current.clear(); }, []);
  function openHtml(attachment: AttachmentMetadata) {
    setError('');
    try {
      let cancel: () => void;
      cancel = openHtmlPreview(attachment, () => htmlLoads.current.delete(cancel));
      htmlLoads.current.add(cancel);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Could not open this file.'); }
  }
  return <><div className="message-attachments">{attachments.map(attachment => attachment.kind === 'image' ? <button className="message-image-button" key={attachment.id} onClick={() => setPreview(attachment)} title={t('attachments.preview', { name: attachment.name })}><img loading="lazy" src={attachment.url} alt={attachment.name} width={attachment.width ?? undefined} height={attachment.height ?? undefined} /><span>{attachment.name}</span></button> : <div className="file-attachment" key={attachment.id} data-attachment-id={attachment.id}><Icon name="file" size={28} /><div><a href={attachment.url} download={attachment.name} title={`Download ${attachment.name}`}>{attachment.name}</a><small>{fileSize(attachment.size)}</small></div>{attachment.preview_kind === 'html' ? <IconButton icon="arrow" label={`Open ${attachment.name} in new tab`} onClick={() => openHtml(attachment)} /> : attachment.preview_kind && <IconButton icon="search" label={`Preview ${attachment.name}`} onClick={() => setPreview(attachment)} />}</div>)}</div>{error && <div className="attachment-preview-notice" role="alert"><Icon name="info" size={16} /><span>{error}</span><IconButton icon="close" label="Dismiss file preview error" size={14} onClick={() => setError('')} /></div>}{preview && <FilePreview key={preview.id} attachment={preview} onClose={() => setPreview(null)} />}</>;
}
