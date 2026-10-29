import { useRef, useState } from 'react';
import { requestKey } from './api/client';
import type { ApiClient, DuetSendBudget } from './api/client';
import { Icon } from './ui';
import './TeamsOverlays.css';

// Reusing a consent key after a lost response cannot grant a second batch.
const consentKeys = new Map<string, string>();
function consentKey(scope: string) {
  const known = consentKeys.get(scope);
  if (known) return known;
  const storageKey = `duet.peerMessageConsent:${scope}`;
  let key: string | null = null;
  try { key = sessionStorage.getItem(storageKey); } catch { /* Keep a tab-local key. */ }
  key ||= requestKey();
  consentKeys.set(scope, key);
  try { sessionStorage.setItem(storageKey, key); } catch { /* The in-memory key still protects retry. */ }
  return key;
}

export function DuetMessageBudget({ api, chatId, budgets, onChange }: { api: ApiClient; chatId: string; budgets: DuetSendBudget[]; onChange: () => Promise<unknown> }) {
  const [pending, setPending] = useState<Record<string, 'sending' | 'authorized'>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const inFlight = useRef(new Set<string>());
  if (!budgets.length) return null;
  async function continueSending(budget: DuetSendBudget, scope: string) {
    if (inFlight.current.has(scope) || pending[scope] === 'authorized' || !budget.can_continue || budget.pending_count < 1) return;
    inFlight.current.add(scope);
    setPending(previous => ({ ...previous, [scope]: 'sending' }));
    setErrors(previous => ({ ...previous, [scope]: '' }));
    try {
      await api.continueDuetMessages(chatId, budget.causal_chain_id, consentKey(scope), budget.allowance);
      setPending(previous => ({ ...previous, [scope]: 'authorized' }));
    } catch (error) {
      setPending(previous => { const next = { ...previous }; delete next[scope]; return next; });
      setErrors(previous => ({ ...previous, [scope]: error instanceof Error ? error.message : String(error) }));
    } finally {
      inFlight.current.delete(scope);
      try { await onChange(); } catch { /* Canonical state is refreshed on reconnect. */ }
    }
  }
  return <div className="duet-send-budgets" role="region" aria-label="Duet message allowance" aria-live="polite">{budgets.map(budget => {
    const scope = JSON.stringify([api.ownerId, chatId, budget.causal_chain_id, budget.allowance]);
    const state = pending[scope];
    return <section className="duet-send-budget" key={budget.causal_chain_id} aria-busy={state === 'sending'}>
      <Icon name="info" size={17} /><div className="duet-send-budget-copy">
        <div className="duet-send-budget-heading"><strong>{budget.pending_count ? 'Messages waiting to send' : 'Messages between Duets'}</strong></div>
        <p>{budget.spent} of {budget.allowance} messages sent between Duets. {budget.remaining} remaining.</p>
        {budget.pending_count > 0 && <><p className="duet-send-budget-hint">{budget.pending_count} prepared {budget.pending_count === 1 ? 'message has' : 'messages have'} not been sent. An owner can allow the next {budget.continuation_allowance} messages between Duets.</p>{budget.can_continue && <div className="duet-send-continue-actions"><button type="button" className="secondary-button" disabled={!!state || inFlight.current.has(scope)} onClick={() => { void continueSending(budget, scope); }}>{state === 'sending' ? 'Authorizing…' : state === 'authorized' ? 'Sending authorized' : `Continue · ${budget.continuation_allowance} more messages`}</button><small>This allows messages between Duets in this same exchange, including prepared messages waiting to send.</small></div>}</>}
        {errors[scope] && <p className="duet-send-continue-error" role="alert">{errors[scope]} <span>Retry checks this same authorization.</span></p>}
      </div>
    </section>;
  })}</div>;
}
