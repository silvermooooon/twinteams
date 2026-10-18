import { useEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { requestKey } from './api/client';
import type { Message } from './api/client';
import { Icon, IconButton, Modal } from './ui';
import { t } from './i18n';
import './MessageReactions.css';

type EmojiVariant = { emoji: string; image: string; animated?: string; duration_ms?: number };
type EmojiEntry = EmojiVariant & { name: string; zh: string; keywords: string[]; group: string; variants: EmojiVariant[] };
type ReactionIntent = { emoji: string; active: boolean; key: string };
export type ReactionPreferences = { quick: string[]; tone: number; recent: string[] };
const defaults: ReactionPreferences = { quick: ['👍', '❤️', '😆', '😮'], tone: 0, recent: [] };
let catalogPromise: Promise<EmojiEntry[]> | undefined;
const emojiIndex = new Map<string, EmojiEntry>();
function catalog() { return catalogPromise ||= fetch('/emoji/catalog.json').then(async response => { if (!response.ok) throw new Error(t('reactions.loadFailed')); const entries = await response.json() as EmojiEntry[]; for (const entry of entries) { emojiIndex.set(entry.emoji.replaceAll('\ufe0f', ''), entry); for (const variant of entry.variants) emojiIndex.set(variant.emoji.replaceAll('\ufe0f', ''), entry); } return entries; }).catch(error => { catalogPromise = undefined; throw error; }); }
function useCatalog() {
  const [entries, setEntries] = useState<EmojiEntry[]>([]); const [error, setError] = useState('');
  useEffect(() => { let alive = true; catalog().then(value => { if (alive) setEntries(value); }).catch(reason => { if (alive) setError(String(reason.message || reason)); }); return () => { alive = false; }; }, []);
  return { entries, error };
}
function baseEntry(entries: EmojiEntry[], emoji: string) { return entries.length === 1 ? entries.find(entry => entry.emoji.replaceAll('\ufe0f', '') === emoji.replaceAll('\ufe0f', '') || entry.variants.some(item => item.emoji === emoji)) : emojiIndex.get(emoji.replaceAll('\ufe0f', '')); }
function toned(entry: Pick<EmojiEntry, 'emoji' | 'image' | 'variants'>, tone: number) { return entry.variants[tone] || entry.variants[0] || entry; }
function EmojiImage({ emoji, entries, animate = false }: { emoji: string; entries: EmojiEntry[]; animate?: boolean }) {
  const [hover, setHover] = useState(false);
  const entry = baseEntry(entries, emoji); const variant = entry?.variants.find(item => item.emoji === emoji) || entry;
  return variant ? <picture className="fluent-emoji" onPointerEnter={event => { if (event.pointerType !== 'touch') setHover(true); }} onPointerLeave={() => setHover(false)}>{variant.animated && <source media="(prefers-reduced-motion: no-preference)" srcSet={(hover || animate) ? variant.animated : variant.image} />}<img src={variant.image} alt="" draggable={false} loading="lazy" /></picture> : <span aria-hidden="true">{emoji}</span>;
}
export function useReactionPreferences(ownerId: string) {
  const read = () => {
    try { const value = JSON.parse(localStorage.getItem(`twinteams.reactions:${ownerId}`) || 'null'); if (value && Array.isArray(value.quick) && Array.isArray(value.recent) && Number.isInteger(value.tone) && value.tone >= 0 && value.tone <= 5) return { quick: value.quick.filter((item: unknown) => typeof item === 'string' && item.length <= 32).slice(0, 5), tone: value.tone, recent: value.recent.filter((item: unknown) => typeof item === 'string' && item.length <= 32).slice(0, 30) } as ReactionPreferences; } catch { /* Invalid local preference falls back to defaults. */ }
    return defaults;
  };
  const [state, setState] = useState(() => ({ ownerId, value: read() }));
  useEffect(() => { setState({ ownerId, value: read() }); }, [ownerId]);
  const value = state.ownerId === ownerId ? state.value : defaults;
  const save = (next: ReactionPreferences) => { try { localStorage.setItem(`twinteams.reactions:${ownerId}`, JSON.stringify(next)); } catch { /* Session preferences still work when browser storage is unavailable. */ } setState({ ownerId, value: next }); };
  return { value, save };
}
const categories = [
  ['recent', 'recent', '🕘'], ['Smileys & Emotion', 'smileys', '😀'], ['Gestures', 'gestures', '👍'], ['People & Body', 'people', '🧑'],
  ['Animals & Nature', 'animals', '🐶'], ['Food & Drink', 'food', '🍎'], ['Travel & Places', 'travel', '🚗'], ['Objects', 'objects', '💡'],
  ['Activities', 'activities', '⚽'], ['Symbols', 'symbols', '❤️'],
] as const;

function EmojiPicker({ anchor, preferences, onPreferences, onSelect, onClose }: { anchor: HTMLElement; preferences: ReactionPreferences; onPreferences: (next: ReactionPreferences) => void; onSelect: (emoji: string) => void; onClose: () => void }) {
  const { entries, error } = useCatalog(); const [query, setQuery] = useState(''); const [category, setCategory] = useState('Smileys & Emotion'); const [visible, setVisible] = useState(120);
  const [custom, setCustom] = useState(false); const [draft, setDraft] = useState(preferences); const [slot, setSlot] = useState<number | null>(null);
  const close = useRef(onClose); close.current = onClose;
  const popup = useRef<HTMLDivElement>(null); const search = useRef<HTMLInputElement>(null); const gallery = useRef<HTMLDivElement>(null);
  const rect = anchor.getBoundingClientRect(); const width = Math.min(318, window.innerWidth - 24); const height = Math.min(488, window.innerHeight - 32);
  const left = Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12)); const top = rect.top >= height + 12 ? rect.top - height - 8 : Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - height - 12));
  useEffect(() => {
    const frame = requestAnimationFrame(() => search.current?.focus({ preventScroll: true }));
    const outside = (event: PointerEvent) => { if (!custom && !popup.current?.contains(event.target as Node) && !anchor.contains(event.target as Node)) close.current(); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && !custom) { event.preventDefault(); close.current(); anchor.focus({ preventScroll: true }); } };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', escape);
    return () => { cancelAnimationFrame(frame); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [custom, anchor]);
  useEffect(() => { setVisible(120); if (gallery.current) gallery.current.scrollTop = 0; }, [query, category]);
  const activePreferences = custom ? draft : preferences;
  const filtered = entries.filter(entry => query.trim() ? [entry.name, entry.zh, entry.emoji, ...entry.variants.map(item => item.emoji), ...entry.keywords].join(' ').toLowerCase().includes(query.trim().toLowerCase()) : category === 'recent' ? preferences.recent.some(emoji => baseEntry([entry], emoji)) : (entry.group === category || (category === 'Symbols' && entry.group === 'Flags')));
  const select = (entry: EmojiEntry) => {
    const variant = toned(entry, activePreferences.tone);
    if (custom) { const index = slot ?? draft.quick.findIndex(item => !item); const next = [...draft.quick]; const target = index >= 0 ? index : next.length < 5 ? next.length : -1; if (target < 0) return; for (let other = 0; other < next.length; other++) if (other !== target && next[other] === entry.emoji) next[other] = ''; next[target] = entry.emoji; setDraft({ ...draft, quick: next }); setSlot(null); }
    else { onSelect(variant.emoji); onClose(); }
  };
  const content = <>
    <div className="reaction-picker-search"><Icon name="search" size={17} /><input ref={search} aria-label={t('reactions.search')} value={query} placeholder={t('reactions.search')} onChange={event => setQuery(event.target.value)} />{!custom && <IconButton icon="settings" label={t('reactions.customize')} onClick={() => { setDraft(preferences); setSlot(null); setCustom(true); }} />}</div>
    <div className="reaction-gallery" ref={gallery} onScroll={event => { const target = event.currentTarget; if (target.scrollTop + target.clientHeight >= target.scrollHeight - 120) setVisible(count => count + 120); }} onKeyDown={event => {
      const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('.emoji-option')]; const index = buttons.indexOf(event.target as HTMLButtonElement); if (index < 0) return;
      const next = event.key === 'ArrowRight' ? index + 1 : event.key === 'ArrowLeft' ? index - 1 : event.key === 'ArrowDown' ? index + 6 : event.key === 'ArrowUp' ? index - 6 : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : -1;
      if (next >= 0) { event.preventDefault(); buttons[Math.min(next, buttons.length - 1)]?.focus(); }
    }}>
      <div className="reaction-category-title">{query ? t('reactions.results', { count: filtered.length }) : t(`reactions.category.${categories.find(item => item[0] === category)?.[1] || 'smileys'}` as Parameters<typeof t>[0])}</div>
      {!!error && <p role="alert">{error}</p>}
      {!entries.length && !error && <p className="muted">{t('common.loading')}</p>}
      {!!entries.length && !filtered.length && <p className="muted">{t('reactions.noResults')}</p>}
      <div className="reaction-emoji-grid">{filtered.slice(0, visible).map(entry => { const variant = toned(entry, activePreferences.tone); const name = entry.name; return <button type="button" key={entry.emoji} className="emoji-option" title={name} aria-label={`${name} ${variant.emoji}`} onClick={() => select(entry)}><EmojiImage emoji={variant.emoji} entries={entries} /></button>; })}</div>
    </div>
    <div className="reaction-categories" role="tablist" aria-label={t('reactions.categories')}>{categories.map(([id, label, emoji]) => <button type="button" role="tab" key={id} aria-selected={category === id && !query} title={t(`reactions.category.${label}` as Parameters<typeof t>[0])} aria-label={t(`reactions.category.${label}` as Parameters<typeof t>[0])} onClick={() => { setCategory(id); setQuery(''); }}><EmojiImage emoji={emoji} entries={entries} /></button>)}</div>
  </>;
  return custom ? <Modal title={t('reactions.customizeTitle')} wide onClose={() => setCustom(false)}><div className="reaction-customization"><p>{t('reactions.customizeDetail')}</p><div className="reaction-slots">{Array.from({ length: 5 }, (_, index) => <div key={index} className={slot === index ? 'selected' : ''}><button type="button" aria-label={t('reactions.slot', { number: index + 1 })} onClick={() => setSlot(index)}>{draft.quick[index] ? <EmojiImage emoji={toned(baseEntry(entries, draft.quick[index]) || { emoji: draft.quick[index], image: '', variants: [] }, draft.tone).emoji} entries={entries} /> : <Icon name="plus" size={22} />}</button>{draft.quick[index] && <button type="button" className="reaction-slot-remove" aria-label={t('reactions.removeSlot', { number: index + 1 })} onClick={() => { const next = [...draft.quick]; next[index] = ''; setDraft({ ...draft, quick: next }); setSlot(index); }}><Icon name="close" size={12} /></button>}</div>)}</div><p>{t('reactions.skinTone')}</p><div className="reaction-skin-tones">{['#ffca3e', '#f9d5b2', '#e6b78e', '#c68a5a', '#98623e', '#5c3927'].map((color, index) => <button type="button" key={color} style={{ '--skin-tone': color } as CSSProperties} aria-label={t('reactions.tone', { number: index })} aria-pressed={draft.tone === index} onClick={() => setDraft({ ...draft, tone: index })} />)}</div>{slot !== null && <div className="reaction-custom-gallery">{content}</div>}<p className="form-note">{t('reactions.deviceOnly')}</p><div className="modal-actions"><button className="secondary-button" onClick={() => setCustom(false)}>{t('common.cancel')}</button><button className="primary-button" disabled={JSON.stringify(draft) === JSON.stringify(preferences)} onClick={() => { onPreferences({ ...draft, quick: draft.quick.filter(Boolean) }); setCustom(false); }}>{t('common.save')}</button></div></div></Modal> : createPortal(<div className="reaction-picker" ref={popup} role="dialog" aria-label={t('reactions.more')} data-floating-menu style={{ left, top, width, height }}>{content}</div>, document.body);
}

