import { BotShareDialog, BotInstallPage } from './BotSharing';
import { BotSkillsDialog } from './BotSkills';
import { AgentMessageDialog, BotDispatchDialog, BotEditorDialog, BotRoomDialog, BotTranscriptDialog, CoordinationLinks, RoomRecipients } from './PersonalBots';
import { PersonCard } from './PersonCard';
import { DesktopNotificationPrompt, DesktopNotificationSettings, useDesktopNotifications } from './DesktopNotifications';
import { MessageReferenceQuote } from './MessageReferenceQuote';
import { MessageMarkdown } from './MessageMarkdown';
import { FailureNotice } from './FailureNotice';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { resolveChatDestination } from './api/chatDestination';
import type { ChatDestination, DuetNavigationTarget } from './api/chatDestination';
import { ApiClient, ApiError, requestKey } from './api/client';
import { useTwinReset } from './TwinReset';
import { TwinOnboarding } from './TwinOnboarding';
import { AskUserCard } from './AskUserCard';
import { LocalSendItem, useLocalSends } from './LocalSends';
import { PrivateTaskStatuses, isBackgroundPrivateTask, privateTaskLabel } from './PrivateTaskStatuses';
import { MessageReactions, useReactionPreferences } from './MessageReactions';
import type { ReactionPreferences } from './MessageReactions';
import type { TwinResetStatus } from './api/twin';
import type { Actor, Approval, Bootstrap, Chat, ChatDetail, ChatSettings, Message, MessageReference, MessageReaction, PersonalBot, ReadResult, Reminder, ReplyRequest, UserQuestion } from './api/client';
import { DuetApprovalActions } from './DuetConversations';
import { DetailSidebar } from './DetailSidebar';
import { LoginScreen } from './Auth';
import { ProviderConfigPage } from './ProviderConfig';
import { MyTwinPage, TWIN_TABS } from './MyTwin';
import type { TwinTab } from './MyTwin';
import { CredentialRequestCards } from './TwinCredentials';
import { twinApi } from './api/twin';
import type { Credential, CredentialRequest } from './api/twin';
import { getLocalProviderConfig } from './api/provider-config';
import type { AuthSession } from './Auth';
import { PeoplePicker, PeopleDialog, RenameChatDialog, ReminderDialog, RemindersPage } from './Dialogs';
import { Avatar, BrandMark, ChatAvatar, chatTitle, FixtureBanner, Icon, IconButton, Modal, useMenuBehavior, dateTime, time } from './ui';
import { t, formatDate } from './i18n';
import { DraftAttachmentList, MessageAttachments, useAttachmentDrafts } from './Attachments';
import { ChatNavigation, ChatSearchPanel } from './TeamsNavigation';
import { useLiquidRefraction } from './refraction';
import './styles.css';
import './TeamsOverlays.css';

// Observed Teams new-conversation illustration, served locally rather than hot-linked.
// Source: https://tflteams.public.onecdn.static.microsoft/evergreen-assets/illustrations/webp/256/compose-l-standard-256x256.webp
const COMPOSE_ILLUSTRATION = '/assets/teams-compose.webp';

