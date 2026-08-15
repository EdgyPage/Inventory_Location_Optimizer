// tasks.js — per-task effort, base against comparison.
//
// A task is one picker's single-aisle pick sequence, so this is where placement strategy shows
// up as labour: same items, same batch, different walk.
//
// CAVEAT, drawn on the chart: `task_start_time` is BATCH-RELATIVE — every batch restarts near
// zero — so there is no global clock in the persisted data. Cross-batch position is synthesised
// from the cumulative sum of `batch_stats.duration`, which is batch MAKESPAN rather than summed
// task time. Within a batch the axis is exact; across batches it is an approximation, and the
// label says so.

import { register } from '../core/registry.js';
import { centred, dot, fit, rect, text } from '../core/canvas.js';

register({
  id: 'tasks',
  title: 'Tasks',
  needs: ['tasks', 'tasksCompare'],
  watches: ['run', 'compareRun', 'batch'],

  render(ctx) {
    const { canvas, data, sel } = ctx;
    const { ctx: g, w, h } = fit(canvas);
    const a = data.tasks?.tasks || [];
    const b = data.tasksCompare?.tasks || [];
    if (!a.length) return centred(g, w, h, 'no tasks recorded for this batch');

    const half = (h - 60) / 2;
    text(g, `batch ${sel.batch} — ${a.length} tasks`
      + (b.length ? ` vs ${b.length} in the comparison arm` : ''), 6, 4,
    { size: 11, weight: 600 });

    // ── scatter: analytical work W against measured duration ──
    const all = a.concat(b);
    const maxW = Math.max(1, ...all.map((t) => t.W));
    const maxD = Math.max(1, ...all.map((t) => t.duration));
    const box = { x: 40, y: 24, w: w - 60, h: half };
    axes(g, box, 'W (analytical work)', 'duration (s)');
    for (const [rows, color] of [[a, '#4aa3ff'], [b, '#66bb6a']]) {
      for (const t of rows) {
        dot(g, box.x + (box.w * t.W) / maxW, box.y + box.h - (box.h * t.duration) / maxD,
          2, `${color}bb`);
      }
    }
    stat(g, box.x + box.w - 4, box.y + 4, [
      [`base   mean ${mean(a, 'duration').toFixed(1)}s  items ${sum(a, 'total_items')}`, '#4aa3ff'],
      b.length
        ? [`cmp    mean ${mean(b, 'duration').toFixed(1)}s  items ${sum(b, 'total_items')}`, '#66bb6a']
        : null,
    ].filter(Boolean));

    // ── per-batch duration series, with the synthesised-clock caveat stated ──
    const box2 = { x: 40, y: 40 + half, w: w - 60, h: half - 20 };
    const batches = data.meta?.batches || [];
    text(g, 'task duration distribution per batch — cross-batch x is synthesised from batch '
      + 'makespan, not a persisted global clock', 6, box2.y - 14,
    { size: 9, color: 'rgba(255,255,255,.4)' });
    axes(g, box2, 'batch', 'duration (s)');
    const byBatch = new Map();
    for (const t of a) {
      if (!byBatch.has(t.batch_id)) byBatch.set(t.batch_id, []);
      byBatch.get(t.batch_id).push(t.duration);
    }
    const lo = batches.length ? batches[0] : 0;
    const hi = batches.length ? batches[batches.length - 1] : 1;
    for (const [batch, durs] of byBatch) {
      const x = box2.x + (box2.w * (batch - lo)) / Math.max(1, hi - lo);
      durs.sort((p, q) => p - q);
      const q1 = durs[Math.floor(durs.length * 0.25)];
      const q3 = durs[Math.floor(durs.length * 0.75)];
      const med = durs[Math.floor(durs.length * 0.5)];
      rect(g, x - 2, box2.y + box2.h - (box2.h * q3) / maxD, 4,
        (box2.h * (q3 - q1)) / maxD, 'rgba(74,163,255,0.45)');
      rect(g, x - 4, box2.y + box2.h - (box2.h * med) / maxD, 8, 1.5, '#4aa3ff');
    }
  },
});

function axes(g, box, xl, yl) {
  rect(g, box.x, box.y, box.w, box.h, 'rgba(255,255,255,0.03)');
  g.strokeStyle = 'rgba(255,255,255,0.18)';
  g.lineWidth = 1;
  g.beginPath();
  g.moveTo(box.x, box.y);
  g.lineTo(box.x, box.y + box.h);
  g.lineTo(box.x + box.w, box.y + box.h);
  g.stroke();
  text(g, xl, box.x + box.w / 2, box.y + box.h + 4,
    { size: 9, align: 'center', color: 'rgba(255,255,255,.45)' });
  g.save();
  g.translate(box.x - 28, box.y + box.h / 2);
  g.rotate(-Math.PI / 2);
  text(g, yl, 0, 0, { size: 9, align: 'center', color: 'rgba(255,255,255,.45)' });
  g.restore();
}

function stat(g, x, y, lines) {
  lines.forEach(([s, color], i) => {
    text(g, s, x, y + i * 14, { size: 10, align: 'right', color });
  });
}

const sum = (rows, k) => rows.reduce((acc, r) => acc + r[k], 0);
const mean = (rows, k) => (rows.length ? sum(rows, k) / rows.length : 0);