export function MessageReactions({ message, preferences, onPreferences, onReact, children }: { message: Message; preferences: ReactionPreferences; onPreferences: (next: ReactionPreferences) => void; onReact: (emoji: string, active: boolean, key: string) => Promise<void>; children: ReactNode }) {
  const { entries } = useCatalog(); const [picker, setPicker] = useState<HTMLElement | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [pop, setPop] = useState(''); const [people, setPeople] = useState<string | null>(null);
  const pending = useRef<ReactionIntent | null>(null); const inFlight = useRef(false); const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { if (!pop) return; const entry = baseEntry(entries, pop); const variant = entry?.variants.find(item => item.emoji === pop); const timer = setTimeout(() => setPop(''), variant?.duration_ms || 1500); return () => clearTimeout(timer); }, [pop]);
  const reactions = message.reactions || [];
  const react = async (emoji: string, retryIntent?: ReactionIntent) => {
    if (inFlight.current) return;
    const active = retryIntent?.active ?? !reactions.some(item => item.emoji === emoji && item.mine);
    const intent = retryIntent || (pending.current?.emoji === emoji && pending.current.active === active ? pending.current : { emoji, active, key: requestKey() });
    pending.current = intent; inFlight.current = true; setBusy(true); setError('');
    try { await onReact(emoji, active, intent.key); if (!alive.current) return; pending.current = null; setPop(active ? emoji : ''); onPreferences({ ...preferences, recent: [emoji, ...preferences.recent.filter(item => item !== emoji)].slice(0, 30) }); }
    catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { inFlight.current = false; if (alive.current) setBusy(false); }
  };
  return <><div className={`message-actions reaction-actions ${picker ? 'picker-open' : ''}`} aria-label={t('reactions.actions')}>
    <div className="reaction-quick">{preferences.quick.filter(Boolean).map(emoji => { const entry = baseEntry(entries, emoji); const selected = entry ? toned(entry, preferences.tone).emoji : emoji; return <button type="button" key={emoji} className="reaction-quick-button" disabled={busy} title={t('reactions.react', { emoji: selected })} aria-label={t('reactions.react', { emoji: selected })} aria-pressed={reactions.some(item => item.emoji === selected && item.mine)} onClick={() => { void react(selected); }}><EmojiImage emoji={selected} entries={entries} /></button>; })}</div>
    <button type="button" className="reaction-more" aria-label={t('reactions.more')} title={t('reactions.more')} aria-expanded={!!picker} onClick={event => setPicker(picker ? null : event.currentTarget)}><Icon name="smile" size={19} /><i>+</i></button><span className="reaction-toolbar-divider" />{children}
  </div>{!!reactions.length && <div className="message-reactions" aria-label={t('reactions.label')}>{reactions.slice(0, 5).map(reaction => <div key={reaction.emoji} className={`reaction-chip ${reaction.mine ? 'mine' : ''} ${pop === reaction.emoji ? 'reaction-pop' : ''}`} title={reaction.actors.map(actor => actor.name).join(', ')}><button type="button" disabled={busy} aria-label={t(reaction.mine ? 'reactions.remove' : 'reactions.react', { emoji: reaction.emoji })} aria-pressed={reaction.mine} onClick={() => { void react(reaction.emoji); }}><EmojiImage emoji={reaction.emoji} entries={entries} animate={pop === reaction.emoji} /></button><button type="button" className="reaction-count" aria-label={t('reactions.who', { emoji: reaction.emoji, count: reaction.count })} onClick={() => setPeople(reaction.emoji)}>{reaction.count}</button></div>)}{reactions.length > 5 && <button type="button" className="reaction-overflow" aria-label={t('reactions.moreTypes', { count: reactions.length - 5 })} onClick={() => setPeople('all')}>+{reactions.length - 5}</button>}</div>}{error && <div className="reaction-error" role="alert">{error}<button type="button" onClick={() => { if (pending.current) void react(pending.current.emoji, pending.current); }}>{t('common.retry')}</button></div>}{picker && <EmojiPicker anchor={picker} preferences={preferences} onPreferences={onPreferences} onSelect={emoji => { void react(emoji); }} onClose={() => setPicker(null)} />}{people && <Modal title={t('reactions.whoTitle')} onClose={() => setPeople(null)}><div className="reaction-roster">{reactions.filter(item => people === 'all' || item.emoji === people).map(reaction => <section className="reaction-people" key={reaction.emoji}><EmojiImage emoji={reaction.emoji} entries={entries} /><div><strong>{reaction.count}</strong><ul>{reaction.actors.map(actor => <li key={actor.id}>{actor.name}{actor.kind === 'twin' && <small>Twin</small>}</li>)}</ul></div></section>)}</div></Modal>}</>;
}
