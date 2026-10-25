import { DesktopNotificationPrompt, DesktopNotificationSettings, useDesktopNotifications } from './DesktopNotifications';
import { MessageMarkdown } from './MessageMarkdown';
import { FailureNotice } from './FailureNotice';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiClient, ApiError, requestKey } from './api/client';
import { useTwinReset } from './TwinReset';
import { TwinOnboarding } from './TwinOnboarding';
import { AskUserCard } from './AskUserCard';
import { LocalSendItem, useLocalSends } from './LocalSends';
import { MessageReactions, useReactionPreferences } from './MessageReactions';
import type { ReactionPreferences } from './MessageReactions';
import type { TwinResetStatus } from './api/twin';
import type { Actor, Approval, Bootstrap, Chat, ChatDetail, ChatSettings, Message, MessageReaction, ReplyRequest, RoutingFailure, UserQuestion } from './api/client';
import { DetailSidebar } from './DetailSidebar';
import { AutomaticConversations } from './AutomaticConversations';
import { LoginScreen } from './Auth';
import { ProviderConfigPage } from './ProviderConfig';
import { MyTwinPage, TWIN_TABS } from './MyTwin';
import type { TwinTab } from './MyTwin';
import { CredentialRequestCards } from './TwinCredentials';
import { twinApi } from './api/twin';
import type { Credential, CredentialRequest } from './api/twin';
import { getLocalProviderConfig } from './api/provider-config';
import type { AuthSession } from './Auth';
import { PeoplePicker, PeopleDialog, RenameChatDialog, ReminderDialog, RemindersPage, SettingsDialog } from './Dialogs';
import { Avatar, BrandMark, ChatAvatar, chatTitle, FixtureBanner, Icon, IconButton, Modal, useMenuBehavior, dateTime, time } from './ui';
import { t, formatDate } from './i18n';
import { DraftAttachmentList, MessageAttachments, useAttachmentDrafts } from './Attachments';
import { ChatNavigation, ChatSearchPanel } from './TeamsNavigation';
import './styles.css';
import './TeamsOverlays.css';

// Observed Teams new-conversation illustration, served locally rather than hot-linked.
// Source: https://tflteams.public.onecdn.static.microsoft/evergreen-assets/illustrations/webp/256/compose-l-standard-256x256.webp
const COMPOSE_ILLUSTRATION = '/assets/teams-compose.webp';

type Page = 'chat' | 'reminders' | 'provider-config' | 'my-twin';
function routePage(): Page { return location.hash.startsWith('#/my-twin') ? 'my-twin' : location.hash === '#/provider-config' ? 'provider-config' : location.hash === '#reminders' ? 'reminders' : 'chat'; }
function routeTwinTab(): TwinTab { const tab = location.hash.split('/')[2] as TwinTab; return TWIN_TABS.includes(tab) ? tab : 'profile'; }

