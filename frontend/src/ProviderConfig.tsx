import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { getLocalProviderConfig, ProviderConfigError, verifyLocalProvider } from './api/provider-config';
import type { ProviderConfigErrorCode } from './api/provider-config';
import { t } from './i18n';
import { Icon } from './ui';
import './ProviderConfig.css';

const errorKeys = {
  unavailable: 'providerConfig.unavailable', unauthorized: 'providerConfig.unauthorized',
  'invalid-key': 'providerConfig.invalidKey', 'rate-limit': 'providerConfig.rateLimit',
  'verification-failed': 'providerConfig.failed', network: 'providerConfig.network',
  'provider-timeout': 'providerConfig.timeout', 'provider-connection-failed': 'providerConfig.connectionFailed',
  'provider-rejected': 'providerConfig.rejected', 'provider-invalid-response': 'providerConfig.invalidResponse',
  'model-unavailable': 'providerConfig.modelUnavailable', busy: 'providerConfig.busy',
  'storage-unavailable': 'providerConfig.storageUnavailable', 'save-failed': 'providerConfig.saveFailed',
  'invalid-origin': 'providerConfig.invalidOrigin',
} as const satisfies Record<ProviderConfigErrorCode, Parameters<typeof t>[0]>;

/** Keys stay in the password element until a user submits this exact billed test. */
export function ProviderConfigPage({ allowed, onBack }: { allowed: boolean; onBack: () => void }) {
  const keyField = useRef<HTMLInputElement>(null);
  const request = useRef<AbortController | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(false);
  const [ready, setReady] = useState(false);
  const [hasKey, setHasKey] = useState(false);
  const [saveForLater, setSaveForLater] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [existingSaved, setExistingSaved] = useState(false);
  const [storageAvailable, setStorageAvailable] = useState(false);
  const [storagePath, setStoragePath] = useState('');
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState<ProviderConfigErrorCode | null>(null);
  const setKeyField = useCallback((field: HTMLInputElement | null) => {
    // Ref detachment runs before the input is removed, even when the form was
    // rendered after the status request finished.
    if (keyField.current && keyField.current !== field) keyField.current.value = '';
    keyField.current = field;
  }, []);

  useEffect(() => {
    mounted.current = true;
    const field = keyField.current;
    const controller = new AbortController();
    const clearForNavigation = () => {
      if (keyField.current) keyField.current.value = '';
      setHasKey(false);
    };
    window.addEventListener('pagehide', clearForNavigation);
    setReady(false); setError(null); setVerified(false); setSaved(false); setExistingSaved(false); setStoragePath('');
    if (allowed) {
      getLocalProviderConfig(controller.signal).then(config => {
        if (controller.signal.aborted) return;
        setReady(config.available); setExistingSaved(config.saved); setStorageAvailable(config.storageAvailable); setStoragePath(config.storagePath);
        if (!config.available) setError('unavailable');
      }).catch(() => { if (!controller.signal.aborted) setError('unavailable'); });
    }
    return () => {
      mounted.current = false;
      window.removeEventListener('pagehide', clearForNavigation);
      controller.abort(); request.current?.abort();
      if (field) field.value = '';
      if (keyField.current) keyField.current.value = '';
    };
  }, [allowed]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const field = keyField.current;
    if (!allowed || !ready || inFlight.current || !field || !field.value.length) return;
    inFlight.current = true;
    setBusy(true); setError(null); setVerified(false);
    const controller = new AbortController(); request.current = controller;
    let apiKey = field.value;
    field.value = ''; setHasKey(false);
    try {
      const pending = verifyLocalProvider(apiKey, saveForLater && storageAvailable, controller.signal);
      apiKey = '';
      const result = await pending;
      if (mounted.current && !controller.signal.aborted) { setVerified(true); setSaved(result.saved); if (result.saved) setExistingSaved(true); setSaveForLater(false); if (result.saveFailed) setError('save-failed'); }
    } catch (failure) {
      if (mounted.current && !controller.signal.aborted) setError(failure instanceof ProviderConfigError ? failure.code : 'network');
    } finally {
      apiKey = '';
      if (field) field.value = '';
      inFlight.current = false;
      if (request.current === controller) request.current = null;
      if (mounted.current) { setBusy(false); setHasKey(false); }
    }
  }

  return <main className="provider-config-page">
    <header className="provider-config-header"><div><span className="provider-config-eyebrow">{t('providerConfig.localOnly')}</span><h1>{t('providerConfig.title')}</h1></div><button type="button" className="secondary-button" onClick={onBack}>{t('providerConfig.back')}</button></header>
    <div className="provider-config-content">
      {!allowed ? <div className="provider-config-notice" role="status"><Icon name="info" size={19} /><p>{t('providerConfig.unavailable')}</p></div> : !ready ? <div className="provider-config-notice" role="status">{error ? <><Icon name="info" size={19} /><p>{t(errorKeys[error])}</p></> : <><span className="spinner" /><p>{t('common.loading')}</p></>}</div> : <>
        <section className="provider-config-card" aria-labelledby="provider-config-test-heading">
          <div className="provider-config-card-title"><Icon name="settings" size={21} /><h2 id="provider-config-test-heading">{t('providerConfig.testTitle')}</h2><span>{t('providerConfig.disabled')}</span></div>
          <p>{t('providerConfig.intro')}</p>
          <dl className="provider-config-details"><div><dt>{t('providerConfig.provider')}</dt><dd>OpenAI</dd></div><div><dt>{t('providerConfig.model')}</dt><dd>gpt-6-luna</dd></div><div><dt>{t('providerConfig.destination')}</dt><dd>https://api.openai.com/v1/responses</dd></div></dl>
          <div className="provider-config-scope"><strong>{t('providerConfig.scopeTitle')}</strong><p>{t('providerConfig.scope')}</p><p>{t('providerConfig.billed')}</p></div>
          <form onSubmit={submit} autoComplete="off">
            <label className="provider-config-key-label" htmlFor="openai-api-key">{t('providerConfig.keyLabel')}</label>
            <input ref={setKeyField} id="openai-api-key" type="password" autoComplete="off" autoCorrect="off" autoCapitalize="off" spellCheck={false} required disabled={busy} aria-describedby="provider-key-privacy" onInput={event => setHasKey(event.currentTarget.value.length > 0)} />
            <p id="provider-key-privacy" className="provider-config-hint">{t('providerConfig.keyPrivacy')}</p>
            <label className="provider-config-save"><input type="checkbox" checked={saveForLater} disabled={busy || !storageAvailable} onChange={event => setSaveForLater(event.target.checked)} /><span>{t('providerConfig.saveLabel')}</span></label>
            <p className="provider-config-hint">{t('providerConfig.saveDetail')}</p>
            <p className="provider-config-storage-path">{storagePath || 'backend/data/provider-secrets/openai.env'}</p>
            <p className="provider-config-hint">{t('providerConfig.saveScope')}</p>
            {existingSaved && saveForLater && <p className="provider-config-hint">{t('providerConfig.replaceSaved')}</p>}
            {!storageAvailable && <p className="provider-config-hint">{t('providerConfig.storageUnavailable')}</p>}
            <button type="submit" className="primary-button provider-config-submit" disabled={!hasKey || busy}>{busy ? t('providerConfig.validating') : t(saveForLater ? 'providerConfig.validateAndSave' : 'providerConfig.validate')}</button>
          </form>
          {error && <div className="provider-config-result error" role="alert"><Icon name="info" size={18} /><p>{t(errorKeys[error])}</p></div>}
          {verified && <div className="provider-config-result success" role="status"><Icon name="check" size={18} /><div><strong>{t('providerConfig.verified')}</strong><p>{t(saved ? 'providerConfig.savedResult' : 'providerConfig.unsavedResult')}</p></div></div>}
          {existingSaved && !saved && <p className="provider-config-saved-status">{t('providerConfig.alreadySaved')}</p>}
        </section>
        <div className="provider-config-notice"><Icon name="info" size={19} /><p>{t('providerConfig.futureUse')}</p></div>
      </>}
    </div>
  </main>;
}
