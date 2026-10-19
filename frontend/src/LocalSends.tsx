import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './api/client';
import type { Actor, ApiClient, ChatDetail, Message, ReplyRequest, SendAcknowledgment } from './api/client';
import { Avatar, Icon, time } from './ui';
import { MessageAttachments } from './Attachments';
import { t } from './i18n';

type Payload = { body: string; attachment_ids: string[]; client_request_key: string; reply_to_message_id?: string; reply_for_me: boolean };
export type LocalSend = {
  key: string; ownerId: string; chatId: string; epoch: number; payload: Payload; message: Message;
  phase: 'sending' | 'sent' | 'failed'; error?: string; uncertain?: boolean; acceptedAt?: number;
  messageId?: string; requestId?: string; requestFailed?: boolean;
};

/** In-memory local echoes only. Canonical HTTP content always replaces an echo. */
export function useLocalSends(api: ApiClient) {
  const [items, setItems] = useState<LocalSend[]>([]);
  const entries = useRef<LocalSend[]>([]); const epochs = useRef(new Map<string, number>());
  const owner = useRef(api.ownerId); owner.current = api.ownerId;
  const inFlight = useRef(new Set<string>());
  const refresh = useRef<(chatId: string) => void>(() => {});
  const update = useCallback((change: (previous: LocalSend[]) => LocalSend[]) => {
    entries.current = change(entries.current); setItems(entries.current);
  }, []);
  const clear = useCallback((chatId?: string) => {
    if (chatId) epochs.current.set(chatId, (epochs.current.get(chatId) || 0) + 1);
    else { for (const entry of entries.current) epochs.current.set(entry.chatId, (epochs.current.get(entry.chatId) || 0) + 1); }
    update(previous => chatId ? previous.filter(item => item.chatId !== chatId) : []);
    api.cancelCommands(chatId);
  }, [api, update]);
  useEffect(() => { entries.current = []; setItems([]); epochs.current.clear(); return () => { api.cancelCommands(); }; }, [api]);
  const current = (entry: LocalSend) => owner.current === entry.ownerId && (epochs.current.get(entry.chatId) || 0) === entry.epoch && entries.current.some(item => item.key === entry.key);
  async function transmit(entry: LocalSend) {
    if (!current(entry) || inFlight.current.has(entry.key)) return;
    inFlight.current.add(entry.key);
    update(previous => previous.map(item => item.key === entry.key ? { ...item, phase: 'sending', error: undefined, uncertain: false } : item));
    try {
      const ack = await api.post<SendAcknowledgment>(`/chats/${entry.chatId}/messages`, entry.payload);
      if (!current(entry)) return;
      const result = ack.result || ack;
      if (result.message?.state === 'withdrawn' || entry.payload.reply_for_me && result.request?.status === 'cancelled') { update(previous => previous.filter(item => item.key !== entry.key)); return; }
      update(previous => previous.map(item => item.key === entry.key ? { ...item, phase: entry.payload.reply_for_me && result.request?.status === 'failed' ? 'failed' : 'sent', requestFailed: entry.payload.reply_for_me && result.request?.status === 'failed', acceptedAt: performance.now(), messageId: result.message?.id || item.messageId, requestId: result.request?.id || item.requestId } : item));
      refresh.current(entry.chatId);
    } catch (error) {
      if (!current(entry) || error instanceof DOMException && error.name === 'AbortError') return;
      if (error instanceof ApiError && error.status === 401) { clear(); return; }
      // A missing quote or attachment can reject one send without revoking the chat.
      // Recheck canonical authorization before clearing other submissions.
      if (error instanceof ApiError && [403, 404].includes(error.status)) refresh.current(entry.chatId);
      // A lost acknowledgment may follow a committed write. Retry freezes the key and payload.
      update(previous => previous.map(item => item.key === entry.key && !item.acceptedAt ? { ...item, phase: 'failed', uncertain: !(error instanceof ApiError) || error.status >= 500, error: error instanceof Error ? error.message : String(error) } : item));
    } finally { inFlight.current.delete(entry.key); }
  }
  const submit = (chatId: string, payload: Payload, message: Message) => {
    const entry: LocalSend = { key: payload.client_request_key, ownerId: api.ownerId, chatId, epoch: epochs.current.get(chatId) || 0, payload, message, phase: 'sending' };
    update(previous => [...previous, entry]); void transmit(entry);
  };
  const reconcile = useCallback((detail: ChatDetail, startedAt: number) => {
    if (owner.current !== api.ownerId) return;
    update(previous => previous.flatMap(original => {
      let entry = original;
      if (entry.ownerId !== api.ownerId || entry.chatId !== detail.chat.id) return [entry];
      const quote = entry.message.reply_to;
      if (quote && !detail.messages.some(item => item.id === quote.id && item.state === 'published')) entry = { ...entry, message: { ...entry.message, reply_to: { ...quote, body: '', unavailable: true } } };
      const message = detail.messages.find(item => item.client_request_key === entry.key || item.id === entry.messageId || !!entry.requestId && item.request_id === entry.requestId);
      if (message) return []; // Never retain a second copy after an authorized canonical observation.
      const request = detail.requests.find(item => entry.requestId ? item.id === entry.requestId : item.client_request_key === entry.key);
      if (request && entry.payload.reply_for_me) {
        if (request.status === 'completed' || request.status === 'cancelled') return [];
        return [{ ...entry, requestId: request.id, acceptedAt: entry.acceptedAt || performance.now(), phase: request.status === 'failed' ? 'failed' as const : 'sent' as const, requestFailed: request.status === 'failed', uncertain: false, error: request.error }];
      }
      // Only a read begun after persistence can revoke an accepted echo. Earlier reads
      // cannot erase an in-flight send or a later ACK; partial ACKs never add canonical content.
      if (entry.acceptedAt && entry.acceptedAt <= startedAt) return [];
      return [entry];
    }));
  }, [api, update]);
  const followRetry = (requestId: string, request: ReplyRequest) => { update(previous => previous.map(entry => entry.requestId === requestId ? { ...entry, requestId: request.id, requestFailed: false, phase: 'sent', acceptedAt: performance.now(), error: undefined } : entry)); };
  return { followRetry, items: items.filter(item => item.ownerId === api.ownerId), submit, retry: (entry: LocalSend) => { if (!entry.requestFailed) void transmit(entry); }, clear, reconcile, refresh };
}

