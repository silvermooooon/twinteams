import { Children, cloneElement, isValidElement, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactElement, ReactNode, RefObject } from 'react';
import { createPortal } from 'react-dom';
import type { Actor, Chat } from './api/client';
import { t, formatDate, formatTime } from './i18n';
import { PresetAvatar } from './TwinAvatars';
import { defaultAvatarColor } from './avatarColors';
import { humanizeTechnicalError } from './errorFormat';

export type IconName =
  | 'chat'
  | 'bell'
  | 'search'
  | 'send'
  | 'smile'
  | 'edit'
  | 'check'
  | 'clock'
  | 'plus'
  | 'more'
  | 'chevron'
  | 'people'
  | 'info'
  | 'close'
  | 'reply'
  | 'spark'
  | 'sidebar'
  | 'folder'
  | 'file'
  | 'arrow'
  | 'refresh'
  | 'settings'
  | 'trash'
  | 'video'
  | 'apps'
  | 'image'
  | 'attachment'
  | 'star'
  | 'panel-left'
  | 'play'
  | 'pause'
  | 'stop'
  | 'upload';

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, ReactNode> = {
    upload: <><path d="M12 16V3m-5 5 5-5 5 5" /><path d="M4 16v4h16v-4" /></>,
    play: <path d="m8 5 11 7-11 7Z" />,
    pause: <><rect x="6" y="5" width="4" height="14" rx=".5" /><rect x="14" y="5" width="4" height="14" rx=".5" /></>,
    stop: <rect x="6" y="6" width="12" height="12" rx="1" />,
    video: <><rect x="3" y="6" width="12" height="12" rx="2" /><path d="m15 10 6-4v12l-6-4" /></>,
    apps: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
    image: <><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8" cy="8" r="1.5" /><path d="m3 17 6-6 4 4 3-3 5 5" /></>,
    attachment: <path d="m8 12 7-7a3 3 0 0 1 4 4L9 19a5 5 0 0 1-7-7L12 2m-7 13 9-9" />,
    star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z" />,
    'panel-left': <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M9 4v16M5.5 8h1M5.5 11h1M5.5 14h1" /></>,
    plus: <path d="M12 4v16M4 12h16" />,
    more: <><circle cx="5" cy="12" r=".9" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r=".9" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r=".9" fill="currentColor" stroke="none" /></>,
    chevron: <path d="m9 5 7 7-7 7" />,
    chat: <><path d="M20 11a8 8 0 0 1-8 8H6l-4 3v-7a8 8 0 1 1 18-4Z" /><path d="M7 8h8M7 12h5" /></>,
    bell: <><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9Z" /><path d="M9 21h6" /></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
    send: <><path d="m3 3 19 9-19 9 4-9-4-9Z" /><path d="M7 12h15" /></>,
    smile: <><circle cx="12" cy="12" r="9" /><path d="M8 14s1 3 4 3 4-3 4-3M8 9h.01M16 9h.01" /></>,
    edit: <><path d="M11 4H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-6M14 4l4-3 4 4-12 12-5 1 1-5Z" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 6v6l4 2" /></>,
    people: <><circle cx="9" cy="6" r="3" /><path d="M2 21v-3a7 7 0 0 1 14 0v3ZM17 3a3 3 0 0 1 0 6M18 13a6 6 0 0 1 4 5v3" /></>,
    info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7h.01" /></>,
    close: <path d="m6 6 12 12M6 18 18 6" />,
    reply: <><path d="m9 5-7 7 7 7M2 12h12a7 7 0 0 1 7 7" /></>,
    spark: <><path d="m12 2 2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5Z" /><path d="m20 2 1 2 2 1-2 1-1 2-1-2-2-1 2-1Z" /></>,
    sidebar: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M15 3v18" /></>,
    folder: <path d="M3 5h7l2 3h9v12H3Z" />,
    file: <><path d="M5 2h9l5 5v15H5ZM14 2v6h5M8 13h8M8 17h6" /></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2" /></>,
    settings: <><path d="m10 2-1 3-3 1-3-1-2 4 3 2v3l-3 2 2 4 3-1 3 1 1 3h4l1-3 3-1 3 1 2-4-3-2v-3l3-2-2-4-3 1-3-1-1-3Z" /><circle cx="12" cy="12" r="3" /></>,
    trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7" /></>,
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths[name]}
    </svg>
  );
}

