"""test_production_hours_e2e.py — the objective's three legs, against the run that wrote them.

`Tests/unit/test_production_hours.py` pins the frame's arithmetic on hand-built rows.  This
file answers the question that only a real database can: **are those the right seconds?**

The put leg had no consumer before this, and that is exactly the condition under which a
measurement surface can be wired end to end, produce plausible numbers, and be wrong with
nothing able to notice — the argument `Diagnostics/receiving_report.py` was written from.
So each leg here is checked against a surface written by DIFFERENT code from DIFFERENT
state, and agreement is the evidence:

  unload   the frame's `unload_seconds` against `batch_stats.recv_seconds`, which
           `Inventory_Manager.receiving_snapshot()` writes from the dock's own counters.
           This one carries extra weight: the ticket specified the unload leg as a read of
           `recv_seconds`, and the build routes it through `work_events` instead because
           that column is outside the guaranteed sim-DB surface and postdates
           `work_events`, so no honest capability covers it. The deviation is only safe
           while the two surfaces agree, and this is where that is established rather than
           asserted.
  pick     the frame's `pick_seconds` against `task_stats.duration`, which is where it
           comes from — so this checks the JOIN, batch for batch, not the number.
  put      against the raw `work_events` sum, which checks the SQL fold and the role
           filter, and against zero, which checks that the leg is measured at all.

Then: the figure actually draws.  A grant is not an output and neither is a declaration —
`driver._run_one` swallows every render exception so one bad evaluation cannot sink the
pool, and a worker's logger reaches no file. An evaluation has shipped in this repo that
raised on every publish run, produced no figure, appeared in no log, and left the summary
reporting every request granted.

Run:  python -m pytest Tests/e2e/test_production_hours_e2e.py -q
"""
from __future__ import annotations

import glob
import logging
import os
import queue
import sqlite3

import pytest

from Optimization import run_simulation as rs
from Optimization.persistence.Picking_Data import load_work_hours
from Optimization.simdriver import strategy_runner as sr
from Optimization.Performance_Evaluations.common.frames import _bdf, _tdf, _wdf

# Sibling helpers — the harness that drives one arm through the production seam.
# `Tests/` is deliberately a non-package so pytest's prepend import mode makes this work
# with no per-file sys.path setup (see Tests/conftest.py).
from test_standing_yard_e2e import _run_one_arm, _store_dbs


def _frames(db, run_id):
    """The three frames as the analysis context would build them, straight off the file."""
    from Optimization.persistence.Picking_Data import load_batch_stats, load_task_stats
    df_b = _bdf(load_batch_stats(db, run_id))
    df_t = _tdf(load_task_stats(db, run_id), {}, {})
    df_w = _wdf(load_work_hours(db, run_id), df_b, df_t)
    return df_b, df_t, df_w


def _sql(db, statement, *args):
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        return con.execute(statement, args).fetchone()[0]
    finally:
        con.close()


@pytest.fixture(scope='module')
def _arm(tmp_path_factory):
    """One standing-yard arm, run once for every test here.

    Module-scoped because the run is the expensive part and nothing below mutates it.
    `monkeypatch` is function-scoped, so the CONFIG edits are made and undone by hand — the
    same keys `_run_one_arm` sets, restored in a finally so a failure here cannot leak
    inbound settings into another test module.
    """
    tmp = tmp_path_factory.mktemp('prod_hours')

    class _Patch:
        """The two `monkeypatch` methods `_run_one_arm` uses, with an undo log."""

        def __init__(self):
            self._undo = []

        def setitem(self, mapping, key, value):
            self._undo.append((mapping, key, mapping[key] if key in mapping else None,
                               key in mapping))
            mapping[key] = value

        def restore(self):
            for mapping, key, old, existed in reversed(self._undo):
                if existed:
                    mapping[key] = old
                else:
                    mapping.pop(key, None)

    patch = _Patch()
    try:
        # Two doors and a capped receiving day, so the crews genuinely work: put-away and
        # unloading both have to happen for the legs to be non-zero, and a yard that never
        # binds would leave the unload leg trivially small.
        db, run_id = _run_one_arm(tmp, patch, standing=True, allocation='split',
                                  n_batches=5, doors=2, recv_day=900.0)
        yield db, run_id
    finally:
        patch.restore()


# ── the legs, each against an independently written surface ─────────────────────

def test_the_put_leg_is_measured_at_all(_arm):
    """The leg that did not exist. Zero here means the whole objective is pick + unload."""
    db, run_id = _arm
    rows = load_work_hours(db, run_id)
    assert rows, 'the run wrote no work-event rows at all'
    assert {r['role'] for r in rows} >= {'put', 'pick'}, (
        f'roles present: {sorted({r["role"] for r in rows})}')
    _df_b, _df_t, df_w = _frames(db, run_id)
    assert float(df_w['put_seconds'].sum()) > 0.0, (
        'put-away hours are zero on a run that demonstrably put stock away — the role '
        'filter, the fold, or the timing is broken')


def test_the_put_leg_matches_the_raw_work_events_sum(_arm):
    """Checks the SQL fold and the role filter against the rows underneath it."""
    db, run_id = _arm
    _df_b, _df_t, df_w = _frames(db, run_id)
    raw = _sql(db, "SELECT COALESCE(SUM(duration), 0.0) FROM work_events "
                   "WHERE run_id = ? AND role = 'put'", run_id)
    assert float(df_w['put_seconds'].sum()) == pytest.approx(float(raw), rel=1e-9)


