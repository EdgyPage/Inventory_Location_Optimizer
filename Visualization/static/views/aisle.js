// aisle.js — one aisle at full bin resolution, with the pickers working it.
//
// Time is resolved HERE, from the event list the shell already loaded for this batch. Nothing is
// fetched as `t` moves: picker positions are interpolated between `arrive` events and bin
// quantities are depleted by replaying `pick` events up to t.

import { register } from '../core/registry.js';
import { binGrid, centred, dot, fit, manhattan, rect, stroke, text } from '../core/canvas.js';
import { EMPTY } from '../core/palette.js';

const PICKER_COLORS = ['#4aa3ff', '#ff7043', '#66bb6a', '#e040fb', '#ffca28', '#26c6da'];
let lastGrid = null;
let lastBox = null;

register({
  id: 'aisle',
  title: 'Aisle',
  needs: ['geometry', 'aisleState', 'palette'],
  watches: ['run', 'compareRun', 'batch', 't', 'aisle', 'bin', 'colorMode'],

  render(ctx) {
    const { canvas, data, sel, palette } = ctx;
    const { ctx: g, w, h } = fit(canvas);
    if (sel.aisle === null) return centred(g, w, h, 'pick an aisle in the Warehouse view');
    const st = data.aisleState;
    if (!st) return centred(g, w, h, 'loading…');
    const geom = st.geom || {};
    if (!geom.bay_x) return centred(g, w, h, `aisle ${sel.aisle} has no geometry`);

    // ── header ──
    const home = countHome(st.bins, sel.aisle, palette);
    text(g, `aisle ${sel.aisle}  ·  ${geom.handling_type}/${geom.unit_type}/${geom.storage_size}`
      + `  ·  ${geom.bay_x}x${geom.bay_y}`, 4, 4, { size: 11, weight: 600 });
    text(g, `${Object.keys(st.bins).length} occupied  ·  ${home.pct}% home`
      + (st.exact ? '' : `  ·  NOT EXACT: ${st.restocks_pending} restocks not shown`),
    4, 20, { size: 10, color: st.exact ? 'rgba(255,255,255,.55)' : '#ffca28' });

    const box = { x: 4, y: 38, w: w - 8, h: h - 46 };
    lastBox = box;
    rect(g, box.x, box.y, box.w, box.h, 'rgba(255,255,255,0.03)');
    const grid = binGrid(box, geom.bay_x, geom.bay_y);
    lastGrid = grid;

    // Deplete by replaying picks up to t — no fetch.
    const picked = pickedByBin(st.events, sel.t);
    for (let y = 1; y <= geom.bay_y; y += 1) {
      for (let x = 1; x <= geom.bay_x; x += 1) {
        const key = `${sel.aisle},${x},${y}`;
        const b = st.bins[key];
        const cell = grid.at(x, y);
        const qty = b ? b.qty - (picked.get(key) || 0) : 0;
        rect(g, cell.x, cell.y, Math.max(0.8, cell.w - 0.4), Math.max(0.8, cell.h - 0.4),
          (b && qty > 0) ? palette.forSku(b.sku, sel.aisle) : EMPTY);
      }
    }

    if (sel.bin && sel.bin.aisle === sel.aisle) {
      const c = grid.at(sel.bin.bayX, sel.bin.bayY);
      // An outline, never a hue change: recolouring the selection would destroy the very
      // comparison the user is making.
      stroke(g, c.x, c.y, c.w, c.h, 'oklch(0.98 0 0)', 1.5);
    }

    drawPickers(g, grid, st.events, sel.t, geom);
    if (grid.cw < 2) {
      text(g, `${geom.bay_x} bays across ${Math.round(box.w)}px — each bin is `
        + `${grid.cw.toFixed(1)}px; click to inspect`, 4, h - 12,
      { size: 9, color: 'rgba(255,255,255,.4)' });
    }
  },

  hit(x, y) {
    if (!lastGrid || !lastBox) return null;
    if (x < lastBox.x || x > lastBox.x + lastBox.w) return null;
    if (y < lastBox.y || y > lastBox.y + lastBox.h) return null;
    const cell = lastGrid.hit(x, y);
    return cell ? { kind: 'bin', ...cell } : null;
  },
});

/** Units picked per bin up to time t. Cheap: one pass over the batch's own events. */
export function pickedByBin(events, t) {
  const out = new Map();
  if (!events) return out;
  for (const e of events) {
    if (e.event_type !== 'pick' || !e.location) continue;
    if (t !== null && e.time > t) break;                 // events arrive time-ordered
    const key = e.location.join(',');
    out.set(key, (out.get(key) || 0) + (e.quantity || 0));
  }
  return out;
}

function countHome(bins, aisleId, palette) {
  let home = 0;
  let total = 0;
  for (const b of Object.values(bins)) {
    total += 1;
    if (palette.isHome(b.sku, aisleId)) home += 1;
  }
  return { home, total, pct: total ? Math.round((100 * home) / total) : 0 };
}

/** Picker dots interpolated between `arrive` events, with the Manhattan leg they are walking. */
function drawPickers(g, grid, events, t, geom) {
  if (!events || t === null) return;
  const byPicker = new Map();
  for (const e of events) {
    if (e.time > t) break;
    if (!e.location) continue;
    byPicker.set(e.picker_id, e);
  }
  for (const [pid, e] of byPicker) {
    const next = events.find((n) => n.picker_id === pid && n.time > t && n.location);
    const from = grid.at(e.location[1], e.location[2]);
    const a = { x: from.x + from.w / 2, y: from.y + from.h / 2 };
    const color = PICKER_COLORS[pid % PICKER_COLORS.length];
    if (next) {
      const to = grid.at(next.location[1], next.location[2]);
      const b = { x: to.x + to.w / 2, y: to.y + to.h / 2 };
      const span = Math.max(1e-6, next.time - e.time);
      const f = Math.max(0, Math.min(1, (t - e.time) / span));
      manhattan(g, a, b, color, 0.5);
      // Manhattan interpolation: all of x first, then y — matching how the sim moves a picker.
      const xLeg = Math.abs(b.x - a.x);
      const total = xLeg + Math.abs(b.y - a.y);
      const travelled = f * total;
      const pos = travelled <= xLeg
        ? { x: a.x + Math.sign(b.x - a.x) * travelled, y: a.y }
        : { x: b.x, y: a.y + Math.sign(b.y - a.y) * (travelled - xLeg) };
      dot(g, pos.x, pos.y, 4, color);
    } else {
      dot(g, a.x, a.y, 4, color);
    }
  }
}
