// main.js — the shell: chrome, data resolution, and the drill map.
//
// Views self-register on import. The shell resolves each view's `needs` into one shared `data`
// object (shared object IDENTITY, so drilling refetches nothing), routes state changes to the
// views that `watch` the changed keys, and owns the drill map overview -> aisle -> bin. No view
// knows about another view.

import * as api from './core/api.js';
import { emit, get, subscribe } from './core/state.js';
import { available, byId } from './core/registry.js';
import { makePalette } from './core/palette.js';
import { pointer } from './core/canvas.js';

import './views/overview.js';
import './views/aisle.js';
import './views/bin.js';
import './views/converge.js';
import './views/popular.js';
import './views/diff.js';
import './views/tasks.js';

const $ = (id) => document.getElementById(id);
const canvas = $('canvas');
const tabsEl = $('tabs');
const statusEl = $('status');
const noticeEl = $('notice');

let RUNS = [];
let pendingHash = null;   // hash selection waiting for the batch track to load
let AXES = {};
let AXIS_ORDER = [];
let AXIS_LABELS = {};
const filters = {};
let capabilities = [];
let palette = makePalette([], null);
let data = {};
let incomparable = false;
let mounted = null;

// ── boot ───────────────────────────────────────────────────────────────────────

async function boot() {
  const idx = await api.runs();
  RUNS = idx.runs;
  AXES = idx.axes;
  AXIS_ORDER = idx.axis_order;
  AXIS_LABELS = idx.axis_labels;
  $('base').textContent = idx.base;
  $('schema').textContent = idx.schema_short ? `tree ${idx.schema_short}` : '';
  if (!RUNS.length) {
    notice('No arms found. Pass the RUN ROOT — the directory holding run_layout.json.', 'warn');
    return;
  }
  buildFilters();
  const want = readHash();
  const first = RUNS.find((r) => r.id === want.run) || RUNS[0];
  emit({
    run: first.id,
    compareRun: (RUNS.find((r) => r.id === want.compareRun)
      || RUNS.find((r) => r.id !== first.id) || {}).id || null,
    ...(want.view ? { view: want.view } : {}),
  });
  // batch/aisle/bin cannot be applied here: the batch track has not loaded yet, so the run's
  // own batch list is unknown. loadBatchTrack() consumes this once and clears it.
  pendingHash = want;
}

/** Cascading selectors built from the axes the run tree actually has — never hardcoded. */
function buildFilters() {
  const host = $('filters');
  host.innerHTML = '';
  for (const axis of AXIS_ORDER) {
    const values = AXES[axis] || [];
    if (!values.length) continue;             // e.g. `channel` on a store-only run: HIDE it
    const wrap = document.createElement('label');
    wrap.className = 'filter';
    wrap.innerHTML = `<span>${AXIS_LABELS[axis] || axis}</span>`;
    const sel = document.createElement('select');
    sel.innerHTML = '<option value="">any</option>'
      + values.map((v) => `<option value="${v}">${v}</option>`).join('');
    sel.onchange = () => { filters[axis] = sel.value; refreshRunPickers(); };
    wrap.appendChild(sel);
    host.appendChild(wrap);
  }
  refreshRunPickers();
}

function matching() {
  return RUNS.filter((r) => AXIS_ORDER.every(
    (a) => !filters[a] || String(r[a] ?? '') === filters[a]));
}

function refreshRunPickers() {
  const opts = matching();
  for (const [id, key] of [['run-base', 'run'], ['run-compare', 'compareRun']]) {
    const el = $(id);
    const cur = get()[key];
    el.innerHTML = (key === 'compareRun' ? '<option value="">none</option>' : '')
      + opts.map((r) => `<option value="${r.id}">${r.strategy} · ${r.cell} · ${r.config}`
        + `${r.channel ? ` · ${r.channel}` : ''}${r.has_cache ? '' : '  (no cache)'}</option>`)
        .join('');
    if (opts.some((r) => r.id === cur)) el.value = cur;
    el.onchange = () => emit({ [key]: el.value || null });
  }
}

// ── data resolution ────────────────────────────────────────────────────────────
//
// Each `needs` name maps to a loader. api.js caches run-scoped results forever and batch-scoped
// results until the batch changes, so this runs on every render but fetches almost never.

