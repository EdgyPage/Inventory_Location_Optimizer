'use strict';

// ── state ─────────────────────────────────────────────────────────────────────
const OUT = '../out/';
const NS  = 'http://www.w3.org/2000/svg';
let manifest = null;
let data     = null;        // current run JSON
let overlays = {};          // file -> {fill_overall:[], queue_depth:[]} for faint comparison
let curBatch = 0;
let playing  = false, animTimer = null;

const $ = id => document.getElementById(id);
const el = (tag, attrs = {}) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };

// ── colour scale for fill % ───────────────────────────────────────────────────
function lerp(a, b, t) { return a + (b - a) * t; }
function mix(c1, c2, t) { return `rgb(${Math.round(lerp(c1[0],c2[0],t))},${Math.round(lerp(c1[1],c2[1],t))},${Math.round(lerp(c1[2],c2[2],t))})`; }
function fillColor(f) {
  if (f == null) return '#222838';
  const RED=[122,31,31], AMBER=[199,125,46], GREEN=[46,158,87], BLUE=[47,143,176];
  if (f < 0.5)  return mix(RED, AMBER, f / 0.5);
  if (f < 0.85) return mix(AMBER, GREEN, (f - 0.5) / 0.35);
  if (f <= 1.0) return mix(GREEN, BLUE, (f - 0.85) / 0.15);   // saturating toward full
  return '#2f8fb0';
}

// ── bootstrap ─────────────────────────────────────────────────────────────────
fetch(OUT + 'manifest.json')
  .then(r => r.json())
  .then(m => {
    manifest = m;
    const sel = $('run-select');
    for (const run of m.runs) {
      const o = document.createElement('option');
      o.value = run.file;
      o.textContent = `${run.strategy}  (final ${(run.final_fill*100).toFixed(1)}%)`;
      sel.appendChild(o);
    }
    sel.addEventListener('change', () => loadRun(sel.value));
    // preload all runs for overlay, then show the first
    return Promise.all(m.runs.map(run =>
      fetch(OUT + run.file).then(r => r.json()).then(d => {
        overlays[run.file] = {
          fill_overall: d.batches.map(b => b.fill_overall),
          queue_depth:  d.batches.map(b => b.queue_depth),
        };
      })
    )).then(() => loadRun(m.runs[0].file));
  })
  .catch(err => { $('meta-msg').textContent = 'Error: ' + err.message + ' — run trace_lifecycle.py first.'; });

function loadRun(file) {
  fetch(OUT + file).then(r => r.json()).then(d => {
    data = d;
    $('run-select').value = file;
    const m = d.meta;
    const isTrace = m.source === 'trace';
    $('flow-panel').style.display = isTrace ? '' : 'none';
    $('fn-panel').style.display   = isTrace ? '' : 'none';
    const tgt = m.target_fill != null ? `target ${(m.target_fill*100).toFixed(0)}%` : '';
    $('meta-msg').textContent =
      `${m.strategy} · ${m.source} · ${(m.n_skus||0).toLocaleString()} SKUs · ` +
      `${(m.total_bins||0).toLocaleString()} bins · ${tgt}`;
    const sl = $('batch-slider');
    sl.max = d.batches.length - 1;
    curBatch = Math.min(curBatch, d.batches.length - 1);
    sl.value = curBatch;
    buildHeatmap();
    buildLegend();
    render(curBatch);
  });
}

// ── warehouse heatmap ─────────────────────────────────────────────────────────
const CW = 96, CH = 48, GAP = 6, PAD = 8;
function buildHeatmap() {
  const svg = $('heatmap-svg');
  svg.textContent = '';
  const aisles = data.warehouse.aisles;
  const cols = Math.max(...aisles.map(a => a.grid_col)) + 1;
  const rows = Math.max(...aisles.map(a => a.grid_row)) + 1;
  const W = PAD*2 + cols*CW + (cols-1)*GAP;
  const H = PAD*2 + rows*CH + (rows-1)*GAP;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);

  for (const a of aisles) {
    const x = PAD + a.grid_col*(CW+GAP);
    const y = PAD + a.grid_row*(CH+GAP);
    const g = el('g', {});
    const rect = el('rect', { id:'cell-'+a.aisle_id, x, y, width:CW, height:CH, rx:4, fill:'#222838', stroke:'#2c3446' });
    const t1 = el('text', { x:x+6, y:y+15, fill:'#cdd6e4', 'font-size':9, 'font-family':'monospace' });
    t1.textContent = `A${a.aisle_id} ${a.category.slice(0,4)} ${a.unit_type[0].toUpperCase()}`;
    const t2 = el('text', { id:'pct-'+a.aisle_id, x:x+6, y:y+34, fill:'#0c0f16', 'font-size':14, 'font-weight':'bold', 'font-family':'monospace' });
    t2.textContent = '';
    const title = el('title', {}); title.textContent = a.bucket; g.appendChild(title);
    g.appendChild(rect); g.appendChild(t1); g.appendChild(t2);
    svg.appendChild(g);
  }
}

