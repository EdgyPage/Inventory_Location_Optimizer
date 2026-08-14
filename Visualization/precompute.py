"""precompute.py — build the derived viewer sidecar for one or more arms.

    python -m Visualization.precompute "<run_root>" --cell k1_off_rr --config store \\
           --channel store --arms uni_fifo_norsl,opt_rank_labor_norsl

Everything it writes is derived from the sim + keyframe DBs; deleting `_viz/` costs time, never
data. Filters are not a convenience — a full sweep is up to 272 arms, so the default is "the arms
you name", never "everything".

Cost per arm is dominated by two sequential passes:

  * the keyframes (~3.3 M rows over 20 snapshots) -> `bin_span`, occupancy, `final_home`
  * `picks` (~3.1 M rows) -> `sku_rank`, `sku_series`, per-aisle pick counts

Both are single ordered scans over an existing index. In particular the keyframe pass reads in
`(run_id, batch_id)` order — the PK prefix — and NOT `ORDER BY aisle_id, ...`, which has no index
and would add a multi-million-row temp sort for nothing.

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

from Visualization.cache_schema import (
    CACHE_VERSION, cache_freshness, init_cache_db, source_stamps,
)
from Visualization.db_reader import discover_runs
from Visualization.readers.base import _ro
from Visualization.readers.fingerprint import resolve_schema_id

_BATCH = 50_000                                   # executemany chunk


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


# ── the build ────────────────────────────────────────────────────────────────────

def _keyframe_pass(kf_con, run_id):
    """One ordered scan of `bin_keyframe` -> (spans, per-(batch,aisle) occupancy, final homes).

    Reads in `(run_id, batch_id)` order so the existing PK/index serves it. Spans close when a
    bin's SKU changes between consecutive keyframes; a bin that disappears from a keyframe has
    become empty and its span closes at the previous one.
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


def _picks_pass(sim_con, run_id, top_n):
    """One scan of `picks` -> per-SKU totals, per-(batch,aisle) pick counts, top-N series.

    Replaces the measured 24.4 s `GROUP BY sku` and a second scan for the series. Aggregated in
    Python rather than SQL so one read feeds all three.
    """
    totals: dict[int, list] = {}                  # sku -> [picks, units, first, last]
    per_batch: dict[tuple, list] = {}             # (sku, batch) -> [picks, units]
    aisle_picks: dict[tuple, list] = {}           # (batch, aisle) -> [picks, units]
    cur = sim_con.execute(
        'SELECT sku, batch_id, aisle_id, quantity FROM picks WHERE run_id=?', (run_id,))
    while True:
        rows = cur.fetchmany(100_000)
        if not rows:
            break
        for sku, batch, aisle, qty in rows:
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

    ranked = sorted(totals.items(), key=lambda kv: (-kv[1][1], kv[0]))
    rank_of = {sku: i + 1 for i, (sku, _) in enumerate(ranked)}
    keep = {sku for sku, _ in ranked[:top_n]}
    series = [(sku, batch, v[0], v[1]) for (sku, batch), v in per_batch.items() if sku in keep]
    return ranked, rank_of, series, aisle_picks


def build_one(run, top_n: int = 500, force: bool = False, verify: bool = False) -> dict:
    """Build (or refresh) one arm's sidecar. Returns a small report dict."""
    started = time.time()
    state = cache_state(run)
    if state == 'fresh' and not force:
        return {'run': run.id, 'status': 'fresh', 'secs': 0.0}
    if not run.keyframe_db:
        # bin_span, final_home and the exact-frame contract all rest on keyframes.  Refuse
        # loudly rather than emit a cache whose spatial tables are quietly depletion-only.
        return {'run': run.id, 'status': 'skipped',
                'error': 'no keyframes DB (run written with --keyframe-interval 0); '
                         'the spatial tables cannot be built exactly without one'}

    os.makedirs(os.path.dirname(run.viz_cache), exist_ok=True)
    # Only the temp file is cleared.  The live sidecar is replaced atomically at the end, so a
    # failing --force rebuild leaves the previous (still valid) cache in place rather than none.
    tmp = run.viz_cache + '.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)

    sim_con, kf_con = _ro(run.sim_db), _ro(run.keyframe_db)
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

        spans, occupancy, homes, final_kf = _keyframe_pass(kf_con, run.run_id)
        ranked, rank_of, series, aisle_picks = _picks_pass(sim_con, run.run_id, top_n)
    finally:
        sim_con.close()
        kf_con.close()

    # An occupied bin counts as "home" when its SKU's final-keyframe home aisle SET contains this
    # aisle — not when it sits in one designated bin. See cache_schema.final_home.
    home_aisle_sets = {sku: {int(a) for a in h['home_aisles'].split(',') if a}
                       for sku, h in homes.items()}

    out = init_cache_db(tmp)
    try:
        _executemany(out, 'INSERT OR REPLACE INTO bin_span '
                          '(run_id, aisle_id, bayX, bayY, kf_from, kf_to, sku, qty_at_from) '
                          'VALUES (?,?,?,?,?,?,?,?)',
                     ((run.run_id, a, bx, by, kf_from, kf_to, sku, qty)
                      for kf_from, kf_to, sku, qty, a, bx, by in spans))
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
                       h['n_homes'], h['home_aisles'], final_kf) for sku, h in homes.items()))
        _executemany(out, 'INSERT OR REPLACE INTO aisle_batch_rollup '
                          '(run_id, batch_id, aisle_id, occupied, capacity, qty, n_skus, picks, '
                          'units_picked, visits, task_secs, home_match) '
                          'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     _rollup_rows(run, batches, capacity, occupancy, aisle_picks, task_secs,
                                  home_aisle_sets, spans))

        meta = {
            'cache_version': str(CACHE_VERSION),
            'sim_schema_id': schema,
            'run_id': str(run.run_id),
            'keyframe_interval': str(kf_interval),
            'top_n': str(top_n),
            'final_home_batch': str(final_kf),
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
            'spans': len(spans), 'skus': len(ranked),
            'mb': round(os.path.getsize(run.viz_cache) / 1e6, 1)}


def _rollup_rows(run, batches, capacity, occupancy, aisle_picks, task_secs, home_sets, spans):
    """Per (batch, aisle) rollups.

    Occupancy is only known at keyframes, so a non-keyframe batch inherits the nearest keyframe
    at or below it — the same rule `state_at` uses, and it is why `home_match` and `occupied`
    step rather than glide between keyframes.
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
        return (f'built  {res["run"]}  {res["secs"]}s  {res["mb"]} MB  '
                f'{res["spans"]} spans  {res["skus"]} skus')
    return f'{res["status"]:6} {res["run"]}  {res.get("error", "")}'


if __name__ == '__main__':
    raise SystemExit(main())
