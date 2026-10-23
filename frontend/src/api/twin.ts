import type { Actor, ApiClient } from './client';

export type AvatarPreset = 'orbit' | 'bloom' | 'wave' | 'sunrise' | 'mountain' | 'spark';
export type TwinSkill = { name: string; description: string; enabled: boolean; available: boolean; error: string | null };
export type SkillDetail = { name: string; description: string; content: string; references: string[] };
export type MemorySource = { session_id: string; event_id: string };
export type MemoryEntry = { key: string; content: string; version: string; sources: MemorySource[]; updated_at: number | null };
export type MemoryMatch = Omit<MemoryEntry, 'content' | 'updated_at'> & { snippet: string };
export type Soul = { content: string; version: string | null };
export type DreamResult = { status: 'queued' | 'already_queued' | 'scheduled' | 'nothing_to_process'; request_id: string | null };
export type TwinResetStatus = { generation: number; status: 'ready' | 'pending' | 'completed'; reset_id: string | null; private_chat_id: string; idempotency_key?: string | null; expected_generation?: number | null };
export type MemoryStatus = { enabled: boolean; mode: 'git' | 'local' | 'disabled'; dream?: { status: 'idle' | 'scheduled' | 'queued' | 'running' | 'failed' | 'cancelled' | 'published' | 'recovery_required' | 'disabled'; due_at: string | null; last_version?: string | null; cursor_seq?: number | null; last_success_at?: string | null; last_error?: string | null } };
export type Credential = { name: string; ref: string; description: string; keynames: string[] };
export type CredentialRequest = Credential & { id: string; owner_id: string; chat_id: string; origin_request_id: string; reason: string; status: 'pending' | 'fulfilled' | 'dismissed' | 'cancelled'; continuation_request_id?: string | null; created_at: string };
export type CredentialList = { configured: boolean; credentials: Credential[] };
export type CredentialRequests = { configured: boolean; requests: CredentialRequest[] };
export type CredentialInput = { description?: string; values: Record<string, string> };
export type RuntimeStatus = { configured: boolean; provider: string | null; model: string | null; api: string | null; memory: { enabled: boolean | null; mode: 'git' | 'local' | 'disabled' | null }; observed_at: number | null; worker: { online: boolean; expires_at: number | null } };
export type TaskSchedule = { kind: 'once'; at: string } | { kind: 'daily'; time: string } | { kind: 'weekly'; time: string; weekdays: number[] } | { kind: 'interval'; seconds: number };
export type ScheduledTaskRun = { request_id: string; status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'; accepted_at: string; planned_at: string | null; trigger: 'manual' | 'scheduled'; run_id: string | null; error: string | null };
export type ScheduledTask = { id: string; kind: 'user' | 'system_review'; title: string; instructions: string; timezone: string; schedule: TaskSchedule; enabled: boolean; revision: number; next_run_at: string | null; last_run_at: string | null; last_request_id: string | null; last_status: ScheduledTaskRun['status'] | 'silent' | null; last_error: string | null; active_request_id: string | null; pending_work: boolean; created_at: string; updated_at: string };
export type ScheduledTaskInput = Pick<ScheduledTask, 'title' | 'instructions' | 'timezone' | 'schedule'>;

export function twinApi(api: ApiClient) {
  return {
    profile: (signal?: AbortSignal) => api.get<{ actor: Actor }>('/me/twin', signal),
    scheduledTasks: (signal?: AbortSignal) => api.get<{ items: ScheduledTask[] }>('/me/twin/scheduled-tasks', signal),
    createScheduledTask: (data: ScheduledTaskInput) => api.post<ScheduledTask>('/me/twin/scheduled-tasks', data),
    updateScheduledTask: (id: string, data: Partial<ScheduledTaskInput> & { enabled?: boolean }) => api.patch<ScheduledTask>(`/me/twin/scheduled-tasks/${encodeURIComponent(id)}`, data),
    deleteScheduledTask: (id: string) => api.delete<{ ok: boolean }>(`/me/twin/scheduled-tasks/${encodeURIComponent(id)}`),
    runScheduledTask: (id: string, key: string) => api.post<{ request_id: string; task: ScheduledTask }>(`/me/twin/scheduled-tasks/${encodeURIComponent(id)}/run-now`, { idempotency_key: key }),
    scheduledTaskHistory: (id: string, signal?: AbortSignal) => api.get<{ items: ScheduledTaskRun[] }>(`/me/twin/scheduled-tasks/${encodeURIComponent(id)}/history?limit=20`, signal),
    resetStatus: (signal?: AbortSignal) => api.get<TwinResetStatus>('/me/twin/reset', signal),
    reset: (key: string, generation: number) => api.post<TwinResetStatus>('/me/twin/reset', { idempotency_key: key, expected_generation: generation }),
    update: (data: { name?: string; avatar_preset?: AvatarPreset | null }) => api.patch<Actor>('/me/twin', data),
    avatar: (image: Blob) => api.request<Actor>('/me/twin/avatar', { method: 'POST', body: image, headers: { 'Content-Type': 'image/png' } }),
    removeAvatar: () => api.delete<Actor>('/me/twin/avatar'),
    soul: (signal?: AbortSignal) => api.get<Soul>('/me/twin/soul', signal),
    saveSoul: (content: string, expectedVersion: string | null) => api.request<Soul>('/me/twin/soul', { method: 'PUT', body: JSON.stringify({ content, expected_version: expectedVersion }) }),
    clearSoul: (expectedVersion: string | null) => api.delete<Soul>(`/me/twin/soul${expectedVersion ? `?expected_version=${encodeURIComponent(expectedVersion)}` : ''}`),
    skills: (signal?: AbortSignal) => api.get<{ items: TwinSkill[] }>('/me/twin/skills', signal),
    skill: (name: string, signal?: AbortSignal) => api.get<SkillDetail>(`/me/twin/skills/detail?name=${encodeURIComponent(name)}`, signal),
    setSkill: (name: string, enabled: boolean) => api.patch<TwinSkill>(`/me/twin/skills/${encodeURIComponent(name)}`, { enabled }),
    memories: (after?: string, signal?: AbortSignal) => api.get<{ enabled: boolean; items: MemoryEntry[]; next_cursor: string | null }>(`/me/twin/memory?limit=20${after ? `&after=${encodeURIComponent(after)}` : ''}`, signal),
    searchMemory: (query: string, signal?: AbortSignal) => api.get<{ enabled: boolean; items: MemoryMatch[] }>(`/me/twin/memory/search?q=${encodeURIComponent(query)}&limit=20`, signal),
    memory: (key: string, signal?: AbortSignal) => api.get<MemoryEntry>(`/me/twin/memory/entry?key=${encodeURIComponent(key)}`, signal),
    saveMemory: (key: string, content: string, expectedVersion: string | null) => api.request<MemoryEntry>(`/me/twin/memory/entry?key=${encodeURIComponent(key)}`, { method: 'PUT', body: JSON.stringify({ content, expected_version: expectedVersion }) }),
    deleteMemory: (key: string, expectedVersion: string) => api.delete<{ deleted: boolean; key: string }>(`/me/twin/memory/entry?key=${encodeURIComponent(key)}&expected_version=${encodeURIComponent(expectedVersion)}`),
    memoryStatus: (signal?: AbortSignal) => api.get<MemoryStatus>('/me/twin/memory/status', signal),
    dream: () => api.post<DreamResult>('/me/twin/memory/dream', {}),
    credentials: (signal?: AbortSignal) => api.get<CredentialList>('/me/twin/credentials', signal),
    saveCredential: (name: string, data: CredentialInput) => api.request<Credential>(`/me/twin/credentials/${encodeURIComponent(name)}`, { method: 'PUT', body: JSON.stringify(data) }),
    deleteCredential: (name: string) => api.delete(`/me/twin/credentials/${encodeURIComponent(name)}`),
    requests: (signal?: AbortSignal) => api.get<CredentialRequests>('/me/twin/credential-requests', signal),
    complete: (id: string, data: { existing_name: string } | CredentialInput & { name: string }) => api.post<CredentialRequest>(`/me/twin/credential-requests/${encodeURIComponent(id)}/complete`, data),
    dismiss: (id: string) => api.post<CredentialRequest>(`/me/twin/credential-requests/${encodeURIComponent(id)}/dismiss`),
  };
}
