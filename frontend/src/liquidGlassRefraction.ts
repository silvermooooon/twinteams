/* ============================================================================
   Liquid Glass physical lens refraction for Duet web UI
   ----------------------------------------------------------------------------
   Powered by @vepando/liquid-glass-react's Snell's-law optical engine:
   - Imports refractionProfile, buildSpecularMap, buildInteriorMask, and
     imageDataToUrl from @vepando/liquid-glass-react.
   - 9-slice Snell squircle displacement tiles (corners + 2px stretched edges)
     cached globally so large sidebars and tall rails bend light along a wide
     optical bezel with zero lag on resize and no max-size cutoff.
   - Low pre-refraction blur (1.5-3px, matching vepando's lens/clear variants)
     runs BEFORE url(#filter) so underlying contours, shapes, and scrolling
     content visibly bend, magnify, and chromatically disperse inside the rim.
   - True additive RGB chromatic dispersion via feComposite(arithmetic k2=1 k3=1)
     matching @vepando/liquid-glass-react's GlassFilter pipeline.
   - Interior-masked tint (--lg-vepando-mask) keeps the curved bezel crystal-clear
     and deeply refractive while the flat interior carries a clean luminous veil
     for crisp label and icon legibility.
   - Angle-dependent double-ring specular rim (--lg-vepando-specular) rendered
     via plus-lighter catchlight on ::before.
   - Automatically skips text-bearing overlay sheets (.lg-glass-sheet menus,
     pickers, popovers, drawers, modals) and accessibility fallback modes
     (prefers-reduced-transparency, prefers-contrast: more, forced-colors).
   ========================================================================== */

import {
  buildInteriorMask,
  buildSpecularMap,
  imageDataToUrl,
  refractionProfile,
} from '@vepando/liquid-glass-react';

const SVG_NS = 'http://www.w3.org/2000/svg';

type SliceKey = 'tl' | 'tr' | 'bl' | 'br' | 't' | 'b' | 'l' | 'r';

interface TileImage {
  url: string;
  width: number;
  height: number;
}

interface DisplacementSet {
  tiles: Record<SliceKey, TileImage>;
  baseScale: number;
}

interface SliceRect {
  key: SliceKey;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface RefractState {
  key: string;
  id: string;
}

let filterCounter = 0;
let svgDefs: SVGDefsElement | null = null;
const activeElements = new Map<HTMLElement, RefractState>();
const tileCache = new Map<string, DisplacementSet>();
const specularCache = new Map<string, string>();
const maskCache = new Map<string, string>();
let resizeObserver: ResizeObserver | null = null;
let mutationObserver: MutationObserver | null = null;
let refreshScheduled = false;
let initialized = false;

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

function isChromium(): boolean {
  if (typeof navigator === 'undefined') return false;
  const nav = navigator as Navigator & {
    userAgentData?: { brands?: Array<{ brand: string }> };
  };
  const brands = nav.userAgentData?.brands;
  if (brands?.some((b) => /Chromium/.test(b.brand))) return true;
  const ua = navigator.userAgent;
  return /Chrome\/\d+/.test(ua) && !/Firefox|FxiOS|EdgiOS|CriOS/.test(ua);
}

function refractionDisabledByUser(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return true;
  return (
    window.matchMedia('(prefers-reduced-transparency: reduce)').matches ||
    window.matchMedia('(prefers-contrast: more)').matches ||
    window.matchMedia('(forced-colors: active)').matches
  );
}

export function isBackdropRefractionSupported(): boolean {
  if (typeof window === 'undefined') return false;
  if (!isChromium()) return false;
  if (!window.CSS || !CSS.supports('backdrop-filter', 'url(#a)')) return false;
  return !refractionDisabledByUser();
}

function ensureDefs(): SVGDefsElement {
  if (svgDefs && svgDefs.isConnected) return svgDefs;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  svg.setAttribute('width', '0');
  svg.setAttribute('height', '0');
  svg.dataset.lgRefractDefs = 'true';
  svg.style.cssText =
    'position:absolute;width:0;height:0;overflow:hidden;pointer-events:none';
  svgDefs = document.createElementNS(SVG_NS, 'defs');
  svg.appendChild(svgDefs);
  document.body.appendChild(svg);
  return svgDefs;
}

/* Signed distance & outward normal inside the top-left corner quadrant [0..kx, 0..ky] */
function cornerField(
  x: number,
  y: number,
  r: number,
): { depth: number; nx: number; ny: number } {
  if (r > 0 && x < r && y < r) {
    const dx = x - r;
    const dy = y - r;
    const dist = Math.hypot(dx, dy);
    if (dist > 1e-6) {
      return { depth: r - dist, nx: dx / dist, ny: dy / dist };
    }
    return { depth: r, nx: -Math.SQRT1_2, ny: -Math.SQRT1_2 };
  }
  if (x <= y) {
    return { depth: x, nx: -1, ny: 0 };
  }
  return { depth: y, nx: 0, ny: -1 };
}

function encodeChannel(v: number): number {
  return clamp(Math.round(128 + v * 127), 0, 255);
}

function rasterTile(w: number, h: number, data: Uint8ClampedArray): TileImage {
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) return { url: '', width: w, height: h };
  const img = ctx.createImageData(w, h);
  img.data.set(data);
  ctx.putImageData(img, 0, 0);
  return { url: canvas.toDataURL('image/png'), width: w, height: h };
}

