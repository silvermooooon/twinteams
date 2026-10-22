import { useState } from 'react';
import type { ReplyRequest } from './api/client';
import { t } from './i18n';
import { IconButton } from './ui';

function dismissedIds(key: string) {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) || '[]');
    return new Set<string>(Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []);
  } catch { return new Set<string>(); }
}

/** Dismissal only hides this notice in this browser session; the request stays unchanged. */
export function FailureNotice({ ownerId, request, name, busy, onRetry, composer = false }: { ownerId: string; request: ReplyRequest; name?: string; busy: boolean; onRetry: (request: ReplyRequest) => void; composer?: boolean }) {
  const storageKey = `twinteams.dismissedFailures:${JSON.stringify([ownerId, request.chat_id])}`;
  const [dismissed, setDismissed] = useState(() => dismissedIds(storageKey).has(request.id));
  if (dismissed) return null;
  const dismiss = () => {
    setDismissed(true);
    try { const ids = dismissedIds(storageKey); ids.add(request.id); sessionStorage.setItem(storageKey, JSON.stringify([...ids])); } catch { /* Local dismissal still works when session storage is unavailable. */ }
  };
  return <span className={`failed-chip${composer ? ' composer-failure' : ''}`} data-failed-request={request.id} title={request.error}>{!composer && <span aria-hidden="true">!</span>}{name} · {t('chat.failed')}{request.can_retry && <button type="button" disabled={busy} onClick={() => onRetry(request)}>{t('common.retry')}</button>}<IconButton icon="close" label={t('common.dismiss')} size={14} onClick={dismiss} /></span>;
}
