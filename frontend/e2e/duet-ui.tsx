// Component-only fixture. All API requests are intercepted by duet-ui.cjs.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { ApiClient } from '../src/api/client';
import { AskUserCard } from '../src/AskUserCard';
import { resolveChatDestination } from '../src/api/chatDestination';
import type { DuetNavigationTarget } from '../src/api/chatDestination';
import type { Actor, ChatDetail } from '../src/api/client';
import { DetailSidebar } from '../src/DetailSidebar';
import { DuetApprovalActions, TrustedDuets } from '../src/DuetConversations';
import '../src/styles.css';
import '../src/MyTwin.css';
const actors: Actor[] = ['Alex', 'Blair', 'Casey'].flatMap((name, i) => [
  { id: `owner-${i}`, kind: 'human', name, initials: name[0], color: '#5264ad' },
  { id: `twin-${i}`, owner_id: `owner-${i}`, kind: 'twin', name: `${name} Duet`, initials: name[0], color: '#5264ad' },
]);
const api = new ApiClient('owner-0');
function OriginLinkHarness() {
  const privateChat = { id: 'private-owner', kind: 'private' as const, title: 'Own private Duet', member_owner_ids: ['owner-0'], unread_count: 0 };
  const group = { id: 'pair', kind: 'group' as const, title: 'Human planning', member_owner_ids: ['owner-0','owner-1'], unread_count: 0 };
  const [chat, setChat] = React.useState<ChatDetail['chat']>(group);
  const [target, setTarget] = React.useState<DuetNavigationTarget | null>(null);
  const [settings, setSettings] = React.useState<ChatDetail['settings']>({ reply_for_me_enabled: false, prompt_override: null, sidebar_open: false, sidebar_tab: 'workspace' });
  async function navigate(id: string) {
    const destination = await resolveChatDestination(api, [privateChat, group], id);
    setChat(destination.chatId === privateChat.id ? privateChat : group);
    setSettings(previous => ({ ...previous, sidebar_open: !!destination.duetChatId, sidebar_tab: destination.duetChatId ? 'duets' : 'workspace' }));
    if (destination.duetChatId) setTarget({ chatId: destination.duetChatId, token: crypto.randomUUID() });
  }
  const detail: ChatDetail = { revision: 1, chat, messages: [], members: actors, requests: [], approvals: [], tracking: [], settings };
  return <div className="workspace"><main className="chat-main" data-main-chat-kind={chat.kind}><h1>{chat.title}</h1><button onClick={() => { void navigate('pair'); }}>Open human chat</button><AskUserCard api={api} question={{ id: 'origin-question', message_id: 'question-message', origin_chat_id: 'duet-1', origin_chat_title: 'Launch planning collaboration', question: 'Confirm the meeting?', options: [], status: 'answered', answer: 'Yes', answer_message_id: 'answer', created_at: '2026-10-26T08:00:00Z', answered_at: '2026-10-26T08:01:00Z' }} onChanged={async () => {}} onOrigin={id => { void navigate(id); }} /></main>{settings.sidebar_open && <DetailSidebar api={api} actors={actors} detail={detail} duetTarget={target} version={1} ownerId="owner-0" onSettings={patch => setSettings(previous => ({ ...previous, ...patch }))} onError={error => { throw error; }} onChange={() => {}} onClearCreate={() => {}} />}</div>;
}
function Harness() {
  const [version, refresh] = React.useState(0);
  const [settings, setSettings] = React.useState<ChatDetail['settings']>({ reply_for_me_enabled: false, prompt_override: null, sidebar_open: true, sidebar_tab: 'workspace' });
  const params = new URLSearchParams(location.search); const kind = params.get('kind') === 'private' ? 'private' : 'group';
  const detail: ChatDetail = { revision: version, chat: { id: params.get('origin') || 'pair', kind, title: kind === 'private' ? 'Alex Duet' : 'Planning', member_owner_ids: params.get('origin') === 'multi' ? ['owner-0', 'owner-1', 'owner-2'] : ['owner-0', 'owner-1'], unread_count: 0 }, messages: [], members: actors, requests: [], approvals: [], tracking: [], settings };
  if (params.has('originlink')) return <OriginLinkHarness />;
  if (params.has('approval')) return <main className="chat-main"><DuetApprovalActions api={api} actors={actors} ownerIds={['owner-0','owner-1']} approval={{ id: 'private-approval', chat_id: 'duet-1', type: 'duet_conversation', approver_id: 'owner-0', title: 'Private inbox collaboration', description: 'Coordinate a meeting.', target: 'Blair Duet', impact: 'Prepared **opening** for recipients.', status: 'pending', peer_owner_ids: ['owner-1'], created_at: '2026-10-26T08:00:00Z' }} onChange={async () => {}} /></main>;
  return params.has('trusted') ? <main className="my-twin-page"><section className="twin-panel"><TrustedDuets api={api} revision={version} /></section></main> : <div className="workspace" style={{ minHeight: '100vh', justifyContent: 'flex-end' }}><DetailSidebar api={api} actors={actors} detail={detail} version={version} ownerId="owner-0" onSettings={patch => setSettings(previous => ({ ...previous, ...patch }))} onError={error => { throw error; }} onChange={() => refresh(value => value + 1)} onClearCreate={() => {}} /></div>;
}
createRoot(document.getElementById('root')!).render(<Harness />);