type Page = 'chat' | 'reminders' | 'provider-config' | 'my-twin' | 'bot-share';
function routeShareToken() { return location.hash.match(/^#\/bots\/share\/([A-Za-z0-9_-]+)$/)?.[1] || ''; }
function routePage(): Page { return location.hash.startsWith('#/bots/share/') ? 'bot-share' : location.hash.startsWith('#/my-twin') ? 'my-twin' : location.hash === '#/provider-config' ? 'provider-config' : location.hash === '#reminders' ? 'reminders' : 'chat'; }
function routeTwinTab(): TwinTab { const tab = location.hash.split('/')[2] as TwinTab; return TWIN_TABS.includes(tab) ? tab : 'profile'; }

export function ApprovalsDrawer({ api, actors, approvals, onChange, onDecision }: { api: ApiClient; actors: Actor[]; onChange: () => Promise<unknown>; approvals: Approval[]; onDecision: (id: string, decision: 'approved' | 'rejected') => Promise<void> }) {
  const pending = approvals.filter(a => a.status === 'pending').sort((a, b) => a.created_at.localeCompare(b.created_at));
  const [selected, setSelected] = useState<string | null>(pending[0]?.id || null); const [open, setOpen] = useState(!!pending.length); const [details, setDetails] = useState(false); const [busy, setBusy] = useState(false); const previousIds = useRef<string[]>([]);
  useEffect(() => {
    const ids = pending.map(a => a.id);
    if (!ids.length) { setOpen(false); setSelected(null); }
    else if (!selected || !ids.includes(selected)) {
      const oldIndex = previousIds.current.indexOf(selected || '');
      setSelected(ids[Math.min(Math.max(0, oldIndex), ids.length - 1)]); setDetails(false);
      if (!previousIds.current.length) setOpen(true);
    }
    previousIds.current = ids;
  }, [pending.map(a => a.id).join('|'), selected]);
  if (!pending.length) return null;
  const active = pending.find(a => a.id === selected) || pending[0];
  const fileSharing = active.action?.tool === 'present_files';
  return <div className={`approvals-drawer ${open ? 'open' : ''}`}><button className="approval-toggle" aria-expanded={open} onClick={() => setOpen(p => !p)}><Icon name="bell" size={16} /><span>{t('approvals.pending', { count: pending.length })}</span><span className={open ? 'rotate' : ''}><Icon name="chevron" size={15} /></span></button>{open && <><div className="approval-tabs" role="tablist">{pending.map((approval, i) => <button role="tab" aria-selected={approval.id === active.id} key={approval.id} className={approval.id === active.id ? 'selected' : ''} onClick={() => { setSelected(approval.id); setDetails(false); }} title={approval.title}>{t('approvals.tab', { number: i + 1 })}</button>)}</div><div className="approval-card">{active.type === 'duet_conversation' ? <DuetApprovalActions api={api} actors={actors} approval={active} ownerIds={[active.approver_id, ...(active.peer_owner_ids || [])]} onChange={onChange} /> : <><h3>{active.title}</h3><p>{active.description}</p>{details && <dl className="detail-fields"><dt>{t('approvals.action')}</dt><dd>{active.title}</dd><dt>{t('approvals.target')}</dt><dd>{active.target}</dd><dt>{t('approvals.impact')}</dt><dd>{active.impact}</dd>{active.action && <><dt>{t('approvals.tool')}</dt><dd><code>{active.action.tool}</code></dd><dt>{t('approvals.arguments')}</dt><dd><pre className="approval-arguments">{JSON.stringify(active.action.arguments, null, 2)}</pre></dd></>}</dl>}<div className="approval-actions"><button className="primary-button" disabled={busy} onClick={async () => { setBusy(true); try { await onDecision(active.id, 'approved'); } finally { setBusy(false); } }}>{t(fileSharing ? 'approvals.allowFileSharing' : 'approvals.approve')}</button><button className="secondary-button" disabled={busy} onClick={async () => { setBusy(true); try { await onDecision(active.id, 'rejected'); } finally { setBusy(false); } }}>{t(fileSharing ? 'approvals.rejectFileSharing' : 'approvals.reject')}</button><button className="text-button" onClick={() => setDetails(p => !p)} aria-expanded={details}>{t('common.details')}</button></div><small>{t(fileSharing ? 'approvals.fileScope' : 'approvals.once')}</small></>}</div></>}</div>;
}
function SourceQuote({ name, body, unavailable }: { name: string; body: string; unavailable?: boolean }) { return <div className={`source-quote ${unavailable ? 'unavailable' : ''}`}><strong>{name}</strong><span>{unavailable ? t('chat.quoteUnavailable') : body}</span></div>; }
function MessageItem({ onPerson, message, referenceChatName, question, api, onQuestionChanged, onOrigin, actor, own, actors, ownerId, requests, showQueued, groupChat, onCancel, onRetry, onReply, onReference, onTrack, pending, reactionPreferences, onReactionPreferences, onReact }: { onPerson: (person: Actor, anchor?: DOMRect | null) => void; message: Message; referenceChatName?: string; question?: UserQuestion; api: ApiClient; onQuestionChanged: () => Promise<void>; onOrigin: (chatId: string) => void; actor?: Actor; own: boolean; actors: Actor[]; ownerId: string; requests: ReplyRequest[]; showQueued: boolean; groupChat: boolean; onCancel: (request: ReplyRequest) => void; onRetry: (request: ReplyRequest) => void; onReply: (message: Message) => void; onReference?: (message: Message) => void; onTrack: (message: Message) => void; pending: Set<string>; reactionPreferences: ReactionPreferences; onReactionPreferences: (next: ReactionPreferences) => void; onReact: (message: Message, emoji: string, active: boolean, key: string) => Promise<void> }) {
  const [touchActions, setTouchActions] = useState(false);
  const [menu, setMenu] = useState<{ left: number; top: number } | null>(null);
  const referenceable = groupChat && !own && message.kind === 'human' && message.state === 'published' && onReference;
  function openMenu(x: number, y: number) {
    messageElement.current?.focus({ preventScroll: true });
    setMenu({ left: Math.max(4, Math.min(x, window.innerWidth - 184)), top: Math.max(4, Math.min(y, window.innerHeight - 52)) });
  }
  const messageElement = useRef<HTMLElement>(null); const touchHold = useRef<{ timer: ReturnType<typeof setTimeout>; x: number; y: number } | null>(null);
  const clearTouchHold = () => { if (touchHold.current) clearTimeout(touchHold.current.timer); touchHold.current = null; };
  useEffect(() => () => clearTouchHold(), []);
  useEffect(() => { if (!touchActions && !menu) return; const outside = (event: PointerEvent) => { if (!messageElement.current?.contains(event.target as Node)) { setTouchActions(false); setMenu(null); } }; document.addEventListener('pointerdown', outside); return () => document.removeEventListener('pointerdown', outside); }, [touchActions, menu]);
  const queue = requests.filter(r => r.source_message_id === message.id && (showQueued && r.status === 'queued' || r.status === 'failed'));
  const readNames = message.read_by.filter(id => id !== ownerId && actors.some(a => a.id === id && a.kind === 'human')).map(id => actors.find(a => a.id === id)!.name);
  const isTwin = message.kind === 'twin';
  const showDuetIdentity = groupChat && isTwin && actor?.kind === 'twin';
  return <article data-message-id={message.id} className={`message-row ${own ? 'own' : ''} ${isTwin ? 'twin-message' : ''} ${touchActions ? 'touch-actions' : ''}`} ref={messageElement} tabIndex={0} onPointerDown={event => { if (event.pointerType !== 'touch' || (event.target as Element).closest('button')) return; clearTouchHold(); setTouchActions(false); touchHold.current = { x: event.clientX, y: event.clientY, timer: setTimeout(() => { setTouchActions(true); touchHold.current = null; }, 450) }; }} onPointerMove={event => { if (touchHold.current && Math.hypot(event.clientX - touchHold.current.x, event.clientY - touchHold.current.y) > 10) clearTouchHold(); }} onPointerUp={clearTouchHold} onPointerCancel={clearTouchHold} onContextMenu={event => { if (referenceable) { event.preventDefault(); openMenu(event.clientX, event.clientY); } else if (touchActions) event.preventDefault(); }} onKeyDown={event => {
    if (referenceable && (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10'))) {
      event.preventDefault(); const bounds = event.currentTarget.getBoundingClientRect(); openMenu(bounds.left + 8, bounds.top + 8);
    } else if (event.key === 'Escape' && menu) { setMenu(null); messageElement.current?.focus({ preventScroll: true }); }
  }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) { setTouchActions(false); setMenu(null); } }}>
    {menu && referenceable && <div role="menu" className="fluent-menu duet-glass-overlay message-reference-menu" style={menu}><button type="button" role="menuitem" onClick={() => { setMenu(null); onReference(message); }}><Icon name="spark" size={16} />{t('chat.referenceInDuet')}</button></div>}<Avatar actor={actor} size={34} duetBadge={showDuetIdentity} /><div className="message-content"><div className="message-meta">{actor?.kind === 'human' && actor.id !== ownerId ? <button type="button" className="person-name-button" aria-label={t('contacts.personDetailsFor', { name: actor.name })} onClick={event => onPerson(actor, event.currentTarget.getBoundingClientRect())}>{actor.name}</button> : <span>{actor?.name || ''}</span>}{showDuetIdentity && <span className="message-duet-label">DUET</span>}<time title={dateTime(message.created_at)}>{time(message.created_at)}</time></div><div className="message-bubble">{message.coordination && <CoordinationLinks actors={actors} coordination={message.coordination} chatId={message.chat_id} onOpen={onOrigin} />}{message.message_reference && <MessageReferenceQuote reference={message.message_reference} chatName={referenceChatName} />}{message.reply_to && <SourceQuote name={message.reply_to.author_name} body={message.reply_to.body || t('attachments.message')} unavailable={message.reply_to.unavailable} />}{question ? <AskUserCard key={`${api.ownerId}:${question.id}`} api={api} question={question} onChanged={onQuestionChanged} onOrigin={onOrigin} /> : message.body && <MessageMarkdown text={message.body} />}{!!message.attachments?.length && <><MessageAttachments attachments={message.attachments} /></>}</div><MessageReactions message={message} preferences={reactionPreferences} onPreferences={onReactionPreferences} onReact={(emoji, active, key) => onReact(message, emoji, active, key)}><button title={t('chat.reply')} onClick={() => onReply(message)}><Icon name="reply" size={14} /></button> {referenceable && <IconButton icon="spark" size={14} label={t('chat.referenceInDuet')} onClick={() => onReference(message)} />}<button title={t('chat.trackingFromMessage')} onClick={() => onTrack(message)}><Icon name="check" size={14} /></button></MessageReactions>{!!queue.length && <div className="message-request-chips">{queue.map(request => { const twin = actors.find(a => a.id === request.target_twin_id); return request.status === 'queued' ? <span className="queue-chip" key={request.id} title={t('chat.queued')}><Icon name="clock" size={14} /><Avatar actor={twin} size={20} /><span>{twin?.name}</span>{message.author_id === ownerId && request.requester_id === ownerId && request.can_cancel && <button disabled={pending.has(request.id)} onClick={() => onCancel(request)} aria-label={t('chat.cancelRequest')} title={t('chat.cancelRequest')}><Icon name="close" size={12} /></button>}</span> : <FailureNotice key={JSON.stringify([ownerId, request.chat_id, request.id])} ownerId={ownerId} request={request} name={twin?.name} busy={pending.has(request.id)} onRetry={onRetry} />; })}</div>}{own && <div className="read-receipt" title={readNames.length ? t('chat.readBy', { names: readNames.join(', ') }) : t('chat.sent')}><Icon name="check" size={12} />{readNames.length ? t('chat.readBy', { names: readNames.join(', ') }) : ''}</div>}</div></article>;
}
function newerChat(previous: Chat | undefined, incoming: Chat): Chat {
  return previous && (previous.revision ?? -1) > (incoming.revision ?? -1) ? previous : incoming;
}

export default function App() {
  useMenuBehavior();
  useLiquidRefraction();
  const [session, setSession] = useState<AuthSession | null>(null); const [authChecked, setAuthChecked] = useState(false); const [developmentMode, setDevelopmentMode] = useState(false); const [acceptanceFixture, setAcceptanceFixture] = useState(false); const [configLoading, setConfigLoading] = useState(true); const [configError, setConfigError] = useState(''); const [accountOpen, setAccountOpen] = useState(false); const ownerId = session?.current_owner_id || '';
  const api = useMemo(() => new ApiClient(ownerId), [ownerId]);
  const currentApi = useRef(api); currentApi.current = api;
  const reactionPreferences = useReactionPreferences(ownerId);
  const localSends = useLocalSends(api);
  const [twinTab, setTwinTab] = useState<TwinTab>(routeTwinTab); const [twinVersion, setTwinVersion] = useState(0);
  const [scheduledTasksRevision, setScheduledTasksRevision] = useState(0);
  const [memoryResetRevision, setMemoryResetRevision] = useState(0); const [privateResetVersions, setPrivateResetVersions] = useState<Record<string, number>>({});
  const [credentials, setCredentials] = useState<Credential[]>([]); const [credentialRequests, setCredentialRequests] = useState<CredentialRequest[]>([]); const [credentialsConfigured, setCredentialsConfigured] = useState(false); const [credentialsLoading, setCredentialsLoading] = useState(true); const [credentialsError, setCredentialsError] = useState('');
  const credentialSequence = useRef(0); const actorCache = useRef(new Map<string, Actor>());
  const [providerConfigAvailable, setProviderConfigAvailable] = useState(false);
  const providerConfigAllowed = ownerId === 'owner-alex' && developmentMode && !acceptanceFixture;
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null); const [selected, setSelected] = useState(''); const [detail, setDetail] = useState<ChatDetail | null>(null); const [loading, setLoading] = useState(true); const [chatLoading, setChatLoading] = useState(false); const [error, setError] = useState(''); const [page, setPage] = useState<Page>(routePage); const [completed, setCompleted] = useState(false); const [sidebarSnapshot, setSidebarSnapshot] = useState<{ chatId: string; revision: number } | null>(null); const [connected, setConnected] = useState(false); const [hasConnected, setHasConnected] = useState(false);
  const [duetTarget, setDuetTarget] = useState<DuetNavigationTarget | null>(null); const destinationSequence = useRef(0);
  const [trackingVersion, setTrackingVersion] = useState(0); const [notificationSettingsOpen, setNotificationSettingsOpen] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({}); const [replyTo, setReplyTo] = useState<Message | null>(null); const [busyKeys, setBusyKeys] = useState<Set<string>>(new Set());
  const [referenceDraft, setReferenceDraft] = useState<{ key: string; value: MessageReference } | null>(null);
  const detailCurrent = useRef(detail); detailCurrent.current = detail;
  const chatCache = useRef(new Map<string, { detail: ChatDetail | null; kind: Chat['kind']; beforeSeq: number | null; scrollTop: number; atBottom: boolean }>());
  const observedRevisions = useRef(new Map<string, number>());
  const cacheGeneration = useRef(0); const detailCacheGeneration = useRef(-1); const socketConnected = useRef(false);
  const bootstrapLoad = useRef<{ api: ApiClient; pending: boolean; promise: Promise<void> } | null>(null);
  const chatVisit = useRef<AbortController | null>(null);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [readFailures, setReadFailures] = useState<Record<string, 'failed' | 'retrying'>>({});
  const chatLoads = useRef(new Map<string, { generation: number | undefined; signal: AbortSignal; pending: boolean; revision: number; promise: Promise<void> }>());
  const [shareToken, setShareToken] = useState(routeShareToken);
  const [botDialog, setBotDialog] = useState<{ mode: 'create' | 'edit' | 'share' | 'skills' | 'dispatch' | 'message' | 'room' | 'inspect'; botId?: string } | null>(null);
  const [roomRecipients, setRoomRecipients] = useState<Record<string, string[]>>({});
  const [personCard, setPersonCard] = useState<{ actor: Actor; anchor?: DOMRect | null } | null>(null);
  const [notifAnchor, setNotifAnchor] = useState<DOMRect | null>(null);
  const [chatMoreOpen, setChatMoreOpen] = useState(false);
  const [narrowShowingChat, setNarrowShowingChat] = useState(false);
  const utilityClusterRef = useRef<HTMLDivElement>(null);
  const openPersonCard = useCallback((actor: Actor, anchor?: DOMRect | null) => {
    setPersonCard({ actor, anchor: anchor ?? null });
  }, []);
  const [query, setQuery] = useState(''); const [people, setPeople] = useState<Actor[]>([]); const [searchOpen, setSearchOpen] = useState(false); const [peopleLoading, setPeopleLoading] = useState(false); const [renameChat, setRenameChat] = useState<Chat | null>(null); const [peopleModal, setPeopleModal] = useState<{ add: boolean; person?: Actor } | null>(null);  const [reminderOpen, setReminderOpen] = useState(false); const [trackingSeed, setTrackingSeed] = useState<string | null>(null);
  const [sidebarVisible, setSidebarVisible] = useState(true); const [sidebarWidth, setSidebarWidth] = useState(() => Math.min(420, Math.max(248, Number(localStorage.getItem('twinteams.chatPaneWidth')) || 304))); const [newChat, setNewChat] = useState(false); const [findOpen, setFindOpen] = useState(false); const [appMenuOpen, setAppMenuOpen] = useState(false); const [shortcutsOpen, setShortcutsOpen] = useState(false); const [composerMenu, setComposerMenu] = useState<'emoji' | 'attachments' | 'more' | null>(null); const [messageHighlight, setMessageHighlight] = useState(''); const [dragActive, setDragActive] = useState(false); const [expandedComposer, setExpandedComposer] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null); const imageInput = useRef<HTMLInputElement>(null);
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth);
  const isMid = viewportWidth >= 900 && viewportWidth < 1200;
  const isNarrow = viewportWidth < 900;
  const maxPaneWidth = Math.min(420, Math.max(248, viewportWidth - 460));
  const paneWidth = Math.min(sidebarWidth, maxPaneWidth);
  useEffect(() => { const resize = () => setViewportWidth(window.innerWidth); window.addEventListener('resize', resize); return () => window.removeEventListener('resize', resize); }, []);
  const sidebarDrag = useRef<{ x: number; width: number } | null>(null);
  useEffect(() => {
    const move = (event: PointerEvent) => { if (sidebarDrag.current) setSidebarWidth(Math.min(Math.min(420, Math.max(248, window.innerWidth - 460)), Math.max(248, sidebarDrag.current.width + event.clientX - sidebarDrag.current.x))); };
    const up = () => { if (sidebarDrag.current) { sidebarDrag.current = null; document.body.classList.remove('resizing-pane'); } };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.classList.remove('resizing-pane'); };
  }, []);
  useEffect(() => { localStorage.setItem('twinteams.chatPaneWidth', String(sidebarWidth)); }, [sidebarWidth]);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => { const target = event.target as Element; if (!target.closest('[data-floating-menu]')) { setAccountOpen(false); setAppMenuOpen(false); setComposerMenu(null); setChatMoreOpen(false); } };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setSearchOpen(false); setAccountOpen(false); setAppMenuOpen(false); setComposerMenu(null); setChatMoreOpen(false); setShortcutsOpen(false); if (!(document.activeElement instanceof HTMLInputElement)) setFindOpen(false); }
      if (document.querySelector('[aria-modal="true"]')) return;
      if ((navigator.platform.includes('Mac') ? event.metaKey && event.altKey : event.altKey && event.shiftKey && !event.ctrlKey && !event.metaKey) && (event.code === 'KeyO' || event.key.toLowerCase() === 'o') && page === 'chat' && selected && !newChat && fileInput.current) { event.preventDefault(); setComposerMenu('attachments'); return; }
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'x' && page === 'chat' && selected && !newChat) { event.preventDefault(); setExpandedComposer(previous => !previous); input.current?.focus(); return; }
      if ((event.ctrlKey || event.metaKey) && !event.altKey) {
        if (event.key.toLowerCase() === 'k') { event.preventDefault(); setSearchOpen(true); searchInput.current?.focus(); }
        if (event.key.toLowerCase() === 'f' && page === 'chat' && selected) { event.preventDefault(); if (window.innerWidth >= 900 && window.innerWidth < 1200) setSidebarVisible(false); setFindOpen(true); }
        if (event.key === '\\') { event.preventDefault(); setSidebarVisible(previous => { const next = !previous; if (next && window.innerWidth >= 900 && window.innerWidth < 1200) { setFindOpen(false); if (detailCurrent.current?.settings.sidebar_open) void setSettings({ sidebar_open: false }); } return next; }); }
        if (event.key.toLowerCase() === 'n') { event.preventDefault(); setNewChat(true); setNarrowShowingChat(true); setPage('chat'); }
        if (event.key === '/') { event.preventDefault(); setShortcutsOpen(previous => !previous); }
      }
    };
    document.addEventListener('pointerdown', dismiss); document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', key); };
  }, [page, selected, newChat]);
  useEffect(() => { setComposerMenu(null); setChatMoreOpen(false); setFindOpen(false); setDragActive(false); setRenameChat(null); }, [selected, ownerId]);
  useEffect(() => { if (!messageHighlight) return; const timer = setTimeout(() => setMessageHighlight(''), 1800); return () => clearTimeout(timer); }, [messageHighlight]);
  function insertEmoji(emoji: string) {
    const start = input.current?.selectionStart ?? draft.length; const end = input.current?.selectionEnd ?? start;
    setDrafts(previous => { const text = previous[draftKey] || ''; return { ...previous, [draftKey]: text.slice(0, start) + emoji + text.slice(end) }; });
    requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(start + emoji.length, start + emoji.length); });
  }
  function showWorkspace() { setComposerMenu(null); setFindOpen(false); void setSettings({ sidebar_open: true, sidebar_tab: 'workspace' }); }
  function jumpToMessage(id: string) { setMessageHighlight(id); requestAnimationFrame(() => messageArea.current?.querySelector<HTMLElement>(`[data-message-id="${id}"]`)?.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })); }
    const bootstrapSequence = useRef(0); const chatSequence = useRef(0); const pendingSettings = useRef(new Map<string, Partial<ChatSettings>>()); const settingsQueue = useRef<Promise<void>>(Promise.resolve());
  const input = useRef<HTMLTextAreaElement>(null); const searchInput = useRef<HTMLInputElement>(null); const searchWrap = useRef<HTMLDivElement>(null); const messageArea = useRef<HTMLDivElement>(null); const current = useRef({ ownerId, selected }); current.current = { ownerId, selected }; const readSent = useRef(new Set<string>()); const readPending = useRef(new Set<string>()); const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null); const scrollBottom = useRef(true); const focused = useRef(document.visibilityState === 'visible' && document.hasFocus());
  const historyFloor = useRef<Record<string, number>>({}); const resetGenerations = useRef<Record<string, number>>({});
  const bootstrapCurrent = useRef(bootstrap); bootstrapCurrent.current = bootstrap;
  const deviceId = useRef(sessionStorage.getItem('twinteams.deviceId') || requestKey());
  const actualOwnerId = bootstrap?.current_owner_id || ownerId; const actors = bootstrap?.actors || []; const owner = actors.find(a => a.id === actualOwnerId); const ownTwin = actors.find(a => a.kind === 'twin' && a.owner_id === actualOwnerId); const draftKey = `${actualOwnerId}:${selected}`; const draft = drafts[draftKey] || ''; const activeChat = detail?.chat.id === selected && detail.chat.kind !== 'duet' ? detail : null;
  const reference = referenceDraft?.key === draftKey && activeChat?.chat.kind === 'private' ? referenceDraft.value : undefined;
  useEffect(() => { if (reference) input.current?.focus(); }, [reference]);
  const desktopNotifications = useDesktopNotifications(ownerId, chooseChat);
  const { observeBootstrap: observeNotificationBootstrap, observeChat: observeNotificationChat, acknowledgeRead: acknowledgeNotificationRead, revoke: revokeNotifications, stop: stopNotifications } = desktopNotifications;

  const bots = bootstrap?.bots || [];
  const activeBot = activeChat?.chat.kind === 'bot' ? bots.find(bot => bot.id === activeChat.chat.bot_id) : undefined;
  const personalAgents = actors.filter(actor => actor.owner_id === actualOwnerId && actor.kind !== 'human');
  const roomMembers = activeChat?.chat.kind === 'internal' ? activeChat.chat.member_actor_ids!.map(id => actors.find(actor => actor.id === id)!) : [];
  const selectedRecipients = roomRecipients[selected] ?? (ownTwin ? [ownTwin.id] : []);
  const privateChat = bootstrap?.chats.find(chat => chat.kind === 'private');
  const activeTitle = activeChat ? chatTitle(activeChat.chat, actors, actualOwnerId) : '';
  const directPeer = activeChat?.chat.kind === 'group' && activeChat.chat.member_owner_ids.length === 2 ? actors.find(actor => actor.kind === 'human' && actor.id !== actualOwnerId && activeChat.chat.member_owner_ids.includes(actor.id)) : undefined;
  const canRenameChat = activeChat?.chat.kind === 'group' && activeChat.chat.member_owner_ids.length >= 3 && activeChat.chat.can_rename !== false;
  const mergeActor = useCallback((actor: Actor) => {
    if (current.current.ownerId !== api.ownerId) return;
    const cached = actorCache.current.get(actor.id);
    if ((cached?.profile_revision || 0) > (actor.profile_revision || 0)) return;
    actorCache.current.set(actor.id, actor);
    setBootstrap(previous => {
      if (!previous) return previous;
      const prior = previous.actors.find(item => item.id === actor.id);
      if ((prior?.profile_revision || 0) > (actor.profile_revision || 0)) return previous;
      return { ...previous, actors: prior ? previous.actors.map(item => item.id === actor.id ? actor : item) : [...previous.actors, actor], chats: previous.chats.map(chat => chat.kind === 'private' && chat.member_owner_ids.includes(actor.owner_id || '') && actor.kind === 'twin' ? { ...chat, title: actor.name } : chat) };
    });
    setDetail(previous => previous ? { ...previous, members: previous.members.map(item => item.id === actor.id ? actor : item), chat: previous.chat.kind === 'private' && previous.chat.member_owner_ids.includes(actor.owner_id || '') && actor.kind === 'twin' ? { ...previous.chat, title: actor.name } : previous.chat } : previous);
  }, [api]);
  const refreshCredentials = useCallback(async () => {
    if (!api.ownerId) return;
    const sequence = ++credentialSequence.current;
    setCredentialsLoading(true);
    try { const [saved, requested] = await Promise.all([twinApi(api).credentials(), twinApi(api).requests()]); if (current.current.ownerId !== api.ownerId || sequence !== credentialSequence.current) return; setCredentials(saved.credentials); setCredentialRequests(requested.requests); setCredentialsConfigured(saved.configured); setCredentialsError(''); }
    catch (failure) { if (current.current.ownerId === api.ownerId && sequence === credentialSequence.current) setCredentialsError(failure instanceof Error ? failure.message : t('twin.loadFailed')); }
    finally { if (current.current.ownerId === api.ownerId && sequence === credentialSequence.current) setCredentialsLoading(false); }
  }, [api]);
  useEffect(() => { actorCache.current.clear(); credentialSequence.current++; setCredentials([]); setCredentialRequests([]); setCredentialsConfigured(false); setCredentialsError(''); if (ownerId) void refreshCredentials(); }, [ownerId, refreshCredentials]);
  const attachmentDrafts = useAttachmentDrafts(api, actualOwnerId, selected);
  const attachmentsReady = attachmentDrafts.items.every(item => item.status === 'ready');
  const canSend = !!(draft.trim() || attachmentDrafts.items.length) && attachmentsReady && (activeChat?.chat.kind !== 'internal' || selectedRecipients.length > 0);
  useEffect(() => { if (!input.current) return; input.current.style.height = 'auto'; input.current.style.height = `${Math.min(180, Math.max(expandedComposer ? 120 : 28, input.current.scrollHeight))}px`; }, [draft, expandedComposer, selected, newChat]);
  const checkAuth = useCallback(async () => { setConfigLoading(true); setConfigError(''); const client = new ApiClient(); try { const [config, session] = await Promise.all([client.get<{ development_mode: boolean; acceptance_fixture?: boolean }>('/config'), client.get<AuthSession>('/auth/me').catch(() => null)]); setDevelopmentMode(config.development_mode); setAcceptanceFixture(config.acceptance_fixture === true); setSession(session); } catch (e) { setConfigError(e instanceof Error ? e.message : String(e)); } finally { setAuthChecked(true); setConfigLoading(false); } }, []);
  useEffect(() => { localStorage.removeItem('twinteams.developmentOwner'); void checkAuth(); }, [checkAuth]);
  useEffect(() => {
    setProviderConfigAvailable(false);
    if (!providerConfigAllowed) return;
    const controller = new AbortController();
    getLocalProviderConfig(controller.signal).then(config => { if (!controller.signal.aborted) setProviderConfigAvailable(config.available); }).catch(() => {});
    return () => controller.abort();
  }, [providerConfigAllowed]);
  const onError = useCallback((e: unknown) => { if (currentApi.current !== api) return; if (e instanceof ApiError && [401, 403, 404].includes(e.status)) { chatCache.current.clear(); cacheGeneration.current++; localSends.clear(e.status === 401 ? undefined : current.current.selected); if (e.status === 401) { stopNotifications(); setSession(null); } } if (e instanceof DOMException && e.name === 'AbortError') return; setError(e instanceof Error ? e.message : String(e)); }, [api, localSends.clear, stopNotifications]);
  useEffect(() => { api.onUnauthorized = () => { if (currentApi.current !== api) return; chatCache.current.clear(); cacheGeneration.current++; stopNotifications(); localSends.clear(); setSession(null); }; api.onAccessLost = () => { if (currentApi.current !== api) return; chatCache.current.clear(); cacheGeneration.current++; revokeNotifications(); localSends.clear(); setDetail(null); }; return () => { api.onUnauthorized = undefined; api.onAccessLost = undefined; }; }, [api, localSends.clear, stopNotifications, revokeNotifications]);
  const loadBootstrap = useCallback((refresh = true): Promise<void> => {
    const existing = bootstrapLoad.current;
    if (existing?.api === api) { if (refresh) existing.pending = true; return existing.promise; }
    const entry = { api, pending: false, promise: Promise.resolve() };
    bootstrapLoad.current = entry;
    entry.promise = (async () => {
      do {
        entry.pending = false;
        const sequence = ++bootstrapSequence.current; const result = await api.get<Bootstrap>('/bootstrap'); if (currentApi.current !== api || current.current.ownerId !== api.ownerId || sequence !== bootstrapSequence.current) continue; result.actors = result.actors.map(actor => { const cached = actorCache.current.get(actor.id); if (cached && (cached.profile_revision || 0) > (actor.profile_revision || 0)) return cached; actorCache.current.set(actor.id, actor); return actor; }); result.chats = result.chats.filter(chat => chat.kind !== 'duet').map(chat => { const twin = result.actors.find(actor => actor.kind === 'twin' && chat.member_owner_ids.includes(actor.owner_id || '')); return chat.kind === 'private' && twin ? { ...chat, title: twin.name } : chat; }); observeNotificationBootstrap(result.chats, result.actors, result.server_time); setBootstrap(previous => ({ ...result, chats: result.chats.map(chat => newerChat(previous?.chats.find(old => old.id === chat.id), chat)) })); if (result.acceptance_fixture === true) setAcceptanceFixture(true); setSelected(previous => result.chats.some(chat => chat.id === previous) ? previous : result.chats.find(chat => chat.kind === 'private')?.id || result.chats[0]?.id || '');
      } while (entry.pending && currentApi.current === api);
    })().finally(() => { if (bootstrapLoad.current === entry) bootstrapLoad.current = null; });
    return entry.promise;
  }, [api, observeNotificationBootstrap]);
  const fetchChat = useCallback(async (chatId: string, signal: AbortSignal) => {
    if (!chatId) return;
    const sequence = ++chatSequence.current;
    const startedAt = performance.now();
    const key = `${api.ownerId}:${chatId}`;
    const generation = resetGenerations.current[key];
    const authorizedGeneration = cacheGeneration.current;
    const privateChat = bootstrapCurrent.current?.chats.some(chat => chat.id === chatId && chat.kind === 'private');
    const floor = historyFloor.current[key] ?? (messageArea.current && !scrollBottom.current && detailCurrent.current?.chat.id === chatId
      ? detailCurrent.current.before_seq ?? undefined : undefined);
    let latest: ChatDetail | undefined; let reauthorizingHistory = false;
    const isCurrent = () => !signal.aborted && currentApi.current === api && current.current.selected === chatId && current.current.ownerId === api.ownerId && sequence === chatSequence.current && generation === resetGenerations.current[key] && (!privateChat || authorizedGeneration === cacheGeneration.current);
    try {
      const result = await api.get<ChatDetail>(`/chats/${chatId}?limit=30`, signal);
      if (!isCurrent() || detailCurrent.current?.chat.id === chatId && result.revision < detailCurrent.current.revision) return;
      if (result.chat.kind === 'duet') { void resolveChatDestination(api, [...(bootstrapCurrent.current?.chats || []), result.chat], chatId).then(destination => { if (isCurrent()) return openDestination(destination); }).catch(onError); return; }
      latest = result;
      setSidebarSnapshot({ chatId, revision: result.revision });
      let messages = result.messages.filter(message => result.chat.kind !== 'group' || message.kind === 'human' || message.kind === 'system');
      let questions = result.questions || [];
      let cursor = result.before_seq;
      let hasMore = result.has_more;
      reauthorizingHistory = floor !== undefined && !!hasMore && !!cursor && cursor > floor;
      if (reauthorizingHistory) {
        // Status metadata is already authorized. Do not hold it behind older pages,
        // or merge new body/history windows without a complete reauthorization.
        setDetail(previous => previous?.chat.id === chatId ? { ...previous, requests: result.requests, approvals: result.approvals, questions: [...(previous.questions || []).filter(question => !result.messages.some(message => message.id === question.message_id)), ...(result.questions || [])] } : previous);
        localSends.reconcileRequests(result, startedAt);
      }
      // Reauthorize every already loaded page, replacing withdrawn/revoked content.
      // Each request is bounded; no stale earlier-page content is merged from cache.
      while (floor !== undefined && hasMore && cursor && cursor > floor) {
        const page = await api.get<ChatDetail>(`/chats/${chatId}?limit=${Math.min(100, cursor - floor)}&before_seq=${cursor}`, signal);
        if (!isCurrent()) return;
        messages = [...page.messages.filter(message => result.chat.kind !== 'group' || message.kind === 'human' || message.kind === 'system'), ...messages];
        questions = [...(page.questions || []), ...questions];
        hasMore = page.has_more;
        if (!page.before_seq || page.before_seq >= cursor) break;
        cursor = page.before_seq;
      }
      if (!isCurrent()) return;
      if (detailCurrent.current?.chat.id === chatId && result.revision < detailCurrent.current.revision) return;
      localSends.reconcile({ ...result, messages }, startedAt);
      result.members = result.members.map(member => { const cached = actorCache.current.get(member.id); if (cached && (cached.profile_revision || 0) > (member.profile_revision || 0)) return cached; actorCache.current.set(member.id, member); return member; });
      if (result.chat.kind === 'private') { const twin = result.members.find(member => member.kind === 'twin' && result.chat.member_owner_ids.includes(member.owner_id || '')); if (twin) result.chat = { ...result.chat, title: twin.name }; }
      observeNotificationChat(result.chat, result.members);
      setBootstrap(previous => previous ? { ...previous, actors: previous.actors.map(actor => { const updated = result.members.find(member => member.id === actor.id); return updated && (updated.profile_revision || 0) >= (actor.profile_revision || 0) ? updated : actor; }), chats: previous.chats.map(chat => chat.id === result.chat.id ? newerChat(chat, result.chat) : chat) } : previous);
      // Users may scroll during the HTTP reads; preserve their current viewport.
      const anchored = messageArea.current && !scrollBottom.current ? messageArea.current.scrollHeight - messageArea.current.scrollTop : null;
      setDetail(previous => {
        // A partial reaction acknowledgement cannot advance the full chat snapshot.
        // Preserve only newer local reaction aggregates on still-authorized messages.
        const newer = previous?.chat.id === chatId ? previous.messages.filter(message => (message.reactions_revision || 0) > result.revision) : [];
        return { ...result, chat: newerChat(previous?.chat.id === chatId ? previous.chat : undefined, result.chat), questions, messages: messages.map(message => { const reaction = newer.find(item => item.id === message.id); return reaction ? { ...message, reactions: reaction.reactions, reactions_revision: reaction.reactions_revision } : message; }), has_more: hasMore, before_seq: cursor, settings: { ...result.settings, ...pendingSettings.current.get(key) } };
      });
      detailCacheGeneration.current = authorizedGeneration;
      if (anchored !== null) requestAnimationFrame(() => { if (messageArea.current) messageArea.current.scrollTop = messageArea.current.scrollHeight - anchored; });
      return result.revision;
    } catch (error) {
      if (isCurrent()) {
        if (error instanceof ApiError && [401, 403, 404, 409].includes(error.status)) { chatCache.current.delete(key); detailCacheGeneration.current = -1; revokeNotifications(chatId); localSends.clear(chatId); setDetail(null); }
        else if (reauthorizingHistory && latest) {
          // Failed reauthorization cannot leave old cached pages on screen indefinitely.
          delete historyFloor.current[key];
          const snapshot = latest;
          setDetail(previous => previous?.chat.id === chatId && snapshot.revision >= previous.revision ? { ...snapshot, settings: { ...snapshot.settings, ...pendingSettings.current.get(key) } } : previous);
          localSends.reconcile(snapshot, startedAt);
        }
      }
      throw error;
    }
  }, [api, localSends.reconcile, localSends.reconcileRequests, localSends.clear, observeNotificationChat, revokeNotifications]);
  const loadChat = useCallback((chatId = selected, revision?: number): Promise<void> => {
    const signal = chatVisit.current?.signal;
    if (!signal || signal.aborted || !chatId || chatId !== current.current.selected || currentApi.current !== api) return Promise.resolve();
    const key = `${api.ownerId}:${chatId}`; const generation = resetGenerations.current[key];
    const existing = chatLoads.current.get(key);
    if (existing && existing.signal === signal && existing.generation === generation) { if (revision === undefined) existing.pending = true; else existing.revision = Math.max(existing.revision, revision); return existing.promise; }
    const entry = { generation, signal, pending: false, revision: -1, promise: Promise.resolve() };
    chatLoads.current.set(key, entry);
    entry.promise = (async () => {
      let received = -1;
      do { entry.pending = false; entry.revision = -1; received = await fetchChat(chatId, signal) ?? -1; }
      while ((entry.pending || entry.revision > received) && !signal.aborted && currentApi.current === api && current.current.ownerId === api.ownerId && current.current.selected === chatId && resetGenerations.current[key] === generation);
    })().finally(() => { if (chatLoads.current.get(key) === entry) chatLoads.current.delete(key); });
    return entry.promise;
  }, [api, selected, fetchChat]);
  localSends.refresh.current = chatId => { if (current.current.selected === chatId) void loadChat(chatId).catch(onError); };
  const refreshAll = useCallback(async () => { try { await Promise.all([loadBootstrap(), loadChat()]); } catch (e) { onError(e); } }, [loadBootstrap, loadChat, onError]);
  const applyTwinReset = useCallback((status: TwinResetStatus) => {
    const chatId = status.private_chat_id; const key = `${api.ownerId}:${chatId}`;
    chatCache.current.delete(key); cacheGeneration.current++;
    resetGenerations.current[key] = status.generation; bootstrapSequence.current++;
    localSends.clear(chatId);
    if (!bootstrapCurrent.current) void loadBootstrap().catch(onError);
    delete historyFloor.current[key]; pendingSettings.current.delete(key);
    setDrafts(previous => { const next = { ...previous }; delete next[key]; return next; });
    setReferenceDraft(previous => previous?.key === key ? null : previous);
    attachmentDrafts.clearScope(key);
    setMemoryResetRevision(value => value + 1); setTwinVersion(value => value + 1);
    setPrivateResetVersions(previous => ({ ...previous, [chatId]: status.generation }));
    for (const value of readSent.current) if (value.startsWith(`${key}:`)) readSent.current.delete(value);
    readPending.current.delete(key);
    setReadFailures(previous => { const next = { ...previous }; delete next[key]; return next; });
    if (current.current.selected === chatId) {
      chatSequence.current++; setDetail(null); setReplyTo(null); setTrackingSeed(null); setFindOpen(false); setMessageHighlight(''); setComposerMenu(null);
      void loadChat(chatId).catch(onError);
    }
    void api.get<Chat>(`/chats/${chatId}/summary`).then(chat => { if (current.current.ownerId === api.ownerId && resetGenerations.current[key] === status.generation) setBootstrap(previous => previous ? { ...previous, chats: previous.chats.map(old => old.id === chatId ? chat : old) } : previous); }).catch(onError);
    void refreshCredentials();
  }, [api, loadBootstrap, loadChat, onError, attachmentDrafts.clearScope, refreshCredentials, localSends.clear]);
  const twinReset = useTwinReset(api, applyTwinReset);
  const refreshTwinReset = twinReset.refresh;
  useEffect(() => { chatCache.current.clear(); observedRevisions.current.clear(); cacheGeneration.current++; detailCacheGeneration.current = -1; setPersonCard(null); setBotDialog(null); setRoomRecipients({}); setDuetTarget(null); setReferenceDraft(null); destinationSequence.current++; if (!ownerId) { localSends.clear(); setBootstrap(null); setDetail(null); setSelected(''); setLoading(false); return; } let alive = true; chatLoads.current.clear(); setLoading(true); setDetail(null); setBootstrap(null); setSelected(''); setHasConnected(false); readSent.current.clear(); readPending.current.clear(); setReadFailures({}); loadBootstrap().catch(e => { if (alive) onError(e); }).finally(() => { if (alive) setLoading(false); }); return () => { alive = false; }; }, [api]);
  useEffect(() => {
    if (!selected) return;
    const visit = new AbortController(); chatVisit.current = visit;
    const key = `${api.ownerId}:${selected}`;
    const cached = socketConnected.current ? chatCache.current.get(key) : undefined;
    setChatLoading(!cached?.detail); setLoadingEarlier(false); setReplyTo(null); setTrackingSeed(null); scrollBottom.current = cached?.atBottom ?? true;
    setDetail(previous => cached?.detail || (previous?.chat.id === selected ? previous : null));
    if (cached) {
      if (cached.beforeSeq) historyFloor.current[key] = cached.beforeSeq;
      if (messageArea.current) messageArea.current.scrollTop = cached.scrollTop;
    }
    loadChat(selected).then(() => { if (cached && !cached.detail) requestAnimationFrame(() => { if (!visit.signal.aborted && messageArea.current) messageArea.current.scrollTop = cached.scrollTop; }); }).catch(e => { if (!visit.signal.aborted) onError(e); }).finally(() => { if (!visit.signal.aborted) setChatLoading(false); });
    return () => {
      visit.abort();
      // Only a bounded cached window can carry earlier pages into the next visit.
      delete historyFloor.current[key];
    };
  }, [selected, api]);
  const chatScopeKey = bootstrap?.chats.map(chat => chat.id).sort().join('|') || '';
  useEffect(() => { api.subscribeChats([selected, ...chatScopeKey.split('|')]); }, [api, selected, chatScopeKey]);
  useEffect(() => {
    if (!ownerId) return;
    const controller = new AbortController(); let retryTimer: ReturnType<typeof setTimeout> | undefined; let reconnectDelay = 700; let trackingTimer: ReturnType<typeof setTimeout> | undefined; let synchronized = false;
    const summaryLoads = new Set<string>();
    async function loadSummary(chatId: string) {
      const key = `${api.ownerId}:${chatId}`; const generation = resetGenerations.current[key];
      const loadKey = `${key}:${generation}`;
      if (summaryLoads.has(loadKey)) return;
      summaryLoads.add(loadKey);
      try {
        let received: number;
        do {
          const chat = await api.get<Chat>(`/chats/${chatId}/summary`, controller.signal);
          if (controller.signal.aborted || currentApi.current !== api || generation !== resetGenerations.current[key]) return;
          const revision = chat.revision ?? -1; received = revision;
          const previous = bootstrapCurrent.current?.chats.find(item => item.id === chatId);
          if (revision >= (previous?.revision ?? -1)) observeNotificationChat(chat, bootstrapCurrent.current?.actors || []);
          setBootstrap(previous => previous ? { ...previous, chats: previous.chats.map(old => old.id === chatId && revision >= (old.revision ?? -1) ? chat : old) } : previous);
        } while (received < (observedRevisions.current.get(key) ?? -1));
      } catch (failure) {
        if (!controller.signal.aborted && currentApi.current === api && generation === resetGenerations.current[key] && failure instanceof ApiError && [403, 404].includes(failure.status)) { chatCache.current.delete(key); revokeNotifications(chatId); }
      } finally { summaryLoads.delete(loadKey); }
    }
    const run = async () => {
      try {
        await api.events('', controller.signal, event => {
          if (controller.signal.aborted || currentApi.current !== api) return;
          const selected = current.current.selected;
          if (event.type === 'actor_changed' && event.actor_id) {
            void api.get<Actor>(`/actors/${encodeURIComponent(event.actor_id)}`).then(mergeActor).catch(onError);
            return;
          }
          if (event.type === 'personal_changed' || event.type === 'twin_changed' || event.type === 'credential_requests_changed') {
            if (event.section === 'bots') { void refreshBots().catch(onError); return; }
            if (event.section === 'scheduled_tasks') { setScheduledTasksRevision(value => value + 1); return; }
            if (event.section === 'tracking' || event.section === 'chats') {
              // These notices also cover withdrawn sources and membership changes.
              cacheGeneration.current++;
              for (const [key, entry] of chatCache.current) if (entry.kind === 'private') chatCache.current.delete(key);
              if (detailCurrent.current?.chat.kind === 'private') void loadChat(selected).catch(onError);
            }
            if (event.section === 'tracking') { clearTimeout(trackingTimer); trackingTimer = setTimeout(() => setTrackingVersion(value => value + 1), 80); return; }
            if (event.section === 'reset') { void refreshTwinReset(); return; }
            if (event.section === 'chats') { setTrackingVersion(value => value + 1); void loadBootstrap().catch(onError); return; }
            setTwinVersion(value => value + 1);
            if (!event.section || event.section === 'credentials' || event.type === 'credential_requests_changed') void refreshCredentials();
            return;
          }
          if (event.type === 'worker_status') { setTwinVersion(value => value + 1); return; }
          if (event.chat_id && event.revision !== undefined) {
            const key = `${api.ownerId}:${event.chat_id}`;
            observedRevisions.current.set(key, Math.max(observedRevisions.current.get(key) ?? -1, event.revision));
            const cached = chatCache.current.get(key);
            if (cached?.detail && cached.detail.revision < event.revision) cached.detail = null;
          }
          if (event.chat_id && event.chat_id !== selected) {
            const known = bootstrapCurrent.current?.chats.find(chat => chat.id === event.chat_id);
            if (event.revision === undefined || event.revision > (known?.revision ?? -1)) void loadSummary(event.chat_id);
            return;
          }
          if (event.type === 'changed') {
            const alreadyLoaded = () => event.revision !== undefined && detailCurrent.current?.chat.id === selected && event.revision <= detailCurrent.current.revision;
            if (alreadyLoaded()) return;
            if (refreshTimer.current) clearTimeout(refreshTimer.current);
            refreshTimer.current = setTimeout(() => { if (current.current.selected === selected && !alreadyLoaded()) void loadChat(selected, event.revision).catch(onError); }, 80);
          }
        }, state => {
          if (controller.signal.aborted || currentApi.current !== api) return;
          socketConnected.current = state; setConnected(state);
          if (!state) { chatCache.current.clear(); cacheGeneration.current++; return; }
          const selected = current.current.selected;
          setHasConnected(true); reconnectDelay = 700;
          if (!synchronized) { synchronized = true; if (!bootstrapCurrent.current) void loadBootstrap(false).catch(onError); return; }
          setTrackingVersion(value => value + 1); setScheduledTasksRevision(value => value + 1);
          void loadBootstrap().catch(onError); if (selected) void loadChat(selected).catch(onError);
          setTwinVersion(value => value + 1); void refreshCredentials(); void refreshTwinReset();
          void twinApi(api).profile().then(result => mergeActor(result.actor)).catch(() => {});
        });
      } catch { if (!controller.signal.aborted) setConnected(false); }
      if (!controller.signal.aborted) { retryTimer = setTimeout(() => { void run(); }, reconnectDelay); reconnectDelay = Math.min(reconnectDelay * 2, 10000); }
    };
    void run();
    return () => { controller.abort(); clearTimeout(retryTimer); clearTimeout(trackingTimer); if (refreshTimer.current) clearTimeout(refreshTimer.current); setConnected(false); };
  }, [api, ownerId, mergeActor, refreshCredentials, refreshTwinReset, observeNotificationChat, revokeNotifications]);
  const markVisibleRead = useCallback((retry = false) => {
    const chatId = selected; const reader = actualOwnerId; const key = `${reader}:${chatId}`;
    const shown = detailCurrent.current;
    if (!focused.current || page !== 'chat' || current.current.selected !== chatId || shown?.chat.id !== chatId || !messageArea.current || readPending.current.has(key)) return;
    if (isNarrow && !narrowShowingChat) return;
    if (isNarrow && (findOpen || shown.settings.sidebar_open)) return;
    if (readFailures[key] && shown.chat.unread_count === 0) {
      setReadFailures(previous => { const next = { ...previous }; delete next[key]; return next; });
      return;
    }
    if (readFailures[key] && !retry) return;
    const area = messageArea.current.getBoundingClientRect(); const ids: string[] = [];
    const unread = new Set(shown.messages.filter(message => message.state === 'published' && message.author_id !== reader && !message.read_by.includes(reader) && (shown.chat.kind !== 'group' || message.kind === 'human')).map(message => message.id));
    messageArea.current.querySelectorAll<HTMLElement>('[data-message-id]').forEach(element => {
      const id = element.dataset.messageId!; const rect = element.getBoundingClientRect();
      const visibleHeight = Math.min(rect.bottom, area.bottom, window.innerHeight) - Math.max(rect.top, area.top, 0);
      const visibleWidth = Math.min(rect.right, area.right, window.innerWidth) - Math.max(rect.left, area.left, 0);
      if (rect.height > 0 && visibleWidth > 0 && visibleHeight >= Math.min(rect.height * .6, 80) && unread.has(id) && !readSent.current.has(`${key}:${id}`)) ids.push(id);
    });
    if (!ids.length) {
      if (retry) setReadFailures(previous => { const next = { ...previous }; delete next[key]; return next; });
      return;
    }
    readPending.current.add(key);
    if (retry) setReadFailures(previous => ({ ...previous, [key]: 'retrying' }));
    const generation = resetGenerations.current[key];
    const isCurrent = () => currentApi.current === api && generation === resetGenerations.current[key];
    void api.post<ReadResult>(`/chats/${chatId}/read`, { message_ids: ids }).then(result => {
      if (!isCurrent()) return;
      result.message_ids.forEach(id => readSent.current.add(`${key}:${id}`));
      acknowledgeNotificationRead(chatId, result.message_ids);
      observeNotificationChat(result.chat, bootstrapCurrent.current?.actors || []);
      setBootstrap(previous => previous ? { ...previous, chats: previous.chats.map(chat => chat.id === chatId ? newerChat(chat, result.chat) : chat) } : previous);
      // A read acknowledgement updates the summary, not the full history revision.
      setDetail(previous => previous?.chat.id === chatId ? { ...previous, chat: newerChat(previous.chat, result.chat), messages: previous.messages.map(message => result.message_ids.includes(message.id) && !message.read_by.includes(reader) ? { ...message, read_by: [...message.read_by, reader] } : message) } : previous);
      setReadFailures(previous => { const next = { ...previous }; delete next[key]; return next; });
    }).catch(failure => {
      if (!isCurrent()) return;
      setReadFailures(previous => ({ ...previous, [key]: 'failed' }));
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) onError(failure);
      if (failure instanceof ApiError && [403, 404, 409].includes(failure.status)) {
        if (current.current.selected === chatId) void loadChat(chatId).catch(onError);
      }
    }).finally(() => { if (isCurrent()) readPending.current.delete(key); });
  }, [api, page, selected, actualOwnerId, readFailures, isNarrow, narrowShowingChat, findOpen, activeChat?.settings.sidebar_open, acknowledgeNotificationRead, observeNotificationChat, loadChat, onError]);
  useEffect(() => { const timer = setTimeout(() => { if (scrollBottom.current) messageArea.current?.scrollTo({ top: messageArea.current.scrollHeight }); markVisibleRead(); }, 60); return () => clearTimeout(timer); }, [activeChat?.messages, localSends.items.length, selected, markVisibleRead]);
  useEffect(() => {
    if (!ownerId) return; sessionStorage.setItem('twinteams.deviceId', deviceId.current);
    const update = () => { focused.current = document.visibilityState === 'visible' && document.hasFocus(); void api.post('/presence', { device_id: deviceId.current, focused_chat_id: focused.current && page === 'chat' ? selected || null : null, visible: focused.current }).catch(() => {}); if (focused.current) markVisibleRead(); };
    update(); const timer = setInterval(() => { void api.post('/presence', { device_id: deviceId.current, focused_chat_id: focused.current && page === 'chat' ? selected || null : null, visible: focused.current }).catch(() => {}); }, 4000); window.addEventListener('focus', update); window.addEventListener('blur', update); document.addEventListener('visibilitychange', update);
    return () => { clearInterval(timer); window.removeEventListener('focus', update); window.removeEventListener('blur', update); document.removeEventListener('visibilitychange', update); };
  }, [api, selected, page, connected, markVisibleRead]);
  useEffect(() => { if (!searchOpen || !ownerId) return; const abort = new AbortController(); const timer = setTimeout(() => { setPeopleLoading(true); api.get<Actor[]>(`/people?q=${encodeURIComponent(query)}`, abort.signal).then(setPeople).catch(e => { if (!abort.signal.aborted) onError(e); }).finally(() => { if (!abort.signal.aborted) setPeopleLoading(false); }); }, 180); return () => { clearTimeout(timer); abort.abort(); }; }, [query, searchOpen, api]);
  useEffect(() => { const close = (e: MouseEvent) => { if (!searchWrap.current?.contains(e.target as Node)) setSearchOpen(false); }; document.addEventListener('mousedown', close); return () => document.removeEventListener('mousedown', close); }, []);
  useEffect(() => { const pop = () => { setPage(routePage()); setShareToken(routeShareToken()); setTwinTab(routeTwinTab()); setAccountOpen(false); }; window.addEventListener('hashchange', pop); return () => window.removeEventListener('hashchange', pop); }, []);
  function navigate(next: Page) { setPage(next); setAppMenuOpen(false); setChatMoreOpen(false); setNewChat(false); setComposerMenu(null); setAccountOpen(false); setSearchOpen(false); setPeopleModal(null); setReminderOpen(false); history.replaceState(null, '', next === 'my-twin' ? `#/my-twin/${twinTab}` : next === 'provider-config' ? '#/provider-config' : `#${next}`); }
  function chooseTwinTab(next: TwinTab) { setTwinTab(next); history.replaceState(null, '', `#/my-twin/${next}`); }
  const inspectorOpen = !!(findOpen || activeChat?.settings.sidebar_open);
  useEffect(() => {
    if (isMid && sidebarVisible && inspectorOpen) setSidebarVisible(false);
  }, [isMid, sidebarVisible, inspectorOpen]);
  function toggleSidebarPane() {
    setSidebarVisible(previous => {
      const next = !previous;
      if (next && isMid) {
        setFindOpen(false);
        if (activeChat?.settings.sidebar_open) void setSettings({ sidebar_open: false });
      }
      return next;
    });
  }
  function toggleInspectorPane() {
    if (!activeChat) return;
    const next = !activeChat.settings.sidebar_open;
    setFindOpen(false);
    if (next && isMid) setSidebarVisible(false);
    void setSettings({ sidebar_open: next });
  }
  function toggleFindPane() {
    setFindOpen(previous => {
      const next = !previous;
      if (next && isMid) setSidebarVisible(false);
      return next;
    });
  }
  async function openDestination(destination: ChatDestination) {
    const navigation = destinationSequence.current;
    if (currentApi.current !== api || current.current.ownerId !== api.ownerId) return;
    if (destination.duetChatId) {
      await api.patch(`/chats/${encodeURIComponent(destination.chatId)}/settings`, { sidebar_open: true, sidebar_tab: 'duets' });
      if (currentApi.current !== api || current.current.ownerId !== api.ownerId || navigation !== destinationSequence.current) return;
      setDuetTarget({ chatId: destination.duetChatId, token: requestKey() });
      setDetail(previous => previous?.chat.id === destination.chatId ? { ...previous, settings: { ...previous.settings, sidebar_open: true, sidebar_tab: 'duets' } } : previous);
    }
    if (destination.chatId !== current.current.selected) {
      const previous = detailCurrent.current; const cache = chatCache.current;
      const previousKey = previous && `${api.ownerId}:${previous.chat.id}`;
      if (socketConnected.current && previous && previousKey && detailCacheGeneration.current === cacheGeneration.current) {
        const trimmed = previous.messages.length > 130;
        const messages = trimmed ? previous.messages.slice(-30) : previous.messages;
        const snapshot = trimmed ? { ...previous, messages, questions: previous.questions?.filter(question => messages.some(message => message.id === question.message_id)), has_more: true, before_seq: messages[0]?.seq } : previous;
        cache.delete(previousKey);
        const valid = previous.revision >= (observedRevisions.current.get(previousKey) ?? -1);
        cache.set(previousKey, { detail: valid ? snapshot : null, kind: previous.chat.kind, beforeSeq: !trimmed && messages.length > 30 ? previous.before_seq ?? null : null, scrollTop: trimmed ? 0 : messageArea.current?.scrollTop || 0, atBottom: trimmed || scrollBottom.current });
        while (cache.size > 5) cache.delete(cache.keys().next().value!);
      }
      const nextKey = `${api.ownerId}:${destination.chatId}`;
      const cached = socketConnected.current ? cache.get(nextKey) : undefined;
      if (cached) { cache.delete(nextKey); cache.set(nextKey, cached); detailCacheGeneration.current = cacheGeneration.current; }
      setDetail(cached?.detail || null);
    }
    if (window.innerWidth < 900) setNarrowShowingChat(true);
    setSelected(destination.chatId); setFindOpen(false); navigate('chat');
  }
  function mergeBot(bot: PersonalBot) {
    actorCache.current.set(bot.actor.id, bot.actor);
    setBootstrap(previous => previous && { ...previous,
      bots: previous.bots?.some(item => item.id === bot.id) ? previous.bots.map(item => item.id === bot.id ? bot : item) : [...(previous.bots || []), bot],
      actors: previous.actors.some(item => item.id === bot.id) ? previous.actors.map(item => item.id === bot.id ? bot.actor : item) : [...previous.actors, bot.actor],
      chats: previous.chats.some(item => item.id === bot.chat_id) ? previous.chats.map(item => item.id === bot.chat_id ? bot.chat : item) : [...previous.chats, bot.chat],
    });
    chatCache.current.delete(`${api.ownerId}:${bot.chat_id}`);
    setDetail(previous => previous?.chat.id === bot.chat_id ? { ...previous, chat: bot.chat, members: previous.members.map(item => item.id === bot.id ? bot.actor : item) } : previous);
  }
  async function refreshBots() {
    const saved = await api.get<PersonalBot[]>('/bots');
    if (currentApi.current !== api) return;
    saved.forEach(mergeBot);
  }
  function internalMessageSent(chatId: string) {
    if (currentApi.current !== api) return;
    if (chatId === selected) void loadChat(chatId).catch(onError);
    else void openDestination({ chatId });
  }
  function referenceChatName(value?: MessageReference) {
    const chat = value && bootstrap?.chats.find(item => item.id === value.chat_id);
    return chat ? chatTitle(chat, actors, actualOwnerId) : undefined;
  }
  function referenceInDuet(message: Message) {
    const inbox = bootstrap?.chats.find(chat => chat.kind === 'private' && chat.member_owner_ids.includes(actualOwnerId));
    if (!inbox) return;
    setReferenceDraft({ key: `${actualOwnerId}:${inbox.id}`, value: { message_id: message.id, chat_id: message.chat_id,
      author_name: actors.find(actor => actor.id === message.author_id)?.name || '', body: message.body, attachments: message.attachments || [] } });
    chooseChat(inbox.id);
  }
  function chooseChat(chatId: string) {
    const sequence = ++destinationSequence.current;
    const known = bootstrapCurrent.current?.chats.find(chat => chat.id === chatId);
    if (known && known.kind !== 'duet') { void openDestination({ chatId }); return; }
    void resolveChatDestination(api, bootstrapCurrent.current?.chats || [], chatId).then(destination => { if (sequence === destinationSequence.current) return openDestination(destination); }).catch(onError);
  }
  async function reactToMessage(message: Message, emoji: string, active: boolean, key: string) {
    const scope = `${api.ownerId}:${message.chat_id}`; const generation = resetGenerations.current[scope];
    const result = await api.put<{ chat_id: string; message_id: string; reactions: MessageReaction[]; revision: number }>(`/chats/${encodeURIComponent(message.chat_id)}/messages/${encodeURIComponent(message.id)}/reactions`, { emoji, active, client_request_key: key });
    if (current.current.ownerId !== api.ownerId || current.current.selected !== message.chat_id || generation !== resetGenerations.current[scope]) return;
    setDetail(previous => previous?.chat.id === message.chat_id ? { ...previous, messages: previous.messages.map(item => item.id === result.message_id && Math.max(previous.revision, item.reactions_revision || 0) <= result.revision ? { ...item, reactions: result.reactions, reactions_revision: result.revision } : item) } : previous);
  }
  async function withBusy(key: string | string[], action: () => Promise<unknown>) { const keys = typeof key === 'string' ? [key] : key; if (keys.some(id => busyKeys.has(id))) return; setBusyKeys(p => new Set([...p, ...keys])); try { await action(); } catch (e) { onError(e); } finally { setBusyKeys(p => { const next = new Set(p); keys.forEach(id => next.delete(id)); return next; }); } }
  async function setSettings(patch: Partial<ChatSettings>) {
    if (!activeChat) return;
    const chatId = activeChat.chat.id; const settingsKey = `${api.ownerId}:${chatId}`;
    pendingSettings.current.set(settingsKey, { ...pendingSettings.current.get(settingsKey), ...patch });
    setDetail(previous => previous?.chat.id === chatId ? { ...previous, settings: { ...previous.settings, ...patch } } : previous);
    const clearPatch = () => {
      const remaining = { ...pendingSettings.current.get(settingsKey) };
      for (const field of Object.keys(patch) as (keyof ChatSettings)[]) if (remaining[field] === patch[field]) delete remaining[field];
      if (Object.keys(remaining).length) pendingSettings.current.set(settingsKey, remaining); else pendingSettings.current.delete(settingsKey);
      return remaining;
    };
    settingsQueue.current = settingsQueue.current.catch(() => {}).then(async () => {
      if (current.current.ownerId !== api.ownerId) { clearPatch(); return; }
      try { const saved = await api.patch<ChatSettings>(`/chats/${chatId}/settings`, patch); const remaining = clearPatch(); if (current.current.ownerId === api.ownerId) setDetail(previous => previous?.chat.id === chatId ? { ...previous, settings: { ...saved, ...remaining } } : previous); }
      catch (error) { clearPatch(); onError(error); await loadChat(chatId).catch(onError); }
    });
    await settingsQueue.current;
  }
  async function loadEarlier() {
    const signal = chatVisit.current?.signal;
    if (loadingEarlier || !signal || signal.aborted || !activeChat?.has_more || !activeChat.before_seq) return;
    setLoadingEarlier(true);
    const key = `${api.ownerId}:${selected}`; const generation = resetGenerations.current[key]; const sequence = chatSequence.current;
    try {
      const previousHeight = messageArea.current?.scrollHeight || 0;
      const result = await api.get<ChatDetail>(`/chats/${selected}?before_seq=${activeChat.before_seq}&limit=100`, signal);
      if (signal.aborted || current.current.ownerId !== api.ownerId || current.current.selected !== selected || generation !== resetGenerations.current[key]) return;
      if (result.before_seq) historyFloor.current[key] = Math.min(historyFloor.current[key] ?? Infinity, result.before_seq);
      if (chatLoads.current.get(key)?.signal === signal || sequence !== chatSequence.current || detailCurrent.current?.chat.id === selected && result.revision < detailCurrent.current.revision) {
        // A live refresh overlaps this page. Reauthorize to the requested cursor; never merge stale bodies.
        await loadChat(selected);
        return;
      }
      setDetail(current => current?.chat.id === result.chat.id ? { ...current, has_more: result.has_more, before_seq: result.before_seq, messages: [...result.messages, ...current.messages], questions: [...(result.questions || []), ...(current.questions || [])] } : current);
      requestAnimationFrame(() => { if (messageArea.current) messageArea.current.scrollTop += messageArea.current.scrollHeight - previousHeight; });
    } catch (error) { onError(error); }
    finally { if (!signal.aborted) setLoadingEarlier(false); }
  }
  function send() {
    if (!canSend || !activeChat) return;
    const body = draft.trim(); const chatId = selected; const key = requestKey();
    const files = attachmentDrafts.items.map(item => item.attachment!);
    const quoted = replyTo ? { id: replyTo.id, author_name: actors.find(actor => actor.id === replyTo.author_id)?.name || '', body: replyTo.body || replyTo.attachments?.map(item => item.name).join(' · ') || t('attachments.message'), unavailable: replyTo.state !== 'published' } : undefined;
    const payload = { ...(activeChat.chat.kind === 'internal' ? { recipient_actor_ids: selectedRecipients } : {}), body, attachment_ids: files.map(item => item.id), client_request_key: key, reply_to_message_id: replyTo?.id, reference_message_id: reference?.message_id };
    const message: Message = { id: key, chat_id: chatId, seq: 0, author_id: actualOwnerId, kind: 'human', body, attachments: files, reply_to: quoted, message_reference: reference, created_at: new Date().toISOString(), read_by: [], state: 'published' };
    setError('');
    localSends.submit(chatId, payload, message);
    if (reference) setReferenceDraft(null);
    setDrafts(previous => ({ ...previous, [draftKey]: '' }));
    attachmentDrafts.clear(attachmentDrafts.items.map(item => item.localId));
    setReplyTo(null); scrollBottom.current = true; input.current?.focus();
  }
  function cancel(request: ReplyRequest) { void withBusy(request.id, async () => { await api.post(`/reply-requests/${request.id}/cancel`); await loadChat(selected); }); }
  function retry(request: ReplyRequest) { void withBusy(request.id, async () => { const result = await api.post<ReplyRequest>(`/reply-requests/${request.id}/retry`, { client_request_key: requestKey() }); localSends.followRetry(request.id, result); await loadChat(selected); }); }
  function toggleReminder(reminder: Reminder) {
    const key = `${api.ownerId}:${reminder.chat_id}`; const generation = resetGenerations.current[key];
    void withBusy(reminder.id, async () => {
      const result = await api.patch<Reminder>(`/reminders/${reminder.id}`, { done: !reminder.done });
      if (currentApi.current !== api || generation !== resetGenerations.current[key]) return;
      bootstrapSequence.current++;
      if (bootstrapLoad.current?.api === api) bootstrapLoad.current.pending = true;
      setBootstrap(previous => previous ? { ...previous, reminders: previous.reminders.map(item => item.id === result.id ? result : item) } : previous);
    });
  }
  // Quiet system work stays in Activity/Scheduled tasks, separate from input status.
  const requests = activeChat && activeChat.chat.kind !== 'group' ? (activeChat.requests || []).filter(request => !request.quiet_background && !(request.source === 'memory_dream' && request.status === 'failed')) : [];
  const foregroundRequests = requests.filter(request => !isBackgroundPrivateTask(request, activeChat?.chat.kind, activeChat?.messages));
  const privatePending = [
    ...foregroundRequests,
    ...localSends.items.flatMap(item => activeChat?.chat.kind !== 'group' && item.chatId === selected && item.phase === 'sent' && item.request?.chat_id === selected && !requests.some(request => request.id === item.request?.id) ? [{ ...item.request, target_twin_id: item.payload.recipient_actor_ids?.[0] || activeChat?.chat.bot_id || ownTwin?.id }] : []),
  ];
  const hiddenFailures = requests.filter(request => request.status === 'failed' && (isBackgroundPrivateTask(request, activeChat?.chat.kind, activeChat?.messages) || !activeChat?.messages.some(message => message.id === request.source_message_id)));
  const pendingCount = bootstrap?.reminders.filter(reminder => !reminder.done).length || 0;
  if (!authChecked) return <div className="app-loading auth-loading"><span className="spinner" />{t('common.loading')}</div>;
  if (!session) return <LoginScreen api={api} acceptanceFixture={acceptanceFixture} developmentMode={developmentMode} configLoading={configLoading} configError={configError} onLogin={result => { setSession(result); setDevelopmentMode(result.development_mode); setError(''); }} onReload={() => { void checkAuth(); }} />;

  const isMainShell = page === 'chat' || page === 'reminders';
  const showSidebarColumn = isMainShell && (!isNarrow ? sidebarVisible : (page === 'chat' ? !narrowShowingChat : !narrowShowingChat));
  const showMainStage = !isNarrow || !isMainShell || narrowShowingChat;
  const openNotificationsPopover = (event: React.MouseEvent<HTMLElement>) => {
    setNotifAnchor(utilityClusterRef.current?.getBoundingClientRect() || event.currentTarget.getBoundingClientRect());
    setNotificationSettingsOpen(true);
    setAppMenuOpen(false);
    setAccountOpen(false);
  };
  const chatSubtitle = !connected
    ? null
    : activeChat?.chat.kind === 'bot'
      ? (activeBot?.assignment || t('bots.conversation'))
      : activeChat?.chat.kind === 'internal'
        ? `${roomMembers.map(actor => actor.name).join(' · ') || t('bots.roomConversation')}`
        : activeChat?.chat.kind === 'private'
          ? t('chat.private')
          : activeChat
            ? t('chat.peopleTwins', { count: activeChat.chat.member_owner_ids.length })
            : '';

  return (
    <div className={`app-shell ${acceptanceFixture ? 'acceptance-fixture' : ''} ${isNarrow ? 'is-narrow' : isMid ? 'is-mid' : 'is-wide'}`}>
      {acceptanceFixture && <FixtureBanner />}
      <div className="workspace">
        {isMainShell && (
          <>
            <aside
              className={`sidebar duet-glass ${showSidebarColumn ? '' : 'collapsed'}`}
              data-duet-refract="0.55"
              inert={!showSidebarColumn}
              aria-hidden={!showSidebarColumn}
              style={{ width: showSidebarColumn ? (isNarrow ? '100%' : paneWidth) : 0 }}
            >
              <div className="sidebar-brand-bar">
                <div className="brand" title={t('app.name')}><BrandMark /></div>
                <div className="purpose-segmented segmented-control" role="tablist" aria-label={t('nav.main')}>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={page === 'chat'}
                    className={page === 'chat' ? 'selected' : ''}
                    onClick={() => { navigate('chat'); if (isNarrow) setNarrowShowingChat(false); }}
                  >
                    {t('nav.chat')}
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={page === 'reminders'}
                    className={page === 'reminders' ? 'selected' : ''}
                    onClick={() => { navigate('reminders'); if (isNarrow) setNarrowShowingChat(true); }}
                  >
                    <span>{t('nav.reminders')}</span>
                    {pendingCount > 0 && <span className="purpose-badge">{pendingCount}</span>}
                  </button>
                </div>
                {!isNarrow && (
                  <IconButton
                    icon="panel-left"
                    size={16}
                    label={t('shell.toggleChats')}
                    onClick={toggleSidebarPane}
                  />
                )}
              </div>

              {page === 'chat' ? (
                <ChatNavigation
                  key={actualOwnerId}
                  ownerId={actualOwnerId}
                  chats={bootstrap?.chats || []}
                  actors={actors}
                  loading={loading}
                  selected={selected}
                  newChat={newChat}
                  onChoose={chooseChat}
                  onNewBot={() => setBotDialog({ mode: 'create' })}
                  onNewRoom={() => setBotDialog({ mode: 'room' })}
                  onEditBot={botId => setBotDialog({ mode: 'edit', botId })}
                  onShareBot={botId => setBotDialog({ mode: 'share', botId })}
                  onNew={() => {
                    if (isNarrow) setNarrowShowingChat(true);
                    setNewChat(true);
                    setFindOpen(false);
                    setSearchOpen(false);
                  }}
                />
              ) : (
                <>
                  <div className="sidebar-tools">
                    <button
                      type="button"
                      className="primary-button sidebar-full-action"
                      onClick={() => setReminderOpen(true)}
                    >
                      <Icon name="plus" size={15} />
                      <span>{t('reminders.new')}</span>
                    </button>
                  </div>
                  <div className="reminder-nav">
                    {[false, true].map(value => (
                      <button
                        type="button"
                        key={String(value)}
                        className={completed === value ? 'selected' : ''}
                        onClick={() => {
                          setCompleted(value);
                          if (isNarrow) setNarrowShowingChat(true);
                        }}
                      >
                        <Icon name={value ? 'check' : 'bell'} size={16} />
                        <span>{t(value ? 'reminders.completed' : 'reminders.pending')}</span>
                        <span className="reminder-nav-count">{bootstrap?.reminders.filter(r => r.done === value).length || 0}</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </aside>
            {!isNarrow && (
              <div
                className="pane-resizer"
                style={{ display: showSidebarColumn ? undefined : 'none' }}
                role="separator"
                aria-orientation="vertical"
                aria-label={t('shell.resizeChats')}
                aria-valuenow={paneWidth}
                aria-valuemin={248}
                aria-valuemax={maxPaneWidth}
                tabIndex={0}
                onPointerDown={event => {
                  sidebarDrag.current = { x: event.clientX, width: paneWidth };
                  event.currentTarget.setPointerCapture(event.pointerId);
                  document.body.classList.add('resizing-pane');
                }}
                onKeyDown={event => {
                  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                    event.preventDefault();
                    setSidebarWidth(previous => Math.min(maxPaneWidth, Math.max(248, Math.min(previous, maxPaneWidth) + (event.key === 'ArrowRight' ? 16 : -16))));
                  }
                }}
              />
            )}
          </>
        )}

        <div className={`workspace-stage ${showMainStage ? '' : 'narrow-list-only'}`}>
          <div className="window-utility-row">
            {isMainShell && !isNarrow && !sidebarVisible && (
              <div className="floating-nav-pill duet-glass" data-duet-refract="0.5">
                <IconButton
                  icon="panel-left"
                  size={16}
                  label={t('shell.toggleChats')}
                  onClick={toggleSidebarPane}
                />
              </div>
            )}
            {isNarrow && page === 'reminders' && narrowShowingChat && (
              <button
                type="button"
                className="floating-nav-pill duet-glass settings-back-button"
                onClick={() => setNarrowShowingChat(false)}
              >
                <Icon name="reply" size={15} />
                <span>{t('nav.reminders')}</span>
              </button>
            )}
            <div className="utility-cluster duet-glass" ref={utilityClusterRef} data-duet-refract="0.55">
              <div className="search-wrap" ref={searchWrap}>
                <div className="global-search">
                  <Icon name="search" size={15} />
                  <input
                    ref={searchInput}
                    placeholder={t('search.placeholder')}
                    aria-label={t('search.placeholder')}
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    onFocus={() => setSearchOpen(true)}
                    onKeyDown={e => { if (e.key === 'Escape') setSearchOpen(false); }}
                  />
                  {query && (
                    <IconButton icon="close" size={13} label={t('common.close')} onClick={() => setQuery('')} />
                  )}
                </div>
                {searchOpen && (
                  <div className="search-results duet-glass-overlay">
                    <p>{t('search.hint')}</p>
                    {peopleLoading && !people.length ? (
                      <div className="search-empty">{t('common.loading')}</div>
                    ) : people.filter(a => a.kind === 'human' && a.id !== actualOwnerId).length ? (
                      people.filter(a => a.kind === 'human' && a.id !== actualOwnerId).map(person => (
                        <div className="person-search-row" key={person.id}>
                          <button
                            className="person-search-chat"
                            onClick={() => { setPeopleModal({ add: false, person }); setSearchOpen(false); }}
                          >
                            <Avatar actor={person} size={32} />
                            <span>{person.name}</span>
                            <Icon name="arrow" size={15} />
                          </button>
                          <IconButton
                            icon="info"
                            label={t('contacts.personDetailsFor', { name: person.name })}
                            size={15}
                            onClick={event => {
                              openPersonCard(person, event.currentTarget.getBoundingClientRect());
                              setSearchOpen(false);
                            }}
                          />
                        </div>
                      ))
                    ) : (
                      <div className="search-empty">{t('search.empty')}</div>
                    )}
                  </div>
                )}
              </div>

              <span className="utility-divider" aria-hidden="true" />

              <div className="app-menu-wrap" data-floating-menu>
                <IconButton
                  icon="more"
                  size={16}
                  label={t('shell.appMenu')}
                  active={appMenuOpen}
                  onClick={() => { setAppMenuOpen(previous => !previous); setAccountOpen(false); setChatMoreOpen(false); }}
                />
                {appMenuOpen && (
                  <div className="fluent-menu duet-glass-overlay app-options-menu" role="menu">
                    <button type="button" role="menuitem" onClick={openNotificationsPopover}>
                      <Icon name="bell" size={16} />
                      <span>{t('notifications.title')}</span>
                    </button>
                    <button type="button" role="menuitem" onClick={() => { setShortcutsOpen(true); setAppMenuOpen(false); }}>
                      <Icon name="info" size={16} />
                      <span>{t('shell.keyboard')}</span>
                      <kbd className="menu-shortcut">⌘/</kbd>
                    </button>
                  </div>
                )}
              </div>

              <div className="account-menu-wrap" data-floating-menu>
                <button
                  type="button"
                  className="account-button"
                  title={t('auth.account')}
                  aria-label={t('auth.account')}
                  aria-expanded={accountOpen}
                  onClick={() => { setAccountOpen(p => !p); setAppMenuOpen(false); setChatMoreOpen(false); }}
                >
                  <span className="account-name">{owner?.name}</span>
                  <Avatar actor={owner} size={28} />
                </button>
                {accountOpen && (
                  <div className="account-menu duet-glass-overlay" role="menu">
                    <div className="account-menu-header">
                      <Avatar actor={owner} size={34} />
                      <div>
                        <strong>{owner?.name}</strong>
                        {bootstrap?.development_mode && <small>{t('auth.development')}</small>}
                      </div>
                    </div>
                    <div className="menu-separator" />
                    <button type="button" role="menuitem" onClick={() => navigate('my-twin')}>
                      <Icon name="spark" size={16} />
                      <span>{t('twin.title')}</span>
                    </button>
                    <button type="button" role="menuitem" onClick={() => { navigate('my-twin'); chooseTwinTab('connections'); setAccountOpen(false); }}>
                      <Icon name="people" size={16} />
                      <span>Trusted Duets</span>
                    </button>
                    {providerConfigAllowed && providerConfigAvailable && (
                      <button type="button" role="menuitem" onClick={() => navigate('provider-config')}>
                        <Icon name="settings" size={16} />
                        <span>{t('providerConfig.title')}</span>
                      </button>
                    )}
                    <div className="menu-separator" />
                    <button type="button" role="menuitem" onClick={openNotificationsPopover}>
                      <Icon name="bell" size={16} />
                      <span>{t('notifications.title')}</span>
                    </button>
                    <button type="button" role="menuitem" onClick={() => { setShortcutsOpen(true); setAccountOpen(false); }}>
                      <Icon name="info" size={16} />
                      <span>{t('shell.keyboard')}</span>
                      <kbd className="menu-shortcut">⌘/</kbd>
                    </button>
                    <div className="menu-separator" />
                    <button
                      type="button"
                      role="menuitem"
                      onClick={async () => {
                        try {
                          stopNotifications();
                          localSends.clear();
                          await api.post('/presence', { device_id: deviceId.current, focused_chat_id: null, visible: false }).catch(() => {});
                          await api.post(`/auth/logout?device_id=${encodeURIComponent(deviceId.current)}`);
                          setAccountOpen(false);
                          setNotificationSettingsOpen(false);
                          navigate('chat');
                          setSession(null);
                          setSearchOpen(false);
                          setPeopleModal(null);
                          setReminderOpen(false);
                          setReplyTo(null);
                          setDrafts({});
                          setError('');
                        } catch (e) {
                          onError(e);
                        }
                      }}
                    >
                      <span>{t('auth.signOut')}</span>
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>

          <DesktopNotificationPrompt controller={desktopNotifications} />

          <div className="stage-columns">
            {page === 'bot-share' ? (
              <BotInstallPage key={`${ownerId}:${shareToken}`} api={api} token={shareToken} onBack={() => navigate('chat')} onInstalled={bot => { if (currentApi.current !== api) return; mergeBot(bot); void openDestination({ chatId: bot.chat_id }); }} />
            ) : page === 'my-twin' ? (
              <MyTwinPage key={ownerId} api={api} account={owner} actor={ownTwin} tab={twinTab} revision={twinVersion} credentials={credentials} requests={credentialRequests} credentialsConfigured={credentialsConfigured} credentialsLoading={credentialsLoading} credentialsError={credentialsError} reset={twinReset} memoryRevision={memoryResetRevision} scheduledTasksRevision={scheduledTasksRevision} onTab={chooseTwinTab} onActor={mergeActor} onBack={() => navigate('chat')} onCredentialsChange={() => { void refreshCredentials(); }} />
            ) : page === 'provider-config' ? (
              <ProviderConfigPage key={ownerId} allowed={providerConfigAllowed} onBack={() => navigate('chat')} />
            ) : page === 'reminders' ? (
              <RemindersPage reminders={bootstrap?.reminders || []} completed={completed} onNew={() => setReminderOpen(true)} onChat={chooseChat} onToggle={toggleReminder} />
            ) : newChat ? (
              <main className="chat-main new-chat-main">
                {isNarrow && (
                  <header className="chat-header duet-glass" data-duet-refract="0.55">
                    <IconButton icon="reply" size={16} label={t('shell.backToChats')} onClick={() => { setNewChat(false); setNarrowShowingChat(false); }} />
                    <div className="chat-header-copy">
                      <h2>{t('shell.newMessage')}</h2>
                    </div>
                  </header>
                )}
                <PeoplePicker inline api={api} ownerId={actualOwnerId} onClose={() => { setNewChat(false); if (isNarrow) setNarrowShowingChat(false); }} onDone={chat => { setNewChat(false); chooseChat(chat.id); void loadBootstrap().catch(onError); if (chat.id === selected) void loadChat().catch(onError); }} onError={onError} />
                <div className="new-conversation-empty">
                  <img className="conversation-illustration" src={COMPOSE_ILLUSTRATION} width={180} height={180} alt="" aria-hidden="true" />
                  <h2>{t('shell.newHeading')}</h2>
                  <p>{t('shell.newDetail')}</p>
                </div>
                <div className="composer-area new-chat-composer" aria-label={t('chat.message')}>
                  <div className="composer duet-glass is-disabled" data-duet-refract="0.55">
                    <textarea disabled rows={1} aria-label={t('chat.message')} placeholder={t('shell.typeMessage')} />
                    <div className="composer-tools">
                      <IconButton icon="smile" label={t('chat.emoji')} disabled />
                      <IconButton icon="attachment" label={t('workspace.upload')} disabled />
                      <IconButton icon="image" label={t('attachments.media')} disabled />
                      <span className="tool-divider" />
                      <button type="button" className="icon-button send-button" disabled title={t('chat.send')} aria-label={t('chat.send')}>
                        <Icon name="send" size={17} />
                      </button>
                    </div>
                  </div>
                </div>
              </main>
            ) : activeChat ? (
              <>
                <main
                  className={`chat-main ${findOpen || activeChat.settings.sidebar_open ? 'with-side-panel' : ''} ${dragActive ? 'file-drag-active' : ''}`}
                  onLoadCapture={() => { if (scrollBottom.current && messageArea.current) messageArea.current.scrollTop = messageArea.current.scrollHeight; markVisibleRead(); }}
                  onDragEnter={event => { if (Array.from(event.dataTransfer.types).includes('Files')) { event.preventDefault(); setDragActive(true); } }}
                  onDragOver={event => { if (Array.from(event.dataTransfer.types).includes('Files')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } }}
                  onDragLeave={event => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragActive(false); }}
                  onDrop={event => { setDragActive(false); if (event.dataTransfer.files.length) { event.preventDefault(); attachmentDrafts.ingest(Array.from(event.dataTransfer.files)); input.current?.focus(); } }}
                >
                  <input ref={fileInput} type="file" hidden multiple onChange={event => { attachmentDrafts.ingest(Array.from(event.target.files || [])); event.target.value = ''; }} />
                  <input ref={imageInput} type="file" hidden multiple accept="image/*" onChange={event => { attachmentDrafts.ingest(Array.from(event.target.files || [])); event.target.value = ''; }} />
                  {dragActive && (
                    <div className="attachment-drop-overlay">
                      <Icon name="attachment" size={44} />
                      <strong>{t('attachments.drop')}</strong>
                      <span>{t('attachments.dropDetail')}</span>
                    </div>
                  )}

                  <div className="duet-scroll-edge-top" aria-hidden="true" />
                  <div className="duet-scroll-edge-bottom" aria-hidden="true" />

                  <header className="chat-header duet-glass" data-duet-refract="0.55">
                    {isNarrow && (
                      <IconButton
                        icon="reply"
                        size={16}
                        label={t('shell.backToChats')}
                        onClick={() => { setNarrowShowingChat(false); setFindOpen(false); }}
                      />
                    )}
                    <ChatAvatar chat={activeChat.chat} actors={actors} ownerId={actualOwnerId} size={32} />
                    <div className="chat-header-copy">
                      <div className="chat-header-title">
                        <h2>{activeTitle}</h2>
                        {directPeer && (
                          <IconButton
                            icon="info"
                            label={t('contacts.personDetailsFor', { name: directPeer.name })}
                            size={14}
                            onClick={event => openPersonCard(directPeer, event.currentTarget.getBoundingClientRect())}
                          />
                        )}
                        {canRenameChat && (
                          <IconButton
                            icon="edit"
                            label={t('chat.rename')}
                            size={14}
                            onClick={() => setRenameChat(activeChat.chat)}
                          />
                        )}
                      </div>
                      {!connected ? (
                        <p className="chat-header-status" role="status">
                          <i className="status-dot connecting" aria-hidden="true" />
                          <span>{t(hasConnected ? 'connection.reconnecting' : 'connection.connecting')}</span>
                          <button type="button" className="inline-refresh-link" onClick={() => { void refreshAll(); }}>
                            {t('common.refresh')}
                          </button>
                        </p>
                      ) : (
                        chatSubtitle && <p>{chatSubtitle}</p>
                      )}
                    </div>

                    <div className="header-actions">
                      {activeChat.chat.kind === 'group' && (
                        <button
                          type="button"
                          className="member-button"
                          onClick={() => setPeopleModal({ add: true })}
                          title={t('chat.addMembers')}
                        >
                          <Icon name="people" size={16} />
                          <span>{activeChat.members.filter(member => member.kind === 'human').length}</span>
                        </button>
                      )}
                      <IconButton
                        icon="search"
                        size={16}
                        label={t('shell.findInChat')}
                        active={findOpen}
                        onClick={toggleFindPane}
                      />
                      <IconButton
                        icon="sidebar"
                        size={16}
                        label={t('chat.toggleSidebar')}
                        active={activeChat.settings.sidebar_open && !findOpen}
                        onClick={toggleInspectorPane}
                      />
                      <div className="chat-more-wrap" data-floating-menu>
                        <IconButton
                          icon="more"
                          size={16}
                          label={t('shell.conversationMore')}
                          active={chatMoreOpen}
                          onClick={() => { setChatMoreOpen(previous => !previous); setAccountOpen(false); setAppMenuOpen(false); }}
                        />
                        {chatMoreOpen && (
                          <div className="fluent-menu duet-glass-overlay chat-more-menu" role="menu">
                            {activeChat.chat.kind === 'private' && (
                              <>
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'dispatch' }); }}>
                                  <Icon name="arrow" size={15} />
                                  <span>{t('bots.dispatch')}</span>
                                </button>
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'message' }); }}>
                                  <Icon name="chat" size={15} />
                                  <span>{t('bots.message')}</span>
                                </button>
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'room' }); }}>
                                  <Icon name="people" size={15} />
                                  <span>{t('bots.newRoom')}</span>
                                </button>
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'inspect' }); }}>
                                  <Icon name="search" size={15} />
                                  <span>{t('bots.inspect')}</span>
                                </button>
                              </>
                            )}
                            {activeBot && (
                              <>
                                {activeBot.assignment && (
                                  <p className="menu-context-note">{activeBot.assignment}</p>
                                )}
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'edit', botId: activeBot.id }); }}>
                                  <Icon name="edit" size={15} />
                                  <span>{t('bots.edit')}</span>
                                </button>
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'skills', botId: activeBot.id }); }}>
                                  <Icon name="star" size={15} />
                                  <span>{t('botSkills.manage')}</span>
                                </button>
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'share', botId: activeBot.id }); }}>
                                  <Icon name="arrow" size={15} />
                                  <span>{t('botShare.title')}</span>
                                </button>
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'message', botId: activeBot.id }); }}>
                                  <Icon name="chat" size={15} />
                                  <span>{t('bots.message')}</span>
                                </button>
                                {privateChat && (
                                  <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); chooseChat(privateChat.id); }}>
                                    <Icon name="spark" size={15} />
                                    <span>{t('bots.openMain')}</span>
                                  </button>
                                )}
                              </>
                            )}
                            {activeChat.chat.kind === 'internal' && (
                              <>
                                {!!roomMembers.length && (
                                  <p className="menu-context-note">{t('bots.roomMembers')}: {roomMembers.map(actor => actor.name).join(', ')}</p>
                                )}
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'message' }); }}>
                                  <Icon name="chat" size={15} />
                                  <span>{t('bots.message')}</span>
                                </button>
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setBotDialog({ mode: 'room' }); }}>
                                  <Icon name="people" size={15} />
                                  <span>{t('bots.newRoom')}</span>
                                </button>
                                {privateChat && (
                                  <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); chooseChat(privateChat.id); }}>
                                    <Icon name="spark" size={15} />
                                    <span>{t('bots.openMain')}</span>
                                  </button>
                                )}
                              </>
                            )}
                            {activeChat.chat.kind === 'group' && (
                              <>
                                {directPeer && (
                                  <button
                                    type="button"
                                    role="menuitem"
                                    onClick={event => {
                                      setChatMoreOpen(false);
                                      openPersonCard(directPeer, event.currentTarget.getBoundingClientRect());
                                    }}
                                  >
                                    <Icon name="info" size={15} />
                                    <span>{t('contacts.personDetails')}</span>
                                  </button>
                                )}
                                <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setPeopleModal({ add: true }); }}>
                                  <Icon name="people" size={15} />
                                  <span>{t('chat.addMembers')}</span>
                                </button>
                                {canRenameChat && (
                                  <button type="button" role="menuitem" onClick={() => { setChatMoreOpen(false); setRenameChat(activeChat.chat); }}>
                                    <Icon name="edit" size={15} />
                                    <span>{t('chat.rename')}</span>
                                  </button>
                                )}
                              </>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </header>

                  <div
                    className={`message-area ${!activeChat.has_more && !activeChat.messages.length && !localSends.items.some(item => item.chatId === selected) ? 'empty' : ''}`}
                    ref={messageArea}
                    onScroll={() => {
                      if (messageArea.current) scrollBottom.current = messageArea.current.scrollHeight - messageArea.current.scrollTop - messageArea.current.clientHeight < 100;
                      markVisibleRead();
                    }}
                  >
                    {!bootstrap?.model_configured && (
                      <div className="model-notice">
                        <Icon name="info" size={15} />
                        <span>{t('config.unconfigured')}</span>
                      </div>
                    )}
                    {readFailures[draftKey] && (
                      <div className="read-error" role="alert">
                        <Icon name="info" size={15} />
                        <span>{t('chat.readFailed')}</span>
                        <button className="text-button" disabled={readFailures[draftKey] === 'retrying'} onClick={() => markVisibleRead(true)}>
                          {t('common.retry')}
                        </button>
                      </div>
                    )}
                    {activeChat.has_more && (
                      <button className="text-button load-earlier-button" disabled={loadingEarlier} onClick={() => { void loadEarlier(); }}>
                        {t('shell.loadEarlier')}
                      </button>
                    )}
                    {!activeChat.has_more && !activeChat.messages.length && !localSends.items.some(item => item.chatId === selected) ? (
                      <div className="chat-empty-state">
                        <ChatAvatar chat={activeChat.chat} actors={actors} ownerId={actualOwnerId} size={76} />
                        <h3>{t(activeChat.chat.kind === 'group' && activeChat.chat.member_owner_ids.length >= 3 ? 'chat.emptyGroupTitle' : 'chat.empty')}</h3>
                        <p>{t(activeChat.chat.kind === 'bot' ? 'bots.emptyChat' : activeChat.chat.kind === 'internal' ? 'bots.roomEmpty' : activeChat.chat.kind === 'private' ? 'chat.emptyPrivate' : 'chat.emptyGroup')}</p>
                      </div>
                    ) : (
                      activeChat.messages
                        .filter(m => m.state === 'published' && (activeChat.chat.kind !== 'group' || m.kind === 'human' || m.kind === 'system'))
                        .sort((a, b) => a.seq - b.seq)
                        .map((message, index, all) => {
                          const actor = actors.find(a => a.id === message.author_id);
                          const own = message.author_id === actualOwnerId;
                          const showDate = index === 0 || new Date(message.created_at).toDateString() !== new Date(all[index - 1].created_at).toDateString();
                          return (
                            <div key={message.id} className={messageHighlight === message.id ? 'message-highlight' : ''}>
                              {showDate && (
                                <div className="date-divider">
                                  {formatDate(new Date(message.created_at), { month: 'long', day: 'numeric' })}
                                </div>
                              )}
                              <MessageItem
                                onPerson={openPersonCard}
                                message={message}
                                referenceChatName={referenceChatName(message.message_reference)}
                                question={activeChat.questions?.find(question => question.message_id === message.id)}
                                api={api}
                                onQuestionChanged={() => loadChat(selected)}
                                onOrigin={chooseChat}
                                actor={actor}
                                own={own}
                                actors={actors}
                                ownerId={actualOwnerId}
                                requests={foregroundRequests}
                                showQueued={false}
                                groupChat={activeChat.chat.kind === 'group'}
                                pending={busyKeys}
                                reactionPreferences={reactionPreferences.value}
                                onReactionPreferences={reactionPreferences.save}
                                onReact={reactToMessage}
                                onCancel={cancel}
                                onRetry={retry}
                                onReference={referenceInDuet}
                                onReply={m => { if (reference) setReferenceDraft(null); setReplyTo(m); input.current?.focus(); }}
                                onTrack={m => {
                                  setTrackingSeed(m.id);
                                  if (isMid) setSidebarVisible(false);
                                  void setSettings({ sidebar_open: true, sidebar_tab: 'tracking' });
                                }}
                              />
                            </div>
                          );
                        })
                    )}
                    {localSends.items.filter(item => item.chatId === selected).map(entry => (
                      <LocalSendItem key={entry.key} entry={entry} referenceChatName={referenceChatName(entry.message.message_reference)} actor={owner} onRetry={() => localSends.retry(entry)} />
                    ))}
                  </div>

                  <div className="composer-area">
                    {activeChat.chat.kind === 'private' && actualOwnerId === api.ownerId && (
                      <TwinOnboarding key={`${api.ownerId}:${selected}:${privateResetVersions[selected] || 0}`} api={api} chat={activeChat.chat} revision={activeChat.revision} onChanged={() => { void loadChat(selected).catch(onError); }} />
                    )}
                    {activeChat.chat.kind === 'private' && (
                      <CredentialRequestCards key={`credentials:${actualOwnerId}:${selected}`} compact api={api} requests={credentialRequests.filter(request => request.chat_id === selected)} credentials={credentials} onChange={() => { void refreshCredentials(); }} />
                    )}
                    <ApprovalsDrawer key={`${actualOwnerId}:${selected}`} api={api} actors={actors} onChange={refreshAll} approvals={activeChat.approvals.filter(a => a.approver_id === actualOwnerId)} onDecision={async (id, decision) => { try { await api.post(`/approvals/${encodeURIComponent(id)}/decision`, { decision }); await refreshAll(); } catch (e) { onError(e); if (e instanceof ApiError && [404, 409].includes(e.status)) await refreshAll(); } }} />
                    {(connected || !hasConnected) && personalAgents.map(actor => (
                      <PrivateTaskStatuses key={actor.id} requests={privatePending.filter(request => request.target_twin_id === actor.id)} actor={actor} busy={busyKeys} onCancel={ids => { void withBusy(ids, async () => { await Promise.all(ids.map(id => api.post(`/reply-requests/${id}/cancel`))); await loadChat(selected); }); }} />
                    ))}
                    {hiddenFailures.map(request => (
                      <FailureNotice key={JSON.stringify([actualOwnerId, request.chat_id, request.id])} ownerId={actualOwnerId} request={request} name={actors.find(a => a.id === request.target_twin_id)?.name} busy={busyKeys.has(request.id)} onRetry={retry} label={`${privateTaskLabel(request, activeChat.messages, activeChat.chat.kind)} · ${t('chat.failed')}`} composer />
                    ))}
                    {reference && (
                      <div className="composer-reply">
                        <MessageReferenceQuote reference={reference} chatName={referenceChatName(reference)} />
                        <IconButton icon="close" size={14} label={t('chat.removeReference')} onClick={() => setReferenceDraft(null)} />
                      </div>
                    )}
                    {replyTo && (
                      <div className="composer-reply">
                        <SourceQuote name={actors.find(a => a.id === replyTo.author_id)?.name || ''} body={replyTo.body || replyTo.attachments?.map(attachment => attachment.name).join(' · ') || t('attachments.message')} />
                        <IconButton icon="close" size={14} label={t('common.cancel')} onClick={() => setReplyTo(null)} />
                      </div>
                    )}
                    {activeChat.chat.kind === 'internal' && (
                      <RoomRecipients members={roomMembers} selected={selectedRecipients} onChange={ids => setRoomRecipients(previous => ({ ...previous, [selected]: ids }))} />
                    )}
                    <DraftAttachmentList items={attachmentDrafts.items} error={attachmentDrafts.error} onRemove={attachmentDrafts.remove} onRetry={attachmentDrafts.retry} onDismissError={attachmentDrafts.dismissError} />
                    <div className={`composer duet-glass ${expandedComposer ? 'expanded' : ''}`} data-duet-refract="0.6">
                      <textarea
                        ref={input}
                        rows={1}
                        aria-label={t('chat.message')}
                        placeholder={t('shell.typeMessage')}
                        value={draft}
                        onChange={e => setDrafts(p => ({ ...p, [draftKey]: e.target.value }))}
                        onPaste={event => {
                          const files = Array.from(event.clipboardData.items).filter(item => item.kind === 'file').map(item => item.getAsFile()).filter((file): file is File => !!file);
                          if (files.length) {
                            if (!event.clipboardData.getData('text/plain')) event.preventDefault();
                            attachmentDrafts.ingest(files);
                          }
                        }}
                        onKeyDown={event => {
                          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && (!expandedComposer || event.ctrlKey || event.metaKey)) {
                            event.preventDefault();
                            void send();
                          }
                        }}
                      />
                      <div className="composer-tools">
                        <div className="composer-menu-wrap" data-floating-menu>
                          <IconButton icon="smile" size={17} label={t('chat.emoji')} active={composerMenu === 'emoji'} onClick={() => setComposerMenu(previous => previous === 'emoji' ? null : 'emoji')} />
                          {composerMenu === 'emoji' && (
                            <div className="emoji-picker duet-glass-overlay" role="dialog" aria-label={t('chat.emoji')}>
                              <header>
                                <strong>{t('chat.emoji')}</strong>
                                <IconButton icon="close" label={t('common.close')} size={15} onClick={() => setComposerMenu(null)} />
                              </header>
                              <div className="emoji-grid">
                                {['🙂','😀','😄','😁','😅','😂','😊','😍','🥰','😎','🤔','😴','😢','😭','😮','😬','👍','👎','👏','🙌','👋','🙏','💪','✌️','❤️','💜','💙','💛','🎉','✨','🔥','💯','✅','👀','💡','📌','🌟','☕','🌈','🚀','🐱','🐶','🌸','🌻','🍎','🍕','🎂','⚽'].map(emoji => (
                                  <button key={emoji} aria-label={emoji} onClick={() => { insertEmoji(emoji); }}>{emoji}</button>
                                ))}
                              </div>
                            </div>
                          )}
                        </div>
                        <div className="composer-menu-wrap" data-floating-menu>
                          <IconButton icon="attachment" size={17} label={`${t('workspace.upload')} (${navigator.platform.includes('Mac') ? '⌥⌘O' : 'Alt+Shift+O'})`} active={composerMenu === 'attachments'} onClick={() => setComposerMenu(previous => previous === 'attachments' ? null : 'attachments')} />
                          {composerMenu === 'attachments' && (
                            <div className="fluent-menu duet-glass-overlay composer-popover" role="menu">
                              <button type="button" role="menuitem" onClick={() => { setComposerMenu(null); fileInput.current?.click(); }}>
                                <Icon name="file" size={16} />
                                <span>{t('attachments.uploadDevice')}</span>
                              </button>
                            </div>
                          )}
                        </div>
                        <IconButton icon="image" size={17} label={t('attachments.media')} onClick={() => imageInput.current?.click()} />
                        <div className="composer-menu-wrap" data-floating-menu>
                          <IconButton icon="plus" size={17} label={t('shell.moreTools')} active={composerMenu === 'more'} onClick={() => setComposerMenu(previous => previous === 'more' ? null : 'more')} />
                          {composerMenu === 'more' && (
                            <div className="fluent-menu duet-glass-overlay composer-popover" role="menu">
                              <button type="button" role="menuitem" onClick={() => { setExpandedComposer(previous => !previous); setComposerMenu(null); input.current?.focus(); }}>
                                <Icon name="edit" size={16} />
                                <span>{t(expandedComposer ? 'attachments.collapse' : 'attachments.expand')}</span>
                              </button>
                              <button type="button" role="menuitem" onClick={() => { if (isMid) setSidebarVisible(false); showWorkspace(); }}>
                                <Icon name="folder" size={16} />
                                <span>{t(activeChat.chat.kind === 'group' ? 'sidebar.sharedFiles' : 'sidebar.workspace')}</span>
                              </button>
                              <button type="button" role="menuitem" onClick={() => { setComposerMenu(null); setFindOpen(false); if (isMid) setSidebarVisible(false); void setSettings({ sidebar_open: true, sidebar_tab: 'tracking' }); }}>
                                <Icon name="check" size={16} />
                                <span>{t('sidebar.tracking')}</span>
                              </button>
                              <button type="button" role="menuitem" onClick={() => { setComposerMenu(null); setReminderOpen(true); }}>
                                <Icon name="bell" size={16} />
                                <span>{t('reminders.new')}</span>
                              </button>
                            </div>
                          )}
                        </div>
                        <span className="tool-divider" />
                        <button
                          type="button"
                          className="icon-button send-button"
                          disabled={!canSend}
                          title={`${t('chat.send')}${expandedComposer ? ` (${navigator.platform.includes('Mac') ? '⌘' : 'Ctrl+'}Enter)` : ''}`}
                          aria-label={t('chat.send')}
                          onClick={() => { void send(); }}
                        >
                          <Icon name="send" size={17} />
                        </button>
                      </div>
                    </div>
                  </div>
                </main>

                {findOpen ? (
                  <ChatSearchPanel
                    messages={activeChat.messages}
                    actors={actors}
                    onClose={() => {
                      setFindOpen(false);
                      requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('.header-actions > .icon-button')?.focus());
                    }}
                    onJump={jumpToMessage}
                  />
                ) : activeChat.settings.sidebar_open && (
                  <DetailSidebar
                    key={`${actualOwnerId}:${selected}:${privateResetVersions[selected] || 0}`}
                    api={api}
                    detail={activeChat}
                    duetTarget={duetTarget}
                    actors={actors}
                    version={sidebarSnapshot?.chatId === selected ? sidebarSnapshot.revision : activeChat.revision}
                    trackingVersion={trackingVersion}
                    ownerId={actualOwnerId}
                    onSettings={patch => { void setSettings(patch); }}
                    onError={onError}
                    onChange={() => { void refreshAll(); }}
                    createFrom={trackingSeed}
                    onClearCreate={() => setTrackingSeed(null)}
                  />
                )}
              </>
            ) : (
              <main className="chat-main">
                <div className="app-loading">
                  {loading || chatLoading ? (
                    <><span className="spinner" />{t('common.loading')}</>
                  ) : (
                    <>
                      <Icon name="chat" size={28} />
                      <p>{error || t('chat.noChats')}</p>
                      <button className="secondary-button" onClick={() => { setError(''); void loadBootstrap().catch(onError); }}>
                        {t('connection.reload')}
                      </button>
                    </>
                  )}
                </div>
              </main>
            )}
          </div>
        </div>
      </div>

      {botDialog?.mode === 'create' && <BotEditorDialog api={api} onClose={() => setBotDialog(null)} onSaved={bot => { mergeBot(bot); void openDestination({ chatId: bot.chat_id }); }} />}
      {botDialog?.mode === 'edit' && <BotEditorDialog key={botDialog.botId} api={api} bot={bots.find(bot => bot.id === botDialog.botId)} onClose={() => setBotDialog(null)} onSaved={mergeBot} onShare={() => setBotDialog({ mode: 'share', botId: botDialog.botId })} onSkills={() => setBotDialog({ mode: 'skills', botId: botDialog.botId })} />}
      {botDialog?.mode === 'share' && bots.find(bot => bot.id === botDialog.botId) && <BotShareDialog key={`${ownerId}:${botDialog.botId}`} api={api} bot={bots.find(bot => bot.id === botDialog.botId)!} onClose={() => setBotDialog(null)} />}
      {botDialog?.mode === 'skills' && bots.find(bot => bot.id === botDialog.botId) && <BotSkillsDialog key={`${ownerId}:${botDialog.botId}`} api={api} bot={bots.find(bot => bot.id === botDialog.botId)!} onClose={() => setBotDialog(null)} />}
      {botDialog?.mode === 'dispatch' && <BotDispatchDialog api={api} bots={bots} onClose={() => setBotDialog(null)} onSent={internalMessageSent} />}
      {botDialog?.mode === 'message' && <AgentMessageDialog api={api} agents={personalAgents} initialSender={botDialog.botId || ownTwin!.id} onClose={() => setBotDialog(null)} onSent={internalMessageSent} />}
      {botDialog?.mode === 'room' && <BotRoomDialog api={api} bots={bots} mainDuet={ownTwin} onClose={() => setBotDialog(null)} onCreated={chat => { setBootstrap(previous => previous && { ...previous, chats: [...previous.chats.filter(item => item.id !== chat.id), chat] }); void openDestination({ chatId: chat.id }); }} />}
      {botDialog?.mode === 'inspect' && privateChat && <BotTranscriptDialog api={api} bots={bots} privateChatId={privateChat.id} onClose={() => setBotDialog(null)} onSent={internalMessageSent} />}
      {personCard && <PersonCard key={`${actualOwnerId}:${personCard.actor.id}`} api={api} person={personCard.actor} anchor={personCard.anchor} onClose={() => setPersonCard(null)} />}
      {notificationSettingsOpen && <DesktopNotificationSettings controller={desktopNotifications} anchor={notifAnchor} onClose={() => setNotificationSettingsOpen(false)} />}
      {renameChat && <RenameChatDialog api={api} chat={renameChat} onClose={() => setRenameChat(null)} onSaved={chat => { if (current.current.ownerId !== api.ownerId) return; bootstrapSequence.current++; setBootstrap(previous => previous ? { ...previous, chats: previous.chats.map(item => item.id === chat.id ? chat : item) } : previous); if (current.current.selected === chat.id) { chatSequence.current++; setDetail(previous => previous?.chat.id === chat.id ? { ...previous, chat } : previous); void loadChat(chat.id).catch(onError); } }} />}
      {shortcutsOpen && <Modal title={t('shell.keyboard')} onClose={() => setShortcutsOpen(false)}><p className="keyboard-shortcuts">{t('shell.keyboardDetail')}</p></Modal>}
      {error && <div className="error-toast duet-glass-overlay" role="alert"><Icon name="info" size={16} /><span>{error}</span><IconButton icon="close" size={15} label={t('common.dismiss')} onClick={() => setError('')} /></div>}
      {peopleModal && <PeopleDialog api={api} ownerId={actualOwnerId} chat={peopleModal.add && activeChat?.chat.kind === 'group' ? activeChat.chat : undefined} initialPerson={peopleModal.person} onClose={() => setPeopleModal(null)} onDone={chat => { setPeopleModal(null); chooseChat(chat.id); void loadBootstrap().catch(onError); if (chat.id === selected) void loadChat().catch(onError); }} onError={onError} />}
      {reminderOpen && <ReminderDialog api={api} onClose={() => setReminderOpen(false)} onDone={() => { void loadBootstrap().catch(onError); }} onError={onError} />}
    </div>
  );
}
