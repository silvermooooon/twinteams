import { useEffect, useRef, useState } from 'react';
import { LiquiGlass } from '@liqui-design/glass';
import { Menu, MenuContent, MenuItem, MenuTrigger } from '@/components/ui/menu';
import { Toggle } from '@/components/ui/toggle';
import { LIQUI_MENU_GLASS, LIQUI_PANEL_GLASS } from '@/lib/liquiPresets';
import type { Actor, Chat } from './api/client';
import { Avatar, ChatAvatar, chatTitle, Icon, IconButton } from './ui';
import { t } from './i18n';

export function ChatNavigation({ chats, actors, loading, selected, newChat, onChoose, onNew, onNewBot, onNewRoom, onEditBot, onShareBot, ownerId }: { onNewBot: () => void; onNewRoom: () => void; onEditBot: (id: string) => void; onShareBot: (id: string) => void; ownerId: string; chats: Chat[]; actors: Actor[]; loading: boolean; selected: string; newChat: boolean; onChoose: (id: string) => void; onNew: () => void }) {
  const [query, setQuery] = useState(''); const [search, setSearch] = useState(false); const [unread, setUnread] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [favorites, setFavorites] = useState<string[]>(() => { try { const saved: unknown = JSON.parse(localStorage.getItem(`twinteams.favorites:${ownerId}`) || '[]'); return Array.isArray(saved) ? saved.filter((id): id is string => typeof id === 'string') : []; } catch { return []; } }); const [groupOnly, setGroupOnly] = useState(false); const [favoritesOnly, setFavoritesOnly] = useState(false); const [menu, setMenu] = useState<string | null>(null); const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => { localStorage.setItem(`twinteams.favorites:${ownerId}`, JSON.stringify(favorites)); }, [favorites, ownerId]);
  useEffect(() => { if (search) searchRef.current?.focus(); }, [search]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { setSearch(false); setQuery(''); } };
    document.addEventListener('keydown', key);
    return () => { document.removeEventListener('keydown', key); };
  }, []);
  const filtered = chats.filter(chat => chat.kind !== 'duet' && (!groupOnly || chat.kind === 'group' && chat.member_owner_ids.length >= 3) && (!favoritesOnly || chat.kind === 'private' || favorites.includes(chat.id)) && (!unread || chat.unread_count > 0) && (!query.trim() || `${chatTitle(chat, actors, ownerId)} ${chat.last_message || ''}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
  const pinned = (chat: Chat) => chat.kind === 'private' || favorites.includes(chat.id);
  const row = (chat: Chat) => <div className={`chat-list-row ${selected === chat.id && !newChat ? 'selected' : ''} ${menu === chat.id ? 'menu-open' : ''}`} key={chat.id}>
    <button className="chat-item lg-interactive" aria-current={selected === chat.id && !newChat ? 'page' : undefined} onClick={() => onChoose(chat.id)}><ChatAvatar chat={chat} actors={actors} ownerId={ownerId} size={36} /><span className="chat-copy"><span className={`chat-name ${chat.unread_count ? 'unread' : ''}`}>{chatTitle(chat, actors, ownerId)}</span><span className="chat-preview">{chat.last_message || t(chat.kind === 'private' ? 'chat.private' : chat.kind === 'bot' ? 'bots.conversation' : chat.kind === 'internal' ? 'bots.roomConversation' : 'chat.shared')}</span></span>{chat.unread_count > 0 && <span className="chat-unread">{chat.unread_count}</span>}</button>
    <div className="chat-row-menu" data-chat-menu>
      <Menu open={menu === chat.id} onOpenChange={open => setMenu(open ? chat.id : null)}>
        <MenuTrigger className="icon-button lg-interactive" aria-label={t('shell.chatOptions')} title={t('shell.chatOptions')}>
          <Icon name="more" size={17} />
        </MenuTrigger>
        <MenuContent glass={LIQUI_MENU_GLASS} align="end" sideOffset={6} className="lq-sheet-surface lq-menu-popup min-w-[196px]">
          {chat.kind === 'bot' ? <>
            <MenuItem onClick={() => { onEditBot(chat.bot_id!); setMenu(null); }}><Icon name="edit" size={16} />{t('bots.edit')}</MenuItem>
            <MenuItem onClick={() => { onShareBot(chat.bot_id!); setMenu(null); }}><Icon name="arrow" size={16} />{t('botShare.title')}</MenuItem>
          </> : chat.kind !== 'internal' ? (
            <MenuItem disabled={chat.kind === 'private'} onClick={() => { setFavorites(previous => previous.includes(chat.id) ? previous.filter(id => id !== chat.id) : [...previous, chat.id]); setMenu(null); }}><Icon name="star" size={16} />{t(favorites.includes(chat.id) ? 'shell.unfavorite' : 'shell.favorite')}</MenuItem>
          ) : null}
          <MenuItem onClick={() => { onChoose(chat.id); setMenu(null); }}><Icon name="chat" size={16} />{t('shell.openChat')}</MenuItem>
        </MenuContent>
      </Menu>
    </div>
  </div>;
  return <><div className="sidebar-heading"><h1>{t('nav.chat')}</h1><div className="sidebar-heading-actions lq-inline-cluster"><IconButton icon="search" label={t('shell.filterChats')} active={search} onClick={() => setSearch(previous => !previous)} /><IconButton icon="edit" label={t('search.find')} active={newChat} onClick={onNew} /></div></div>
    {search && <div className="list-search"><Icon name="search" size={16} /><input ref={searchRef} value={query} onChange={event => setQuery(event.target.value)} placeholder={t('shell.filterChats')} aria-label={t('shell.filterChats')} /><IconButton icon="close" size={14} label={t('common.close')} onClick={() => { setSearch(false); setQuery(''); }} /></div>}
    <div className="filters"><Toggle pressed={unread} onPressedChange={setUnread} className="lq-filter-pill">{t('chat.unread')}</Toggle><Toggle pressed={favoritesOnly} onPressedChange={setFavoritesOnly} className="lq-filter-pill">{t('shell.favorites')}</Toggle><Toggle pressed={groupOnly} onPressedChange={setGroupOnly} className="lq-filter-pill">{t('shell.groupChats')}</Toggle></div>
    <div className="conversation-list">{loading ? <div className="loading-row">{t('common.loading')}</div> : <>{(['favorites', 'chats', 'bots', 'rooms'] as const).map(section => {
      const rows = filtered.filter(chat => section === 'bots' ? chat.kind === 'bot' : section === 'rooms' ? chat.kind === 'internal' : ['private', 'group'].includes(chat.kind) && (section === 'favorites' ? pinned(chat) : !pinned(chat)));
      const personal = section === 'bots' || section === 'rooms';
      const create = section === 'bots' ? onNewBot : onNewRoom;
      return <section key={section} className={`chat-list-section ${personal ? 'bot-list-section' : ''}`}><div className={personal ? 'bot-section-heading' : undefined}><button className="section-label" aria-expanded={!collapsed[section]} onClick={() => setCollapsed(previous => ({ ...previous, [section]: !previous[section] }))}><Icon name="chevron" size={12} /><span>{t(section === 'favorites' ? 'shell.favorites' : section === 'bots' ? 'bots.title' : section === 'rooms' ? 'bots.rooms' : 'nav.chat')}</span></button>{personal && <IconButton icon="plus" size={16} label={t(section === 'bots' ? 'bots.create' : 'bots.newRoom')} onClick={create} />}</div>{!collapsed[section] && <div className="chat-section-rows">{rows.map(row)}{section === 'chats' && newChat && <button className="chat-item lg-interactive selected new-chat-row" onClick={onNew} aria-current="page"><span className="new-chat-avatar"><Icon name="chat" size={18} /></span><span>{t('shell.newMessage')}</span></button>}{personal && !rows.length && <div className="bot-section-empty"><p>{t(query || unread || groupOnly || favoritesOnly ? 'shell.noMatchingChats' : section === 'bots' ? 'bots.emptyDetail' : 'bots.roomEmpty')}</p></div>}</div>}</section>;
    })}{!filtered.length && !newChat && <div className="sidebar-empty"><span className="empty-state-icon lg-glass-pill"><Icon name={unread ? 'check' : 'chat'} size={24} /></span><p>{t(unread ? 'chat.noUnread' : query ? 'shell.noMatchingChats' : 'chat.noChats')}</p></div>}</>}</div></>;

}

export function ChatSearchPanel({ messages, actors, onClose, onJump }: { messages: import('./api/client').Message[]; actors: Actor[]; onClose: () => void; onJump: (id: string) => void }) {
  const [query, setQuery] = useState(''); const input = useRef<HTMLInputElement>(null);
  useEffect(() => { input.current?.focus(); }, []);
  const matches = query.trim() ? messages.filter(message => message.state === 'published' && `${message.body} ${(message.attachments || []).map(attachment => attachment.name).join(' ')}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())) : [];
  return <LiquiGlass {...LIQUI_PANEL_GLASS} frost={0.42} className="chat-find-panel lq-panel-surface" contentClassName="chat-find-panel-inner" role="complementary" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); } }} aria-label={t('shell.findInChat')}><header><h2>{t('shell.findInChat')}</h2><IconButton icon="close" label={t('common.close')} onClick={onClose} /></header><div className="chat-find-content"><div className="list-search"><Icon name="search" size={18} /><input ref={input} value={query} placeholder={t('shell.findMessage')} aria-label={t('shell.findMessage')} onChange={event => setQuery(event.target.value)} /></div><p className="search-scope">{t('shell.searchScope')}</p>{query.trim() ? <><h3>{t('shell.searchResults', { count: matches.length })}</h3><div className="message-search-results">{matches.map(message => <button className="lg-interactive" key={message.id} onClick={() => onJump(message.id)}><Avatar actor={actors.find(actor => actor.id === message.author_id)} size={28} /><span><strong>{actors.find(actor => actor.id === message.author_id)?.name}</strong><span>{message.body || message.attachments?.map(attachment => attachment.name).join(' · ')}</span></span></button>)}{!matches.length && <p>{t('shell.noMessages')}</p>}</div></> : <div className="find-instructions"><span className="empty-state-icon lg-glass-pill"><Icon name="search" size={32} /></span><h3>{t('shell.findHeading')}</h3><p>{t('shell.findDetail')}</p></div>}</div></LiquiGlass>;
}