const LOADERS = {
  meta: (s) => api.meta(s.run),
  geometry: (s) => api.geometry(s.run),
  batches: (s) => api.batches(s.run),
  rollup: (s) => api.aisleRollup(s.run, s.batch),
  rollupCompare: (s) => (s.compareRun ? api.aisleRollup(s.compareRun, s.batch) : null),
  state: (s) => api.state(s.run, s.batch),
  aisleState: (s) => (s.aisle === null ? null : api.aisle(s.run, s.batch, s.aisle)),
  aisleStateCompare: (s) => (s.aisle === null || !s.compareRun
    ? null : api.aisle(s.compareRun, s.batch, s.aisle)),
  tasks: (s) => api.tasks(s.run, s.batch),
  tasksCompare: (s) => (s.compareRun ? api.tasks(s.compareRun, s.batch) : null),
  topSkus: (s) => api.topSkus(s.run, 200),
  skuSeries: (s, d) => {
    const skus = (d.topSkus?.skus || []).slice(0, s.topN).map((x) => x.sku);
    return skus.length ? api.skuSeries(s.run, skus) : { series: {} };
  },
  skuScores: (s, d) => {
    const sku = d.aisleState?.bins?.[binKey(s)]?.sku;
    return sku ? api.skuScores(s.run, [sku]) : {};
  },
  binScores: (s) => (s.aisle === null ? null : api.scores(s.run, [s.aisle])),
  binHistory: (s) => (s.bin ? api.binHistory(s.run, s.bin.aisle, s.bin.bayX, s.bin.bayY) : null),
  // The colour authority is the COMPARISON run, applied to both panes — that is what makes the
  // two directly comparable rather than each internally consistent and mutually meaningless.
  finalHome: (s) => api.finalHome(s.compareRun || s.run),
  palette: () => null,                       // built below, from geometry + finalHome
};

function binKey(s) {
  return s.bin ? `${s.bin.aisle},${s.bin.bayX},${s.bin.bayY}` : '';
}

async function resolve(view, s) {
  const names = new Set(['meta', 'geometry', 'batches', 'finalHome', ...view.needs]);
  // topSkus before skuSeries, aisleState before skuScores: a couple of loaders read `data`.
  const ordered = [...names].sort((a, b) => order(a) - order(b));
  for (const name of ordered) {
    const fn = LOADERS[name];
    if (!fn) continue;
    try {
      data[name] = await fn(s, data);
    } catch (err) {
      data[name] = null;
      notice(`${name}: ${err.message}`, 'warn');
    }
  }
  palette = makePalette(data.geometry?.aisles || [], data.finalHome?.homes || null);
  const base = RUNS.find((r) => r.id === s.run);
  const cmp = RUNS.find((r) => r.id === s.compareRun);
  incomparable = !!(base && cmp && base.warehouse_fingerprint && cmp.warehouse_fingerprint
    && base.warehouse_fingerprint !== cmp.warehouse_fingerprint);
}

const ORDER = ['meta', 'geometry', 'batches', 'finalHome', 'topSkus', 'aisleState'];
const order = (n) => (ORDER.indexOf(n) < 0 ? ORDER.length : ORDER.indexOf(n));

// ── render loop ────────────────────────────────────────────────────────────────

function ctxFor(s) {
  return { canvas, data, sel: s, palette, emit, incomparable };
}

async function rerender(changed) {
  const s = get();
  if (!s.run) return;
  if (changed.includes('run') || changed.includes('compareRun')) {
    api.invalidateAll();
    data = {};
    capabilities = (await api.capabilities(s.run)).capabilities;
    // Re-sync the selects: they are built before the first emit (and before a hash selection
    // lands), so without this they show a different arm than the one actually being drawn.
    refreshRunPickers();
    buildTabs();
    await loadBatchTrack();
  }
  if (changed.includes('batch')) api.invalidateBatch();

  const view = byId(s.view) || available(capabilities)[0];
  if (!view) return;
  if (mounted !== view.id) { mounted = view.id; view.mount?.(canvas, ctxFor(s)); }

  // Time-only changes never refetch: `t` is resolved client-side from the loaded event list.
  const onlyTime = changed.length && changed.every((k) => k === 't');
  const mine = api.bumpToken();
  if (!onlyTime) await resolve(view, s);
  // Drop a stale render: `resolve` awaits, so a newer state change can land mid-flight and the
  // two panes would otherwise settle on different batches. (A `get() !== s` identity check does
  // NOT work here — state is one object mutated in place, so it is always identical.)
  if (api.currentToken() !== mine) return;
  view.render(ctxFor(get()));
  updateStatus();
  writeHash();
}

// ── deep linking ───────────────────────────────────────────────────────────────
// The URL carries the selection so a view can be shared, reloaded, or screenshotted.

function writeHash() {
  const s = get();
  const p = new URLSearchParams();
  if (s.run) p.set('run', s.run);
  if (s.compareRun) p.set('cmp', s.compareRun);
  p.set('view', s.view);
  p.set('batch', String(s.batch));
  if (s.aisle !== null) p.set('aisle', String(s.aisle));
  if (s.bin) p.set('bin', `${s.bin.bayX},${s.bin.bayY}`);
  const next = `#${p.toString()}`;
  if (window.location.hash !== next) window.history.replaceState(null, '', next);
}

