import { en } from './en';

export type TranslationKey = keyof typeof en;
type Values = Record<string, string | number>;
export const DEFAULT_LOCALE = 'en';

// Product copy and date/time formatting are always English, including for browsers
// that retain the obsolete twinteams.locale preference from an earlier release.
export const i18n = { locale: DEFAULT_LOCALE } as const;
export const t = (key: TranslationKey, values: Values = {}): string =>
  en[key].replace(/\{(\w+)\}/g, (placeholder, name: string) =>
    values[name] === undefined ? placeholder : String(values[name]));
const dateFormatters = new Map<string, Intl.DateTimeFormat>();
export const formatDate = (date: Date, options: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }) => {
  const key = JSON.stringify(options);
  let formatter = dateFormatters.get(key);
  if (!formatter) { formatter = new Intl.DateTimeFormat(DEFAULT_LOCALE, options); dateFormatters.set(key, formatter); }
  return formatter.format(date);
};
export const formatTime = (date: Date) => formatDate(date, { hour: 'numeric', minute: '2-digit' });
export const formatRelativeDay = (offset: number) =>
  new Intl.RelativeTimeFormat(DEFAULT_LOCALE, { numeric: 'auto' }).format(offset, 'day');
