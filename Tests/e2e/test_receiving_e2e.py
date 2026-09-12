"""test_receiving_e2e.py — a receiving crew through the production seam, reconciled.

Every other test of this feature drives the manager directly. This one goes through
`_prepare_channel_run` + `_run_strategy_worker` — the same two calls a real run makes — and
then reads the DATABASE back from disk and reconciles it. That matters because the interesting
failures are all in the wiring between those layers, not inside any of them:

  * a `we.extend` that never runs, so `work_events` and `batch_stats` disagree;
  * a crew handed to `recv_rows` as a `Crew` rather than its pre-offset worker tuple, so uids
    restart at 0 and collide with the pickers, which nothing downstream can see;
  * a clock that is not carried, so one receiver unloads two batches at the same instant;
  * a snapshot drained twice, so a batch reports an idle dock that moved hundreds of units.

None of those raises. Each produces a run that looks completely healthy — `work_events` has
no consumer anywhere outside `Tests/`, so nothing else in the repo would notice.

`reconcile()` is the assertion, and it is sabotage-checked: all six of its checks were
confirmed to FAIL against a hand-corrupted copy of a real DB, and the merge-key check catches
a duplicate that the existing `assert rows == sorted(rows)` guard passes.

Deliberately NOT built on `Tests/bench/coverage_e2e.py`: that harness hands every worker a
`queue.Queue()` it never drains and prints DONE regardless of what happened, so a green run
through it is not evidence.

Run:  python -m pytest Tests/e2e/test_receiving_e2e.py -q
"""
from __future__ import annotations

import logging
import os
import queue
import sqlite3

import pytest

from Diagnostics.receiving_report import reconcile
from Optimization import run_simulation as rs
from Optimization.persistence.Picking_Data import load_batch_stats
from Optimization.simdriver import strategy_runner as sr
from Warehouse.generation import generate_affinity as ga
from Warehouse.generation.generate_inventory import (
    Family, build_inventory_from_plan, save_inventory_to_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}


def _store_dbs(tmp_path, n_skus=250):
    plan = [Family('food', 0.6, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            Family('clothing', 0.4, (0.5, 0.5), _DIM, _DIM, _DIM, _WT)]
    inv = build_inventory_from_plan(num_skus=n_skus, plan=plan, seed=3)
    inv_db = str(tmp_path / 'inv.db')
    save_inventory_to_db(inv, inv_db, {'name': 'recv', 'num_skus': n_skus})
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


def _run_one_arm(tmp_path, monkeypatch, *, crew_size, day_seconds=None, n_batches=6):
    """One store arm, end to end. Returns (db_path, run_id)."""
    log = logging.getLogger('recv-e2e')
    log.setLevel(logging.ERROR)
    g = rs.CONFIG['global']
    monkeypatch.setitem(g, 'n_batches', n_batches)
    monkeypatch.setitem(g, 'recv_crew_size', crew_size)
    monkeypatch.setitem(g, 'recv_day_seconds', day_seconds)
    monkeypatch.setitem(g, 'recv_day_origin', 0.0)
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])

    inv_db, aff_db = _store_dbs(tmp_path)
    build_pair = str(tmp_path / 'build'); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        # No `max_bins`: a bin cap that binds below what the run's DECLARED levels need
        # now refuses the plan rather than fielding less (department-calibration, "Field
        # the floor", decision 3), and this fixture's cap sat below the 60-bucket
        # structural floor anyway -- it was already being warned past, not honoured.
        inv_db, aff_db, log, max_skus=250, min_bins=3000,
        keyframe_interval=0, warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))

    pair_dir = str(tmp_path / 'run'); os.makedirs(pair_dir, exist_ok=True)
    mixed, channel_runs = rs._channel_runs_for(shared['inventory'])
    ch, cfg = channel_runs[0]
    args, _sk = rs._prepare_channel_run(ch, cfg, mixed, shared, pair_dir, log, workers=1)

    a = args[0]
    a['log_queue'] = queue.Queue()
    sr._run_strategy_worker(a)
    return a['db_path'], a['run_id']


# ── the crew is off ───────────────────────────────────────────────────────────────

