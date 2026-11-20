import { useEffect, useRef, useState } from 'react';
import { ApiError, requestKey } from './api/client';
import type { ApiClient, BotShareCatalog, BotSharePreview, BotShareToken, BotSnapshot, PersonalBot } from './api/client';
import { Avatar, Empty, Icon, Modal, dateTime } from './ui';
import { t } from './i18n';
import './BotSharing.css';

function failureText(error: unknown) {
  if (error instanceof ApiError && [403, 404, 410].includes(error.status)) return t('botShare.unavailableDetail');
  return error instanceof Error ? error.message : t('common.error');
}

function SnapshotFacts({ snapshot }: { snapshot: Pick<BotSnapshot, 'skill_count' | 'memory_included'> }) {
  return <div className="bot-snapshot-facts"><span><Icon name="spark" size={14} />{t(snapshot.skill_count === 1 ? 'botShare.skillOne' : 'botShare.skillsCount', { count: snapshot.skill_count })}</span><span><Icon name="file" size={14} />{t(snapshot.memory_included ? 'botShare.memoryIncluded' : 'botShare.memoryEmpty')}</span></div>;
}

function ShareLink({ token, busy, onRevoke }: { token: BotShareToken; busy: boolean; onRevoke: () => void }) {
  const [copied, setCopied] = useState(false); const [copyError, setCopyError] = useState(false);
  const link = new URL(location.href); link.hash = `/bots/share/${token.token}`;
  return <div className={`bot-share-link ${token.revoked_at ? 'revoked' : ''}`}>
    <div className="bot-share-link-heading"><time>{t('botShare.linkCreated', { time: dateTime(token.created_at) })}</time>{token.revoked_at && <span>{t('botShare.revoked')}</span>}</div>
    {!token.revoked_at && <><div className="bot-share-link-actions"><input aria-label={t('botShare.link')} value={link.href} readOnly onFocus={event => event.currentTarget.select()} /><button type="button" className="secondary-button lg-interactive" onClick={async () => { setCopyError(false); setCopied(false); try { await navigator.clipboard.writeText(link.href); setCopied(true); } catch { setCopyError(true); } }}>{t(copied ? 'botShare.copied' : 'botShare.copy')}</button><button type="button" className="text-button lg-interactive" disabled={busy} onClick={onRevoke}>{t('botShare.revoke')}</button></div>{copied && <span className="bot-share-feedback" role="status">{t('botShare.copiedDetail')}</span>}{copyError && <p className="twin-inline-error" role="alert">{t('botShare.copyFailed')}</p>}</>}
  </div>;
}

