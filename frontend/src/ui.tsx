import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import type { Actor, Chat } from './api/client';
import { t, formatDate, formatTime } from './i18n';
export type IconName = 'chat' | 'bell' | 'search' | 'send' | 'smile' | 'edit' | 'check' | 'clock' | 'plus' | 'more' | 'chevron' | 'people' | 'info' | 'close' | 'reply' | 'spark' | 'sidebar' | 'folder' | 'file' | 'arrow' | 'refresh' | 'settings' | 'trash';
export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    plus: <path d="M12 4v16M4 12h16" />, more: <><circle cx="5" cy="12" r=".8" /><circle cx="12" cy="12" r=".8" /><circle cx="19" cy="12" r=".8" /></>, chevron: <path d="m9 5 7 7-7 7" />,
    chat: <><path d="M20 11a8 8 0 0 1-8 8H6l-4 3v-7a8 8 0 1 1 18-4Z" /><path d="M7 8h8M7 12h5" /></>, bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z" /><path d="M9 21h6" /></>, search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>, send: <><path d="m3 3 19 9-19 9 4-9-4-9Z" /><path d="M7 12h15" /></>, smile: <><circle cx="12" cy="12" r="9" /><path d="M8 14s1 3 4 3 4-3 4-3M8 9h.01M16 9h.01" /></>, edit: <><path d="M11 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-6M14 4l4-3 4 4-12 12-5 1 1-5Z" /></>, check: <path d="m5 12 4 4L19 6" />, clock: <><circle cx="12" cy="12" r="9" /><path d="M12 6v6l4 2" /></>, people: <><circle cx="9" cy="6" r="3" /><path d="M2 21v-3a7 7 0 0 1 14 0v3ZM17 3a3 3 0 0 1 0 6M18 13a6 6 0 0 1 4 5v3" /></>, info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>, close: <path d="m6 6 12 12M6 18 18 6" />, reply: <><path d="m9 5-7 7 7 7M2 12h12a7 7 0 0 1 7 7" /></>, spark: <><path d="m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5Z" /><path d="m20 2 1 2 2 1-2 1-1 2-1-2-2-1 2-1Z" /></>, sidebar: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M15 3v18" /></>, folder: <path d="M3 5h7l2 3h9v12H3Z" />, file: <><path d="M5 2h9l5 5v15H5ZM14 2v6h5M8 13h8M8 17h6" /></>, arrow: <path d="M4 12h16m-6-6 6 6-6 6" />, refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2" /></>, settings: <><path d="m10 2-1 3-3 1-3-1-2 4 3 2v3l-3 2 2 4 3-1 3 1 1 3h4l1-3 3-1 3 1 2-4-3-2v-3l3-2-2-4-3 1-3-1-1-3Z" /><circle cx="12" cy="12" r="3" /></>, trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
export function BrandMark() { return <span className="brand-mark" aria-hidden="true"><i /><i /><b>T</b></span>; }
export function Avatar({ actor, size = 36, twin = false, group = false }: { actor?: Actor; size?: number; twin?: boolean; group?: boolean }) {
  const isTwin = twin || actor?.kind === 'twin';
  return <span className={`avatar ${isTwin ? 'twin' : ''} ${group ? 'group-avatar' : ''}`} style={{ width: size, height: size, backgroundColor: actor?.color || undefined, fontSize: size * .33 }} aria-hidden="true">{group ? <Icon name="people" size={size * .55} /> : isTwin ? <BrandMark /> : actor?.initials || '·'}{isTwin && <span className="twin-badge">✧</span>}</span>;
}
export function ChatAvatar({ chat, actors, size = 40 }: { chat: Chat; actors: Actor[]; size?: number }) { return <Avatar actor={actors.find(a => a.kind === 'twin' && a.owner_id === chat.member_owner_ids[0])} size={size} group={chat.kind === 'group'} />; }
export function IconButton({ icon, label, onClick, disabled, active, size = 20 }: { icon: IconName; label: string; onClick?: () => void; disabled?: boolean; active?: boolean; size?: number }) { return <button type="button" className={`icon-button ${active ? 'active' : ''}`} title={label} aria-label={label} onClick={onClick} disabled={disabled}><Icon name={icon} size={size} /></button>; }
export function Modal({ title, children, onClose, wide = false, closeDisabled = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean; closeDisabled?: boolean }) {
  const container = useRef<HTMLElement>(null);
  const close = useRef(onClose); const disabled = useRef(closeDisabled); close.current = onClose; disabled.current = closeDisabled;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => { (container.current?.querySelector('input:not([hidden]):not([type="checkbox"]),textarea,select') as HTMLElement | null || container.current)?.focus({ preventScroll: true }); });
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
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !closeDisabled) onClose(); }}><section ref={container} tabIndex={-1} className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}><div className="modal-heading"><h2>{title}</h2><IconButton icon="close" label={t('common.close')} onClick={onClose} disabled={closeDisabled} /></div>{children}</section></div>;
}
export function Empty({ icon, title, detail }: { icon: IconName; title: string; detail?: string }) { return <div className="empty-state"><Icon name={icon} size={28} /><h3>{title}</h3>{detail && <p>{detail}</p>}</div>; }
export function time(value?: string | null) { if (!value) return ''; const date = new Date(value); return isNaN(date.getTime()) ? value : formatTime(date); }
export function dateTime(value?: string | null) { if (!value) return ''; const date = new Date(value); return isNaN(date.getTime()) ? value : formatDate(date, { dateStyle: 'medium', timeStyle: 'short' }); }

/** Server-declared test mode only. Never enables a provider or generates responses. */
export function FixtureBanner() { return <div className="acceptance-fixture-banner" role="status"><Icon name="info" size={16} /><strong>{t('config.fixtureTitle')}</strong><span>{t('config.fixtureDetail')}</span></div>; }
