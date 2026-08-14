// diff.js — the same aisle in two arms, bin by bin.
//
// Per-bin ternary, because "different" is not one thing:
//   same sku        both arms put the same item here
//   different sku   both arms filled it, with different items
//   only one filled an occupancy mismatch — one arm left it empty
//
// Refuses to draw when the two arms have different `warehouse_fingerprint`s: arms from cells
// with different aisle layouts would be compared with aisle ids that silently mean different
// things, and a picture that looks fine and is wrong is worse than no picture.

import { register } from '../core/registry.js';
import { binGrid, centred, fit, rect, stroke, text } from '../core/canvas.js';
import { EMPTY } from '../core/palette.js';

const SAME = 'oklch(0.55 0.13 155)';
const DIFF = 'oklch(0.62 0.16 60)';
const ONLY_BASE = 'oklch(0.55 0.15 250)';
const ONLY_CMP = 'oklch(0.55 0.15 320)';

let lastGrid = null;
let lastBox = null;

register({
  id: 'diff',
  title: 'Compare',
  needs: ['geometry', 'aisleState', 'aisleStateCompare', 'palette'],
  watches: ['run', 'compareRun', 'batch', 't', 'aisle', 'bin'],

  render(ctx) {
    const { canvas, data, sel } = ctx;
    const { ctx: g, w, h } = fit(canvas);
    if (!sel.compareRun) return centred(g, w, h, 'choose a comparison arm');
    if (ctx.incomparable) {
      return centred(g, w, h,
        'these arms have different warehouse fingerprints — their aisle ids are not the same '
        + 'warehouse, so a bin-for-bin diff would be meaningless');
    }
    if (sel.aisle === null) return centred(g, w, h, 'pick an aisle');
    const a = data.aisleState;
    const b = data.aisleStateCompare;
    if (!a || !b) return centred(g, w, h, 'loading…');
    const geom = a.geom || {};
    if (!geom.bay_x) return centred(g, w, h, `aisle ${sel.aisle} has no geometry`);

    let same = 0; let diff = 0; let onlyA = 0; let onlyB = 0;
    const cells = [];
    for (let y = 1; y <= geom.bay_y; y += 1) {
      for (let x = 1; x <= geom.bay_x; x += 1) {
        const key = `${sel.aisle},${x},${y}`;
        const ba = a.bins[key];
        const bb = b.bins[key];
        let kind = null;
        if (ba && bb) { if (ba.sku === bb.sku) { same += 1; kind = SAME; } else { diff += 1; kind = DIFF; } }
        else if (ba) { onlyA += 1; kind = ONLY_BASE; }
        else if (bb) { onlyB += 1; kind = ONLY_CMP; }
        if (kind) cells.push([x, y, kind]);
      }
    }

    text(g, `aisle ${sel.aisle} — base vs comparison at batch ${sel.batch}`, 4, 4,
      { size: 11, weight: 600 });
    let lx = 4;
    lx = legend(g, lx, 20, SAME, `same sku ${same}`);
    lx = legend(g, lx, 20, DIFF, `different sku ${diff}`);
    lx = legend(g, lx, 20, ONLY_BASE, `only base ${onlyA}`);
    legend(g, lx, 20, ONLY_CMP, `only comparison ${onlyB}`);
    if (!a.exact || !b.exact) {
      text(g, `frame is between keyframes — ${Math.max(a.restocks_pending, b.restocks_pending)}`
        + ' restocks not shown in either arm', 4, 36, { size: 10, color: '#ffca28' });
    }

    const box = { x: 4, y: 52, w: w - 8, h: h - 60 };
    lastBox = box;
    rect(g, box.x, box.y, box.w, box.h, 'rgba(255,255,255,0.03)');
    const grid = binGrid(box, geom.bay_x, geom.bay_y);
    lastGrid = grid;
    for (const [x, y, color] of cells) {
      const c = grid.at(x, y);
      rect(g, c.x, c.y, Math.max(0.8, c.w - 0.4), Math.max(0.8, c.h - 0.4), color);
    }
    if (sel.bin && sel.bin.aisle === sel.aisle) {
      const c = grid.at(sel.bin.bayX, sel.bin.bayY);
      stroke(g, c.x, c.y, c.w, c.h, 'oklch(0.98 0 0)', 1.5);
    }
  },

  hit(x, y) {
    if (!lastGrid || !lastBox) return null;
    if (y < lastBox.y || y > lastBox.y + lastBox.h) return null;
    const cell = lastGrid.hit(x, y);
    return cell ? { kind: 'bin', ...cell } : null;
  },
});

function legend(g, x, y, color, label) {
  rect(g, x, y, 10, 10, color);
  text(g, label, x + 14, y, { size: 10, color: 'rgba(255,255,255,.7)' });
  return x + 20 + label.length * 6.2;
}
