// api.js — every fetch, with three cache tiers.
//
// The tiers are the whole reason drilling overview -> aisle -> bin costs no network:
//
//   RUN-scoped    geometry, scores, capabilities, final_home, batches, top_skus
//                 Fetched once per arm. Scrubbing batch or time never invalidates them.
//   BATCH-scoped  state, events, aisle_rollup, tasks, reorder_queue
//                 Dropped when the batch changes. Nothing else touches them.
//   NOT FETCHED   anything keyed on `t`. Time is resolved client-side by replaying the event
//                 list already loaded for the batch — the one thing the previous viewer got
//                 right, and worth keeping.
//
// A monotonic token guards against stale responses: two panes stepping batches quickly would
// otherwise race and leave one pane drawing a different batch than the other.

const runCache = new Map();     // `${run}|${verb}|${args}` -> Promise
const batchCache = new Map();   // same, cleared on batch change
let token = 0;

export function bumpToken() { return ++token; }
export function currentToken() { return token; }

export function invalidateBatch() { batchCache.clear(); }
export function invalidateAll() { runCache.clear(); batchCache.clear(); }

function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue;
    p.set(k, Array.isArray(v) ? v.join(',') : String(v));
  }
  return p.toString();
}

async function raw(path, params) {
  const res = await fetch(`/api/${path}?${qs(params)}`);
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json()).error || detail; } catch { /* non-JSON error page */ }
    // 501 is the deliberate one: the file's schema has no vetted reader, and the message names
    // the differing tables. Surfacing it verbatim beats a generic "failed to load".
    throw new Error(detail);
  }
  return res.json();
}

function cached(store, key, fn) {
  if (!store.has(key)) store.set(key, fn().catch((err) => { store.delete(key); throw err; }));
  return store.get(key);
}

const runScoped = (run, verb, params = {}) =>
  cached(runCache, `${run}|${verb}|${qs(params)}`, () => raw(verb, { run, ...params }));

const batchScoped = (run, verb, params = {}) =>
  cached(batchCache, `${run}|${verb}|${qs(params)}`, () => raw(verb, { run, ...params }));

// ── navigation (not run-scoped: there is no run yet) ────────────────────────────
export const runs = () => cached(runCache, '::runs', () => raw('runs', {}));

// ── run-scoped ─────────────────────────────────────────────────────────────────
export const capabilities = (run) => runScoped(run, 'capabilities');
export const meta = (run) => runScoped(run, 'meta');
export const geometry = (run) => runScoped(run, 'geometry');
export const batches = (run) => runScoped(run, 'batches');
export const finalHome = (run) => runScoped(run, 'final_home');
export const topSkus = (run, n) => runScoped(run, 'top_skus', { n });
export const skuSeries = (run, skus) => runScoped(run, 'sku_series', { skus });
export const skuScores = (run, skus) => runScoped(run, 'sku_scores', { skus });
export const binHistory = (run, aisle, bayX, bayY) =>
  runScoped(run, 'bin_history', { aisle, bayX, bayY });
export const aisleBins = (run, aisle) => runScoped(run, 'aisle_bins', { aisle });
// Scores are per-aisle-scoped on purpose: unscoped this is 396,500 rows.
export const scores = (run, aisles) => runScoped(run, 'scores', { aisles });

// ── batch-scoped ───────────────────────────────────────────────────────────────
export const state = (run, batch, aisles) => batchScoped(run, 'state', { batch, aisles });
export const aisle = (run, batch, a) => batchScoped(run, 'aisle', { batch, aisle: a });
export const events = (run, batch, a) => batchScoped(run, 'events', { batch, aisle: a });
export const aisleRollup = (run, batch) => batchScoped(run, 'aisle_rollup', { batch });
export const tasks = (run, batch) => batchScoped(run, 'tasks', { batch });
export const reorderQueue = (run, batch) => batchScoped(run, 'reorder_queue', { batch });
