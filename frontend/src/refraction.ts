import { useEffect } from 'react';

const SVG_NS = 'http://www.w3.org/2000/svg';

type RefractState = {
  id: string;
  key: string;
};

let filterCounter = 0;
let defsNode: SVGDefsElement | null = null;
const applied = new WeakMap<HTMLElement, RefractState>();

function isChromiumEngine(): boolean {
  if (typeof navigator === 'undefined') return false;
  const uaData = (navigator as Navigator & { userAgentData?: { brands?: Array<{ brand: string }> } }).userAgentData;
  if (uaData?.brands?.some(b => /Chromium/i.test(b.brand))) return true;
  return /Chrome\/\d+/.test(navigator.userAgent) && !/Firefox|FxiOS|EdgiOS|CriOS/.test(navigator.userAgent);
}

function refractionAllowed(): boolean {
  if (typeof window === 'undefined' || typeof CSS === 'undefined') return false;
  if (!isChromiumEngine()) return false;
  if (!CSS.supports('backdrop-filter', 'url(#a)')) return false;
  if (window.matchMedia('(prefers-reduced-transparency: reduce)').matches) return false;
  if (window.matchMedia('(prefers-contrast: more)').matches) return false;
  if (document.documentElement.getAttribute('data-reduced-transparency') === 'true') return false;
  return true;
}

function ensureDefs(): SVGDefsElement {
  if (defsNode?.isConnected) return defsNode;
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('width', '0');
  svg.setAttribute('height', '0');
  svg.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;pointer-events:none';
  defsNode = document.createElementNS(SVG_NS, 'defs');
  svg.appendChild(defsNode);
  document.body.appendChild(svg);
  return defsNode;
}

function roundedRectField(x: number, y: number, hw: number, hh: number, r: number) {
  const ax = Math.abs(x);
  const ay = Math.abs(y);
  const qx = ax - (hw - r);
  const qy = ay - (hh - r);
  const sx = x < 0 ? -1 : 1;
  const sy = y < 0 ? -1 : 1;
  if (qx > 0 && qy > 0) {
    const len = Math.sqrt(qx * qx + qy * qy) || 1;
    return { d: len - r, nx: (qx / len) * sx, ny: (qy / len) * sy };
  }
  if (qx > qy) return { d: qx - r, nx: sx, ny: 0 };
  return { d: qy - r, nx: 0, ny: sy };
}

function buildBezelMaps(w: number, h: number, radius: number, bezel: number): { dispHref: string; maskHref: string } | null {
  // Keep 1:1 fidelity on toolbars/composers; downscale only very large full-height panels.
  const scaleFactor = w * h > 160000 ? 0.5 : 1;
  const mw = Math.max(16, Math.round(w * scaleFactor));
  const mh = Math.max(16, Math.round(h * scaleFactor));
  const mr = radius * scaleFactor;
  const mb = Math.max(5, bezel * scaleFactor);

  const dispCanvas = document.createElement('canvas');
  dispCanvas.width = mw;
  dispCanvas.height = mh;
  const dispCtx = dispCanvas.getContext('2d');
  if (!dispCtx) return null;

  const maskCanvas = document.createElement('canvas');
  maskCanvas.width = mw;
  maskCanvas.height = mh;
  const maskCtx = maskCanvas.getContext('2d');
  if (!maskCtx) return null;

  const dispImg = dispCtx.createImageData(mw, mh);
  const maskImg = maskCtx.createImageData(mw, mh);
  const dData = dispImg.data;
  const mData = maskImg.data;
  const hw = mw / 2;
  const hh = mh / 2;

  for (let py = 0; py < mh; py++) {
    for (let px = 0; px < mw; px++) {
      const x = px + 0.5 - hw;
      const y = py + 0.5 - hh;
      const field = roundedRectField(x, y, hw, hh, mr);
      const inside = -field.d;
      let vx = 0;
      let vy = 0;
      let rimAlpha = 0;
      if (inside < mb && inside > -1) {
        const t = 1 - Math.max(0, inside) / mb;
        const smooth = t * t * (3 - 2 * t);
        // Steep lens slope near the outer bevel, decaying smoothly into the frosted interior.
        const m = Math.pow(t, 1.55);
        vx = -field.nx * m;
        vy = -field.ny * m;
        rimAlpha = Math.min(1, Math.pow(smooth, 0.65) * 1.15);
      }
      const idx = (py * mw + px) * 4;
      dData[idx] = Math.round(128 + Math.max(-1, Math.min(1, vx)) * 127);
      dData[idx + 1] = Math.round(128 + Math.max(-1, Math.min(1, vy)) * 127);
      dData[idx + 2] = 128;
      dData[idx + 3] = 255;

      mData[idx] = 255;
      mData[idx + 1] = 255;
      mData[idx + 2] = 255;
      mData[idx + 3] = Math.round(rimAlpha * 255);
    }
  }
  dispCtx.putImageData(dispImg, 0, 0);
  maskCtx.putImageData(maskImg, 0, 0);
  return {
    dispHref: dispCanvas.toDataURL('image/png'),
    maskHref: maskCanvas.toDataURL('image/png'),
  };
}

