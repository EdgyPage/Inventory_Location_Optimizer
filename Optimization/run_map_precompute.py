"""run_map_precompute.py — measure the map family's OFFLINE build on a finished run.

WHY A SEPARATE CLI.  `strat.build()` now carries a timer, so every future run records its
setup span inline.  A run already on disk cannot be re-simulated to get one — that is
hundreds of GB and hours — but it CAN be re-measured: the build is a pure function of the
run's own catalogue and warehouse shape, both of which the run kept.  This rebuilds those,
times `build_optimal_map` alone, and writes the result back into the run's runtime table
stamped `backfill` so nothing downstream mistakes it for an inline measurement.

    python -m Optimization.run_map_precompute <run_root>

RUN IT AFTER `analyze_run`, not before.  The measurements themselves go into the run's
runtime table and survive anything, but the per-class detail lands under the dossier tree,
and the dossier stage WIPES that tree on every analysis pass (it is a derived directory —
a stale figure from a previous suite is worse than a missing one).  Backfill first and the
census is silently gone by the time the site stages.

THE GATE, and why it is not optional.  A hand-rolled rebuild of the worker's setup phase is
a SECOND implementation of something the simulator already does — the exact drift class the
run dossier exists to end, reintroduced by the fix for it.  So the measurement is refused
unless the reconstruction reproduces the warehouse identity the run itself recorded
(`n_bins`, `regime_bins`, `n_aisles`, SKU count).  A number measured on a warehouse nobody
checked is an unfalsifiable claim, and this file would be where it entered.

WHAT THE NUMBER IS AND IS NOT.  An UNCONTENDED, single-arm wall time: this process builds
one map at a time, where the sweep ran a 20-worker pool.  It is therefore not comparable as
a ratio against the loop seconds in the same table, which is why `precomp_src` exists and
why every consumer must read it.  It also carries the per-class solver split — how much of
the assignment was solved exactly rather than greedily — which is the other thing the
published pages asserted without measuring.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time

# ── path setup: repo root on sys.path so package imports resolve when run as a
#    script (python Optimization/run_map_precompute.py <dir>); `-m` needs none.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Optimization.config.objectives import OBJECTIVES
from Optimization.config.sim_config import _OUTPUT_DIR, _setup_logging, regime_sizing_from_config
from Optimization.persistence import runtime_metrics as rm
from Optimization.run_analysis import _apply_run_shape
from Optimization.runschema import resolver_for

#: The rules that HAVE an offline build, straight from the rule registry — so adding a
#: precomputing rule does not need an edit here, and a rule that stops precomputing does
#: not leave a stale name behind.
MAP_RULES = tuple(sorted(k for k, o in OBJECTIVES.items() if o.precompute))


def _metas_by_pair(base_dir, rt) -> dict:
    """{pair: [(cell, ChannelRun, sim_meta), ...]} across the WHOLE run.

    `rt.channel_runs()` spans cells; the raw walker takes one cell directory, which is the
    shape the per-cell analysis uses and the wrong one here — it silently yields nothing
    when handed a run root.
    """
    out: dict = {}
    for cell, run in rt.channel_runs():
        with open(rt.leaf_path(run, 'sim_meta'), encoding='utf-8') as fh:
            out.setdefault(run.pair, []).append((cell, run, json.load(fh)))
    return out


def _workload_params(rt, cell, run):
    """WorkloadParams from the leaf's committed config.json, or None when it is absent."""
    import dataclasses
    from Optimization.config.sim_config import _CART_TYPES
    from Optimization.metrics.Workload import WorkloadParams
    from Warehouse.picking.Pick import PickConfig, StoreCart
    try:
        path = rt.path('config_json', cell=cell, pair=run.pair, config=run.config)
    except Exception:                                      # noqa: BLE001 - absence is data
        return None
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as fh:
        cfg = json.load(fh)
    fields = {f.name for f in dataclasses.fields(PickConfig)}
    kw = {k: v for k, v in cfg.items() if k in fields}
    # `cart` is serialised as its NAME; PickConfig wants the class.  The same registry the
    # sim resolves it through, so a store leaf gets the store cart and a fulfillment leaf
    # does not silently inherit it — the cart term is where the two channels diverge most.
    kw['cart'] = _CART_TYPES.get(cfg.get('cart'), StoreCart)
    # The top height bracket's threshold is infinity, and JSON has no infinity — it
    # round-trips as null.  Restoring it is not cosmetic: `height_multiplier` compares
    # against the threshold, so a null makes every bin's height multiplier raise.
    if kw.get('height_brackets'):
        kw['height_brackets'] = tuple(
            (float('inf') if thr is None else thr, mult)
            for thr, mult in kw['height_brackets'])
    return WorkloadParams.from_pick_config(PickConfig(**kw))


