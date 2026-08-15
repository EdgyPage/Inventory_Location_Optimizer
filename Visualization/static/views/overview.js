// overview.js — the whole warehouse, plus a legible band of up to 24 aisles.
//
// Two tiers, and the top one is not optional: 384 aisles is 16 pages of a 24-aisle band, so a
// band alone is not an overview. The mini-map stays on screen and drives which aisles the band
// shows; click a mini-map cell to page to it, click a band tile to drill into that aisle.

import { register } from '../core/registry.js';
import { binGrid, centred, fit, rect, stroke, text } from '../core/canvas.js';
import { minimap, packAisles } from '../core/layout.js';
import { EMPTY, ramp } from '../core/palette.js';

let page = 0;
let lastPack = null;
let lastMap = null;
const MINIMAP_H = 84;

register({
  id: 'overview',
  title: 'Warehouse',
  needs: ['geometry', 'rollup', 'state', 'palette'],
  watches: ['run', 'compareRun', 'batch', 'aisle', 'colorMode'],

  render(ctx) {
    const { canvas, data, sel, palette } = ctx;
    const { ctx: g, w, h } = fit(canvas);
    const aisles = data.geometry?.aisles || [];
    if (!aisles.length) return centred(g, w, h, 'no aisles');

    const rollup = new Map((data.rollup?.aisles || []).map((r) => [r.aisle_id, r]));
    const maxPicks = Math.max(1, ...[...rollup.values()].map((r) => r.picks || 0));

    // ── tier 1: every aisle, one cell each ──
    lastMap = minimap(aisles, w, MINIMAP_H - 18);
    text(g, `all ${aisles.length} aisles`, 0, 0, { size: 10, color: 'rgba(255,255,255,.5)' });
    for (const cell of lastMap.cells) {
      rect(g, cell.x, cell.y + 12, cell.w, cell.h, cellColor(cell.aisle, rollup, maxPicks,
        sel.colorMode, palette));
      if (cell.aisle.aisle_id === sel.aisle) {
        stroke(g, cell.x, cell.y + 12, cell.w, cell.h, 'oklch(0.98 0 0)', 1.5);
      }
    }

    // ── tier 2: the legible band ──
    const bandY = MINIMAP_H;
    const pack = packAisles(aisles, w, h - bandY, page);
    lastPack = { pack, bandY };
    page = pack.page;
    text(g, `aisles ${page * pack.perPage + 1}-${Math.min(aisles.length,
      (page + 1) * pack.perPage)} of ${aisles.length}  ·  page ${page + 1}/${pack.pages}`
      + '   [ / ] to page', 0, bandY - 12,
    { size: 10, color: 'rgba(255,255,255,.5)' });

    const bins = data.state?.bins || {};
    for (const tile of pack.tiles) {
      const a = tile.aisle;
      const box = { ...tile.box, y: tile.box.y + bandY };
      const head = { ...tile.header, y: tile.header.y + bandY };
      const r = rollup.get(a.aisle_id) || {};
      text(g, `${a.aisle_id}`, head.x + 2, head.y + 1,
        { size: 10, weight: 600, color: palette.forAisle(a.aisle_id) });
      text(g, `${r.occupied ?? 0}/${a.capacity}`, head.x + head.w - 2, head.y + 1,
        { size: 9, align: 'right', color: 'rgba(255,255,255,.45)' });

      rect(g, box.x, box.y, box.w, box.h, 'rgba(255,255,255,0.03)');
      drawAisleBins(g, box, a, bins, sel, palette);
      if (a.aisle_id === sel.aisle) stroke(g, box.x, box.y, box.w, box.h, 'oklch(0.98 0 0)', 2);
    }
  },

  hit(x, y, ctx) {
    if (lastMap && y < MINIMAP_H - 6) {
      const a = lastMap.hit(x, y - 12);
      if (a) {
        // Paging to the clicked aisle is the mini-map's whole job.
        const per = lastPack?.pack.perPage || 1;
        page = Math.floor(ctx.data.geometry.aisles.findIndex(
          (z) => z.aisle_id === a.aisle_id) / per);
        return { kind: 'aisle', aisle: a.aisle_id };
      }
      return null;
    }
    if (!lastPack) return null;
    for (const tile of lastPack.pack.tiles) {
      const box = { ...tile.box, y: tile.box.y + lastPack.bandY };
      if (x >= box.x && x <= box.x + box.w && y >= box.y && y <= box.y + box.h) {
        return { kind: 'aisle', aisle: tile.aisle.aisle_id };
      }
    }
    return null;
  },

  key(ev) {
    if (ev.key === ']') { page += 1; return true; }
    if (ev.key === '[') { page = Math.max(0, page - 1); return true; }
    return false;
  },
});

function cellColor(a, rollup, maxPicks, mode, palette) {
  const r = rollup.get(a.aisle_id);
  if (!r) return EMPTY;
  if (mode === 'fill') return ramp((r.occupied || 0) / Math.max(1, a.capacity));
  if (mode === 'picks') return ramp((r.picks || 0) / maxPicks);
  if (mode === 'home' && r.home_match !== null && r.occupied) {
    // Share of occupied bins whose SKU counts this aisle among its final homes.
    return ramp((r.home_match || 0) / r.occupied);
  }
  return palette.forAisle(a.aisle_id);
}

// Bins are drawn per COLUMN when the tile is too small for individual bins — a 150x40 aisle in a
// 90 px tile has 0.6 px per bin, so drawing each one is a lie. Collapsing to columns keeps the
// occupancy read honest at any zoom.
function drawAisleBins(g, box, a, bins, sel, palette) {
  const grid = binGrid(box, a.bay_x, a.bay_y);
  if (grid.cw < 1.2 || grid.ch < 1.2) {
    const colH = box.h;
    const cw = box.w / a.bay_x;
    for (let x = 1; x <= a.bay_x; x += 1) {
      let filled = 0;
      let color = null;
      for (let y = 1; y <= a.bay_y; y += 1) {
        const b = bins[`${a.aisle_id},${x},${y}`];
        if (b) { filled += 1; if (!color) color = palette.forSku(b.sku, a.aisle_id); }
      }
      if (!filled) continue;
      const frac = filled / a.bay_y;
      rect(g, box.x + (x - 1) * cw, box.y + colH * (1 - frac), Math.max(0.7, cw),
        colH * frac, color || EMPTY);
    }
    return;
  }
  for (let y = 1; y <= a.bay_y; y += 1) {
    for (let x = 1; x <= a.bay_x; x += 1) {
      const b = bins[`${a.aisle_id},${x},${y}`];
      const cell = grid.at(x, y);
      rect(g, cell.x, cell.y, Math.max(0.7, cell.w - 0.3), Math.max(0.7, cell.h - 0.3),
        b ? palette.forSku(b.sku, a.aisle_id) : EMPTY);
    }
  }
}
