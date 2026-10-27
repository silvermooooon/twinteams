import { useEffect, useRef, useState } from 'react';
import type { DuetNavigationTarget } from './api/chatDestination';
import { requestKey } from './api/client';
import type { Actor, ApiClient, Approval, Chat, DuetAuthorization, DuetConversation, DuetConversationDetail, RoutingFailure } from './api/client';
import { AutomaticConversations } from './AutomaticConversations';
import { FailureNotice } from './FailureNotice';
import { MessageMarkdown } from './MessageMarkdown';
import { MessageAttachments } from './Attachments';
import { Avatar, BrandMark, Empty, Icon, IconButton, dateTime, time } from './ui';
import './DuetConversations.css';

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const ownerName = (id: string, actors: Actor[]) => actors.find(actor => actor.kind === 'human' && actor.id === id)?.name || id;
const duetName = (id: string, actors: Actor[]) => actors.find(actor => actor.kind === 'twin' && actor.owner_id === id)?.name || `${ownerName(id, actors)}’s Duet`;
const statuses = { pending: 'Awaiting approval', active: 'Active', rejected: 'Rejected', closed: 'Closed' };

export function DuetApprovalActions({ api, approval, actors, ownerIds, onChange, showOpening = true }: { showOpening?: boolean; api: ApiClient; approval: Approval; actors: Actor[]; ownerIds: string[]; onChange: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const peers = approval.peer_owner_ids || ownerIds.filter(id => id !== approval.approver_id);
  async function decide(decision: 'approved' | 'rejected', scope?: 'once' | 'always') {
    if (busy) return; setBusy(true); setError('');
    try { await api.post(`/approvals/${encodeURIComponent(approval.id)}/decision`, { decision, ...(scope ? { scope } : {}) }); await onChange(); }
    catch (failure) { setError(errorText(failure)); } finally { setBusy(false); }
  }
  return <section className="duet-approval" aria-label="Duet collaboration permission"><h4>{approval.title}</h4><p>{approval.description}</p><p><strong>Recipient Duets:</strong> {peers.map(id => duetName(id, actors)).join(', ') || 'None'}</p>{showOpening && approval.impact && <details className="duet-opening"><summary>Opening request</summary><MessageMarkdown text={approval.impact} /></details>}<p className="duet-hint">Allow once applies to this conversation. Always allow lets your Duet collaborate with these recipients in future conversations. Manage access in Trusted Duets.</p><div className="duet-approval-actions"><button type="button" className="primary-button" disabled={busy} onClick={() => { void decide('approved', 'once'); }}>Allow once</button><button type="button" className="secondary-button" disabled={busy} onClick={() => { void decide('approved', 'always'); }}>Always allow</button><button type="button" className="text-button" disabled={busy} onClick={() => { void decide('rejected'); }}>Reject</button></div>{error && <p role="alert" className="duet-error">{error}</p>}</section>;
}

export function DuetConversations({ api, originChat, navigationTarget, actors, ownerId, version, onChange }: { api: ApiClient; originChat: Chat; navigationTarget?: DuetNavigationTarget | null; actors: Actor[]; ownerId: string; version: number; onChange: () => void }) {
  const historyFloor = useRef<Record<string, number>>({});
  const selection = useRef('');
  const [conversations, setConversations] = useState<DuetConversation[]>([]); const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<DuetConversationDetail | null>(null); const [loading, setLoading] = useState(true); const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState(''); const [detailError, setDetailError] = useState(''); const [refresh, setRefresh] = useState(0); const [busy, setBusy] = useState('');
  useEffect(() => { if (navigationTarget) setSelected(navigationTarget.chatId); }, [navigationTarget]);
  selection.current = selected;
  async function reload() { setRefresh(value => value + 1); onChange(); }
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    api.get<{ conversations: DuetConversation[] }>(`/duet-conversations?origin_chat_id=${encodeURIComponent(originChat.id)}`, controller.signal).then(result => {
      if (controller.signal.aborted) return; setConversations(result.conversations); setSelected(previous => result.conversations.some(item => item.chat_id === previous) ? previous : '');
    }).catch(failure => { if (!controller.signal.aborted) { setConversations([]); setSelected(''); setError(errorText(failure)); } }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, originChat.id, version, refresh]);
  useEffect(() => {
    if (!selected) { setDetail(null); setDetailError(''); return; }
    const controller = new AbortController(); setDetailLoading(true); setDetailError(''); setDetail(previous => previous?.chat.id === selected ? previous : null);
    (async () => {
      const result = await api.get<DuetConversationDetail>(`/duet-conversations/${encodeURIComponent(selected)}?limit=100`, controller.signal);
      const floor = historyFloor.current[selected];
      while (floor !== undefined && result.has_more && result.before_seq && result.before_seq > floor) {
        const page = await api.get<DuetConversationDetail>(`/duet-conversations/${encodeURIComponent(selected)}?limit=100&before_seq=${result.before_seq}`, controller.signal);
        result.messages = [...page.messages, ...result.messages]; result.has_more = page.has_more;
        if (!page.before_seq || page.before_seq >= result.before_seq) break;
        result.before_seq = page.before_seq;
      }
      if (!controller.signal.aborted) { if (result.before_seq) historyFloor.current[selected] = result.before_seq; setDetail(result); }
    })().catch(failure => { if (!controller.signal.aborted) { setDetail(null); setDetailError(errorText(failure)); } }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [api, selected, version, refresh]);
  async function loadEarlier() {
    if (!detail?.has_more || !detail.before_seq || busy) return;
    const chatId = selected; setBusy('history'); setDetailError('');
    try {
      const page = await api.get<DuetConversationDetail>(`/duet-conversations/${encodeURIComponent(chatId)}?limit=100&before_seq=${detail.before_seq}`);
      if (selection.current !== chatId) return;
      if (page.before_seq) historyFloor.current[chatId] = page.before_seq;
      setDetail(previous => previous?.chat.id === chatId ? { ...previous, messages: [...page.messages.filter(message => !previous.messages.some(item => item.id === message.id)), ...previous.messages], has_more: page.has_more, before_seq: page.before_seq } : previous);
    } catch (failure) { if (selection.current === chatId) setDetailError(errorText(failure)); } finally { setBusy(''); }
  }
  async function retryRouting(failure: RoutingFailure) {
    if (busy || !failure.can_retry || detail?.conversation.status !== 'active') return;
    setBusy(failure.id); setDetailError('');
    try {
      await api.post(`/chats/${encodeURIComponent(failure.chat_id)}/routing-decisions/${encodeURIComponent(failure.id)}/retry`, { client_request_key: requestKey(), expected_command_id: failure.command_id });
      await reload();
    } catch (error) { setDetailError(errorText(error)); } finally { setBusy(''); }
  }
  async function closeConversation() {
    if (!detail || busy) return; setBusy('close'); setDetailError('');
    try { await api.post(`/duet-conversations/${encodeURIComponent(detail.chat.id)}/close`); await reload(); } catch (failure) { setDetailError(errorText(failure)); } finally { setBusy(''); }
  }
  const conversation = detail?.conversation;
  const members = [...actors, ...(detail?.members || []).filter(member => !actors.some(actor => actor.id === member.id))];
  const approvals = detail?.approvals || conversation?.approvals || [];
  return <div className="duet-conversations"><div className="panel-actions"><span className="panel-label">{selected ? 'Duet conversation' : originChat.kind === 'private' ? 'Your Duet collaborations' : 'Collaborations from this chat'}</span><IconButton icon="refresh" label="Refresh Duet conversations" disabled={loading || detailLoading} onClick={() => setRefresh(value => value + 1)} /></div>{selected ? <>
    <div className="duet-thread-toolbar"><IconButton icon="reply" label="Back to Duet conversations" onClick={() => setSelected('')} /><span>Owner view · Read only</span>{conversation?.can_close && !['closed', 'rejected'].includes(conversation.status) && <IconButton icon="stop" label="Close Duet conversation" disabled={!!busy} onClick={() => { void closeConversation(); }} />}</div>
    {detailLoading && !detail && <div className="loading-row" role="status"><span className="spinner" />Loading conversation…</div>}
    {detailError && <div className="duet-error" role="alert">{detailError}<IconButton icon="refresh" label="Retry loading Duet conversation" onClick={() => setRefresh(value => value + 1)} /></div>}
    {detail && conversation && <>
      <div className="duet-thread-heading"><BrandMark /><h3>{conversation.title}</h3><span className={`duet-status ${conversation.status}`}>{statuses[conversation.status]}</span></div><p className="duet-purpose">{conversation.purpose}</p>{conversation.opening_message && <details className="duet-opening"><summary>Opening request</summary><MessageMarkdown text={conversation.opening_message} /></details>}<p className="duet-hint">{conversation.participant_owner_ids.map(id => duetName(id, members)).join(' · ')}</p>
      <ul className="duet-decisions" aria-label="Owner approvals">{approvals.map(approval => <li key={approval.id}><Icon name={approval.status === 'approved' ? 'check' : approval.status === 'rejected' ? 'close' : 'clock'} size={14} /><span>{ownerName(approval.approver_id, members)}</span><strong>{approval.status === 'pending' ? 'Pending' : approval.status === 'rejected' ? 'Rejected' : approval.scope === 'always' || approval.scope === 'standing' ? 'Always allowed' : 'Allowed once'}</strong></li>)}</ul>
      {!['closed', 'rejected'].includes(conversation.status) && approvals.filter(approval => approval.approver_id === ownerId && approval.status === 'pending').map(approval => <DuetApprovalActions key={approval.id} api={api} approval={approval} actors={members} ownerIds={conversation.participant_owner_ids} onChange={reload} showOpening={false} />)}
      <div className="duet-transcript" aria-label="Read-only Duet transcript" aria-busy={detailLoading}>{detail.has_more && <button type="button" className="text-button" disabled={!!busy} onClick={() => { void loadEarlier(); }}>Load earlier messages</button>}{detail.messages.filter(message => message.state === 'published').sort((a, b) => a.seq - b.seq).map(message => { const actor = members.find(item => item.id === message.author_id); return <article className="duet-transcript-message" key={message.id} data-message-id={message.id}><Avatar actor={actor} twin={message.kind === 'twin'} duetBadge size={29} /><div><div className="message-meta"><span>{actor?.name || 'Duet'}</span>{message.kind === 'twin' && <span className="message-duet-label">DUET</span>}<time title={dateTime(message.created_at)}>{time(message.created_at)}</time></div>{message.body && <MessageMarkdown text={message.body} />}{!!message.attachments?.length && <MessageAttachments attachments={message.attachments} />}{message.routing_failure && <div className="message-request-chips"><FailureNotice key={JSON.stringify([ownerId, message.routing_failure.id, message.routing_failure.command_id])} ownerId={ownerId} request={{ ...message.routing_failure, can_retry: conversation.status === 'active' && message.routing_failure.can_retry && (message.author_owner_id || actor?.owner_id) === ownerId }} label="Automatic reply could not start" busy={!!busy} onRetry={retryRouting} /></div>}</div></article>; })}{!detail.messages.length && <p className="duet-hint">{conversation.status === 'pending' ? 'The conversation starts after every owner approves.' : conversation.status === 'rejected' ? 'An owner declined this conversation.' : conversation.status === 'closed' ? 'This conversation is closed.' : 'The Duets are preparing their conversation.'}</p>}</div>
      {!['closed', 'rejected'].includes(conversation.status) && <><AutomaticConversations key={`${ownerId}:${selected}`} api={api} chatId={selected} conversations={detail.automatic_conversations} actors={members} onChange={reload} />{detail.requests.filter(request => request.target_twin_id === members.find(actor => actor.kind === 'twin' && actor.owner_id === ownerId)?.id).map(request => request.status === 'failed' ? <FailureNotice key={request.id} ownerId={ownerId} request={request} name="Your Duet" busy={busy === request.id} onRetry={async () => { if (!request.can_retry || busy) return; setBusy(request.id); try { await api.post(`/reply-requests/${request.id}/retry`, { client_request_key: requestKey() }); await reload(); } catch (failure) { setDetailError(errorText(failure)); } finally { setBusy(''); } }} /> : ['queued', 'running'].includes(request.status) ? <div role="status" className="duet-run-state" key={request.id}><span className="spinner" />{request.status === 'queued' ? 'Your Duet is preparing…' : 'Your Duet is working…'}{request.can_cancel && <IconButton icon="stop" label={request.status === 'queued' ? 'Cancel your Duet request' : 'Stop your Duet'} disabled={!!busy} onClick={async () => { if (busy) return; setBusy(request.id); try { await api.post(`/reply-requests/${request.id}/cancel`); await reload(); } catch (failure) { setDetailError(errorText(failure)); } finally { setBusy(''); } }} />}</div> : null)}</>}
    </>}
  </> : <><p className="duet-hint">Duets collaborate in their own conversations. You can review their messages and manage permissions here.</p>{error ? <p className="duet-error" role="alert">{error}</p> : loading && !conversations.length ? <div className="loading-row" role="status"><span className="spinner" />Loading collaborations…</div> : !conversations.length ? <Empty icon="spark" title="No Duet conversations yet" detail="Ask your private Duet to collaborate with another person’s Duet." /> : <div className="duet-conversation-list">{conversations.map(item => <button type="button" className="duet-conversation-card" key={item.id} onClick={() => setSelected(item.chat_id)}><BrandMark /><div><h3>{item.title}</h3><p>{item.purpose}</p><span className={`duet-status ${item.status}`}>{statuses[item.status]}</span>{item.approvals.some(approval => approval.approver_id === ownerId && approval.status === 'pending') && <small className="duet-needs-approval">Your approval needed</small>}</div><Icon name="chevron" size={16} /></button>)}</div>}</>}</div>;
}

export function TrustedDuets({ api, revision }: { api: ApiClient; revision: number }) {
  const [items, setItems] = useState<DuetAuthorization[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [busy, setBusy] = useState(''); const [refresh, setRefresh] = useState(0);
  useEffect(() => { const controller = new AbortController(); setLoading(true); setError(''); api.get<{ authorizations: DuetAuthorization[] }>('/duet-authorizations', controller.signal).then(result => { if (!controller.signal.aborted) setItems(result.authorizations); }).catch(failure => { if (!controller.signal.aborted) setError(errorText(failure)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [api, revision, refresh]);
  return <><div className="twin-section-heading"><div><h2>Trusted Duets</h2><p>Duets you have always allowed your Duet to collaborate with.</p></div><IconButton icon="refresh" label="Refresh trusted Duets" disabled={loading} onClick={() => setRefresh(value => value + 1)} /></div><p className="twin-help">Revoking access stops conversations that depend on this permission. Conversations allowed once keep their own permission. Future collaborations will ask again.</p>{error && <p className="duet-error" role="alert">{error}</p>}{loading ? <div className="twin-loading" role="status"><span className="spinner" />Loading trusted Duets…</div> : !items.length ? <Empty icon="spark" title="No trusted Duets yet" detail="Choose Always allow on a collaboration request to add its recipients here." /> : <div className="twin-list">{items.map(item => <article className="twin-list-row" key={item.peer_owner_id}><span className="twin-list-icon"><BrandMark /></span><div className="twin-list-copy"><h3>{item.peer_name}</h3><p>Always allowed · {dateTime(item.created_at)}</p></div><IconButton icon="trash" label={`Revoke access for ${item.peer_name}`} disabled={!!busy} onClick={async () => { setBusy(item.peer_owner_id); setError(''); try { await api.delete(`/duet-authorizations/${encodeURIComponent(item.peer_owner_id)}`); setItems(previous => previous.filter(peer => peer.peer_owner_id !== item.peer_owner_id)); } catch (failure) { setError(errorText(failure)); } finally { setBusy(''); } }} /></article>)}</div>}</>;
}
