import { useEffect, useRef, useState } from 'react';
import type { ApiClient, PersonalBot } from './api/client';
import type { SkillDetail, TwinSkill } from './api/twin';
import { Empty, Icon, IconButton, Modal } from './ui';
import { t } from './i18n';
import './BotSharing.css';

function errorText(error: unknown) { return error instanceof Error ? error.message : t('common.error'); }

export function BotSkillsDialog({ api, bot, onClose }: { api: ApiClient; bot: PersonalBot; onClose: () => void }) {
  const [items, setItems] = useState<TwinSkill[] | null>(null); const [loading, setLoading] = useState(true); const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [loadError, setLoadError] = useState(''); const [notice, setNotice] = useState('');
  const uploadInput = useRef<HTMLInputElement>(null); const base = `/bots/${encodeURIComponent(bot.id)}/skills`;
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setLoadError('');
    api.get<{ items: TwinSkill[] }>(base, controller.signal).then(value => { if (!controller.signal.aborted) setItems(value.items); }).catch(error => { if (!controller.signal.aborted) setLoadError(errorText(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, base, revision]);
  async function toggle(skill: TwinSkill) {
    if (busy) return; setBusy(true); setError(''); setNotice('');
    try { const saved = await api.patch<TwinSkill>(`${base}/${encodeURIComponent(skill.name)}`, { enabled: !skill.enabled }); setItems(previous => previous!.map(item => item.name === saved.name ? saved : item)); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  async function upload(files: FileList) {
    if (busy) return; setBusy(true); setError(''); setNotice('');
    try { const body = new FormData(); for (const file of files) body.append('files', file, file.webkitRelativePath); const saved = await api.request<{ items: TwinSkill[] }>(`${base}/upload`, { method: 'POST', body }); setItems(saved.items); setNotice(t('twin.skillUploaded')); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); if (uploadInput.current) uploadInput.current.value = ''; }
  }
  return <Modal wide title={t('botSkills.title', { name: bot.name })} closeDisabled={busy} onClose={onClose}>
    <p className="form-note">{t('botSkills.hint')}</p>
    {selected ? <BotSkillEditor key={selected} api={api} base={base} name={selected} onBusy={setBusy} onBack={() => setSelected(null)} onSaved={() => setRevision(value => value + 1)} onDeleted={items => { setItems(items); setSelected(null); setNotice(t('botSkills.removed')); }} /> : <>
      <div className="bot-skill-actions"><button type="button" className="secondary-button lg-interactive" disabled={busy || loading || !!loadError} onClick={() => uploadInput.current?.click()}><Icon name="upload" size={15} />{t(busy ? 'common.loading' : 'twin.uploadSkill')}</button><IconButton icon="refresh" label={t('common.refresh')} disabled={busy || loading} onClick={() => setRevision(value => value + 1)} /><input ref={uploadInput} type="file" hidden multiple {...{ webkitdirectory: '' }} onChange={event => { if (event.target.files?.length) void upload(event.target.files); }} /></div>
      {notice && <p className="bot-share-notice" role="status">{notice}</p>}{error && <p className="twin-inline-error" role="alert">{error}</p>}
      {loading ? <div className="loading-row" role="status">{t('common.loading')}</div> : loadError ? <div role="alert" className="twin-inline-error"><p>{loadError}</p><button className="secondary-button lg-interactive" onClick={() => setRevision(value => value + 1)}>{t('common.retry')}</button></div> : !items?.length ? <Empty icon="spark" title={t('botSkills.noSkills')} detail={t('botSkills.empty')} /> : <div>{items.map(skill => <article className="bot-skill-row" key={skill.name}>
        <Icon name="spark" size={20} /><button type="button" className="twin-detail-link" disabled={busy} onClick={() => { setError(''); setNotice(''); setSelected(skill.name); }}><h3>{skill.name}</h3><p>{skill.description || t('twin.noDescription')}</p>{skill.error && <small className="twin-inline-error">{skill.error}</small>}</button>
        <button type="button" role="switch" aria-checked={skill.enabled} aria-label={t('twin.skillToggle', { name: skill.name })} className={`twin-switch lg-interactive ${skill.enabled ? 'on' : ''}`} disabled={busy || !skill.available && !skill.enabled} onClick={() => { void toggle(skill); }}><span /></button>
        <IconButton icon="edit" label={t('botSkills.edit', { name: skill.name })} disabled={busy} onClick={() => setSelected(skill.name)} />
      </article>)}</div>}
    </>}
  </Modal>;
}

function BotSkillEditor({ api, base, name, onBusy, onBack, onSaved, onDeleted }: { api: ApiClient; base: string; name: string; onBusy: (value: boolean) => void; onBack: () => void; onSaved: () => void; onDeleted: (items: TwinSkill[]) => void }) {
  const [detail, setDetail] = useState<SkillDetail | null>(null); const [content, setContent] = useState(''); const [loading, setLoading] = useState(true); const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [saved, setSaved] = useState(false); const [confirmDelete, setConfirmDelete] = useState(false);
  const path = `${base}/${encodeURIComponent(name)}`;
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    api.get<SkillDetail>(path, controller.signal).then(value => { if (!controller.signal.aborted) { setDetail(value); setContent(value.content); } }).catch(error => { if (!controller.signal.aborted) setError(errorText(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, path, revision]);
  async function save() {
    if (busy || !content.trim()) return; setBusy(true); onBusy(true); setError(''); setSaved(false);
    try { const saved = await api.put<SkillDetail>(path, { content }); setDetail(saved); setContent(saved.content); setSaved(true); onSaved(); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); onBusy(false); }
  }
  async function remove() {
    if (busy) return; setBusy(true); onBusy(true); setError('');
    try { const result = await api.delete<{ items: TwinSkill[] }>(path); onDeleted(result.items); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); onBusy(false); }
  }
  return <div className="bot-skill-editor"><div className="bot-skill-actions"><button type="button" className="text-button lg-interactive" disabled={busy} onClick={onBack}>{t('botSkills.back')}</button><strong>{name}</strong></div>
    {loading ? <div className="loading-row" role="status">{t('common.loading')}</div> : detail ? <><label>{t('botSkills.content')}<textarea aria-label={t('botSkills.content')} value={content} disabled={busy} spellCheck={false} onChange={event => { setContent(event.target.value); setSaved(false); }} /></label><p className="form-note">{t('botSkills.editHint')}</p>{!!detail.references.length && <p className="bot-skill-reference">{t('twin.references')}: {detail.references.join(', ')}</p>}{saved && <p className="bot-share-notice" role="status">{t('botSkills.saved')}</p>}<div className="modal-actions"><button type="button" className="text-button lg-interactive" disabled={busy} onClick={() => setConfirmDelete(true)}>{t('botSkills.remove')}</button><button type="button" className="primary-button lg-interactive" disabled={busy || !content.trim() || content === detail.content} onClick={() => { void save(); }}>{t(busy ? 'common.loading' : 'common.save')}</button></div></> : <button className="secondary-button lg-interactive" onClick={() => setRevision(value => value + 1)}>{t('common.retry')}</button>}
    {error && <p className="twin-inline-error" role="alert">{error}</p>}
    {confirmDelete && <div className="bot-skill-delete"><p>{t('botSkills.removeHint', { name })}</p><div className="modal-actions"><button type="button" className="secondary-button lg-interactive" disabled={busy} onClick={() => setConfirmDelete(false)}>{t('common.cancel')}</button><button type="button" className="danger-button lg-interactive" disabled={busy} onClick={() => { void remove(); }}>{t('botSkills.removeConfirm')}</button></div></div>}
  </div>;
}
