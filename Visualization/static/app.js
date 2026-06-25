'use strict';
// DB-backed replay & compare viewer.  Up to 4 panes, synced batch + playback.
//   active-aisles mode (default): only aisles where a picker is CURRENTLY located, each drawn
//     as its bin layout with the picker dot + Manhattan-routed arrows tracing the pick path.
//   all-aisles mode: zoomed-out per-aisle heatmap (fill / picks / layout score).
// Click any aisle to drill into its full bin grid; click a bin to inspect its status.

const MAX_PANES    = 4;
const PICKER_COLS  = ['#2196F3', '#FF5722', '#4CAF50', '#E040FB', '#FFC107', '#00E5FF'];
const REORDER_FRAC = 0.12;   // leading fraction of the timeline used for the reorder phase

// ── state ──────────────────────────────────────────────────────────────────────
let RUNS = [];               // [{id,label,...}]
let panes = [];              // [{run, ov, bd, scores, perAisleScore, canvas, ctx, layout, ...}]
let curBatch = 0;
let maxBatch = 0;
let curT = 0;                // 0..1 normalised position in the (reorder+pick) timeline
let playing = false, lastTS = null, speed = 5, animH = null;
let colorMode = 'fill';
let activeOnly = true;
let loadToken = 0;           // monotonically increasing; stale fetch responses are dropped
let modal = { run: null, aisle: null, data: null, scores: null, sel: null };

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
$('active-only').onchange  = e => { activeOnly = e.target.checked; refresh(true); };
$('color-mode').onchange   = e => { colorMode = e.target.value; ensureScores().then(renderAll); };
slider.oninput = () => { curT = parseFloat(slider.value); if (!playing) renderAll(); updateTimeUI(); };
$('aisle-close').onclick = () => $('aisle-modal').classList.add('hidden');
$('aisle-canvas').onclick = onAisleCanvasClick;
$('queue-btn').onclick = () => {
  $('queue-modal').classList.remove('hidden');
  Promise.all(panes.filter(p => !p.scores).map(fetchScores)).then(renderQueuePanel);
};
$('queue-close').onclick = () => $('queue-modal').classList.add('hidden');
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
    run, el, ov: null, bd: null, scores: null, perAisleScore: null,
    canvas: el.querySelector('canvas'), titleEl: el.querySelector('.pane-stat'),
    layout: null, _tiles: null, _byPicker: null, _byAisle: null, _maxPicks: null,
  };
  pane.ctx = pane.canvas.getContext('2d');
  pane.canvas.onclick = ev => onPaneClick(pane, ev);
  el.querySelector('.pane-close').onclick = () => removePane(pane);
  panes.push(pane);
  setGridClass();
  $('run-count').textContent = `${panes.length} / ${MAX_PANES} panes`;
  slider.disabled = false;
  const tok = loadToken;
  loadPane(pane, curBatch, tok).then(() => {
    if (tok !== loadToken) return;
    sizeCanvases(); layoutAll(); renderAll();
  });
}

function removePane(pane) {
  panes = panes.filter(p => p !== pane);
  pane.el.remove();
  setGridClass();
  $('run-count').textContent = `${panes.length} / ${MAX_PANES} panes`;
  sizeCanvases(); layoutAll(); renderAll();
}

function setGridClass() {
  panesEl.className = panes.length >= 3 ? 'cols-4' : (panes.length === 2 ? 'cols-2' : '');
}

// Fetch the dataset the CURRENT mode needs.  active-only → /api/batch (bin detail + events);
// all-aisles → /api/overview (cheap per-aisle aggregates).  Responses from a superseded
// request (token mismatch) are dropped so panes never drift out of sync.
function loadPane(pane, batch, tok) {
  if (activeOnly) {
    return fetch(`/api/batch?run=${encodeURIComponent(pane.run.id)}&batch=${batch}`)
      .then(r => r.json()).then(bd => {
        if (tok !== loadToken) return;
        pane.bd = bd; indexBatch(pane);
      });
  }
  return fetch(`/api/overview?run=${encodeURIComponent(pane.run.id)}&batch=${batch}`)
    .then(r => r.json()).then(ov => {
      if (tok !== loadToken) return;
      pane.ov = ov; pane._maxPicks = null;
    });
}

