/* ============================================================================
   Liquid Glass edge refraction (lensing) for Duet web UI
   ----------------------------------------------------------------------------
   Progressively enhances elements marked [data-refract] with a 9-slice
   Snell-Descartes squircle SVG feDisplacementMap rim lens in Chromium browsers
   (which support url(#filter) inside backdrop-filter).

   Architecture adapted from glassy-liquid's optical pipeline:
   - 9-slice displacement tiles (corners + 2px stretched edges) cached globally,
     so large sidebars and tall rails refract with negligible CPU/memory cost
     and no max-size cutoff.
   - CSS blur() runs BEFORE url(#filter) so softened background shapes & light
     bend crisply along the curved bezel rim instead of blurring the lens away.
   - Gentle sRGB tone-lift table (feComponentTransfer) keeps dark chrome labels
     legible over darker scrolled content without turning the panel milky.
   - Automatically skips text-bearing overlay sheets (.lg-glass-sheet menus,
     pickers, popovers, drawers, modals) and accessibility fallback modes
     (prefers-reduced-transparency, prefers-contrast: more, forced-colors).
   ========================================================================== */

const SVG_NS = 'http://www.w3.org/2000/svg';

type SliceKey = 'tl' | 'tr' | 'bl' | 'br' | 't' | 'b' | 'l' | 'r';

interface TileImage {
  url: string;
  width: number;
  height: number;
}

type DisplacementSet = Record<SliceKey, TileImage>;

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
const profileCache = new Map<string, Float32Array>();
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

/* Snell-Descartes squircle bezel optical profile (ior=1.5, thickness=1, float=0.55) */
function squircleHeight(t: number): number {
  const x = clamp(t, 0, 1);
  return Math.pow(1 - Math.pow(1 - x, 4), 0.25);
}

function getOpticalProfile(ior = 1.5, thickness = 1, floatDist = 0.55): Float32Array {
  const key = `${ior}|${thickness}|${floatDist}`;
  const cached = profileCache.get(key);
  if (cached) return cached;

  const samples = 256;
  const raw = new Float32Array(samples);
  let peak = 0;
  const h = 1e-4;

  for (let i = 0; i < samples; i++) {
    const t = (i + 0.5) / samples;
    const a = Math.max(0, t - h);
    const b = Math.min(1, t + h);
    const slope = (thickness * (squircleHeight(b) - squircleHeight(a))) / (b - a);
    const alpha = Math.atan(Math.abs(slope));
    const theta = Math.asin(clamp(Math.sin(alpha) / ior, -1, 1));
    const deviation = alpha - theta;
    const height = floatDist + thickness * squircleHeight(t);
    const disp = Math.sign(slope) * height * Math.tan(deviation);
    raw[i] = disp;
    peak = Math.max(peak, Math.abs(disp));
  }

  const values = new Float32Array(samples);
  if (peak > 0) {
    for (let i = 0; i < samples; i++) values[i] = raw[i]! / peak;
  }
  values[samples - 1] = 0;
  profileCache.set(key, values);
  return values;
}

