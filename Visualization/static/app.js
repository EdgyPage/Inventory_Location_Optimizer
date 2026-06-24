'use strict';
// DB-backed replay & compare viewer.  Up to 4 panes, each an overview heatmap of a run's
// aisles for the current batch with animated pickers; click an aisle to drill into its bins
// (SKU-coloured, live picks).  Batch + playback are synced across panes.

const MAX_PANES   = 4;
const PICKER_COLS = ['#2196F3', '#FF5722', '#4CAF50', '#E040FB', '#FFC107', '#00E5FF'];
const REORDER_FRAC = 0.12;   // leading fraction of the timeline used for the reorder phase

// ── state ──────────────────────────────────────────────────────────────────────
let RUNS = [];               // [{id,label,...}]
let panes = [];              // [{run, ov, scores, perAisleScore, canvas, ctx, layout}]
let curBatch = 0;
let maxBatch = 0;
let curT = 0;                // 0..1 normalised position in the (reorder+pick) timeline
let playing = false, lastTS = null, speed = 5, animH = null;
let colorMode = 'fill';
let activeOnly = true;
let modal = { run: null, aisle: null, data: null, t: 0 };

// ── DOM ─────────────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const runSelect = $('run-select'), panesEl = $('panes'), loadingEl = $('loading-msg');
const slider = $('time-slider'), timeDisp = $('time-display'), phaseLabel = $('phase-label');
const batchLabel = $('batch-label');

// ── init ──────────────────────────────────────────────────────────────────────
fetch('/api/runs').then(r => r.json()).then(d => {
  RUNS = d.runs || [];
  runSelect.innerHTML = '<option value="">— pick a run —</option>' +
    RUNS.map(r => `<option value="${r.id}">${r.label}  (${r.n_batches}b)</option>`).join('');
  loadingEl.textContent = `${RUNS.length} runs available · add up to ${MAX_PANES} panes`;
}).catch(e => { loadingEl.textContent = 'Error loading runs: ' + e.message; });

$('add-run').onclick = () => {
  const id = runSelect.value;
  if (!id || panes.length >= MAX_PANES) return;
  if (panes.some(p => p.run.id === id)) return;
  const run = RUNS.find(r => r.id === id);
  maxBatch = Math.max(maxBatch, (run.n_batches || 1) - 1);
  addPane(run);
};

$('batch-prev').onclick = () => gotoBatch(curBatch - 1);
$('batch-next').onclick = () => gotoBatch(curBatch + 1);
$('play-btn').onclick   = togglePlay;
$('speed-select').onchange = e => speed = parseFloat(e.target.value);
$('active-only').onchange  = e => { activeOnly = e.target.checked; layoutAll(); renderAll(); };
$('color-mode').onchange   = e => { colorMode = e.target.value; ensureScores().then(renderAll); };
slider.oninput = () => { curT = parseFloat(slider.value); if (!playing) renderAll(); updateTimeUI(); };
$('aisle-close').onclick = () => $('aisle-modal').classList.add('hidden');
window.addEventListener('resize', () => { sizeCanvases(); layoutAll(); renderAll(); });

// ── panes ───────────────────────────────────────────────────────────────────────
function addPane(run) {
  const el = document.createElement('div'); el.className = 'pane';
  el.innerHTML = `
    <div class="pane-head">
      <span class="pane-title">${run.strategy}</span>
      <span class="pane-stat"></span>
      <button class="pane-close">✕</button>
    </div>
    <div class="pane-canvas-wrap"><canvas></canvas></div>`;
  panesEl.appendChild(el);
  const pane = {
    run, el, ov: null, scores: null, perAisleScore: null,
    canvas: el.querySelector('canvas'), titleEl: el.querySelector('.pane-stat'),
    layout: null,
  };
  pane.ctx = pane.canvas.getContext('2d');
  pane.canvas.onclick = ev => onPaneClick(pane, ev);
  el.querySelector('.pane-close').onclick = () => removePane(pane);
  panes.push(pane);
  panesEl.className = panes.length >= 3 ? 'cols-4' : (panes.length === 2 ? 'cols-2' : '');
  $('run-count').textContent = `${panes.length} / ${MAX_PANES} panes`;
  slider.disabled = false;
  loadPane(pane, curBatch).then(() => { sizeCanvases(); layoutAll(); renderAll(); });
}

function removePane(pane) {
  panes = panes.filter(p => p !== pane);
  pane.el.remove();
  panesEl.className = panes.length >= 3 ? 'cols-4' : (panes.length === 2 ? 'cols-2' : '');
  $('run-count').textContent = `${panes.length} / ${MAX_PANES} panes`;
  sizeCanvases(); layoutAll(); renderAll();
}