export function BrandMark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <img src="/assets/duet-symbol.svg" alt="" />
    </span>
  );
}

export function Avatar({ actor, size = 36, twin = false, group = false, duetBadge = false }: { actor?: Actor; size?: number; twin?: boolean; group?: boolean; duetBadge?: boolean }) {
  const isTwin = twin || actor?.kind === 'twin';
  const [failedUrl, setFailedUrl] = useState('');
  const fallback = !group && !actor?.avatar_preset && (!actor?.avatar_url || failedUrl === actor.avatar_url);
  const colors = fallback && actor ? defaultAvatarColor(actor.name || actor.id) : undefined;
  return (
    <span
      className={`avatar ${isTwin ? 'twin' : ''} ${group ? 'group-avatar' : ''}`}
      style={{
        width: size,
        height: size,
        backgroundColor: colors?.backgroundColor || (!isTwin ? actor?.color || undefined : undefined),
        color: colors?.color,
        fontSize: size * 0.34,
      }}
      aria-hidden="true"
    >
      {group ? (
        <Icon name="people" size={size * 0.54} />
      ) : actor?.avatar_preset ? (
        <PresetAvatar preset={actor.avatar_preset} />
      ) : actor?.avatar_url && failedUrl !== actor.avatar_url ? (
        <img src={actor.avatar_url} alt="" onError={() => setFailedUrl(actor.avatar_url || '')} />
      ) : isTwin ? (
        <BrandMark />
      ) : (
        actor?.initials || '·'
      )}
      {isTwin && (
        <span className={duetBadge ? 'twin-badge duet-identity-badge' : 'twin-badge'}>
          {duetBadge ? <BrandMark /> : '✧'}
        </span>
      )}
    </span>
  );
}

export function chatTitle(chat: Chat, actors: Actor[], ownerId: string) {
  const peer = chat.kind === 'group' && chat.member_owner_ids.length === 2
    ? chat.member_owner_ids.find(id => id !== ownerId)
    : null;
  return actors.find(actor => actor.kind === 'human' && actor.id === peer)?.name || chat.title;
}

export function ChatAvatar({ chat, actors, ownerId, size = 40 }: { chat: Chat; actors: Actor[]; ownerId: string; size?: number }) {
  const peer = chat.kind === 'group' && chat.member_owner_ids.length === 2
    ? chat.member_owner_ids.find(id => id !== ownerId)
    : null;
  const group = chat.kind === 'internal' || (chat.kind === 'group' && chat.member_owner_ids.length >= 3);
  const actor = chat.kind === 'bot'
    ? actors.find(item => item.id === chat.bot_id)
    : group
      ? undefined
      : peer
        ? actors.find(item => item.kind === 'human' && item.id === peer)
        : actors.find(item => item.kind === 'twin' && item.owner_id === chat.member_owner_ids[0]);
  return <Avatar actor={actor} size={size} group={group} />;
}

export function IconButton({
  icon,
  label,
  onClick,
  disabled,
  active,
  size = 18,
  className = '',
  hideTooltip = false,
}: {
  icon: IconName;
  label: string;
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
  active?: boolean;
  size?: number;
  className?: string;
  hideTooltip?: boolean;
}) {
  return (
    <button
      type="button"
      className={`icon-button ${active ? 'active' : ''} ${className}`}
      title={hideTooltip ? undefined : label}
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
    >
      <Icon name={icon} size={size} />
    </button>
  );
}

/** Switch a whole segmented control between text labels and icon-only mode
 *  when labels would overflow or truncate inside the available container width. */
export function useSegmentedIconFallback(
  containerRef: RefObject<HTMLElement | null>,
  groupRef: RefObject<HTMLElement | null>,
  resetKey: string,
  reservePx = 0,
) {
  const [iconsOnly, setIconsOnly] = useState(false);
  const textWidthRef = useRef<number | null>(null);

  useEffect(() => {
    textWidthRef.current = null;
    setIconsOnly(false);
  }, [resetKey]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    const group = groupRef.current;
    if (!container || !group) return;

    const measure = () => {
      const style = getComputedStyle(container);
      const padX = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
      const available = container.clientWidth - padX - reservePx;
      if (available <= 0) return;

      if (!iconsOnly) {
        const natural = Math.ceil(group.scrollWidth);
        const childOverflow = Array.from(group.children).some(
          child => child instanceof HTMLElement && child.scrollWidth > child.clientWidth + 1,
        );
        if (natural > available + 1 || childOverflow) {
          textWidthRef.current = Math.max(natural, textWidthRef.current || 0);
          setIconsOnly(true);
        }
      } else if (textWidthRef.current !== null && available >= textWidthRef.current + 8) {
        setIconsOnly(false);
      }
    };

    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, [containerRef, groupRef, iconsOnly, reservePx, resetKey]);

  return iconsOnly;
}