function ApprovalsDrawer({ approvals, onDecision }: { approvals: Approval[]; onDecision: (id: string, decision: 'approved' | 'rejected') => Promise<void> }) {
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
  return <div className={`approvals-drawer ${open ? 'open' : ''}`}><button className="approval-toggle" aria-expanded={open} onClick={() => setOpen(p => !p)}><Icon name="bell" size={16} /><span>{t('approvals.pending', { count: pending.length })}</span><span className={open ? 'rotate' : ''}><Icon name="chevron" size={15} /></span></button>{open && <><div className="approval-tabs" role="tablist">{pending.map((approval, i) => <button role="tab" aria-selected={approval.id === active.id} key={approval.id} className={approval.id === active.id ? 'selected' : ''} onClick={() => { setSelected(approval.id); setDetails(false); }} title={approval.title}>{t('approvals.tab', { number: i + 1 })}</button>)}</div><div className="approval-card"><h3>{active.title}</h3><p>{active.description}</p>{details && <dl className="detail-fields"><dt>{t('approvals.action')}</dt><dd>{active.title}</dd><dt>{t('approvals.target')}</dt><dd>{active.target}</dd><dt>{t('approvals.impact')}</dt><dd>{active.impact}</dd>{active.action && <><dt>{t('approvals.tool')}</dt><dd><code>{active.action.tool}</code></dd><dt>{t('approvals.arguments')}</dt><dd><pre className="approval-arguments">{JSON.stringify(active.action.arguments, null, 2)}</pre></dd></>}</dl>}<div className="approval-actions"><button className="primary-button" disabled={busy} onClick={async () => { setBusy(true); try { await onDecision(active.id, 'approved'); } finally { setBusy(false); } }}>{t('approvals.approve')}</button><button className="secondary-button" disabled={busy} onClick={async () => { setBusy(true); try { await onDecision(active.id, 'rejected'); } finally { setBusy(false); } }}>{t('approvals.reject')}</button><button className="text-button" onClick={() => setDetails(p => !p)} aria-expanded={details}>{t('common.details')}</button></div><small>{t('approvals.once')}</small></div></>}</div>;
}
function SourceQuote({ name, body, unavailable }: { name: string; body: string; unavailable?: boolean }) { return <div className={`source-quote ${unavailable ? 'unavailable' : ''}`}><strong>{name}</strong><span>{unavailable ? t('chat.quoteUnavailable') : body}</span></div>; }
function MessageItem({ message, question, api, onQuestionChanged, onOrigin, actor, own, actors, ownerId, requests, showQueued, onCancel, onRetry, onRouteRetry, onReply, onTrack, pending, reactionPreferences, onReactionPreferences, onReact }: { message: Message; question?: UserQuestion; api: ApiClient; onQuestionChanged: () => Promise<void>; onOrigin: (chatId: string) => void; actor?: Actor; own: boolean; actors: Actor[]; ownerId: string; requests: ReplyRequest[]; showQueued: boolean; onCancel: (request: ReplyRequest) => void; onRetry: (request: ReplyRequest) => void; onRouteRetry: (failure: RoutingFailure) => void; onReply: (message: Message) => void; onTrack: (message: Message) => void; pending: Set<string>; reactionPreferences: ReactionPreferences; onReactionPreferences: (next: ReactionPreferences) => void; onReact: (message: Message, emoji: string, active: boolean, key: string) => Promise<void> }) {
  const [touchActions, setTouchActions] = useState(false);
  const messageElement = useRef<HTMLElement>(null); const touchHold = useRef<{ timer: ReturnType<typeof setTimeout>; x: number; y: number } | null>(null);
  const clearTouchHold = () => { if (touchHold.current) clearTimeout(touchHold.current.timer); touchHold.current = null; };
  useEffect(() => () => clearTouchHold(), []);
  useEffect(() => { if (!touchActions) return; const outside = (event: PointerEvent) => { if (!messageElement.current?.contains(event.target as Node)) setTouchActions(false); }; document.addEventListener('pointerdown', outside); return () => document.removeEventListener('pointerdown', outside); }, [touchActions]);
  const queue = requests.filter(r => r.source_message_id === message.id && (showQueued && r.status === 'queued' || r.status === 'failed'));
  const readNames = message.read_by.filter(id => id !== ownerId && actors.some(a => a.id === id && a.kind === 'human')).map(id => actors.find(a => a.id === id)!.name);
  const isTwin = message.kind === 'twin' || message.kind === 'reply_for_me';
  return <article data-message-id={message.id} className={`message-row ${own ? 'own' : ''} ${isTwin ? 'twin-message' : ''} ${touchActions ? 'touch-actions' : ''}`} ref={messageElement} tabIndex={0} onPointerDown={event => { if (event.pointerType !== 'touch' || (event.target as Element).closest('button')) return; clearTouchHold(); setTouchActions(false); touchHold.current = { x: event.clientX, y: event.clientY, timer: setTimeout(() => { setTouchActions(true); touchHold.current = null; }, 450) }; }} onPointerMove={event => { if (touchHold.current && Math.hypot(event.clientX - touchHold.current.x, event.clientY - touchHold.current.y) > 10) clearTouchHold(); }} onPointerUp={clearTouchHold} onPointerCancel={clearTouchHold} onContextMenu={event => { if (touchActions) event.preventDefault(); }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setTouchActions(false); }}><Avatar actor={actor} size={34} /><div className="message-content"><div className="message-meta"><span>{actor?.name || ''}</span><time title={dateTime(message.created_at)}>{time(message.created_at)}</time>{message.kind === 'reply_for_me' && <small>{t('chat.replyForMe')}</small>}</div><div className={`message-bubble ${message.kind === 'reply_for_me' ? 'reply-for-me-bubble' : ''}`}>{message.reply_to && <SourceQuote name={message.reply_to.author_name} body={message.reply_to.body || t('attachments.message')} unavailable={message.reply_to.unavailable} />}{message.kind === 'reply_for_me' && message.raw_owner_input !== undefined && <details className="raw-input" open><summary>{t('chat.rawInput')}<Icon name="chevron" size={14} /></summary><p>{message.raw_owner_input}</p></details>}{question ? <AskUserCard key={`${api.ownerId}:${question.id}`} api={api} question={question} onChanged={onQuestionChanged} onOrigin={onOrigin} /> : message.body && <MessageMarkdown text={message.body} />}{!!message.attachments?.length && <><MessageAttachments attachments={message.attachments} />{message.kind === 'reply_for_me' && <p className="attachment-private-hint">{t('attachments.ownerOnly')}</p>}</>}</div>{message.kind === 'reply_for_me' && <div className="raw-disclosure">{t('chat.rawVisible')}</div>}<MessageReactions message={message} preferences={reactionPreferences} onPreferences={onReactionPreferences} onReact={(emoji, active, key) => onReact(message, emoji, active, key)}><button title={t('chat.reply')} onClick={() => onReply(message)}><Icon name="reply" size={14} /></button><button title={t('chat.trackingFromMessage')} onClick={() => onTrack(message)}><Icon name="check" size={14} /></button></MessageReactions>{!!queue.length && <div className="message-request-chips">{queue.map(request => { const twin = actors.find(a => a.id === request.target_twin_id); return request.status === 'queued' ? <span className="queue-chip" key={request.id} title={t('chat.queued')}><Icon name="clock" size={14} /><Avatar actor={twin} size={20} /><span>{twin?.name}</span>{message.author_id === ownerId && request.requester_id === ownerId && request.can_cancel && <button disabled={pending.has(request.id)} onClick={() => onCancel(request)} aria-label={t('chat.cancelRequest')} title={t('chat.cancelRequest')}><Icon name="close" size={12} /></button>}</span> : <FailureNotice key={JSON.stringify([ownerId, request.chat_id, request.id])} ownerId={ownerId} request={request} name={twin?.name} busy={pending.has(request.id)} onRetry={onRetry} />; })}</div>}{message.routing_failure && <div className="message-request-chips"><FailureNotice key={message.routing_failure.command_id} ownerId={ownerId} request={message.routing_failure} label={t('chat.routingFailed')} busy={pending.has(message.routing_failure.id)} onRetry={onRouteRetry} /></div>}{own && <div className="read-receipt" title={readNames.length ? t('chat.readBy', { names: readNames.join(', ') }) : t('chat.sent')}><Icon name="check" size={12} />{readNames.length ? t('chat.readBy', { names: readNames.join(', ') }) : ''}</div>}</div></article>;
}
function PrivateReplyStatus({ request, actor, busy, onCancel }: { request: ReplyRequest; actor?: Actor; busy: boolean; onCancel: (request: ReplyRequest) => void }) {
  const preparing = request.status === 'queued'; const stopping = busy || request.cancel_requested === true; const memoryReview = request.source === 'memory_dream';
  return <div className="typing-row" role="status" data-reply-phase={stopping ? 'stopping' : preparing ? 'preparing' : 'running'} data-request-id={request.id} title={stopping ? t('chat.stopping') : preparing ? t('chat.acceptedWaiting') : undefined}>
    <span className="typing-avatars"><Avatar actor={actor} size={25} /></span>
    <span>{t(stopping ? 'chat.stopping' : preparing ? memoryReview ? 'chat.preparingMemoryReview' : 'chat.preparingPrivateReply' : memoryReview ? 'chat.organizingMemory' : 'chat.yourTwinTyping')}</span>
    {!preparing && !stopping ? <span className="typing-dots"><i /><i /><i /></span> : <Icon name="clock" size={14} />}
    {request.can_cancel && <button type="button" className="stop-button" disabled={stopping} aria-label={t(preparing ? 'chat.cancelRequest' : 'chat.stop')} onClick={() => onCancel(request)}>{t(preparing ? 'common.cancel' : 'chat.stop')}</button>}
  </div>;
}
export default function App() {
  useMenuBehavior();
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
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null); const [selected, setSelected] = useState(''); const [detail, setDetail] = useState<ChatDetail | null>(null); const [loading, setLoading] = useState(true); const [chatLoading, setChatLoading] = useState(false); const [error, setError] = useState(''); const [page, setPage] = useState<Page>(routePage); const [completed, setCompleted] = useState(false); const [version, setVersion] = useState(0); const [connected, setConnected] = useState(false); const [hasConnected, setHasConnected] = useState(false);
  const [trackingVersion, setTrackingVersion] = useState(0); const [notificationSettingsOpen, setNotificationSettingsOpen] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({}); const [replyTo, setReplyTo] = useState<Message | null>(null); const [busyKeys, setBusyKeys] = useState<Set<string>>(new Set()); const [liveText, setLiveText] = useState<Record<string, string>>({});
  const detailCurrent = useRef(detail); detailCurrent.current = detail;
  const chatLoads = useRef(new Map<string, { generation: number | undefined; pending: boolean; promise: Promise<void> }>());
  const [query, setQuery] = useState(''); const [people, setPeople] = useState<Actor[]>([]); const [searchOpen, setSearchOpen] = useState(false); const [peopleLoading, setPeopleLoading] = useState(false); const [renameChat, setRenameChat] = useState<Chat | null>(null); const [peopleModal, setPeopleModal] = useState<{ add: boolean; person?: Actor } | null>(null); const [settingsOpen, setSettingsOpen] = useState(false); const [reminderOpen, setReminderOpen] = useState(false); const [trackingSeed, setTrackingSeed] = useState<string | null>(null);
  const [sidebarVisible, setSidebarVisible] = useState(true); const [sidebarWidth, setSidebarWidth] = useState(() => Math.min(480, Math.max(240, Number(localStorage.getItem('twinteams.chatPaneWidth')) || 360))); const [newChat, setNewChat] = useState(false); const [findOpen, setFindOpen] = useState(false); const [appMenuOpen, setAppMenuOpen] = useState(false); const [shortcutsOpen, setShortcutsOpen] = useState(false); const [composerMenu, setComposerMenu] = useState<'emoji' | 'attachments' | 'more' | null>(null); const [messageHighlight, setMessageHighlight] = useState(''); const [dragActive, setDragActive] = useState(false); const [expandedComposer, setExpandedComposer] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null); const imageInput = useRef<HTMLInputElement>(null);
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth); const maxPaneWidth = Math.min(480, Math.max(240, viewportWidth - 436)); const paneWidth = Math.min(sidebarWidth, maxPaneWidth);
  useEffect(() => { const resize = () => setViewportWidth(window.innerWidth); window.addEventListener('resize', resize); return () => window.removeEventListener('resize', resize); }, []);
  const sidebarDrag = useRef<{ x: number; width: number } | null>(null);
  useEffect(() => {
    const move = (event: PointerEvent) => { if (sidebarDrag.current) setSidebarWidth(Math.min(Math.min(480, Math.max(240, window.innerWidth - 436)), Math.max(240, sidebarDrag.current.width + event.clientX - sidebarDrag.current.x))); };
    const up = () => { if (sidebarDrag.current) { sidebarDrag.current = null; document.body.classList.remove('resizing-pane'); } };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.classList.remove('resizing-pane'); };
  }, []);
  useEffect(() => { localStorage.setItem('twinteams.chatPaneWidth', String(sidebarWidth)); }, [sidebarWidth]);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => { const target = event.target as Element; if (!target.closest('[data-floating-menu]')) { setAccountOpen(false); setAppMenuOpen(false); setComposerMenu(null); } };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setSearchOpen(false); setAccountOpen(false); setAppMenuOpen(false); setComposerMenu(null); setShortcutsOpen(false); if (!(document.activeElement instanceof HTMLInputElement)) setFindOpen(false); }
      if (document.querySelector('[aria-modal="true"]')) return;
      if ((navigator.platform.includes('Mac') ? event.metaKey && event.altKey : event.altKey && event.shiftKey && !event.ctrlKey && !event.metaKey) && (event.code === 'KeyO' || event.key.toLowerCase() === 'o') && page === 'chat' && selected && !newChat && fileInput.current) { event.preventDefault(); setComposerMenu('attachments'); return; }
      if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === 'x' && page === 'chat' && selected && !newChat) { event.preventDefault(); setExpandedComposer(previous => !previous); input.current?.focus(); return; }
      if ((event.ctrlKey || event.metaKey) && !event.altKey) {
        if (event.key.toLowerCase() === 'k') { event.preventDefault(); searchInput.current?.focus(); }
        if (event.key.toLowerCase() === 'f' && page === 'chat' && selected) { event.preventDefault(); setFindOpen(true); }
        if (event.key === '\\') { event.preventDefault(); setSidebarVisible(previous => !previous); }
        if (event.key.toLowerCase() === 'n') { event.preventDefault(); setNewChat(true); setPage('chat'); }
      }
    };
    document.addEventListener('pointerdown', dismiss); document.addEventListener('keydown', key);
    return () => { document.removeEventListener('pointerdown', dismiss); document.removeEventListener('keydown', key); };
  }, [page, selected, newChat]);
  useEffect(() => { setComposerMenu(null); setFindOpen(false); setDragActive(false); setRenameChat(null); }, [selected, ownerId]);
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
  const actualOwnerId = bootstrap?.current_owner_id || ownerId; const actors = bootstrap?.actors || []; const owner = actors.find(a => a.id === actualOwnerId); const ownTwin = actors.find(a => a.kind === 'twin' && a.owner_id === actualOwnerId); const draftKey = `${actualOwnerId}:${selected}`; const draft = drafts[draftKey] || ''; const activeChat = detail?.chat.id === selected ? detail : null;
  const desktopNotifications = useDesktopNotifications(ownerId, chooseChat);
  const { observeBootstrap: observeNotificationBootstrap, observeChat: observeNotificationChat, close: closeNotifications, revoke: revokeNotifications, stop: stopNotifications } = desktopNotifications;
  const replyForMe = !!activeChat && activeChat.chat.kind !== 'private' && activeChat.settings.reply_for_me_enabled;
  const activeTitle = activeChat ? chatTitle(activeChat.chat, actors, actualOwnerId) : '';
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
  const canSend = !!(draft.trim() || attachmentDrafts.items.length) && attachmentsReady;
  useEffect(() => { if (!input.current) return; input.current.style.height = 'auto'; input.current.style.height = `${Math.min(180, Math.max(expandedComposer ? 120 : 28, input.current.scrollHeight))}px`; }, [draft, expandedComposer, selected, newChat]);
  const checkAuth = useCallback(async () => { setConfigLoading(true); setConfigError(''); const client = new ApiClient(); try { const config = await client.get<{ development_mode: boolean; acceptance_fixture?: boolean }>('/config'); setDevelopmentMode(config.development_mode); setAcceptanceFixture(config.acceptance_fixture === true); try { setSession(await client.get<AuthSession>('/auth/me')); } catch { setSession(null); } } catch (e) { setConfigError(e instanceof Error ? e.message : String(e)); } finally { setAuthChecked(true); setConfigLoading(false); } }, []);
  useEffect(() => { localStorage.removeItem('twinteams.developmentOwner'); void checkAuth(); }, [checkAuth]);
  useEffect(() => {
    setProviderConfigAvailable(false);
    if (!providerConfigAllowed) return;
    const controller = new AbortController();
    getLocalProviderConfig(controller.signal).then(config => { if (!controller.signal.aborted) setProviderConfigAvailable(config.available); }).catch(() => {});
    return () => controller.abort();
  }, [providerConfigAllowed]);
  const onError = useCallback((e: unknown) => { if (currentApi.current !== api) return; if (e instanceof ApiError && [401, 403, 404].includes(e.status)) { localSends.clear(e.status === 401 ? undefined : current.current.selected); if (e.status === 401) { stopNotifications(); setSession(null); } } if (e instanceof DOMException && e.name === 'AbortError') return; setError(e instanceof Error ? e.message : String(e)); }, [api, localSends.clear, stopNotifications]);
  useEffect(() => { api.onUnauthorized = () => { if (currentApi.current !== api) return; stopNotifications(); localSends.clear(); setSession(null); }; api.onAccessLost = () => { if (currentApi.current !== api) return; revokeNotifications(); localSends.clear(); setDetail(null); }; return () => { api.onUnauthorized = undefined; api.onAccessLost = undefined; }; }, [api, localSends.clear, stopNotifications, revokeNotifications]);
  const loadBootstrap = useCallback(async () => { const sequence = ++bootstrapSequence.current; const result = await api.get<Bootstrap>('/bootstrap'); if (currentApi.current !== api || current.current.ownerId !== api.ownerId || sequence !== bootstrapSequence.current) return; result.actors = result.actors.map(actor => { const cached = actorCache.current.get(actor.id); if (cached && (cached.profile_revision || 0) > (actor.profile_revision || 0)) return cached; actorCache.current.set(actor.id, actor); return actor; }); result.chats = result.chats.map(chat => { const twin = result.actors.find(actor => actor.kind === 'twin' && chat.member_owner_ids.includes(actor.owner_id || '')); return chat.kind === 'private' && twin ? { ...chat, title: twin.name } : chat; }); observeNotificationBootstrap(result.chats, result.actors, result.server_time); setBootstrap(result); if (result.acceptance_fixture === true) setAcceptanceFixture(true); setSelected(previous => result.chats.some(chat => chat.id === previous) ? previous : result.chats.find(chat => chat.kind === 'private')?.id || result.chats[0]?.id || ''); }, [api, observeNotificationBootstrap]);
  const fetchChat = useCallback(async (chatId: string) => {
    if (!chatId) return;
    const sequence = ++chatSequence.current;
    const startedAt = performance.now();
    const key = `${api.ownerId}:${chatId}`;
    const generation = resetGenerations.current[key];
    const floor = historyFloor.current[key];
    const anchored = messageArea.current && !scrollBottom.current ? messageArea.current.scrollHeight - messageArea.current.scrollTop : null;
    let latest: ChatDetail | undefined; let reauthorizingHistory = false;
    const isCurrent = () => currentApi.current === api && current.current.selected === chatId && current.current.ownerId === api.ownerId && sequence === chatSequence.current && generation === resetGenerations.current[key];
    try {
      const result = await api.get<ChatDetail>(`/chats/${chatId}?limit=100`);
      if (!isCurrent() || detailCurrent.current?.chat.id === chatId && result.revision < detailCurrent.current.revision) return;
      latest = result;
      let messages = result.messages;
      let questions = result.questions || [];
      let cursor = result.before_seq;
      let hasMore = result.has_more;
      reauthorizingHistory = floor !== undefined && !!hasMore && !!cursor && cursor > floor;
      if (reauthorizingHistory) {
        // Status metadata is already authorized. Do not hold it behind older pages,
        // or merge new body/history windows without a complete reauthorization.
        setDetail(previous => previous?.chat.id === chatId ? { ...previous, requests: result.requests, approvals: result.approvals, questions: [...(previous.questions || []).filter(question => !result.messages.some(message => message.id === question.message_id)), ...(result.questions || [])], automatic_conversations: result.automatic_conversations } : previous);
        setVersion(value => value + 1);
      }
      // Reauthorize every already loaded page, replacing withdrawn/revoked content.
      // Each request is bounded; no stale earlier-page content is merged from cache.
      while (floor !== undefined && hasMore && cursor && cursor > floor) {
        const page = await api.get<ChatDetail>(`/chats/${chatId}?limit=100&before_seq=${cursor}`);
        if (!isCurrent()) return;
        messages = [...page.messages, ...messages];
        questions = [...(page.questions || []), ...questions];
        hasMore = page.has_more;
        if (!page.before_seq || page.before_seq >= cursor) break;
        cursor = page.before_seq;
      }
      if (currentApi.current !== api || current.current.selected !== chatId || current.current.ownerId !== api.ownerId || sequence !== chatSequence.current || generation !== resetGenerations.current[key]) return;
      if (detailCurrent.current?.chat.id === chatId && result.revision < detailCurrent.current.revision) return;
      localSends.reconcile({ ...result, messages }, startedAt);
      result.members = result.members.map(member => { const cached = actorCache.current.get(member.id); if (cached && (cached.profile_revision || 0) > (member.profile_revision || 0)) return cached; actorCache.current.set(member.id, member); return member; });
      if (result.chat.kind === 'private') { const twin = result.members.find(member => member.kind === 'twin' && result.chat.member_owner_ids.includes(member.owner_id || '')); if (twin) result.chat = { ...result.chat, title: twin.name }; }
      if (cursor) historyFloor.current[key] = cursor;
      observeNotificationChat(result.chat, result.members);
      setBootstrap(previous => previous ? { ...previous, actors: previous.actors.map(actor => { const updated = result.members.find(member => member.id === actor.id); return updated && (updated.profile_revision || 0) >= (actor.profile_revision || 0) ? updated : actor; }), chats: previous.chats.map(chat => chat.id === result.chat.id ? result.chat : chat) } : previous);
      setDetail(previous => {
        // A partial reaction acknowledgement cannot advance the full chat snapshot.
        // Preserve only newer local reaction aggregates on still-authorized messages.
        const newer = previous?.chat.id === chatId ? previous.messages.filter(message => (message.reactions_revision || 0) > result.revision) : [];
        return { ...result, questions, messages: messages.map(message => { const reaction = newer.find(item => item.id === message.id); return reaction ? { ...message, reactions: reaction.reactions, reactions_revision: reaction.reactions_revision } : message; }), has_more: hasMore, before_seq: cursor, settings: { ...result.settings, ...pendingSettings.current.get(key) } };
      });
      setLiveText({}); if (!reauthorizingHistory) setVersion(value => value + 1);
      if (anchored !== null) requestAnimationFrame(() => { if (messageArea.current) messageArea.current.scrollTop = messageArea.current.scrollHeight - anchored; });
    } catch (error) {
      if (isCurrent()) {
        if (error instanceof ApiError && [401, 403, 404, 409].includes(error.status)) { revokeNotifications(chatId); localSends.clear(chatId); setDetail(null); }
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
  }, [api, localSends.reconcile, localSends.clear, observeNotificationChat, revokeNotifications]);
  const loadChat = useCallback((chatId = selected): Promise<void> => {
    if (!chatId) return Promise.resolve();
    const key = `${api.ownerId}:${chatId}`; const generation = resetGenerations.current[key];
    const existing = chatLoads.current.get(key);
    if (existing && existing.generation === generation) { existing.pending = true; return existing.promise; }
    const entry = { generation, pending: false, promise: Promise.resolve() };
    chatLoads.current.set(key, entry);
    entry.promise = (async () => {
      do { entry.pending = false; await fetchChat(chatId); }
      while (entry.pending && currentApi.current === api && current.current.ownerId === api.ownerId && current.current.selected === chatId && resetGenerations.current[key] === generation);
    })().finally(() => { if (chatLoads.current.get(key) === entry) chatLoads.current.delete(key); });
    return entry.promise;
  }, [api, selected, fetchChat]);
  localSends.refresh.current = chatId => { if (current.current.selected === chatId) void loadChat(chatId).catch(onError); };
  const refreshAll = useCallback(async () => { try { await Promise.all([loadBootstrap(), loadChat()]); } catch (e) { onError(e); } }, [loadBootstrap, loadChat, onError]);
  const applyTwinReset = useCallback((status: TwinResetStatus) => {
    const chatId = status.private_chat_id; const key = `${api.ownerId}:${chatId}`;
    resetGenerations.current[key] = status.generation; bootstrapSequence.current++;
    localSends.clear(chatId);
    if (!bootstrapCurrent.current) void loadBootstrap().catch(onError);
    delete historyFloor.current[key]; pendingSettings.current.delete(key);
    setDrafts(previous => { const next = { ...previous }; delete next[key]; return next; });
    attachmentDrafts.clearScope(key);
    setMemoryResetRevision(value => value + 1); setTwinVersion(value => value + 1);
    setPrivateResetVersions(previous => ({ ...previous, [chatId]: status.generation }));
    for (const value of readSent.current) if (value.startsWith(`${key}:`)) readSent.current.delete(value);
    for (const value of readPending.current) if (value.startsWith(`${key}:`)) readPending.current.delete(value);
    if (current.current.selected === chatId) {
      chatSequence.current++; setDetail(null); setLiveText({}); setReplyTo(null); setTrackingSeed(null); setFindOpen(false); setMessageHighlight(''); setComposerMenu(null);
      void loadChat(chatId).catch(onError);
    }
    void api.get<Chat>(`/chats/${chatId}/summary`).then(chat => { if (current.current.ownerId === api.ownerId && resetGenerations.current[key] === status.generation) setBootstrap(previous => previous ? { ...previous, chats: previous.chats.map(old => old.id === chatId ? chat : old) } : previous); }).catch(onError);
    void refreshCredentials();
  }, [api, loadBootstrap, loadChat, onError, attachmentDrafts.clearScope, refreshCredentials, localSends.clear]);
  const twinReset = useTwinReset(api, applyTwinReset);
  const refreshTwinReset = twinReset.refresh;
  useEffect(() => { if (!ownerId) { localSends.clear(); setBootstrap(null); setDetail(null); setSelected(''); setLiveText({}); setLoading(false); return; } let alive = true; chatLoads.current.clear(); setLoading(true); setDetail(null); setBootstrap(null); setSelected(''); setLiveText({}); setHasConnected(false); readSent.current.clear(); readPending.current.clear(); loadBootstrap().catch(e => { if (alive) onError(e); }).finally(() => { if (alive) setLoading(false); }); return () => { alive = false; }; }, [api]);
  useEffect(() => { if (!selected) return; let alive = true; setChatLoading(true); setReplyTo(null); setTrackingSeed(null); scrollBottom.current = true; setDetail(previous => previous?.chat.id === selected ? previous : null); loadChat(selected).catch(e => { if (alive) onError(e); }).finally(() => { if (alive) setChatLoading(false); }); return () => { alive = false; }; }, [selected, api]);
  const chatScopeKey = bootstrap?.chats.map(chat => chat.id).sort().join('|') || '';
  useEffect(() => { api.subscribeChats([selected, ...chatScopeKey.split('|')]); }, [api, selected, chatScopeKey]);
  useEffect(() => {
    if (!ownerId) return;
    const controller = new AbortController(); let retryTimer: ReturnType<typeof setTimeout> | undefined; let reconnectDelay = 700; let trackingTimer: ReturnType<typeof setTimeout> | undefined;
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
            if (event.section === 'scheduled_tasks') { setScheduledTasksRevision(value => value + 1); return; }
            if (event.section === 'tracking') { clearTimeout(trackingTimer); trackingTimer = setTimeout(() => setTrackingVersion(value => value + 1), 80); return; }
            if (event.section === 'reset') { void refreshTwinReset(); return; }
            if (event.section === 'chats') { setTrackingVersion(value => value + 1); void loadBootstrap().catch(onError); return; }
            setTwinVersion(value => value + 1);
            if (!event.section || event.section === 'credentials' || event.type === 'credential_requests_changed') void refreshCredentials();
            return;
          }
          if (event.type === 'worker_status') { setTwinVersion(value => value + 1); return; }
          if (event.chat_id && event.chat_id !== selected) {
            const chatId = event.chat_id; const key = `${api.ownerId}:${chatId}`; const generation = resetGenerations.current[key];
            void api.get<Chat>(`/chats/${chatId}/summary`).then(chat => { if (current.current.ownerId === api.ownerId && generation === resetGenerations.current[key]) { observeNotificationChat(chat, bootstrapCurrent.current?.actors || []); setBootstrap(previous => previous ? { ...previous, chats: previous.chats.map(old => old.id === chat.id ? chat : old) } : previous); } }).catch(failure => { if (current.current.ownerId === api.ownerId && generation === resetGenerations.current[key] && failure instanceof ApiError && [403, 404].includes(failure.status)) revokeNotifications(chatId); });
            return;
          }
          if (event.type === 'changed') {
            const alreadyLoaded = () => event.revision !== undefined && detailCurrent.current?.chat.id === selected && event.revision <= detailCurrent.current.revision;
            if (alreadyLoaded()) return;
            if (refreshTimer.current) clearTimeout(refreshTimer.current);
            refreshTimer.current = setTimeout(() => { if (current.current.selected === selected && !alreadyLoaded()) void loadChat(selected).catch(onError); }, 80);
          }
        }, state => { if (controller.signal.aborted || currentApi.current !== api) return; const selected = current.current.selected; setConnected(state); if (state) { setTrackingVersion(value => value + 1); setHasConnected(true); setScheduledTasksRevision(value => value + 1); reconnectDelay = 700; setLiveText({}); void loadBootstrap().catch(onError); if (selected) void loadChat(selected).catch(onError); setTwinVersion(value => value + 1); void refreshCredentials(); void refreshTwinReset(); void twinApi(api).profile().then(result => mergeActor(result.actor)).catch(() => {}); } });
      } catch { if (!controller.signal.aborted) setConnected(false); }
      if (!controller.signal.aborted) { setLiveText({}); retryTimer = setTimeout(() => { void run(); }, reconnectDelay); reconnectDelay = Math.min(reconnectDelay * 2, 10000); }
    };
    void run();
    return () => { controller.abort(); clearTimeout(retryTimer); clearTimeout(trackingTimer); if (refreshTimer.current) clearTimeout(refreshTimer.current); setConnected(false); };
  }, [api, ownerId, mergeActor, refreshCredentials, refreshTwinReset, observeNotificationChat, revokeNotifications]);
  const markVisibleRead = useCallback(() => {
    if (!focused.current || page !== 'chat' || detailCurrent.current?.chat.id !== selected || !messageArea.current) return;
    const area = messageArea.current.getBoundingClientRect(); const ids: string[] = [];
    messageArea.current.querySelectorAll<HTMLElement>('[data-message-id]').forEach(element => { const id = element.dataset.messageId!; const rect = element.getBoundingClientRect(); const visibleHeight = Math.min(rect.bottom, area.bottom) - Math.max(rect.top, area.top); const key = `${actualOwnerId}:${selected}:${id}`; if (visibleHeight >= Math.min(rect.height * .6, 80) && !readSent.current.has(key) && !readPending.current.has(key)) { ids.push(id); readPending.current.add(key); } });
    if (!ids.length) return;
    const chatId = selected; const reader = actualOwnerId; closeNotifications(chatId);
    void api.post(`/chats/${chatId}/read`, { message_ids: ids }).then(() => { ids.forEach(id => readSent.current.add(`${reader}:${chatId}:${id}`)); }).catch(() => {}).finally(() => { ids.forEach(id => readPending.current.delete(`${reader}:${chatId}:${id}`)); });
  }, [api, page, selected, actualOwnerId, closeNotifications]);
  useEffect(() => { const timer = setTimeout(() => { if (scrollBottom.current) messageArea.current?.scrollTo({ top: messageArea.current.scrollHeight }); markVisibleRead(); }, 60); return () => clearTimeout(timer); }, [activeChat?.messages, localSends.items.length, selected, markVisibleRead]);
  useEffect(() => {
    if (!ownerId) return; sessionStorage.setItem('twinteams.deviceId', deviceId.current);
    const update = () => { focused.current = document.visibilityState === 'visible' && document.hasFocus(); void api.post('/presence', { device_id: deviceId.current, focused_chat_id: focused.current && page === 'chat' ? selected || null : null, visible: focused.current }).catch(() => {}); if (focused.current) markVisibleRead(); };
    update(); const timer = setInterval(update, 4000); window.addEventListener('focus', update); window.addEventListener('blur', update); document.addEventListener('visibilitychange', update);
    return () => { clearInterval(timer); window.removeEventListener('focus', update); window.removeEventListener('blur', update); document.removeEventListener('visibilitychange', update); };
  }, [api, selected, page, markVisibleRead]);
  useEffect(() => { if (!searchOpen || !ownerId) return; const abort = new AbortController(); const timer = setTimeout(() => { setPeopleLoading(true); api.get<Actor[]>(`/people?q=${encodeURIComponent(query)}`, abort.signal).then(setPeople).catch(e => { if (!abort.signal.aborted) onError(e); }).finally(() => { if (!abort.signal.aborted) setPeopleLoading(false); }); }, 180); return () => { clearTimeout(timer); abort.abort(); }; }, [query, searchOpen, api]);
  useEffect(() => { const close = (e: MouseEvent) => { if (!searchWrap.current?.contains(e.target as Node)) setSearchOpen(false); }; document.addEventListener('mousedown', close); return () => document.removeEventListener('mousedown', close); }, []);
  useEffect(() => { const pop = () => { setPage(routePage()); setTwinTab(routeTwinTab()); setAccountOpen(false); }; window.addEventListener('hashchange', pop); return () => window.removeEventListener('hashchange', pop); }, []);
  function navigate(next: Page) { setPage(next); setAppMenuOpen(false); setNewChat(false); setComposerMenu(null); setAccountOpen(false); setSearchOpen(false); setSettingsOpen(false); setPeopleModal(null); setReminderOpen(false); history.replaceState(null, '', next === 'my-twin' ? `#/my-twin/${twinTab}` : next === 'provider-config' ? '#/provider-config' : `#${next}`); }
  function chooseTwinTab(next: TwinTab) { setTwinTab(next); history.replaceState(null, '', `#/my-twin/${next}`); }
  function chooseChat(chatId: string) { if (matchMedia('(max-width:600px)').matches) setSidebarVisible(false); setSelected(chatId); setFindOpen(false); navigate('chat'); }
  async function reactToMessage(message: Message, emoji: string, active: boolean, key: string) {
    const scope = `${api.ownerId}:${message.chat_id}`; const generation = resetGenerations.current[scope];
    const result = await api.put<{ chat_id: string; message_id: string; reactions: MessageReaction[]; revision: number }>(`/chats/${encodeURIComponent(message.chat_id)}/messages/${encodeURIComponent(message.id)}/reactions`, { emoji, active, client_request_key: key });
    if (current.current.ownerId !== api.ownerId || current.current.selected !== message.chat_id || generation !== resetGenerations.current[scope]) return;
    setDetail(previous => previous?.chat.id === message.chat_id ? { ...previous, messages: previous.messages.map(item => item.id === result.message_id && Math.max(previous.revision, item.reactions_revision || 0) <= result.revision ? { ...item, reactions: result.reactions, reactions_revision: result.revision } : item) } : previous);
  }
  async function withBusy(key: string, action: () => Promise<unknown>) { if (busyKeys.has(key)) return; setBusyKeys(p => new Set(p).add(key)); try { await action(); await refreshAll(); } catch (e) { onError(e); } finally { setBusyKeys(p => { const next = new Set(p); next.delete(key); return next; }); } }
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
    if (!activeChat?.has_more || !activeChat.before_seq) return;
    const key = `${api.ownerId}:${selected}`; const generation = resetGenerations.current[key]; const sequence = chatSequence.current;
    try {
      const previousHeight = messageArea.current?.scrollHeight || 0;
      const result = await api.get<ChatDetail>(`/chats/${selected}?before_seq=${activeChat.before_seq}&limit=100`);
      if (current.current.ownerId !== api.ownerId || current.current.selected !== selected || generation !== resetGenerations.current[key] || sequence !== chatSequence.current || detailCurrent.current?.chat.id === selected && result.revision < detailCurrent.current.revision) return;
      if (result.before_seq) historyFloor.current[key] = result.before_seq;
      setDetail(current => current?.chat.id === result.chat.id ? { ...current, has_more: result.has_more, before_seq: result.before_seq, messages: [...result.messages, ...current.messages], questions: [...(result.questions || []), ...(current.questions || [])] } : current);
      requestAnimationFrame(() => { if (messageArea.current) messageArea.current.scrollTop += messageArea.current.scrollHeight - previousHeight; });
    } catch (error) { onError(error); }
  }
  function send() {
    if (!canSend || !activeChat) return;
    const body = draft.trim(); const chatId = selected; const key = requestKey();
    const files = attachmentDrafts.items.map(item => item.attachment!);
    const quoted = replyTo ? { id: replyTo.id, author_name: actors.find(actor => actor.id === replyTo.author_id)?.name || '', body: replyTo.body || replyTo.attachments?.map(item => item.name).join(' · ') || t('attachments.message'), unavailable: replyTo.state !== 'published' } : undefined;
    const payload = { body, attachment_ids: files.map(item => item.id), client_request_key: key, reply_to_message_id: replyTo?.id, reply_for_me: replyForMe };
    const message: Message = { id: key, chat_id: chatId, seq: 0, author_id: actualOwnerId, kind: 'human', body, attachments: files, reply_to: quoted, created_at: new Date().toISOString(), read_by: [], state: 'published' };
    setError('');
    localSends.submit(chatId, payload, message);
    setDrafts(previous => ({ ...previous, [draftKey]: '' }));
    attachmentDrafts.clear(attachmentDrafts.items.map(item => item.localId));
    setReplyTo(null); scrollBottom.current = true; input.current?.focus();
  }
  function cancel(request: ReplyRequest) { void withBusy(request.id, () => api.post(`/reply-requests/${request.id}/cancel`)); }
  function retry(request: ReplyRequest) { void withBusy(request.id, async () => { const result = await api.post<ReplyRequest>(`/reply-requests/${request.id}/retry`, { client_request_key: requestKey() }); localSends.followRetry(request.id, result); }); }
  function retryRoute(failure: RoutingFailure) { void withBusy(failure.id, () => api.post(`/chats/${failure.chat_id}/routing-decisions/${failure.id}/retry`, { client_request_key: requestKey(), expected_command_id: failure.command_id })); }
  // Scheduled roots and continuations report progress in Scheduled tasks and Activity.
  const requests = (activeChat?.requests || []).filter(request => !request.quiet_background);
  // The API lists newest first, including accepted_seq descending for equal timestamps.
  // Reverse before the stable timestamp sort to preserve FIFO ties without using message order.
  const privatePending = activeChat?.chat.kind === 'private' ? requests.filter(request => request.target_twin_id === ownTwin?.id && (request.status === 'queued' || request.status === 'running')).slice().reverse().sort((a, b) => a.accepted_at.localeCompare(b.accepted_at)) : [];
  const privateStatusRequest = privatePending[0];
  const running = requests.filter(r => r.status === 'running'); const runningNames = running.map(r => actors.find(a => a.id === r.target_twin_id)?.name || '').filter(Boolean); const ownRunning = running.find(r => r.target_twin_id === ownTwin?.id && r.can_cancel); const composerQueue = activeChat?.chat.kind === 'private' ? privatePending.filter(request => request.status === 'queued' && request.id !== privateStatusRequest?.id) : requests.filter(r => r.status === 'queued' && (r.target_twin_id === ownTwin?.id || (r.requester_id === actualOwnerId && activeChat?.messages.find(m => m.id === r.source_message_id)?.author_id !== actualOwnerId))); const hiddenFailures = requests.filter(r => r.status === 'failed' && !activeChat?.messages.some(m => m.id === r.source_message_id)); const pendingCount = bootstrap?.reminders.filter(r => !r.done).length || 0;
  if (!authChecked) return <div className="app-loading auth-loading"><span className="spinner" />{t('common.loading')}</div>;
  if (!session) return <LoginScreen api={api} acceptanceFixture={acceptanceFixture} developmentMode={developmentMode} configLoading={configLoading} configError={configError} onLogin={result => { setSession(result); setDevelopmentMode(result.development_mode); setError(''); }} onReload={() => { void checkAuth(); }} />;
  return <div className={`app-shell ${acceptanceFixture ? 'acceptance-fixture' : ''}`}><header className="topbar"><div className="brand" title={t('app.name')}><BrandMark /></div><IconButton icon="panel-left" label={t('shell.toggleChats')} active={!sidebarVisible} onClick={() => setSidebarVisible(previous => !previous)} /><div className="search-wrap" ref={searchWrap}><div className="global-search"><Icon name="search" size={18} /><input ref={searchInput} placeholder={t('search.placeholder')} aria-label={t('search.placeholder')} value={query} onChange={e => setQuery(e.target.value)} onFocus={() => setSearchOpen(true)} onKeyDown={e => { if (e.key === 'Escape') setSearchOpen(false); }} /></div>{searchOpen && <div className="search-results"><p>{t('search.hint')}</p>{peopleLoading && !people.length ? <div className="search-empty">{t('common.loading')}</div> : people.filter(a => a.id !== actualOwnerId).length ? people.filter(a => a.id !== actualOwnerId).map(person => <button key={person.id} onClick={() => { setPeopleModal({ add: false, person }); setSearchOpen(false); }}><Avatar actor={person} size={34} /><span>{person.name}</span><Icon name="arrow" size={16} /></button>) : <div className="search-empty">{t('search.empty')}</div>}</div>}</div><div className="top-right"><div className="app-menu-wrap" data-floating-menu><IconButton icon="more" label={t('shell.appMenu')} active={appMenuOpen} onClick={() => { setAppMenuOpen(previous => !previous); setAccountOpen(false); }} />{appMenuOpen && <div className="fluent-menu app-options-menu" role="menu"><button role="menuitem" onClick={() => { setNotificationSettingsOpen(true); setAppMenuOpen(false); }}><Icon name="bell" size={18} />{t('notifications.title')}</button><button role="menuitem" onClick={() => { setShortcutsOpen(true); setAppMenuOpen(false); }}><Icon name="info" size={18} />{t('shell.keyboard')}</button></div>}</div><div className="account-menu-wrap" data-floating-menu><button className="account-button" title={t('auth.account')} aria-label={t('auth.account')} aria-expanded={accountOpen} onClick={() => { setAccountOpen(p => !p); setAppMenuOpen(false); }}><span className="account-name">{owner?.name}</span><Avatar actor={owner} size={31} /></button>{accountOpen && <div className="account-menu" role="menu"><strong>{owner?.name}</strong><button role="menuitem" onClick={() => navigate('my-twin')}><Icon name="spark" size={16} />{t('twin.title')}</button>{bootstrap?.development_mode && <small>{t('auth.development')}</small>}{providerConfigAllowed && providerConfigAvailable && <button onClick={() => navigate('provider-config')}>{t('providerConfig.title')}</button>}<button onClick={async () => { try { stopNotifications(); localSends.clear(); await api.post('/presence', { device_id: deviceId.current, focused_chat_id: null, visible: false }).catch(() => {}); await api.post(`/auth/logout?device_id=${encodeURIComponent(deviceId.current)}`); setAccountOpen(false); setNotificationSettingsOpen(false); navigate('chat'); setSession(null); setSearchOpen(false); setPeopleModal(null); setSettingsOpen(false); setReminderOpen(false); setReplyTo(null); setDrafts({}); setError(''); } catch (e) { onError(e); } }}>{t('auth.signOut')}</button></div>}</div></div></header>{acceptanceFixture && <FixtureBanner />}<DesktopNotificationPrompt controller={desktopNotifications} /><div className="workspace"><nav className="app-rail" aria-label={t('nav.main')}>{([{ id: 'chat', label: t('nav.chat'), icon: 'chat' }, { id: 'reminders', label: t('nav.reminders'), icon: 'bell' }] as const).map(item => <button className={`rail-item ${page === item.id ? 'active' : ''}`} key={item.id} title={item.label} aria-label={item.label} aria-current={page === item.id ? 'page' : undefined} onClick={() => navigate(item.id)}><span className="rail-icon"><Icon name={item.icon} size={23} />{item.id === 'reminders' && pendingCount > 0 && <span className="rail-badge">{pendingCount}</span>}</span><span className="rail-label">{item.label}</span></button>)}<div className="rail-spacer" /><button className="rail-item" title={t('shell.appMenu')} aria-label={t('shell.appMenu')} onClick={() => { setShortcutsOpen(true); }}><Icon name="apps" size={21} /></button></nav>{page !== 'provider-config' && page !== 'my-twin' && <><aside className={`sidebar ${sidebarVisible ? '' : 'collapsed'}`} inert={!sidebarVisible} aria-hidden={!sidebarVisible} style={{ width: sidebarVisible ? paneWidth : 0 }}>{page === 'chat' ? <ChatNavigation key={actualOwnerId} ownerId={actualOwnerId} chats={bootstrap?.chats || []} actors={actors} loading={loading} selected={selected} newChat={newChat} onChoose={chooseChat} onNew={() => { if (matchMedia('(max-width:600px)').matches) setSidebarVisible(false); setNewChat(true); setFindOpen(false); setSearchOpen(false); }} /> : <><div className="sidebar-heading"><h1>{t('nav.reminders')}</h1><IconButton icon="plus" label={t('reminders.new')} onClick={() => setReminderOpen(true)} /></div><div className="reminder-nav">{[false, true].map(value => <button key={String(value)} className={completed === value ? 'selected' : ''} onClick={() => setCompleted(value)}><Icon name={value ? 'check' : 'bell'} size={18} />{t(value ? 'reminders.completed' : 'reminders.pending')}<span>{bootstrap?.reminders.filter(r => r.done === value).length || 0}</span></button>)}</div></>}</aside><div className="pane-resizer" style={{ display: sidebarVisible ? undefined : 'none' }} role="separator" aria-orientation="vertical" aria-label={t('shell.resizeChats')} aria-valuenow={paneWidth} aria-valuemin={240} aria-valuemax={maxPaneWidth} tabIndex={0} onPointerDown={event => { sidebarDrag.current = { x: event.clientX, width: paneWidth }; event.currentTarget.setPointerCapture(event.pointerId); document.body.classList.add('resizing-pane'); }} onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); setSidebarWidth(previous => Math.min(maxPaneWidth, Math.max(240, Math.min(previous, maxPaneWidth) + (event.key === 'ArrowRight' ? 16 : -16)))); } }} /></>}

    {page === 'my-twin' ? <MyTwinPage key={ownerId} api={api} account={owner} actor={ownTwin} tab={twinTab} revision={twinVersion} credentials={credentials} requests={credentialRequests} credentialsConfigured={credentialsConfigured} credentialsLoading={credentialsLoading} credentialsError={credentialsError} reset={twinReset} memoryRevision={memoryResetRevision} scheduledTasksRevision={scheduledTasksRevision} onTab={chooseTwinTab} onActor={mergeActor} onBack={() => navigate('chat')} onCredentialsChange={() => { void refreshCredentials(); }} /> : page === 'provider-config' ? <ProviderConfigPage key={ownerId} allowed={providerConfigAllowed} onBack={() => navigate('chat')} /> : page === 'reminders' ? <RemindersPage reminders={bootstrap?.reminders || []} completed={completed} onNew={() => setReminderOpen(true)} onChat={chooseChat} onToggle={reminder => { void withBusy(reminder.id, () => api.patch(`/reminders/${reminder.id}`, { done: !reminder.done })); }} /> : newChat ? <main className="chat-main new-chat-main"><PeoplePicker inline api={api} ownerId={actualOwnerId} onClose={() => setNewChat(false)} onDone={chat => { setNewChat(false); chooseChat(chat.id); void loadBootstrap().catch(onError); if (chat.id === selected) void loadChat().catch(onError); }} onError={onError} /><div className="new-conversation-empty"><img className="conversation-illustration" src={COMPOSE_ILLUSTRATION} width={256} height={256} alt="" aria-hidden="true" /><h2>{t('shell.newHeading')}</h2><p>{t('shell.newDetail')}</p></div><div className="composer-area new-chat-composer" aria-label={t('chat.message')}><div className="composer"><textarea disabled rows={1} aria-label={t('chat.message')} placeholder={t('shell.typeMessage')} /><div className="composer-tools"><IconButton icon="smile" label={t('chat.emoji')} disabled /><IconButton icon="attachment" label={t('workspace.upload')} disabled /><IconButton icon="image" label={t('attachments.media')} disabled /><span className="tool-divider" /><IconButton icon="send" label={t('chat.send')} disabled /></div></div></div></main> : activeChat ? <><main className={`chat-main ${findOpen || activeChat.settings.sidebar_open ? 'with-side-panel' : ''} ${dragActive ? 'file-drag-active' : ''}`} onLoadCapture={() => { if (scrollBottom.current && messageArea.current) messageArea.current.scrollTop = messageArea.current.scrollHeight; markVisibleRead(); }} onDragEnter={event => { if (Array.from(event.dataTransfer.types).includes('Files')) { event.preventDefault(); setDragActive(true); } }} onDragOver={event => { if (Array.from(event.dataTransfer.types).includes('Files')) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; } }} onDragLeave={event => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDragActive(false); }} onDrop={event => { setDragActive(false); if (event.dataTransfer.files.length) { event.preventDefault(); attachmentDrafts.ingest(Array.from(event.dataTransfer.files)); input.current?.focus(); } }}><input ref={fileInput} type="file" hidden multiple onChange={event => { attachmentDrafts.ingest(Array.from(event.target.files || [])); event.target.value = ''; }} /><input ref={imageInput} type="file" hidden multiple accept="image/*" onChange={event => { attachmentDrafts.ingest(Array.from(event.target.files || [])); event.target.value = ''; }} />{dragActive && <div className="attachment-drop-overlay"><Icon name="attachment" size={48} /><strong>{t('attachments.drop')}</strong><span>{t('attachments.dropDetail')}</span></div>}<header className="chat-header"><ChatAvatar chat={activeChat.chat} actors={actors} ownerId={actualOwnerId} size={32} /><div className="chat-header-copy"><div className="chat-header-title"><h2>{activeTitle}</h2>{canRenameChat && <IconButton icon="edit" label={t('chat.rename')} size={15} onClick={() => setRenameChat(activeChat.chat)} />}</div><p>{activeChat.chat.kind === 'private' ? t('chat.private') : t('chat.peopleTwins', { count: activeChat.chat.member_owner_ids.length })}</p></div><div className="header-actions"><IconButton icon="search" label={t('shell.findInChat')} active={findOpen} onClick={() => setFindOpen(previous => !previous)} /><button className="member-button" onClick={() => setPeopleModal({ add: true })} title={t('chat.addMembers')}><Icon name="people" size={20} /><span>{activeChat.members.length}</span></button><IconButton icon="settings" label={t('chat.settings')} onClick={() => setSettingsOpen(true)} /><IconButton icon="sidebar" label={t('chat.toggleSidebar')} active={activeChat.settings.sidebar_open} onClick={() => { setFindOpen(false); void setSettings({ sidebar_open: !activeChat.settings.sidebar_open }); }} /></div></header>{!bootstrap?.model_configured && <div className="model-notice"><Icon name="info" size={15} /><span>{t('config.unconfigured')}</span></div>}<div className={`connection-state ${connected ? 'connected' : ''}`} role="status"><i className={connected ? 'online' : ''} />{connected ? t('connection.live') : t(hasConnected ? 'connection.reconnecting' : 'connection.connecting')}{!connected && <button onClick={() => { setLiveText({}); void refreshAll(); }}>{t('common.refresh')}</button>}</div><div className={`message-area ${!activeChat.messages.length && !localSends.items.some(item => item.chatId === selected) ? 'empty' : ''}`} ref={messageArea} onScroll={() => { if (messageArea.current) scrollBottom.current = messageArea.current.scrollHeight - messageArea.current.scrollTop - messageArea.current.clientHeight < 100; markVisibleRead(); }}>{activeChat.has_more && <button className="text-button" onClick={() => { void loadEarlier(); }}>{t('shell.loadEarlier')}</button>}{!activeChat.messages.length && !localSends.items.some(item => item.chatId === selected) ? <div className="chat-empty-state"><ChatAvatar chat={activeChat.chat} actors={actors} ownerId={actualOwnerId} size={96} /><h3>{t(activeChat.chat.kind === 'group' && activeChat.chat.member_owner_ids.length >= 3 ? 'chat.emptyGroupTitle' : 'chat.empty')}</h3><p>{t(activeChat.chat.kind === 'private' ? 'chat.emptyPrivate' : 'chat.emptyGroup')}</p></div> : activeChat.messages.filter(m => m.state === 'published').sort((a, b) => a.seq - b.seq).map((message, index, all) => { const actor = actors.find(a => a.id === message.author_id); const own = message.author_id === actualOwnerId || (message.kind === 'reply_for_me' && actor?.owner_id === actualOwnerId); const showDate = index === 0 || new Date(message.created_at).toDateString() !== new Date(all[index - 1].created_at).toDateString(); return <div key={message.id} className={messageHighlight === message.id ? 'message-highlight' : ''}>{showDate && <div className="date-divider">{formatDate(new Date(message.created_at), { month: 'long', day: 'numeric' })}</div>}<MessageItem message={message} question={activeChat.questions?.find(question => question.message_id === message.id)} api={api} onQuestionChanged={() => loadChat(selected)} onOrigin={chooseChat} actor={actor} own={own} actors={actors} ownerId={actualOwnerId} requests={requests} showQueued={activeChat.chat.kind !== 'private'} pending={busyKeys} reactionPreferences={reactionPreferences.value} onReactionPreferences={reactionPreferences.save} onReact={reactToMessage} onCancel={cancel} onRetry={retry} onRouteRetry={retryRoute} onReply={m => { setReplyTo(m); input.current?.focus(); }} onTrack={m => { setTrackingSeed(m.id); void setSettings({ sidebar_open: true, sidebar_tab: 'tracking' }); }} /></div>; })}{localSends.items.filter(item => item.chatId === selected).map(entry => <LocalSendItem key={entry.key} entry={entry} actor={owner} onRetry={() => localSends.retry(entry)} />)}{running.filter(request => liveText[request.id]).map(request => <div className="message-row live-message" key={request.id}><Avatar actor={actors.find(a => a.id === request.target_twin_id)} size={34} /><div className="message-content"><div className="message-meta"><span>{actors.find(a => a.id === request.target_twin_id)?.name}</span><small>{t('chat.transient')}</small></div><div className="message-bubble"><MessageMarkdown text={liveText[request.id]} /><span className="live-cursor" /></div></div></div>)}</div><div className="composer-area">{activeChat.chat.kind === 'private' && actualOwnerId === api.ownerId && <TwinOnboarding key={`${api.ownerId}:${selected}:${privateResetVersions[selected] || 0}`} api={api} chat={activeChat.chat} revision={activeChat.revision} onChanged={() => { void loadChat(selected).catch(onError); }} />}<AutomaticConversations key={`${actualOwnerId}:${selected}`} api={api} chatId={selected} conversations={activeChat.automatic_conversations} actors={actors} onChange={refreshAll} />{activeChat.chat.kind === 'private' && <CredentialRequestCards key={`credentials:${actualOwnerId}:${selected}`} compact api={api} requests={credentialRequests.filter(request => request.chat_id === selected)} credentials={credentials} onChange={() => { void refreshCredentials(); }} />}<ApprovalsDrawer key={`${actualOwnerId}:${selected}`} approvals={activeChat.approvals.filter(a => a.approver_id === actualOwnerId)} onDecision={async (id, decision) => { try { await api.post(`/approvals/${id}/decision`, { decision }); await refreshAll(); } catch (e) { onError(e); } }} />{privateStatusRequest ? <PrivateReplyStatus request={privateStatusRequest} actor={ownTwin} busy={busyKeys.has(privateStatusRequest.id)} onCancel={cancel} /> : activeChat.chat.kind !== 'private' && !!running.length && <div className="typing-row"><span className="typing-avatars">{running.slice(0, 3).map(request => <Avatar key={request.id} actor={actors.find(a => a.id === request.target_twin_id)} size={25} />)}</span><span>{running.length === 1 ? t(ownRunning ? 'chat.yourTwinTyping' : 'chat.typingOne', { name: runningNames[0] }) : t('chat.typingMany', { names: runningNames.join(', ') })}</span><span className="typing-dots"><i /><i /><i /></span>{ownRunning && <button className="stop-button" disabled={busyKeys.has(ownRunning.id)} onClick={() => cancel(ownRunning)}>{busyKeys.has(ownRunning.id) ? t('chat.stopping') : t('chat.stop')}</button>}</div>}{!!composerQueue.length && <div className="owner-queue"><span>{t('chat.yourQueue')}</span>{composerQueue.map(request => <span className="queue-chip" key={request.id}><Icon name="clock" size={13} /><span>{actors.find(a => a.id === request.target_twin_id)?.name}</span>{request.can_cancel && <button disabled={busyKeys.has(request.id)} onClick={() => cancel(request)} aria-label={t('chat.cancelRequest')}><Icon name="close" size={12} /></button>}</span>)}</div>}{hiddenFailures.map(request => <FailureNotice key={JSON.stringify([actualOwnerId, request.chat_id, request.id])} ownerId={actualOwnerId} request={request} name={actors.find(a => a.id === request.target_twin_id)?.name} busy={busyKeys.has(request.id)} onRetry={retry} composer />)}{replyTo && <div className="composer-reply"><SourceQuote name={actors.find(a => a.id === replyTo.author_id)?.name || ''} body={replyTo.body || replyTo.attachments?.map(attachment => attachment.name).join(' · ') || t('attachments.message')} /><IconButton icon="close" label={t('common.cancel')} onClick={() => setReplyTo(null)} /></div>}{replyForMe && <div className="mode-hint"><Icon name="spark" size={15} />{t('chat.modeHint', { name: ownTwin?.name || '' })}</div>}<DraftAttachmentList items={attachmentDrafts.items} error={attachmentDrafts.error} onRemove={attachmentDrafts.remove} onRetry={attachmentDrafts.retry} onDismissError={attachmentDrafts.dismissError} /><div className={`composer ${expandedComposer ? 'expanded' : ''} ${replyForMe ? 'reply-mode' : ''}`}><textarea ref={input} rows={1} aria-label={t('chat.message')} placeholder={replyForMe ? t('chat.replyPlaceholder') : t('shell.typeMessage')} value={draft} onChange={e => setDrafts(p => ({ ...p, [draftKey]: e.target.value }))} onPaste={event => { const files = Array.from(event.clipboardData.items).filter(item => item.kind === 'file').map(item => item.getAsFile()).filter((file): file is File => !!file); if (files.length) { if (!event.clipboardData.getData('text/plain')) event.preventDefault(); attachmentDrafts.ingest(files); } }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && (!expandedComposer || event.ctrlKey || event.metaKey)) { event.preventDefault(); void send(); } }} /><div className="composer-tools"><div className="composer-menu-wrap" data-floating-menu><IconButton icon="smile" label={t('chat.emoji')} active={composerMenu === 'emoji'} onClick={() => setComposerMenu(previous => previous === 'emoji' ? null : 'emoji')} />{composerMenu === 'emoji' && <div className="emoji-picker" role="dialog" aria-label={t('chat.emoji')}><header><strong>{t('chat.emoji')}</strong><IconButton icon="close" label={t('common.close')} size={16} onClick={() => setComposerMenu(null)} /></header><div className="emoji-grid">{['🙂','😀','😄','😁','😅','😂','😊','😍','🥰','😎','🤔','😴','😢','😭','😮','😬','👍','👎','👏','🙌','👋','🙏','💪','✌️','❤️','💜','💙','💛','🎉','✨','🔥','💯','✅','👀','💡','📌','🌟','☕','🌈','🚀','🐱','🐶','🌸','🌻','🍎','🍕','🎂','⚽'].map(emoji => <button key={emoji} aria-label={emoji} onClick={() => { insertEmoji(emoji); }}>{emoji}</button>)}</div></div>}</div><div className="composer-menu-wrap" data-floating-menu><IconButton icon="attachment" label={`${t('workspace.upload')} (${navigator.platform.includes('Mac') ? '⌥⌘O' : 'Alt+Shift+O'})`} active={composerMenu === 'attachments'} onClick={() => setComposerMenu(previous => previous === 'attachments' ? null : 'attachments')} />{composerMenu === 'attachments' && <div className="fluent-menu composer-popover" role="menu"><button role="menuitem" onClick={() => { setComposerMenu(null); fileInput.current?.click(); }}><Icon name="file" size={19} />{t('attachments.uploadDevice')}</button></div>}</div><IconButton icon="image" label={t('attachments.media')} onClick={() => imageInput.current?.click()} />{activeChat.chat.kind !== 'private' && <button className={`twin-toggle ${replyForMe ? 'active' : ''}`} title={t('chat.replyForMe')} aria-label={t('chat.replyForMe')} aria-pressed={replyForMe} onClick={() => { void setSettings({ reply_for_me_enabled: !replyForMe }); }}><Avatar actor={ownTwin} size={29} /></button>}<div className="composer-menu-wrap" data-floating-menu><IconButton icon="plus" label={t('shell.moreTools')} active={composerMenu === 'more'} onClick={() => setComposerMenu(previous => previous === 'more' ? null : 'more')} />{composerMenu === 'more' && <div className="fluent-menu composer-popover" role="menu"><button role="menuitem" onClick={() => { setExpandedComposer(previous => !previous); setComposerMenu(null); input.current?.focus(); }}><Icon name="edit" size={18} />{t(expandedComposer ? 'attachments.collapse' : 'attachments.expand')}</button><button role="menuitem" onClick={showWorkspace}><Icon name="folder" size={18} />{t('sidebar.workspace')}</button><button role="menuitem" onClick={() => { setComposerMenu(null); setFindOpen(false); void setSettings({ sidebar_open: true, sidebar_tab: 'tracking' }); }}><Icon name="check" size={18} />{t('sidebar.tracking')}</button><button role="menuitem" onClick={() => { setComposerMenu(null); setReminderOpen(true); }}><Icon name="bell" size={18} />{t('reminders.new')}</button></div>}</div><span className="tool-divider" /><button className="icon-button send-button" disabled={!canSend} title={`${t('chat.send')}${expandedComposer ? ` (${navigator.platform.includes('Mac') ? '⌘' : 'Ctrl+'}Enter)` : ''}`} aria-label={t('chat.send')} onClick={() => { void send(); }}><Icon name="send" size={21} /></button></div></div></div></main>{findOpen ? <ChatSearchPanel messages={activeChat.messages} actors={actors} onClose={() => { setFindOpen(false); requestAnimationFrame(() => document.querySelector<HTMLButtonElement>('.header-actions > .icon-button')?.focus()); }} onJump={jumpToMessage} /> : activeChat.settings.sidebar_open && <DetailSidebar key={`${actualOwnerId}:${selected}:${privateResetVersions[selected] || 0}`} api={api} detail={activeChat} actors={actors} version={version} trackingVersion={trackingVersion} ownerId={actualOwnerId} onSettings={patch => { void setSettings(patch); }} onError={onError} onChange={() => { void refreshAll(); }} createFrom={trackingSeed} onClearCreate={() => setTrackingSeed(null)} />}</> : <main className="chat-main"><div className="app-loading">{loading || chatLoading ? <><span className="spinner" />{t('common.loading')}</> : <><Icon name="chat" size={32} /><p>{error || t('chat.noChats')}</p><button className="secondary-button" onClick={() => { setError(''); void loadBootstrap().catch(onError); }}>{t('connection.reload')}</button></>}</div></main>}
  </div>{notificationSettingsOpen && <DesktopNotificationSettings controller={desktopNotifications} onClose={() => setNotificationSettingsOpen(false)} />}{renameChat && <RenameChatDialog api={api} chat={renameChat} onClose={() => setRenameChat(null)} onSaved={chat => { if (current.current.ownerId !== api.ownerId) return; bootstrapSequence.current++; setBootstrap(previous => previous ? { ...previous, chats: previous.chats.map(item => item.id === chat.id ? chat : item) } : previous); if (current.current.selected === chat.id) { chatSequence.current++; setDetail(previous => previous?.chat.id === chat.id ? { ...previous, chat } : previous); void loadChat(chat.id).catch(onError); } }} />}{shortcutsOpen && <Modal title={t('shell.keyboard')} onClose={() => setShortcutsOpen(false)}><p className="keyboard-shortcuts">{t('shell.keyboardDetail')}</p></Modal>}{error && <div className="error-toast" role="alert"><Icon name="info" size={18} /><span>{error}</span><IconButton icon="close" label={t('common.dismiss')} onClick={() => setError('')} /></div>}{peopleModal && <PeopleDialog api={api} ownerId={actualOwnerId} chat={peopleModal.add && activeChat?.chat.kind === 'group' ? activeChat.chat : undefined} initialPerson={peopleModal.person} onClose={() => setPeopleModal(null)} onDone={chat => { setPeopleModal(null); chooseChat(chat.id); void loadBootstrap().catch(onError); if (chat.id === selected) void loadChat().catch(onError); }} onError={onError} />}{settingsOpen && activeChat && <SettingsDialog api={api} detail={activeChat} onClose={() => setSettingsOpen(false)} onSaved={() => { void refreshAll(); }} onError={onError} />}{reminderOpen && <ReminderDialog api={api} onClose={() => setReminderOpen(false)} onDone={() => { void loadBootstrap().catch(onError); }} onError={onError} />}</div>;
}
