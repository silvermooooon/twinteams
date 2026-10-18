import { useCallback, useEffect, useState } from 'react';
import { ApiError } from './api/client';
import type { ApiClient } from './api/client';
import { twinApi } from './api/twin';
import type { MemoryEntry, MemorySource, MemoryStatus, Soul } from './api/twin';
import { t } from './i18n';
import { Icon, IconButton, Modal, dateTime } from './ui';

type Snapshot = { content: string; version: string | number | null; exists: boolean; sources?: MemorySource[]; updated_at?: number | null };
export type ContentDraft = { content: string; baseline: string; version: string | number | null; key?: string; conflict?: Snapshot; rebased?: boolean };
export type ContentDrafts = Record<string, ContentDraft>;
export type SetContentDraft = (id: string, draft: ContentDraft | null) => void;
export function draftChanged(draft: ContentDraft) { return draft.content !== draft.baseline || !!draft.conflict; }
function fromSnapshot(snapshot: Snapshot): ContentDraft { return { content: snapshot.content, baseline: snapshot.content, version: snapshot.version }; }
function reconcile(draft: ContentDraft | undefined, snapshot: Snapshot): ContentDraft {
  if (!draft || !draftChanged(draft) || draft.content === snapshot.content) return fromSnapshot(snapshot);
  return draft.version !== snapshot.version ? { ...draft, conflict: snapshot } : draft;
}
function failureText(failure: unknown) { return failure instanceof Error ? failure.message : t('twin.loadFailed'); }
function contentSnapshot(value: Soul | MemoryEntry | null): Snapshot { return value ? { ...value, exists: value.version !== null } : { content: '', version: null, exists: false }; }
function conflictSnapshot(failure: unknown): Snapshot | undefined {
  if (!(failure instanceof ApiError) || failure.status !== 409 || !failure.payload || typeof failure.payload !== 'object') return;
  const payload = failure.payload as { current?: Soul | MemoryEntry | null };
  if (!Object.prototype.hasOwnProperty.call(payload, 'current')) return;
  const current = payload.current;
  if (current === null) return contentSnapshot(null);
  if (current && typeof current.content === 'string' && (typeof current.version === 'string' || typeof current.version === 'number' || current.version === null)) return contentSnapshot(current);
}