function sampleProfile(table: Float32Array, t: number): number {
  const n = table.length;
  if (t >= 1) return 0;
  const x = clamp(t, 0, 1) * n - 0.5;
  if (x <= 0) return table[0] ?? 0;
  const i = Math.floor(x);
  const f = x - i;
  const a = table[i] ?? 0;
  const b = i + 1 < n ? (table[i + 1] ?? 0) : 0;
  return a + (b - a) * f;
}

function getDisplacementSet(
  radius: number,
  bezel: number,
  thickness: number,
  kx: number,
  ky: number,
  dpr: number,
): DisplacementSet {
  const qr = Math.round(radius * 2) / 2;
  const qb = Math.max(2, Math.round(bezel * 2) / 2);
  const qt = Math.max(4, Math.round(thickness * 2) / 2);
  const qkx = Math.round(kx * 2) / 2;
  const qky = Math.round(ky * 2) / 2;
  const d = clamp(Math.round(dpr * 2) / 2, 1, 2);
  const key = `${qr}|${qb}|${qt}|${qkx}|${qky}|${d}`;
  const cached = tileCache.get(key);
  if (cached) return cached;

  // Use @vepando/liquid-glass-react's exact 3D Snell ray-traced squircle profile
  const rawProfile = refractionProfile({
    bezel: qb,
    thickness: qt,
    ior: 1.5,
    profile: 'squircle',
  });
  let peak = 0;
  for (let i = 0; i < rawProfile.length; i++) {
    peak = Math.max(peak, Math.abs(rawProfile[i] ?? 0));
  }
  const safePeak = peak || 1;
  const normalized = new Float32Array(rawProfile.length);
  for (let i = 0; i < rawProfile.length; i++) {
    normalized[i] = (rawProfile[i] ?? 0) / safePeak;
  }
  normalized[rawProfile.length - 1] = 0;

  const cw = Math.max(1, Math.ceil(qkx * d));
  const ch = Math.max(1, Math.ceil(qky * d));

  const magnitude = (depth: number): number => {
    if (depth < 0 || depth >= qb) return 0;
    return sampleProfile(normalized, depth / qb);
  };

  const makeCorner = (flipX: boolean, flipY: boolean): TileImage => {
    const px = new Uint8ClampedArray(cw * ch * 4);
    for (let j = 0; j < ch; j++) {
      const ty = flipY ? ch - 1 - j : j;
      const y = Math.min(qky, (ty + 0.5) / d);
      for (let i = 0; i < cw; i++) {
        const tx = flipX ? cw - 1 - i : i;
        const x = Math.min(qkx, (tx + 0.5) / d);
        const f = cornerField(x, y, qr);
        const mag = magnitude(f.depth);
        const vx = -f.nx * mag * (flipX ? -1 : 1);
        const vy = -f.ny * mag * (flipY ? -1 : 1);
        const o = (j * cw + i) * 4;
        px[o] = encodeChannel(vx);
        px[o + 1] = encodeChannel(vy);
        px[o + 2] = 128;
        px[o + 3] = 255;
      }
    }
    return rasterTile(cw, ch, px);
  };

  const makeEdge = (side: 't' | 'b' | 'l' | 'r'): TileImage => {
    const horizontal = side === 't' || side === 'b';
    const len = horizontal ? ch : cw;
    const w = horizontal ? 2 : cw;
    const h = horizontal ? ch : 2;
    const px = new Uint8ClampedArray(w * h * 4);
    for (let k = 0; k < len; k++) {
      const depth = (k + 0.5) / d;
      const mag = magnitude(depth);
      const v = side === 't' || side === 'l' ? mag : -mag;
      const idx = side === 't' || side === 'l' ? k : len - 1 - k;
      for (let s = 0; s < 2; s++) {
        const i = horizontal ? s : idx;
        const j = horizontal ? idx : s;
        const o = (j * w + i) * 4;
        px[o] = encodeChannel(horizontal ? 0 : v);
        px[o + 1] = encodeChannel(horizontal ? v : 0);
        px[o + 2] = 128;
        px[o + 3] = 255;
      }
    }
    return rasterTile(w, h, px);
  };

  const set: DisplacementSet = {
    tiles: {
      tl: makeCorner(false, false),
      tr: makeCorner(true, false),
      bl: makeCorner(false, true),
      br: makeCorner(true, true),
      t: makeEdge('t'),
      b: makeEdge('b'),
      l: makeEdge('l'),
      r: makeEdge('r'),
    },
    baseScale: safePeak * 2,
  };

  if (tileCache.size > 128) tileCache.clear();
  tileCache.set(key, set);
  return set;
}

