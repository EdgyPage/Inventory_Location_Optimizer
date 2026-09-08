"""test_standing_yard_e2e.py — the standing yard's byte-identity layers, on the real DB.

The unit tier (`Tests/unit/test_standing_yard.py`) pins the event STREAMS at manager
scale.  This file pins the same two claims where they were actually made — against the
run DATABASE, through the production seam (`_prepare_channel_run` + `_run_strategy_worker`,
the `test_receiving_e2e` harness shape):

  * THE DEGENERATE LOCKSTEP: standing-on, FIFO policies, doors >= every trailer that
    ever stands, no receiving cap, allocation='merged' writes a DB **byte-identical**
    to the v1 drain-everything trailer pipeline — every table, every row, every column
    (`simulation_runs.created` excepted: it is wall clock, and a probe of two identical
    v1 runs showed it is the ONLY nondeterministic column in the file).
  * THE CONTAINMENT PROPERTY: the same configuration with allocation='split' writes a DB
    identical EXCEPT the receive rows' labor stamps — t_abs / t_local / shift_index /
    actor_uid / actor_local — proving crew allocation is labor-only: same packs, same
    order, same durations, same placements, same picks.

Under a receiving cap the two allocations legitimately diverge in the unloaded set
(split makes partial progress everywhere; merged completes top-ranked trailers first).
That divergence is the feature, documented at the design ticket, and deliberately not
equality-tested anywhere.

Run:  python -m pytest Tests/e2e/test_standing_yard_e2e.py -q
"""
from __future__ import annotations

import logging
import os
import queue
import sqlite3

import pytest

from Optimization import run_simulation as rs
from Optimization.simdriver import strategy_runner as sr
from Warehouse.generation import generate_affinity as ga
from Warehouse.generation.generate_inventory import (
    Family, build_inventory_from_plan, save_inventory_to_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}

#: The one column two byte-identical runs may not share: wall clock at row creation.
_VOLATILE = {'simulation_runs': {'created'}}
#: What the containment property lets a receive row move: labor stamps, never work.
_LABOR_STAMPS = {'t_abs', 't_local', 'shift_index', 'actor_uid', 'actor_local'}

#: The yard's MEASUREMENT tables, which have no v1 counterpart to be identical to.
#:
#: This is an exemption from the row comparison and NOT a hole in it, for a reason worth
#: stating precisely: byte-identity here is a claim about placement physics and the event
#: stream, and these two tables record the standing model's own machinery — trailers
#: holding real doors, and per-drain door contention — which the v1 drain has no concept
#: of. Requiring them to match would require the standing run not to measure itself.
#:
#: The exemption is paid for below, twice. `_yard_row_counts` asserts the standing run
#: wrote rows and the v1 run wrote none, so "exempt" cannot silently become "the writer
#: never fired"; and every OTHER table still compares exactly, which is what proves the
#: yard writer perturbs nothing it observes.
_MEASUREMENT_ONLY = ('yard_trailers', 'yard_drains')


def _record_the_declaration(base_dir, label, shared, log):
    """Record the run's STOCK DECLARATION at the run root, as the real driver does.

    A run declares its own stock levels at setup (ADR-0002) and records the fixed point it
    declared at, so anything re-planning its warehouse later reproduces exactly those levels --
    the catalogue holds none.  `run_analysis` re-declares from this block and refuses rather
    than sizing a warehouse from nothing without it.  This harness assembles a run tree by hand
    instead of going through `_build_work_units`, so it must write the record the same way;
    calling the PRODUCTION function is the point, because a hand-rolled copy would drift.
    """
    import Optimization.simdriver.workunits as _wu
    from Optimization.runschema.sim_manifest import _write_run_spec, _load_run_spec
    if _load_run_spec(base_dir) is None:
        _write_run_spec(base_dir, {'argv': ['e2e-harness']})
    _wu._record_coverage(base_dir, label, shared.get('coverage'), log)


def _yard_row_counts(db) -> dict:
    con = sqlite3.connect(db)
    try:
        return {t: con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                for t in _MEASUREMENT_ONLY}
    finally:
        con.close()


