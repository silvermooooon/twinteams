import { useEffect, useRef, useState } from 'react';
import type { ApiClient, ReplyPreview, UserQuestion } from './api/client';
import { t } from './i18n';
import { MessageMarkdown } from './MessageMarkdown';
import { Icon, IconButton } from './ui';
import './AskUserCard.css';

function OwnerReplyPreview({ preview }: { preview: ReplyPreview }) {
  return <section aria-label={t('questions.replyPreview')} className="owner-reply-preview">
    <h3>{t('questions.replyPreview')}</h3>
    <dl className="detail-fields">
      <dt>{t('questions.destination')}</dt><dd>{preview.destination}</dd>
      <dt>{t('questions.visibleTo')}</dt><dd>{preview.audience.join(', ')}</dd>
      <dt>{t('questions.inReplyTo')}</dt><dd><strong>{preview.author_name}</strong>{preview.original_body && <div>{preview.original_body}</div>}{preview.original_files.map((name, i) => <div key={i}>{name}</div>)}</dd>
      {preview.body && <><dt>{t('questions.replyText')}</dt><dd>{preview.body}</dd></>}
      {!!preview.files.length && <><dt>{t('questions.replyFiles')}</dt><dd><ul>{preview.files.map((name, i) => <li key={i}>{name}</li>)}</ul></dd></>}
    </dl>
  </section>;
}

/** Content and terminal status come from the canonical chat snapshot. A pending
 * question never locks the normal composer or grants approval/credential access. */
export function AskUserCard({ api, question: snapshot, onChanged, onOrigin }: { api: ApiClient; question: UserQuestion; onChanged: () => Promise<void>; onOrigin: (chatId: string) => void }) {
  const [confirmed, setConfirmed] = useState<UserQuestion | null>(null);
  const question = snapshot.status === 'pending' ? confirmed || snapshot : snapshot;
  const [custom, setCustom] = useState(false);
  const preview = question.reply_preview;
  const [answer, setAnswer] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const command = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const changed = useRef(onChanged); changed.current = onChanged;
  useEffect(() => () => command.current?.abort(), [api, question.id]);
  useEffect(() => { if (question.status !== 'pending') { setAnswer(''); setError(''); } }, [question.status]);
  useEffect(() => { if (input.current) { input.current.style.height = 'auto'; input.current.style.height = `${Math.min(120, Math.max(28, input.current.scrollHeight))}px`; } }, [answer]);
  async function submit(value?: string, selectedOption = false) {
    if (command.current || question.status !== 'pending' || value !== undefined && !value.trim()) return;
    const controller = new AbortController(); command.current = controller; setBusy(true); setError('');
    try {
      const result = await api.request<UserQuestion>(`/questions/${encodeURIComponent(question.id)}/${value === undefined ? 'dismiss' : 'answer'}`, { method: 'POST', signal: controller.signal, body: JSON.stringify(value === undefined ? {} : selectedOption ? { answer: value, selected_option: true } : { answer: value }) });
      if (controller.signal.aborted) return;
      setConfirmed(result); // Server-confirmed state survives a failed chat reload.
      try { await changed.current(); } catch { if (!controller.signal.aborted) setError('Your response was saved. Reopen this conversation to refresh its task progress.'); }
    } catch (failure) {
      if (!controller.signal.aborted) {
        setError(failure instanceof Error ? failure.message : 'Could not update this question.');
        // A competing tab may already have answered. Reconcile its canonical
        // status while preserving the error if the question is still pending.
        try { await changed.current(); } catch { /* The next reconnect can refresh. */ }
      }
    } finally {
      if (command.current === controller) command.current = null;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return <section className={`ask-user-card ${question.status}`} data-question-id={question.id} aria-label="Question from your Duet" aria-busy={busy}>
    <div className="ask-user-heading"><Icon name={question.status === 'answered' ? 'check' : question.status === 'cancelled' ? 'close' : 'reply'} size={16} /><strong>{preview ? (question.status === 'pending' ? 'Waiting for your confirmation' : question.answer === 'Confirm send' ? 'Sent in your name' : 'Not sent') : question.status === 'pending' ? 'Waiting for your answer' : question.status === 'answered' ? 'Answered' : 'Dismissed'}</strong>{question.status === 'pending' && !preview && <IconButton icon="close" label="Dismiss question" size={15} disabled={busy} onClick={() => { void submit(); }} />}</div>
    <MessageMarkdown text={question.question} />
    {preview && <OwnerReplyPreview preview={preview} />}
    {!preview && question.origin_chat_title && <a className="ask-user-origin" href="#/chat" onClick={event => { event.preventDefault(); onOrigin(question.origin_chat_id); }} title={`Open ${question.origin_chat_title}`}><Icon name="chat" size={13} /><span>From {question.origin_chat_title}</span><Icon name="arrow" size={13} /></a>}
    {question.status === 'pending' ? <>
      {!!question.options.length && <div className="ask-user-options" role="group" aria-label={preview ? "Send confirmation" : "Suggested answers"}>{question.options.map((option, index) => <button type="button" className={preview && option === 'Confirm send' ? 'primary-button' : 'secondary-button'} key={index} disabled={busy} onClick={() => { void submit(option, true); }}>{option}</button>)}</div>}
      {!preview && !!question.options.length && !custom && <button type="button" className="text-button ask-user-custom" disabled={busy} onClick={() => { setCustom(true); requestAnimationFrame(() => input.current?.focus()); }}>{t('questions.customAnswer')}</button>}
      {!preview && (!question.options.length || custom) && <form className="ask-user-answer" onSubmit={event => { event.preventDefault(); void submit(answer); }}><textarea ref={input} rows={1} aria-label="Your answer" placeholder="Type an answer, or reply in the conversation" value={answer} disabled={busy} onChange={event => setAnswer(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(answer); } }} /><IconButton icon="send" label="Send answer" disabled={busy || !answer.trim()} onClick={() => { void submit(answer); }} /></form>}
      <small className="ask-user-hint">You can keep chatting while this question is open.</small>
    </> : preview ? null : question.status === 'answered' && question.answer !== null ? <div className="ask-user-saved-answer"><small>Your answer</small><p>{question.answer}</p></div> : <small className="ask-user-hint">Your Duet knows you chose not to answer.</small>}
    {error && <p className="ask-user-error" role="alert">{error}</p>}
  </section>;
}