function getCachedSpecularUrl(
  w: number,
  h: number,
  radius: number,
  bezel: number,
  dpr: number,
): string {
  const sdpr = w * h > 160_000 ? Math.min(1.25, dpr) : Math.min(2, dpr);
  const key = `${w}|${h}|${Math.round(radius)}|${Math.round(bezel)}|${sdpr}`;
  const cached = specularCache.get(key);
  if (cached) return cached;
  const img = buildSpecularMap(
    { width: w, height: h, radius },
    {
      angle: -135,
      rimWidth: 1.35,
      glowWidth: clamp(bezel * 0.62, 6, 20),
    },
    sdpr,
  );
  const url = imageDataToUrl(img);
  if (specularCache.size > 96) {
    const oldest = specularCache.keys().next().value;
    if (oldest !== undefined) specularCache.delete(oldest);
  }
  specularCache.set(key, url);
  return url;
}

function getCachedInteriorMaskUrl(
  w: number,
  h: number,
  radius: number,
  bezel: number,
): string {
  // Smooth gradient mask can be rasterized at 0.5x for large panels for instant perf
  const down = Math.max(w, h) > 280 ? 0.5 : 1;
  const mw = Math.max(4, Math.round(w * down));
  const mh = Math.max(4, Math.round(h * down));
  const mr = Math.min(radius * down, mw / 2, mh / 2);
  const mb = Math.max(2, bezel * down);
  const key = `${mw}|${mh}|${Math.round(mr)}|${Math.round(mb)}`;
  const cached = maskCache.get(key);
  if (cached) return cached;
  const img = buildInteriorMask({ width: mw, height: mh, radius: mr }, mb, 1);
  const url = imageDataToUrl(img);
  if (maskCache.size > 96) {
    const oldest = maskCache.keys().next().value;
    if (oldest !== undefined) maskCache.delete(oldest);
  }
  maskCache.set(key, url);
  return url;
}

function buildSlices(w: number, h: number, kx: number, ky: number): SliceRect[] {
  const out: SliceRect[] = [
    { key: 'tl', x: 0, y: 0, width: kx, height: ky },
    { key: 'tr', x: w - kx, y: 0, width: kx, height: ky },
    { key: 'bl', x: 0, y: h - ky, width: kx, height: ky },
    { key: 'br', x: w - kx, y: h - ky, width: kx, height: ky },
  ];
  const midW = w - kx * 2;
  const midH = h - ky * 2;
  if (midW > 0.5) {
    out.push({ key: 't', x: kx, y: 0, width: midW, height: ky });
    out.push({ key: 'b', x: kx, y: h - ky, width: midW, height: ky });
  }
  if (midH > 0.5) {
    out.push({ key: 'l', x: 0, y: ky, width: kx, height: midH });
    out.push({ key: 'r', x: w - kx, y: ky, width: kx, height: midH });
  }
  return out;
}