function loadPane(pane, batch) {
  return fetch(`/api/overview?run=${encodeURIComponent(pane.run.id)}&batch=${batch}`)
    .then(r => r.json()).then(ov => { pane.ov = ov; pane._maxPicks = null; });
}

function ensureScores() {
  if (colorMode !== 'score') return Promise.resolve();
  return Promise.all(panes.filter(p => !p.scores).map(p =>
    fetch(`/api/scores?run=${encodeURIComponent(p.run.id)}`).then(r => r.json()).then(s => {
      p.scores = s;
      const sum = {}, cnt = {};
      for (const [k, v] of Object.entries(s)) {
        const a = +k.split(',', 1)[0]; sum[a] = (sum[a] || 0) + v; cnt[a] = (cnt[a] || 0) + 1;
      }
      p.perAisleScore = {}; for (const a in sum) p.perAisleScore[a] = sum[a] / cnt[a];
    })));
}

// ── batch / playback ──────────────────────────────────────────────────────────
function gotoBatch(b) {
  b = Math.max(0, Math.min(maxBatch, b));
  if (b === curBatch && panes.every(p => p.ov)) return;
  curBatch = b; curT = 0;
  Promise.all(panes.map(p => loadPane(p, b))).then(() => {
    layoutAll(); updateTimeUI(); renderAll();
  });
}

function togglePlay() {
  if (!panes.length) return;
  playing = !playing;
  $('play-btn').innerHTML = playing ? '&#9646;&#9646;' : '&#9654;';
  if (playing) { lastTS = null; animH = requestAnimationFrame(animate); }
  else if (animH) cancelAnimationFrame(animH);
}

function animate(ts) {
  if (!playing) return;
  if (lastTS !== null) {
    // one normalised unit = ~ (max sim-time / speed-scaled) seconds; advance by wallclock.
    curT += (ts - lastTS) / 1000 * (speed / 20);
    if (curT >= 1) {                       // roll to next batch (synced restart at t=0)
      if (curBatch < maxBatch) { lastTS = ts; gotoBatch(curBatch + 1); return; }
      curT = 1; playing = false; $('play-btn').innerHTML = '&#9654;';
    }
  }
  lastTS = ts;
  slider.value = curT; updateTimeUI(); renderAll();
  if (playing) animH = requestAnimationFrame(animate);
}

function updateTimeUI() {
  batchLabel.textContent = `batch ${curBatch} / ${maxBatch}`;
  const reorder = curT < REORDER_FRAC;
  phaseLabel.textContent = reorder ? 'reorder ⇣' : 'picking';
  phaseLabel.classList.toggle('reorder', reorder);
  const mt = panes[0] && panes[0].ov ? panes[0].ov.max_time : 0;
  const simT = reorder ? 0 : ((curT - REORDER_FRAC) / (1 - REORDER_FRAC)) * mt;
  timeDisp.textContent = reorder ? 'reorder phase' : `t = ${simT.toFixed(0)}`;
}

// pick-phase sim-time for a pane at the global curT (0 during reorder phase)
function paneSimTime(pane) {
  if (curT < REORDER_FRAC || !pane.ov) return 0;
  return ((curT - REORDER_FRAC) / (1 - REORDER_FRAC)) * pane.ov.max_time;
}