def _assert_yard_is_observed_only_when_there_is_a_yard(db_v1, db_yard):
    """The price of the `_MEASUREMENT_ONLY` exemption, asserted rather than assumed."""
    assert _yard_row_counts(db_v1) == {t: 0 for t in _MEASUREMENT_ONLY}, (
        'the v1 trailer run wrote yard rows — the tables are meant to hold rows only '
        'when the standing yard is on, or "no yard" and "an empty yard" stop being '
        'distinguishable')
    got = _yard_row_counts(db_yard)
    assert all(n > 0 for n in got.values()), (
        f'the standing run recorded no yard at all ({got}) — the exemption above would '
        f'then be hiding a writer that never fired, which is exactly the failure '
        f'`save_checkpoint_bundle` has produced before over 68 databases')


def _store_dbs(tmp_path, n_skus=250):
    plan = [Family('food', 0.6, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            Family('clothing', 0.4, (0.5, 0.5), _DIM, _DIM, _DIM, _WT)]
    inv = build_inventory_from_plan(num_skus=n_skus, plan=plan, seed=3)
    inv_db = str(tmp_path / 'inv.db')
    save_inventory_to_db(inv, inv_db, {'name': 'yard', 'num_skus': n_skus})
    aff_db = str(tmp_path / 'aff.db')
    conn = ga._init_db(aff_db)
    skus = sorted(c.sku for c in inv.orders)
    rows = []
    for a, b in zip(skus, skus[1:]):
        rows += [(a, b, 1.5), (b, a, 1.5)]
    conn.executemany('INSERT OR REPLACE INTO affinity (sku_i, sku_j, lift) VALUES (?,?,?)', rows)
    conn.commit()
    conn.close()
    return inv_db, aff_db


def _run_one_arm(tmp_path, monkeypatch, *, standing, allocation='split', n_batches=6,
                 doors=500, recv_day=None):
    """One store arm through the production seam, trailer pipeline ON.

    The degenerate knobs are the two lockstep tests' whole point: doors far beyond any
    batch's trailer count, FIFO everywhere, and no receiving day — so the yard drains whole
    every batch and the ONLY difference between the arms is the standing machinery.
    `doors`/`recv_day` exist so the censored-tail test can leave that degenerate corner
    deliberately, which is the only way trailers are still standing when a run stops.
    """
    log = logging.getLogger('yard-e2e')
    log.setLevel(logging.ERROR)
    g = rs.CONFIG['global']
    monkeypatch.setitem(g, 'n_batches', n_batches)
    monkeypatch.setitem(g, 'recv_crew_size', 2)
    monkeypatch.setitem(g, 'recv_day_seconds', recv_day)
    monkeypatch.setitem(g, 'recv_day_origin', 0.0)
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'inbound_dock_doors', doors)
    monkeypatch.setitem(g, 'inbound_standing_yard', standing)
    monkeypatch.setitem(g, 'inbound_crew_allocation', allocation)
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])

    inv_db, aff_db = _store_dbs(tmp_path)
    build_pair = str(tmp_path / 'build'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=250, max_bins=40000, min_bins=3000,
        keyframe_interval=0, warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))

    pair_dir = str(tmp_path / 'run'); os.makedirs(pair_dir, exist_ok=True)
    mixed, channel_runs = rs._channel_runs_for(shared['inventory'])
    ch, cfg = channel_runs[0]
    args, _sk = rs._prepare_channel_run(ch, cfg, mixed, shared, pair_dir, log, workers=1)

    a = args[0]
    a['log_queue'] = queue.Queue()
    sr._run_strategy_worker(a)
    return a['db_path'], a['run_id']


def _dump(db):
    """{table: (columns, rows-in-insertion-order)} for every table in the file."""
    con = sqlite3.connect(db)
    out = {}
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for t in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info({t})')]
            try:
                rows = con.execute(f'SELECT * FROM {t} ORDER BY rowid').fetchall()
            except sqlite3.OperationalError:      # WITHOUT ROWID: PK order is natural
                rows = con.execute(f'SELECT * FROM {t}').fetchall()
            out[t] = (cols, rows)
    finally:
        con.close()
    return out


def _masked(cols, rows, dropped):
    if not dropped:
        return rows
    keep = [i for i, c in enumerate(cols) if c not in dropped]
    return [tuple(r[i] for i in keep) for r in rows]


# ── layer 2: the degenerate lockstep, on the file itself ─────────────────────────