function readAttrNumber(
  el: HTMLElement,
  name: string,
  fallback: number,
): number {
  const raw = el.getAttribute(name);
  if (raw == null || raw === '') return fallback;
  const parsed = Number.parseFloat(raw);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function removeRefraction(el: HTMLElement): void {
  const state = activeElements.get(el);
  if (!state) return;
  const existing = document.getElementById(state.id);
  if (existing) existing.remove();
  el.style.removeProperty('backdrop-filter');
  el.style.removeProperty('-webkit-backdrop-filter');
  el.style.removeProperty('--lg-vepando-specular');
  el.style.removeProperty('--lg-vepando-mask');
  el.classList.remove('is-refracting');
  activeElements.delete(el);
}

function applyRefraction(el: HTMLElement): void {
  const rect = el.getBoundingClientRect();
  const w = Math.round(rect.width);
  const h = Math.round(rect.height);
  if (w < 20 || h < 20) return;

  const isClear = el.classList.contains('lg-glass-clear');
  const isSheet = el.classList.contains('lg-glass-sheet');
  const isPill =
    el.classList.contains('lg-glass-pill') ||
    el.classList.contains('lg-glass-group');
  const allowSheet = el.hasAttribute('data-refract-allow');

  // Floating text-bearing overlays (menus, pickers, popovers, drawers, modals)
  // keep pure CSS heavy frost so underlying page text never bleeds through.
  if (isSheet && !allowSheet) {
    removeRefraction(el);
    return;
  }

  const cs = getComputedStyle(el);
  let radius = Number.parseFloat(cs.borderTopLeftRadius) || 0;
  radius = Math.min(radius, w / 2, h / 2);

  const strength = clamp(readAttrNumber(el, 'data-refract', 0.88), 0, 2);
  if (strength <= 0) return;

  const shortSide = Math.min(w, h);

  // Wide optical bezel calibrated to @vepando/liquid-glass-react's lens/clear
  // profile: fits cleanly to the corner radius (up to 34px on large panels)
  // so light visibly wraps and magnifies around the entire rounded perimeter.
  const fitBezel = radius >= 12 ? radius : Math.min(shortSide * 0.3, 24);
  const defaultBezel = clamp(
    Math.min(shortSide * 0.34, fitBezel, 34),
    10,
    Math.max(10, Math.min(radius || shortSide * 0.4, shortSide * 0.46)),
  );
  const bezel = clamp(
    readAttrNumber(el, 'data-refract-bezel', defaultBezel),
    6,
    shortSide * 0.48,
  );
  const kx = Math.min(Math.max(radius, bezel), w / 2);
  const ky = Math.min(Math.max(radius, bezel), h / 2);

  // Calibrated to @vepando/liquid-glass-react's optical lens:
  // - Taller slab thickness (2.1-2.4x bezel) gives dramatic Snell bending right
  //   inside the rim while keeping the flat plateau completely undistorted.
  // - Low pre-refraction blur (1.5-2.5px) keeps bent contours crisp instead of
  //   smearing the lens away like thin frost.
  const thicknessRatio = isClear ? 2.4 : shortSide <= 84 ? 2.25 : 2.15;
  const thickness = bezel * thicknessRatio;

  const defaultBlur = isClear
    ? 0.5
    : isPill || shortSide <= 62
      ? 1.5
      : 2.5;
  const blur = readAttrNumber(el, 'data-refract-blur', defaultBlur);

  // Physical refraction multiplier (vepando clear=1.3, lens=1.0, regular=2.0)
  const lensGain = isClear ? 1.55 : shortSide <= 84 ? 1.48 : 1.42;
  const refractionMult = Math.round(lensGain * strength * 100) / 100;

  const defaultDispersion = isClear ? 0.15 : shortSide <= 90 ? 0.135 : 0.115;
  const dispersion = clamp(
    readAttrNumber(el, 'data-refract-dispersion', defaultDispersion),
    0,
    0.25,
  );
  const dpr = clamp(window.devicePixelRatio || 1, 1, 2);

  const key = [
    w,
    h,
    Math.round(radius),
    Math.round(bezel),
    Math.round(thickness),
    refractionMult,
    blur,
    dispersion,
    dpr,
  ].join('|');
  const prev = activeElements.get(el);
  if (prev && prev.key === key) return;

  const dispSet = getDisplacementSet(radius, bezel, thickness, kx, ky, dpr);
  const slices = buildSlices(w, h, kx, ky);
  if (!dispSet.tiles.tl.url) return;

  const scale = clamp(dispSet.baseScale * refractionMult, 24, 150);

  const specularUrl = getCachedSpecularUrl(w, h, radius, bezel, dpr);
  const maskUrl = getCachedInteriorMaskUrl(w, h, radius, bezel);

  const id = prev ? prev.id : `lg-refract-${++filterCounter}`;
  const oldFilter = document.getElementById(id);
  if (oldFilter) oldFilter.remove();

  const filter = document.createElementNS(SVG_NS, 'filter');
  filter.setAttribute('id', id);
  filter.setAttribute('x', '0');
  filter.setAttribute('y', '0');
  filter.setAttribute('width', String(w));
  filter.setAttribute('height', String(h));
  filter.setAttribute('filterUnits', 'userSpaceOnUse');
  filter.setAttribute('primitiveUnits', 'userSpaceOnUse');
  filter.setAttribute('color-interpolation-filters', 'sRGB');

  const addNode = (tag: string, attrs: Record<string, string | number>) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      node.setAttribute(k, String(v));
    }
    filter.appendChild(node);
    return node;
  };

  addNode('feFlood', {
    'flood-color': '#808080',
    'flood-opacity': 1,
    x: 0,
    y: 0,
    width: w,
    height: h,
    result: 'n',
  });

  for (const s of slices) {
    const tile = dispSet.tiles[s.key];
    const feImage = document.createElementNS(SVG_NS, 'feImage');
    feImage.setAttribute('href', tile.url);
    feImage.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', tile.url);
    feImage.setAttribute('x', s.x.toFixed(2));
    feImage.setAttribute('y', s.y.toFixed(2));
    feImage.setAttribute('width', s.width.toFixed(2));
    feImage.setAttribute('height', s.height.toFixed(2));
    feImage.setAttribute('preserveAspectRatio', 'none');
    feImage.setAttribute('result', `s${s.key}`);
    filter.appendChild(feImage);
  }

  const merge = document.createElementNS(SVG_NS, 'feMerge');
  merge.setAttribute('result', 'map');
  const baseNode = document.createElementNS(SVG_NS, 'feMergeNode');
  baseNode.setAttribute('in', 'n');
  merge.appendChild(baseNode);
  for (const s of slices) {
    const node = document.createElementNS(SVG_NS, 'feMergeNode');
    node.setAttribute('in', `s${s.key}`);
    merge.appendChild(node);
  }
  filter.appendChild(merge);

  // Additive RGB chromatic dispersion matching @vepando/liquid-glass-react GlassFilter
  if (dispersion > 0.001) {
    const channels = [
      { c: 'r', s: scale * (1 + dispersion), m: '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0' },
      { c: 'g', s: scale, m: '0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0' },
      { c: 'b', s: scale * (1 - dispersion), m: '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0' },
    ];
    for (const ch of channels) {
      addNode('feDisplacementMap', {
        in: 'SourceGraphic',
        in2: 'map',
        scale: ch.s.toFixed(2),
        xChannelSelector: 'R',
        yChannelSelector: 'G',
        result: `bent-${ch.c}`,
      });
      addNode('feColorMatrix', {
        in: `bent-${ch.c}`,
        type: 'matrix',
        values: ch.m,
        result: ch.c,
      });
    }
    addNode('feComposite', {
      in: 'r',
      in2: 'g',
      operator: 'arithmetic',
      k1: 0,
      k2: 1,
      k3: 1,
      k4: 0,
      result: 'rg',
    });
    addNode('feComposite', {
      in: 'rg',
      in2: 'b',
      operator: 'arithmetic',
      k1: 0,
      k2: 1,
      k3: 1,
      k4: 0,
    });
  } else {
    addNode('feDisplacementMap', {
      in: 'SourceGraphic',
      in2: 'map',
      scale: scale.toFixed(2),
      xChannelSelector: 'R',
      yChannelSelector: 'G',
    });
  }

  ensureDefs().appendChild(filter);

  // CSS low-radius blur FIRST, then Snell SVG lens refraction, then optical saturate/lift
  const sat = isClear ? 150 : 162;
  const bright = isClear ? 1.02 : 1.035;
  const filterValue = `${blur > 0 ? `blur(${blur}px) ` : ''}url(#${id}) saturate(${sat}%) brightness(${bright})`;
  el.style.setProperty('backdrop-filter', filterValue);
  el.style.setProperty('-webkit-backdrop-filter', filterValue);
  el.style.setProperty('--lg-vepando-specular', `url("${specularUrl}")`);
  el.style.setProperty('--lg-vepando-mask', `url("${maskUrl}")`);
  el.classList.add('is-refracting');
  activeElements.set(el, { key, id });
}

