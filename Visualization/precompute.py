"""precompute.py — build the derived viewer sidecar for one or more arms.

    python -m Visualization.precompute "<run_root>" --cell k1_off_rr --config store \\
           --channel store --arms uni_fifo_norsl,opt_rank_labor_norsl

Everything it writes is derived from the sim + keyframe DBs; deleting `_viz/` costs time, never
data. Filters are not a convenience — a full sweep is up to 272 arms, so the default is "the arms
you name", never "everything".

Cost per arm is dominated by two sequential passes:

  * `picks` (~3.1 M rows) -> `sku_rank`, `sku_series`, per-aisle pick counts, per-bin depletion
  * the bin-mutation LOG (~2.2 M rows) -> `bin_span`, occupancy, `final_home`

Both are single ordered scans over an existing index. The log pass reads `bin_placement` and
`bin_eviction` in `(run_id, batch_id, seq)` order — their PK — and the picks pass takes no
`ORDER BY` at all, because neither has an index on `aisle_id` and asking for one would add a
multi-million-row temp sort for nothing.

Two span sources, and the sidecar records which one it used
-----------------------------------------------------------
`bin_placement + bin_eviction + picks` is the complete bin-mutation record, so folding it gives
the simulation's own bin state at EVERY batch (`Tests/integration/test_bin_log_replay.py` proves
this against a live sim). Arms written before the log existed — every arm of the current archive —
fall back to `_keyframe_pass`, whose spans are exact only on the keyframe grid, which is the old
contract (`RECONSTRUCTION.md` §1). `cache_meta.span_source` says which, and the reader will only
report `exact` off the keyframe grid for the first.

Must not import flask: this runs under the bare repo interpreter.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

if __package__ in (None, ''):                     # direct-run bootstrap; `-m` needs nothing
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Schema import compat as _compat
from Visualization.cache_schema import (
    CACHE_VERSION, SPAN_SOURCE_KEYFRAME, SPAN_SOURCE_LOG,
    cache_freshness, init_cache_db, source_stamps,
)
from Visualization.db_reader import discover_runs
from Visualization.readers.base import _ro
from Visualization.readers.fingerprint import resolve_schema_id

_BATCH = 50_000                                   # executemany chunk

# What this module reads out of a sim DB, at the granularity the compatibility gate checks.
# Precompute deliberately BYPASSES the vetted reader (see the builder docstrings: it re-derives
# spans from the raw log for speed) — the bypass is fine, being INVISIBLE to CI was not: this was
# the largest raw reader in the repo with no declaration, so a schema change could not know it
# was a consumer.  `bin_placement`/`bin_eviction`/`bin_keyframe` are CONDITIONAL (only some
# vetted vintages carry them) and are correctly NOT declared here — every read of those is
# already behind a rows-probe (`_has_bin_log`) or keyframe fallback, the negotiation pattern.
REQUIRES = _compat.Requires(
    family='sim_db',
    label='Visualization precompute (raw sidecar builder)',
    tables={
        'picks': ('run_id', 'batch_id', 'aisle_id', 'bayX', 'bayY', 'sku', 'quantity'),
        'batch_stats': ('run_id', 'batch_id'),
        'task_stats': ('run_id', 'batch_id', 'aisle_id', 'duration'),
        'simulation_runs': ('run_id', 'keyframe_interval'),
    })


# ── staleness ────────────────────────────────────────────────────────────────────

def _source_stamps(run) -> dict:
    """This arm's source stamps, via the single implementation in cache_schema."""
    return source_stamps(run.sim_db, run.keyframe_db, run.warehouse_db)


def cache_state(run) -> str:
    """'fresh' | 'stale' | 'absent' | 'partial' for this arm's sidecar.

    Same function the READ path uses (`SqliteSimReader.cache_status`), so the builder and the
    viewer can never disagree about whether a cache is trustworthy.
    """
    return cache_freshness(run.viz_cache, run.sim_db, run.keyframe_db, run.warehouse_db)


# ── the build: spans from the bin-mutation log ───────────────────────────────────

