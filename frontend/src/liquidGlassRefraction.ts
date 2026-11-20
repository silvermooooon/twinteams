/* ============================================================================
   Liquid Glass edge refraction (lensing) for Duet web UI
   ----------------------------------------------------------------------------
   Progressively enhances elements marked [data-refract] with an SVG
   feDisplacementMap rim lens in Chromium browsers (which support url(#filter)
   inside backdrop-filter). Automatically skips under
   prefers-reduced-transparency: reduce or prefers-contrast: more, and cleans
   up filters when React unmounts elements.
   ========================================================================== */

const SVG_NS = 'http://www.w3.org/2000/svg';

interface RefractState {
  key: string;
  id: string;
}

let filterCounter = 0;
let svgDefs: SVGDefsElement | null = null;
const activeElements = new Map<HTMLElement, RefractState>();
let resizeObserver: ResizeObserver | null = null;
let mutationObserver: MutationObserver | null = null;
let refreshScheduled = false;
let initialized = false;

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

function roundedRectField(
  x: number,
  y: number,
  hw: number,
  hh: number,
  r: number,
): { d: number; nx: number; ny: number } {
  const ax = Math.abs(x);
  const ay = Math.abs(y);
  const qx = ax - (hw - r);
  const qy = ay - (hh - r);
  const sx = x < 0 ? -1 : 1;
  const sy = y < 0 ? -1 : 1;

  if (qx > 0 && qy > 0) {
    const len = Math.hypot(qx, qy) || 1;
    return { d: len - r, nx: (qx / len) * sx, ny: (qy / len) * sy };
  }
  if (qx > qy) {
    return { d: qx - r, nx: sx, ny: 0 };
  }
  return { d: qy - r, nx: 0, ny: sy };
}

function buildDisplacementMap(
  w: number,
  h: number,
  radius: number,
  bezel: number,
): string | null {
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;

  const img = ctx.createImageData(w, h);
  const data = img.data;
  const hw = w / 2;
  const hh = h / 2;

  for (let py = 0; py < h; py++) {
    for (let px = 0; px < w; px++) {
      const x = px + 0.5 - hw;
      const y = py + 0.5 - hh;
      const field = roundedRectField(x, y, hw, hh, radius);
      const inside = -field.d;
      let vx = 0;
      let vy = 0;

      if (inside < bezel && inside > -1) {
        const t = 1 - Math.max(0, inside) / bezel;
        const smooth = t * t * (3 - 2 * t);
        const curve = Math.pow(smooth, 1.6);
        vx = -field.nx * curve;
        vy = -field.ny * curve;
      }

      const idx = (py * w + px) * 4;
      data[idx] = Math.round(128 + Math.max(-1, Math.min(1, vx)) * 127);
      data[idx + 1] = Math.round(128 + Math.max(-1, Math.min(1, vy)) * 127);
      data[idx + 2] = 128;
      data[idx + 3] = 255;
    }
  }

  ctx.putImageData(img, 0, 0);
  return canvas.toDataURL('image/png');
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
  // Skip tiny elements or giant full-viewport areas to keep canvas generation fast
  if (w < 20 || h < 20 || w > 1200 || h > 900) return;

  const cs = getComputedStyle(el);
  let radius = Number.parseFloat(cs.borderTopLeftRadius) || 0;
  radius = Math.min(radius, w / 2, h / 2);

  const strength = Math.max(0, Math.min(2, readAttrNumber(el, 'data-refract', 0.85)));
  if (strength <= 0) return;

  let bezel = readAttrNumber(
    el,
    'data-refract-bezel',
    Math.max(8, Math.min(18, radius * 0.5 || 12)),
  );
  bezel = Math.min(bezel, Math.min(w, h) * 0.3);

  const isClear = el.classList.contains('lg-glass-clear');
  const isSheet = el.classList.contains('lg-glass-sheet');
  const isText = el.classList.contains('lg-glass-text');
  // Chromium's SVG feDisplacementMap inside backdrop-filter can clip blur near
  // element edges under DPR scaling or filter bounds, causing sharp text bleed
  // on menus, popovers, modals, and text-bearing chrome. Keep pure CSS
  // backdrop-filter on sheets and text-bearing glass; apply SVG rim lensing
  // only to clear media overlays and non-text glass emblems.
  if (isSheet || isText) {
    removeRefraction(el);
    return;
  }
  const defaultBlur = isClear ? 1.5 : 14;
  const blur = readAttrNumber(el, 'data-refract-blur', defaultBlur);
  const dispersion = Math.max(
    0,
    Math.min(0.14, readAttrNumber(el, 'data-refract-dispersion', 0.035)),
  );
  const scale = Math.round(bezel * 1.1 * strength);

  const key = [w, h, Math.round(radius), Math.round(bezel), scale, blur, dispersion].join('|');
  const prev = activeElements.get(el);
  if (prev && prev.key === key) return;

  const mapUrl = buildDisplacementMap(w, h, radius, bezel);
  if (!mapUrl) return;

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
  filter.setAttribute('color-interpolation-filters', 'sRGB');

  const addNode = (tag: string, attrs: Record<string, string | number>) => {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) {
      node.setAttribute(k, String(v));
    }
    filter.appendChild(node);
  };

  const feImage = document.createElementNS(SVG_NS, 'feImage');
  feImage.setAttribute('href', mapUrl);
  feImage.setAttributeNS('http://www.w3.org/1999/xlink', 'xlink:href', mapUrl);
  feImage.setAttribute('x', '0');
  feImage.setAttribute('y', '0');
  feImage.setAttribute('width', String(w));
  feImage.setAttribute('height', String(h));
  feImage.setAttribute('preserveAspectRatio', 'none');
  feImage.setAttribute('result', 'raw');
  filter.appendChild(feImage);

  addNode('feGaussianBlur', {
    in: 'raw',
    stdDeviation: Math.max(1.5, bezel * 0.22).toFixed(1),
    result: 'map',
  });

  if (dispersion > 0) {
    const channels = [
      { c: 'r', s: scale, m: '1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0' },
      { c: 'g', s: scale * (1 - dispersion), m: '0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0' },
      { c: 'b', s: scale * (1 - 2 * dispersion), m: '0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0' },
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
      scale,
      xChannelSelector: 'R',
      yChannelSelector: 'G',
    });
  }

  ensureDefs().appendChild(filter);

  const filterValue = `url(#${id}) blur(${blur}px) saturate(${isSheet ? 195 : 185}%)`;
  el.style.setProperty('backdrop-filter', filterValue);
  el.style.setProperty('-webkit-backdrop-filter', filterValue);
  el.classList.add('is-refracting');
  activeElements.set(el, { key, id });
}

export function refreshLiquidGlassRefraction(): void {
  if (typeof document === 'undefined') return;

  // Prune detached elements first
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
      attributeFilter: ['data-refract', 'class'],
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