export function refreshLiquidGlassRefraction(): void {
  if (typeof document === 'undefined') return;

  for (const el of Array.from(activeElements.keys())) {
    if (!el.isConnected) {
      removeRefraction(el);
      resizeObserver?.unobserve(el);
    }
  }

  const supported = isBackdropRefractionSupported();
  const targets = document.querySelectorAll<HTMLElement>('[data-refract]');
  let anyActive = false;

  targets.forEach((el) => {
    if (supported) {
      applyRefraction(el);
      resizeObserver?.observe(el);
      if (activeElements.has(el)) anyActive = true;
    } else {
      removeRefraction(el);
      resizeObserver?.unobserve(el);
    }
  });

  document.documentElement.classList.toggle('lg-refraction-on', anyActive);
}

function scheduleRefresh(): void {
  if (refreshScheduled) return;
  refreshScheduled = true;
  requestAnimationFrame(() => {
    refreshScheduled = false;
    refreshLiquidGlassRefraction();
  });
}

function updatePointerHighlight(event: PointerEvent): void {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const host = target.closest<HTMLElement>(
    '.pill, .rail-item, .notification-switch, .twin-switch, .header-actions.lg-glass-group, .sidebar-heading-actions.lg-glass-group',
  );
  if (!host) return;
  const rect = host.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return;
  const px = clamp(((event.clientX - rect.left) / rect.width) * 100, 0, 100);
  const py = clamp(((event.clientY - rect.top) / rect.height) * 100, 0, 100);
  host.style.setProperty('--lg-px', `${px.toFixed(1)}%`);
  host.style.setProperty('--lg-py', `${py.toFixed(1)}%`);
}

