/** This isolated client never places credentials in shared app state or API errors. */
export type LocalProviderConfig = { available: boolean; saved: boolean; storageAvailable: boolean; storagePath: string };
export type ProviderVerification = { verified: true; saved: boolean; saveFailed: boolean };
export type ProviderConfigErrorCode = 'unavailable' | 'unauthorized' | 'invalid-key' | 'rate-limit' | 'verification-failed' | 'network' | 'provider-timeout' | 'provider-connection-failed' | 'provider-rejected' | 'provider-invalid-response' | 'model-unavailable' | 'busy' | 'storage-unavailable' | 'save-failed' | 'invalid-origin';

export class ProviderConfigError extends Error {
  constructor(readonly code: ProviderConfigErrorCode) { super(code); }
}

function responseError(status: number): ProviderConfigError {
  return new ProviderConfigError(status === 401 ? 'unauthorized' : status === 403 || status === 404 ? 'unavailable' : status === 429 ? 'rate-limit' : 'verification-failed');
}

function safeError(code: unknown, status: number): ProviderConfigError {
  const codes: Record<string, ProviderConfigErrorCode> = {
    invalid_key: 'invalid-key', provider_authentication_failed: 'invalid-key',
    model_unavailable: 'model-unavailable', provider_limit: 'rate-limit',
    validation_rate_limited: 'rate-limit', validation_in_progress: 'busy',
    storage_unavailable: 'storage-unavailable', authentication_required: 'unauthorized',
    invalid_origin: 'invalid-origin', proxy_not_allowed: 'invalid-origin',
    local_only: 'unavailable', admin_required: 'unavailable',
    provider_timeout: 'provider-timeout', provider_connection_failed: 'provider-connection-failed',
    provider_rejected: 'provider-rejected', provider_invalid_response: 'provider-invalid-response',
  };
  return typeof code === 'string' && Object.hasOwn(codes, code) ? new ProviderConfigError(codes[code]) : responseError(status);
}

export async function getLocalProviderConfig(signal?: AbortSignal): Promise<LocalProviderConfig> {
  const response = await fetch('/api/local-provider-config', { credentials: 'include', cache: 'no-store', signal });
  if (!response.ok) throw responseError(response.status);
  const data: unknown = await response.json();
  if (!data || typeof data !== 'object') throw new ProviderConfigError('unavailable');
  const config = data as Record<string, unknown>;
  const storagePath = typeof config.storage_path === 'string' && config.storage_path.length < 1024 && /^\/[^\x00-\x1f\x7f]*\/backend\/data\/provider-secrets\/openai\.env$/.test(config.storage_path) ? config.storage_path : '';
  // Do not display arbitrary provider/server fields. The test destination is fixed.
  return { available: config.available === true && config.enabled === false && config.provider === 'openai' && config.model === 'gpt-6-luna' && config.endpoint_host === 'api.openai.com' && config.admin_owner_id === 'owner-alex', saved: config.saved === true, storageAvailable: config.storage_available === true && !!storagePath, storagePath };
}

export async function verifyLocalProvider(apiKey: string, saveForLater: boolean, signal: AbortSignal): Promise<ProviderVerification> {
  let body = JSON.stringify({ api_key: apiKey, confirm_billed_call: true, save_for_later: saveForLater });
  apiKey = '';
  let pending: Promise<Response>;
  try {
    pending = fetch('/api/local-provider-config/verify', { method: 'POST', credentials: 'include', cache: 'no-store', headers: { 'Content-Type': 'application/json', 'X-Twinteams-Config': '1' }, body, signal });
  } finally { body = ''; }
  let response: Response;
  try { response = await pending; } catch { throw new ProviderConfigError('network'); }
  let data: unknown;
  try { data = await response.json(); } catch (error) {
    // A truncated response is a connection loss; malformed JSON is an invalid
    // local-service response. Never expose the exception or returned body.
    throw new ProviderConfigError(error instanceof SyntaxError ? 'verification-failed' : 'network');
  }
  if (!data || typeof data !== 'object') throw new ProviderConfigError('verification-failed');
  const result = data as Record<string, unknown>;
  // Only fixed metadata and allowlisted error identifiers leave this client.
  // A successful test followed by a failed save must never trigger another call.
  if (result.validated === true && result.enabled === false && result.model === 'gpt-6-luna' && result.error_code === 'storage_unavailable') return { verified: true, saved: false, saveFailed: true };
  if (!response.ok) throw safeError(result.error_code, response.status);
  if (result.validated !== true || result.enabled !== false || result.model !== 'gpt-6-luna' || result.error_code !== null) throw new ProviderConfigError('verification-failed');
  return { verified: true, saved: saveForLater && result.saved === true, saveFailed: false };
}
