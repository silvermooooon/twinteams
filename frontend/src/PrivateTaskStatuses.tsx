import type { Actor, Message, ReplyRequest } from './api/client';
import { t } from './i18n';
import { Avatar, IconButton } from './ui';
import './PrivateTaskStatuses.css';

export function isBackgroundPrivateTask(request: Pick<ReplyRequest, 'background' | 'source'>) {
  return request.background ?? ['system_review', 'scheduled_task', 'memory_dream'].includes(request.source);
}

export function privateTaskSourceLabel(source: string) {
  if (source === 'system_review') return t('chat.reviewTask');
  if (source === 'scheduled_task') return t('chat.scheduledTask');
  if (source === 'memory_dream') return t('chat.memoryTask');
  return undefined;
}

/** Describe the request's own source. A background run never borrows the latest input. */
export function privateTaskLabel(request: ReplyRequest, messages: Message[]) {
  const sourceLabel = privateTaskSourceLabel(request.source);
  if (sourceLabel) return sourceLabel;
  if (isBackgroundPrivateTask(request)) return t('chat.backgroundTask');
  const message = messages.find(item => item.id === request.source_message_id && item.kind === 'human');
  const body = message?.body.replace(/\s+/g, ' ').trim();
  return body ? t('chat.replyTask', { message: body.length > 100 ? body.slice(0, 100) + '…' : body }) : t('chat.privateTask');
}

type ResponseStatus = Pick<ReplyRequest, 'id' | 'source' | 'status' | 'can_cancel' | 'quiet_background' | 'background' | 'cancel_requested'>;

export function PrivateTaskStatuses({ requests, actor, busy, onCancel }: { requests: ResponseStatus[]; actor?: Actor; busy: Set<string>; onCancel: (ids: string[]) => void }) {
  const active = requests.filter(request => !request.quiet_background && !isBackgroundPrivateTask(request) && (request.status === 'queued' || request.status === 'running'));
  if (!active.length) return null;
  const cancellable = active.filter(request => request.can_cancel && !request.cancel_requested);
  const stopping = active.every(request => busy.has(request.id) || request.cancel_requested);
  const label = t('chat.typing', { name: actor?.name || t('chat.myTwin') });
  return <div className="typing-row private-task-status" role="status" aria-label={`${label}…`} data-reply-phase={stopping ? 'stopping' : active.some(request => request.status === 'running') ? 'running' : 'preparing'}>
    <span className="typing-avatars"><Avatar actor={actor} size={25} /></span>
    <span>{label}</span><span className="typing-dots" aria-hidden="true"><i /><i /><i /></span>
    {!!cancellable.length && <IconButton icon="stop" size={14} className="stop-responses" label={t('chat.stopResponses')} disabled={cancellable.some(request => busy.has(request.id))} onClick={() => onCancel(cancellable.map(request => request.id))} />}
  </div>;
}