function readHash() {
  const p = new URLSearchParams(window.location.hash.slice(1));
  const out = {};
  if (p.get('run')) out.run = p.get('run');
  if (p.get('cmp')) out.compareRun = p.get('cmp');
  if (p.get('view')) out.view = p.get('view');
  if (p.get('batch')) out.batch = Number(p.get('batch'));
  if (p.get('aisle')) out.aisle = Number(p.get('aisle'));
  const bin = p.get('bin');
  if (bin && out.aisle !== undefined) {
    const [bx, by] = bin.split(',').map(Number);
    out.bin = { aisle: out.aisle, bayX: bx, bayY: by };
  }
  return out;
}

function buildTabs() {
  const usable = available(capabilities);
  tabsEl.innerHTML = '';
  for (const v of usable) {
    const b = document.createElement('button');
    b.textContent = v.title;
    b.className = v.id === get().view ? 'tab active' : 'tab';
    b.onclick = () => emit({ view: v.id });
    tabsEl.appendChild(b);
  }
  if (!usable.some((v) => v.id === get().view) && usable.length) emit({ view: usable[0].id });
  const hidden = ['aisle_metrics', 'reorder_queue'].filter((c) => !capabilities.includes(c));
  notice(hidden.length
    ? `not recorded by this arm: ${hidden.join(', ')} — those panels are hidden, not zeroed`
    : '', 'info');
}

async function loadBatchTrack() {
  const s = get();
  const meta = await api.meta(s.run);
  data.meta = meta;
  const batches = meta.batches || [];
  const slider = $('batch');
  slider.min = 0;
  slider.max = Math.max(0, batches.length - 1);
  slider.value = 0;
  slider.oninput = () => emit({ batch: batches[Number(slider.value)] ?? 0, t: null });

  // A hash may name a batch this arm never recorded (batches have holes — a batch with no
  // tasks writes no batch_stats row), so the arm's own list is the authority.
  const want = pendingHash || {};
  pendingHash = null;
  const batch = batches.includes(want.batch) ? want.batch : (batches[0] ?? 0);
  slider.value = Math.max(0, batches.indexOf(batch));
  emit({
    batch,
    ...(want.aisle !== undefined ? { aisle: want.aisle } : {}),
    ...(want.bin ? { bin: want.bin } : {}),
  });
}

function updateStatus() {
  const s = get();
  const kfs = data.meta?.keyframes || [];
  // A run carrying the bin-mutation log is exactly reconstructible at EVERY batch; only the
  // pre-log archive is limited to its keyframes.
  const logged = (data.meta?.capabilities || []).includes('bin_log');
  const exact = logged || kfs.includes(s.batch);
  statusEl.innerHTML = `batch <b>${s.batch}</b>`
    + `<span class="${exact ? 'ok' : 'warn'}">${exact ? 'exact' : 'between keyframes'}</span>`
    + (s.aisle !== null ? ` · aisle <b>${s.aisle}</b>` : '')
    + (s.bin ? ` · bin <b>${s.bin.bayX},${s.bin.bayY}</b>` : '')
    + (data.meta?.schema_id ? ` · sim schema <b>${data.meta.schema_id}</b>` : '');
}

function notice(msg, kind = 'info') {
  noticeEl.textContent = msg || '';
  noticeEl.className = msg ? `notice ${kind}` : 'notice';
}

// ── the drill map lives HERE, not in any view ──────────────────────────────────

canvas.addEventListener('click', (ev) => {
  const view = byId(get().view);
  if (!view) return;
  const p = pointer(canvas, ev);
  const target = view.hit(p.x, p.y, ctxFor(get()));
  if (!target) return;
  if (target.kind === 'aisle') emit({ aisle: target.aisle, bin: null });
  if (target.kind === 'bin') emit({ bin: { aisle: get().aisle, ...target } });
});

window.addEventListener('keydown', (ev) => {
  if (ev.target.tagName === 'SELECT' || ev.target.tagName === 'INPUT') return;
  const view = byId(get().view);
  if (view?.key?.(ev, ctxFor(get()))) { ev.preventDefault(); rerender([]); return; }
  const batches = data.meta?.batches || [];
  const i = batches.indexOf(get().batch);
  if (ev.key === 'ArrowRight' && i < batches.length - 1) emit({ batch: batches[i + 1], t: null });
  if (ev.key === 'ArrowLeft' && i > 0) emit({ batch: batches[i - 1], t: null });
});

$('time').oninput = (e) => {
  const max = data.aisleState?.events?.slice(-1)[0]?.time || 0;
  emit({ t: max ? (Number(e.target.value) / 100) * max : null });
};
$('colorMode').onchange = (e) => emit({ colorMode: e.target.value });

window.addEventListener('resize', () => rerender([]));
subscribe((changed) => { rerender(changed); });

boot().catch((err) => notice(`failed to load: ${err.message}`, 'warn'));
