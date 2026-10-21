import { useEffect, useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { ActivityEvent, ActivityRun, Actor, ApiClient, ChatDetail, ChatSettings, Tracking, WorkspaceEntry } from './api/client';
import { ApiError } from './api/client';
import { Avatar, ChatAvatar, chatTitle, Empty, Icon, IconButton, Modal, dateTime, time } from './ui';
import { t } from './i18n';
import './TeamsOverlays.css';

function SideDrawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const container = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latestClose = useRef(onClose); latestClose.current = onClose;
  const [closing, setClosing] = useState(false);
  function close() {
    if (closeTimer.current) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { latestClose.current(); return; }
    setClosing(true);
    closeTimer.current = setTimeout(() => latestClose.current(), 140);
  }
  useEffect(() => {
    previousFocus.current = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => container.current?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true }));
    return () => { cancelAnimationFrame(frame); if (closeTimer.current) clearTimeout(closeTimer.current); if (previousFocus.current?.isConnected) previousFocus.current.focus({ preventScroll: true }); };
  }, []);
  return <div ref={container} className={`side-drawer ${closing ? 'is-closing' : ''}`} role="dialog" aria-label={title} tabIndex={-1} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    if (event.key !== 'Tab' || !container.current) return;
    const controls = [...container.current.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled):not([hidden]),textarea:not(:disabled),select:not(:disabled),summary,[tabindex="0"]')].filter(element => element.offsetParent !== null);
    const first = controls[0], last = controls[controls.length - 1];
    if (!first) { event.preventDefault(); container.current.focus(); }
    else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }}><div className="drawer-heading"><IconButton icon="arrow" label={t('common.back')} onClick={close} /><h3>{title}</h3><IconButton icon="close" label={t('common.close')} onClick={close} /></div>{children}</div>;
}

