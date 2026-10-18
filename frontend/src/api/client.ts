export type Actor = { id: string; kind: 'human' | 'twin'; owner_id?: string; name: string; initials: string; color: string; avatar_url?: string | null; avatar_preset?: string | null; profile_revision?: number };
export type Chat = { id: string; kind: 'private' | 'group'; title: string; member_owner_ids: string[]; unread_count: number; last_message?: string; can_rename?: boolean };
export type ChatAttachment = { id: string; name: string; mime_type: string; size: number; kind: 'image' | 'unsupported'; url: string; width?: number | null; height?: number | null };
export type MessageReaction = { emoji: string; count: number; actors: Pick<Actor, 'id' | 'owner_id' | 'name' | 'kind'>[]; mine: boolean };
export type Message = { id: string; chat_id: string; seq: number; author_id: string; kind: 'human' | 'twin' | 'reply_for_me' | 'system'; body: string; reactions?: MessageReaction[]; reactions_revision?: number; attachments?: ChatAttachment[]; raw_owner_input?: string; created_at: string; reply_to?: { id: string; author_name: string; body: string; unavailable?: boolean }; read_by: string[]; request_id?: string; state: 'published' | 'withdrawn' };
export type ReplyRequest = { id: string; chat_id: string; source_message_id: string | null; target_twin_id: string; requester_id: string; source: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; accepted_at: string; error?: string; can_cancel: boolean; can_retry: boolean; session_id?: string; run_id?: string };
export type ChatSettings = { reply_for_me_enabled: boolean; prompt_override: string | null; sidebar_open: boolean; sidebar_tab: 'activity' | 'workspace' | 'tracking' };
export type Approval = { id: string; chat_id: string; approver_id: string; title: string; description: string; target: string; impact: string; action?: { tool: string; arguments: Record<string, unknown> } | null; status: 'pending' | 'approved' | 'rejected'; created_at: string };
export type Tracking = { id: string; chat_id: string; title: string; description: string; status: string; created_at: string; updated_at: string; updates?: { id: string; body: string; created_at: string }[] };
export type Reminder = { id: string; title: string; detail: string; due_at: string | null; done: boolean; chat_id?: string; kind?: string };
export type ActivityRun = { session_id: string; run_id: string; parent_run_id?: string; source: string; status: string; started_at: string; finished_at?: string; title: string; summary?: string };
export type ActivityEvent = { id: string; seq: number; type: string; time: string; label: string; input?: string; output?: string; status?: string };
export type WorkspaceEntry = { id: string; name: string; kind: 'file' | 'folder'; path: string; size?: number; children?: WorkspaceEntry[] };
/** Only chat-visible paused candidates are returned. Counts are canonical server totals. */
export type AutomaticConversation = { id: string; causal_chain_id: string; status: 'paused'; reason: 'budget_exhausted' | 'repeated_content'; spent: number; allowance: number; remaining: number; paused_pending_count: number; continuation_allowance: number; can_continue: boolean; source_owner_id: string };
export type AutomaticContinuation = { id: string; causal_chain_id: string; spent: number; allowance: number; remaining: number; continuation_allowance: number; grant_id: string; already_applied: boolean };
export type ChatDetail = { revision: number; has_more?: boolean; before_seq?: number | null; chat: Chat; messages: Message[]; requests: ReplyRequest[]; approvals: Approval[]; tracking: Tracking[]; settings: ChatSettings; members: Actor[]; automatic_conversations?: AutomaticConversation[] };
export type Bootstrap = { current_owner_id: string; actors: Actor[]; chats: Chat[]; reminders: Reminder[]; model_configured: boolean; development_mode: boolean; acceptance_fixture?: boolean };
export type LiveEvent = { revision?: number; type: 'changed' | 'actor_changed' | 'personal_changed' | 'twin_changed' | 'credential_requests_changed' | 'worker_status' | 'token' | 'status' | 'operation_started' | 'output_committed'; chat_id?: string; actor_id?: string; profile_revision?: number; section?: string; area?: string; request_id?: string; text?: string; event_id?: string; operation_id?: string };
export type HistoryPolicy = 'all' | 'fixed_7d' | 'no_prior';