// ── canvas sizing / layout ──────────────────────────────────────────────────────
function sizeCanvases() {
  const dpr = window.devicePixelRatio || 1;
  for (const p of panes) {
    const w = p.canvas.clientWidth, h = p.canvas.clientHeight;
    p.canvas.width = Math.max(1, w * dpr); p.canvas.height = Math.max(1, h * dpr);
    p.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
}

function paneAisles(pane) {
  if (!pane.ov) return [];
  return activeOnly ? pane.ov.aisles.filter(a => a.active) : pane.ov.aisles;
}

function layoutAll() {
  for (const p of panes) layoutPane(p);
}

function layoutPane(pane) {
  const aisles = paneAisles(pane);
  const W = pane.canvas.clientWidth || 1, H = pane.canvas.clientHeight || 1;
  const n = Math.max(1, aisles.length);
  // choose grid columns to roughly fill the pane aspect ratio
  let cols = activeOnly ? Math.max(1, Math.round(Math.sqrt(n * W / Math.max(H, 1))))
                        : (pane.ov ? pane.ov.grid_cols : 6);
  cols = Math.min(cols, n);
  const rows = Math.ceil(n / cols);
  const pad = 6, gap = 3;
  const cw = (W - 2 * pad - (cols - 1) * gap) / cols;
  const ch = (H - 2 * pad - (rows - 1) * gap) / rows;
  const cells = aisles.map((a, i) => {
    const c = i % cols, r = Math.floor(i / cols);
    return { a, x: pad + c * (cw + gap), y: pad + r * (ch + gap), w: cw, h: ch };
  });
  pane.layout = { cells, cw, ch };
}

// ── colours ─────────────────────────────────────────────────────────────────────
function skuColor(sku) {
  if (sku == null) return '#2a2f3a';
  const h = ((sku * 2654435761) % 360 + 360) % 360;
  return `hsl(${h} 70% 55%)`;
}
function heat(v) {                       // 0..1 → blue→green→amber→red
  v = Math.max(0, Math.min(1, v));
  const h = (1 - v) * 220;               // 220 (blue) .. 0 (red)
  return `hsl(${h} 75% 50%)`;
}
function aisleColor(pane, a) {
  if (colorMode === 'picks') {
    const mx = pane._maxPicks || (pane._maxPicks = Math.max(1, ...pane.ov.aisles.map(x => x.picks)));
    return a.picks ? heat(a.picks / mx) : '#1b2030';
  }
  if (colorMode === 'score' && pane.perAisleScore) {
    const vals = Object.values(pane.perAisleScore);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const s = pane.perAisleScore[a.aisle_id];
    return s == null ? '#1b2030' : heat((s - lo) / (hi - lo || 1));
  }
  return a.capacity ? heat(a.fill) : '#1b2030';     // fill (default)
}

// ── render overview ─────────────────────────────────────────────────────────────
function renderAll() { for (const p of panes) renderPane(p); }

function renderPane(pane) {
  const ctx = pane.ctx; if (!ctx) return;
  ctx.clearRect(0, 0, pane.canvas.clientWidth, pane.canvas.clientHeight);
  if (!pane.ov || !pane.layout) return;
  const { cells } = pane.layout;
  const cellByAisle = {};
  for (const c of cells) {
    cellByAisle[c.a.aisle_id] = c;
    ctx.fillStyle = aisleColor(pane, c.a);
    ctx.fillRect(c.x, c.y, c.w, c.h);
    if (c.a.active) { ctx.strokeStyle = '#00E5FF'; ctx.lineWidth = 1.5;
      ctx.strokeRect(c.x + 0.5, c.y + 0.5, c.w - 1, c.h - 1); }
    if (c.w > 26 && c.h > 14) {
      ctx.fillStyle = 'rgba(255,255,255,.65)'; ctx.font = '9px monospace';
      ctx.fillText('A' + c.a.aisle_id, c.x + 3, c.y + 11);
    }
  }
  // pickers (aisle-level), interpolated along waypoints by sim-time
  const t = paneSimTime(pane);
  for (const pp of pane.ov.picker_paths) {
    const pos = pickerCellAt(pp.waypoints, t, cellByAisle);
    if (!pos) continue;
    ctx.beginPath(); ctx.arc(pos.x, pos.y, Math.min(7, pane.layout.cw / 4), 0, 7);
    ctx.fillStyle = PICKER_COLS[pp.picker_id % PICKER_COLS.length];
    ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.stroke();
  }
  // header stats
  const q = pane.ov.timing || {};
  pane.titleEl.textContent =
    `b${pane.ov.batch} · fill ${(avgFill(pane) * 100).toFixed(0)}% · q ${q.queue_depth ?? 0}` +
    (q.in_transit_qty ? ` · transit ${q.in_transit_qty}` : '');
}

function avgFill(pane) {
  const a = pane.ov.aisles, cap = a.reduce((s, x) => s + x.capacity, 0) || 1;
  return a.reduce((s, x) => s + x.occupied, 0) / cap;
}

function pickerCellAt(wp, t, cellByAisle) {
  if (!wp.length) return null;
  let i = 0; while (i < wp.length - 1 && wp[i + 1].t <= t) i++;
  const c0 = cellByAisle[wp[i].aisle_id];
  if (t <= wp[0].t || i === wp.length - 1) return c0 ? center(c0) : null;
  const c1 = cellByAisle[wp[i + 1].aisle_id];
  if (!c0 || !c1) return c0 ? center(c0) : (c1 ? center(c1) : null);
  const f = (t - wp[i].t) / (wp[i + 1].t - wp[i].t || 1);
  const a = center(c0), b = center(c1);
  return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f };
}
const center = c => ({ x: c.x + c.w / 2, y: c.y + c.h / 2 });