def test_the_degenerate_merged_run_writes_a_byte_identical_db(tmp_path, monkeypatch):
    db_v1, _ = _run_one_arm(tmp_path / 'v1', monkeypatch, standing=False)
    db_yd, _ = _run_one_arm(tmp_path / 'yd', monkeypatch, standing=True,
                            allocation='merged')
    d1, d2 = _dump(db_v1), _dump(db_yd)
    assert set(d1) == set(d2)
    _assert_yard_is_observed_only_when_there_is_a_yard(db_v1, db_yd)
    for t in sorted(d1):
        cols, rows1 = d1[t]
        cols2, rows2 = d2[t]
        assert cols == cols2, t
        if t in _MEASUREMENT_ONLY:
            continue
        drop = _VOLATILE.get(t, set())
        assert _masked(cols, rows1, drop) == _masked(cols2, rows2, drop), (
            f'table {t!r} diverged — the degenerate standing run is not the v1 drain')
    # non-vacuity: the arms actually received through the trailer source
    con = sqlite3.connect(db_v1)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM work_events WHERE role='receive'").fetchone()[0]
    finally:
        con.close()
    assert n > 0, 'no receiving happened; the lockstep proved nothing'


# ── layer 3: containment — split moves labor stamps only ─────────────────────────

def test_the_split_run_differs_only_in_receive_labor_stamps(tmp_path, monkeypatch):
    db_v1, _ = _run_one_arm(tmp_path / 'v1', monkeypatch, standing=False)
    db_sp, _ = _run_one_arm(tmp_path / 'sp', monkeypatch, standing=True,
                            allocation='split')
    d1, d2 = _dump(db_v1), _dump(db_sp)
    assert set(d1) == set(d2)
    _assert_yard_is_observed_only_when_there_is_a_yard(db_v1, db_sp)
    for t in sorted(d1):
        cols, rows1 = d1[t]
        cols2, rows2 = d2[t]
        assert cols == cols2, t
        drop = set(_VOLATILE.get(t, set()))
        if t in _MEASUREMENT_ONLY:
            continue
        if t == 'work_events':
            role_i = cols.index('role')
            recv1 = [r for r in rows1 if r[role_i] == 'receive']
            recv2 = [r for r in rows2 if r[role_i] == 'receive']
            assert (_masked(cols, recv1, _LABOR_STAMPS)
                    == _masked(cols, recv2, _LABOR_STAMPS)), (
                'a receive row moved something besides its labor stamps — that is '
                'placement physics leaking out of the allocation mode')
            assert recv1 != recv2, (
                'no receive stamp differed anywhere — the containment test is vacuous '
                '(the scenario never staged two trailers against the crew)')
            rest1 = [r for r in rows1 if r[role_i] != 'receive']
            rest2 = [r for r in rows2 if r[role_i] != 'receive']
            assert rest1 == rest2, 'a NON-receive work_events row moved'
            continue
        assert _masked(cols, rows1, drop) == _masked(cols2, rows2, drop), (
            f'table {t!r} diverged — split allocation must be labor-only')


# ── the censored tail, on the run's own file ─────────────────────────────────────

def test_the_trailers_still_standing_at_run_end_reach_the_file(tmp_path, monkeypatch):
    """The run-end flush fires even when the FINAL CHECKPOINT BLOCK does not.

    This is the one path the two lockstep tests above cannot reach, and the reason it has
    its own writer rather than riding `save_checkpoint_bundle`. The checkpoint cadence is
    `max(1, n_batches // 10)`, so at these sizes it is **every batch** — which means the
    accumulator is empty when the loop ends and the `if pb:` final flush is skipped
    entirely. A censored tail written inside that block would be lost on every run whose
    batch count divides evenly by its cadence, and those rows are exactly where an
    adversarial ordering concentrates its overage.

    One door and a short receiving day is what makes trailers stand: the crew cannot
    unload what arrives, so the yard is still occupied when the run stops.
    """
    db, _ = _run_one_arm(tmp_path / 'cens', monkeypatch, standing=True,
                         allocation='merged', doors=1, recv_day=60.0)
    con = sqlite3.connect(db)
    try:
        by_status = dict(con.execute(
            'SELECT status, COUNT(*) FROM yard_trailers GROUP BY status').fetchall())
        censored = con.execute(
            'SELECT COUNT(*) FROM yard_trailers WHERE emptied_s IS NULL').fetchone()[0]
        # A censored row must still carry the stamp it DOES have; an arrival written as
        # NULL would make every detention span in the run unresolvable.
        no_arrival = con.execute(
            'SELECT COUNT(*) FROM yard_trailers WHERE arrived_s IS NULL').fetchone()[0]
    finally:
        con.close()
    assert by_status.get('standing', 0) > 0, (
        f'one door and a 60 s receiving day left nothing standing ({by_status}) — either '
        f'the scenario no longer creates a backlog, or the run-end flush never ran')
    assert censored == by_status['standing'], (
        'a row is `standing` iff its detention is censored; the two disagreeing means '
        'the status and the null pattern are being written from different places')
    assert no_arrival == 0


