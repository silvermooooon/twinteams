import type { Actor, Message, ReplyRequest } from './api/client';
import { t } from './i18n';
import { Avatar, Icon } from './ui';
import './PrivateTaskStatuses.css';

export function isBackgroundPrivateTask(request: ReplyRequest) {
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

export function PrivateTaskStatuses({ requests, messages, actor, busy, onCancel }: { requests: ReplyRequest[]; messages: Message[]; actor?: Actor; busy: Set<string>; onCancel: (request: ReplyRequest) => void }) {
  const active = requests.filter(request => !request.quiet_background && (request.status === 'queued' || request.status === 'running')).slice().reverse().sort((a, b) => a.accepted_at.localeCompare(b.accepted_at));
  if (!active.length) return null;
  return <div className="private-task-statuses" role="region" aria-label={t('chat.activeTasks')}>
    {active.map(request => {
      const queued = request.status === 'queued'; const stopping = busy.has(request.id) || request.cancel_requested === true;
      const background = isBackgroundPrivateTask(request); const label = privateTaskLabel(request, messages);
      return <div key={request.id} className={`private-task-status ${background ? 'background-task' : 'typing-row'}`} role="status" data-reply-phase={stopping ? 'stopping' : queued ? 'preparing' : 'running'} data-request-id={request.id}>
        {background ? <Icon name="clock" size={20} /> : <span className="typing-avatars"><Avatar actor={actor} size={25} /></span>}
        <span className="private-task-copy"><span className="private-task-label" title={label}>{label}</span><span className="private-task-phase">{t(stopping ? 'chat.stopping' : queued ? 'chat.acceptedWaiting' : 'chat.taskRunning')}</span></span>
        {!queued && !stopping ? <span className="typing-dots" aria-hidden="true"><i /><i /><i /></span> : <Icon name="clock" size={14} />}
        {request.can_cancel && <button type="button" className="stop-button" disabled={stopping} aria-label={t(queued ? 'chat.cancelRequest' : 'chat.stop')} title={label} onClick={() => onCancel(request)}>{t(queued ? 'common.cancel' : 'chat.stop')}</button>}
      </div>;
    })}
  </div>;
}
