import { useEffect, useRef, useState } from 'react';
import type { ApiClient, UserQuestion } from './api/client';
import { MessageMarkdown } from './MessageMarkdown';
import { Icon, IconButton } from './ui';
import './AskUserCard.css';

/** Content and terminal status come from the canonical chat snapshot. A pending
 * question never locks the normal composer or grants approval/credential access. */
export function AskUserCard({ api, question: snapshot, onChanged, onOrigin }: { api: ApiClient; question: UserQuestion; onChanged: () => Promise<void>; onOrigin: (chatId: string) => void }) {
  const [confirmed, setConfirmed] = useState<UserQuestion | null>(null);
  const question = snapshot.status === 'pending' ? confirmed || snapshot : snapshot;
  const [answer, setAnswer] = useState(''); const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const command = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const changed = useRef(onChanged); changed.current = onChanged;
  useEffect(() => () => command.current?.abort(), [api, question.id]);
  useEffect(() => { if (question.status !== 'pending') { setAnswer(''); setError(''); } }, [question.status]);
  useEffect(() => { if (input.current) { input.current.style.height = 'auto'; input.current.style.height = `${Math.min(120, Math.max(28, input.current.scrollHeight))}px`; } }, [answer]);
  async function submit(value?: string) {
    if (command.current || question.status !== 'pending' || value !== undefined && !value.trim()) return;
    const controller = new AbortController(); command.current = controller; setBusy(true); setError('');
    try {
      const result = await api.request<UserQuestion>(`/questions/${encodeURIComponent(question.id)}/${value === undefined ? 'dismiss' : 'answer'}`, { method: 'POST', signal: controller.signal, body: JSON.stringify(value === undefined ? {} : { answer: value }) });
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
  return <section className={`ask-user-card ${question.status}`} data-question-id={question.id} aria-label="Question from your Twin" aria-busy={busy}>
    <div className="ask-user-heading"><Icon name={question.status === 'answered' ? 'check' : question.status === 'cancelled' ? 'close' : 'reply'} size={16} /><strong>{question.status === 'pending' ? 'Waiting for your answer' : question.status === 'answered' ? 'Answered' : 'Dismissed'}</strong>{question.status === 'pending' && <IconButton icon="close" label="Dismiss question" size={15} disabled={busy} onClick={() => { void submit(); }} />}</div>
    <MessageMarkdown text={question.question} />
    {question.origin_chat_title && <a className="ask-user-origin" href="#/chat" onClick={event => { event.preventDefault(); onOrigin(question.origin_chat_id); }} title={`Open ${question.origin_chat_title}`}><Icon name="chat" size={13} /><span>From {question.origin_chat_title}</span><Icon name="arrow" size={13} /></a>}
    {question.status === 'pending' ? <>
      {!!question.options.length && <div className="ask-user-options" role="group" aria-label="Suggested answers">{question.options.map((option, index) => <button type="button" className="secondary-button" key={index} disabled={busy} onClick={() => { void submit(option); }}>{option}</button>)}</div>}
      <form className="ask-user-answer" onSubmit={event => { event.preventDefault(); void submit(answer); }}><textarea ref={input} rows={1} aria-label="Your answer" placeholder="Type an answer, or reply in the conversation" value={answer} disabled={busy} onChange={event => setAnswer(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !event.nativeEvent.isComposing) { event.preventDefault(); void submit(answer); } }} /><IconButton icon="send" label="Send answer" disabled={busy || !answer.trim()} onClick={() => { void submit(answer); }} /></form>
      <small className="ask-user-hint">You can keep chatting while this question is open.</small>
    </> : question.status === 'answered' && question.answer !== null ? <div className="ask-user-saved-answer"><small>Your answer</small><p>{question.answer}</p></div> : <small className="ask-user-hint">Your Twin knows you chose not to answer.</small>}
    {error && <p className="ask-user-error" role="alert">{error}</p>}
  </section>;
}
