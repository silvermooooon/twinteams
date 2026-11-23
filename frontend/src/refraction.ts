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

function buildDisplacementMap(w: number, h: number, radius: number, bezel: number): string {
  // Scale down map generation for large surfaces while keeping smooth bezel fidelity.
  const scaleFactor = Math.max(w, h) > 480 ? 0.5 : 1;
  const mw = Math.max(16, Math.round(w * scaleFactor));
  const mh = Math.max(16, Math.round(h * scaleFactor));
  const mr = radius * scaleFactor;
  const mb = Math.max(4, bezel * scaleFactor);

  const canvas = document.createElement('canvas');
  canvas.width = mw;
  canvas.height = mh;
  const ctx = canvas.getContext('2d');
  if (!ctx) return '';

  const img = ctx.createImageData(mw, mh);
  const data = img.data;
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
      if (inside < mb && inside > -1) {
        const t = 1 - Math.max(0, inside) / mb;
        let m = t * t * (3 - 2 * t);
        m = Math.pow(m, 1.6);
        vx = -field.nx * m;
        vy = -field.ny * m;
      }
      const idx = (py * mw + px) * 4;
      data[idx] = Math.round(128 + Math.max(-1, Math.min(1, vx)) * 127);
      data[idx + 1] = Math.round(128 + Math.max(-1, Math.min(1, vy)) * 127);
      data[idx + 2] = 128;
      data[idx + 3] = 255;
    }
  }
  ctx.putImageData(img, 0, 0);
  return canvas.toDataURL('image/png');
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
  const bezel = Math.min(Math.max(8, Math.min(18, radius * 0.55)), Math.min(w, h) * 0.3);
  const strength = Math.max(0.2, Math.min(1.5, parseFloat(el.getAttribute('data-duet-refract') || '0.75') || 0.75));
  const blur = Math.max(8, Math.min(28, parseFloat(el.getAttribute('data-duet-refract-blur') || '16') || 16));
  const scale = Math.round(bezel * 1.1 * strength);

  const key = `${w}|${h}|${Math.round(radius)}|${Math.round(bezel)}|${scale}|${blur}`;
  const prev = applied.get(el);
  if (prev?.key === key) return;

  const id = prev?.id || `duet-refract-${++filterCounter}`;
  document.getElementById(id)?.remove();

  const href = buildDisplacementMap(w, h, radius, bezel);
  if (!href) return;

  const filter = document.createElementNS(SVG_NS, 'filter');
  filter.setAttribute('id', id);
  filter.setAttribute('x', '0');
  filter.setAttribute('y', '0');
  filter.setAttribute('width', String(w));
  filter.setAttribute('height', String(h));
  filter.setAttribute('filterUnits', 'userSpaceOnUse');
  filter.setAttribute('color-interpolation-filters', 'sRGB');

  const feImg = document.createElementNS(SVG_NS, 'feImage');
  feImg.setAttribute('href', href);
  feImg.setAttribute('x', '0');
  feImg.setAttribute('y', '0');
  feImg.setAttribute('width', String(w));
  feImg.setAttribute('height', String(h));
  feImg.setAttribute('preserveAspectRatio', 'none');
  feImg.setAttribute('result', 'raw');

  const feBlur = document.createElementNS(SVG_NS, 'feGaussianBlur');
  feBlur.setAttribute('in', 'raw');
  feBlur.setAttribute('stdDeviation', Math.max(1.5, bezel * 0.25).toFixed(1));
  feBlur.setAttribute('result', 'map');

  const feDisp = document.createElementNS(SVG_NS, 'feDisplacementMap');
  feDisp.setAttribute('in', 'SourceGraphic');
  feDisp.setAttribute('in2', 'map');
  feDisp.setAttribute('scale', String(scale));
  feDisp.setAttribute('xChannelSelector', 'R');
  feDisp.setAttribute('yChannelSelector', 'G');

  filter.append(feImg, feBlur, feDisp);
  ensureDefs().appendChild(filter);

  const filterVal = `url(#${id}) blur(${blur}px) saturate(180%)`;
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

    const mqRT = window.matchMedia('(prefers-reduced-transparency: reduce)');
    const mqHC = window.matchMedia('(prefers-contrast: more)');
    mqRT.addEventListener?.('change', scheduleScan);
    mqHC.addEventListener?.('change', scheduleScan);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      mo.disconnect();
      ro?.disconnect();
      mqRT.removeEventListener?.('change', scheduleScan);
      mqHC.removeEventListener?.('change', scheduleScan);
      for (const el of tracked) removeRefraction(el);
      tracked.clear();
    };
  }, []);
}