function runLabel(status: string) {
  if (status === 'completed') return t('activity.completed');
  if (status === 'running') return t('activity.running');
  if (status === 'failed') return t('activity.failed');
  if (status === 'stopped' || status === 'cancelled') return t('activity.stopped');
  if (status === 'queued') return t('activity.queued');
  return status;
}
function RunStatus({ status }: { status: string }) { return <span className={`run-status ${status}`}><span className={status === 'running' ? 'spinner' : 'status-icon'}>{status === 'completed' ? <Icon name="check" size={13} /> : status === 'running' ? null : status === 'failed' ? '!' : <Icon name="clock" size={14} />}</span>{runLabel(status)}</span>; }
type WorkspaceTreeProps = { entries: WorkspaceEntry[]; api: ApiClient; chatId: string; refreshKey: string; onFile: (file: WorkspaceEntry) => void; onError: (error: unknown) => void };
function WorkspaceTree(props: WorkspaceTreeProps) {
  return <div className="workspace-tree">{props.entries.map(entry => entry.kind === 'folder' ? <WorkspaceFolder key={entry.id} {...props} entry={entry} /> : <div key={entry.id}><button className="tree-row file" title={entry.path} onClick={() => props.onFile(entry)}><span className="tree-spacer" /><Icon name="file" size={23} /><span>{entry.name}</span></button></div>)}</div>;
}
function WorkspaceFolder({ entry, ...props }: WorkspaceTreeProps & { entry: WorkspaceEntry }) {
  const [expanded, setExpanded] = useState(false); const [children, setChildren] = useState<WorkspaceEntry[]>([]); const [loaded, setLoaded] = useState(false); const [loading, setLoading] = useState(false);
  const { api, chatId, refreshKey, onError } = props;
  useEffect(() => {
    if (!expanded || entry.children) return;
    const abort = new AbortController(); let alive = true; setLoading(true);
    api.get<WorkspaceEntry[]>(`/chats/${chatId}/workspace?path=${encodeURIComponent(entry.path)}`, abort.signal).then(items => { if (alive) { setChildren(items); setLoaded(true); } }).catch(error => { if (alive) { if (error instanceof ApiError && [401, 403, 404, 409].includes(error.status)) setChildren([]); onError(error); } }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; abort.abort(); };
  }, [api, chatId, entry.path, entry.children, expanded, refreshKey, onError]);
  return <div><button className="tree-row folder" title={entry.path} aria-expanded={expanded} aria-busy={loading || undefined} onClick={() => setExpanded(previous => !previous)}><span className={`tree-chevron ${expanded ? 'expanded' : ''}`}><Icon name="chevron" size={14} /></span><Icon name="folder" size={23} /><span>{entry.name}</span></button>{expanded && <div className="tree-children">{loading && !loaded && !entry.children ? <div className="loading-row">{t('common.loading')}</div> : <WorkspaceTree {...props} entries={entry.children || children} />}</div>}</div>;
}
export function TrackingEditor({ item, onSave, onClose, busy }: { item?: Tracking; onSave: (data: { title: string; description: string; status: string }) => Promise<void>; onClose: () => void; busy?: boolean }) {
  const [title, setTitle] = useState(item?.title || ''); const [description, setDescription] = useState(item?.description || ''); const [status, setStatus] = useState(item?.status || 'active');
  return <Modal closeDisabled={busy} title={item ? t('common.edit') : t('tracking.new')} onClose={onClose}><form onSubmit={e => { e.preventDefault(); if (title.trim()) void onSave({ title: title.trim(), description: description.trim(), status }); }}><label>{t('tracking.title')}<input required value={title} onChange={e => setTitle(e.target.value)} /></label><label>{t('tracking.description')}<textarea rows={4} value={description} onChange={e => setDescription(e.target.value)} /></label>{item && <label>{t('tracking.status')}<select value={status} onChange={e => setStatus(e.target.value)}>{['active', 'completed', 'archived'].map(s => <option key={s} value={s}>{t(`tracking.${s}` as 'tracking.active' | 'tracking.completed' | 'tracking.archived')}</option>)}{!['active', 'completed', 'archived'].includes(status) && <option>{status}</option>}</select></label>}<div className="modal-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>{t('common.cancel')}</button><button className="primary-button" disabled={!title.trim() || busy}>{t('common.save')}</button></div></form></Modal>;
}
export function DetailSidebar({ api, detail, actors, version, onSettings, onError, onChange, ownerId, createFrom, onClearCreate }: { api: ApiClient; detail: ChatDetail; actors: Actor[]; version: number; onSettings: (patch: Partial<ChatSettings>) => void; onError: (error: unknown) => void; onChange: () => void; ownerId: string; createFrom?: string | null; onClearCreate: () => void }) {
  const [activityCursor, setActivityCursor] = useState<unknown[] | null>(null);
  const [activity, setActivity] = useState<ActivityRun[]>([]); const [workspace, setWorkspace] = useState<WorkspaceEntry[]>([]); const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState({ activity: false, workspace: false }); const [workspaceRefresh, setWorkspaceRefresh] = useState(0);
  const [runDetail, setRunDetail] = useState<{ run: ActivityRun; events: ActivityEvent[]; has_more?: boolean; next_seq?: number | null } | null>(null); const [runKey, setRunKey] = useState<ActivityRun | null>(null); const [runLoading, setRunLoading] = useState(false);
  const [trackingDetail, setTrackingDetail] = useState<Tracking | null>(null); const [edit, setEdit] = useState<Tracking | 'new' | null>(null); const [update, setUpdate] = useState(''); const [busy, setBusy] = useState(false); const [file, setFile] = useState<WorkspaceEntry | null>(null); const [deleteConfirm, setDeleteConfirm] = useState(false); const [sourceId, setSourceId] = useState<string | null>(null); const [preview, setPreview] = useState(''); const [previewLoading, setPreviewLoading] = useState(false); const [previewUnavailable, setPreviewUnavailable] = useState(false); const [uploading, setUploading] = useState(false); const uploadInput = useRef<HTMLInputElement>(null);
  const tabsId = useId();
  const chatId = detail.chat.id; const tab = detail.settings.sidebar_tab === 'activity' && detail.chat.kind === 'group' ? 'workspace' : detail.settings.sidebar_tab;
  useEffect(() => { setRunKey(null); setRunDetail(null); setTrackingDetail(null); setEdit(null); setFile(null); setActivity([]); setWorkspace([]); setLoaded({ activity: false, workspace: false }); }, [chatId, ownerId]);
  useEffect(() => { if (createFrom) { setSourceId(createFrom); setEdit('new'); onClearCreate(); } }, [createFrom]);
  useEffect(() => {
    const abort = new AbortController(); let alive = true;
    setLoading(true);
    const endpoint = tab === 'activity' ? 'activity' : tab === 'workspace' ? 'workspace' : null;
    if (!endpoint) { setLoading(false); return () => abort.abort(); }
    api.get<{ items: ActivityRun[]; next_cursor?: unknown } | WorkspaceEntry[]>(`/chats/${chatId}/${endpoint}`, abort.signal).then(result => { if (alive) { if (tab === 'activity') { setActivity((result as { items: ActivityRun[] }).items.sort((a, b) => b.started_at.localeCompare(a.started_at))); setActivityCursor((result as { next_cursor?: unknown[] }).next_cursor || null); } else setWorkspace(result as WorkspaceEntry[]); setLoaded(previous => ({ ...previous, [endpoint]: true })); } }).catch(error => { if (alive) onError(error); }).finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; abort.abort(); };
  }, [api, chatId, tab, version, workspaceRefresh]);
  useEffect(() => {
    if (!runKey) return;
    let alive = true; setRunLoading(true);
    api.get<{ run: ActivityRun; events: ActivityEvent[] }>(`/chats/${chatId}/activity/${encodeURIComponent(runKey.session_id)}/${encodeURIComponent(runKey.run_id)}`).then(result => { if (alive) setRunDetail(result); }).catch(error => { if (alive) onError(error); }).finally(() => { if (alive) setRunLoading(false); });
    return () => { alive = false; };
  }, [api, chatId, runKey, version]);
  useEffect(() => {
    if (!trackingDetail) return;
    let alive = true;
    api.get<Tracking>(`/chats/${chatId}/tracking/${trackingDetail.id}`).then(result => { if (alive) setTrackingDetail(result); }).catch(error => { if (alive) onError(error); });
    return () => { alive = false; };
  }, [api, chatId, trackingDetail?.id, version]);
  async function moreActivity() {
    if (!activityCursor) return;
    try {
      const result = await api.get<{ items: ActivityRun[]; next_cursor: unknown[] | null }>(`/chats/${chatId}/activity?before=${encodeURIComponent(JSON.stringify(activityCursor))}`);
      setActivity(previous => [...previous, ...result.items]); setActivityCursor(result.next_cursor);
    } catch (error) { onError(error); }
  }
  async function moreEvents() {
    if (!runKey || !runDetail?.has_more || runDetail.next_seq == null) return;
    try {
      const result = await api.get<{ run: ActivityRun; events: ActivityEvent[]; has_more: boolean; next_seq: number | null }>(`/chats/${chatId}/activity/${encodeURIComponent(runKey.session_id)}/${encodeURIComponent(runKey.run_id)}?after_seq=${runDetail.next_seq}`);
      setRunDetail(previous => previous ? { ...result, events: [...previous.events, ...result.events] } : result);
    } catch (error) { onError(error); }
  }
  async function saveTracking(data: { title: string; description: string; status: string }) {
    setBusy(true);
    try {
      const result = edit === 'new' ? await api.post<Tracking>(`/chats/${chatId}/tracking`, { title: data.title, description: data.description, source_message_ids: sourceId ? [sourceId] : [] }) : await api.patch<Tracking>(`/chats/${chatId}/tracking/${(edit as Tracking).id}`, data);
      setEdit(null); setTrackingDetail(result); onChange();
    } catch (error) { onError(error); } finally { setBusy(false); }
  }
  useEffect(() => {
    if (!file) { setPreview(''); return; }
    const abort = new AbortController(); setPreview(''); setPreviewLoading(true); setPreviewUnavailable(false);
    fetch(`/api/chats/${chatId}/workspace/files/${encodeURIComponent(file.id)}/preview`, { credentials: 'same-origin', signal: abort.signal }).then(async response => {
      if (!response.ok) { if (response.status === 415) { setPreviewUnavailable(true); return; } const error = await response.json(); throw new Error(error.detail || response.statusText); }
      setPreview(await response.text());
    }).catch(error => { if (!abort.signal.aborted) { setPreviewUnavailable(true); onError(error); } }).finally(() => { if (!abort.signal.aborted) setPreviewLoading(false); });
    return () => abort.abort();
  }, [file?.id, chatId, api]);
  async function uploadFile(selected: File) {
    if (!selected.size || selected.size > 20 * 1024 * 1024) { onError(t('workspace.fileLimit')); return; }
    setUploading(true);
    try { await api.request(`/chats/${chatId}/workspace/files?name=${encodeURIComponent(selected.name)}`, { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: selected }); onChange(); }
    catch (error) { onError(error); } finally { setUploading(false); if (uploadInput.current) uploadInput.current.value = ''; }
  }
  const members = detail.members.filter(a => a.kind === 'human');
  return <aside className="detail-sidebar"><div className="detail-heading"><ChatAvatar chat={detail.chat} actors={actors} ownerId={ownerId} size={48} /><div><h2>{chatTitle(detail.chat, actors, ownerId)}</h2><p>{t(detail.chat.kind === 'private' ? 'chat.private' : 'chat.shared')}</p></div><IconButton icon="close" label={t('common.close')} onClick={() => onSettings({ sidebar_open: false })} /></div><div className="detail-tabs" role="tablist" aria-label={chatTitle(detail.chat, actors, ownerId)} onKeyDown={event => { const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End']; if (!keys.includes(event.key)) return; event.preventDefault(); const tabs = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]')]; const current = tabs.indexOf(document.activeElement as HTMLButtonElement); const index = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; tabs[index]?.focus(); tabs[index]?.click(); }}>{(detail.chat.kind === 'private' ? ['activity', 'workspace', 'tracking'] : ['workspace', 'tracking']).map(value => <button key={value} role="tab" id={`${tabsId}-${value}`} aria-controls={`${tabsId}-panel`} tabIndex={tab === value ? 0 : -1} aria-selected={tab === value} className={tab === value ? 'selected' : ''} onClick={() => onSettings({ sidebar_tab: value as ChatSettings['sidebar_tab'] })}>{t(`sidebar.${value}` as 'sidebar.activity' | 'sidebar.workspace' | 'sidebar.tracking')}</button>)}</div><div className="detail-content" role="tabpanel" id={`${tabsId}-panel`} aria-labelledby={`${tabsId}-${tab}`} tabIndex={0}>
    {tab === 'activity' && <><div className="panel-label">{t('sidebar.thisChat')}</div>{loading && !loaded.activity ? <div className="loading-row">{t('common.loading')}</div> : !activity.length ? <Empty icon="clock" title={t('activity.empty')} detail={t('activity.emptyDetail')} /> : activity.map(run => <button className="run-card" key={`${run.session_id}:${run.run_id}`} onClick={() => { setRunDetail(null); setRunKey(run); }}><div className="run-top"><RunStatus status={run.status} /><span className={`run-tag ${run.parent_run_id ? '' : 'main'}`}>{t(run.parent_run_id ? 'activity.subagent' : 'activity.main')}</span><time>{time(run.started_at)}</time><Icon name="chevron" size={16} /></div><h3>{run.title}</h3>{run.summary && <p>{run.summary}</p>}</button>)}{activityCursor && <button className="text-button" onClick={() => { void moreActivity(); }}>{t('activity.loadMoreRuns')}</button>}</>}
    {tab === 'workspace' && <><div className="panel-actions"><span className="panel-label">{t(detail.chat.kind === 'private' ? 'sidebar.privateWorkspace' : 'sidebar.sharedWorkspace')}</span><button className="text-button" disabled={loading} onClick={() => setWorkspaceRefresh(value => value + 1)} aria-label={t('workspace.refresh')}><Icon name="refresh" size={14} />{t('common.refresh')}</button><button className="text-button" disabled={uploading} onClick={() => uploadInput.current?.click()}><Icon name="plus" size={14} />{t(uploading ? 'common.loading' : 'workspace.upload')}</button><input ref={uploadInput} type="file" hidden onChange={e => { const selected = e.target.files?.[0]; if (selected) void uploadFile(selected); }} /></div>{loading && !loaded.workspace ? <div className="loading-row">{t('common.loading')}</div> : workspace.length ? <WorkspaceTree entries={workspace} api={api} chatId={chatId} refreshKey={`${version}:${workspaceRefresh}`} onFile={setFile} onError={onError} /> : <Empty icon="folder" title={t('workspace.empty')} />}{detail.chat.kind === 'group' && <div className="members-section"><div className="panel-label">{t('sidebar.groupMembers', { count: members.length * 2 })}</div>{members.map(actor => <div className="member-row" key={actor.id}><Avatar actor={actor} size={32} /><span>{actor.name}<small><Icon name="spark" size={13} />{detail.members.find(a => a.kind === 'twin' && a.owner_id === actor.id)?.name || ''}</small></span></div>)}</div>}</>}
    {tab === 'tracking' && <><div className="panel-actions"><span className="panel-label">{t('sidebar.tracking')}</span><button className="text-button" onClick={() => { setSourceId(null); setEdit('new'); }}><Icon name="plus" size={15} />{t('tracking.new')}</button></div>{!detail.tracking.length ? <Empty icon="check" title={t('tracking.empty')} detail={t('tracking.emptyDetail')} /> : detail.tracking.map(item => <button className="tracking-card" key={item.id} onClick={() => { setTrackingDetail(item); setUpdate(''); }}><span className={`tracking-status ${item.status}`} /><div><h3>{item.title}</h3><p>{item.description}</p><small>{dateTime(item.updated_at)}</small></div><Icon name="chevron" size={15} /></button>)}</>}
  </div>
    {runKey && <SideDrawer key={`${runKey.session_id}:${runKey.run_id}`} title={runKey.title} onClose={() => { setRunKey(null); setRunDetail(null); }}><div className="drawer-scroll"><RunStatus status={runDetail?.run.status || runKey.status} /><p className="drawer-description">{runDetail?.run.summary}</p><dl className="detail-fields"><dt>{t('activity.started')}</dt><dd>{dateTime(runKey.started_at)}</dd>{runDetail?.run.finished_at && <><dt>{t('activity.finished')}</dt><dd>{dateTime(runDetail.run.finished_at)}</dd></>}{runKey.parent_run_id && <><dt>{t('activity.parent')}</dt><dd>{runKey.parent_run_id}</dd></>}</dl><h4>{t('activity.events')}</h4>{runLoading && !runDetail ? <p className="muted">{t('common.loading')}</p> : !runDetail?.events.length ? <p className="muted">{t('activity.noEvents')}</p> : <div className="event-chain">{[...runDetail.events].sort((a, b) => a.seq - b.seq).map(event => <article className={`event-card ${event.status || ''}`} key={event.id}><span className="event-dot" /><div className="event-title"><h4>{event.label}</h4><time>{time(event.time)}</time></div><small>{event.type}{event.status ? ` · ${event.status}` : ''}</small>{event.input && <details><summary>{t('activity.input')}</summary><pre>{event.input}</pre></details>}{event.output && <details open><summary>{t('activity.output')}</summary><pre>{event.output}</pre></details>}</article>)}</div>}{runDetail?.has_more && <button className="text-button" onClick={() => { void moreEvents(); }}>{t('activity.loadMoreEvents')}</button>}</div></SideDrawer>}
    {trackingDetail && <SideDrawer key={trackingDetail.id} title={trackingDetail.title} onClose={() => setTrackingDetail(null)}><div className="drawer-scroll"><div className="tracking-detail-actions"><span className="small-badge">{trackingDetail.status}</span><IconButton icon="edit" label={t('common.edit')} onClick={() => setEdit(trackingDetail)} /><IconButton icon="trash" label={t('common.delete')} onClick={() => setDeleteConfirm(true)} /></div><p className="drawer-description">{trackingDetail.description}</p><h4>{t('tracking.updates')}</h4><div className="tracking-updates">{trackingDetail.updates?.length ? trackingDetail.updates.map(item => <article key={item.id}><time>{dateTime(item.created_at)}</time><p>{item.body}</p></article>) : <p className="muted">{t('tracking.noUpdates')}</p>}</div><form className="update-form" onSubmit={async e => { e.preventDefault(); if (!update.trim()) return; setBusy(true); try { const result = await api.post<Tracking>(`/chats/${chatId}/tracking/${trackingDetail.id}/updates`, { body: update.trim(), source_message_ids: [] }); setTrackingDetail(result); setUpdate(''); onChange(); } catch (error) { onError(error); } finally { setBusy(false); } }}><label>{t('tracking.addUpdate')}<textarea rows={3} placeholder={t('tracking.updatePlaceholder')} value={update} onChange={e => setUpdate(e.target.value)} /></label><button className="primary-button" disabled={!update.trim() || busy}>{t('common.add')}</button></form></div></SideDrawer>}
    {edit && <TrackingEditor key={edit === 'new' ? `new-${createFrom || ''}` : edit.id} item={edit === 'new' ? undefined : edit} onSave={saveTracking} onClose={() => setEdit(null)} busy={busy} />}
    {deleteConfirm && trackingDetail && <Modal title={t('tracking.deleteConfirm')} onClose={() => setDeleteConfirm(false)}><p>{trackingDetail.title}</p><div className="modal-actions"><button className="secondary-button" onClick={() => setDeleteConfirm(false)}>{t('common.cancel')}</button><button className="danger-button" onClick={async () => { try { await api.delete(`/chats/${chatId}/tracking/${trackingDetail.id}`); setTrackingDetail(null); setDeleteConfirm(false); onChange(); } catch (error) { onError(error); } }}>{t('common.delete')}</button></div></Modal>}
    {file && <Modal title={file.name} onClose={() => setFile(null)} wide>{previewLoading ? <div className="file-preview"><span className="spinner" /></div> : previewUnavailable ? <div className="file-preview"><Icon name="file" size={42} /><p>{t('workspace.noPreview')}</p></div> : <pre className="text-file-preview">{preview}</pre>}<dl className="detail-fields"><dt>{t('workspace.path')}</dt><dd>{file.path}</dd>{file.size !== undefined && file.size !== null && <><dt>{t('workspace.file')}</dt><dd>{t('workspace.size', { size: file.size })}</dd></>}</dl><div className="modal-actions"><a className="primary-button download-button" href={`/api/chats/${chatId}/workspace/files/${encodeURIComponent(file.id)}`} download={file.name}>{t('workspace.download')}</a></div></Modal>}
  </aside>;
}