function removeRefraction(el: HTMLElement) {
  const prev = applied.get(el);
  if (!prev) return;
  document.getElementById(prev.id)?.remove();
  el.style.removeProperty('backdrop-filter');
  el.style.removeProperty('-webkit-backdrop-filter');
  el.classList.remove('is-duet-refracting');
  applied.delete(el);
}

function applyRefraction(el: HTMLElement) {
  // Never stack a second lens inside an already-refracting ancestor.
  if (el.parentElement?.closest('[data-duet-refract]')) {
    removeRefraction(el);
    return;
  }

  const rect = el.getBoundingClientRect();
  const w = Math.round(rect.width);
  const h = Math.round(rect.height);
  if (w < 24 || h < 24) return;

  const cs = getComputedStyle(el);
  const rawRadius = parseFloat(cs.borderTopLeftRadius) || 14;
  const radius = Math.min(rawRadius, w / 2, h / 2);
  const bezel = Math.min(Math.max(12, Math.min(16, radius * 0.9)), Math.min(w, h) * 0.34);
  const strength = Math.max(0.2, Math.min(1.5, parseFloat(el.getAttribute('data-duet-refract') || '0.8') || 0.8));
  const blur = Math.max(6, Math.min(36, parseFloat(el.getAttribute('data-duet-refract-blur') || '24') || 24));
  const scale = Math.round(bezel * 2.35 * strength);

  const key = `${w}|${h}|${Math.round(radius)}|${Math.round(bezel)}|${scale}|${blur}`;
  const prev = applied.get(el);
  if (prev?.key === key) return;

  const id = prev?.id || `duet-refract-${++filterCounter}`;
  document.getElementById(id)?.remove();

  const maps = buildBezelMaps(w, h, radius, bezel);
  if (!maps) return;

  const filter = document.createElementNS(SVG_NS, 'filter');
  const pad = Math.ceil(blur * 1.5);
  filter.setAttribute('id', id);
  // Expand filter bounds beyond the element so feGaussianBlur samples real backdrop pixels
  // instead of transparent edges (which would otherwise drop alpha < 1 and leak sharp text).
  filter.setAttribute('x', String(-pad));
  filter.setAttribute('y', String(-pad));
  filter.setAttribute('width', String(w + pad * 2));
  filter.setAttribute('height', String(h + pad * 2));
  filter.setAttribute('filterUnits', 'userSpaceOnUse');
  filter.setAttribute('color-interpolation-filters', 'sRGB');

  // Run saturate + blur INSIDE the SVG filter BEFORE displacement so Chromium never drops
  // CSS blur over scrolling layers, while keeping a crisp optical bend in the 10–16px rim.
  // Keep saturation calibrated (~1.32) so bright blue atmosphere tones stay smooth without B=255 clipping.
  const feSat = document.createElementNS(SVG_NS, 'feColorMatrix');
  feSat.setAttribute('in', 'SourceGraphic');
  feSat.setAttribute('type', 'saturate');
  feSat.setAttribute('values', '1.32');
  feSat.setAttribute('result', 'sat');

  const feMidBlur = document.createElementNS(SVG_NS, 'feGaussianBlur');
  feMidBlur.setAttribute('in', 'sat');
  feMidBlur.setAttribute('stdDeviation', (blur * 0.42).toFixed(1));
  feMidBlur.setAttribute('edgeMode', 'duplicate');
  feMidBlur.setAttribute('result', 'mid');

  // Light pre-blur on the rim path preserves crisp bubble/text curvature at the 10–16px bevel.
  const feRimSrcBlur = document.createElementNS(SVG_NS, 'feGaussianBlur');
  feRimSrcBlur.setAttribute('in', 'sat');
  feRimSrcBlur.setAttribute('stdDeviation', '1.0');
  feRimSrcBlur.setAttribute('edgeMode', 'duplicate');
  feRimSrcBlur.setAttribute('result', 'rimSrc');

  // Feed the smooth analytic SDF map directly (no feGaussianBlur across transparent edges,
  // which would otherwise average G=255 with G=0 and cancel displacement at the top/left rim).
  const feDispImg = document.createElementNS(SVG_NS, 'feImage');
  feDispImg.setAttribute('href', maps.dispHref);
  feDispImg.setAttribute('x', '0');
  feDispImg.setAttribute('y', '0');
  feDispImg.setAttribute('width', String(w));
  feDispImg.setAttribute('height', String(h));
  feDispImg.setAttribute('preserveAspectRatio', 'none');
  feDispImg.setAttribute('result', 'map');

  const feDisp = document.createElementNS(SVG_NS, 'feDisplacementMap');
  feDisp.setAttribute('in', 'rimSrc');
  feDisp.setAttribute('in2', 'map');
  feDisp.setAttribute('scale', String(scale));
  feDisp.setAttribute('xChannelSelector', 'R');
  feDisp.setAttribute('yChannelSelector', 'G');
  feDisp.setAttribute('result', 'rimBent');

  const feMaskImg = document.createElementNS(SVG_NS, 'feImage');
  feMaskImg.setAttribute('href', maps.maskHref);
  feMaskImg.setAttribute('x', '0');
  feMaskImg.setAttribute('y', '0');
  feMaskImg.setAttribute('width', String(w));
  feMaskImg.setAttribute('height', String(h));
  feMaskImg.setAttribute('preserveAspectRatio', 'none');
  feMaskImg.setAttribute('result', 'rimMask');

  const feRimOnly = document.createElementNS(SVG_NS, 'feComposite');
  feRimOnly.setAttribute('in', 'rimBent');
  feRimOnly.setAttribute('in2', 'rimMask');
  feRimOnly.setAttribute('operator', 'in');
  feRimOnly.setAttribute('result', 'rimOnly');

  const feMerge = document.createElementNS(SVG_NS, 'feComposite');
  feMerge.setAttribute('in', 'rimOnly');
  feMerge.setAttribute('in2', 'mid');
  feMerge.setAttribute('operator', 'over');
  feMerge.setAttribute('result', 'merged');

  // Subtle Fresnel transmission (~0.92) keeps glass interior calm so near-white lips pop,
  // and pins output alpha to 1.0 so unblurred DOM pixels never bleed through the middle.
  const feOpaque = document.createElementNS(SVG_NS, 'feComponentTransfer');
  feOpaque.setAttribute('in', 'merged');
  for (const tag of ['feFuncR', 'feFuncG', 'feFuncB']) {
    const fn = document.createElementNS(SVG_NS, tag);
    fn.setAttribute('type', 'linear');
    fn.setAttribute('slope', '0.92');
    fn.setAttribute('intercept', '0');
    feOpaque.appendChild(fn);
  }
  const feFuncA = document.createElementNS(SVG_NS, 'feFuncA');
  feFuncA.setAttribute('type', 'linear');
  feFuncA.setAttribute('slope', '0');
  feFuncA.setAttribute('intercept', '1');
  feOpaque.appendChild(feFuncA);

  filter.append(feSat, feMidBlur, feRimSrcBlur, feDispImg, feDisp, feMaskImg, feRimOnly, feMerge, feOpaque);
  ensureDefs().appendChild(filter);

  const filterVal = `url(#${id})`;
  el.style.setProperty('backdrop-filter', filterVal);
  el.style.setProperty('-webkit-backdrop-filter', filterVal);
  el.classList.add('is-duet-refracting');
  applied.set(el, { id, key });
}