function hasClassName(element: ReactElement<{ className?: string }>, name: string) {
  const value = element.props.className;
  if (typeof value !== 'string') return false;
  return value.split(/\s+/).includes(name);
}

/** Keep Sheet actions in a fixed footer slot; content scrolls in the pane (coverage §2, §6.10). */
function partitionModalChildren(children: ReactNode): { body: ReactNode; footer: ReactNode } {
  const body: ReactNode[] = [];
  const footer: ReactNode[] = [];
  Children.forEach(children, (child, index) => {
    if (!isValidElement(child)) {
      body.push(child);
      return;
    }
    const element = child as ReactElement<{ className?: string; children?: ReactNode }>;
    if (hasClassName(element, 'modal-actions')) {
      footer.push(element);
      return;
    }
    if (element.type === 'form') {
      const formBody: ReactNode[] = [];
      const formFooter: ReactNode[] = [];
      Children.forEach(element.props.children, inner => {
        if (isValidElement(inner) && hasClassName(inner as ReactElement<{ className?: string }>, 'modal-actions')) {
          formFooter.push(inner);
        } else {
          formBody.push(inner);
        }
      });
      body.push(
        cloneElement(
          element,
          { key: element.key ?? `modal-form-${index}`, className: [element.props.className, 'modal-form'].filter(Boolean).join(' ') },
          <>
            <div className="modal-scroller">{formBody}</div>
            {formFooter}
          </>,
        ),
      );
      return;
    }
    body.push(element);
  });
  const hasForm = body.some(node => isValidElement(node) && node.type === 'form');
  const wrappedBody = !hasForm && footer.length ? <div className="modal-scroller">{body}</div> : body;
  return { body: wrappedBody, footer: footer.length ? footer : null };
}

export function Modal({
  title,
  children,
  onClose,
  wide = false,
  closeDisabled = false,
  variant = 'sheet',
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  closeDisabled?: boolean;
  variant?: 'sheet' | 'alert';
}) {
  const container = useRef<HTMLElement>(null);
  const close = useRef(onClose);
  const disabled = useRef(closeDisabled);
  const dirtyRef = useRef(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  close.current = onClose;
  disabled.current = closeDisabled;

  const requestBackdropDismiss = () => {
    if (disabled.current) return;
    if (variant === 'sheet' && dirtyRef.current) {
      setConfirmDiscard(true);
      return;
    }
    close.current();
  };

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => {
      if (!container.current || container.current.contains(document.activeElement)) return;
      if (variant === 'alert') {
        const cancelBtn = container.current.querySelector<HTMLElement>('.modal-actions .secondary-button, .twin-content-actions .secondary-button');
        (cancelBtn || container.current).focus({ preventScroll: true });
      } else {
        const firstInput = container.current.querySelector<HTMLElement>(
          'input:not(:disabled):not([hidden]):not([type="checkbox"]),textarea:not(:disabled),select:not(:disabled)',
        );
        (firstInput || container.current).focus({ preventScroll: true });
      }
    });
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!disabled.current) {
          if (variant === 'sheet' && dirtyRef.current) {
            setConfirmDiscard(true);
          } else {
            close.current();
          }
        }
        return;
      }
      if (event.key !== 'Tab' || !container.current) return;
      const focusable = [
        ...container.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled),a[href],input:not(:disabled):not([hidden]),textarea:not(:disabled),select:not(:disabled),[tabindex="0"]',
        ),
      ].filter(element => element.offsetParent !== null);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first) {
        event.preventDefault();
        container.current.focus();
      } else if (event.shiftKey && (document.activeElement === first || !container.current.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !container.current.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('keydown', handleKey);
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, [variant]);

  return createPortal(
    <div
      className={`twins-modal-backdrop ${variant === 'alert' ? 'alert-backdrop' : ''}`}
      onMouseDown={event => {
        if (event.target === event.currentTarget) requestBackdropDismiss();
      }}
    >
      <section
        ref={container}
        tabIndex={-1}
        className={`modal duet-glass-overlay ${wide ? 'wide' : ''} ${variant === 'alert' ? 'modal-alert' : 'modal-sheet'}`}
        role={variant === 'alert' ? 'alertdialog' : 'dialog'}
        aria-modal="true"
        aria-label={title}
        onInput={event => {
          if (variant !== 'sheet') return;
          const target = event.target as HTMLElement;
          if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) {
            dirtyRef.current = true;
            if (confirmDiscard) setConfirmDiscard(false);
          }
        }}
      >
        <div className="modal-heading">
          <h2>{title}</h2>
          {variant !== 'alert' && (
            <IconButton icon="close" label={t('common.close')} onClick={onClose} disabled={closeDisabled} />
          )}
        </div>
        {confirmDiscard && (
          <div className="sheet-discard-bar" role="status">
            <span>{t('common.discardChanges')}</span>
            <div className="sheet-discard-actions">
              <button type="button" className="secondary-button" onClick={() => setConfirmDiscard(false)}>
                {t('common.keepEditing')}
              </button>
              <button type="button" className="danger-button" onClick={() => { dirtyRef.current = false; onClose(); }}>
                {t('common.discard')}
              </button>
            </div>
          </div>
        )}
        {(() => {
          const { body, footer } = partitionModalChildren(children);
          return (
            <>
              <div className="modal-body">{body}</div>
              {footer}
            </>
          );
        })()}
      </section>
    </div>,
    document.body,
  );
}