def log_present(sim_con, run_id) -> bool:
    """Does this run carry a bin-mutation log?

    PK-served (`LIMIT 1` on `(run_id, batch_id, seq)`), and tolerant of a DB written before the
    table existed — which is every arm in the archive, so the OperationalError is the NORMAL
    answer here, not an error condition.
    """
    try:
        return sim_con.execute('SELECT 1 FROM bin_placement WHERE run_id=? LIMIT 1',
                               (run_id,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def _bin_spans(events, picks, last_batch):
    """Fold ONE bin's PLACE/EVICT/PICK events into its exact occupancy spans.

    `events` are `(batch, kind, seq, sku, qty)` sorted ascending, `kind` 0 = EVICT, 1 = PLACE —
    so the sort itself imposes the runner's within-batch order, EVICT -> PLACE -> PICK: the
    reloader runs first, then `check_reorders()`, and only then the pick simulation.
    `picks` is `{batch: units}` for this bin, or None.

    A span therefore OPENS at the batch of its PLACE (the bin already holds the unit at that
    batch's START, which is the frame every keyframe and every `state_at` is defined on) and
    CLOSES at whichever comes first:

      * the batch before the next PLACE/EVICT — a span that opens and closes inside one batch is
        never visible at start-of-batch resolution and is dropped (`t_to < t_from`);
      * the batch whose picks drain it to zero.

    Returns `[(t_from, t_to, sku, qty_at_from, applied)]`, where `applied` is the
    `[(batch, units)]` actually consumed inside the span — the caller needs it to keep the
    per-aisle qty rollup exact without re-reading `picks`.
    """
    out = []
    pick_batches = sorted(picks) if picks else []
    n_picks = len(pick_batches)
    pi = 0
    open_from, open_sku, qty0, qty = -1, -1, 0, 0
    applied: list = []

    for ev_batch, kind, _seq, ev_sku, ev_qty in events:
        if ev_batch > last_batch:                 # sorted: nothing past the run's last batch counts
            break
        if open_from >= 0:
            # Picks strictly BEFORE this event's batch: the event happens at the batch start,
            # ahead of that batch's own picks.
            while pi < n_picks and pick_batches[pi] < ev_batch:
                b = pick_batches[pi]
                pi += 1
                if b < open_from:                 # depletion of a previous occupancy of this bin
                    continue
                units = min(picks[b], qty)        # clamped: the aisle total must never go negative
                qty -= units
                applied.append((b, units))
                if qty <= 0:
                    out.append((open_from, b, open_sku, qty0, applied))
                    open_from, applied = -1, []
                    break
        while pi < n_picks and pick_batches[pi] < ev_batch:
            pi += 1                               # keep the pointer monotone across a closed span
        if open_from >= 0:
            if ev_batch - 1 >= open_from:
                out.append((open_from, ev_batch - 1, open_sku, qty0, applied))
            open_from, applied = -1, []
        if kind:                                  # PLACE opens a new span at this batch
            open_from, open_sku, qty0, qty, applied = ev_batch, ev_sku, ev_qty, ev_qty, []

    if open_from >= 0:                            # the tail: drain to the end of the run
        closed_at = last_batch
        while pi < n_picks:
            b = pick_batches[pi]
            pi += 1
            if b > last_batch:
                break
            if b < open_from:
                continue
            units = min(picks[b], qty)
            qty -= units
            applied.append((b, units))
            if qty <= 0:
                closed_at = b
                break
        out.append((open_from, closed_at, open_sku, qty0, applied))
    return out


def _log_pass(sim_con, run_id, batches, bin_picks):
    """One ordered scan each of `bin_placement` + `bin_eviction` -> spans exact at EVERY batch.

    Returns `(spans, homes, final_batch, deltas)`.  `deltas` holds per-`(aisle, batch)` STEPS for
    the four rollup metrics rather than their values: a span contributes `+1` at `t_from` and
    `-1` at `t_to + 1`, so the rollup prefix-sums in O(spans + picks) instead of expanding
    bins x batches — 2.2M steps against 39.65M cells on a production arm.
    """
    last_batch = max(batches) if batches else 0
    events: dict[tuple, list] = {}

    for r in sim_con.execute(
            'SELECT batch_id, seq, aisle_id, bayX, bayY, sku, qty FROM bin_placement '
            'WHERE run_id=? ORDER BY batch_id, seq', (run_id,)):
        events.setdefault((int(r[2]), int(r[3]), int(r[4])), []).append(
            (int(r[0]), 1, int(r[1]), int(r[5]), int(r[6])))
    try:
        for r in sim_con.execute(
                'SELECT batch_id, seq, aisle_id, bayX, bayY FROM bin_eviction '
                'WHERE run_id=? ORDER BY batch_id, seq', (run_id,)):
            events.setdefault((int(r[2]), int(r[3]), int(r[4])), []).append(
                (int(r[0]), 0, int(r[1]), -1, 0))
    except sqlite3.OperationalError:              # placements without the eviction table
        pass

    spans: list[tuple] = []
    occ_d: dict[tuple, int] = {}
    qty_d: dict[tuple, int] = {}
    nsk_d: dict[tuple, int] = {}
    hom_d: dict[tuple, int] = {}
    by_aisle_sku: dict[tuple, list] = {}          # (aisle, sku) -> intervals, for DISTINCT skus
    homes: dict[int, dict] = {}
    home_aisles: dict[int, set] = {}

    for key, evs in events.items():
        evs.sort()
        aisle, bx, by = key
        for t_from, t_to, sku, qty0, applied in _bin_spans(evs, bin_picks.get(key), last_batch):
            if t_to < t_from:                     # opened and closed inside one batch
                continue
            spans.append((t_from, t_to, sku, qty0, aisle, bx, by))
            by_aisle_sku.setdefault((aisle, sku), []).append((t_from, t_to))
            occ_d[(aisle, t_from)] = occ_d.get((aisle, t_from), 0) + 1
            occ_d[(aisle, t_to + 1)] = occ_d.get((aisle, t_to + 1), 0) - 1
            # qty: the placed units enter at t_from, each batch's picks leave at the NEXT batch
            # (they happen after that batch's frame), and whatever is left is removed at close.
            qty_d[(aisle, t_from)] = qty_d.get((aisle, t_from), 0) + qty0
            picked = 0
            for b, units in applied:
                qty_d[(aisle, b + 1)] = qty_d.get((aisle, b + 1), 0) - units
                picked += units
            qty_d[(aisle, t_to + 1)] = qty_d.get((aisle, t_to + 1), 0) + picked - qty0

            if t_from <= last_batch <= t_to:
                qty_end = qty0 - sum(u for b, u in applied if b < last_batch)
                home_aisles.setdefault(sku, set()).add(aisle)
                cand = {'aisle_id': aisle, 'bayX': bx, 'bayY': by, 'qty': qty_end, 'n_homes': 1}
                prev = homes.get(sku)
                if prev is None:
                    homes[sku] = cand
                else:
                    prev['n_homes'] += 1
                    cand['n_homes'] = prev['n_homes']
                    if (cand['qty'], (-aisle, -bx, -by)) > (prev['qty'],
                                                            (-prev['aisle_id'], -prev['bayX'],
                                                             -prev['bayY'])):
                        homes[sku] = cand
    events.clear()                                # ~2.2M tuples; freed before the second pass

    # n_skus counts DISTINCT skus, so a sku's intervals across several bins of one aisle must be
    # merged first — two bins holding it at once would otherwise count twice.
    for (aisle, sku), ivs in by_aisle_sku.items():
        ivs.sort()
        cur_from, cur_to = ivs[0]
        for f, t in ivs[1:]:
            if f <= cur_to + 1:
                cur_to = max(cur_to, t)
                continue
            nsk_d[(aisle, cur_from)] = nsk_d.get((aisle, cur_from), 0) + 1
            nsk_d[(aisle, cur_to + 1)] = nsk_d.get((aisle, cur_to + 1), 0) - 1
            cur_from, cur_to = f, t
        nsk_d[(aisle, cur_from)] = nsk_d.get((aisle, cur_from), 0) + 1
        nsk_d[(aisle, cur_to + 1)] = nsk_d.get((aisle, cur_to + 1), 0) - 1
    by_aisle_sku.clear()

    # home_match needs the final homes, so it is the one metric that cannot be accumulated in the
    # walk above.  Still O(spans): the same +1/-1 at the span's edges.
    for t_from, t_to, sku, _qty, aisle, _bx, _by in spans:
        if aisle in home_aisles.get(sku, ()):
            hom_d[(aisle, t_from)] = hom_d.get((aisle, t_from), 0) + 1
            hom_d[(aisle, t_to + 1)] = hom_d.get((aisle, t_to + 1), 0) - 1

    for sku, home in homes.items():
        home['home_aisles'] = ','.join(str(a) for a in sorted(home_aisles[sku]))
    return (spans, homes, last_batch,
            {'occupied': occ_d, 'qty': qty_d, 'n_skus': nsk_d, 'home_match': hom_d})


# ── the build: spans from the keyframes (pre-log runs) ───────────────────────────

def _keyframe_pass(kf_con, run_id):
    """One ordered scan of `bin_keyframe` -> (spans, per-(batch,aisle) occupancy, final homes).

    The FALLBACK for an arm written before `bin_placement` existed. Reads in `(run_id, batch_id)`
    order so the existing PK/index serves it. Spans close when a bin's SKU changes between
    consecutive keyframes; a bin that disappears from a keyframe has become empty and its span
    closes at the previous one.

    These spans live on the KEYFRAME grid: they say nothing about what happened between two
    snapshots, which is exactly the 59% gap `RECONSTRUCTION.md` §1 measures. `build_one` records
    `span_source=keyframe` so the reader keeps reporting `exact: false` off that grid.
    """
    spans: list[tuple] = []
    open_span: dict[tuple, list] = {}             # bin -> [kf_from, sku, qty_at_from, kf_last]
    seen_this_kf: set = set()
    occupancy: dict[tuple, list] = {}             # (batch, aisle) -> [occupied, qty, {skus}]
    last_kf_state: dict[int, dict] = {}           # sku -> primary-home candidate, final keyframe
    home_aisles: dict[int, set] = {}
    current_kf = None
    final_kf = None

    def close_missing():
        """Any bin not seen in the keyframe just finished is now empty; close its span."""
        for key in [k for k in open_span if k not in seen_this_kf]:
            kf_from, sku, qty, kf_last = open_span.pop(key)
            spans.append((kf_from, kf_last, sku, qty, *key))

    for r in kf_con.execute(
            'SELECT batch_id, aisle_id, bayX, bayY, sku, qty FROM bin_keyframe '
            'WHERE run_id=? ORDER BY batch_id', (run_id,)):
        batch, aisle, bx, by, sku, qty = (int(r[0]), int(r[1]), int(r[2]),
                                          int(r[3]), int(r[4]), int(r[5]))
        if batch != current_kf:
            if current_kf is not None:
                close_missing()
            current_kf, seen_this_kf = batch, set()
            last_kf_state, home_aisles = {}, {}    # only the FINAL keyframe's survive
            final_kf = batch
        if qty <= 0:
            continue
        key = (aisle, bx, by)
        seen_this_kf.add(key)

        cur = open_span.get(key)
        if cur is None:
            open_span[key] = [batch, sku, qty, batch]
        elif cur[1] != sku:
            spans.append((cur[0], cur[3], cur[1], cur[2], *key))
            open_span[key] = [batch, sku, qty, batch]
        else:
            cur[3] = batch

        slot = occupancy.setdefault((batch, aisle), [0, 0, set()])
        slot[0] += 1
        slot[1] += qty
        slot[2].add(sku)

        home_aisles.setdefault(sku, set()).add(aisle)
        cand = {'aisle_id': aisle, 'bayX': bx, 'bayY': by, 'qty': qty, 'n_homes': 1}
        prev = last_kf_state.get(sku)
        if prev is None:
            last_kf_state[sku] = cand
        else:
            prev['n_homes'] += 1
            cand['n_homes'] = prev['n_homes']
            if (cand['qty'], (-aisle, -bx, -by)) > (prev['qty'],
                                                    (-prev['aisle_id'], -prev['bayX'],
                                                     -prev['bayY'])):
                last_kf_state[sku] = cand

    if current_kf is not None:
        close_missing()
    for key, (kf_from, sku, qty, kf_last) in open_span.items():
        spans.append((kf_from, kf_last, sku, qty, *key))

    for sku, home in last_kf_state.items():
        home['home_aisles'] = ','.join(str(a) for a in sorted(home_aisles[sku]))
    return spans, occupancy, last_kf_state, final_kf


def _picks_pass(sim_con, run_id, top_n, want_bin_picks=False):
    """One scan of `picks` -> per-SKU totals, per-(batch,aisle) pick counts, top-N series.

    Replaces the measured 24.4 s `GROUP BY sku` and a second scan for the series. Aggregated in
    Python rather than SQL so one read feeds all of them.

    `want_bin_picks` adds per-`(bin, batch)` depletion, which is the third input the span fold
    needs (PLACE + EVICT tell it what a bin holds; picks tell it when the bin runs dry). It is
    opt-in because it is the one aggregate proportional to the pick count rather than to the SKU
    or aisle count, and the keyframe fallback has no use for it.
    """
    totals: dict[int, list] = {}                  # sku -> [picks, units, first, last]
    per_batch: dict[tuple, list] = {}             # (sku, batch) -> [picks, units]
    aisle_picks: dict[tuple, list] = {}           # (batch, aisle) -> [picks, units]
    bin_picks: dict[tuple, dict] = {}             # (aisle, bayX, bayY) -> {batch: units}
    cur = sim_con.execute(
        'SELECT sku, batch_id, aisle_id, quantity, bayX, bayY FROM picks WHERE run_id=?',
        (run_id,))
    while True:
        rows = cur.fetchmany(100_000)
        if not rows:
            break
        for sku, batch, aisle, qty, bx, by in rows:
            t = totals.get(sku)
            if t is None:
                totals[sku] = [1, qty, batch, batch]
            else:
                t[0] += 1
                t[1] += qty
                if batch < t[2]:
                    t[2] = batch
                if batch > t[3]:
                    t[3] = batch
            pb = per_batch.setdefault((sku, batch), [0, 0])
            pb[0] += 1
            pb[1] += qty
            ap = aisle_picks.setdefault((batch, aisle), [0, 0])
            ap[0] += 1
            ap[1] += qty
            if want_bin_picks:
                d = bin_picks.setdefault((aisle, bx, by), {})
                d[batch] = d.get(batch, 0) + qty

    ranked = sorted(totals.items(), key=lambda kv: (-kv[1][1], kv[0]))
    rank_of = {sku: i + 1 for i, (sku, _) in enumerate(ranked)}
    keep = {sku for sku, _ in ranked[:top_n]}
    series = [(sku, batch, v[0], v[1]) for (sku, batch), v in per_batch.items() if sku in keep]
    return ranked, rank_of, series, aisle_picks, bin_picks


def build_one(run, top_n: int = 500, force: bool = False, verify: bool = False) -> dict:
    """Build (or refresh) one arm's sidecar. Returns a small report dict."""
    started = time.time()
    state = cache_state(run)
    if state == 'fresh' and not force:
        return {'run': run.id, 'status': 'fresh', 'secs': 0.0}

    sim_con = _ro(run.sim_db)
    from_log = log_present(sim_con, run.run_id)
    if not from_log and not run.keyframe_db:
        # With no log, bin_span/final_home/the exact-frame contract all rest on keyframes.
        # Refuse loudly rather than emit a cache whose spatial tables are quietly depletion-only.
        sim_con.close()
        return {'run': run.id, 'status': 'skipped',
                'error': 'no bin-mutation log and no keyframes DB (run written with '
                         '--keyframe-interval 0); the spatial tables cannot be built exactly'}

    os.makedirs(os.path.dirname(run.viz_cache), exist_ok=True)
    # Only the temp file is cleared.  The live sidecar is replaced atomically at the end, so a
    # failing --force rebuild leaves the previous (still valid) cache in place rather than none.
    tmp = run.viz_cache + '.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)

    # The keyframe DB is opened only when it is the span source: a log-backed build never reads
    # it, and an arm with a log but no keyframes is legitimate.
    kf_con = _ro(run.keyframe_db) if (run.keyframe_db and not from_log) else None
    try:
        schema, _source = resolve_schema_id(sim_con, verify=verify)
        # Read geometry directly rather than through run.reader(): the reader is memoised on
        # the RunRef and binding it here would fix its view of the cache to "absent" — the file
        # is created by the os.replace at the end of this function.
        wcon = _ro(run.warehouse_db)
        try:
            geometry = [{'aisle_id': int(r['aisle_id']),
                         'capacity': int(r['bay_x'] or 0) * int(r['bay_y'] or 0)}
                        for r in wcon.execute(
                            'SELECT aisle_id, bay_x, bay_y FROM aisle_layout')]
        finally:
            wcon.close()
        capacity = {int(a['aisle_id']): a['capacity'] for a in geometry}
        kf_interval = sim_con.execute(
            'SELECT keyframe_interval FROM simulation_runs WHERE run_id=?',
            (run.run_id,)).fetchone()['keyframe_interval']
        batches = [int(r[0]) for r in sim_con.execute(
            'SELECT batch_id FROM batch_stats WHERE run_id=? ORDER BY batch_id', (run.run_id,))]
        task_secs = {(int(r[0]), int(r[1])): (float(r[2]), int(r[3])) for r in sim_con.execute(
            'SELECT batch_id, aisle_id, SUM(duration), COUNT(*) FROM task_stats '
            'WHERE run_id=? GROUP BY batch_id, aisle_id', (run.run_id,))}

        # The picks pass runs FIRST on the log path: its per-bin depletion is what tells the
        # span fold when a bin ran dry, so it is an input to `_log_pass`, not a sibling of it.
        ranked, rank_of, series, aisle_picks, bin_picks = _picks_pass(
            sim_con, run.run_id, top_n, want_bin_picks=from_log)
        if from_log:
            spans, homes, final_batch, deltas = _log_pass(
                sim_con, run.run_id, batches, bin_picks)
            occupancy = None
        else:
            spans, occupancy, homes, final_batch = _keyframe_pass(kf_con, run.run_id)
            deltas = None
    finally:
        sim_con.close()
        if kf_con is not None:
            kf_con.close()
    bin_picks = None                              # up to ~2.5M entries; not needed past this point

    # An occupied bin counts as "home" when its SKU's final-frame home aisle SET contains this
    # aisle — not when it sits in one designated bin. See cache_schema.final_home.
    home_aisle_sets = {sku: {int(a) for a in h['home_aisles'].split(',') if a}
                       for sku, h in homes.items()}

    out = init_cache_db(tmp)
    try:
        _executemany(out, 'INSERT OR REPLACE INTO bin_span '
                          '(run_id, aisle_id, bayX, bayY, t_from, t_to, sku, qty_at_from) '
                          'VALUES (?,?,?,?,?,?,?,?)',
                     ((run.run_id, a, bx, by, t_from, t_to, sku, qty)
                      for t_from, t_to, sku, qty, a, bx, by in spans))
        _executemany(out, 'INSERT OR REPLACE INTO sku_rank '
                          '(run_id, sku, rank, picks, units, first_batch, last_batch) '
                          'VALUES (?,?,?,?,?,?,?)',
                     ((run.run_id, sku, rank_of[sku], v[0], v[1], v[2], v[3])
                      for sku, v in ranked))
        _executemany(out, 'INSERT OR REPLACE INTO sku_series '
                          '(run_id, sku, batch_id, picks, units) VALUES (?,?,?,?,?)',
                     ((run.run_id, sku, batch, p, u) for sku, batch, p, u in series))
        _executemany(out, 'INSERT OR REPLACE INTO final_home '
                          '(run_id, sku, aisle_id, bayX, bayY, qty, n_homes, home_aisles, '
                          'batch_id) VALUES (?,?,?,?,?,?,?,?,?)',
                     ((run.run_id, sku, h['aisle_id'], h['bayX'], h['bayY'], h['qty'],
                       h['n_homes'], h['home_aisles'], final_batch) for sku, h in homes.items()))
        rollup = (_rollup_rows_from_log(run, batches, capacity, deltas, aisle_picks, task_secs)
                  if from_log else
                  _rollup_rows(run, batches, capacity, occupancy, aisle_picks, task_secs,
                               home_aisle_sets, spans))
        _executemany(out, 'INSERT OR REPLACE INTO aisle_batch_rollup '
                          '(run_id, batch_id, aisle_id, occupied, capacity, qty, n_skus, picks, '
                          'units_picked, visits, task_secs, home_match) '
                          'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', rollup)

        meta = {
            'cache_version': str(CACHE_VERSION),
            'span_source': SPAN_SOURCE_LOG if from_log else SPAN_SOURCE_KEYFRAME,
            'sim_schema_id': schema,
            'run_id': str(run.run_id),
            'keyframe_interval': str(kf_interval),
            'top_n': str(top_n),
            'final_home_batch': str(final_batch),
            'n_spans': str(len(spans)),
            'built_utc': datetime.now(timezone.utc).isoformat(),
            'built_secs': f'{time.time() - started:.1f}',
            **_source_stamps(run),
        }
        out.executemany('INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?,?)',
                        list(meta.items()))
        out.commit()
    finally:
        out.close()

    # Atomic: a reader never sees a partial build.  Retried because SQLite opens files without
    # FILE_SHARE_DELETE on Windows, so a viewer reading this sidecar makes the swap fail — and
    # discarding a finished build for a transient lock would throw away minutes of work.
    for attempt in range(5):
        try:
            os.replace(tmp, run.viz_cache)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.5)
    return {'run': run.id, 'status': 'built', 'secs': round(time.time() - started, 1),
            'spans': len(spans), 'skus': len(ranked), 'span_source': meta['span_source'],
            'mb': round(os.path.getsize(run.viz_cache) / 1e6, 1)}


def _rollup_rows_from_log(run, batches, capacity, deltas, aisle_picks, task_secs):
    """Per (batch, aisle) rollups, prefix-summed from `_log_pass`'s per-batch steps.

    Exact at EVERY batch — nothing inherits a keyframe any more. The running totals are carried
    across the full batch range rather than across `batches`, because a batch that produced no
    tasks writes no `batch_stats` row (`RECONSTRUCTION.md` §7) and skipping its steps would
    desynchronise every batch after it.
    """
    if not batches:
        return
    wanted, last = set(batches), max(batches)
    occ_d, qty_d = deltas['occupied'], deltas['qty']
    nsk_d, hom_d = deltas['n_skus'], deltas['home_match']
    for aisle, cap in capacity.items():
        occ = qty = nsk = hom = 0
        for batch in range(last + 1):
            occ += occ_d.get((aisle, batch), 0)
            qty += qty_d.get((aisle, batch), 0)
            nsk += nsk_d.get((aisle, batch), 0)
            hom += hom_d.get((aisle, batch), 0)
            if batch not in wanted:
                continue
            picks, units = aisle_picks.get((batch, aisle), (0, 0))
            secs, visits = task_secs.get((batch, aisle), (0.0, 0))
            yield (run.run_id, batch, aisle, occ, cap, qty, nsk,
                   picks, units, visits, secs, hom)


def _rollup_rows(run, batches, capacity, occupancy, aisle_picks, task_secs, home_sets, spans):
    """Per (batch, aisle) rollups on the KEYFRAME grid — the pre-log fallback.

    Occupancy is only known at keyframes, so a non-keyframe batch inherits the nearest keyframe
    at or below it — the same rule `state_at` uses without a log, and it is why `home_match` and
    `occupied` step rather than glide between keyframes.
    """
    kf_batches = sorted({b for b, _a in occupancy})
    if not kf_batches:
        return
    # home_match per (keyframe, aisle): occupied bins whose sku counts this aisle as a home.
    home_match: dict[tuple, int] = {}
    for kf_from, kf_to, sku, _qty, aisle, _bx, _by in spans:
        if aisle in home_sets.get(sku, ()):
            for kf in kf_batches:
                if kf_from <= kf <= kf_to:
                    home_match[(kf, aisle)] = home_match.get((kf, aisle), 0) + 1

    for batch in batches:
        prior = [k for k in kf_batches if k <= batch]
        kf = prior[-1] if prior else kf_batches[0]
        for aisle, cap in capacity.items():
            occ = occupancy.get((kf, aisle))
            picks, units = aisle_picks.get((batch, aisle), (0, 0))
            secs, visits = task_secs.get((batch, aisle), (0.0, 0))
            yield (run.run_id, batch, aisle,
                   occ[0] if occ else 0, cap, occ[1] if occ else 0,
                   len(occ[2]) if occ else 0,
                   picks, units, visits, secs, home_match.get((kf, aisle), 0))


def _executemany(con, sql, rows_iter):
    """Chunked executemany so a 3M-row insert never materialises as one list."""
    chunk = []
    for row in rows_iter:
        chunk.append(row)
        if len(chunk) >= _BATCH:
            con.executemany(sql, chunk)
            chunk.clear()
    if chunk:
        con.executemany(sql, chunk)


# ── CLI ──────────────────────────────────────────────────────────────────────────

def _worker(args):
    """Module-level entry point: ProcessPoolExecutor spawns, so this must be picklable.

    The RunRef is passed IN rather than re-discovered.  Calling discover_runs here would make
    every worker walk the whole run root and open every warehouse.db and sim DB — ~272 walks and
    ~74k redundant opens on the results drive across a full sweep, since each payload is one arm
    and the per-process cache never gets a second hit.  RunRef is a plain dataclass whose
    `_reader` is None at submit time, so it pickles.
    """
    run, top_n, force, verify = args
    try:
        return build_one(run, top_n=top_n, force=force, verify=verify)
    except Exception as exc:                      # noqa: BLE001 - one bad arm must not kill the run
        return {'run': run.id, 'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}


def _select(runs, args):
    def keep(r):
        return ((not args.cell or r.cell == args.cell)
                and (not args.pair or r.pair == args.pair)
                and (not args.config or r.config == args.config)
                and (not args.channel or (r.channel or '') == args.channel)
                and (not args.arms or r.strategy in args.arms))
    return [r for r in runs if keep(r)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='Build the derived viewer cache for selected arms of a run.')
    ap.add_argument('base_dir', nargs='?', default=None,
                    help='run root (the dir holding run_layout.json); '
                         'defaults to $COMPARISON_OUTPUT_DIR')
    ap.add_argument('--cell')
    ap.add_argument('--pair')
    ap.add_argument('--config')
    ap.add_argument('--channel')
    ap.add_argument('--arms', help='comma-separated strategy keys')
    ap.add_argument('--top-n', type=int, default=500,
                    help='SKUs kept in sku_series (all SKUs are always ranked)')
    ap.add_argument('--workers', type=int, default=4,
                    help='parallel arms; the results drive saturates well before the CPU does')
    ap.add_argument('--force', action='store_true', help='rebuild even when fresh')
    ap.add_argument('--verify', action='store_true',
                    help='re-derive the sim schema id and fail on a mismatch')
    ap.add_argument('--list', action='store_true', help='show what would be built, then exit')
    args = ap.parse_args(argv)

    # Same resolution every run-tree CLI uses: an absolute path as-is, a bare name against
    # COMPARISON_OUTPUT_DIR.  Importing sim_config is what loads .env in the first place.
    from Optimization.config.sim_config import _OUTPUT_DIR
    from Optimization.runschema import resolve_base_dir

    raw = args.base_dir
    if raw:
        base = resolve_base_dir(raw.strip().strip('"').strip("'").rstrip('\\/'))
    elif _OUTPUT_DIR:
        base = os.path.abspath(_OUTPUT_DIR)
    else:
        ap.error('no run root given and COMPARISON_OUTPUT_DIR is unset (see .env.example)')
    args.arms = set(args.arms.split(',')) if args.arms else None

    runs = _select(discover_runs(base), args)
    if not runs:
        print(f'No arms matched under {base}', flush=True)
        return 1

    if args.list:
        for r in runs:
            print(f'  {cache_state(r):8} {r.id}')
        print(f'{len(runs)} arm(s).')
        return 0

    todo = [r for r in runs if args.force or cache_state(r) != 'fresh']
    print(f'{len(runs)} arm(s) selected; {len(todo)} to build '
          f'({len(runs) - len(todo)} already fresh).', flush=True)
    if not todo:
        return 0

    payloads = [(r, args.top_n, args.force, args.verify) for r in todo]
    results = []
    if args.workers > 1 and len(todo) > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for res in pool.map(_worker, payloads):
                results.append(res)
                print(f'  {_fmt(res)}', flush=True)
    else:
        for payload in payloads:
            res = _worker(payload)
            results.append(res)
            print(f'  {_fmt(res)}', flush=True)

    failed = [r for r in results if r['status'] in ('error', 'skipped')]
    built = [r for r in results if r['status'] == 'built']
    total_mb = sum(r.get('mb', 0) for r in built)
    print(f'\n{len(built)} built ({total_mb:.0f} MB), {len(failed)} failed/skipped.', flush=True)
    for r in failed:
        print(f'  ! {r["run"]}: {r.get("error", "")}', flush=True)
    return 1 if failed else 0


def _fmt(res: dict) -> str:
    if res['status'] == 'built':
        # The span source is printed because it decides whether the viewer can call a frame
        # exact: `keyframe` means the arm predates the bin-mutation log and never will be.
        return (f'built  {res["run"]}  {res["secs"]}s  {res["mb"]} MB  '
                f'{res["spans"]} spans  {res["skus"]} skus  spans<-{res["span_source"]}')
    return f'{res["status"]:6} {res["run"]}  {res.get("error", "")}'


if __name__ == '__main__':
    raise SystemExit(main())