export function LocalSendItem({ entry, actor, onRetry }: { entry: LocalSend; actor?: Actor; onRetry: () => void }) {
  const message = entry.message; const preparing = entry.payload.reply_for_me;
  const label = entry.phase === 'failed' ? t(entry.uncertain ? 'chat.sendUnconfirmed' : 'chat.sendFailed') : preparing ? t('chat.preparingReply') : t(entry.phase === 'sending' ? 'chat.sending' : 'chat.sent');
  return <article className={`message-row own local-send ${entry.phase}`} data-local-send={entry.key} tabIndex={0}><Avatar actor={actor} size={34} /><div className="message-content"><div className="message-meta"><span>{actor?.name}</span><time>{time(message.created_at)}</time>{preparing && <small>{t('chat.replyForMe')}</small>}</div><div className="message-bubble">{message.reply_to && <div className="source-quote"><strong>{message.reply_to.author_name}</strong><span>{message.reply_to.unavailable ? t('chat.quoteUnavailable') : message.reply_to.body}</span></div>}{preparing && <small className="local-input-label">{t('chat.localOriginal')}</small>}{message.body && <div className="message-text">{message.body}</div>}{!!message.attachments?.length && <MessageAttachments attachments={message.attachments} />}</div><div className="local-send-status" role="status" title={entry.error || (!preparing && entry.phase === 'sent' ? t('chat.persistedHint') : undefined)}><Icon name={entry.phase === 'failed' ? 'info' : entry.phase === 'sent' && !preparing ? 'check' : 'clock'} size={12} /><span>{label}</span>{entry.phase === 'failed' && !entry.requestFailed && <button type="button" className="text-button" aria-label={t('chat.retrySend')} onClick={onRetry}>{t('common.retry')}</button>}</div></div></article>;
}
