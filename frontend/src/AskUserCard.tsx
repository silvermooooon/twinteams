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
      {preview.author_name && <><dt>{t('questions.inReplyTo')}</dt><dd><strong>{preview.author_name}</strong>{preview.original_body && <div>{preview.original_body}</div>}{preview.original_files?.map((name, i) => <div key={i}>{name}</div>)}</dd></>}
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
  const unavailable = !!question.origin_unavailable;
  const preview = question.reply_preview;
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const command = useRef<AbortController | null>(null);
  const changed = useRef(onChanged); changed.current = onChanged;
  useEffect(() => () => command.current?.abort(), [api, question.id]);
  useEffect(() => { if (question.status !== 'pending') setError(''); }, [question.status]);
  async function submit(value?: string) {
    if (command.current || question.status !== 'pending') return;
    const controller = new AbortController(); command.current = controller; setBusy(true); setError('');
    try {
      const result = await api.request<UserQuestion>(`/questions/${encodeURIComponent(question.id)}/${value === undefined ? 'dismiss' : 'answer'}`, { method: 'POST', signal: controller.signal, body: JSON.stringify(value === undefined ? {} : { answer: value, selected_option: true }) });
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
    <div className="ask-user-heading"><Icon name={question.status === 'answered' ? 'check' : question.status === 'cancelled' ? 'close' : 'reply'} size={16} /><strong>{unavailable && question.status === 'pending' ? 'Question unavailable' : preview ? (question.status === 'pending' ? 'Waiting for your confirmation' : question.answer === 'Confirm send' ? 'Sent in your name' : 'Not sent') : question.status === 'pending' ? 'Waiting for your answer' : question.status === 'answered' ? 'Answered' : 'Dismissed'}</strong>{question.status === 'pending' && !preview && !unavailable && <IconButton icon="close" label="Dismiss question" size={15} disabled={busy} onClick={() => { void submit(); }} />}</div>
    <MessageMarkdown text={question.question} />
    {preview && <OwnerReplyPreview preview={preview} />}
    {!preview && question.origin_chat_title && !question.origin_unavailable && <a className="ask-user-origin" href="#/chat" onClick={event => { event.preventDefault(); onOrigin(question.origin_chat_id); }} title={`Open ${question.origin_chat_title}`}><Icon name="chat" size={13} /><span>From {question.origin_chat_title}</span><Icon name="arrow" size={13} /></a>}
    {question.status === 'pending' ? !unavailable && <>
      {!!question.options.length && <div className="ask-user-options" role="group" aria-label={preview ? "Send confirmation" : "Suggested answers"}>{question.options.map((option, index) => <button type="button" className={preview && option === 'Confirm send' ? 'primary-button' : 'secondary-button'} key={index} disabled={busy} onClick={() => { void submit(option); }}>{option}</button>)}</div>}
      {!preview && <small className="ask-user-hint">Reply in chat.</small>}
    </> : preview ? null : question.status === 'answered' && question.answer !== null ? <div className="ask-user-saved-answer"><small>Your answer</small><p>{question.answer}</p></div> : <small className="ask-user-hint">Your Duet knows you chose not to answer.</small>}
    {error && <p className="ask-user-error" role="alert">{error}</p>}
  </section>;
}
