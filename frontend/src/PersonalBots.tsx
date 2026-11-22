import { useEffect, useRef, useState } from 'react';
import { LiquiGlass } from '@liqui-design/glass';
import { Button } from '@/components/ui/button';
import { DialogActions } from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { LIQUI_SHEET_GLASS } from '@/lib/liquiPresets';
import { ApiError, requestKey } from './api/client';
import type { Actor, ApiClient, Chat, ChatDetail, Coordination, PersonalBot } from './api/client';
import { Avatar, Empty, Icon, Modal, dateTime } from './ui';
import { MessageMarkdown } from './MessageMarkdown';
import { MessageAttachments } from './Attachments';
import { t } from './i18n';
import './PersonalBots.css';

function errorText(error: unknown) {
  if (error instanceof ApiError && [403, 404].includes(error.status)) return t('bots.unavailable');
  return error instanceof Error ? error.message : t('common.error');
}

function BotChoices({ bots, selected, onChange, disabled }: { bots: PersonalBot[]; selected: string[]; onChange: (ids: string[]) => void; disabled: boolean }) {
  return <fieldset className="bot-choices" disabled={disabled}><legend>{t('bots.select')}</legend>{bots.length ? bots.map(bot => <label className="bot-choice" key={bot.id}><Switch lens={false} checked={selected.includes(bot.id)} disabled={disabled} aria-label={bot.name} onCheckedChange={() => onChange(selected.includes(bot.id) ? selected.filter(id => id !== bot.id) : [...selected, bot.id])} /><Avatar actor={bot.actor} size={30} /><span><strong>{bot.name}</strong><small>{bot.assignment}</small></span></label>) : <p className="form-note">{t('bots.emptyDetail')}</p>}</fieldset>;
}