def test_no_crew_writes_no_receiving_and_still_reconciles(tmp_path, monkeypatch):
    """A run with no receiving crew must record four honest zeros and PASS. A tool that
    reported "no activity" as a failure would be useless on every run before this feature."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=0)
    rows = load_batch_stats(db, run_id)
    assert rows, 'the arm did not simulate'
    assert all(r.recv_unloaded == 0 and r.recv_seconds == 0.0 for r in rows)
    assert all(r.recv_depth == 0 and r.recv_cut == 0 for r in rows)

    con = sqlite3.connect(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM work_events WHERE role='receive'").fetchone()[0]
    finally:
        con.close()
    assert n == 0

    r = reconcile(db, run_id)
    assert r['verdict'] == 'PASS', r
    assert r['active'] is False


# ── the crew is on ────────────────────────────────────────────────────────────────

def test_a_receiving_crew_reconciles_across_both_surfaces(tmp_path, monkeypatch):
    """THE end-to-end claim. `work_events` and `batch_stats` are written by different code
    from different state and share nothing below the manager, so their agreement is real
    evidence rather than a tautology."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=2, day_seconds=3600.0)
    r = reconcile(db, run_id)
    assert r['active'], 'no receiving happened; the test proves nothing'
    assert r['verdict'] == 'PASS', (
        f'failed: {[k for k, v in r["checks"].items() if not v]} — {r}')
    assert r['qty_events'] == r['qty_batch_stats'] > 0
    assert r['seconds_events'] > 0.0


def test_the_three_crews_hold_disjoint_uid_blocks(tmp_path, monkeypatch):
    """A uid collision is invisible to everything else: it passes `put_rows`' bounds check,
    the DDL has no uniqueness constraint, and the merged view still sorts. It surfaces only
    as a per-actor rollup merging two people — and is quietest when the receiving crew is
    small, which is the likely configuration."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=2, day_seconds=3600.0)
    r = reconcile(db, run_id)
    blocks = r['uid_blocks']
    assert set(blocks) == {'pick', 'put', 'receive'}, blocks
    assert r['uid_overlaps'] == 0
    # NO contiguity assertion. It was here and it was unsound: an allocated-but-IDLE crew
    # leaves the same gap as a misallocated one, so on a 200-batch run with two of three put
    # queues idle it failed a provably healthy roster (allocated 0..28, observed {0..24, 26,
    # 28}). Disjointness is the half that actually guards the collision.
    # ...and they are in allocation order, which is what makes the cursor chaining visible
    lo = {role: rng[0] for role, rng in blocks.items()}
    assert lo['pick'] < lo['put'] < lo['receive'], blocks


def test_a_short_day_leaves_work_on_the_dock(tmp_path, monkeypatch):
    """ROLLOVER, through the production seam. A day far shorter than one unload lets exactly
    the committed unload through per batch — the START gate — and the rest carries."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=1, day_seconds=0.5)
    rows = load_batch_stats(db, run_id)
    # `any`, not `sum`. `recv_cut` is a LEVEL -- it equals `recv_depth` whenever a whistle
    # is in force -- so summing it counts a waiting unit once per batch it waits. What is
    # meaningful is whether the boundary bit at all, and in how many batches.
    assert any(r.recv_cut > 0 for r in rows), 'the whistle never bit'
    assert max(r.recv_depth for r in rows) > 0, 'nothing was left standing'
    # the dock is never negative and the carry is monotone while nothing clears it
    assert all(r.recv_depth >= 0 for r in rows)
    assert reconcile(db, run_id)['verdict'] == 'PASS'


