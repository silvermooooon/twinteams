# English interface copy

Duet uses English for all product-authored interface text and date/time formatting. `en.ts` and `twin.ts` hold the typed English copy; `t(key, values)` interpolates placeholders such as `{name}`. The interface does not offer a language selection and ignores obsolete `twinteams.locale` preferences. `main.tsx` sets the document language to `en`.

Use `formatDate`, `formatTime`, and `formatRelativeDay` for English dates and times. Navigation and filter state use stable IDs.

Names, messages, SOUL.md, memories, Skills, reminders, and other user-authored API content are displayed as supplied. Emoji names and accessibility labels are English; Chinese emoji search metadata remains available as non-visible synonyms. Official third-party licenses remain unchanged.

Run `npm run typecheck` and `npm run build` separately. The synthetic checks cover desktop/mobile layouts and English copy even when a browser retains a Chinese locale preference.