/** Attaches lightweight rim refraction to [data-duet-refract] chrome in Chromium,
 *  automatically disabling under Reduce Transparency or Increase Contrast. */
export function useLiquidRefraction() {
  useEffect(() => {
    let raf = 0;
    const tracked = new Set<HTMLElement>();

    const ro = typeof ResizeObserver === 'function'
      ? new ResizeObserver(entries => {
          if (!refractionAllowed()) return;
          for (const entry of entries) {
            if (entry.target instanceof HTMLElement && entry.target.isConnected) {
              applyRefraction(entry.target);
            }
          }
        })
      : null;

    const scan = () => {
      raf = 0;
      const allowed = refractionAllowed();
      const current = new Set(Array.from(document.querySelectorAll<HTMLElement>('[data-duet-refract]')));
      for (const el of tracked) {
        if (!current.has(el) || !el.isConnected) {
          removeRefraction(el);
          ro?.unobserve(el);
          tracked.delete(el);
        }
      }
      for (const el of current) {
        if (allowed) {
          applyRefraction(el);
          if (!tracked.has(el)) {
            tracked.add(el);
            ro?.observe(el);
          }
        } else {
          removeRefraction(el);
          ro?.unobserve(el);
          tracked.delete(el);
        }
      }
    };

    const scheduleScan = () => {
      if (!raf) raf = requestAnimationFrame(scan);
    };

    scheduleScan();

    const mo = new MutationObserver(scheduleScan);
    mo.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-duet-refract', 'class'] });
    const rootMo = new MutationObserver(scheduleScan);
    rootMo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-reduced-transparency', 'data-theme'] });

    const mqRT = window.matchMedia('(prefers-reduced-transparency: reduce)');
    const mqHC = window.matchMedia('(prefers-contrast: more)');
    mqRT.addEventListener?.('change', scheduleScan);
    mqHC.addEventListener?.('change', scheduleScan);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      mo.disconnect();
      rootMo.disconnect();
      ro?.disconnect();
      mqRT.removeEventListener?.('change', scheduleScan);
      mqHC.removeEventListener?.('change', scheduleScan);
      for (const el of tracked) removeRefraction(el);
      tracked.clear();
    };
  }, []);
}