/** Lightweight, non-blocking anchored Popover (DESIGN.md §4). */
export function Popover({
  title,
  anchor,
  align = 'right',
  width = 320,
  onClose,
  closeDisabled = false,
  children,
}: {
  title: string;
  anchor?: DOMRect | null;
  align?: 'left' | 'right' | 'center';
  width?: number;
  onClose: () => void;
  closeDisabled?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const [style, setStyle] = useState<CSSProperties>({ visibility: 'hidden' });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const margin = 12;
    const w = Math.min(width, vw - margin * 2);
    const h = el.offsetHeight || 220;

    let top = 64;
    let left = Math.round((vw - w) / 2);

    if (anchor) {
      top = anchor.bottom + 8;
      if (top + h > vh - margin && anchor.top - h - 8 > margin) {
        top = anchor.top - h - 8;
      } else {
        top = Math.max(margin, Math.min(top, vh - h - margin));
      }
      if (align === 'right') left = anchor.right - w;
      else if (align === 'center') left = anchor.left + anchor.width / 2 - w / 2;
      else left = anchor.left;
      left = Math.max(margin, Math.min(left, vw - w - margin));
    }

    setStyle({ top: `${Math.round(top)}px`, left: `${Math.round(left)}px`, width: `${Math.round(w)}px`, visibility: 'visible' });
  }, [anchor, align, width]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (closeDisabled) return;
      if (!ref.current?.contains(event.target as Node)) onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !closeDisabled) {
        event.stopPropagation();
        onClose();
      }
    };
    const timer = setTimeout(() => {
      document.addEventListener('pointerdown', onPointerDown);
    }, 0);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose, closeDisabled]);

  return createPortal(
    <section
      ref={ref}
      className="duet-popover duet-glass-overlay"
      role="dialog"
      aria-label={title}
      style={style}
      data-floating-menu
    >
      <div className="duet-popover-heading">
        <h2>{title}</h2>
        <IconButton icon="close" size={16} label={t('common.close')} onClick={onClose} disabled={closeDisabled} />
      </div>
      <div className="duet-popover-body">{children}</div>
    </section>,
    document.body,
  );
}

