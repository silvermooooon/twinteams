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

The product-facing name is Duet. Repository paths, environment variable prefixes,
API headers, browser preference keys, database tables and existing stored content
retain their current identifiers. A visual rebrand does not migrate user data,
reset preferences or rename infrastructure resources.