function buildLegend() {
  const stops = [['0%',0],['25%',0.25],['50%',0.5],['70%',0.7],['85%',0.85],['100%',1.0]];
  $('heat-legend').innerHTML = 'fill: ' + stops.map(([lbl,f]) =>
    `<span><span class="heat-swatch" style="background:${fillColor(f)}"></span>${lbl}</span>`).join(' ');
}

// ── lifecycle flow ─────────────────────────────────────────────────────────────
const STAGES = [
  ['intake',   'Intake',    '#5b8def'],
  ['placed',   'Placed',    '#2e9e57'],
  ['stuck',    'Stuck',     '#d6603a'],
  ['picked',   'Picked',    '#c77d2e'],
  ['emptied',  'Emptied',   '#9a6cd0'],
  ['reclaimed','Reclaimed', '#3aa6b0'],
];
function renderFlow(b) {
  const sc = b.stage_counts || {};
  const max = Math.max(1, ...STAGES.map(([k]) => sc[k] || 0));
  const flow = $('flow'); flow.innerHTML = '';
  for (const [k, label, color] of STAGES) {
    const v = sc[k] || 0;
    const row = document.createElement('div'); row.className = 'flow-row';
    row.innerHTML =
      `<span class="flow-label">${label}</span>` +
      `<span class="flow-bar-wrap"><span class="flow-bar" style="width:${(v/max*100).toFixed(1)}%;background:${color}"></span></span>` +
      `<span class="flow-val">${v.toLocaleString()}</span>`;
    flow.appendChild(row);
  }
  const leak = $('leak-flag');
  const stuck = sc.stuck || 0, placed = sc.placed || 0, intake = sc.intake || 0;
  if (stuck > 0 || (intake > 0 && placed < intake)) {
    leak.className = 'warn';
    leak.textContent = `⚠ ${stuck.toLocaleString()} unit(s) stuck in queue (enqueued but not placed). ` +
      `Placed ${placed.toLocaleString()} of ${intake.toLocaleString()} intake.`;
  } else {
    leak.className = 'ok';
    leak.textContent = '✓ no units stuck this batch';
  }
}

// ── fn-execution table ──────────────────────────────────────────────────────────
function renderFnTable(b) {
  const tb = $('fn-table').querySelector('tbody'); tb.innerHTML = '';
  for (const f of (b.fn_trace || [])) {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${f.name}</td><td>${f.calls.toLocaleString()}</td><td>${(f.total_s*1000).toFixed(2)}</td>`;
    tb.appendChild(tr);
  }
}

// ── line charts ─────────────────────────────────────────────────────────────────
function lineChart(title, series, opts = {}) {
  // series: [{values:[], color, faint}]  — x index = batch
  const wrap = document.createElement('div'); wrap.className = 'chart';
  const t = document.createElement('div'); t.className = 'chart-title'; t.textContent = title; wrap.appendChild(t);
  const W = 320, H = 120, mb = 16, ml = 34, mt = 6, mr = 6;
  const svg = el('svg', { viewBox:`0 0 ${W} ${H}` });
  const n = Math.max(...series.map(s => s.values.length), 1);
  let max = opts.max != null ? opts.max : Math.max(1e-9, ...series.flatMap(s => s.values));
  let min = opts.min != null ? opts.min : 0;
  const px = i => ml + (n <= 1 ? 0 : i / (n - 1) * (W - ml - mr));
  const py = v => H - mb - (v - min) / (max - min) * (H - mb - mt);
  // axes
  svg.appendChild(el('line', { x1:ml, y1:H-mb, x2:W-mr, y2:H-mb, stroke:'#2c3446' }));
  for (const frac of [0, 0.5, 1]) {
    const v = min + frac * (max - min); const y = py(v);
    svg.appendChild(el('line', { x1:ml, y1:y, x2:W-mr, y2:y, stroke:'#1c2230' }));
    const lab = el('text', { x:2, y:y+3, fill:'#5a6680', 'font-size':8 });
    lab.textContent = opts.pct ? (v*100).toFixed(0)+'%' : (v>=1000 ? (v/1000).toFixed(1)+'k' : v.toFixed(0));
    svg.appendChild(lab);
  }
  for (const s of series) {
    if (!s.values.length) continue;
    const d = s.values.map((v,i) => `${i?'L':'M'}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join('');
    svg.appendChild(el('path', { d, fill:'none', stroke:s.color, 'stroke-width': s.faint?1:1.8, 'stroke-opacity': s.faint?0.28:1 }));
  }
  // selected-batch marker
  if (opts.marker != null && n > 1) {
    const x = px(opts.marker);
    svg.appendChild(el('line', { x1:x, y1:mt, x2:x, y2:H-mb, stroke:'#e6ecf5', 'stroke-opacity':0.5, 'stroke-dasharray':'2,2' }));
  }
  wrap.appendChild(svg);
  return wrap;
}