function useSnapshot(load: (signal: AbortSignal) => Promise<Snapshot>, revision: number) {
  const [value, setValue] = useState<Snapshot | null>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [attempt, setAttempt] = useState(0);
  useEffect(() => { const controller = new AbortController(); setLoading(true); setError(''); load(controller.signal).then(result => { if (!controller.signal.aborted) setValue(result); }).catch(failure => { if (!controller.signal.aborted) setError(failureText(failure)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [load, revision, attempt]);
  return { value, setValue, loading, error, retry: () => setAttempt(value => value + 1) };
}

function ConflictPanel({ draft, onDraft }: { draft: ContentDraft; onDraft: (draft: ContentDraft) => void }) {
  if (!draft.conflict) return null;
  const current = draft.conflict;
  return <section className="twin-content-conflict" role="alert">
    <strong><Icon name="info" size={17} />{t('twin.versionConflict')}</strong>
    <p>{t('twin.mergeHint')}</p>
    <h4>{t('twin.serverContent')}</h4>
    {current.exists ? <pre className="twin-text-content">{current.content || t('twin.emptyContent')}</pre> : <p>{t('twin.entryDeleted')}</p>}
    <small>{t('twin.version')}: <code>{current.version ?? '—'}</code></small>
    <div className="twin-row-actions">
      <button type="button" className="secondary-button" onClick={() => onDraft({ ...draft, version: current.version, baseline: current.content, conflict: undefined, rebased: true })}>{t('twin.reviewVersion')}</button>
      <button type="button" className="text-button" onClick={() => onDraft({ ...fromSnapshot(current), key: draft.key })}>{t('twin.reloadContent')}</button>
    </div>
  </section>;
}

export function SoulTab({ api, revision, draft, onDraft }: { api: ApiClient; revision: number; draft?: ContentDraft; onDraft: SetContentDraft }) {
  const load = useCallback(async (signal: AbortSignal) => contentSnapshot(await twinApi(api).soul(signal)), [api]);
  const resource = useSnapshot(load, revision); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [saved, setSaved] = useState(false); const [clear, setClear] = useState(false);
  useEffect(() => { if (resource.value) onDraft('soul', reconcile(draft, resource.value)); }, [resource.value, onDraft]);
  const current = draft || (resource.value ? fromSnapshot(resource.value) : undefined);
  async function mutate(remove: boolean) {
    if (!current || current.conflict || busy || typeof current.version === 'number') return;
    setBusy(true); setError(''); setSaved(false);
    try {
      const updated = remove ? await twinApi(api).clearSoul(current.version) : await twinApi(api).saveSoul(current.content, current.version);
      const snapshot = contentSnapshot(updated); resource.setValue(snapshot); onDraft('soul', fromSnapshot(snapshot)); setSaved(true); setClear(false);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) {
        let latest = conflictSnapshot(failure);
        if (!latest) { try { latest = contentSnapshot(await twinApi(api).soul()); } catch (readFailure) { setError(failureText(readFailure)); } }
        if (latest) onDraft('soul', { ...current, conflict: latest });
        setClear(false);
      } else setError(failureText(failure));
    } finally { setBusy(false); }
  }
  return <><div className="twin-section-heading"><div><h2>{t('twin.soul')}</h2><p>{t('twin.soulHint')}</p></div><IconButton icon="refresh" label={t('twin.refresh')} disabled={busy} onClick={resource.retry} /></div>
    {resource.error && <div className="twin-notice error" role="alert">{resource.error}<button type="button" className="text-button" onClick={resource.retry}>{t('twin.retry')}</button></div>}
    {!current ? <div className="twin-loading" role="status"><span className="spinner" />{t('common.loading')}</div> : <form className="twin-content-editor" onSubmit={event => { event.preventDefault(); void mutate(false); }}>
      <ConflictPanel draft={current} onDraft={value => onDraft('soul', value)} />
      <label>{t('twin.soulContent')}<textarea aria-label={t('twin.soulContent')} maxLength={50000} rows={14} value={current.content} disabled={busy} placeholder={t('twin.soulEmpty')} onChange={event => { onDraft('soul', { ...current, content: event.target.value }); setSaved(false); }} /></label>
      <div className="twin-content-metadata"><span>{t('twin.version')}: <code>{current.version ?? '—'}</code></span><span role="status">{saved ? t('twin.saved') : current.rebased ? t('twin.rebased') : draftChanged(current) ? t('twin.draftKept') : ''}</span></div>
      {error && <p className="twin-inline-error" role="alert">{error}</p>}
      <div className="twin-content-actions"><div className="twin-row-actions"><button type="button" className="text-button" disabled={busy || current.version === null || !!current.conflict} onClick={() => setClear(true)}>{t('twin.clearSoul')}</button>{draftChanged(current) && !current.conflict && <button type="button" className="text-button" disabled={busy} onClick={() => onDraft('soul', { ...current, content: current.baseline, rebased: false })}>{t('twin.discardDraft')}</button>}</div><button className="primary-button" disabled={busy || !draftChanged(current) || !!current.conflict}>{t(busy ? 'twin.saving' : 'common.save')}</button></div>
    </form>}
    {clear && <Modal title={t('twin.clearSoul')} onClose={() => setClear(false)} closeDisabled={busy}><p>{t('twin.clearSoulConfirm')}</p><div className="modal-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => setClear(false)}>{t('common.cancel')}</button><button type="button" className="danger-button" disabled={busy} onClick={() => { void mutate(true); }}>{t('twin.clearSoul')}</button></div></Modal>}
  </>;
}

export function MemoryEditor({ api, entryKey, revision, draft, onDraft, onClose, onSaved }: { api: ApiClient; entryKey: string | null; revision: number; draft?: ContentDraft; onDraft: SetContentDraft; onClose: () => void; onSaved: () => void }) {
  const id = entryKey === null ? 'memory:new' : `memory:${entryKey}`;
  const load = useCallback(async (signal: AbortSignal) => {
    if (entryKey === null) return contentSnapshot(null);
    try { return contentSnapshot(await twinApi(api).memory(entryKey, signal)); }
    catch (failure) { if (failure instanceof ApiError && failure.status === 404) return contentSnapshot(null); throw failure; }
  }, [api, entryKey]);
  const resource = useSnapshot(load, revision); const [editing, setEditing] = useState(entryKey === null || !!draft && draftChanged(draft)); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [remove, setRemove] = useState(false);
  useEffect(() => { if (resource.value) onDraft(id, { ...reconcile(draft, resource.value), key: draft?.key || entryKey || '' }); }, [resource.value, onDraft, id, entryKey]);
  const current = draft || (resource.value ? { ...fromSnapshot(resource.value), key: entryKey || '' } : undefined);
  const key = entryKey ?? current?.key ?? '';
  const sources = resource.value?.version === current?.version ? resource.value?.sources : undefined;
  async function mutate(deleting: boolean) {
    if (!current || current.conflict || busy || !key.trim()) return;
    setBusy(true); setError('');
    try {
      if (deleting) {
        if (typeof current.version !== 'string') return;
        await twinApi(api).deleteMemory(key, current.version);
      } else await twinApi(api).saveMemory(key, current.content, typeof current.version === 'string' ? current.version : null);
      onDraft(id, null); onSaved(); onClose();
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) {
        let latest = conflictSnapshot(failure);
        if (!latest) { try { latest = contentSnapshot(await twinApi(api).memory(key)); } catch (readFailure) { if (readFailure instanceof ApiError && readFailure.status === 404) latest = contentSnapshot(null); else setError(failureText(readFailure)); } }
        if (latest) onDraft(id, { ...current, conflict: latest });
        setRemove(false); setEditing(true);
      } else setError(failureText(failure));
    } finally { setBusy(false); }
  }
  return <Modal wide title={entryKey === null ? t('twin.addMemory') : entryKey} onClose={onClose} closeDisabled={busy}>
    {resource.error && <div className="twin-notice error" role="alert">{resource.error}<button type="button" className="text-button" onClick={resource.retry}>{t('twin.retry')}</button></div>}
    {!current ? <div className="twin-loading" role="status"><span className="spinner" />{t('common.loading')}</div> : <form className="twin-content-editor" onSubmit={event => { event.preventDefault(); void mutate(false); }}>
      <ConflictPanel draft={current} onDraft={value => onDraft(id, value)} />
      {entryKey === null && <><label>{t('twin.memoryKey')}<input aria-label={t('twin.memoryKey')} required value={key} disabled={busy} maxLength={512} onChange={event => onDraft(id, { ...current, key: event.target.value, version: null, baseline: '', conflict: undefined, rebased: false })} /></label><p className="twin-help">{t('twin.memoryKeyHint')}</p></>}
      {editing ? <label>{t('twin.memoryContent')}<textarea aria-label={t('twin.memoryContent')} maxLength={100000} rows={12} value={current.content} disabled={busy} onChange={event => onDraft(id, { ...current, content: event.target.value })} /></label> : <pre className="twin-text-content">{current.content || t(resource.value?.exists === false ? 'twin.entryDeleted' : 'twin.emptyContent')}</pre>}
      <div className="twin-content-metadata"><span>{t('twin.gitVersion')}: <code>{current.version ?? '—'}</code></span>{current.rebased && <span role="status">{t('twin.rebased')}</span>}{draftChanged(current) && !current.rebased && <span>{t('twin.draftKept')}</span>}</div>
      {!!sources?.length && <><h3 className="twin-subheading">{t('twin.sources')}</h3><p className="twin-help">{t('twin.sourceHint')}</p><div className="twin-memory-sources">{sources.map((source, index) => <div key={`${source.session_id}:${source.event_id}:${index}`}><small>{t('twin.sourceReference')}</small><code>session: {source.session_id}</code><code>event: {source.event_id}</code></div>)}</div></>}
      {error && <p className="twin-inline-error" role="alert">{error}</p>}
      {remove ? <div className="twin-delete-confirm"><p>{t('twin.deleteMemoryConfirm', { name: key })}</p><div className="twin-row-actions"><button type="button" className="secondary-button" disabled={busy} onClick={() => setRemove(false)}>{t('common.cancel')}</button><button type="button" className="danger-button" disabled={busy || !!current.conflict} onClick={() => { void mutate(true); }}>{t('twin.deleteMemory')}</button></div></div> : <div className="twin-content-actions"><div className="twin-row-actions">{entryKey !== null && current.version !== null && <button type="button" className="text-button" disabled={busy || !!current.conflict} onClick={() => setRemove(true)}>{t('twin.deleteMemory')}</button>}{editing && draftChanged(current) && !current.conflict && <button type="button" className="text-button" disabled={busy} onClick={() => onDraft(id, { ...current, content: current.baseline, rebased: false })}>{t('twin.discardDraft')}</button>}</div>{editing ? <button className="primary-button" disabled={busy || !key.trim() || !!current.conflict || entryKey !== null && !draftChanged(current)}>{t(busy ? 'twin.saving' : 'common.save')}</button> : <button type="button" className="primary-button" disabled={busy || resource.value?.exists === false} onClick={() => setEditing(true)}><Icon name="edit" size={15} />{t('twin.editMemory')}</button>}</div>}
    </form>}
  </Modal>;
}

const dreamLabels = { idle: 'twin.dreamIdle', scheduled: 'twin.dreamScheduled', queued: 'twin.dreamQueued', running: 'twin.dreamRunning', failed: 'twin.dreamFailed', cancelled: 'twin.dreamCancelled', published: 'twin.dreamPublished', recovery_required: 'twin.dreamRecovery', disabled: 'twin.disabled' } as const;
export function MemoryStatusCard({ status, api, onChange }: { status: MemoryStatus; api: ApiClient; onChange: () => void }) {
  const dream = status.dream;
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [result, setResult] = useState('');
  async function runDream() {
    if (busy) return; setBusy(true); setError(''); setResult('');
    try { const value = await twinApi(api).dream(); setResult(t(value.status === 'nothing_to_process' ? 'twin.dreamNothing' : value.status === 'already_queued' ? 'twin.dreamAlreadyQueued' : 'twin.dreamQueuedNotice')); onChange(); }
    catch (failure) { setError(failure instanceof ApiError && failure.message === 'Memory publication requires reconciliation' ? t('twin.dreamNeedsReview') : failureText(failure)); onChange(); }
    finally { setBusy(false); }
  }
  return <section className="twin-memory-status"><div><Icon name="file" size={16} /><strong>{t(status.enabled ? status.mode === 'git' ? 'twin.gitMemory' : 'twin.localMemory' : 'twin.disabled')}</strong></div>{status.mode === 'git' && <p className="twin-help">{t('twin.gitHint')}</p>}{dream && <><div className="twin-dream-heading"><strong>{t('twin.dream')}</strong><span>{t(dreamLabels[dream.status])}</span></div><dl className="twin-detail-fields"><div><dt>{t('twin.dreamNext')}</dt><dd>{dream.due_at ? dateTime(dream.due_at) : t('twin.notScheduled')}</dd></div>{dream.last_success_at !== undefined && <div><dt>{t('twin.dreamSuccess')}</dt><dd>{dream.last_success_at ? dateTime(dream.last_success_at) : t('twin.noSuccess')}</dd></div>}{dream.last_error && <div><dt>{t('twin.dreamError')}</dt><dd className="twin-inline-error">{dream.last_error}</dd></div>}</dl></>}{status.enabled && <div className="twin-dream-action"><button type="button" className="secondary-button" disabled={busy || dream?.status === 'queued' || dream?.status === 'running' || dream?.status === 'recovery_required'} onClick={() => { void runDream(); }}>{t(busy ? 'twin.dreamSubmitting' : 'twin.dreamNow')}</button><p className="twin-help">{t('twin.dreamNowHint')}</p></div>}{result && <p className="twin-help" role="status">{result}</p>}{error && <p className="twin-inline-error" role="alert">{error}</p>}</section>;
}
