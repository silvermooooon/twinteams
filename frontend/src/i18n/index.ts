import { en } from './en';

export type TranslationKey = keyof typeof en;
export type TranslationResource = Partial<Record<TranslationKey, string>>;
type Values = Record<string, string | number>;
const resources: Record<string, TranslationResource> = { en };
export const DEFAULT_LOCALE = 'en';

/** Locale is chosen at startup. Unknown languages and missing keys fall back to English. */
export function createI18n(requestedLocale: string = DEFAULT_LOCALE) {
  let locale = DEFAULT_LOCALE;
  try { locale = Intl.getCanonicalLocales(requestedLocale)[0] || DEFAULT_LOCALE; } catch { /* Invalid locale: use English. */ }
  const language = locale.split('-')[0];
  const resource = resources[locale] ?? resources[language];
  if (!resource) locale = DEFAULT_LOCALE;
  const dictionary = resource ?? en;
  const t = (key: TranslationKey, values: Values = {}): string =>
    (dictionary[key] ?? en[key]).replace(/\{(\w+)\}/g, (placeholder, name: string) =>
      values[name] === undefined ? placeholder : String(values[name]));
  const formatDate = (date: Date, options: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }) =>
    new Intl.DateTimeFormat(locale, options).format(date);
  const formatTime = (date: Date) => formatDate(date, { hour: 'numeric', minute: '2-digit' });
  const formatRelativeDay = (offset: number) => new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(offset, 'day');
  const pluralCategory = (count: number) => new Intl.PluralRules(locale).select(count);
  return { locale, t, formatDate, formatTime, formatRelativeDay, pluralCategory };
}

// A future saved language preference can be supplied here before rendering the app.
export const i18n = createI18n();
export const { t, formatDate, formatTime, formatRelativeDay, pluralCategory } = i18n;
