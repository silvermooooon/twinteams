# Duet / 双重奏

Duet uses graphite (#202124), white and neutral grey surfaces, with red (#D71920)
for the brand mark and selected navigation indicators. Dark red (#B5121B) is used
for small red foreground symbols. Red is a selective accent, not the background
for every action. Primary actions remain graphite; destructive actions retain
explicit labels, confirmation and their existing error styling. Status is never
communicated by colour alone.

The two opposing, offset frames in the mark represent two voices sharing a space.
`frontend/public/assets/duet-symbol.svg` is used by the shared BrandMark component,
favicon and browser notifications. `duet-logo.svg` is the full wordmark for materials
that have room for it. Existing contact/avatar colours and user artwork remain intact.

`frontend/src/Duet.css` owns the shared visual identity and is loaded after the
component styles. Component styles still own layout, dimensions, responsive
behaviour and motion. The existing system font stack is retained. Flat surfaces
and restrained corners replace the old purple branding without adding controls
or moving panels. The interface remains English; the Chinese product name is 双重奏.

This identity draws on the clarity, angular geometry and selective red described
in [HSBC's published creative principles](https://create.hsbc/external/newsfeed/why-creative-direction-matters.html).
It is Duet's own identity; the palette and mark are not an official HSBC theme.

Duet names both the product and each person’s Agent. UI copy says “My Duet”,
“Duet name” and “your Duet”. A user-provided personal name (for example, Javis)
is retained in chat and self-introductions; Duet remains the Agent’s role.
System prompts, welcome messages, tool descriptions and user-facing errors use
the same terminology.

The product-facing name is Duet. Repository paths, environment variable prefixes,
API headers, browser preference keys, database tables and existing stored content
retain their current identifiers. A visual rebrand does not migrate user data,
reset preferences or rename infrastructure resources.

`python -m app.init_db` also upgrades previous platform-generated default names
to Duet, synchronizes private-chat display titles, and updates only the managed
SOUL.md name heading. Custom names and persona text are retained. The upgrade is
repeatable and does not rewrite conversation history or Runtime journals.

## Interface principles: the art of whitespace

- Leave space around the conversation and its primary actions. Show the minimum
  information needed for the user's current task; preserve the established layout.
- Prefer completing work through conversation with Duet. Keep manual controls
  secondary; use recognizable icons with accessible names and discoverable labels.
  Use explicit text when an icon would make a decision or consequence unclear.
- During a reply, the main chat shows only a simple typing indicator. Do not add
  preparation, queue, worker, tool or model-stage explanations there, or imply that
  a reply has finished merely because the indicator disappears.
- Keep technical status and execution details in their existing inspection surfaces,
  opened by the user. Do not automatically expand them, steal focus or repeat the
  same progress explanation across the chat, banners and sidebar.
- Preserve necessary confirmations, accessible controls and actionable failures.
  State the user-visible consequence and next step briefly; never hide an unsent
  message, a required decision or a failure that prevents the current action.
  Reuse contextual controls rather than introducing technical warning banners.
- Background maintenance stays quiet unless it changes something the owner needs
  to know or decide. Recovered issues do not merit another warning. Follow the
  existing Duet notification and review flow instead of adding technical banners.
