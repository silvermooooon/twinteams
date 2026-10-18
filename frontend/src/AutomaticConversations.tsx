import { useRef, useState } from 'react';
import { requestKey } from './api/client';
import type { Actor, ApiClient, AutomaticConversation } from './api/client';
import { t } from './i18n';
import { Icon } from './ui';

// A lost response must not turn a retry, navigation, or reload into a second grant.
// These tab-local keys contain only opaque identifiers, never chat content.
const consentKeys = new Map<string, string>();
function consentKey(scope: string) {
  const memoryKey = consentKeys.get(scope);
  if (memoryKey) return memoryKey;
  const storageKey = `twinteams.automaticConsent:${scope}`;
  let key: string | null = null;
  try { key = sessionStorage.getItem(storageKey); } catch { /* Storage may be disabled. */ }
  key ||= requestKey();
  consentKeys.set(scope, key);
  try { sessionStorage.setItem(storageKey, key); } catch { /* Keep the in-memory retry key. */ }
  return key;
}

export function AutomaticConversations({ api, chatId, conversations = [], actors, onChange }: { api: ApiClient; chatId: string; conversations?: AutomaticConversation[]; actors: Actor[]; onChange: () => Promise<unknown> }) {
  const [pending, setPending] = useState<Record<string, 'sending' | 'authorized'>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const inFlight = useRef(new Set<string>());
  const paused = conversations.filter(chain => chain.status === 'paused' && chain.paused_pending_count > 0);
  if (!paused.length) return null;

  async function continueChain(chain: AutomaticConversation, scope: string) {
    if (inFlight.current.has(scope) || pending[scope] === 'authorized') return;
    if (!chain.can_continue || chain.reason !== 'budget_exhausted' || !Number.isSafeInteger(chain.continuation_allowance) || chain.continuation_allowance <= 0) return;
    inFlight.current.add(scope);
    setPending(previous => ({ ...previous, [scope]: 'sending' }));
    setErrors(previous => ({ ...previous, [scope]: '' }));
    try {
      await api.continueAutomaticConversation(chatId, chain.id, consentKey(scope), chain.continuation_allowance, chain.allowance);
      setPending(previous => ({ ...previous, [scope]: 'authorized' }));
    } catch (error) {
      setPending(previous => { const next = { ...previous }; delete next[scope]; return next; });
      setErrors(previous => ({ ...previous, [scope]: error instanceof Error ? error.message : String(error) }));
    } finally {
      inFlight.current.delete(scope);
      setPending(previous => ({ ...previous }));
      // REST, including the normal poll, decides when the paused banner changes.
      try { await onChange(); } catch { /* The canonical poll can recover a failed refresh. */ }
    }
  }

  return <div className="automatic-conversations" role="region" aria-label={t('automatic.region')} aria-live="polite">
    {paused.map(chain => {
      // A new canonical allowance is a new, explicitly authorized batch.
      const scope = JSON.stringify([api.ownerId, chatId, chain.id, chain.allowance, chain.continuation_allowance]);
      const state = pending[scope];
      const canContinue = chain.can_continue && chain.reason === 'budget_exhausted' && Number.isSafeInteger(chain.continuation_allowance) && chain.continuation_allowance > 0;
      const sourceOwner = actors.find(actor => actor.id === chain.source_owner_id && actor.kind === 'human');
      return <section className="automatic-paused" key={chain.id} aria-busy={state === 'sending'}>
        <Icon name="info" size={17} />
        <div className="automatic-paused-copy">
          <div className="automatic-paused-heading"><strong>{t('automatic.paused')}</strong>{sourceOwner && <span>{t('automatic.startedBy', { name: sourceOwner.name })}</span>}</div>
          <p>{chain.reason === 'budget_exhausted' ? t('automatic.budgetReason', { spent: chain.spent, allowance: chain.allowance }) : t('automatic.repeatedReason')}</p>
          <p className="automatic-paused-hint">{t('automatic.manualAvailable')}</p>
          {canContinue && <div className="automatic-continue-actions"><button type="button" className="secondary-button" disabled={!!state || inFlight.current.has(scope)} onClick={() => { void continueChain(chain, scope); }}>{state === 'sending' ? t('automatic.continuing') : state === 'authorized' ? t('automatic.authorized') : t('automatic.continue', { count: chain.continuation_allowance })}</button><small>{t('automatic.consent', { count: chain.continuation_allowance })}</small></div>}
          {errors[scope] && <p className="automatic-continue-error" role="alert">{errors[scope]} <span>{t('automatic.retryHint')}</span></p>}
        </div>
      </section>;
    })}
  </div>;
}
