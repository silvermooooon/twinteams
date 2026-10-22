import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { Actor, Chat } from './api/client';
import { t, formatDate, formatTime } from './i18n';
import { PresetAvatar } from './TwinAvatars';
import { defaultAvatarColor } from './avatarColors';
export type IconName = 'chat' | 'bell' | 'search' | 'send' | 'smile' | 'edit' | 'check' | 'clock' | 'plus' | 'more' | 'chevron' | 'people' | 'info' | 'close' | 'reply' | 'spark' | 'sidebar' | 'folder' | 'file' | 'arrow' | 'refresh' | 'settings' | 'trash' | 'video' | 'apps' | 'image' | 'attachment' | 'star' | 'panel-left';
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    video: <><rect x="3" y="6" width="12" height="12" rx="2" /><path d="m15 10 6-4v12l-6-4" /></>, apps: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>, image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8" cy="8" r="1.5" /><path d="m3 17 6-6 4 4 3-3 5 5" /></>, attachment: <path d="m8 12 7-7a3 3 0 0 1 4 4L9 19a5 5 0 0 1-7-7L12 2m-7 13 9-9" />, star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z" />, 'panel-left': <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16M5.5 8h1M5.5 11h1M5.5 14h1" /></>,
    plus: <path d="M12 4v16M4 12h16" />, more: <><circle cx="5" cy="12" r=".8" /><circle cx="12" cy="12" r=".8" /><circle cx="19" cy="12" r=".8" /></>, chevron: <path d="m9 5 7 7-7 7" />,
    chat: <><path d="M20 11a8 8 0 0 1-8 8H6l-4 3v-7a8 8 0 1 1 18-4Z" /><path d="M7 8h8M7 12h5" /></>, bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z" /><path d="M9 21h6" /></>, search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>, send: <><path d="m3 3 19 9-19 9 4-9-4-9Z" /><path d="M7 12h15" /></>, smile: <><circle cx="12" cy="12" r="9" /><path d="M8 14s1 3 4 3 4-3 4-3M8 9h.01M16 9h.01" /></>, edit: <><path d="M11 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-6M14 4l4-3 4 4-12 12-5 1 1-5Z" /></>, check: <path d="m5 12 4 4L19 6" />, clock: <><circle cx="12" cy="12" r="9" /><path d="M12 6v6l4 2" /></>, people: <><circle cx="9" cy="6" r="3" /><path d="M2 21v-3a7 7 0 0 1 14 0v3ZM17 3a3 3 0 0 1 0 6M18 13a6 6 0 0 1 4 5v3" /></>, info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>, close: <path d="m6 6 12 12M6 18 18 6" />, reply: <><path d="m9 5-7 7 7 7M2 12h12a7 7 0 0 1 7 7" /></>, spark: <><path d="m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5Z" /><path d="m20 2 1 2 2 1-2 1-1 2-1-2-2-1 2-1Z" /></>, sidebar: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M15 3v18" /></>, folder: <path d="M3 5h7l2 3h9v12H3Z" />, file: <><path d="M5 2h9l5 5v15H5ZM14 2v6h5M8 13h8M8 17h6" /></>, arrow: <path d="M4 12h16m-6-6 6 6-6 6" />, refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2" /></>, settings: <><path d="m10 2-1 3-3 1-3-1-2 4 3 2v3l-3 2 2 4 3-1 3 1 1 3h4l1-3 3-1 3 1 2-4-3-2v-3l3-2-2-4-3 1-3-1-1-3Z" /><circle cx="12" cy="12" r="3" /></>, trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
