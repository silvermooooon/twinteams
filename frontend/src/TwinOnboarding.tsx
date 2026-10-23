import { useEffect, useRef, useState } from 'react';
import type { ApiClient, Chat } from './api/client';
import { IconButton } from './ui';
import './TwinOnboarding.css';

type OnboardingState = {
  enabled: boolean;
  status: 'new' | 'active' | 'completed' | 'skipped' | 'unavailable' | 'disabled';
  chat_id: string | null;
  welcome_message_id: string | null;
};
const terminal = (state: OnboardingState | null) => state && ['completed', 'skipped', 'unavailable', 'disabled'].includes(state.status);

/** The welcome is a canonical server Message. This component only starts or skips
 * the optional introduction; it never supplies chat content or holds up a send. */
export function TwinOnboarding({ api, chat, revision, onChanged }: { api: ApiClient; chat: Chat; revision: number; onChanged: () => void }) {
  const ownPrivate = !!api.ownerId && chat.kind === 'private' && chat.member_owner_ids.length === 1 && chat.member_owner_ids[0] === api.ownerId;
  const [state, setState] = useState<OnboardingState | null>(null);
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false); const [retry, setRetry] = useState(0);
  const current = useRef(state); current.current = state;
  const changed = useRef(onChanged); changed.current = onChanged;
  const skipping = useRef(false); const mutation = useRef(0); const command = useRef<AbortController | null>(null);
  useEffect(() => () => command.current?.abort(), []);
  useEffect(() => {
    if (!ownPrivate || terminal(current.current) || skipping.current) return;
    const controller = new AbortController(); const epoch = mutation.current;
    const valid = () => !controller.signal.aborted && epoch === mutation.current && !skipping.current;
    setError('');
    void (async () => {
      try {
        let result = await api.get<OnboardingState>('/me/twin/onboarding', controller.signal);
        if (!valid() || result.chat_id && result.chat_id !== chat.id) return;
        if (result.enabled && result.status === 'new' && result.chat_id === chat.id) {
          result = await api.request<OnboardingState>('/me/twin/onboarding/start', { method: 'POST', signal: controller.signal });
          if (!valid() || result.chat_id && result.chat_id !== chat.id) return;
          setState(result);
          changed.current(); // Reload canonical messages; do not append a local bubble.
        } else setState(result);
      } catch (failure) {
        if (valid()) setError(failure instanceof Error ? failure.message : 'Could not load the introduction.');
      }
    })();
    return () => controller.abort();
  }, [api, chat.id, ownPrivate, revision, retry]);
  async function skip() {
    if (skipping.current) return;
    const controller = new AbortController(); command.current = controller;
    skipping.current = true; mutation.current++; setBusy(true); setError('');
    try {
      const result = await api.request<OnboardingState>('/me/twin/onboarding/skip', { method: 'POST', signal: controller.signal });
      if (!controller.signal.aborted && (!result.chat_id || result.chat_id === chat.id)) { setState(result); changed.current(); }
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Could not skip the introduction.');
    } finally {
      skipping.current = false;
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  if (!ownPrivate || !error && (!state?.enabled || state.status !== 'active')) return null;
  return <div className="twin-onboarding" role="group" aria-label="Optional introduction">
    {error ? <><span role="alert">{error}</span><IconButton icon="refresh" label="Retry introduction" disabled={busy} onClick={() => setRetry(value => value + 1)} /></> : <span>You can jump straight into work.</span>}
    {state?.enabled && state.status === 'active' && <IconButton icon="close" label="Skip introduction" disabled={busy} onClick={() => { void skip(); }} />}
  </div>;
}
