import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { DialogActions } from '@/components/ui/dialog';
import type { Actor, ApiClient } from './api/client';
import { Avatar, Icon, IconButton, Modal } from './ui';
import { t } from './i18n';
import './PersonCard.css';

type Contact = { owner_id: string; human: Actor; tags: string[]; last_interaction_at: string | null; revision: number };

export function PersonCard({ api, person, onClose }: { api: ApiClient; person: Actor; onClose: () => void }) {
  const [contact, setContact] = useState<Contact | null>(null);
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  const [error, setError] = useState('');
  const [editing, setEditing] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true); setError('');
    api.get<{ contact: Contact | null }>(`/me/twin/contacts/${encodeURIComponent(person.id)}`, abort.signal)
      .then(result => { setContact(result.contact); setTags(result.contact?.tags || []); })
      .catch(failure => { if (!abort.signal.aborted) setError(failure instanceof Error ? failure.message : t('common.error')); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [api, person.id, reload]);
  return <Modal title={t('contacts.personDetails')} onClose={onClose} closeDisabled={busy}>
    <div className="person-card">
      <div className="person-card-identity"><Avatar actor={person} size={52} /><h3>{person.name}</h3></div>
      <section className="person-card-tags" aria-label={t('contacts.tags')}>
        <div className="person-card-section-heading"><h3>{t('contacts.tags')}</h3>{contact && !editing && <Button type="button" variant="ghost" size="sm" onClick={() => { setTags(contact.tags.length ? [...contact.tags] : ['']); setSaved(false); setEditing(true); }}><Icon name="edit" size={14} />{t('contacts.editTags')}</Button>}</div>
        <p className="person-card-note">{t('contacts.private')}</p>
        {loading ? <p className="person-card-status" role="status">{t('common.loading')}</p> : error && !contact ? <><p className="twin-inline-error" role="alert">{error}</p><Button type="button" variant="ghost" size="sm" onClick={() => setReload(value => value + 1)}>{t('common.retry')}</Button></> : !contact ? <p className="person-card-status">{t('contacts.noInteraction')}</p> : editing ? <form onSubmit={async event => {
          event.preventDefault(); if (busy) return;
          setBusy(true); setError('');
          try {
            const updated = await api.put<Contact>(`/me/twin/contacts/${encodeURIComponent(person.id)}/tags`, { tags: tags.map(tag => tag.trim()).filter(Boolean) });
            setContact(updated); setTags(updated.tags); setEditing(false); setSaved(true);
          } catch (failure) { setError(failure instanceof Error ? failure.message : t('common.error')); }
          finally { setBusy(false); }
        }}>
          <p className="person-card-hint">{t('contacts.hint')}</p>
          <div className="person-card-tag-fields">{tags.map((tag, index) => <div className="person-card-tag-field" key={index}><input autoFocus={index === 0} disabled={busy} aria-label={t('contacts.tagNumber', { number: index + 1 })} maxLength={64} placeholder={t('contacts.placeholder')} value={tag} onChange={event => setTags(previous => previous.map((value, i) => i === index ? event.target.value : value))} /><IconButton icon="close" label={t('contacts.removeTag', { number: index + 1 })} disabled={busy} size={16} onClick={() => setTags(previous => previous.filter((_, i) => i !== index))} /></div>)}</div>
          {tags.length < 8 && <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => setTags(previous => [...previous, ''])}><Icon name="plus" size={14} />{t('contacts.addTag')}</Button>}
          {error && <p className="twin-inline-error" role="alert">{error}</p>}
          <DialogActions><Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => { setEditing(false); setError(''); }}>{t('common.cancel')}</Button><Button type="submit" variant="accent" size="sm" disabled={busy}>{t(busy ? 'common.loading' : 'common.save')}</Button></DialogActions>
        </form> : <>{contact.tags.length ? <div className="person-card-tag-list">{contact.tags.map(tag => <span key={tag}>{tag}</span>)}</div> : <p className="person-card-status">{t('contacts.empty')}</p>}{saved && <p className="person-card-saved" role="status">{t('contacts.saved')}</p>}</>}
      </section>
    </div>
  </Modal>;
}
