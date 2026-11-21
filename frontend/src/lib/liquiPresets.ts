import type { LiquiGlassProps, LiquiTheme } from '@liqui-design/glass';

/**
 * Duet × Liqui Design theme calibration.
 *
 * Keeps Duet's graphite/white identity with selective Duet crimson accents
 * (`#d71920`), while using `@liqui-design/glass`'s real SVG refraction kernel,
 * 2x baked specular rim lighting, and continuous squircle corners.
 */
export const duetLiquiTheme: Partial<LiquiTheme> = {
  glass: {
    material: 'auto',
    profile: 'squircle',
    finish: 'classic',
    frost: 0.36,
    specular: 0.9,
    dispersion: 0,
    saturation: 1.75,
    radiusScale: 1,
    refractionScale: 1,
    bezelScale: 1,
    blurScale: 1,
    brightness: 1.02,
    edgeShade: 0.52,
    hairline: 0.14,
    tintFloor: 0.22,
  },
  light: {
    text: 'rgba(20, 22, 26, 0.92)',
    'text-dim': 'rgba(44, 50, 64, 0.72)',
    tint: 'rgba(255, 255, 255, 0.56)',
    'tint-deep': 'rgba(240, 246, 255, 0.24)',
    'rim-hi': 'rgba(255, 255, 255, 0.96)',
    'rim-lo': 'rgba(205, 222, 248, 0.42)',
    shadow:
      '0 18px 44px -8px rgba(15, 25, 48, 0.17), 0 4px 12px -2px rgba(15, 25, 48, 0.08)',
    highlight: 'rgba(255, 255, 255, 0.62)',
    control: 'rgba(255, 255, 255, 0.74)',
    'control-rim': 'rgba(18, 30, 54, 0.15)',
    accent: '#202124',
    danger: '#d71920',
    'danger-text': '#b5121b',
    scrim: 'rgba(15, 22, 38, 0.26)',
  },
};

/** Large inset floating chrome panels (left rail, sidebar, right detail panel, find panel). */
export const LIQUI_PANEL_GLASS = {
  radius: 22,
  blur: 4,
  frost: 0.34,
  refraction: 96,
  bezel: 22,
  elevated: true,
} satisfies Partial<LiquiGlassProps>;

/** Floating horizontal bars (chat top header, composer capsule, secondary page headers). */
export const LIQUI_BAR_GLASS = {
  radius: 20,
  blur: 4,
  frost: 0.36,
  refraction: 88,
  bezel: 18,
  elevated: true,
} satisfies Partial<LiquiGlassProps>;

/** Compact floating capsules in the topbar (toolbar strips, global search pill). */
export const LIQUI_CAPSULE_GLASS = {
  radius: 21,
  blur: 3,
  frost: 0.34,
  refraction: 68,
  bezel: 15,
  elevated: true,
} satisfies Partial<LiquiGlassProps>;

/**
 * High-legibility floating text-bearing sheets (menus, popovers, dialogs, pickers,
 * search results, drawers): strong optical blur + luminous frosted tint so underlying
 * app text is NEVER recognizable through them, while preserving real Liqui refraction
 * and baked specular rim lighting.
 */
export const LIQUI_SHEET_GLASS = {
  elevated: true,
  radius: 18,
  blur: 16,
  frost: 0.92,
  refraction: 92,
  bezel: 18,
  saturation: 1.85,
  specular: 0.92,
} satisfies Partial<LiquiGlassProps>;

/** Compact menu popup glass (account menu, app options, composer menus, row context menus). */
export const LIQUI_MENU_GLASS = {
  ...LIQUI_SHEET_GLASS,
  radius: 14,
  refraction: 84,
  bezel: 16,
} satisfies Partial<LiquiGlassProps>;

/** Elevated modal dialog glass over scrim. */
export const LIQUI_DIALOG_GLASS = {
  ...LIQUI_SHEET_GLASS,
  radius: 22,
  refraction: 100,
  bezel: 22,
} satisfies Partial<LiquiGlassProps>;
