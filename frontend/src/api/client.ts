export type Actor = { id: string; kind: 'human' | 'twin'; owner_id?: string; name: string; initials: string; color: string };
export type Chat = { id: string; kind: 'private' | 'group'; title: string; member_owner_ids: string[]; unread_count: number; last_message?: string };
export type Message = { id: string; chat_id: string; seq: number; author_id: string; kind: 'human' | 'twin' | 'reply_for_me' | 'system'; body: string; raw_owner_input?: string; created_at: string; reply_to?: { id: string; author_name: string; body: string; unavailable?: boolean }; read_by: string[]; request_id?: string; state: 'published' | 'withdrawn' };
export type ReplyRequest = { id: string; chat_id: string; source_message_id: string | null; target_twin_id: string; requester_id: string; source: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; accepted_at: string; error?: string; can_cancel: boolean; can_retry: boolean; session_id?: string; run_id?: string };
export type ChatSettings = { reply_for_me_enabled: boolean; prompt_override: string | null; sidebar_open: boolean; sidebar_tab: 'activity' | 'workspace' | 'tracking' };
export type Approval = { id: string; chat_id: string; approver_id: string; title: string; description: string; target: string; impact: string; status: 'pending' | 'approved' | 'rejected'; created_at: string };
export type Tracking = { id: string; chat_id: string; title: string; description: string; status: string; created_at: string; updated_at: string; updates?: { id: string; body: string; created_at: string }[] };
export type Reminder = { id: string; title: string; detail: string; due_at: string | null; done: boolean; chat_id?: string; kind?: string };
export type ActivityRun = { session_id: string; run_id: string; parent_run_id?: string; source: string; status: string; started_at: string; finished_at?: string; title: string; summary?: string };
export type ActivityEvent = { id: string; seq: number; type: string; time: string; label: string; input?: string; output?: string; status?: string };
export type WorkspaceEntry = { id: string; name: string; kind: 'file' | 'folder'; path: string; size?: number; children?: WorkspaceEntry[] };
/** Only chat-visible paused candidates are returned. Counts are canonical server totals. */
export type AutomaticConversation = { id: string; causal_chain_id: string; status: 'paused'; reason: 'budget_exhausted' | 'repeated_content'; spent: number; allowance: number; remaining: number; paused_pending_count: number; continuation_allowance: number; can_continue: boolean; source_owner_id: string };
export type AutomaticContinuation = { id: string; causal_chain_id: string; spent: number; allowance: number; remaining: number; continuation_allowance: number; grant_id: string; already_applied: boolean };
export type ChatDetail = { chat: Chat; messages: Message[]; requests: ReplyRequest[]; approvals: Approval[]; tracking: Tracking[]; settings: ChatSettings; members: Actor[]; automatic_conversations?: AutomaticConversation[] };
export type Bootstrap = { current_owner_id: string; actors: Actor[]; chats: Chat[]; reminders: Reminder[]; model_configured: boolean; development_mode: boolean; acceptance_fixture?: boolean };
export type LiveEvent = { type: 'changed' | 'token' | 'status' | 'operation_started' | 'output_committed'; chat_id?: string; request_id?: string; text?: string; event_id?: string; operation_id?: string };
export type HistoryPolicy = 'all' | 'fixed_7d' | 'no_prior';

export function requestKey() { return crypto.randomUUID(); }
export class ApiClient {
  constructor(readonly ownerId: string = '') {}
  headers(json = true) { return { ...(json ? { 'Content-Type': 'application/json' } : {}),  }; }
  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(`/api${path}`, { ...options, credentials: 'same-origin', headers: { ...this.headers(), ...options.headers } });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try { const body = await response.json(); detail = typeof body.detail === 'string' ? body.detail : detail; } catch { /* Preserve HTTP error. */ }
      throw new Error(detail);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }
  get<T>(path: string, signal?: AbortSignal) { return this.request<T>(path, { signal }); }
  post<T>(path: string, data: unknown = {}) { return this.request<T>(path, { method: 'POST', body: JSON.stringify(data) }); }
  patch<T>(path: string, data: unknown) { return this.request<T>(path, { method: 'PATCH', body: JSON.stringify(data) }); }
  delete<T>(path: string) { return this.request<T>(path, { method: 'DELETE' }); }
  continueAutomaticConversation(chatId: string, chainId: string, clientRequestKey: string, additionalReplies: number, expectedAllowance: number) {
    return this.post<AutomaticContinuation>(`/chats/${encodeURIComponent(chatId)}/automatic-conversations/${encodeURIComponent(chainId)}/continue`, { client_request_key: clientRequestKey, additional_replies: additionalReplies, expected_allowance: expectedAllowance });
  }
  /** Live deltas are intentionally disposable. No Last-Event-ID or replay cursor is kept. */
  async events(chatId: string, signal: AbortSignal, onEvent: (event: LiveEvent) => void, onConnection: (connected: boolean) => void) {
    const response = await fetch(`/api/events?chat_id=${encodeURIComponent(chatId)}`, { signal, credentials: 'same-origin', headers: { ...this.headers(false), Accept: 'text/event-stream' } });
    if (!response.ok || !response.body) throw new Error(`Live connection: ${response.status}`);
    onConnection(true);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      while (!signal.aborted) {
        const result = await reader.read();
        if (result.done) break;
        buffer += decoder.decode(result.value, { stream: true }).replace(/\r\n/g, '\n');
        let boundary: number;
        while ((boundary = buffer.indexOf('\n\n')) >= 0) {
          const block = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
          const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
          if (data) { try { onEvent(JSON.parse(data) as LiveEvent); } catch { /* Heartbeat or unknown event. */ } }
        }
      }
    } finally { reader.releaseLock(); onConnection(false); }
  }
}
