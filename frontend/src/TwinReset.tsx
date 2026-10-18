import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, requestKey } from './api/client';
import type { ApiClient } from './api/client';
import { twinApi } from './api/twin';
import type { TwinResetStatus } from './api/twin';
import { t } from './i18n';
import { Modal } from './ui';

export function useTwinReset(api: ApiClient, onCompleted: (status: TwinResetStatus) => void) {
  const [info, setInfo] = useState<TwinResetStatus | null>(null); const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [conflict, setConflict] = useState(false);
  const current = useRef(info); const owner = useRef(api.ownerId); const callback = useRef(onCompleted); callback.current = onCompleted;
  const operation = useRef<{ key: string; generation: number } | null>(null); const observed = useRef(0); const sequence = useRef(0); const submitting = useRef(false);
  const refresh = useCallback(async () => {
    if (!api.ownerId) return null;
    const serial = ++sequence.current;
    try {
      const value = await twinApi(api).resetStatus();
      if (owner.current !== api.ownerId || serial !== sequence.current) return null;
      if (current.current && value.generation < current.current.generation) return current.current;
      current.current = value; setInfo(value); setLoading(false);
      if (value.status === 'completed' && value.generation > observed.current) { observed.current = value.generation; callback.current(value); }
      return value;
    } catch (failure) { if (owner.current === api.ownerId && serial === sequence.current) { setLoading(false); setError(failure instanceof Error ? failure.message : t('twin.loadFailed')); } return null; }
  }, [api]);
  useEffect(() => { owner.current = api.ownerId; sequence.current++; operation.current = null; current.current = null; observed.current = 0; setInfo(null); setLoading(!!api.ownerId); setError(''); setConflict(false); if (api.ownerId) void refresh(); }, [api, refresh]);
  const start = () => { setError(''); setConflict(false); void refresh(); };
  async function submit() {
    if (!current.current || submitting.current || conflict) return false;
    if (!operation.current) {
      const value = current.current;
      if (value.status === 'pending' && (!value.idempotency_key || value.expected_generation == null)) { setError(t('twin.resetPending')); return false; }
      operation.current = value.status === 'pending' ? { key: value.idempotency_key!, generation: value.expected_generation! } : { key: requestKey(), generation: value.generation };
    }
    const intent = operation.current; const originalOwner = api.ownerId;
    submitting.current = true; setBusy(true); setError('');
    try {
      const result = await twinApi(api).reset(intent.key, intent.generation);
      if (owner.current !== originalOwner) return false;
      const canonical = await refresh();
      if (result.status === 'completed') { if (canonical) operation.current = null; return !!canonical; }
      setError(t('twin.resetPending')); return false;
    } catch (failure) {
      if (owner.current !== originalOwner) return false;
      if (failure instanceof ApiError && failure.status === 409) { operation.current = null; setConflict(true); await refresh(); setError(t('twin.resetConflict')); }
      else setError(failure instanceof Error ? failure.message : t('twin.loadFailed'));
      return false;
    } finally { submitting.current = false; if (owner.current === originalOwner) setBusy(false); }
  }
  return { info, loading, busy, error, conflict, start, submit, refresh };
}

export type TwinResetControl = ReturnType<typeof useTwinReset>;
export function TwinResetSection({ reset }: { reset: TwinResetControl }) {
  const [open, setOpen] = useState(false); const [completed, setCompleted] = useState(false);
  return <section className="twin-danger-zone"><div><h2>{t('twin.resetTitle')}</h2><p>{t('twin.resetSummary')}</p></div><button type="button" className="danger-button" disabled={reset.loading || reset.busy || !reset.info} onClick={() => { setCompleted(false); reset.start(); setOpen(true); }}>{t('twin.reset')}</button>
    {completed && <p className="twin-save-status" role="status">{t('twin.resetCompleted')}</p>}
    {!open && reset.error && !reset.info && <div className="twin-inline-error" role="alert">{reset.error}<button type="button" className="text-button" onClick={() => { void reset.refresh(); }}>{t('twin.retry')}</button></div>}
    {open && <Modal title={t('twin.resetConfirmTitle')} closeDisabled={reset.busy} onClose={() => setOpen(false)}><div className="twin-reset-explanation"><p>{t('twin.resetDeletes')}</p><p>{t('twin.resetKeeps')}</p><p>{t('twin.resetIrreversible')}</p></div>{reset.error && <p className="twin-inline-error" role="alert">{reset.error}</p>}<div className="modal-actions"><button type="button" className="secondary-button" disabled={reset.busy} onClick={() => setOpen(false)}>{t('common.cancel')}</button><button type="button" className="danger-button" disabled={reset.busy || reset.conflict || !reset.info} onClick={async () => { if (await reset.submit()) { setOpen(false); setCompleted(true); } }}>{t(reset.busy ? 'twin.resetting' : 'twin.resetConfirm')}</button></div></Modal>}
  </section>;
}