/** Portaled dropdown menu so backdrop-filter blurs content behind glass toolbar ancestors. */
export function AnchoredMenuPortal({
  anchorRef,
  className,
  gap = 6,
  children,
}: {
  anchorRef: { current: HTMLElement | null };
  className: string;
  gap?: number;
  children: ReactNode;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<CSSProperties>({ position: 'fixed', top: 0, left: 0, right: 'auto', visibility: 'hidden' });

  useLayoutEffect(() => {
    const update = () => {
      const anchor = anchorRef.current;
      const el = menuRef.current;
      if (!anchor || !el) return;
      const a = anchor.getBoundingClientRect();
      const vw = document.documentElement.clientWidth || window.innerWidth;
      const vh = window.innerHeight;
      const w = el.offsetWidth || 220;
      const h = el.offsetHeight || 140;
      const margin = 8;
      const left = Math.max(margin, Math.min(a.right - w, vw - w - margin));
      const top = a.bottom + gap + h > vh - margin && a.top - gap - h >= margin
        ? a.top - gap - h
        : Math.max(margin, Math.min(a.bottom + gap, vh - h - margin));
      setPos({ position: 'fixed', top: `${Math.round(top)}px`, left: `${Math.round(left)}px`, right: 'auto', visibility: 'visible' });
    };
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, [anchorRef, gap]);

  return createPortal(
    <div ref={menuRef} className={className} role="menu" data-floating-menu style={pos}>
      {children}
    </div>,
    document.body,
  );
}

/** Human-readable technical error presentation with expandable raw details (DESIGN.md §6). */
export function TechnicalError({ error, className = '' }: { error: string; className?: string }) {
  const formatted = humanizeTechnicalError(error);
  return (
    <div className={`technical-error ${className}`} role="alert">
      <p className="technical-error-summary">{formatted.summary}</p>
      {formatted.details && (
        <details className="technical-error-details">
          <summary>{t('common.details')}</summary>
          <pre>{formatted.details}</pre>
        </details>
      )}
    </div>
  );
}

export function Empty({
  icon,
  title,
  detail,
  action,
}: {
  icon: IconName;
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-state-icon">
        <Icon name={icon} size={22} />
      </span>
      <h3>{title}</h3>
      {detail && <p>{detail}</p>}
      {action && <div className="empty-state-action">{action}</div>}
    </div>
  );
}

export function time(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  return isNaN(date.getTime()) ? value : formatTime(date);
}

export function dateTime(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  return isNaN(date.getTime()) ? value : formatDate(date, { dateStyle: 'medium', timeStyle: 'short' });
}

/** Server-declared test mode only. Never enables a provider or generates responses. */
export function FixtureBanner() {
  return (
    <div className="acceptance-fixture-banner" role="status">
      <Icon name="info" size={15} />
      <strong>{t('config.fixtureTitle')}</strong>
      <span>{t('config.fixtureDetail')}</span>
    </div>
  );
}

/** Shared keyboard behavior for anchored menus. Popover state remains owned by callers. */
export function useMenuBehavior() {
  useEffect(() => {
    const triggers = new Map<HTMLElement, HTMLElement | null>();
    const frames = new Set<number>();
    const observer = new MutationObserver(() => {
      document.querySelectorAll<HTMLElement>('[role="menu"],.emoji-picker').forEach(menu => {
        if (triggers.has(menu)) return;
        const active = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        triggers.set(menu, active);
        const frame = requestAnimationFrame(() => {
          frames.delete(frame);
          if (menu.isConnected) menu.querySelector<HTMLElement>('button:not(:disabled),input')?.focus({ preventScroll: true });
        });
        frames.add(frame);
      });
      for (const [menu, trigger] of triggers) {
        if (!menu.isConnected) {
          if (trigger?.isConnected && (document.activeElement === document.body || menu.contains(document.activeElement))) {
            trigger.focus({ preventScroll: true });
          }
          triggers.delete(menu);
        }
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const menu = target.closest<HTMLElement>('[role="menu"],.emoji-picker');
      if (!menu) return;
      const buttons = [...menu.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
      const current = buttons.indexOf(target as HTMLButtonElement);
      let next = -1;
      if (event.key === 'ArrowDown') next = (current + 1) % buttons.length;
      if (event.key === 'ArrowUp') next = (current - 1 + buttons.length) % buttons.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = buttons.length - 1;
      if (next >= 0) {
        event.preventDefault();
        buttons[next]?.focus();
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        triggers.get(menu)?.focus({ preventScroll: true });
      }
    };
    document.addEventListener('keydown', key);
    return () => {
      observer.disconnect();
      frames.forEach(cancelAnimationFrame);
      document.removeEventListener('keydown', key);
    };
  }, []);
}
