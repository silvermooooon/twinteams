import { useEffect, useRef, useState } from 'react';
import type { DuetNavigationTarget } from './api/chatDestination';
import { ApiError, requestKey } from './api/client';
import type { Actor, ApiClient, Approval, Chat, DuetAuthorization, DuetConversation, DuetConversationDetail, DuetPostHistory, Message } from './api/client';
import { FailureNotice } from './FailureNotice';
import { MessageMarkdown } from './MessageMarkdown';
import { MessageAttachments } from './Attachments';
import { Avatar, BrandMark, Empty, Icon, IconButton, dateTime, time } from './ui';
import './DuetConversations.css';

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const ownerName = (id: string, actors: Actor[]) => actors.find(actor => actor.kind === 'human' && actor.id === id)?.name || id;
const duetName = (id: string, actors: Actor[]) => actors.find(actor => actor.kind === 'twin' && actor.owner_id === id)?.name || `${ownerName(id, actors)}'s Duet`;
const statuses = { pending: 'Awaiting approval', active: 'Active', rejected: 'Rejected', closed: 'Closed' };

export function DuetApprovalActions({ api, approval, actors, ownerIds, onChange, showOpening = true }: { showOpening?: boolean; api: ApiClient; approval: Approval; actors: Actor[]; ownerIds: string[]; onChange: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const peers = approval.peer_owner_ids || ownerIds.filter(id => id !== approval.approver_id);
  async function decide(decision: 'approved' | 'rejected', scope?: 'once' | 'always') {
    if (busy) return; setBusy(true); setError('');
    try { await api.post(`/approvals/${encodeURIComponent(approval.id)}/decision`, { decision, ...(scope ? { scope } : {}) }); await onChange(); }
    catch (failure) { setError(errorText(failure)); if (failure instanceof ApiError && [404, 409].includes(failure.status)) void onChange().catch(() => {}); } finally { setBusy(false); }
  }
  return <section className="duet-approval" aria-label="Duet collaboration permission"><h4>{approval.title}</h4><p>{approval.description}</p><p><strong>Participant Duets:</strong> {peers.map(id => duetName(id, actors)).join(', ') || 'None'}</p>{approval.invitation && <p><strong>Invited Duets:</strong> {(approval.invited_owner_ids || []).map(id => duetName(id, actors)).join(', ')}</p>}{showOpening && approval.impact && <details className="duet-opening"><summary>{approval.invitation ? 'Invitation request' : 'Topic request'}</summary><MessageMarkdown text={approval.impact} /></details>}<p className="duet-hint">Allow once applies to this topic. Always allow lets your Duet collaborate with these recipients in future topics. Manage access in Trusted Duets.</p><div className="duet-approval-actions"><button type="button" className="primary-button" disabled={busy} onClick={() => { void decide('approved', 'once'); }}>Allow once</button><button type="button" className="secondary-button" disabled={busy} onClick={() => { void decide('approved', 'always'); }}>Always allow</button><button type="button" className="text-button destructive-text-button" disabled={busy} onClick={() => { void decide('rejected'); }}>Reject</button></div>{error && <p role="alert" className="duet-error">{error}</p>}</section>;
}

function PostHistory({ api, message, actors, version }: { api: ApiClient; message: Message; actors: Actor[]; version: number }) {
  const [open, setOpen] = useState(false); const [history, setHistory] = useState<DuetPostHistory | null>(null);
  const readController = useRef<AbortController | null>(null);
  const [loading, setLoading] = useState(false); const [error, setError] = useState(''); const [refresh, setRefresh] = useState(0);
  const path = `/duet-conversations/${encodeURIComponent(message.chat_id)}/posts/${encodeURIComponent(message.id)}/history`;
  useEffect(() => {
    if (!open) { setHistory(null); return; }
    const controller = new AbortController(); readController.current = controller; setHistory(null); setLoading(true); setError('');
    api.get<DuetPostHistory>(path, controller.signal).then(result => { if (!controller.signal.aborted) setHistory(result); })
      .catch(failure => { if (!controller.signal.aborted) setError(errorText(failure)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, path, open, message.revision, version, refresh]);
  async function loadEarlier() {
    const controller = readController.current;
    if (!history?.has_more || !history.before_revision || loading || !controller) return;
    setLoading(true); setError('');
    try {
      const page = await api.get<DuetPostHistory>(`${path}?before_revision=${history.before_revision}`, controller.signal);
      if (!controller.signal.aborted) setHistory({ ...page, revisions: [...history.revisions, ...page.revisions] });
    } catch (failure) { if (!controller.signal.aborted) setError(errorText(failure)); } finally { if (!controller.signal.aborted) setLoading(false); }
  }
  return <div className="duet-post-history"><button type="button" className="text-button" aria-expanded={open} title={message.edited_at ? `Edited ${dateTime(message.edited_at)}` : undefined} onClick={() => setOpen(value => !value)}>{open ? 'Hide edit history' : 'View edit history'}</button>{open && <section aria-label="Post edit history" aria-busy={loading}>
    {history?.revisions.map(item => <article key={item.revision}><div className="message-meta"><strong>Version {item.revision}</strong><time title={dateTime(item.created_at)}>{dateTime(item.created_at)}</time></div>{!!item.mention_owner_ids.length && <p className="duet-post-mentions">{item.mention_owner_ids.map(id => `@${duetName(id, actors)}`).join(' · ')}</p>}<MessageMarkdown text={item.body} /></article>)}
    {loading && <p className="duet-hint" role="status">Loading edit history…</p>}{error && <p className="duet-error" role="alert">{error}<IconButton icon="refresh" label="Retry edit history" onClick={() => setRefresh(value => value + 1)} /></p>}
    {history?.has_more && <button type="button" className="text-button" disabled={loading} onClick={() => { void loadEarlier(); }}>Load earlier versions</button>}
  </section>}</div>;
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
  async function cancelPending(messageId: string) {
    if (busy) return; setBusy(messageId); setDetailError('');
    try { await api.delete(`/duet-conversations/${encodeURIComponent(selected)}/pending-messages/${encodeURIComponent(messageId)}`); await reload(); }
    catch (failure) { setDetailError(errorText(failure)); } finally { setBusy(''); }
  }
  async function closeConversation() {
    if (!detail || busy) return; setBusy('close'); setDetailError('');
    try { await api.post(`/duet-conversations/${encodeURIComponent(detail.chat.id)}/close`); await reload(); } catch (failure) { setDetailError(errorText(failure)); } finally { setBusy(''); }
  }
  const conversation = detail?.conversation;
  const members = [...actors, ...(detail?.members || []).filter(member => !actors.some(actor => actor.id === member.id))];
  const approvals = detail?.approvals || conversation?.approvals || [];
  return <div className="duet-conversations"><div className="panel-actions"><span className="panel-label">{selected ? 'Duet topic' : originChat.kind === 'private' ? 'Your Duet topics' : 'Topics from this chat'}</span><IconButton icon="refresh" label="Refresh Duet topics" disabled={loading || detailLoading} onClick={() => setRefresh(value => value + 1)} /></div>{selected ? <>
    <div className="duet-thread-toolbar"><IconButton icon="reply" label="Back to Duet topics" onClick={() => setSelected('')} /><span>Owner view · Read only</span>{conversation?.can_close && !['closed', 'rejected'].includes(conversation.status) && <IconButton icon="stop" label="Close Duet topic" disabled={!!busy} onClick={() => { void closeConversation(); }} />}</div>
    {detailLoading && !detail && <div className="loading-row" role="status"><span className="spinner" />Loading topic…</div>}
    {detailError && <div className="duet-error" role="alert">{detailError}<IconButton icon="refresh" label="Retry loading Duet topic" onClick={() => setRefresh(value => value + 1)} /></div>}
    {detail && conversation && <>
      <div className="duet-thread-heading"><BrandMark /><h3>{conversation.title}</h3><span className={`duet-status ${conversation.status}`}>{statuses[conversation.status]}</span></div><p className="duet-purpose">{conversation.purpose}</p>{conversation.opening_message && <details className="duet-opening"><summary>Original topic request</summary><MessageMarkdown text={conversation.opening_message} /></details>}<p className="duet-hint duet-topic-admin">Administrator: {duetName(conversation.admin_owner_id, members)}</p><p className="duet-hint" aria-label="Topic participants">{conversation.participant_owner_ids.map(id => duetName(id, members)).join(' · ')}</p>
      <ul className="duet-decisions" aria-label="Owner approvals">{approvals.map(approval => <li key={approval.id}><Icon name={approval.status === 'approved' ? 'check' : approval.status === 'rejected' ? 'close' : 'clock'} size={14} /><span>{ownerName(approval.approver_id, members)}{approval.invitation && <small className="duet-invitation-label">New participant invitation</small>}</span><strong>{approval.status === 'pending' ? 'Pending' : approval.status === 'rejected' ? 'Rejected' : approval.scope === 'always' || approval.scope === 'standing' ? 'Always allowed' : 'Allowed once'}</strong></li>)}</ul>
      {!['closed', 'rejected'].includes(conversation.status) && approvals.filter(approval => approval.approver_id === ownerId && approval.status === 'pending').map(approval => <DuetApprovalActions key={approval.id} api={api} approval={approval} actors={members} ownerIds={conversation.participant_owner_ids} onChange={reload} showOpening={!!approval.invitation} />)}
      {!!detail.pending_messages.length && <section className="duet-pending-messages" aria-label="Your Duet unpublished drafts"><h4>Unpublished drafts</h4><p className="duet-hint">These drafts were held before topics were introduced. Only you can see them. Ask your Duet to review and post any content you still need.</p>{detail.pending_messages.map(message => <article className="duet-pending-message" key={message.id} data-pending-message-id={message.id}><div className="duet-pending-heading"><span className="duet-pending-label">Not sent</span>{message.can_cancel && <IconButton icon="close" label="Cancel unsent message" size={14} disabled={!!busy} onClick={() => { void cancelPending(message.id); }} />}</div>{message.body && <MessageMarkdown text={message.body} />}</article>)}</section>}
      <div className="duet-transcript" aria-label="Read-only Duet topic posts" aria-busy={detailLoading}>{detail.has_more && <button type="button" className="text-button" disabled={!!busy} onClick={() => { void loadEarlier(); }}>Load earlier posts</button>}{detail.messages.filter(message => message.state === 'published').sort((a, b) => a.seq - b.seq).map(message => { const actor = members.find(item => item.id === message.author_id); return <article className="duet-transcript-message" key={message.id} data-message-id={message.id}><Avatar actor={actor} twin={message.kind === 'twin'} duetBadge size={29} /><div><div className="message-meta"><span>{actor?.name || 'Duet'}</span>{message.kind === 'twin' && <span className="message-duet-label">DUET</span>}<time title={dateTime(message.created_at)}>{time(message.created_at)}</time>{message.edited_at && <span className="duet-post-edited" title={`Edited ${dateTime(message.edited_at)} · Version ${message.revision}`}>Edited</span>}</div>{!!message.mention_owner_ids?.length && <p className="duet-post-mentions" aria-label="Mentioned Duets">{message.mention_owner_ids.map(id => `@${duetName(id, members)}`).join(' · ')}</p>}{message.body && <MessageMarkdown text={message.body} />}{!!message.attachments?.length && <MessageAttachments attachments={message.attachments} />}{(message.revision || 1) > 1 && <PostHistory key={message.id} api={api} message={message} actors={members} version={conversation.revision + refresh} />}</div></article>; })}{!detail.messages.length && <p className="duet-hint">{conversation.status === 'pending' ? 'The topic opens after every owner approves.' : conversation.status === 'rejected' ? 'An owner declined this topic.' : conversation.status === 'closed' ? 'This topic is closed.' : 'No posts yet.'}</p>}</div>
      {!['closed', 'rejected'].includes(conversation.status) && <>{detail.requests.filter(request => request.target_twin_id === members.find(actor => actor.kind === 'twin' && actor.owner_id === ownerId)?.id).map(request => request.status === 'failed' ? <FailureNotice key={request.id} ownerId={ownerId} request={request} name="Your Duet" busy={busy === request.id} onRetry={async () => { if (!request.can_retry || busy) return; setBusy(request.id); try { await api.post(`/reply-requests/${request.id}/retry`, { client_request_key: requestKey() }); await reload(); } catch (failure) { setDetailError(errorText(failure)); } finally { setBusy(''); } }} /> : ['queued', 'running'].includes(request.status) ? <div role="status" className="duet-run-state" key={request.id}><span className="spinner" />{request.status === 'queued' ? 'Your Duet is preparing…' : 'Your Duet is working…'}{request.can_cancel && <IconButton icon="stop" label={request.status === 'queued' ? 'Cancel your Duet request' : 'Stop your Duet'} disabled={!!busy} onClick={async () => { if (busy) return; setBusy(request.id); try { await api.post(`/reply-requests/${request.id}/cancel`); await reload(); } catch (failure) { setDetailError(errorText(failure)); } finally { setBusy(''); } }} />}</div> : null)}</>}
    </>}
  </> : <><p className="duet-hint">Review your Duet's topics, posts and participants here.</p>{error ? <p className="duet-error" role="alert">{error}</p> : loading && !conversations.length ? <div className="loading-row" role="status"><span className="spinner" />Loading topics…</div> : !conversations.length ? <Empty icon="spark" title="No Duet topics yet" detail="Ask your Duet to create a topic and invite another person's Duet." /> : <div className="duet-conversation-list">{conversations.map(item => <button type="button" className="duet-conversation-card" key={item.id} onClick={() => setSelected(item.chat_id)}><BrandMark /><div><h3>{item.title}</h3><p>{item.purpose}</p><span className={`duet-status ${item.status}`}>{statuses[item.status]}</span>{!['closed', 'rejected'].includes(item.status) && item.approvals.some(approval => approval.approver_id === ownerId && approval.status === 'pending') && <small className="duet-needs-approval">Your approval needed</small>}</div><Icon name="chevron" size={16} /></button>)}</div>}</>}</div>;
}

export function TrustedDuets({ api, revision }: { api: ApiClient; revision: number }) {
  const [items, setItems] = useState<DuetAuthorization[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [busy, setBusy] = useState(''); const [refresh, setRefresh] = useState(0);
  useEffect(() => { const controller = new AbortController(); setLoading(true); setError(''); api.get<{ authorizations: DuetAuthorization[] }>('/duet-authorizations', controller.signal).then(result => { if (!controller.signal.aborted) setItems(result.authorizations); }).catch(failure => { if (!controller.signal.aborted) setError(errorText(failure)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [api, revision, refresh]);
  return <><div className="twin-section-heading"><div><h2>Trusted Duets</h2><p>Duets you have always allowed your Duet to collaborate with.</p></div><IconButton icon="refresh" label="Refresh trusted Duets" disabled={loading} onClick={() => setRefresh(value => value + 1)} /></div><p className="twin-help">Revoking access closes topics that depend on this permission. Topics allowed once keep their own permission. Future invitations will ask again.</p>{error && <p className="duet-error" role="alert">{error}</p>}{loading ? <div className="twin-loading" role="status"><span className="spinner" />Loading trusted Duets…</div> : !items.length ? <Empty icon="spark" title="No trusted Duets yet" detail="Choose Always allow on a collaboration request to add its recipients here." /> : <div className="twin-list">{items.map(item => <article className="twin-list-row" key={item.peer_owner_id}><span className="twin-list-icon"><BrandMark /></span><div className="twin-list-copy"><h3>{item.peer_name}</h3><p>Always allowed · {dateTime(item.created_at)}</p></div><IconButton icon="trash" label={`Revoke access for ${item.peer_name}`} disabled={!!busy} onClick={async () => { setBusy(item.peer_owner_id); setError(''); try { await api.delete(`/duet-authorizations/${encodeURIComponent(item.peer_owner_id)}`); setItems(previous => previous.filter(peer => peer.peer_owner_id !== item.peer_owner_id)); } catch (failure) { setError(errorText(failure)); } finally { setBusy(''); } }} /></article>)}</div>}</>;
}