export function BotShareDialog({ api, bot, onClose }: { api: ApiClient; bot: PersonalBot; onClose: () => void }) {
  const [catalog, setCatalog] = useState<BotShareCatalog | null>(null); const [loading, setLoading] = useState(true); const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [loadError, setLoadError] = useState(''); const [notice, setNotice] = useState('');
  const freezeKey = useRef(requestKey()); const tokenKeys = useRef(new Map<string, string>());
  const base = `/bots/${encodeURIComponent(bot.id)}/shares`;
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setLoadError('');
    api.get<BotShareCatalog>(base, controller.signal).then(value => { if (!controller.signal.aborted) setCatalog(value); }).catch(error => { if (!controller.signal.aborted) setLoadError(failureText(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, base, revision]);
  function updateSnapshot(id: string, update: (snapshot: BotSnapshot) => BotSnapshot) {
    setCatalog(previous => previous && { snapshots: previous.snapshots.map(snapshot => snapshot.id === id ? update(snapshot) : snapshot), installed_snapshot: previous.installed_snapshot?.id === id ? update(previous.installed_snapshot) : previous.installed_snapshot });
  }
  async function freeze() {
    if (busy) return; setBusy(true); setError(''); setNotice('');
    try {
      const result = await api.post<{ snapshot: BotSnapshot; token: BotShareToken }>(base, { client_request_key: freezeKey.current });
      const snapshot = { ...result.snapshot, tokens: [...result.snapshot.tokens.filter(token => token.id !== result.token.id), result.token] };
      setCatalog(previous => ({ snapshots: [snapshot, ...(previous?.snapshots || []).filter(item => item.id !== snapshot.id)], installed_snapshot: previous?.installed_snapshot || null }));
      freezeKey.current = requestKey(); setNotice(t('botShare.created'));
    } catch (error) { setError(failureText(error)); }
    finally { setBusy(false); }
  }
  async function issueToken(snapshot: BotSnapshot) {
    if (busy) return; setBusy(true); setError(''); setNotice('');
    if (!tokenKeys.current.has(snapshot.id)) tokenKeys.current.set(snapshot.id, requestKey());
    try {
      const token = await api.post<BotShareToken>(`${base}/${encodeURIComponent(snapshot.id)}/tokens`, { client_request_key: tokenKeys.current.get(snapshot.id) });
      updateSnapshot(snapshot.id, value => ({ ...value, tokens: [...value.tokens.filter(item => item.id !== token.id), token] }));
      tokenKeys.current.delete(snapshot.id); setNotice(t('botShare.linkIssued'));
    } catch (error) { setError(failureText(error)); }
    finally { setBusy(false); }
  }
  async function revoke(snapshot: BotSnapshot, token: BotShareToken) {
    if (busy) return; setBusy(true); setError(''); setNotice('');
    try {
      await api.delete(`${base}/tokens/${encodeURIComponent(token.id)}`);
      updateSnapshot(snapshot.id, value => ({ ...value, tokens: value.tokens.map(item => item.id === token.id ? { ...item, revoked_at: new Date().toISOString() } : item) }));
      setNotice(t('botShare.revokedDetail'));
    } catch (error) { setError(failureText(error)); }
    finally { setBusy(false); }
  }
  function snapshotCard(snapshot: BotSnapshot, original = false) {
    return <article className="bot-snapshot-card" key={snapshot.id} data-snapshot-id={snapshot.id}>
      <div className="bot-snapshot-heading"><div><h3>{original ? t('botShare.original') : snapshot.name}</h3><time>{dateTime(snapshot.created_at)}</time></div><button type="button" className="secondary-button lg-interactive" disabled={busy || loading} onClick={() => { void issueToken(snapshot); }}>{t(original ? 'botShare.shareOriginal' : 'botShare.issueLink')}</button></div>
      {original && <p className="form-note">{t('botShare.originalHint')}</p>}
      <p className="bot-snapshot-assignment">{snapshot.assignment}</p><SnapshotFacts snapshot={snapshot} />
      {snapshot.parent_snapshot_id && <p className="bot-snapshot-lineage">{t('botShare.lineage', { id: snapshot.parent_snapshot_id.slice(0, 12) })}</p>}
      {snapshot.tokens.length ? snapshot.tokens.map(token => <ShareLink key={token.id} token={token} busy={busy || loading} onRevoke={() => { void revoke(snapshot, token); }} />) : <p className="form-note">{t('botShare.noLinks')}</p>}
    </article>;
  }
  return <Modal wide title={t('botShare.title')} closeDisabled={busy} onClose={onClose}><div className="bot-share-dialog">
    <div className="bot-share-profile"><Avatar actor={bot.actor} size={42} /><strong>{bot.name}</strong></div>
    <p className="form-note">{t('botShare.explanation')}</p><p className="form-note">{t('botShare.exclusions')}</p>
    <div className="bot-share-current"><div><h3>{t('botShare.current')}</h3><p>{t('botShare.currentHint')}</p></div><button type="button" className="primary-button lg-interactive" disabled={busy || loading || !!loadError} onClick={() => { void freeze(); }}>{t(busy ? 'common.loading' : 'botShare.freeze')}</button></div>
    {error && <p className="twin-inline-error" role="alert">{error}</p>}{notice && <p className="bot-share-notice" role="status"><Icon name="check" size={16} />{notice}</p>}
    <div className="bot-share-history-heading"><h3>{t('botShare.history')}</h3><button type="button" className="text-button lg-interactive" disabled={loading || busy} onClick={() => setRevision(value => value + 1)}><Icon name="refresh" size={14} />{t('common.refresh')}</button></div>
    {loading ? <div className="loading-row" role="status">{t('common.loading')}</div> : loadError ? <div role="alert" className="twin-inline-error"><p>{loadError}</p><button type="button" className="secondary-button lg-interactive" onClick={() => setRevision(value => value + 1)}>{t('common.retry')}</button></div> : catalog && <>
      {catalog.installed_snapshot && snapshotCard(catalog.installed_snapshot, true)}
      {catalog.snapshots.length ? catalog.snapshots.map(snapshot => snapshotCard(snapshot)) : <Empty icon="file" title={t('botShare.empty')} detail={t('botShare.emptyDetail')} />}
    </>}
    <p className="form-note">{t('botShare.revocationHint')}</p>
  </div></Modal>;
}

export function BotInstallPage({ api, token, onInstalled, onBack }: { api: ApiClient; token: string; onInstalled: (bot: PersonalBot) => void; onBack: () => void }) {
  const [preview, setPreview] = useState<BotSharePreview | null>(null); const [loading, setLoading] = useState(true); const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [loadError, setLoadError] = useState('');
  useEffect(() => {
    const controller = new AbortController(); setPreview(null); setError(''); setLoadError(''); setLoading(true);
    if (!token) { setLoading(false); setLoadError(t('botShare.unavailableDetail')); return; }
    api.request<BotSharePreview>('/bot-shares/preview', { method: 'POST', body: JSON.stringify({ token }), signal: controller.signal }).then(result => { if (!controller.signal.aborted) setPreview(result); }).catch(error => { if (!controller.signal.aborted) setLoadError(failureText(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, token, revision]);
  async function install() {
    if (busy || !preview) return;
    if (preview.installed_bot) { onInstalled(preview.installed_bot); return; }
    setBusy(true); setError('');
    try { onInstalled(await api.post<PersonalBot>('/bot-shares/install', { token })); }
    catch (error) { setError(failureText(error)); }
    finally { setBusy(false); }
  }
  return <main className="bot-install-page"><div className="bot-install-card lg-glass-sheet lg-glass-text" data-refract="0.72"><button type="button" className="text-button lg-interactive" disabled={busy} onClick={onBack}>{t('botShare.back')}</button><p className="bot-share-eyebrow">{t('botShare.sharedBot')}</p>
    {loading ? <div className="loading-row" role="status"><span className="spinner" />{t('botShare.loadingPreview')}</div> : loadError ? <><Empty icon="info" title={t('botShare.unavailable')} /><p className="twin-inline-error" role="alert">{loadError}</p><button type="button" className="secondary-button lg-interactive" onClick={() => setRevision(value => value + 1)}>{t('common.retry')}</button></> : preview && <>
      <h1>{preview.snapshot.name}</h1><p className="bot-install-assignment">{preview.snapshot.assignment}</p><SnapshotFacts snapshot={preview.snapshot} />
      <p className="bot-install-source">{t('botShare.sourceExplanation')}</p><p className="form-note">{t('botShare.installHint')}</p><p className="form-note">{t('botShare.permissionsHint')}</p>
      {preview.installed_bot && <p className="bot-share-notice" role="status"><Icon name="check" size={16} />{t('botShare.alreadyInstalled')}</p>}
      {error && <p className="twin-inline-error" role="alert">{error}</p>}
      <button type="button" className="primary-button bot-install-action lg-interactive" disabled={busy} onClick={() => { void install(); }}>{t(busy ? 'botShare.installing' : preview.installed_bot ? 'botShare.openInstalled' : 'botShare.install')}</button>
    </>}
  </div></main>;
}
