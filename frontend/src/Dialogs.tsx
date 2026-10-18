import { useEffect, useId, useRef, useState } from 'react';
import type { Actor, ApiClient, Chat, ChatDetail, ChatSettings, HistoryPolicy, Reminder } from './api/client';
import { Avatar, Icon, Modal, dateTime } from './ui';
import { t } from './i18n';
import './TeamsOverlays.css';
type PeoplePickerProps = { api: ApiClient; ownerId: string; chat?: Chat; initialPerson?: Actor; onClose: () => void; onDone: (chat: Chat) => void; onError: (error: unknown) => void; inline?: boolean; onBusyChange?: (busy: boolean) => void };

/** Shared recipient selection for a new message and the add-members dialog. */
export function PeoplePicker({ api, ownerId, chat, initialPerson, onClose, onDone, onError, inline = false, onBusyChange }: PeoplePickerProps) {
  const [query, setQuery] = useState('');
  const [people, setPeople] = useState<Actor[]>([]);
  const [selected, setSelected] = useState<Actor[]>(initialPerson ? [initialPerson] : []);
  const [title, setTitle] = useState('');
  const [policy, setPolicy] = useState<HistoryPolicy>('all');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(!inline);
  const [activeIndex, setActiveIndex] = useState(-1);
  const input = useRef<HTMLInputElement>(null);
  const recipients = useRef<HTMLDivElement>(null);
  const listId = useId();
  useEffect(() => { if (activeIndex >= 0) document.getElementById(`${listId}-${activeIndex}`)?.scrollIntoView({ block: 'nearest' }); }, [activeIndex, listId]);
  useEffect(() => {
    const abort = new AbortController();
    const timer = setTimeout(() => {
      setLoading(true);
      api.get<Actor[]>(`/people?q=${encodeURIComponent(query)}`, abort.signal).then(setPeople).catch(error => { if (!abort.signal.aborted) onError(error); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    }, 180);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [api, query]);
  useEffect(() => {
    if (!inline) return;
    const frame = requestAnimationFrame(() => input.current?.focus());
    const outside = (event: PointerEvent) => { if (!recipients.current?.contains(event.target as Node)) setSuggestionsOpen(false); };
    document.addEventListener('pointerdown', outside);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('pointerdown', outside); };
  }, [inline]);
  const available = people.filter(person => person.id !== ownerId && !chat?.member_owner_ids.includes(person.id));
  const suggestions = inline ? available.filter(person => !selected.some(item => item.id === person.id)) : available;
  function togglePerson(person: Actor) {
    setSelected(previous => previous.some(item => item.id === person.id) ? previous.filter(item => item.id !== person.id) : [...previous, person]);
    setActiveIndex(-1);
    if (inline) { setQuery(''); input.current?.focus(); }
  }
  return <form className={`people-picker ${inline ? 'people-picker-inline' : ''}`} aria-busy={busy} onSubmit={async event => {
    event.preventDefault(); if (!selected.length || busy) return;
    setBusy(true); onBusyChange?.(true);
    try {
      const result = await api.post<Chat>(chat ? `/chats/${chat.id}/members` : '/chats', { owner_ids: selected.map(person => person.id), history_policy: policy, ...(!chat && selected.length >= 2 ? { title: title.trim() || undefined } : {}) });
      onDone(result);
    } catch (error) { onError(error); } finally { setBusy(false); onBusyChange?.(false); }
  }}>
    {!inline && <p className="form-note">{t('chat.paired')}</p>}
    <div ref={recipients} className="people-recipient-section">
      <div className="people-recipient-field">
        {inline ? <label className="people-to-label" htmlFor={`${listId}-input`}>{t('chat.to')}</label> : <Icon name="search" size={17} />}
        {inline && selected.map(person => <button disabled={busy} className="recipient-chip" type="button" key={person.id} aria-label={`${t('common.close')} ${person.name}`} onClick={() => togglePerson(person)}>{person.name}<Icon name="close" size={12} /></button>)}
        <input ref={input} id={`${listId}-input`} role="combobox" aria-autocomplete="list" aria-expanded={suggestionsOpen} aria-controls={listId} aria-activedescendant={suggestionsOpen && activeIndex >= 0 && activeIndex < suggestions.length ? `${listId}-${activeIndex}` : undefined} autoComplete="off" aria-label={t('search.placeholder')} placeholder={t(inline ? 'chat.recipientPlaceholder' : 'search.placeholder')} value={query} disabled={busy} onFocus={() => setSuggestionsOpen(true)} onChange={event => { setQuery(event.target.value); setSuggestionsOpen(true); setActiveIndex(-1); }} onKeyDown={event => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === 'Escape' && inline && suggestionsOpen) { event.stopPropagation(); event.preventDefault(); setSuggestionsOpen(false); }
          else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setSuggestionsOpen(true); setActiveIndex(previous => suggestions.length ? previous < 0 ? (event.key === 'ArrowDown' ? 0 : suggestions.length - 1) : (previous + (event.key === 'ArrowDown' ? 1 : -1) + suggestions.length) % suggestions.length : -1); }
          else if (event.key === 'Enter') { event.preventDefault(); const person = suggestions[activeIndex >= 0 ? activeIndex : 0]; if (suggestionsOpen && person) togglePerson(person); }
          else if (event.key === 'Backspace' && !query && inline && selected.length) setSelected(previous => previous.slice(0, -1));
        }} />
      </div>
      {suggestionsOpen && <div className={`people-options ${inline ? 'people-suggestions' : ''}`} id={listId} role="listbox" aria-label={t('search.placeholder')} aria-multiselectable="true">
        {loading && !people.length ? <p className="muted" role="status">{t('common.loading')}</p> : !suggestions.length ? <p className="muted">{t('search.empty')}</p> : suggestions.map((person, index) => <button type="button" role="option" aria-selected={selected.some(item => item.id === person.id)} id={`${listId}-${index}`} className={`person-option ${activeIndex === index ? 'keyboard-active' : ''}`} key={person.id} disabled={busy} onMouseDown={event => event.preventDefault()} onClick={() => togglePerson(person)}><Avatar actor={person} size={32} /><span>{person.name}</span>{selected.some(item => item.id === person.id) && <Icon name="check" size={16} />}</button>)}
      </div>}
    </div>
    {!inline && !!selected.length && <div className="selected-people" aria-label={t('chat.selectedPeople')}>{selected.map(person => <button disabled={busy} type="button" key={person.id} aria-label={`${t('common.close')} ${person.name}`} onClick={() => togglePerson(person)}>{person.name}<Icon name="close" size={12} /></button>)}</div>}
    {(!inline || !!selected.length) && <div className="people-picker-details">
      {inline && <p className="form-note">{t('chat.paired')}</p>}
      {!chat && selected.length >= 2 && <label>{t('chat.name')}<input maxLength={256} value={title} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>}
      <label>{t('chat.history')}<select value={policy} disabled={busy} onChange={event => setPolicy(event.target.value as HistoryPolicy)}><option value="all">{t('chat.historyAll')}</option><option value="fixed_7d">{t('chat.history7d')}</option><option value="no_prior">{t('chat.historyNone')}</option></select></label>
      <div className="modal-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>{t('common.cancel')}</button><button className="primary-button" disabled={!selected.length || busy}>{busy ? t('common.loading') : t(chat ? 'common.add' : 'search.start')}</button></div>
    </div>}
  </form>;
}
export function PeopleDialog(props: PeoplePickerProps) { const [busy, setBusy] = useState(false); return <Modal closeDisabled={busy} title={t(props.chat ? 'chat.addMembers' : 'chat.new')} onClose={props.onClose}><PeoplePicker {...props} onBusyChange={setBusy} /></Modal>; }
export function RenameChatDialog({ api, chat, onClose, onSaved }: { api: ApiClient; chat: Chat; onClose: () => void; onSaved: (chat: Chat) => void }) {
  const [title, setTitle] = useState(chat.title); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  return <Modal title={t('chat.rename')} closeDisabled={busy} onClose={onClose}><form onSubmit={async event => {
    event.preventDefault(); if (!title.trim() || title.trim() === chat.title || busy) return;
    setBusy(true); setError('');
    try { const updated = await api.patch<Chat>(`/chats/${chat.id}`, { title: title.trim() }); onSaved(updated); onClose(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : t('common.error')); }
    finally { setBusy(false); }
  }}><label>{t('chat.groupName')}<input aria-label={t('chat.groupName')} required maxLength={256} value={title} disabled={busy} onChange={event => setTitle(event.target.value)} /></label>{error && <p className="twin-inline-error" role="alert">{error}</p>}<div className="modal-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>{t('common.cancel')}</button><button className="primary-button" disabled={busy || !title.trim() || title.trim() === chat.title}>{t(busy ? 'common.loading' : 'common.save')}</button></div></form></Modal>;
}
export function SettingsDialog({ api, detail, onClose, onSaved, onError }: { api: ApiClient; detail: ChatDetail; onClose: () => void; onSaved: () => void; onError: (error: unknown) => void }) {
  const [globalPrompt, setGlobalPrompt] = useState(''); const [chatPrompt, setChatPrompt] = useState(detail.settings.prompt_override || ''); const [override, setOverride] = useState(detail.settings.prompt_override !== null); const [busy, setBusy] = useState(false); const [loaded, setLoaded] = useState(false);
  useEffect(() => { let active = true; api.get<{ default_reply_prompt: string }>('/settings').then(result => { if (active) { setGlobalPrompt(result.default_reply_prompt); setLoaded(true); } }).catch(onError); return () => { active = false; }; }, [api]);
  return <Modal closeDisabled={busy} title={t('settings.title')} onClose={onClose} wide><form onSubmit={async e => { e.preventDefault(); setBusy(true); try { await api.patch('/settings', { default_reply_prompt: globalPrompt }); await api.patch<ChatSettings>(`/chats/${detail.chat.id}/settings`, { prompt_override: override ? chatPrompt : null }); onSaved(); onClose(); } catch (error) { onError(error); } finally { setBusy(false); } }}><p className="form-note">{t('settings.promptHint')}</p><label>{t('settings.defaultPrompt')}<textarea rows={4} value={globalPrompt} disabled={!loaded} onChange={e => setGlobalPrompt(e.target.value)} /></label><label className="checkbox-label"><input type="checkbox" checked={override} onChange={e => setOverride(e.target.checked)} />{t('settings.override')}</label>{override ? <label>{t('settings.chatPrompt')}<textarea rows={4} value={chatPrompt} onChange={e => setChatPrompt(e.target.value)} /></label> : <p className="form-note">{t('settings.inherit')}</p>}<div className="modal-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>{t('common.cancel')}</button><button className="primary-button" disabled={busy || !loaded}>{t('common.save')}</button></div></form></Modal>;
}
export function ReminderDialog({ api, onClose, onDone, onError }: { api: ApiClient; onClose: () => void; onDone: () => void; onError: (error: unknown) => void }) {
  const [title, setTitle] = useState(''); const [detail, setDetail] = useState(''); const [due, setDue] = useState(''); const [busy, setBusy] = useState(false);
  return <Modal closeDisabled={busy} title={t('reminders.new')} onClose={onClose}><form onSubmit={async e => { e.preventDefault(); if (!title.trim()) return; setBusy(true); try { await api.post('/reminders', { title: title.trim(), detail: detail.trim(), due_at: due ? new Date(due).toISOString() : null }); onDone(); onClose(); } catch (error) { onError(error); } finally { setBusy(false); } }}><label>{t('reminders.item')}<input required placeholder={t('reminders.itemPlaceholder')} value={title} onChange={e => setTitle(e.target.value)} /></label><label>{t('reminders.detail')}<textarea rows={3} value={detail} onChange={e => setDetail(e.target.value)} /></label><label>{t('reminders.due')}<input type="datetime-local" value={due} onChange={e => setDue(e.target.value)} /></label><div className="modal-actions"><button type="button" className="secondary-button" disabled={busy} onClick={onClose}>{t('common.cancel')}</button><button className="primary-button" disabled={!title.trim() || busy}>{t('reminders.add')}</button></div></form></Modal>;
}
export function RemindersPage({ reminders, completed, onToggle, onNew, onChat }: { reminders: Reminder[]; completed: boolean; onToggle: (reminder: Reminder) => void; onNew: () => void; onChat: (chatId: string) => void }) {
  const visible = reminders.filter(item => item.done === completed).sort((a, b) => (a.due_at || 'z').localeCompare(b.due_at || 'z'));
  return <main className="reminders-main"><header className="reminders-header"><h2>{t('nav.reminders')}</h2><span className="muted">{t('reminders.stored')}</span></header><div className="reminders-content"><div className="reminders-title"><div><h2>{t(completed ? 'reminders.completed' : 'reminders.title')}</h2><p>{t('reminders.summary', { count: visible.length })}</p></div><button className="primary-button" onClick={onNew}><Icon name="plus" size={16} />{t('reminders.new')}</button></div><div className="reminder-cards">{visible.length ? visible.map(reminder => <article className={`reminder-card ${reminder.done ? 'done' : ''}`} key={reminder.id}><button className="complete-button" onClick={() => onToggle(reminder)} title={t(reminder.done ? 'reminders.reopen' : 'reminders.complete')} aria-label={t(reminder.done ? 'reminders.reopen' : 'reminders.complete')}>{reminder.done && <Icon name="check" size={14} />}</button><div><h3>{reminder.title}</h3>{reminder.detail && <p>{reminder.detail}</p>}<span className="reminder-time"><Icon name="clock" size={14} />{reminder.due_at ? dateTime(reminder.due_at) : t('reminders.noTime')}</span>{reminder.chat_id && <button className="text-button" onClick={() => onChat(reminder.chat_id!)}>{t('reminders.openChat')}<Icon name="arrow" size={14} /></button>}</div></article>) : <div className="reminder-empty"><Icon name="check" size={30} /><h3>{t(completed ? 'reminders.noCompleted' : 'reminders.noPending')}</h3></div>}</div></div></main>;
}