function sampleProfile(table: Float32Array, t: number): number {
  const n = table.length;
  if (t >= 1) return 0;
  const x = clamp(t, 0, 1) * n - 0.5;
  if (x <= 0) return table[0]!;
  const i = Math.floor(x);
  const f = x - i;
  const a = table[i]!;
  const b = i + 1 < n ? table[i + 1]! : 0;
  return a + (b - a) * f;
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
  return clamp(Math.round(127.5 + v * 127.5), 0, 255);
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

function getDisplacementSet(
  radius: number,
  bezel: number,
  kx: number,
  ky: number,
  dpr: number,
): DisplacementSet {
  const qr = Math.round(radius * 2) / 2;
  const qb = Math.round(bezel * 2) / 2;
  const qkx = Math.round(kx * 2) / 2;
  const qky = Math.round(ky * 2) / 2;
  const d = clamp(Math.round(dpr * 2) / 2, 1, 2);
  const key = `${qr}|${qb}|${qkx}|${qky}|${d}`;
  const cached = tileCache.get(key);
  if (cached) return cached;

  const table = getOpticalProfile();
  const cw = Math.max(1, Math.ceil(qkx * d));
  const ch = Math.max(1, Math.ceil(qky * d));

  const magnitude = (depth: number): number => {
    if (qb <= 0) return 0;
    return sampleProfile(table, Math.max(0, depth) / qb);
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
    tl: makeCorner(false, false),
    tr: makeCorner(true, false),
    bl: makeCorner(false, true),
    br: makeCorner(true, true),
    t: makeEdge('t'),
    b: makeEdge('b'),
    l: makeEdge('l'),
    r: makeEdge('r'),
  };

  if (tileCache.size > 128) tileCache.clear();
  tileCache.set(key, set);
  return set;
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

function toneTransferTable(lift: number): string {
  const out: string[] = [];
  for (let i = 0; i <= 32; i++) {
    const v = i / 32;
    const lifted = v + lift * Math.pow(1 - v, 2);
    out.push(clamp(lifted, 0, 1).toFixed(4));
  }
  return out.join(' ');
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

  const strength = clamp(readAttrNumber(el, 'data-refract', 0.85), 0, 2);
  if (strength <= 0) return;

  const shortSide = Math.min(w, h);
  let bezel = readAttrNumber(
    el,
    'data-refract-bezel',
    clamp(radius * 0.78 || 16, 12, 22),
  );
  bezel = Math.min(bezel, shortSide * 0.42);
  const kx = Math.min(Math.max(radius, bezel), w / 2);
  const ky = Math.min(Math.max(radius, bezel), h / 2);

  // Calibrated against glassy-liquid Control Center: ~9px blur BEFORE SVG rim
  // refraction keeps shapes/color perceptible through the body while curving
  // light crisply along the squircle rim.
  const defaultBlur = isClear ? 2 : 9;
  const blur = readAttrNumber(el, 'data-refract-blur', defaultBlur);

  // Adaptive displacement: capsules refract more deeply, large sidebars stay
  // calm and optical along their edges without distorting interior list rows.
  const baseRefract =
    shortSide <= 64
      ? Math.max(10, shortSide * 0.34)
      : Math.max(15, 22 - (shortSide - 64) * 0.03);
  const refraction = Math.round(baseRefract * strength * 10) / 10;
  const scale = 2 * refraction;

  const defaultDispersion = isClear ? 0.14 : shortSide <= 90 ? 0.08 : 0.055;
  const dispersion = clamp(
    readAttrNumber(el, 'data-refract-dispersion', defaultDispersion),
    0,
    0.25,
  );
  const lift = isClear ? 0 : readAttrNumber(el, 'data-refract-lift', 0.13);
  const dpr = clamp(window.devicePixelRatio || 1, 1, 2);

  const key = [
    w,
    h,
    Math.round(radius),
    Math.round(bezel),
    refraction,
    blur,
    dispersion,
    lift,
    dpr,
  ].join('|');
  const prev = activeElements.get(el);
  if (prev && prev.key === key) return;

  const tiles = getDisplacementSet(radius, bezel, kx, ky, dpr);
  const slices = buildSlices(w, h, kx, ky);
  if (!tiles.tl.url) return;

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
    const tile = tiles[s.key];
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

  if (dispersion > 0.001) {
    const spread = Math.min(0.28, dispersion * 0.5);
    const channels = [
      { c: 'r', s: scale * (1 + spread), m: '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0' },
      { c: 'g', s: scale, m: '0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0' },
      { c: 'b', s: scale * (1 - spread), m: '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0' },
    ];
    for (const ch of channels) {
      addNode('feDisplacementMap', {
        in: 'SourceGraphic',
        in2: 'map',
        scale: ch.s.toFixed(2),
        xChannelSelector: 'R',
        yChannelSelector: 'G',
        result: `d${ch.c}`,
      });
      addNode('feColorMatrix', {
        in: `d${ch.c}`,
        type: 'matrix',
        values: ch.m,
        result: ch.c,
      });
    }
    addNode('feBlend', { in: 'r', in2: 'g', mode: 'screen', result: 'rg' });
    addNode('feBlend', { in: 'rg', in2: 'b', mode: 'screen', result: 'refracted' });
  } else {
    addNode('feDisplacementMap', {
      in: 'SourceGraphic',
      in2: 'map',
      scale: scale.toFixed(2),
      xChannelSelector: 'R',
      yChannelSelector: 'G',
      result: 'refracted',
    });
  }

  if (lift > 0.001) {
    const table = toneTransferTable(lift);
    const ct = document.createElementNS(SVG_NS, 'feComponentTransfer');
    ct.setAttribute('in', 'refracted');
    ct.setAttribute('result', 'out');
    for (const ch of ['R', 'G', 'B'] as const) {
      const fn = document.createElementNS(SVG_NS, `feFunc${ch}`);
      fn.setAttribute('type', 'table');
      fn.setAttribute('tableValues', table);
      ct.appendChild(fn);
    }
    filter.appendChild(ct);
  }

  ensureDefs().appendChild(filter);

  // CSS blur FIRST, then SVG squircle rim refraction, then optical saturation.
  const sat = isClear ? 145 : 172;
  const filterValue = `${blur > 0 ? `blur(${blur}px) ` : ''}url(#${id}) saturate(${sat}%)`;
  el.style.setProperty('backdrop-filter', filterValue);
  el.style.setProperty('-webkit-backdrop-filter', filterValue);
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
