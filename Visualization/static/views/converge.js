// converge.js — watch items restock into position, base arm against comparison arm.
//
// This is the view the colour scheme exists for. Every occupied bin is painted with the hue and
// lightness of where its SKU ENDS UP in the comparison run, and with full chroma only when it is
// already in one of its final home aisles. A converging warehouse visibly saturates.
//
// It steps KEYFRAMES, not batches, and that is a correctness constraint rather than a choice:
// `bin_inventory` records picks and never restocks (RECONSTRUCTION.md §1), so a frame between
// keyframes is missing exactly the events this view is about. Twenty exact frames beat a hundred
// frames that quietly drain the warehouse.

import { register } from '../core/registry.js';
import { centred, fit, rect, text } from '../core/canvas.js';
import { EMPTY } from '../core/palette.js';

register({
  id: 'converge',
  title: 'Converge',
  needs: ['geometry', 'rollup', 'rollupCompare', 'palette'],
  watches: ['run', 'compareRun', 'batch', 'colorMode'],
  requires: ['keyframes'],

  render(ctx) {
    const { canvas, data, sel, palette } = ctx;
    const { ctx: g, w, h } = fit(canvas);
    if (!sel.compareRun) {
      return centred(g, w, h, 'choose a comparison arm — it defines where items belong');
    }
    if (!palette.hasHomes) {
      return centred(g, w, h, 'the comparison arm has no keyframes, so it defines no destinations');
    }
    const aisles = data.geometry?.aisles || [];
    const base = data.rollup?.aisles || [];
    const cmp = data.rollupCompare?.aisles || [];
    if (!base.length) return centred(g, w, h, 'loading…');

    const bStats = convergence(base);
    const cStats = convergence(cmp);

    // ── the headline: what fraction of stock is already home, in each arm ──
    text(g, 'share of occupied bins already in a final home aisle', 6, 4,
      { size: 11, color: 'rgba(255,255,255,.6)' });
    bar(g, 6, 24, w - 12, 26, bStats.pct, `${sel.run.split('/').pop()} (base)`, '#4aa3ff');
    bar(g, 6, 58, w - 12, 26, cStats.pct, `${sel.compareRun.split('/').pop()} (comparison)`,
      '#66bb6a');

    const delta = cStats.pct - bStats.pct;
    text(g, `${delta >= 0 ? '+' : ''}${delta.toFixed(1)} pts`
      + `   ·   ${bStats.home.toLocaleString()} vs ${cStats.home.toLocaleString()} bins home`,
    6, 92, { size: 11, color: delta >= 0 ? '#66bb6a' : '#ffca28' });

    // ── per-aisle convergence, one column per aisle, base above comparison ──
    const top = 116;
    const rowH = Math.min(90, (h - top - 24) / 2);
    text(g, 'per aisle — height is share of that aisle already home', 6, top - 14,
      { size: 10, color: 'rgba(255,255,255,.45)' });
    strip(g, 6, top, w - 12, rowH, base, aisles, palette, 'base');
    strip(g, 6, top + rowH + 10, w - 12, rowH, cmp, aisles, palette, 'comparison');

    const kfs = data.meta?.keyframes || [];
    const at = kfs.indexOf(sel.batch);
    text(g, at >= 0
      ? `keyframe ${at + 1}/${kfs.length} (batch ${sel.batch}) — exact`
      : `batch ${sel.batch} is between keyframes; step with , and . to stay exact`,
    6, h - 14, { size: 10, color: at >= 0 ? 'rgba(255,255,255,.5)' : '#ffca28' });
  },

  key(ev, ctx) {
    // Step keyframe to keyframe: the only frames this view can be honest about.
    const kfs = ctx.data.meta?.keyframes || [];
    if (!kfs.length) return false;
    if (ev.key === '.' || ev.key === ',') {
      const dir = ev.key === '.' ? 1 : -1;
      const idx = kfs.findIndex((b) => b >= ctx.sel.batch);
      const cur = idx < 0 ? kfs.length - 1 : idx;
      const next = Math.max(0, Math.min(kfs.length - 1, cur + dir));
      ctx.emit({ batch: kfs[next], t: null });
      return true;
    }
    return false;
  },
});

function convergence(rows) {
  let home = 0;
  let occ = 0;
  for (const r of rows) {
    if (r.home_match === null || r.home_match === undefined) continue;
    home += r.home_match;
    occ += r.occupied;
  }
  return { home, occ, pct: occ ? (100 * home) / occ : 0 };
}

function bar(g, x, y, w, h, pct, label, color) {
  rect(g, x, y, w, h, 'rgba(255,255,255,0.05)');
  rect(g, x, y, (w * pct) / 100, h, color);
  text(g, label, x + 6, y + h / 2, { size: 11, baseline: 'middle', weight: 600 });
  text(g, `${pct.toFixed(1)}%`, x + w - 6, y + h / 2,
    { size: 12, align: 'right', baseline: 'middle', weight: 600 });
}

function strip(g, x, y, w, h, rows, aisles, palette, label) {
  rect(g, x, y, w, h, 'rgba(255,255,255,0.03)');
  text(g, label, x + 4, y + 2, { size: 9, color: 'rgba(255,255,255,.4)' });
  if (!rows.length) return;
  const byId = new Map(rows.map((r) => [r.aisle_id, r]));
  const cw = w / Math.max(1, aisles.length);
  aisles.forEach((a, i) => {
    const r = byId.get(a.aisle_id);
    if (!r || !r.occupied) return;
    const frac = (r.home_match || 0) / r.occupied;
    rect(g, x + i * cw, y + h * (1 - frac), Math.max(0.8, cw - 0.2), h * frac,
      palette.forAisle(a.aisle_id));
  });
}