// Group a batch's events + start bins by picker/aisle once, so per-frame rendering is cheap
// (the bin map can be ~80k entries — re-scanning it per tile per frame would be far too slow).
function indexBatch(pane) {
  const byP = {}, byA = {}, binsByA = {};
  for (const e of (pane.bd.events || [])) {
    if (e.picker_id != null) (byP[e.picker_id] || (byP[e.picker_id] = [])).push(e);
    if (e.aisle_id != null)  (byA[e.aisle_id]  || (byA[e.aisle_id]  = [])).push(e);
  }
  let occ = 0;
  for (const [k, v] of Object.entries(pane.bd.bins || {})) {
    const p = k.split(',');
    (binsByA[p[0]] || (binsByA[p[0]] = {}))[p[1] + ',' + p[2]] = v;
    if (v.qty > 0) occ++;
  }
  let cap = 0; const ag = pane.bd.aisle_geom || {};
  for (const a of (pane.bd.active_aisles || [])) { const g = ag[a]; if (g) cap += g.bay_x * g.bay_y; }
  pane._byPicker = byP; pane._byAisle = byA; pane._binsByAisle = binsByA;
  pane._occ = occ; pane._cap = cap;
}

function ensureScores() {
  if (colorMode !== 'score' && colorMode !== 'mappref') return Promise.resolve();
  return Promise.all(panes.filter(p => !p.scores).map(fetchScores));
}
// per-aisle mean of a {key: value} bin map → {aisle_id: mean}
function _perAisle(map) {
  const sum = {}, cnt = {};
  for (const [k, v] of Object.entries(map || {})) {
    const a = +k.split(',', 1)[0]; sum[a] = (sum[a] || 0) + v; cnt[a] = (cnt[a] || 0) + 1;
  }
  const out = {}; for (const a in sum) out[a] = sum[a] / cnt[a];
  return out;
}
function fetchScores(pane) {
  // {layout:{key:score}, map_pref:{key:score}, has_map, source}
  return fetch(`/api/scores?run=${encodeURIComponent(pane.run.id)}`).then(r => r.json()).then(s => {
    pane.scores = s;
    pane.perAisleScore   = _perAisle(s.layout);
    pane.perAisleMapPref = _perAisle(s.map_pref);
  });
}
function fetchSkuScores(pane) {
  if (pane.skuScores) return Promise.resolve();
  return fetch(`/api/sku_scores?run=${encodeURIComponent(pane.run.id)}`)
    .then(r => r.json()).then(s => { pane.skuScores = s; });
}

// ── batch / playback ──────────────────────────────────────────────────────────
function gotoBatch(b) {
  b = Math.max(0, Math.min(maxBatch, b));
  if (b === curBatch && panes.every(paneReady)) return;
  curBatch = b; curT = 0;
  refresh(true);
}

// (Re)load all panes for the current batch under a fresh token; render once they all settle.
function refresh(reload) {
  if (!panes.length) { updateTimeUI(); return; }
  const tok = ++loadToken;
  const jobs = reload ? panes.map(p => loadPane(p, curBatch, tok))
                      : Promise.resolve();
  Promise.all([].concat(jobs)).then(() => {
    if (tok !== loadToken) return;
    return ensureScores();
  }).then(() => {
    if (tok !== loadToken) return;
    sizeCanvases(); layoutAll(); updateTimeUI(); renderAll();
  });
}

function paneReady(p) {
  const d = activeOnly ? p.bd : p.ov;
  return !!d && d.batch === curBatch;
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
  const d = panes[0] && (activeOnly ? panes[0].bd : panes[0].ov);
  const mt = d ? d.max_time : 0;
  const simT = reorder ? 0 : ((curT - REORDER_FRAC) / (1 - REORDER_FRAC)) * mt;
  timeDisp.textContent = reorder ? 'reorder phase' : `t = ${simT.toFixed(0)}`;
}