function buildCharts() {
  const host = $('charts'); host.innerHTML = '';
  // overlay faint lines from sibling runs of equal length
  const n = data.batches.length;
  const faintFill = [], faintQ = [];
  for (const file in overlays) {
    if (file === $('run-select').value) continue;
    if (overlays[file].fill_overall.length !== n) continue;
    faintFill.push({ values: overlays[file].fill_overall, color:'#8da2bd', faint:true });
    faintQ.push({ values: overlays[file].queue_depth, color:'#8da2bd', faint:true });
  }
  const sc = i => data.batches[i].stage_counts || {};
  const fill = data.batches.map(b => b.fill_overall);

  host.appendChild(lineChart('Fill % (overall)',
    [...faintFill, { values: fill, color:'#2e9e57' }],
    { pct:true, min:0, max:1, marker:curBatch }));

  if (data.meta.source === 'trace') {
    const q = data.batches.map(b => b.queue_depth);
    host.appendChild(lineChart('Queue depth (stuck units)',
      [...faintQ, { values: q, color:'#d6603a' }], { marker:curBatch }));
    host.appendChild(lineChart('Placed vs Picked / batch',
      [{ values: data.batches.map((_,i)=>sc(i).placed||0), color:'#2e9e57' },
       { values: data.batches.map((_,i)=>sc(i).picked||0), color:'#c77d2e' }],
      { marker:curBatch }));
    host.appendChild(lineChart('Emptied vs Reclaimed / batch',
      [{ values: data.batches.map((_,i)=>sc(i).emptied||0),   color:'#9a6cd0' },
       { values: data.batches.map((_,i)=>sc(i).reclaimed||0), color:'#3aa6b0' }],
      { marker:curBatch }));
  } else if (data.batches.some(b => b.duration != null)) {
    host.appendChild(lineChart('Batch duration (sim time)',
      [{ values: data.batches.map(b => b.duration || 0), color:'#c77d2e' }],
      { marker:curBatch }));
  }
}

// ── render ────────────────────────────────────────────────────────────────────
function render(bi) {
  curBatch = bi;
  const b = data.batches[bi];
  $('batch-display').textContent = `batch ${b.batch}` + (b.batch === 0 ? ' (stock)' : '');
  // heatmap
  for (const a of data.warehouse.aisles) {
    const f = b.fill_by_aisle[a.aisle_id];
    const rect = $('cell-'+a.aisle_id); if (rect) rect.setAttribute('fill', fillColor(f));
    const pct = $('pct-'+a.aisle_id); if (pct) pct.textContent = f == null ? '' : Math.round(f*100)+'%';
  }
  if (data.meta.source === 'trace') { renderFlow(b); renderFnTable(b); }
  buildCharts();
}

// ── controls ────────────────────────────────────────────────────────────────────
$('batch-slider').addEventListener('input', e => { if (data) render(+e.target.value); });
$('play-btn').addEventListener('click', () => {
  if (!data) return;
  playing = !playing;
  $('play-btn').innerHTML = playing ? '&#9646;&#9646;' : '&#9654;';
  if (playing) {
    animTimer = setInterval(() => {
      let b = curBatch + 1;
      if (b > data.batches.length - 1) b = 0;
      $('batch-slider').value = b; render(b);
    }, 350);
  } else { clearInterval(animTimer); }
});
