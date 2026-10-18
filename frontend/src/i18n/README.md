# Interface localization

English (`en`) is the default. All UI copy lives in `en.ts`; components use typed `t(key, values)` calls. Filter/navigation state uses stable IDs rather than translated labels. `t` interpolates `{name}` placeholders and falls back to English for missing translations. Unknown or invalid locales fall back to English too.

## Add a language

1. Add a resource file (for example `fr.ts`) exporting an object that `satisfies TranslationResource` imported as a type from `./index`. Use the keys in `en.ts` and preserve interpolation names such as `{count}` and `{title}`. Partial translations are allowed and fall back to English.
2. Import the resource in `index.ts` and register it in `resources`, for example `{ en, fr }`. Regional locale requests such as `fr-CA` fall back to the `fr` dictionary while retaining regional date/time formatting.
3. Pass the desired locale to `createI18n` when initializing the exported `i18n` instance. `main.tsx` sets the document language from that instance. There is deliberately no language switcher or browser-language auto-detection yet. Runtime switching would require a React locale provider and rendering localized date values from their original timestamps.
4. Use `formatDate`, `formatTime`, and `formatRelativeDay` for dates/times. These use `Intl` and the selected locale. `pluralCategory` exposes `Intl.PluralRules`; add language-specific plural keys and selection logic when a phrase needs them.

Names, messages, reminders, and other API content are user data and do not pass through translation. All data comes from the authenticated API; no demonstration messages or reminders are bundled.

Build with `npm run build`. This runs Vite only, without type checking, tests, or lint.
