// bin.js — one bin, at this instant and across the whole run.
//
// The deepest drill level. Everything here is already loaded except the bin's own history, which
// is one point query against the sidecar's `bin_span` (a ~5-9 s scan without it).

import { register } from '../core/registry.js';
import { centred, fit, rect, text } from '../core/canvas.js';
import { EMPTY } from '../core/palette.js';
import { pickedByBin } from './aisle.js';

register({
  id: 'bin',
  title: 'Bin',
  needs: ['geometry', 'aisleState', 'binHistory', 'binScores', 'skuScores', 'palette'],
  watches: ['run', 'compareRun', 'batch', 't', 'aisle', 'bin'],

  render(ctx) {
    const { canvas, data, sel, palette } = ctx;
    const { ctx: g, w, h } = fit(canvas);
    if (!sel.bin) return centred(g, w, h, 'click a bin in the Aisle view');

    const { aisle, bayX, bayY } = sel.bin;
    const key = `${aisle},${bayX},${bayY}`;
    const st = data.aisleState;
    const now = st?.bins?.[key];
    const picked = pickedByBin(st?.events, sel.t).get(key) || 0;
    const qtyNow = now ? Math.max(0, now.qty - picked) : 0;

    let y = 6;
    text(g, `bin ${aisle} / ${bayX} / ${bayY}`, 6, y, { size: 13, weight: 600 });
    y += 20;

    if (!now) {
      text(g, 'empty at this batch', 6, y, { size: 11, color: 'rgba(255,255,255,.5)' });
      y += 18;
    } else {
      const home = palette.isHome(now.sku, aisle);
      const homes = data.finalHome?.homes?.[String(now.sku)];
      rect(g, 6, y, 14, 14, palette.forSku(now.sku, aisle));
      text(g, `sku ${now.sku}`, 26, y + 1, { size: 12, weight: 600 });
      y += 20;
      row(g, 6, y, 'qty now', `${qtyNow}`); y += 15;
      row(g, 6, y, 'qty at batch start', `${now.qty}`); y += 15;
      row(g, 6, y, 'picked this batch', `${picked}`); y += 15;
      row(g, 6, y, 'at final home', home ? 'yes' : 'no',
        home ? '#66bb6a' : '#ffca28'); y += 15;
      if (homes) {
        row(g, 6, y, 'final home aisles', homes.home_aisles.join(', ')); y += 15;
        if (homes.n_homes > 1) {
          // Flagged rather than hidden: a multi-homed SKU is not one that failed to converge.
          row(g, 6, y, 'replica bins at end', `${homes.n_homes}`,
            'rgba(255,255,255,.5)'); y += 15;
        }
      }
      const sku = data.skuScores?.[String(now.sku)];
      if (sku) {
        y += 6;
        text(g, 'sku scores', 6, y, { size: 10, color: 'rgba(255,255,255,.45)' }); y += 14;
        for (const k of ['expected_popularity', 'expected_labor', 'labor_cost',
          'equilibrium_qty', 'reorder_point']) {
          if (sku[k] === null || sku[k] === undefined) continue;
          row(g, 6, y, k, fmt(sku[k])); y += 14;
        }
      }
    }

    const score = data.binScores?.layout?.[key];
    if (score !== undefined) {
      y += 6;
      row(g, 6, y, 'layout score', fmt(score), 'rgba(255,255,255,.7)'); y += 15;
      const pref = data.binScores?.map_pref?.[key];
      if (pref !== undefined) { row(g, 6, y, 'map pref', fmt(pref)); y += 15; }
    }

    // ── history: every SKU this bin has held, across the run ──
    const spans = data.binHistory?.spans || [];
    y += 10;
    text(g, `history — ${spans.length} occupancy span(s) across the keyframes`, 6, y,
      { size: 10, color: 'rgba(255,255,255,.45)' });
    y += 16;
    if (!spans.length) {
      text(g, 'never occupied at a keyframe', 6, y,
        { size: 11, color: 'rgba(255,255,255,.4)' });
      return;
    }
    // Spans are batch-bounded now (the bin-mutation log resolves every batch, not only the
    // keyframes), so the timeline axis is the batch grid and falls back to keyframes only for
    // a pre-log arm whose spans really do sit on that coarser grid.
    const grid = (data.meta?.batches?.length ? data.meta.batches
                                            : (data.meta?.keyframes || []));
    const lo = grid.length ? grid[0] : 0;
    const hi = grid.length ? grid[grid.length - 1] : 1;
    const barX = 6;
    const barW = w - 12;
    const barH = 18;
    rect(g, barX, y, barW, barH, 'rgba(255,255,255,0.05)');
    for (const s of spans) {
      const x0 = barX + barW * ((s.t_from - lo) / Math.max(1, hi - lo));
      const x1 = barX + barW * ((s.t_to - lo) / Math.max(1, hi - lo));
      rect(g, x0, y, Math.max(2, x1 - x0), barH, palette.forSku(s.sku, aisle));
    }
    y += barH + 4;
    text(g, `batch ${lo}`, barX, y, { size: 9, color: 'rgba(255,255,255,.4)' });
    text(g, `batch ${hi}`, barX + barW, y,
      { size: 9, align: 'right', color: 'rgba(255,255,255,.4)' });
    y += 16;
    for (const s of spans.slice(0, 12)) {
      rect(g, 6, y, 10, 10, palette.forSku(s.sku, aisle));
      text(g, `batches ${s.t_from}-${s.t_to}   sku ${s.sku}   qty ${s.qty_at_from}`, 20, y,
        { size: 10, color: 'rgba(255,255,255,.7)' });
      y += 14;
    }
    if (spans.length > 12) {
      text(g, `… and ${spans.length - 12} more`, 20, y,
        { size: 10, color: 'rgba(255,255,255,.4)' });
    }
  },
});

function row(g, x, y, label, value, color = 'rgba(255,255,255,.85)') {
  text(g, label, x, y, { size: 11, color: 'rgba(255,255,255,.5)' });
  text(g, value, x + 190, y, { size: 11, color });
}

function fmt(v) {
  if (typeof v !== 'number') return String(v);
  return Number.isInteger(v) ? String(v) : v.toFixed(3);
}