export function BotEditorDialog({ api, bot, onClose, onSaved, onShare, onSkills }: { api: ApiClient; bot?: PersonalBot; onClose: () => void; onSaved: (bot: PersonalBot) => void; onShare?: () => void; onSkills?: () => void }) {
  const [name, setName] = useState(bot?.name || '');
  const [assignment, setAssignment] = useState(bot?.assignment || '');
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const changed = name.trim() !== bot?.name || assignment.trim() !== bot?.assignment;
  async function save(next?: () => void) {
    if (!name.trim() || !assignment.trim() || busy) return;
    if (next && !changed) { next(); return; }
    setBusy(true); setError('');
    try { const data = { name: name.trim(), assignment: assignment.trim() }; const saved = bot ? await api.patch<PersonalBot>(`/bots/${encodeURIComponent(bot.id)}`, data) : await api.post<PersonalBot>('/bots', data); onSaved(saved); if (next) next(); else onClose(); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }
  return <Modal title={t(bot ? 'bots.edit' : 'bots.create')} closeDisabled={busy} onClose={onClose}><form onSubmit={event => { event.preventDefault(); void save(); }}>
    <p className="form-note">{t('bots.personalHint')}</p><label>{t('bots.name')}<input aria-label={t('bots.name')} required maxLength={128} value={name} disabled={busy} onChange={event => setName(event.target.value)} /></label><label>{t('bots.assignment')}<textarea aria-label={t('bots.assignment')} required rows={5} value={assignment} disabled={busy} onChange={event => setAssignment(event.target.value)} placeholder={t('bots.assignmentPlaceholder')} /></label><p className="form-note">{t('bots.assignmentHint')}</p>
    {bot && <div className="bot-editor-links">{onShare && <Button type="button" variant="ghost" size="sm" disabled={busy || !name.trim() || !assignment.trim()} onClick={() => { void save(onShare); }}><Icon name="arrow" size={15} />{t(changed ? 'botShare.saveAndShare' : 'botShare.title')}</Button>}{onSkills && <Button type="button" variant="ghost" size="sm" disabled={busy || !name.trim() || !assignment.trim()} onClick={() => { void save(onSkills); }}><Icon name="spark" size={15} />{t(changed ? 'botSkills.saveAndManage' : 'botSkills.manage')}</Button>}</div>}
    {error && <p className="twin-inline-error" role="alert">{error}</p>}<DialogActions><Button type="button" variant="secondary" size="sm" disabled={busy} onClick={onClose}>{t('common.cancel')}</Button><Button type="submit" variant="accent" size="sm" disabled={busy || !name.trim() || !assignment.trim()}>{t(busy ? 'common.loading' : bot ? 'common.save' : 'bots.create')}</Button></DialogActions>
  </form></Modal>;
}

export function BotDispatchDialog({ api, bots, onClose, onSent }: { api: ApiClient; bots: PersonalBot[]; onClose: () => void; onSent: (chatId: string) => void }) {
  const [selected, setSelected] = useState<string[]>([]); const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const key = useRef(requestKey());
  return <Modal title={t('bots.dispatch')} closeDisabled={busy} onClose={onClose}><form onSubmit={async event => {
    event.preventDefault(); if (!selected.length || !body.trim() || busy) return;
    setBusy(true); setError('');
    try { const result = await api.post<{ chat_id: string }>('/bots/dispatch', { bot_ids: selected, body: body.trim(), client_request_key: key.current }); onSent(result.chat_id); onClose(); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }}><p className="form-note">{t('bots.dispatchHint')}</p><BotChoices bots={bots} selected={selected} disabled={busy} onChange={ids => { setSelected(ids); key.current = requestKey(); }} /><label>{t('bots.task')}<textarea aria-label={t('bots.task')} required rows={5} value={body} disabled={busy} onChange={event => { setBody(event.target.value); key.current = requestKey(); }} placeholder={t('bots.taskPlaceholder')} /></label>{error && <p className="twin-inline-error" role="alert">{error}</p>}<DialogActions><Button type="button" variant="secondary" size="sm" disabled={busy} onClick={onClose}>{t('common.cancel')}</Button><Button type="submit" variant="accent" size="sm" disabled={busy || !selected.length || !body.trim()}>{t(busy ? 'common.loading' : 'bots.dispatchAction')}</Button></DialogActions></form></Modal>;
}

export function AgentMessageDialog({ api, agents, initialSender, onClose, onSent }: { api: ApiClient; agents: Actor[]; initialSender: string; onClose: () => void; onSent: (chatId: string) => void }) {
  const [sender, setSender] = useState(initialSender); const [recipient, setRecipient] = useState(agents.find(actor => actor.id !== initialSender)?.id || '');
  const [body, setBody] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const key = useRef(requestKey());
  return <Modal title={t('bots.message')} closeDisabled={busy} onClose={onClose}><form onSubmit={async event => {
    event.preventDefault(); if (!sender || !recipient || !body.trim() || busy) return;
    setBusy(true); setError('');
    try { const result = await api.post<{ chat_id: string }>('/bots/messages', { sender_actor_id: sender, recipient_actor_id: recipient, body: body.trim(), client_request_key: key.current }); onSent(result.chat_id); onClose(); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }}><p className="form-note">{t('bots.messageHint')}</p><label>{t('bots.from')}<select aria-label={t('bots.from')} value={sender} disabled={busy} onChange={event => { const value = event.target.value; setSender(value); if (recipient === value) setRecipient(agents.find(actor => actor.id !== value)?.id || ''); key.current = requestKey(); }}>{agents.map(actor => <option key={actor.id} value={actor.id}>{actor.name}</option>)}</select></label><label>{t('bots.to')}<select aria-label={t('bots.to')} required value={recipient} disabled={busy} onChange={event => { setRecipient(event.target.value); key.current = requestKey(); }}>{agents.filter(actor => actor.id !== sender).map(actor => <option key={actor.id} value={actor.id}>{actor.name}</option>)}</select></label>{agents.length < 2 && <p className="form-note">{t('bots.emptyDetail')}</p>}<label>{t('chat.message')}<textarea aria-label={t('chat.message')} required rows={5} value={body} disabled={busy} onChange={event => { setBody(event.target.value); key.current = requestKey(); }} /></label>{error && <p className="twin-inline-error" role="alert">{error}</p>}<DialogActions><Button type="button" variant="secondary" size="sm" disabled={busy} onClick={onClose}>{t('common.cancel')}</Button><Button type="submit" variant="accent" size="sm" disabled={busy || !sender || !recipient || !body.trim()}>{t(busy ? 'common.loading' : 'bots.sendMessage')}</Button></DialogActions></form></Modal>;
}

export function BotRoomDialog({ api, bots, mainDuet, onClose, onCreated }: { api: ApiClient; bots: PersonalBot[]; mainDuet?: Actor; onClose: () => void; onCreated: (chat: Chat) => void }) {
  const [title, setTitle] = useState(''); const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  return <Modal title={t('bots.newRoom')} closeDisabled={busy} onClose={onClose}><form onSubmit={async event => {
    event.preventDefault(); if (!title.trim() || !selected.length || busy) return;
    setBusy(true); setError('');
    try { const chat = await api.post<Chat>('/bot-rooms', { title: title.trim(), bot_ids: selected }); onCreated(chat); onClose(); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }}><p className="form-note">{t('bots.roomHint')}</p><label>{t('bots.roomName')}<input aria-label={t('bots.roomName')} required maxLength={256} value={title} disabled={busy} onChange={event => setTitle(event.target.value)} /></label><p className="bot-room-supervisor"><Avatar actor={mainDuet} size={26} /><span>{t('bots.mainIncluded', { name: mainDuet?.name || t('chat.myTwin') })}</span></p><BotChoices bots={bots} selected={selected} disabled={busy} onChange={setSelected} />{error && <p className="twin-inline-error" role="alert">{error}</p>}<DialogActions><Button type="button" variant="secondary" size="sm" disabled={busy} onClick={onClose}>{t('common.cancel')}</Button><Button type="submit" variant="accent" size="sm" disabled={busy || !title.trim() || !selected.length}>{t(busy ? 'common.loading' : 'bots.createRoom')}</Button></DialogActions></form></Modal>;
}

export function RoomRecipients({ members, selected, onChange }: { members: Actor[]; selected: string[]; onChange: (ids: string[]) => void }) {
  return <LiquiGlass {...LIQUI_SHEET_GLASS} radius={16} blur={12} frost={0.86} className="room-recipients-wrap lq-sheet-surface" contentClassName="room-recipients-inner"><fieldset className="room-recipients"><legend>{t('bots.roomRecipients')}</legend>{members.map(actor => <label key={actor.id}><Switch lens={false} checked={selected.includes(actor.id)} aria-label={actor.name} onCheckedChange={() => onChange(selected.includes(actor.id) ? selected.filter(id => id !== actor.id) : [...selected, actor.id])} /><Avatar actor={actor} size={20} /><span>{actor.name}</span></label>)}{!selected.length && <span className="form-note">{t('bots.chooseRecipient')}</span>}</fieldset></LiquiGlass>;
}

export function CoordinationLinks({ coordination, actors, chatId, onOpen }: { coordination: Coordination; actors: Actor[]; chatId: string; onOpen: (chatId: string) => void }) {
  const links = [...new Set([coordination.source_chat_id, coordination.target_chat_id].filter((id): id is string => !!id && id !== chatId))];
  const recipientIds = coordination.source_chat_id ? coordination.actor_ids?.slice(1) : coordination.actor_ids;
  const recipients = actors.filter(actor => recipientIds?.includes(actor.id)).map(actor => actor.name).join(', ');
  const label = coordination.status === 'failed' ? 'bots.taskFailed' : coordination.status === 'cancelled' ? 'bots.taskCancelled' : coordination.kind === 'result' && coordination.has_result === false ? 'bots.noResult' : coordination.kind === 'assignment' ? 'bots.assignmentLabel' : coordination.kind === 'result' ? 'bots.resultLabel' : 'bots.messageLabel';
  return <div className="coordination-links"><span><Icon name={coordination.status === 'failed' ? 'info' : coordination.kind === 'result' ? 'check' : 'arrow'} size={13} />{recipients && coordination.kind !== 'result' ? t(coordination.kind === 'assignment' ? 'bots.assignedTo' : 'bots.messageTo', { names: recipients }) : t(label)}</span>{links.map(id => <Button type="button" variant="ghost" size="sm" key={id} className="coordination-link-btn" onClick={() => onOpen(id)}>{t(id === coordination.source_chat_id ? 'bots.openSource' : 'bots.openDestination')}<Icon name="arrow" size={12} /></Button>)}</div>;
}

export function BotTranscriptDialog({ api, bots, privateChatId, onClose, onSent }: { api: ApiClient; bots: PersonalBot[]; privateChatId: string; onClose: () => void; onSent: (chatId: string) => void }) {
  const [selected, setSelected] = useState(bots[0]?.id || '');
  return <Modal wide title={t('bots.inspect')} onClose={onClose}><p className="form-note">{t('bots.inspectHint')}</p>{bots.length ? <><label>{t('bots.selectOne')}<select aria-label={t('bots.selectOne')} value={selected} onChange={event => setSelected(event.target.value)}>{bots.map(bot => <option key={bot.id} value={bot.id}>{bot.name}</option>)}</select></label><BotTranscript key={selected} api={api} bot={bots.find(bot => bot.id === selected)!} privateChatId={privateChatId} onOpen={chatId => { onSent(chatId); onClose(); }} /></> : <Empty icon="chat" title={t('bots.empty')} detail={t('bots.emptyDetail')} />}</Modal>;
}

function BotTranscript({ api, bot, privateChatId, onOpen }: { api: ApiClient; bot: PersonalBot; privateChatId: string; onOpen: (chatId: string) => void }) {
  const [detail, setDetail] = useState<ChatDetail | null>(null); const [loading, setLoading] = useState(true); const [earlier, setEarlier] = useState(false); const [revision, setRevision] = useState(0);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const controller = useRef<AbortController | null>(null); const key = useRef(requestKey());
  useEffect(() => {
    const abort = new AbortController(); controller.current = abort; setLoading(true); setDetail(null); setError('');
    api.get<ChatDetail>(`/bots/${encodeURIComponent(bot.id)}/history?limit=30`, abort.signal).then(setDetail).catch(error => { if (!abort.signal.aborted) setError(errorText(error)); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [api, bot.id, revision]);
  async function loadEarlier() {
    if (!detail?.before_seq || earlier) return;
    setEarlier(true); setError('');
    try { const page = await api.get<ChatDetail>(`/bots/${encodeURIComponent(bot.id)}/history?limit=100&before_seq=${detail.before_seq}`, controller.current!.signal); setDetail(previous => previous && { ...previous, messages: [...page.messages, ...previous.messages], has_more: page.has_more, before_seq: page.before_seq }); }
    catch (error) { if (!controller.current?.signal.aborted) setError(errorText(error)); }
    finally { setEarlier(false); }
  }
  return <><p className="bot-transcript-assignment"><strong>{t('bots.assignment')}</strong>{bot.assignment}</p><div className="bot-transcript-actions"><Button type="button" variant="ghost" size="sm" disabled={loading} onClick={() => setRevision(value => value + 1)}><Icon name="refresh" size={14} />{t('common.refresh')}</Button><Button type="button" variant="secondary" size="sm" disabled={loading || !detail || busy} onClick={async () => {
    setBusy(true); setError('');
    try { await api.post(`/chats/${encodeURIComponent(privateChatId)}/messages`, { body: t('bots.reviewRequest', { name: bot.name, id: bot.id }), client_request_key: key.current }); onOpen(privateChatId); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }}>{t(busy ? 'common.loading' : 'bots.askReview')}</Button></div>{error && <p className="twin-inline-error" role="alert">{error}</p>}<div className="bot-transcript" aria-label={t('bots.transcriptFor', { name: bot.name })}>{loading ? <div className="loading-row" role="status">{t('common.loading')}</div> : detail ? <>{detail.has_more && <Button type="button" variant="ghost" size="sm" disabled={earlier} onClick={() => { void loadEarlier(); }}>{t(earlier ? 'common.loading' : 'shell.loadEarlier')}</Button>}{detail.messages.length ? detail.messages.map(message => <article key={message.id}><div className="bot-transcript-meta"><Avatar actor={detail.members.find(actor => actor.id === message.author_id)} size={26} /><strong>{detail.members.find(actor => actor.id === message.author_id)?.name}</strong><time>{dateTime(message.created_at)}</time></div>{message.coordination && <CoordinationLinks coordination={message.coordination} actors={detail.members} chatId={detail.chat.id} onOpen={onOpen} />}{message.reply_to && <div className="source-quote"><strong>{message.reply_to.author_name}</strong><span>{message.reply_to.unavailable ? t('chat.quoteUnavailable') : message.reply_to.body}</span></div>}<MessageMarkdown text={message.body} />{!!message.attachments?.length && <MessageAttachments attachments={message.attachments} />}</article>) : <Empty icon="chat" title={t('bots.transcriptEmpty')} detail={t('bots.transcriptEmptyDetail')} />}</> : <Empty icon="info" title={t('bots.transcriptUnavailable')} detail={t('bots.unavailable')} />}</div></>;
}