export function BrandMark() { return <span className="brand-mark" aria-hidden="true"><i /><i /><b>T</b></span>; }
export function Avatar({ actor, size = 36, twin = false, group = false }: { actor?: Actor; size?: number; twin?: boolean; group?: boolean }) {
  const isTwin = twin || actor?.kind === 'twin';
  const [failedUrl, setFailedUrl] = useState('');
  const fallback = !group && !actor?.avatar_preset && (!actor?.avatar_url || failedUrl === actor.avatar_url);
  const colors = fallback && actor ? defaultAvatarColor(actor.name || actor.id) : undefined;
  return <span className={`avatar ${isTwin ? 'twin' : ''} ${group ? 'group-avatar' : ''}`} style={{ width: size, height: size, backgroundColor: colors?.backgroundColor || (!isTwin ? actor?.color || undefined : undefined), color: colors?.color, fontSize: size * .33 }} aria-hidden="true">{group ? <Icon name="people" size={size * .55} /> : actor?.avatar_preset ? <PresetAvatar preset={actor.avatar_preset} /> : actor?.avatar_url && failedUrl !== actor.avatar_url ? <img src={actor.avatar_url} alt="" onError={() => setFailedUrl(actor.avatar_url || '')} /> : isTwin ? <BrandMark /> : actor?.initials || '·'}{isTwin && <span className="twin-badge">✧</span>}</span>;
}
export function chatTitle(chat: Chat, actors: Actor[], ownerId: string) {
  const peer = chat.kind === 'group' && chat.member_owner_ids.length === 2 ? chat.member_owner_ids.find(id => id !== ownerId) : null;
  return actors.find(actor => actor.kind === 'human' && actor.id === peer)?.name || chat.title;
}
export function ChatAvatar({ chat, actors, ownerId, size = 40 }: { chat: Chat; actors: Actor[]; ownerId: string; size?: number }) {
  const peer = chat.kind === 'group' && chat.member_owner_ids.length === 2 ? chat.member_owner_ids.find(id => id !== ownerId) : null;
  const group = chat.kind === 'group' && chat.member_owner_ids.length >= 3;
  const actor = group ? undefined : peer ? actors.find(item => item.kind === 'human' && item.id === peer) : actors.find(item => item.kind === 'twin' && item.owner_id === chat.member_owner_ids[0]);
  return <Avatar actor={actor} size={size} group={group} />;
}
export function IconButton({ icon, label, onClick, disabled, active, size = 20 }: { icon: IconName; label: string; onClick?: () => void; disabled?: boolean; active?: boolean; size?: number }) { return <button type="button" className={`icon-button ${active ? 'active' : ''}`} title={label} aria-label={label} onClick={onClick} disabled={disabled}><Icon name={icon} size={size} /></button>; }
export function Modal({ title, children, onClose, wide = false, closeDisabled = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean; closeDisabled?: boolean }) {
  const container = useRef<HTMLElement>(null);
  const close = useRef(onClose); const disabled = useRef(closeDisabled); close.current = onClose; disabled.current = closeDisabled;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => { if (container.current?.contains(document.activeElement)) return; (container.current?.querySelector('input:not(:disabled):not([hidden]):not([type="checkbox"]),textarea:not(:disabled),select:not(:disabled)') as HTMLElement | null || container.current)?.focus({ preventScroll: true }); });
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); if (!disabled.current) close.current(); return; }
      if (event.key !== 'Tab' || !container.current) return;
      const focusable = [...container.current.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled):not([hidden]),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]')].filter(element => element.offsetParent !== null);
      const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (!first) { event.preventDefault(); container.current.focus(); }
      else if (event.shiftKey && (document.activeElement === first || !container.current.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !container.current.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('keydown', handleKey); if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, []);
  return <div className="twins-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !closeDisabled) onClose(); }}><section ref={container} tabIndex={-1} className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}><div className="modal-heading"><h2>{title}</h2><IconButton icon="close" label={t('common.close')} onClick={onClose} disabled={closeDisabled} /></div>{children}</section></div>;
}
export function Empty({ icon, title, detail }: { icon: IconName; title: string; detail?: string }) { return <div className="empty-state"><Icon name={icon} size={28} /><h3>{title}</h3>{detail && <p>{detail}</p>}</div>; }
export function time(value?: string | null) { if (!value) return ''; const date = new Date(value); return isNaN(date.getTime()) ? value : formatTime(date); }
export function dateTime(value?: string | null) { if (!value) return ''; const date = new Date(value); return isNaN(date.getTime()) ? value : formatDate(date, { dateStyle: 'medium', timeStyle: 'short' }); }

/** Server-declared test mode only. Never enables a provider or generates responses. */
export function FixtureBanner() { return <div className="acceptance-fixture-banner" role="status"><Icon name="info" size={16} /><strong>{t('config.fixtureTitle')}</strong><span>{t('config.fixtureDetail')}</span></div>; }

/** Shared keyboard behavior for anchored Fluent menus. Popover state remains owned by callers. */
export function useMenuBehavior() {
  useEffect(() => {
    const triggers = new Map<HTMLElement, HTMLElement | null>();
    const frames = new Set<number>();
    const observer = new MutationObserver(() => {
      document.querySelectorAll<HTMLElement>('[role="menu"],.emoji-picker').forEach(menu => {
        if (triggers.has(menu)) return;
        const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        triggers.set(menu, active);
        const frame = requestAnimationFrame(() => { frames.delete(frame); if (menu.isConnected) menu.querySelector<HTMLElement>('button:not(:disabled),input')?.focus({ preventScroll: true }); });
        frames.add(frame);
      });
      for (const [menu, trigger] of triggers) if (!menu.isConnected) {
        if (trigger?.isConnected && (document.activeElement === document.body || menu.contains(document.activeElement))) trigger.focus({ preventScroll: true });
        triggers.delete(menu);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement; const menu = target.closest<HTMLElement>('[role="menu"],.emoji-picker');
      if (!menu) return;
      const buttons = [...menu.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
      const current = buttons.indexOf(target as HTMLButtonElement);
      let next = -1;
      if (event.key === 'ArrowDown') next = (current + 1) % buttons.length;
      if (event.key === 'ArrowUp') next = (current - 1 + buttons.length) % buttons.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = buttons.length - 1;
      if (next >= 0) { event.preventDefault(); buttons[next]?.focus(); }
      if (event.key === 'Escape') { event.preventDefault(); triggers.get(menu)?.focus({ preventScroll: true }); }
    };
    document.addEventListener('keydown', key);
    return () => { observer.disconnect(); frames.forEach(cancelAnimationFrame); document.removeEventListener('keydown', key); };
  }, []);
}
