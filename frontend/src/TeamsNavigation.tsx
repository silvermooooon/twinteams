import { useEffect, useRef, useState } from 'react';
import type { Actor, Chat, Message } from './api/client';
import { Avatar, ChatAvatar, chatTitle, Empty, Icon, IconButton } from './ui';
import { t } from './i18n';

export function ChatNavigation({
  chats,
  actors,
  loading,
  selected,
  newChat,
  onChoose,
  onNew,
  onNewBot,
  onNewRoom,
  onEditBot,
  onShareBot,
  ownerId,
}: {
  onNewBot: () => void;
  onNewRoom: () => void;
  onEditBot: (id: string) => void;
  onShareBot: (id: string) => void;
  ownerId: string;
  chats: Chat[];
  actors: Actor[];
  loading: boolean;
  selected: string;
  newChat: boolean;
  onChoose: (id: string) => void;
  onNew: () => void;
}) {
  const [query, setQuery] = useState('');
  const [unread, setUnread] = useState(false);
  const [groupOnly, setGroupOnly] = useState(false);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [favorites, setFavorites] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(`twinteams.favorites:${ownerId}`) || '[]');
      return Array.isArray(saved) ? saved.filter((id): id is string => typeof id === 'string') : [];
    } catch {
      return [];
    }
  });
  const [menu, setMenu] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    localStorage.setItem(`twinteams.favorites:${ownerId}`, JSON.stringify(favorites));
  }, [favorites, ownerId]);

  useEffect(() => {
    const close = (event: PointerEvent) => {
      if (!(event.target as Element).closest('[data-chat-menu]')) setMenu(null);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenu(null);
        if (document.activeElement === searchRef.current) {
          setQuery('');
          searchRef.current?.blur();
        }
      }
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', key);
    };
  }, []);

  const filtering = unread || groupOnly || favoritesOnly || !!query.trim();
  const clearFilters = () => {
    setUnread(false);
    setGroupOnly(false);
    setFavoritesOnly(false);
    setQuery('');
  };

  const filtered = chats.filter(
    chat =>
      chat.kind !== 'duet' &&
      (!groupOnly || (chat.kind === 'group' && chat.member_owner_ids.length >= 3)) &&
      (!favoritesOnly || chat.kind === 'private' || favorites.includes(chat.id)) &&
      (!unread || chat.unread_count > 0) &&
      (!query.trim() ||
        `${chatTitle(chat, actors, ownerId)} ${chat.last_message || ''}`
          .toLocaleLowerCase()
          .includes(query.trim().toLocaleLowerCase())),
  );

  const pinned = (chat: Chat) => chat.kind === 'private' || favorites.includes(chat.id);

  const row = (chat: Chat) => {
    const isSelected = selected === chat.id && !newChat;
    return (
      <div className={`chat-list-row ${isSelected ? 'selected' : ''}`} key={chat.id}>
        <button
          className="chat-item"
          aria-current={isSelected ? 'page' : undefined}
          onClick={() => onChoose(chat.id)}
        >
          <ChatAvatar chat={chat} actors={actors} ownerId={ownerId} size={36} />
          <span className="chat-copy">
            <span className={`chat-name ${chat.unread_count ? 'unread' : ''}`}>
              {chatTitle(chat, actors, ownerId)}
            </span>
            <span className="chat-preview">
              {chat.last_message ||
                t(
                  chat.kind === 'private'
                    ? 'chat.private'
                    : chat.kind === 'bot'
                      ? 'bots.conversation'
                      : chat.kind === 'internal'
                        ? 'bots.roomConversation'
                        : 'chat.shared',
                )}
            </span>
          </span>
          {chat.unread_count > 0 && <span className="chat-unread">{chat.unread_count}</span>}
        </button>
        <div className="chat-row-menu" data-chat-menu>
          <button
            type="button"
            className="icon-button"
            aria-label={t('shell.chatOptions')}
            title={t('shell.chatOptions')}
            aria-expanded={menu === chat.id}
            onClick={() => setMenu(menu === chat.id ? null : chat.id)}
          >
            <Icon name="more" size={16} />
          </button>
          {menu === chat.id && (
            <div className="fluent-menu duet-glass-overlay row-context" role="menu">
              {chat.kind === 'bot' ? (
                <>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      onEditBot(chat.bot_id!);
                      setMenu(null);
                    }}
                  >
                    <Icon name="edit" size={16} />
                    {t('bots.edit')}
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      onShareBot(chat.bot_id!);
                      setMenu(null);
                    }}
                  >
                    <Icon name="arrow" size={16} />
                    {t('botShare.title')}
                  </button>
                </>
              ) : (
                chat.kind !== 'internal' && (
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setFavorites(previous =>
                        previous.includes(chat.id)
                          ? previous.filter(id => id !== chat.id)
                          : [...previous, chat.id],
                      );
                      setMenu(null);
                    }}
                    disabled={chat.kind === 'private'}
                  >
                    <Icon name="star" size={16} />
                    {t(favorites.includes(chat.id) ? 'shell.unfavorite' : 'shell.favorite')}
                  </button>
                )
              )}
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  onChoose(chat.id);
                  setMenu(null);
                }}
              >
                <Icon name="chat" size={16} />
                {t('shell.openChat')}
              </button>
            </div>
          )}
        </div>
      </div>
    );
  };

  const sections = (['favorites', 'chats', 'bots', 'rooms'] as const).map(section => {
    const rows = filtered.filter(chat =>
      section === 'bots'
        ? chat.kind === 'bot'
        : section === 'rooms'
          ? chat.kind === 'internal'
          : ['private', 'group'].includes(chat.kind) &&
            (section === 'favorites' ? pinned(chat) : !pinned(chat)),
    );
    return { section, rows };
  });

  const showFilterEmpty = filtering && !filtered.length && !newChat;
  const showNoChatsEmpty = !filtering && !filtered.length && !newChat;

  return (
    <>
      <div className="sidebar-tools">
        <div className="list-search">
          <Icon name="search" size={15} />
          <input
            ref={searchRef}
            value={query}
            onChange={event => setQuery(event.target.value)}
            placeholder={t('shell.filterChats')}
            aria-label={t('shell.filterChats')}
          />
          {query && (
            <IconButton
              icon="close"
              size={13}
              label={t('common.close')}
              onClick={() => setQuery('')}
            />
          )}
        </div>
        <button
          type="button"
          className={`sidebar-new-button ${newChat ? 'active' : ''}`}
          title={t('search.find')}
          aria-label={t('search.find')}
          onClick={onNew}
        >
          <Icon name="edit" size={15} />
        </button>
      </div>

      <div className="filters" role="group" aria-label={t('shell.filterChats')}>
        <button
          type="button"
          className={`pill ${unread ? 'selected' : ''}`}
          aria-pressed={unread}
          onClick={() => setUnread(previous => !previous)}
        >
          {t('chat.unread')}
        </button>
        <button
          type="button"
          className={`pill ${favoritesOnly ? 'selected' : ''}`}
          aria-pressed={favoritesOnly}
          onClick={() => setFavoritesOnly(previous => !previous)}
        >
          {t('shell.favorites')}
        </button>
        <button
          type="button"
          className={`pill ${groupOnly ? 'selected' : ''}`}
          aria-pressed={groupOnly}
          onClick={() => setGroupOnly(previous => !previous)}
        >
          {t('shell.groupChats')}
        </button>
      </div>

      <div className="conversation-list">
        {loading ? (
          <div className="loading-row">{t('common.loading')}</div>
        ) : showFilterEmpty ? (
          <Empty
            icon={unread ? 'check' : 'chat'}
            title={t(unread ? 'chat.noUnread' : 'shell.noMatchingChats')}
            detail={query.trim() ? undefined : t('chat.noChats')}
            action={
              <button type="button" className="secondary-button" onClick={clearFilters}>
                {t('shell.clearFilters')}
              </button>
            }
          />
        ) : showNoChatsEmpty ? (
          <Empty
            icon="chat"
            title={t('chat.noChats')}
            detail={t('shell.newDetail')}
            action={
              <button type="button" className="primary-button" onClick={onNew}>
                {t('search.find')}
              </button>
            }
          />
        ) : (
          sections.map(({ section, rows }) => {
            const personal = section === 'bots' || section === 'rooms';
            const hasNewChatRow = section === 'chats' && newChat;
            // Collapse empty section shells whenever a filter/search is active,
            // or when Favorites/Chat has no rows.
            if (filtering && !rows.length && !hasNewChatRow) return null;
            if (!personal && !rows.length && !hasNewChatRow) return null;

            const create = section === 'bots' ? onNewBot : onNewRoom;
            return (
              <section
                key={section}
                className={`chat-list-section ${personal ? 'bot-list-section' : ''}`}
              >
                <div className={personal ? 'bot-section-heading' : 'chat-section-heading'}>
                  <button
                    type="button"
                    className="section-label"
                    aria-expanded={!collapsed[section]}
                    onClick={() =>
                      setCollapsed(previous => ({ ...previous, [section]: !previous[section] }))
                    }
                  >
                    <Icon name="chevron" size={11} />
                    <span>
                      {t(
                        section === 'favorites'
                          ? 'shell.favorites'
                          : section === 'bots'
                            ? 'bots.title'
                            : section === 'rooms'
                              ? 'bots.rooms'
                              : 'nav.chat',
                      )}
                    </span>
                  </button>
                  {personal && (
                    <IconButton
                      icon="plus"
                      size={14}
                      label={t(section === 'bots' ? 'bots.create' : 'bots.newRoom')}
                      onClick={create}
                    />
                  )}
                </div>
                {!collapsed[section] && (
                  <div className="chat-section-rows">
                    {rows.map(row)}
                    {hasNewChatRow && (
                      <div className="chat-list-row selected">
                        <button
                          type="button"
                          className="chat-item new-chat-row"
                          onClick={onNew}
                          aria-current="page"
                        >
                          <span className="new-chat-avatar">
                            <Icon name="chat" size={16} />
                          </span>
                          <span className="chat-copy">
                            <span className="chat-name">{t('shell.newMessage')}</span>
                          </span>
                        </button>
                      </div>
                    )}
                    {personal && !rows.length && (
                      <div className="bot-section-empty">
                        <p>{t(section === 'bots' ? 'bots.emptyDetail' : 'bots.roomEmpty')}</p>
                      </div>
                    )}
                  </div>
                )}
              </section>
            );
          })
        )}
      </div>
    </>
  );
}