// ── drill-in ─────────────────────────────────────────────────────────────────────
function onPaneClick(pane, ev) {
  if (!pane.layout) return;
  const rect = pane.canvas.getBoundingClientRect();
  const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
  const cell = pane.layout.cells.find(c => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
  if (!cell) return;
  openAisle(pane, cell.a.aisle_id);
}

function openAisle(pane, aisle) {
  modal = { run: pane.run, aisle, data: null, scores: pane.scores };
  $('aisle-modal-title').textContent = `${pane.run.strategy} · Aisle ${aisle} · batch ${curBatch}`;
  $('aisle-modal').classList.remove('hidden');
  fetch(`/api/aisle?run=${encodeURIComponent(pane.run.id)}&batch=${curBatch}&aisle=${aisle}`)
    .then(r => r.json()).then(d => { modal.data = d; renderAisle(); });
}

function renderAisle() {
  const d = modal.data; if (!d) return;
  const cv = $('aisle-canvas'), ctx = cv.getContext('2d');
  // grid extent from bin keys
  let mx = 1, my = 1;
  for (const k of Object.keys(d.bins)) { const [, bx, by] = k.split(',').map(Number); mx = Math.max(mx, bx); my = Math.max(my, by); }
  // also account for empty bins via picks events range
  for (const e of d.events) if (e.location) { mx = Math.max(mx, e.location[1]); my = Math.max(my, e.location[2]); }
  const BIN = 16, GAP = 2, PAD = 12;
  cv.width = PAD * 2 + mx * (BIN + GAP); cv.height = PAD * 2 + my * (BIN + GAP) + 4;
  ctx.clearRect(0, 0, cv.width, cv.height);
  // bin state at this sim-time: start state minus picks ≤ t
  const t = aisleSimTime();
  const qty = {}; const startSku = {};
  for (const [k, v] of Object.entries(d.bins)) { qty[k] = v.qty; startSku[k] = v.sku; }
  const active = new Set();
  for (const e of d.events) {
    if (e.time > t) break;
    if (e.event_type === 'pick' && e.location) {
      const k = e.location.join(','); if (k in qty) qty[k] = Math.max(0, qty[k] - (e.quantity || 0));
    }
  }
  // active bins = last 'arrive' per picker ≤ t
  const lastArrive = {};
  for (const e of d.events) { if (e.time > t) break; if (e.event_type === 'arrive' && e.location) lastArrive[e.picker_id] = e.location.join(','); }
  for (const k of Object.values(lastArrive)) active.add(k);

  for (let by = 1; by <= my; by++) for (let bx = 1; bx <= mx; bx++) {
    const k = `${d.aisle_id},${bx},${by}`;
    const x = PAD + (bx - 1) * (BIN + GAP), y = PAD + (by - 1) * (BIN + GAP);
    let col = '#1a1f2b';                          // empty bin
    if (k in startSku) {
      if (modal.scores && colorMode === 'score') {
        // not typical in modal; fall through to sku colour
      }
      const base = skuColor(startSku[k]);
      const frac = qty[k] / (d.bins[k].qty || 1);
      col = qty[k] <= 0 ? '#3a2a2a' : base;
      ctx.globalAlpha = qty[k] <= 0 ? 1 : 0.4 + 0.6 * Math.min(1, frac);
    } else ctx.globalAlpha = 1;
    ctx.fillStyle = col; ctx.fillRect(x, y, BIN, BIN);
    ctx.globalAlpha = 1;
    if (active.has(k)) { ctx.strokeStyle = '#00E5FF'; ctx.lineWidth = 2; ctx.strokeRect(x + 1, y + 1, BIN - 2, BIN - 2); }
  }
  // pickers in this aisle
  for (const [pid, k] of Object.entries(lastArrive)) {
    const [, bx, by] = k.split(',').map(Number);
    const x = PAD + (bx - 1) * (BIN + GAP) + BIN / 2, y = PAD + (by - 1) * (BIN + GAP) + BIN / 2;
    ctx.beginPath(); ctx.arc(x, y, 6, 0, 7);
    ctx.fillStyle = PICKER_COLS[pid % PICKER_COLS.length]; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.stroke();
  }
  $('aisle-legend').innerHTML =
    'Bins coloured by SKU · opacity = remaining qty · <span class="sw" style="background:#3a2a2a"></span>emptied · cyan = active pick';
}

function aisleSimTime() {
  // reuse the global timeline but against this aisle's own max event time
  const d = modal.data; if (!d || !d.events.length) return 0;
  const mt = d.events[d.events.length - 1].time;
  return curT < REORDER_FRAC ? 0 : ((curT - REORDER_FRAC) / (1 - REORDER_FRAC)) * mt;
}

// keep the drill-in in sync while playing/scrubbing
const _origRenderAll = renderAll;
renderAll = function () { _origRenderAll(); if (!$('aisle-modal').classList.contains('hidden')) renderAisle(); };