export function initLiquidGlassRefraction(): void {
  if (initialized || typeof window === 'undefined' || typeof document === 'undefined') {
    return;
  }
  initialized = true;

  if (typeof ResizeObserver === 'function') {
    resizeObserver = new ResizeObserver((entries) => {
      if (!isBackdropRefractionSupported()) return;
      for (const entry of entries) {
        if (entry.target instanceof HTMLElement && entry.target.hasAttribute('data-refract')) {
          applyRefraction(entry.target);
        }
      }
    });
  }

  if (typeof MutationObserver === 'function' && document.body) {
    mutationObserver = new MutationObserver(() => {
      scheduleRefresh();
    });
    mutationObserver.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['data-refract', 'data-refract-allow', 'class'],
    });
  }

  document.addEventListener('pointermove', updatePointerHighlight, { passive: true });
  document.addEventListener('pointerdown', updatePointerHighlight, { passive: true });

  const mediaQueries = [
    '(prefers-reduced-transparency: reduce)',
    '(prefers-contrast: more)',
    '(forced-colors: active)',
  ];
  for (const query of mediaQueries) {
    const mq = window.matchMedia?.(query);
    mq?.addEventListener?.('change', scheduleRefresh);
  }

  if (document.fonts?.ready) {
    document.fonts.ready.then(scheduleRefresh).catch(() => {});
  }

  scheduleRefresh();
}