# ── the report surface: the yard family must actually DRAW ───────────────────────

def test_the_yard_family_renders_from_a_standing_run(tmp_path, monkeypatch):
    """A grant is not an output, and neither is a declaration.

    Four evaluations, five declared quantities and a run-tree glob can all be correct while
    the render bodies raise on first contact — `driver._run_one` swallows every render
    exception so one failure cannot sink the pool, and a worker's logger reaches no file.
    An evaluation has shipped in this repo that raised on every publish run, produced not
    one figure, appeared in no log, and left the summary reporting all requests granted.

    So this drives the real analysis over a real standing-yard run and asserts FILES, then
    asserts the error tally is empty. Both halves are needed: the files prove something was
    drawn, the tally proves nothing was quietly swallowed on the way.
    """
    from Optimization import run_analysis as ra
    from Optimization.Performance_Evaluations.common import io as pe_io
    from Optimization.Performance_Evaluations.core import requests as pe_requests

    log = logging.getLogger('yard-render'); log.setLevel(logging.ERROR)
    g = rs.CONFIG['global']
    monkeypatch.setitem(g, 'n_batches', 4)
    monkeypatch.setitem(g, 'recv_crew_size', 2)
    monkeypatch.setitem(g, 'recv_day_seconds', 900.0)   # a cap, so trailers stand and queue
    monkeypatch.setitem(g, 'recv_day_origin', 0.0)
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'inbound_dock_doors', 2)     # few doors, so the yard BINDS
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_crew_allocation', 'split')
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])

    inv_db, aff_db = _store_dbs(tmp_path)
    build_pair = str(tmp_path / 'build'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=250, max_bins=40000, min_bins=3000,
        keyframe_interval=0, warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))

    base_dir = str(tmp_path / 'run')
    pair_dir = os.path.join(base_dir, 'store_pair'); os.makedirs(pair_dir, exist_ok=True)
    mixed, channel_runs = rs._channel_runs_for(shared['inventory'])
    ch, cfg = channel_runs[0]
    args, skels = rs._prepare_channel_run(ch, cfg, mixed, shared, pair_dir, log, workers=1)
    for a in args:
        a['log_queue'] = queue.Queue()
        sr._run_strategy_worker(a)
    for skel in skels:                        # one skeleton per channel run
        rs._finalize_config_run(skel)
    _record_the_declaration(base_dir, 'store_pair', shared, log)

    pe_io._MAP_WARNINGS.clear()
    pe_requests.tally_snapshot(reset=True)
    ra.run_analysis(base_dir, log, workers=1, preset='NO_STATS')

    import glob
    figs = sorted(os.path.basename(p) for p in
                  glob.glob(os.path.join(base_dir, '**', 'figures', 'yard', '*.png'),
                            recursive=True))
    assert figs, ('the yard family drew nothing on a run whose yard demonstrably bound — '
                  'see the [era] lines: if the request was refused, the writer and the '
                  'reader disagree about what a yard row is')
    # Every evaluation in the family, by the file each one owns. Named individually rather
    # than counted: three of four drawing is a partial failure that a count would pass.
    assert any(f.endswith('_yard_overage_days.png') for f in figs), figs      # yard.fee
    assert 'absolute_detention_distribution.png' in figs, figs                # yard.detention
    assert any(f.endswith('_yard_depth.png') for f in figs), figs             # yard.binding
    assert 'absolute_yard_scorecard.png' in figs, figs                        # yard.scorecard

    assert pe_io._MAP_WARNINGS == set(), (
        f'a yard figure landed outside its declared out_subdir: {pe_io._MAP_WARNINGS}')
    errs = pe_requests.tally_snapshot().get('errors', {})
    yard_errs = {k: v for k, v in errs.items() if k.startswith('yard.')}
    assert not yard_errs, f'a yard render raised and was swallowed: {yard_errs}'