def _recorded(rows, cell, pair, config, channel, arm) -> dict | None:
    for r in rows:
        if (r.get('cell') == cell and r.get('pair') == pair and r.get('config') == config
                and r.get('channel') == channel and r.get('arm') == arm):
            return r
    return None


def _identity_matches(log, rec, shared) -> bool:
    """The gate: does the rebuild reproduce what the run recorded about its warehouse?

    A refusal here is the DESIGNED outcome, not a failure of this script.  It already
    caught the real thing once: without the run's own shaping params applied first, the
    rebuild produced 5,814 aisles / 6.2 M bins against a run that recorded 384 / 398,500,
    and the timing taken on it would have been published as if it meant something.

    `regime_bins` is deliberately not checked: it is the per-CHANNEL slice, and the whole
    warehouse is what the map is built over.
    """
    want = {'n_aisles': rec.get('n_aisles'), 'n_bins': rec.get('n_bins')}
    got = {'n_aisles': shared['total_aisles'], 'n_bins': shared['total_bins']}
    bad = {k: (want[k], got[k]) for k in want if want[k] and want[k] != got[k]}
    for k, (w, g) in bad.items():
        log.error(f'    REFUSING to record: {k} recorded {w:,} but the rebuild made {g:,}')
    return not bad


def measure_pair(base_dir, rt, pair, metas, rows, log, max_skus=None) -> list:
    """Time `build_optimal_map` once per (channel, map rule) for one inventory pair."""
    from Optimization.simdriver.sim_assets import build_shared_assets
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    from Warehouse.inventory import inventory_optimal as io_mod

    inv_db = next((m.get('inv_db') for _c, _r, m in metas if m.get('inv_db')), None)
    aff_db = next((m.get('aff_db') for _c, _r, m in metas if m.get('aff_db')), None)
    if not inv_db or not aff_db:
        log.warning(f'  {pair}: no inv/aff db recorded in sim_meta — skipped')
        return []
    if not os.path.exists(inv_db):
        log.warning(f'  {pair}: the recorded inventory DB is not on this machine — skipped')
        return []

    # The SAME rebuild the analysis stage does (run_analysis._config_jobs): the run's own
    # inventory and the run's own per-regime sizing.  Sizing this differently from the run
    # is the silent wrong-warehouse bug the gate below exists to catch.
    log.info(f'  {pair}: rebuilding the warehouse shape')
    shared = build_shared_assets(inv_db, aff_db, log, max_skus=max_skus,
                                 regime_sizing=regime_sizing_from_config())
    orders = shared['inventory'].orders
    n_skus = len(orders)

    out = []
    for cell, run, meta in metas:
        # The cost constants are per CHANNEL (store and fulfillment differ in pick
        # intercept, cart size and both pick-time exponents), and the leaf's committed
        # config.json is the run's own record of them — sim_meta does not carry them.
        # Timing the map against the wrong channel's constants would measure a build the
        # run never performed.
        wp = _workload_params(rt, cell, run)
        if wp is None:
            log.warning(f'  {pair}/{run.config}: no leaf config.json — skipped')
            continue
        for rule in MAP_RULES:
            for initial in ('uni', 'opt'):
                arm = f'{initial}_{rule}_norsl'
                rec = _recorded(rows, cell, pair, run.config, run.channel or '', arm)
                if rec is None:
                    continue
                if not _identity_matches(log, rec, shared):
                    continue
                # `warehouse_meta` is the parent's copy of the same geometry the workers
                # each rebuilt from `warehouse_cfg` — the gate above is what certifies
                # it is the geometry this arm actually ran on.
                mgr = Inventory_Manager(shared['warehouse_meta'], affinity=None)
                classes = []
                io_mod.OptimalLayoutMixin._assign_probe = classes.append
                try:
                    freq = {c.sku: c.demand.relative_frequency for c in orders}
                    qty = {c.sku: c.demand.quantity_rate for c in orders}
                    t0 = time.perf_counter()
                    mgr.build_optimal_map(orders, freq, qty, wp)
                    elapsed = time.perf_counter() - t0
                finally:
                    io_mod.OptimalLayoutMixin._assign_probe = None
                stats = getattr(mgr, '_map_lap_stats', {}) or {}
                pct = ((stats.get('lap_units', 0) / stats['units'])
                       if stats.get('units') else None)
                ok = rm.record_precompute(base_dir, cell,
                                          (pair, run.config, run.channel or '', arm),
                                          elapsed, 'backfill', map_lap_pct=pct)
                log.info(f'    {cell}/{run.config}/{arm}: {elapsed:.1f}s  '
                         f'exact-solved {0.0 if pct is None else pct * 100:.2f}% of units'
                         + ('' if ok else '   (no row to update)'))
                for c in classes:
                    out.append({'cell': cell, 'pair': pair, 'config': run.config,
                                'channel': run.channel or '', 'arm': arm, 'rule': rule,
                                'binkey': '|'.join(str(x) for x in c['key']),
                                **{k: c[k] for k in ('n', 'm_cnt', 'gate_n_ok',
                                                     'gate_prod_ok', 'branch', 'solve_s',
                                                     'error')}})
                # One arm per (rule, channel) is enough: `uni` and `opt` build the same map
                # from the same orders, and the second measurement would only re-time it.
                break
    return out


