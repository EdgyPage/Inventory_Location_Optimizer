"""test_queue_state_and_carryover.py — the queue's own state, and what it failed to place.

Two new surfaces, both per batch:

  `put_queue_state`  one row per (batch, queue): depth and oldest age (LEVELS), plus
                     admitted / placed / blocked (FLOWS). `blocked` is the only trace a
                     refused admission leaves anywhere — without it the backpressure is real
                     and invisible.
  `carryover`        what did not get placed, and WHY. "could not reach a bin" and "was
                     refused floor space" are different problems with different fixes, and a
                     single carried-over count cannot tell them apart.

THIS FILE RUNS THE ACTUAL WRITER AND READS THE FILE BACK, because a bundle argument that is
accepted and never inserted is `save_checkpoint_bundle`'s characteristic failure: `work_events`
was one, and the reconciliation meant to catch it passed over 68 databases holding zero rows.
An in-memory check of the accumulator lists would repeat that exactly.

Run:  python -m pytest Tests/integration/test_queue_state_and_carryover.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from Optimization.persistence.Picking_Data import (
    create_run, init_run_db, save_carryover, save_checkpoint_bundle, save_put_queue_state,
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'sim_qs.db')
    init_run_db(path)
    return path, create_run(path, 'test')


def _rows(path, sql, args=()):
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args)]
    finally:
        con.close()


STATE = [
    {'batch_id': 0, 'queue': 'store_cart', 'depth': 3, 'oldest_age': 11, 'staging': None,
     'admitted': 9, 'placed': 6, 'blocked': 0},
    {'batch_id': 0, 'queue': 'store_pallet', 'depth': 40, 'oldest_age': 2, 'staging': 40,
     'admitted': 12, 'placed': 5, 'blocked': 7},
    {'batch_id': 1, 'queue': 'store_cart', 'depth': 0, 'oldest_age': None, 'staging': None,
     'admitted': 4, 'placed': 7, 'blocked': 0},
]
CARRY = [(0, 'unplaced', 101, 8), (0, 'held', 101, 14), (0, 'held', 102, 3),
         (1, 'unplaced', 103, 2)]


# ── the tables exist and hold what was written ────────────────────────────────────

def test_queue_state_round_trips(db):
    path, rid = db
    save_put_queue_state(path, rid, STATE)
    got = _rows(path, 'SELECT * FROM put_queue_state WHERE run_id=? '
                      'ORDER BY batch_id, queue', (rid,))
    assert len(got) == 3
    pallet = next(r for r in got if r['queue'] == 'store_pallet')
    assert (pallet['depth'], pallet['staging'], pallet['blocked']) == (40, 40, 7)
    assert pallet['depth'] == pallet['staging'], 'the fixture should be at its limit'
    empty = next(r for r in got if r['batch_id'] == 1)
    assert empty['oldest_age'] is None and empty['depth'] == 0


def test_carryover_round_trips_with_its_reason(db):
    path, rid = db
    save_carryover(path, rid, CARRY)
    got = _rows(path, 'SELECT reason, sku, qty FROM carryover WHERE run_id=? AND batch_id=0 '
                      'ORDER BY reason, sku', (rid,))
    assert got == [{'reason': 'held', 'sku': 101, 'qty': 14},
                   {'reason': 'held', 'sku': 102, 'qty': 3},
                   {'reason': 'unplaced', 'sku': 101, 'qty': 8}]
    # The same SKU carries for two different reasons in the same batch and they stay apart.
    assert len([r for r in got if r['sku'] == 101]) == 2


def test_a_zero_blocked_count_is_recorded_rather_than_omitted(db):
    """Absence and zero are different claims. A queue that recorded nothing might not have
    been sampled; a queue that recorded 0 was sampled and refused nothing."""
    path, rid = db
    save_put_queue_state(path, rid, STATE)
    cart = _rows(path, "SELECT blocked FROM put_queue_state WHERE run_id=? AND "
                       "queue='store_cart' AND batch_id=0", (rid,))
    assert cart == [{'blocked': 0}]


# ── the bundle actually inserts them ──────────────────────────────────────────────

def test_the_bundle_inserts_both_new_surfaces(db):
    """The guard the `work_events` bug earned. A bundle argument that is accepted and
    silently dropped produces a database that passes every in-memory check."""
    path, rid = db
    save_checkpoint_bundle(
        path, rid, batch_stats=[], task_stats=[], picker_events=[], picks=[],
        bin_placements=[], bin_evictions=[], aisle_metrics=[], reorder_queue=[],
        put_queue_state=STATE, carryover=CARRY)
    n_state = _rows(path, 'SELECT COUNT(*) c FROM put_queue_state WHERE run_id=?', (rid,))
    n_carry = _rows(path, 'SELECT COUNT(*) c FROM carryover WHERE run_id=?', (rid,))
    assert n_state[0]['c'] == len(STATE) > 0
    assert n_carry[0]['c'] == len(CARRY) > 0


def test_a_caller_that_omits_them_writes_no_rows(db):
    """Both are keyword-optional so every pre-existing caller is unchanged."""
    path, rid = db
    save_checkpoint_bundle(
        path, rid, batch_stats=[], task_stats=[], picker_events=[], picks=[],
        bin_placements=[], bin_evictions=[], aisle_metrics=[], reorder_queue=[])
    assert _rows(path, 'SELECT COUNT(*) c FROM put_queue_state')[0]['c'] == 0
    assert _rows(path, 'SELECT COUNT(*) c FROM carryover')[0]['c'] == 0


# ── the grain is one row per (batch, queue) ───────────────────────────────────────

def test_re_saving_a_batch_replaces_rather_than_duplicates(db):
    """A resumed run re-flushes its tail. Two rows claiming to be the same (batch, queue)
    would make every per-queue number ambiguous, so the primary key forbids it."""
    path, rid = db
    save_put_queue_state(path, rid, STATE)
    save_put_queue_state(path, rid, STATE)
    assert _rows(path, 'SELECT COUNT(*) c FROM put_queue_state WHERE run_id=?',
                 (rid,))[0]['c'] == len(STATE)


def test_carryover_is_keyed_by_batch_reason_and_sku(db):
    path, rid = db
    save_carryover(path, rid, CARRY)
    save_carryover(path, rid, [(0, 'held', 101, 99)])       # same key, new value
    got = _rows(path, "SELECT qty FROM carryover WHERE run_id=? AND batch_id=0 AND "
                      "reason='held' AND sku=101", (rid,))
    assert got == [{'qty': 99}], 'the row should be replaced, not duplicated'
    assert _rows(path, 'SELECT COUNT(*) c FROM carryover WHERE run_id=?',
                 (rid,))[0]['c'] == len(CARRY)


# ── the PRODUCER, driven directly ─────────────────────────────────────────────────
#
# These three reporters live on the manager rather than inline in `strategy_runner` for
# exactly one reason: inline, they were reachable only by a full sweep, and no arm in the
# coverage sweep leaves anything unplaced — so the carryover branch had zero coverage while
# looking covered.

def _mgr_with_backlog():
    """A manager holding a known backlog: two queued, three refused."""
    import pathlib as _p
    import sys as _s
    _s.path.insert(0, str(_p.Path(__file__).resolve().parents[1] / 'calltree'))
    import calltree_scenarios as cs
    from Warehouse.inventory.put_queue import PutQueueSet, PutQueueSpec, PALLET

    a = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=1, coverage=2.0, safety=0.4)
    mgr = a.mgr
    mgr.put_queues = PutQueueSet([PutQueueSpec('tight', accepts=(PALLET,), staging=2)])
    mgr._held.clear()
    for q in mgr.put_queues:
        q.drain_counters()

    class _U:
        __slots__ = ('unit_category', 'order', 'quantity', 'storage_size')

        def __init__(self, sku, qty):
            self.unit_category, self.quantity = PALLET, qty
            self.storage_size = 'large'
            self.order = type('O', (), {'sku': sku})()

    for sku, qty in ((7, 3), (7, 4), (8, 5), (9, 2), (9, 6)):
        mgr._admit(_U(sku, qty), 'reorder')
    return mgr


def test_carryover_rows_separate_unplaced_from_held():
    mgr = _mgr_with_backlog()
    assert len(mgr._stock_queue) == 2 and mgr.held_depth == 3
    rows = sorted(mgr.carryover_rows(4))
    assert all(b == 4 for b, _r, _s, _q in rows)
    by = {(r, s): q for _b, r, s, q in rows}
    # First two admitted (sku 7 x3, sku 7 x4) fit; the rest were refused.
    assert by[('unplaced', 7)] == 7
    assert by[('held', 8)] == 5 and by[('held', 9)] == 8
    assert ('unplaced', 8) not in by, 'a held unit was reported as merely unplaced'
    assert sum(by.values()) == 20, 'the carryover does not account for every unit'


def test_queue_contents_reports_held_items_too():
    """A reader that walked only the queues would under-report the backlog exactly when
    backpressure is doing something."""
    mgr = _mgr_with_backlog()
    rows = mgr.queue_contents()
    kinds = {k for k, *_ in rows}
    assert kinds == {'stock', 'held'}
    assert sum(q for *_r, q in rows) == 20
    assert all(qn == 'tight' for *_r, qn, _q in rows)


def test_queue_state_rows_drain_the_counters_exactly_once():
    """Called twice in one batch, the second call would report flows of zero — which is why
    the runner calls it exactly once and this test says so."""
    mgr = _mgr_with_backlog()
    first = mgr.queue_state_rows(4)
    assert len(first) == 1
    assert first[0]['batch_id'] == 4 and first[0]['queue'] == 'tight'
    assert first[0]['admitted'] == 2 and first[0]['blocked'] == 3
    assert first[0]['depth'] == 2 and first[0]['staging'] == 2
    second = mgr.queue_state_rows(4)
    assert second[0]['admitted'] == 0 and second[0]['blocked'] == 0
    assert second[0]['depth'] == 2, 'depth is a level and must not reset'


# ── the working day reaches the database ──────────────────────────────────────────

def test_batch_stats_round_trips_the_working_day_and_the_missed_slot(db):
    """Two columns without which the feature is uninterpretable: no analysis can group a
    result BY DAY, and a run cannot say whether its schedule was ever met.

    `released_late` matters because `release_at` CLAMPS to the instant the arm is free —
    the model has no picker contention, so a batch cannot begin while the crew is still
    working the previous one. That clamp erases the miss; this column is the only record.
    """
    from Optimization.persistence.Picking_Data import BatchStats, save_batch_stats
    path, rid = db
    rows = [
        BatchStats(run_id=rid, batch_id=0, duration=10.0, num_tasks=1, total_items=5,
                   avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5),
        BatchStats(run_id=rid, batch_id=1, duration=20.0, num_tasks=2, total_items=9,
                   avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5),
    ]
    rows[0].work_day, rows[0].released_late = 0, 0.0
    rows[1].work_day, rows[1].released_late = 3, 41.5
    save_batch_stats(path, rid, rows)
    got = _rows(path, 'SELECT batch_id, work_day, released_late FROM batch_stats '
                      'WHERE run_id=? ORDER BY batch_id', (rid,))
    assert got == [{'batch_id': 0, 'work_day': 0, 'released_late': 0.0},
                   {'batch_id': 1, 'work_day': 3, 'released_late': 41.5}]


def test_the_working_day_columns_default_to_zero(db):
    """A continuous schedule has no day structure and no slots, so both are 0 — and an
    older BatchStats that never sets them must still insert."""
    from Optimization.persistence.Picking_Data import BatchStats, save_batch_stats
    path, rid = db
    save_batch_stats(path, rid, [
        BatchStats(run_id=rid, batch_id=7, duration=1.0, num_tasks=1, total_items=1,
                   avg_concurrent_pickers=1.0, picking_pct=0.0, traveling_pct=0.0)])
    assert _rows(path, 'SELECT work_day, released_late FROM batch_stats WHERE run_id=?',
                 (rid,)) == [{'work_day': 0, 'released_late': 0.0}]