def test_the_receive_rows_carry_the_right_role_and_type(tmp_path, monkeypatch):
    """`put_rows` used to hard-code `event_type='put'` while taking role from the worker, so
    a receive row could have carried `role='receive', event_type='put'` — making
    `SUM(duration) WHERE role='put'` disagree with the same query on `event_type`."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=2, day_seconds=3600.0)
    con = sqlite3.connect(db)
    try:
        pairs = con.execute(
            'SELECT DISTINCT role, event_type FROM work_events ORDER BY role, event_type'
        ).fetchall()
        aisle_null = con.execute(
            "SELECT COUNT(*) FROM work_events WHERE role='receive' AND aisle_id IS NOT NULL"
        ).fetchone()[0]
        nonpos = con.execute(
            "SELECT COUNT(*) FROM work_events WHERE role='receive' AND qty <= 0").fetchone()[0]
    finally:
        con.close()
    assert ('receive', 'receive') in pairs
    assert not [p for p in pairs if p[0] == 'receive' and p[1] != 'receive'], pairs
    assert aisle_null == 0, 'a dock has no aisle'
    assert nonpos == 0, 'an unload moves merchandise; qty must be positive'


def test_a_repack_row_is_exempt_but_the_clause_still_bites(tmp_path, monkeypatch):
    """Check 5's `repack` exemption, and the three rows it must NOT exempt.

    `repack_rows` declares that a repack is receiving work by the receiving crew at the
    dock's own per-pack price, so `role='receive'` and only `event_type` differs. Check 5's
    clause said the opposite and would have gone red on the first honest run of ADR-0003's
    rescue path -- silent until then only because `f_repack` is `assumed` 0.0.

    All four assertions or none: an exemption asserted alone is indistinguishable from
    deleting the check.
    """
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=2, day_seconds=3600.0)
    assert reconcile(db, run_id)['verdict'] == 'PASS'

    def _plant(role: str, event_type: str) -> dict:
        con = sqlite3.connect(db)
        try:
            con.execute(
                "INSERT INTO work_events (run_id, batch_id, seq, t_abs, t_local, "
                "shift_index, actor_uid, actor_local, role, mode, event_type, aisle_id, "
                "sku, qty, duration, source) "
                "SELECT run_id, batch_id, seq + 100000, t_abs, t_local, shift_index, "
                "actor_uid, actor_local, ?, mode, ?, aisle_id, sku, qty, duration, source "
                "FROM work_events WHERE run_id = ? AND role = 'receive' LIMIT 1",
                (role, event_type, run_id))
            con.commit()
        finally:
            con.close()
        r = reconcile(db, run_id)
        con = sqlite3.connect(db)
        try:
            con.execute('DELETE FROM work_events WHERE seq >= 100000 AND run_id = ?',
                        (run_id,))
            con.commit()
        finally:
            con.close()
        return r

    ok = _plant('receive', 'repack')
    assert ok['checks']['role_matches_event_type'] is True, (
        'a repack row tripped check 5; `role` is receive by DESIGN and only the type differs')

    for role, etype, why in (('put', 'pick', 'a put row typed pick is the case the '
                                             'rejected weaker clause sails past'),
                             ('receive', 'put', 'a receive row typed put is the original '
                                                'defect check 5 was written for'),
                             ('put', 'repack', 'the exemption is by (role, type) PAIR; '
                                               'only a RECEIVE row may be typed repack')):
        bad = _plant(role, etype)
        assert bad['checks']['role_matches_event_type'] is False, (
            f'({role}, {etype}) passed check 5 -- {why}')


# ── the unload price is one constant ──────────────────────────────────────────────

@pytest.fixture(scope='module')
def varied_arm(tmp_path_factory):
    """An arm whose receiving is VARIED enough for check 6 to mean anything, built once.

    The 6-batch arm the rest of this file uses unloads 7 packs, all of one SKU at qty 1 --
    and check 6 cancels `qty * handle_var`, so on a single (sku, qty) the residual is
    constant however the price was computed. Measured: zeroing that SKU's handle term
    shifted every row by the same amount and the spread check stayed GREEN. 25 batches
    gives ~420 packs over 16 distinct handle terms and 2 quantities, which the
    non-vacuity assertion below pins so this cannot silently regress to a degenerate arm.

    Module-scoped because the three tests here only READ it -- each sabotage works on its
    own copy -- and building it is the expensive half.
    """
    with pytest.MonkeyPatch.context() as mp:
        yield _run_one_arm(tmp_path_factory.mktemp('varied'), mp,
                           crew_size=2, day_seconds=3600.0, n_batches=25)


def test_the_unload_price_collapses_to_the_config_s_own_constant(varied_arm):
    """Check 6, and the reason it is evidence rather than a tautology.

    `duration - qty * sku_scores.handle_var` must collapse to `per_item + intercept`, one
    number carrying no SKU and no quantity. The two sides are built by different code from
    different state -- the dock prices a pack through `unload_cost`, `handle_var` is written
    at inventory load from the PICK config -- and they agree only because
    `UnloadCost.from_putaway` -> `PutawayCost.from_pick` carries the pick coefficients
    through unchanged.

    The constant is asserted against a value DERIVED FROM THE CONFIG here, not merely against
    itself: a check that only proved internal consistency would pass a dock built entirely
    from class defaults, which is the 55x drift `UnloadCost`'s docstring was written against
    and which a 2026-09-01 archive run actually exhibits (1.03 s to unload a unit whose
    handle term alone is 1.79 s).
    """
    from Inbound.unload import UnloadCost
    from Optimization.config.sim_config import _build_pick_cfg
    from Warehouse.operations.putaway import PutawayCost

    db, run_id = varied_arm

    # FIRST: the input is not degenerate. The residual cancels `qty * handle_var`, so on one
    # SKU at one quantity it is constant however the price was computed -- and a green check
    # would then be measuring nothing. This assertion is what stops the fixture drifting back
    # to the small arm the rest of the file uses.
    con = sqlite3.connect(db)
    try:
        n_hv, n_qty = con.execute(
            'SELECT COUNT(DISTINCT s.handle_var), COUNT(DISTINCT w.qty) '
            'FROM work_events w JOIN sku_scores s ON s.run_id = w.run_id AND s.sku = w.sku '
            "WHERE w.run_id = ? AND w.role = 'receive'", (run_id,)).fetchone()
    finally:
        con.close()
    assert n_hv > 1 and n_qty > 1, (
        f'{n_hv} distinct handle terms over {n_qty} distinct quantities -- with one of '
        f'either, the spread is zero however the price was computed')

    r = reconcile(db, run_id)
    assert r['active'], 'no receiving happened; the test proves nothing'
    assert r['checks']['unload_price_is_constant'] is True, r
    assert r['unload_unscored'] == 0 and r['unload_unpriceable'] == 0, r
    assert list(r['unload_constants']) == [run_id], r

    # Through the run harness's OWN dict -> PickConfig conversion, not a hand-built one: the
    # per-key fallbacks that used to live beside it had drifted 55x from the dataclass's, and
    # a second reconstruction here would be a third place for that to happen.
    expected = UnloadCost.from_putaway(
        PutawayCost.from_pick(_build_pick_cfg(rs.REGRESSION_CONFIGS[0], num_pickers=1)))
    assert abs(r['unload_constants'][run_id]
               - (expected.per_item + expected.intercept)) <= 1e-9, (
        f"C = {r['unload_constants'][run_id]} but this run's config derives "
        f'{expected.per_item + expected.intercept}; the dock is not priced from the pick '
        f'config it was built from')


def test_the_constant_check_catches_its_three_defects(varied_arm, tmp_path):
    """Sabotage, each against its own copy so the three failures cannot mask each other.

    The third is the one that earns a SEPARATE check key: an unscored SKU is the only clause
    here that goes red for a reason outside receiving, and reporting it as a spread would
    point a reader at the cost model when the defect is in what the run recorded.
    """
    import shutil

    src, run_id = varied_arm
    assert reconcile(src, run_id)['verdict'] == 'PASS'

    def _sabotage(name: str, sql: str, args=()) -> dict:
        cp = str(tmp_path / f'sab_{name}.db')
        shutil.copy(src, cp)
        con = sqlite3.connect(cp)
        try:
            cur = con.execute(sql, args)
            assert cur.rowcount > 0, f'{name}: the sabotage changed nothing'
            con.commit()
        finally:
            con.close()
        return reconcile(cp, run_id)

    # 1. one row's duration moved by 2x the tolerance -- the smallest thing the check claims
    #    to see, and the one that proves the tolerances are not swallowing real error.
    #
    #    THE TWO TOLERANCES ARE DIFFERENT AND THAT IS THE DECISION (site-dock 25). Check 6's
    #    is a SPREAD -- it does not accumulate, so its error is bounded by one row's rounding
    #    however many rows there are, and it keeps the flat `_TOL`. Check 1 compares two
    #    SUMS, which grow with the row count: a flat 1e-6 s there failed four archived arms
    #    on float re-association over 4.18M s. So the sabotage is sized against the arm's own
    #    sum, and the pair below pins the asymmetry rather than leaving it to be rediscovered.
    from Diagnostics.receiving_report import _TOL, _tol_for
    _base = reconcile(src, run_id)
    _bump = 2 * _tol_for(_base['seconds_events'], _base['seconds_batch_stats'])
    _sql = ('UPDATE work_events SET duration = duration + ? '
            "WHERE role = 'receive' AND run_id = ? "
            'AND id = (SELECT MIN(id) FROM work_events '
            "WHERE role = 'receive' AND run_id = ?)")
    r = _sabotage('dur', _sql, (_bump, run_id, run_id))
    assert r['checks']['unload_price_is_constant'] is False, r
    assert r['checks']['seconds_agree'] is False, (
        f'a {_bump:.3e} s move should show in check 1 on this arm')
    # ... and a move BELOW check 1's scaled tolerance still breaks check 6, which is the
    # half that must not be loosened: a spread is where a coefficient entering by the side
    # door shows up first, and it is visible long before the sum moves.
    assert 2 * _TOL < _bump, 'the arm is too small for the two tolerances to differ'
    r = _sabotage('dur_small', _sql, (2 * _TOL, run_id, run_id))
    assert r['checks']['unload_price_is_constant'] is False, r
    assert r['checks']['seconds_agree'] is True, (
        'check 1 fired on 2e-6 s over a multi-thousand-second arm; that is float '
        're-association, and failing on it is what reddened four archived arms')

    # 2. a coefficient that diverged: one SKU's handle term zeroed. Nothing else in the repo
    #    compares those two numbers, so this is the side door check 6 exists to shut.
    r = _sabotage('hv', 'UPDATE sku_scores SET handle_var = 0.0 WHERE run_id = ? AND sku = '
                        "(SELECT sku FROM work_events WHERE role = 'receive' "
                        ' AND run_id = ? LIMIT 1)', (run_id, run_id))
    assert r['checks']['unload_price_is_constant'] is False, r
    assert r['unload_unscored'] == 0, 'the SKU is still scored; only its value is wrong'
    assert all(v for k, v in r['checks'].items()
               if k != 'unload_price_is_constant'), (
        'zeroing a score broke a check that does not read sku_scores')

    # 3. a received SKU absent from a non-empty sku_scores -- the DISTINCT failure.
    r = _sabotage('gone', 'DELETE FROM sku_scores WHERE run_id = ? AND sku = '
                          "(SELECT sku FROM work_events WHERE role = 'receive' "
                          ' AND run_id = ? LIMIT 1)', (run_id, run_id))
    assert r['checks']['every_received_sku_is_scored'] is False, r
    assert r['unload_unscored'] > 0, r
    assert r['checks']['unload_price_is_constant'] is True, (
        'the missing SKU was reported as a SPREAD; the two failures are not separable and a '
        'reader would be pointed at the cost model for a recording defect')


def test_an_empty_sku_scores_makes_the_check_inactive_not_green(varied_arm, tmp_path):
    """A vintage that cannot answer must not report that it answered. The check key is ABSENT
    from `checks` rather than True -- the same idiom `active=False` uses for a run with no
    receiving crew, and the reason `verdict` alone is never the whole story."""
    import shutil

    src, run_id = varied_arm
    cp = str(tmp_path / 'no_scores.db')
    shutil.copy(src, cp)
    con = sqlite3.connect(cp)
    try:
        con.execute('DELETE FROM sku_scores WHERE run_id = ?', (run_id,))
        con.commit()
    finally:
        con.close()

    r = reconcile(cp, run_id)
    assert 'unload_price_is_constant' not in r['checks'], (
        'an unanswerable file reported a green check 6')
    assert 'every_received_sku_is_scored' not in r['checks'], (
        'an empty sku_scores is inactive, not a run that failed to score its SKUs')
    assert r['unload_constants'] == {} and r['unload_note'], r
    assert r['verdict'] == 'PASS', 'inactive is not a failure'
    assert r['active'] is True, 'receiving still happened; only check 6 could not run'


# ── the reconciliation can fail ───────────────────────────────────────────────────

def test_the_reconciliation_is_not_vacuous(tmp_path, monkeypatch):
    """A green reconciliation means nothing unless it can go red. Corrupts one surface of a
    real run and requires the matching check to catch it."""
    db, run_id = _run_one_arm(tmp_path, monkeypatch, crew_size=2, day_seconds=3600.0)
    assert reconcile(db, run_id)['verdict'] == 'PASS'

    con = sqlite3.connect(db)
    try:
        con.execute('UPDATE batch_stats SET recv_seconds = recv_seconds + 5.0 '
                    'WHERE recv_seconds > 0 AND run_id = ?', (run_id,))
        con.commit()
    finally:
        con.close()

    r = reconcile(db, run_id)
    assert r['verdict'] == 'FAIL'
    assert r['checks']['seconds_agree'] is False
    assert r['checks']['counts_agree'] is True, (
        'corrupting the seconds also broke the count check; the two are not independent '
        'and one of them is not measuring what it claims')