def run(base_dir: str, log=None) -> None:
    log = log or logging.getLogger('analysis')
    rt = resolver_for(base_dir)
    rows = rm.load_rows(base_dir)
    if not rows:
        log.warning('no runtime metrics at the run root — nothing to backfill')
        return
    # THE RUN'S OWN SHAPE, FIRST.  `regime_sizing_from_config()` reads whatever CONFIG this
    # checkout holds; the run's caps and max_skus live in its run_spec.  Skipping this step
    # rebuilt a 5,814-aisle / 6.2 M-bin warehouse against a run that recorded 384 / 398,500 —
    # the gate below caught it, which is precisely why the gate is not optional.
    max_skus = _apply_run_shape(base_dir, log)
    log.info(f'map precompute backfill: {len(MAP_RULES)} precomputing rule(s) '
             f'{MAP_RULES}')
    detail = []
    for pair, metas in _metas_by_pair(base_dir, rt).items():
        detail.extend(measure_pair(base_dir, rt, pair, metas, rows, log, max_skus=max_skus))
    if not detail:
        log.warning('nothing measured (no map arms, or the gate refused every one)')
        return
    # The per-class detail lands under the dossier's tables glob, so it costs no new
    # declaration and no new forbidden filename token.  HEAD's contract, not the run's:
    # this is an ANALYSIS output, and a finished run's own document predates the dossier
    # — resolving through it raises for a directory that is perfectly legal to write.
    from Optimization.runschema import analysis_path
    root = analysis_path(rt, 'dossier_dir')
    if root is None:
        log.warning('  no contract declares the dossier tree — solver census not written')
        return
    tdir = os.path.join(root, 'tables')
    os.makedirs(tdir, exist_ok=True)
    path = os.path.join(tdir, 'map_solver_census.csv')
    with open(path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=list(detail[0]))
        w.writeheader()
        w.writerows(detail)
    lap = sum(1 for d in detail if d['branch'] == 'lap')
    units = sum(d['n'] for d in detail)
    lap_units = sum(d['n'] for d in detail if d['branch'] == 'lap')
    log.info(f'  wrote the solver census: {lap}/{len(detail)} classes solved exactly, '
             f'{lap_units:,}/{units:,} units ({100.0 * lap_units / max(units, 1):.2f}%)')


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Measure the map family\'s offline build on a finished run.')
    ap.add_argument('base_dir')
    args = ap.parse_args(argv)
    base_dir = args.base_dir
    if not os.path.isdir(base_dir):
        base_dir = os.path.join(_OUTPUT_DIR, args.base_dir)
    if not os.path.isdir(base_dir):
        sys.exit(f'Directory not found: {args.base_dir}')
    log = _setup_logging(os.path.join(base_dir, 'analysis.log'))
    run(base_dir, log=log)


if __name__ == '__main__':                                  # pragma: no cover
    main()