def test_the_unload_leg_matches_the_dock_column_the_ticket_specified(_arm):
    """THE deviation check: `work_events` role='receive' == `batch_stats.recv_seconds`.

    Two surfaces written by different code from different state — the metrics writer from
    the dock's drained records, and `Picking_Data` from the manager's counters — so
    agreement is evidence rather than tautology. It is also the licence for reading the
    unload leg out of `work_events`: if these ever diverge, the quantity is measuring
    something other than what the ticket named and one of the two halves is wrong.
    """
    db, run_id = _arm
    _df_b, _df_t, df_w = _frames(db, run_id)
    dock = _sql(db, 'SELECT COALESCE(SUM(recv_seconds), 0.0) FROM batch_stats '
                    'WHERE run_id = ?', run_id)
    assert float(dock) > 0.0, 'the dock recorded no receiving labour to reconcile against'
    assert float(df_w['unload_seconds'].sum()) == pytest.approx(float(dock), rel=1e-6)


def test_the_pick_leg_joins_the_task_frame_batch_for_batch(_arm):
    """Not a check of the number — a check of the JOIN.

    A per-batch off-by-one here would leave the run TOTAL correct while every paired
    comparison in the significance suite lined up batch i's picking against batch i-1's
    put-away, which no total would reveal.
    """
    db, run_id = _arm
    _df_b, df_t, df_w = _frames(db, run_id)
    per_batch = df_t.groupby('batch_id')['duration'].sum()
    w = df_w.set_index('batch_id')
    for b, secs in per_batch.items():
        assert w.loc[b, 'pick_seconds'] == pytest.approx(float(secs), rel=1e-9)
    # Batches with no tasks are present at zero rather than missing — the batch index of
    # this frame is the batch frame's, so every paired statistic can align on it.
    assert set(w.index) == {int(b) for b in _df_b['batch_id']}


def test_the_objective_is_the_sum_of_its_three_legs_on_the_real_file(_arm):
    db, run_id = _arm
    _df_b, _df_t, df_w = _frames(db, run_id)
    legs = df_w['put_seconds'] + df_w['unload_seconds'] + df_w['pick_seconds']
    assert df_w['production_seconds'].sub(legs).abs().max() == pytest.approx(0.0)
    # ...and it is strictly larger than the pick leg alone, which is the whole point:
    # selecting on `production_time` would be selecting on a proper subset of the work.
    assert float(df_w['production_seconds'].sum()) > float(df_w['pick_seconds'].sum())


def test_pick_rows_carry_no_duration_and_that_is_not_reported_as_a_loss(_arm):
    """The NULL contract, on the vintage that actually writes NULLs.

    `duration` was NOT NULL DEFAULT 0 until 2026-08-25, and while it was, every pick row
    claimed zero seconds — so `SUM(duration)` looked like a total while being put+receive
    only. The frame carries the skipped count so a vintage where that matters cannot render
    in silence.

    The second half is the correction this test was rewritten for. The count first covered
    every role, and the figure it feeds duly announced that ~51,000 work rows carried no
    duration and were "excluded from every leg" — on a completely healthy run, about rows
    whose exclusion is the contract. A warning that fires on every correct run is worse
    than none: the count is scoped to the roles this frame folds, where a missing interval
    really does leave a leg short.
    """
    db, run_id = _arm
    timed_picks = _sql(db, "SELECT COUNT(duration) FROM work_events "
                           "WHERE run_id = ? AND role = 'pick'", run_id)
    n_picks = _sql(db, "SELECT COUNT(*) FROM work_events "
                       "WHERE run_id = ? AND role = 'pick'", run_id)
    assert n_picks > 0
    assert timed_picks == 0, 'a pick row carries a duration — it is an instant, not a span'
    _df_b, _df_t, df_w = _frames(db, run_id)
    assert int(df_w['untimed_rows'].sum()) == 0, (
        'a healthy run reported untimed rows — either a put/receive interval genuinely '
        'went missing, or the count has drifted back to including pick rows')


# ── the figure: a declaration is not a drawing ──────────────────────────────────

def test_the_production_legs_figure_renders_from_a_real_run(tmp_path, monkeypatch):
    """Drives the real analysis and asserts FILES, then asserts the error tally is empty.

    Both halves are needed: the file proves something was drawn, the tally proves nothing
    was quietly swallowed on the way. Built as its own run rather than on the module
    fixture because `run_analysis` needs a finalized run tree, not a bare arm.
    """
    from Optimization import run_analysis as ra
    from Optimization.Performance_Evaluations.common import io as pe_io
    from Optimization.Performance_Evaluations.core import requests as pe_requests

    log = logging.getLogger('legs-render'); log.setLevel(logging.ERROR)
    g = rs.CONFIG['global']
    monkeypatch.setitem(g, 'n_batches', 4)
    monkeypatch.setitem(g, 'recv_crew_size', 2)
    monkeypatch.setitem(g, 'recv_day_seconds', 900.0)
    monkeypatch.setitem(g, 'recv_day_origin', 0.0)
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'inbound_dock_doors', 2)
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_crew_allocation', 'split')
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs',
                        [rs.REGRESSION_CONFIGS[0]])

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
    for skel in skels:
        rs._finalize_config_run(skel)

    pe_io._MAP_WARNINGS.clear()
    pe_requests.tally_snapshot(reset=True)
    ra.run_analysis(base_dir, log, workers=1, preset='NO_STATS')

    figs = glob.glob(os.path.join(base_dir, '**', 'figures', 'labor',
                                  'absolute_production_legs.png'), recursive=True)
    assert figs, ('labor.production_legs drew nothing on a run that recorded work events '
                  '— if the request was refused, the capability probe and the writer '
                  'disagree about what a work-event row is')
    errors = pe_requests.tally_snapshot()['errors']
    assert 'labor.production_legs' not in errors, errors.get('labor.production_legs')
    assert 'headline.top_vs_baseline' not in errors, (
        errors.get('headline.top_vs_baseline'))
