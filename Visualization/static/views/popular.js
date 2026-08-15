// popular.js — the N most-picked SKUs over time, in the same colours as the map.
//
// The colour tie is the point: a SKU's swatch here is exactly the colour its bins wear in the
// Warehouse and Aisle views, so "the busiest items live in that hue band" is a single glance
// rather than a cross-reference.
//
// The ranking is over the WHOLE run (units picked), which the sidecar precomputes — live it is a
// ~24 s full scan of `picks`.

import { register } from '../core/registry.js';
import { centred, fit, rect, text } from '../core/canvas.js';

const ROW_H = 26;

register({
  id: 'popular',
  title: 'Top SKUs',
  needs: ['topSkus', 'skuSeries', 'palette', 'geometry'],
  watches: ['run', 'compareRun', 'batch', 'topN'],

  render(ctx) {
    const { canvas, data, sel, palette } = ctx;
    const { ctx: g, w, h } = fit(canvas);
    const skus = (data.topSkus?.skus || []).slice(0, sel.topN);
    if (!skus.length) return centred(g, w, h, 'no picks recorded');

    const series = data.skuSeries?.series || {};
    const batches = (data.meta?.batches || []);
    const lo = batches.length ? batches[0] : 0;
    const hi = batches.length ? batches[batches.length - 1] : 1;
    const homes = data.finalHome?.homes || {};

    text(g, `top ${skus.length} SKUs by units picked  ·  colour = where each ends up in the `
      + 'comparison arm  ·  [ ] to change N', 6, 4,
    { size: 10, color: 'rgba(255,255,255,.5)' });

    const sparkX = 300;
    const sparkW = Math.max(60, w - sparkX - 90);
    const maxUnits = Math.max(1, ...skus.map((s) => s.units));

    skus.forEach((s, i) => {
      const y = 22 + i * ROW_H;
      if (y + ROW_H > h) return;
      const home = homes[String(s.sku)];
      // Colour by the SKU's own destination aisle, so the swatch matches the bins it will
      // occupy once converged.
      const color = home ? palette.forSku(s.sku, home.aisle_id) : 'oklch(0.50 0 0)';

      rect(g, 6, y + 4, 14, 14, color);
      text(g, `#${s.rank}`, 26, y + 7, { size: 10, color: 'rgba(255,255,255,.45)' });
      text(g, `sku ${s.sku}`, 60, y + 7, { size: 11, weight: 600 });
      text(g, `${s.units.toLocaleString()} units`, 170, y + 7,
        { size: 10, color: 'rgba(255,255,255,.6)' });
      text(g, home ? `→ aisle ${home.aisle_id}` : '→ no home', 250, y + 7,
        { size: 10, color: 'rgba(255,255,255,.45)' });

      spark(g, sparkX, y + 3, sparkW, ROW_H - 8, series[String(s.sku)] || [], lo, hi, color,
        sel.batch);

      // Share of the run's busiest SKU, as a bar — keeps the head of the Zipf visible.
      const frac = s.units / maxUnits;
      rect(g, sparkX + sparkW + 10, y + 8, 60 * frac, 7, color);
    });

    const shown = Math.min(skus.length, Math.floor((h - 22) / ROW_H));
    if (shown < skus.length) {
      text(g, `showing ${shown} of ${skus.length} — resize or lower N`, 6, h - 12,
        { size: 9, color: 'rgba(255,255,255,.4)' });
    }
  },

  key(ev, ctx) {
    if (ev.key === ']') { ctx.emit({ topN: Math.min(200, ctx.sel.topN + 25) }); return true; }
    if (ev.key === '[') { ctx.emit({ topN: Math.max(5, ctx.sel.topN - 25) }); return true; }
    return false;
  },
});

/** Per-batch pick volume, with a marker at the batch currently being viewed. */
function spark(g, x, y, w, h, points, lo, hi, color, atBatch) {
  rect(g, x, y, w, h, 'rgba(255,255,255,0.04)');
  if (!points.length) return;
  const maxU = Math.max(1, ...points.map((p) => p.units));
  const span = Math.max(1, hi - lo);
  for (const p of points) {
    const px = x + (w * (p.batch_id - lo)) / span;
    const ph = (h - 2) * (p.units / maxU);
    rect(g, px, y + h - ph, Math.max(1, w / span - 0.5), ph, color);
  }
  const mx = x + (w * (atBatch - lo)) / span;
  rect(g, mx, y, 1, h, 'rgba(255,255,255,0.55)');
}