export function ChatSearchPanel({
  messages,
  actors,
  onClose,
  onJump,
}: {
  messages: Message[];
  actors: Actor[];
  onClose: () => void;
  onJump: (id: string) => void;
}) {
  const [query, setQuery] = useState('');
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  const matches = query.trim()
    ? messages.filter(
        message =>
          message.state === 'published' &&
          `${message.body} ${(message.attachments || []).map(attachment => attachment.name).join(' ')}`
            .toLocaleLowerCase()
            .includes(query.trim().toLocaleLowerCase()),
      )
    : [];

  return (
    <aside
      className="chat-find-panel duet-glass"
      data-duet-refract="0.55"
      onKeyDown={event => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          onClose();
        }
      }}
      aria-label={t('shell.findInChat')}
    >
      <header>
        <h2>{t('shell.findInChat')}</h2>
        <IconButton icon="close" size={16} label={t('common.close')} onClick={onClose} />
      </header>
      <div className="chat-find-content">
        <div className="list-search">
          <Icon name="search" size={15} />
          <input
            ref={input}
            value={query}
            placeholder={t('shell.findMessage')}
            aria-label={t('shell.findMessage')}
            onChange={event => setQuery(event.target.value)}
          />
          {query && (
            <IconButton
              icon="close"
              size={13}
              label={t('common.close')}
              onClick={() => setQuery('')}
            />
          )}
        </div>
        <p className="search-scope">{t('shell.searchScope')}</p>
        {query.trim() ? (
          <>
            <h3>{t('shell.searchResults', { count: matches.length })}</h3>
            <div className="message-search-results">
              {matches.map(message => (
                <button key={message.id} onClick={() => onJump(message.id)}>
                  <Avatar actor={actors.find(actor => actor.id === message.author_id)} size={28} />
                  <span>
                    <strong>{actors.find(actor => actor.id === message.author_id)?.name}</strong>
                    <span>
                      {message.body ||
                        message.attachments?.map(attachment => attachment.name).join(' · ')}
                    </span>
                  </span>
                </button>
              ))}
              {!matches.length && <p>{t('shell.noMessages')}</p>}
            </div>
          </>
        ) : (
          <Empty
            icon="search"
            title={t('shell.findHeading')}
            detail={t('shell.findDetail')}
          />
        )}
      </div>
    </aside>
  );
}
