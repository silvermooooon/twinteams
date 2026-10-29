import { useState } from 'react';
import { t } from './i18n';
import { IconButton } from './ui';

function dismissedIds(key: string) {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(key) || '[]');
    return new Set<string>(Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []);
  } catch { return new Set<string>(); }
}

type Failure = { id: string; chat_id: string; error?: string; can_retry: boolean };

/** Dismissal only hides this notice in this browser session; the failure stays unchanged. */
export function FailureNotice<T extends Failure>({ ownerId, request, name, busy, onRetry, composer = false, label }: { ownerId: string; request: T; name?: string; busy: boolean; onRetry: (request: T) => void; composer?: boolean; label?: string }) {
  const storageKey = `twinteams.dismissedFailures:${JSON.stringify([ownerId, request.chat_id])}`;
  const failureId = request.id;
  const [dismissed, setDismissed] = useState(() => dismissedIds(storageKey).has(failureId));
  if (dismissed) return null;
  const dismiss = () => {
    setDismissed(true);
    try { const ids = dismissedIds(storageKey); ids.add(failureId); sessionStorage.setItem(storageKey, JSON.stringify([...ids])); } catch { /* Local dismissal still works when session storage is unavailable. */ }
  };
  return <span className={`failed-chip${composer ? ' composer-failure' : ''}`} data-failed-request={request.id} title={request.error}>{!composer && <span aria-hidden="true">!</span>}{label || <>{name} · {t('chat.failed')}</>}{request.can_retry && <IconButton icon="refresh" label={t('common.retry')} size={14} disabled={busy} onClick={() => onRetry(request)} />}<IconButton icon="close" label={t('common.dismiss')} size={14} onClick={dismiss} /></span>;
}