export function requestKey() { return crypto.randomUUID(); }
export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly payload: unknown) { super(message); this.name = 'ApiError'; }
}
export class ApiClient {
  constructor(readonly ownerId: string = '') {}
  private socket: WebSocket | null = null;
  private pending = new Map<string, { frame: unknown; resolve: (value: unknown) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();
  command(chatId: string | null, command: string, payload: Record<string, unknown>, commandId: string = requestKey()) {
    return new Promise<unknown>((resolve, reject) => {
      const frame = { v: 1, type: 'command', command_id: commandId, chat_id: chatId, command, payload };
      const timer = setTimeout(() => { this.pending.delete(commandId); reject(new Error('Command acknowledgment timed out; reconnect and retry with the same key.')); }, 30000);
      this.pending.set(commandId, { frame, resolve, reject, timer });
      if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(frame));
    });
  }
  headers(json = true) { return { ...(json ? { 'Content-Type': 'application/json' } : {}),  }; }
  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await fetch(`/api${path}`, { ...options, credentials: 'same-origin', headers: { ...this.headers(), ...options.headers } });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      let payload: unknown = null;
      try { const body = await response.json(); payload = body; detail = typeof body.detail === 'string' ? body.detail : typeof body.detail?.message === 'string' ? body.detail.message : detail; } catch { /* Preserve HTTP error. */ }
      throw new ApiError(detail, response.status, payload);
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }
  uploadAttachment(chatId: string, file: File, signal?: AbortSignal) {
    return this.request<ChatAttachment>(`/chats/${encodeURIComponent(chatId)}/attachments?name=${encodeURIComponent(file.name)}`, { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file, signal });
  }
  get<T>(path: string, signal?: AbortSignal) { return this.request<T>(path, { signal }); }
  post<T>(path: string, data: unknown = {}): Promise<T> {
    const payload = data as Record<string, unknown>;
    const match = path.match(/^\/chats\/([^/]+)\/(messages|reply-requests)$/);
    const stop = path.match(/^\/reply-requests\/([^/]+)\/cancel$/);
    if (this.socket && (match || stop || path === '/presence')) {
      const command = match ? match[2] === 'messages' ? 'send' : 'reply' : stop ? 'stop' : 'presence';
      const body = stop ? { request_id: stop[1] } : payload;
      return this.command(match?.[1] || null, command, body, typeof payload.client_request_key === 'string' ? payload.client_request_key : requestKey()) as Promise<T>;
    }
    return this.request<T>(path, { method: 'POST', body: JSON.stringify(data) });
  }
  put<T>(path: string, data: unknown) { return this.request<T>(path, { method: 'PUT', body: JSON.stringify(data) }); }
  patch<T>(path: string, data: unknown) { return this.request<T>(path, { method: 'PATCH', body: JSON.stringify(data) }); }
  delete<T>(path: string) { return this.request<T>(path, { method: 'DELETE' }); }
  continueAutomaticConversation(chatId: string, chainId: string, clientRequestKey: string, additionalReplies: number, expectedAllowance: number) {
    return this.post<AutomaticContinuation>(`/chats/${encodeURIComponent(chatId)}/automatic-conversations/${encodeURIComponent(chainId)}/continue`, { client_request_key: clientRequestKey, additional_replies: additionalReplies, expected_allowance: expectedAllowance });
  }
  /** Commands resend with stable keys; all content is loaded through authorized HTTP. */
  async events(chatId: string, signal: AbortSignal, onEvent: (event: LiveEvent) => void, onConnection: (connected: boolean) => void, scopes: string[] = [chatId]) {
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/ws`);
    this.socket = socket;
    await new Promise<void>((resolve) => {
      const stop = () => { socket.close(); resolve(); };
      signal.addEventListener('abort', stop, { once: true });
      socket.onopen = () => {
        // Subscribe before HTTP synchronization. Incoming hints trigger another bounded reload.
        for (const scope of new Set([chatId, ...scopes].filter(Boolean))) socket.send(JSON.stringify({ v: 1, type: 'subscribe', chat_id: scope }));
        onConnection(true);
        for (const entry of this.pending.values()) socket.send(JSON.stringify(entry.frame));
      };
      socket.onmessage = event => {
        try {
          const frame = JSON.parse(event.data);
          if (frame.type === 'ping') { socket.send(JSON.stringify({ v: 1, type: 'pong' })); return; }
          const pending = this.pending.get(frame.command_id);
          if (pending && (frame.type === 'ack' || frame.type === 'error')) {
            clearTimeout(pending.timer); this.pending.delete(frame.command_id);
            if (frame.type === 'ack') pending.resolve(frame); else pending.reject(new Error(frame.detail));
          } else if (['changed', 'actor_changed', 'personal_changed', 'twin_changed', 'credential_requests_changed', 'worker_status'].includes(frame.type)) onEvent(frame as LiveEvent);
        } catch { /* A malformed hint cannot replace canonical HTTP state. */ }
      };
      socket.onclose = () => { signal.removeEventListener('abort', stop); onConnection(false); resolve(); };
      socket.onerror = () => socket.close();
    });
  }
}