// pick-phase sim-time for a pane at the global curT (0 during reorder phase)
function paneSimTime(pane) {
  const d = activeOnly ? pane.bd : pane.ov;
  if (curT < REORDER_FRAC || !d) return 0;
  return ((curT - REORDER_FRAC) / (1 - REORDER_FRAC)) * d.max_time;
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

function layoutAll() { for (const p of panes) if (!activeOnly) layoutPane(p); }

// all-aisles heatmap layout (one cell per aisle)
function layoutPane(pane) {
  if (!pane.ov) { pane.layout = null; return; }
  const aisles = pane.ov.aisles;
  const W = pane.canvas.clientWidth || 1, H = pane.canvas.clientHeight || 1;
  const n = Math.max(1, aisles.length);
  let cols = pane.ov.grid_cols || 6;
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

// grid of tile rects to hold n items inside (W,H)
function tileGrid(n, W, H, pad, gap, headerH) {
  n = Math.max(1, n);
  let cols = Math.max(1, Math.round(Math.sqrt(n * W / Math.max(H, 1))));
  cols = Math.min(cols, n);
  const rows = Math.ceil(n / cols);
  const tw = (W - 2 * pad - (cols - 1) * gap) / cols;
  const th = (H - 2 * pad - (rows - 1) * gap) / rows;
  const out = [];
  for (let i = 0; i < n; i++) {
    const c = i % cols, r = Math.floor(i / cols);
    out.push({ x: pad + c * (tw + gap), y: pad + r * (th + gap), w: tw, h: th });
  }
  return out;
}

// ── colours ─────────────────────────────────────────────────────────────────────
function skuColor(sku) {
  if (sku == null) return '#2a2f3a';
  const h = ((sku * 2654435761) % 360 + 360) % 360;
  return `hsl(${h} 70% 55%)`;
}
function heat(v) {                       // 0..1 → blue→green→amber→red
  v = Math.max(0, Math.min(1, v));
  return `hsl(${(1 - v) * 220} 75% 50%)`;
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
  if (colorMode === 'mappref' && pane.perAisleMapPref) {
    const vals = Object.values(pane.perAisleMapPref);
    if (!vals.length) return '#1b2030';            // non-map strategy: no pref
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const s = pane.perAisleMapPref[a.aisle_id];
    return s == null ? '#1b2030' : heat((s - lo) / (hi - lo || 1));
  }
  return a.capacity ? heat(a.fill) : '#1b2030';     // fill (default)
}

// ── render dispatch ───────────────────────────────────────────────────────────
function renderAll() {
  for (const p of panes) (activeOnly ? renderActive : renderOverview)(p);
  if (!$('aisle-modal').classList.contains('hidden')) renderAisle();
  if (!$('queue-modal').classList.contains('hidden')) renderQueuePanel();
}

// ── queue panel: standing reorder queues + score summary, side by side per pane ──
function paneQueue(pane) {
  const d = activeOnly ? pane.bd : pane.ov;
  return d ? (d.reorder_queue || []) : [];
}
function paneTiming(pane) {
  const d = activeOnly ? pane.bd : pane.ov;
  return (d && d.timing) || {};
}
function mean(obj) {
  const v = Object.values(obj || {}); if (!v.length) return null;
  return v.reduce((a, b) => a + b, 0) / v.length;
}

function renderQueuePanel() {
  $('queue-batch').textContent = `batch ${curBatch} / ${maxBatch}`;
  if (!panes.length) { $('queue-body').innerHTML = '<div class="hint">Add runs to compare.</div>'; return; }
  const cards = panes.map(pane => {
    const q = paneQueue(pane), tm = paneTiming(pane);
    const lead = q.filter(r => r.kind === 'lead').sort((a, b) => b.qty - a.qty);
    const stock = q.filter(r => r.kind === 'stock').sort((a, b) => b.qty - a.qty);
    const leadItems = lead.reduce((s, r) => s + r.qty, 0);
    const stockItems = stock.reduce((s, r) => s + r.qty, 0);
    const avgLayout = pane.scores ? mean(pane.scores.layout) : null;
    const hasMap = pane.scores && pane.scores.has_map;
    const qrow = r => `<tr><td>#${r.sku}</td><td>${r.qty}</td>`
      + `<td>${r.unit_type ? r.unit_type + '/' + r.storage_size : '—'}</td>`
      + `<td>${r.kind === 'lead' ? '+' + r.remaining_lead + 'b' : 'ready'}</td></tr>`;
    const tbl = rows => rows.length
      ? `<table class="qtbl"><tr><th>sku</th><th>qty</th><th>unit</th><th>eta</th></tr>`
        + rows.slice(0, 25).map(qrow).join('')
        + (rows.length > 25 ? `<tr><td colspan="4" class="hint">+${rows.length - 25} more…</td></tr>` : '')
        + `</table>`
      : '<div class="hint">empty</div>';
    return `<div class="qcard">
      <div class="qcard-h">${pane.run.strategy}</div>
      <div class="qstat">queue ${tm.queue_depth ?? 0} · lead ${tm.lead_queue_depth ?? 0}`
      + ` (${tm.in_transit_qty ?? 0}u) · avg layout ${avgLayout == null ? '—' : avgLayout.toFixed(1)}`
      + `${hasMap ? ' · map ✓' : ''}</div>
      <div class="qcols">
        <div><div class="qttl">lead / in-transit (${leadItems}u)</div>${tbl(lead)}</div>
        <div><div class="qttl">stock / awaiting bin (${stockItems}u)</div>${tbl(stock)}</div>
      </div></div>`;
  });
  const note = panes.some(p => paneQueue(p).length)
    ? '' : '<div class="hint" style="margin-bottom:8px">No queued reorders recorded for this batch (older runs predate the reorder_queue table).</div>';
  $('queue-body').innerHTML = note + cards.join('');
}

// ── render: all-aisles heatmap ──────────────────────────────────────────────────
function renderOverview(pane) {
  const ctx = pane.ctx; if (!ctx) return;
  ctx.clearRect(0, 0, pane.canvas.clientWidth, pane.canvas.clientHeight);
  pane._tiles = null;
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
  const t = paneSimTime(pane);
  for (const pp of pane.ov.picker_paths) {
    const pos = pickerCellAt(pp.waypoints, t, cellByAisle);
    if (!pos) continue;
    ctx.beginPath(); ctx.arc(pos.x, pos.y, Math.min(7, pane.layout.cw / 4), 0, 7);
    ctx.fillStyle = PICKER_COLS[pp.picker_id % PICKER_COLS.length];
    ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.stroke();
  }
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

// ── render: active-aisle layouts (pickers + Manhattan arrows) ───────────────────
function renderActive(pane) {
  const ctx = pane.ctx, W = pane.canvas.clientWidth, H = pane.canvas.clientHeight;
  if (!ctx) return;
  ctx.clearRect(0, 0, W, H);
  pane._tiles = null;
  const bd = pane.bd;
  if (!bd) { drawCenter(ctx, W, H, 'loading…'); return; }

  if (curT < REORDER_FRAC) { renderReorder(pane); return; }

  const t = paneSimTime(pane);
  // pickers currently located in an aisle, grouped by aisle
  const inAisle = {};                       // aisle_id -> [{pid, st}]
  for (const pid in pane._byPicker) {
    const st = pickerStateAt(pane._byPicker[pid], t);
    if (st && !st.left) (inAisle[st.aisle] || (inAisle[st.aisle] = [])).push({ pid: +pid, st });
  }
  const aisles = Object.keys(inAisle).map(Number).sort((a, b) => a - b);
  if (!aisles.length) { drawCenter(ctx, W, H, 'no active pickers at this instant'); }

  const rects = tileGrid(aisles.length, W, H, 6, 6, 14);
  pane._tiles = aisles.map((aid, i) => ({ aisle_id: aid, ...rects[i] }));
  for (let i = 0; i < aisles.length; i++) {
    drawAisleTile(ctx, rects[i], pane, aisles[i], t, inAisle[aisles[i]]);
  }

  // header: live aisle count + active-aisle fill + queue (occ/cap precomputed in indexBatch)
  const q = bd.timing || {}, cap = pane._cap || 0;
  pane.titleEl.textContent =
    `b${bd.batch} · ${aisles.length} live · fill ${cap ? (pane._occ / cap * 100).toFixed(0) : 0}% · q ${q.queue_depth ?? 0}` +
    (q.in_transit_qty ? ` · transit ${q.in_transit_qty}` : '');
}

function renderReorder(pane) {
  const ctx = pane.ctx, W = pane.canvas.clientWidth, H = pane.canvas.clientHeight;
  const q = (pane.bd && pane.bd.reorder_queue) || [];
  const items = q.reduce((s, r) => s + (r.qty || 0), 0);
  const skus = new Set(q.map(r => r.sku)).size;
  drawCenter(ctx, W, H, q.length
    ? `reorder phase — restocking ${items} units · ${skus} SKUs queued`
    : 'reorder phase — (no queue recorded for this run)');
  // a little stacked-bar of queued SKUs, coloured by SKU
  if (!q.length) return;
  const top = [...q].sort((a, b) => b.qty - a.qty).slice(0, 40);
  const tot = top.reduce((s, r) => s + r.qty, 0) || 1;
  const bx = 16, bw = W - 32, by = H / 2 + 18, bh = 16; let x = bx;
  for (const r of top) {
    const w = bw * r.qty / tot;
    ctx.fillStyle = skuColor(r.sku); ctx.fillRect(x, by, Math.max(1, w), bh); x += w;
  }
  ctx.strokeStyle = '#30363d'; ctx.strokeRect(bx, by, bw, bh);
}

// current state of one picker within the batch at sim-time t
function pickerStateAt(ev, t) {
  let i = -1;
  for (let j = 0; j < ev.length; j++) { if (ev[j].time <= t) i = j; else break; }
  if (i < 0) return null;                              // not started
  let li = i; while (li >= 0 && !ev[li].location) li--;
  if (li < 0) return null;
  const aisle = ev[li].location[0];
  // ended this aisle's task before/at t (task_end/done after the last located event)?
  for (let j = li + 1; j <= i; j++) {
    const ty = ev[j].event_type;
    if (ty === 'task_end' || ty === 'done')
      return { aisle, pos: ev[li].location.slice(1).map(Number),
               path: [ev[li].location.slice(1).map(Number)], left: true };
  }
  // path = located events back to the 'arrive' that began this aisle visit
  // (collapse consecutive duplicate bins, e.g. arrive + first pick at the same bin)
  const path = [];
  const samePt = (a, b) => a && b && a[0] === b[0] && a[1] === b[1];
  for (let j = li; j >= 0; j--) {
    const e = ev[j];
    if (!e.location || e.location[0] !== aisle) { if (e.location) break; else continue; }
    const pt = e.location.slice(1).map(Number);
    if (!samePt(pt, path[0])) path.unshift(pt);
    if (e.event_type === 'arrive') break;
  }
  const from = ev[li].location.slice(1).map(Number);
  let pos = from, toLoc = null, nextT = 0;
  for (let j = i + 1; j < ev.length; j++) {            // next located event → interpolate
    const e = ev[j];
    if (e.location) {
      if (e.location[0] === aisle) { toLoc = e.location.slice(1).map(Number); nextT = e.time; }
      break;
    }
  }
  if (toLoc) {
    const f = Math.max(0, Math.min(1, (t - ev[li].time) / ((nextT - ev[li].time) || 1)));
    pos = manhattanLerp(from, toLoc, f);
    if (f > 0 && !samePt(toLoc, path[path.length - 1])) path.push(toLoc);
  }
  return { aisle, pos, path, from, toLoc, left: false };
}

function manhattanLerp(a, b, f) {        // move along x first, then y
  const dx = b[0] - a[0], dy = b[1] - a[1], tot = Math.abs(dx) + Math.abs(dy);
  if (!tot) return [a[0], a[1]];
  let d = f * tot;
  const xm = Math.min(Math.abs(dx), d); d -= xm;
  const ym = Math.min(Math.abs(dy), d);
  return [a[0] + Math.sign(dx) * xm, a[1] + Math.sign(dy) * ym];
}

// bin qty/sku at sim-time t for one aisle (keyed "bx,by"); only that aisle's bins + events scanned
function aisleBinState(pane, aisleId, t) {
  const qty = {}, startQty = {}, sku = {};
  const src = (pane._binsByAisle || {})[aisleId] || {};
  for (const [kk, v] of Object.entries(src)) { qty[kk] = v.qty; startQty[kk] = v.qty; sku[kk] = v.sku; }
  const evs = pane._byAisle[aisleId] || [];
  for (const e of evs) {
    if (e.time > t) break;
    if (e.event_type === 'pick' && e.location) {
      const kk = e.location[1] + ',' + e.location[2];
      if (kk in qty) qty[kk] = Math.max(0, qty[kk] - (e.quantity || 0));
      if (e.sku != null && sku[kk] == null) sku[kk] = e.sku;
    }
  }
  return { qty, startQty, sku };
}

function drawAisleTile(ctx, rc, pane, aisleId, t, pickers) {
  const geom = (pane.bd.aisle_geom || {})[aisleId] || {};
  const bx = Math.max(1, geom.bay_x || 1), by = Math.max(1, geom.bay_y || 1);
  const headerH = 13, pad = 4;
  // tile frame + label
  ctx.fillStyle = '#0b0e14'; ctx.fillRect(rc.x, rc.y, rc.w, rc.h);
  ctx.strokeStyle = '#00E5FF'; ctx.lineWidth = 1;
  ctx.strokeRect(rc.x + 0.5, rc.y + 0.5, rc.w - 1, rc.h - 1);
  ctx.fillStyle = 'rgba(255,255,255,.8)'; ctx.font = '9px monospace';
  ctx.fillText(`A${aisleId} · ${bx}×${by}`, rc.x + 4, rc.y + 10);

  const gx = rc.x + pad, gy = rc.y + headerH, gw = rc.w - 2 * pad, gh = rc.h - headerH - pad;
  const binW = gw / bx, binH = gh / by;
  const cx = bxi => gx + (bxi - 0.5) * binW, cy = byi => gy + (byi - 0.5) * binH;

  const { qty, startQty, sku } = aisleBinState(pane, aisleId, t);
  for (let yi = 1; yi <= by; yi++) for (let xi = 1; xi <= bx; xi++) {
    const kk = xi + ',' + yi;
    const x = gx + (xi - 1) * binW, y = gy + (yi - 1) * binH;
    if (kk in startQty) {
      const q = qty[kk], q0 = startQty[kk] || 1;
      ctx.globalAlpha = q <= 0 ? 1 : 0.35 + 0.65 * Math.min(1, q / q0);
      ctx.fillStyle = q <= 0 ? '#3a2a2a' : skuColor(sku[kk]);
    } else { ctx.globalAlpha = 1; ctx.fillStyle = '#161b22'; }
    ctx.fillRect(x + 0.5, y + 0.5, Math.max(1, binW - 1), Math.max(1, binH - 1));
  }
  ctx.globalAlpha = 1;

  // picker paths (Manhattan arrows) + dot
  for (const { pid, st } of pickers) {
    const col = PICKER_COLS[pid % PICKER_COLS.length];
    for (let k = 0; k < st.path.length - 1; k++) {
      const a = st.path[k], b = st.path[k + 1];
      const last = k === st.path.length - 2;
      drawArrow(ctx, cx(a[0]), cy(a[1]), cx(b[0]), cy(b[1]), col, last ? 0.95 : 0.4, last);
    }
    const px = cx(st.pos[0]), py = cy(st.pos[1]);
    ctx.beginPath(); ctx.arc(px, py, Math.max(3, Math.min(7, binW / 3)), 0, 7);
    ctx.fillStyle = col; ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.stroke();
  }
}

// Manhattan arrow from (x0,y0) to (x1,y1): horizontal leg then vertical leg, arrowhead at end.
// The head points along whichever leg arrives at (x1,y1): vertical if y differs, else horizontal.
function drawArrow(ctx, x0, y0, x1, y1, color, alpha, head) {
  ctx.save(); ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y0); ctx.lineTo(x1, y1); ctx.stroke();
  if (head) {
    let ang;
    if (Math.abs(y1 - y0) > 0.5) ang = y1 > y0 ? Math.PI / 2 : -Math.PI / 2;   // vertical leg
    else if (Math.abs(x1 - x0) > 0.5) ang = x1 > x0 ? 0 : Math.PI;             // horizontal leg
    else { ctx.restore(); return; }                                            // zero length
    const s = 6;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x1 - s * Math.cos(ang - 0.5), y1 - s * Math.sin(ang - 0.5));
    ctx.moveTo(x1, y1);
    ctx.lineTo(x1 - s * Math.cos(ang + 0.5), y1 - s * Math.sin(ang + 0.5));
    ctx.stroke();
  }
  ctx.restore();
}

function drawCenter(ctx, W, H, text) {
  ctx.fillStyle = '#8b949e'; ctx.font = '12px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText(text, W / 2, H / 2); ctx.textAlign = 'start';
}

// ── click → drill-in ──────────────────────────────────────────────────────────
function onPaneClick(pane, ev) {
  const rect = pane.canvas.getBoundingClientRect();
  const x = ev.clientX - rect.left, y = ev.clientY - rect.top;
  const tiles = pane._tiles;
  if (tiles) {
    const tl = tiles.find(c => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
    if (tl) openAisle(pane, tl.aisle_id);
    return;
  }
  if (!pane.layout) return;
  const cell = pane.layout.cells.find(c => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
  if (cell) openAisle(pane, cell.a.aisle_id);
}

function openAisle(pane, aisle) {
  modal = { run: pane.run, pane, aisle, data: null, scores: pane.scores, sel: null };
  $('aisle-modal-title').textContent = `${pane.run.strategy} · Aisle ${aisle} · batch ${curBatch}`;
  $('bin-detail').innerHTML = '<div class="bin-detail-hint">Click a bin to inspect its status.</div>';
  $('aisle-modal').classList.remove('hidden');
  const jobs = [fetch(`/api/aisle?run=${encodeURIComponent(pane.run.id)}&batch=${curBatch}&aisle=${aisle}`)
    .then(r => r.json()).then(d => { modal.data = d; })];
  if (!pane.scores) jobs.push(fetchScores(pane).then(() => { modal.scores = pane.scores; }));
  jobs.push(fetchSkuScores(pane));            // per-SKU scores for the bin inspector
  Promise.all(jobs).then(renderAisle);
}

function aisleSimTime() {
  const d = modal.data; if (!d || !d.events.length) return 0;
  const mt = d.events[d.events.length - 1].time;
  return curT < REORDER_FRAC ? 0 : ((curT - REORDER_FRAC) / (1 - REORDER_FRAC)) * mt;
}

function renderAisle() {
  const d = modal.data; if (!d) return;
  const cv = $('aisle-canvas'), ctx = cv.getContext('2d');
  const g = d.geom || {};
  let mx = g.bay_x || 1, my = g.bay_y || 1;
  for (const k of Object.keys(d.bins)) { const [, bx, by] = k.split(',').map(Number); mx = Math.max(mx, bx); my = Math.max(my, by); }
  for (const e of d.events) if (e.location) { mx = Math.max(mx, e.location[1]); my = Math.max(my, e.location[2]); }
  const BIN = 18, GAP = 2, PAD = 12;
  cv.width = PAD * 2 + mx * (BIN + GAP); cv.height = PAD * 2 + my * (BIN + GAP);
  ctx.clearRect(0, 0, cv.width, cv.height);

  const t = aisleSimTime();
  const qty = {}, startSku = {}, startQty = {};
  for (const [k, v] of Object.entries(d.bins)) { qty[k] = v.qty; startSku[k] = v.sku; startQty[k] = v.qty; }
  for (const e of d.events) {
    if (e.time > t) break;
    if (e.event_type === 'pick' && e.location) {
      const k = e.location.join(','); if (k in qty) qty[k] = Math.max(0, qty[k] - (e.quantity || 0));
    }
  }
  const lastArrive = {};
  for (const e of d.events) { if (e.time > t) break; if (e.event_type === 'arrive' && e.location) lastArrive[e.picker_id] = e.location.join(','); }
  const active = new Set(Object.values(lastArrive));

  const cells = [];
  for (let by = 1; by <= my; by++) for (let bx = 1; bx <= mx; bx++) {
    const k = `${d.aisle_id},${bx},${by}`;
    const x = PAD + (bx - 1) * (BIN + GAP), y = PAD + (by - 1) * (BIN + GAP);
    cells.push({ key: k, bx, by, x, y, w: BIN, h: BIN });
    let col = '#161b22';
    if (k in startSku) {
      const frac = qty[k] / (startQty[k] || 1);
      col = qty[k] <= 0 ? '#3a2a2a' : skuColor(startSku[k]);
      ctx.globalAlpha = qty[k] <= 0 ? 1 : 0.4 + 0.6 * Math.min(1, frac);
    } else ctx.globalAlpha = 1;
    ctx.fillStyle = col; ctx.fillRect(x, y, BIN, BIN);
    ctx.globalAlpha = 1;
    if (active.has(k)) { ctx.strokeStyle = '#00E5FF'; ctx.lineWidth = 2; ctx.strokeRect(x + 1, y + 1, BIN - 2, BIN - 2); }
    if (modal.sel === k) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 2; ctx.strokeRect(x + 1, y + 1, BIN - 2, BIN - 2); }
  }
  // picker path arrows + dots (per picker, within this aisle, up to t)
  const byPicker = {};
  for (const e of d.events) { if (e.time > t) break; if (e.location) (byPicker[e.picker_id] || (byPicker[e.picker_id] = [])).push(e); }
  for (const pid in byPicker) {
    const evs = byPicker[pid], col = PICKER_COLS[pid % PICKER_COLS.length];
    const pts = evs.filter(e => e.location).map(e => [e.location[1], e.location[2]]);
    const C = b => [PAD + (b[0] - 1) * (BIN + GAP) + BIN / 2, PAD + (b[1] - 1) * (BIN + GAP) + BIN / 2];
    for (let k = 0; k < pts.length - 1; k++) {
      const a = C(pts[k]), b = C(pts[k + 1]);
      drawArrow(ctx, a[0], a[1], b[0], b[1], col, k === pts.length - 2 ? 0.95 : 0.35, true);
    }
    if (pts.length) {
      const p = C(pts[pts.length - 1]);
      ctx.beginPath(); ctx.arc(p[0], p[1], 6, 0, 7);
      ctx.fillStyle = col; ctx.fill(); ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.stroke();
    }
  }

  modal._cells = cells;
  modal._state = { qty, startSku, startQty, sku: startSku, active };
  const sc = d.scores;
  const ascore = sc
    ? ` &nbsp;·&nbsp; <b>aisle scores</b> demand ${(+sc.demand_sum).toFixed(1)} · `
      + `lift ${(+sc.lift_sum).toFixed(1)} · pick-load ${(+sc.pick_load_sum).toFixed(0)} · `
      + `${sc.n_skus} SKUs / ${sc.n_bins} bins`
    : ' &nbsp;·&nbsp; <span style="opacity:.6">no aisle scores for this strategy</span>';
  $('aisle-legend').innerHTML =
    'Bins coloured by SKU · opacity = remaining qty · '
    + '<span class="sw" style="background:#3a2a2a"></span>emptied · cyan = active pick · '
    + 'arrows = picker path' + ascore;
  if (modal.sel) showBinDetail(modal.sel);
}

function onAisleCanvasClick(ev) {
  if (!modal._cells) return;
  const cv = $('aisle-canvas'), rect = cv.getBoundingClientRect();
  const sx = cv.width / rect.width, sy = cv.height / rect.height;
  const x = (ev.clientX - rect.left) * sx, y = (ev.clientY - rect.top) * sy;
  const c = modal._cells.find(c => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
  if (!c) return;
  modal.sel = c.key; renderAisle();        // re-render to draw selection outline
}

function showBinDetail(key) {
  const s = modal._state; if (!s) return;
  const [aid, bx, by] = key.split(',').map(Number);
  const occupied = key in s.startSku;
  const sku = s.startSku[key];
  const qnow = s.qty[key] || 0, q0 = s.startQty[key] || 0;
  const layout = modal.scores && modal.scores.layout ? modal.scores.layout[key] : null;
  const mapPref = modal.scores && modal.scores.map_pref ? modal.scores.map_pref[key] : null;
  const ss = (modal.pane && modal.pane.skuScores && occupied) ? modal.pane.skuScores[String(sku)] : null;
  let status = 'empty bin', cls = 'empty';
  if (s.active.has(key)) { status = 'active pick'; cls = 'active'; }
  else if (occupied && qnow <= 0) { status = 'emptied this batch'; cls = 'emptied'; }
  else if (occupied) { status = 'stocked'; cls = ''; }
  const row = (k, v) => `<dt>${k}</dt><dd>${v}</dd>`;
  const num = (v, d = 1) => (v == null ? '—' : (+v).toFixed(d));
  let html =
    `<h4>${occupied ? `<span class="sw" style="background:${skuColor(sku)}"></span>` : ''}Bin A${aid} · ${bx},${by}</h4>`
    + `<dl>`
    + row('SKU', occupied ? '#' + sku : '—')
    + row('qty now', qnow) + row('qty @ start', q0) + row('picked', Math.max(0, q0 - qnow))
    + (layout != null ? row('layout score', num(layout)) : '')
    + (mapPref != null ? row('map pref', num(mapPref)) : '')
    + `</dl>`;
  if (ss) {
    html += `<h4>SKU #${sku} scores</h4><dl>`
      + (ss.map_target != null ? row('map target', num(ss.map_target)) : '')
      + row('labor cost', num(ss.labor_cost, 2))
      + row('exp. popularity', num(ss.expected_popularity, 3))
      + row('exp. labor', num(ss.expected_labor, 2))
      + row('equilibrium', ss.equilibrium_qty ?? '—')
      + row('reorder pt', ss.reorder_point ?? '—')
      + row('lead time', num(ss.lead_time_mean, 2))
      + `</dl>`;
  }
  html += `<div class="status ${cls}">${status}</div>`;
  $('bin-detail').innerHTML = html;
}
