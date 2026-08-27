"""test_batch_stats_receiving_columns.py — the dock's four numbers survive the round trip.

Four columns on `batch_stats` rather than a `receiving_state` table. The table's case rests
on GRAIN — `put_queue_state` is per-`(batch, queue)` because there can be three put queues —
and there is exactly one dock, so per-batch and per-dock coincide. `batch_stats` already
carries a LEVEL (`queue_depth`) beside FLOWS (`reorder_placements`), and `queue_depth` is in
the same unit, so a reader sums the two for the whole unbinned backlog.

What is asserted here is the thing the immediately preceding feature got wrong: a column
written and never selected reads back as its Python default forever, on every run including
the ones holding real values. `work_day` and `released_late` shipped that way for three days,
and nothing could catch it — the schema id was right, the insert did not fail, and `0` is
also what a pre-column vintage legitimately returns.

So this file writes real non-zero values through the real insert and reads them back through
the real query. A test that only checked the DDL, or only checked the dataclass, would pass
against exactly that defect.

Run:  python -m pytest Tests/unit/test_batch_stats_receiving_columns.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from Optimization.persistence import Picking_Data as pd

_RECV = ('recv_depth', 'recv_unloaded', 'recv_cut', 'recv_seconds')


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / 'sim.db')
    pd.init_run_db(path)
    run_id = pd.create_run(path, 'comparison', {})
    return path, run_id


def _row(batch_id=0, **kw):
    r = pd.BatchStats(run_id=0, batch_id=batch_id, duration=100.0, num_tasks=2,
                      total_items=40, avg_concurrent_pickers=1.0,
                      picking_pct=0.5, traveling_pct=0.5)
    for k, v in kw.items():
        setattr(r, k, v)
    return r


# ── the declaration ───────────────────────────────────────────────────────────────

def test_all_four_are_on_the_dataclass_the_ddl_and_the_read_surface():
    """Three places, and the third is the one that shipped missing last time."""
    fields = pd.BatchStats.__dataclass_fields__
    for c in _RECV:
        assert c in fields, f'{c} is not on BatchStats'
        assert c in pd._CREATE_BATCH_STATS, f'{c} is not in the DDL'
        assert c in pd._BATCH_OPTIONAL, (
            f'{c} is written but not in _BATCH_OPTIONAL, so the query never selects it and '
            f'every reader gets its default — the exact defect work_day shipped with')
        assert c in pd._BATCH_COLS


def test_the_defaults_are_zero_and_correctly_typed():
    """A run with no receiving crew records four honest zeros, not NULLs. And `recv_seconds`
    defaults to `0.0`, not `0`: an int default on a REAL column makes a whole pandas column
    integral and truncates a later merge."""
    r = _row()
    assert (r.recv_depth, r.recv_unloaded, r.recv_cut, r.recv_seconds) == (0, 0, 0, 0.0)
    assert isinstance(r.recv_seconds, float)
    assert isinstance(pd._BATCH_OPTIONAL['recv_seconds'], float)
    for c in ('recv_depth', 'recv_unloaded', 'recv_cut'):
        assert isinstance(pd._BATCH_OPTIONAL[c], int)


# ── the round trip ────────────────────────────────────────────────────────────────

def test_real_values_survive_write_and_read(db):
    """THE regression. Non-zero on purpose: zeros would pass against a reader that never
    selects the column at all."""
    path, run_id = db
    pd.save_batch_stats(path, run_id, [
        _row(0, recv_depth=12, recv_unloaded=37, recv_cut=5, recv_seconds=418.75),
        _row(1, recv_depth=0, recv_unloaded=44, recv_cut=0, recv_seconds=502.5),
    ])
    got = pd.load_batch_stats(path, run_id)
    assert len(got) == 2
    assert (got[0].recv_depth, got[0].recv_unloaded,
            got[0].recv_cut) == (12, 37, 5)
    assert got[0].recv_seconds == pytest.approx(418.75)
    assert (got[1].recv_depth, got[1].recv_unloaded, got[1].recv_cut) == (0, 44, 0)
    assert got[1].recv_seconds == pytest.approx(502.5)


def test_a_zero_is_recorded_rather_than_omitted(db):
    """A run with no receiving crew must write four zeros, and they must read back as zeros
    — not as NULL, and not as a missing column that a consumer then has to guess about."""
    path, run_id = db
    pd.save_batch_stats(path, run_id, [_row(0)])
    con = sqlite3.connect(path)
    try:
        con.row_factory = sqlite3.Row
        r = dict(con.execute(
            f'SELECT {", ".join(_RECV)} FROM batch_stats WHERE run_id=?', (run_id,)).fetchone())
    finally:
        con.close()
    assert r == {'recv_depth': 0, 'recv_unloaded': 0, 'recv_cut': 0, 'recv_seconds': 0.0}
    assert all(v is not None for v in r.values())


def test_the_pre_existing_columns_are_untouched(db):
    """The four are additive. Every column that existed before must hold what it held."""
    path, run_id = db
    pd.save_batch_stats(path, run_id, [
        _row(0, queue_depth=9, items_demanded=55, work_day=3, released_late=41.5,
             recv_depth=12, recv_seconds=418.75)])
    got = pd.load_batch_stats(path, run_id)[0]
    assert (got.duration, got.num_tasks, got.total_items) == (100.0, 2, 40)
    assert (got.queue_depth, got.items_demanded, got.work_day) == (9, 55, 3)
    assert got.released_late == pytest.approx(41.5)


# ── what the columns MEAN ─────────────────────────────────────────────────────────

def test_the_two_depths_are_disjoint_halves_of_one_backlog():
    """`queue_depth` counts the put queues and the held items; `recv_depth` counts the dock.
    An item is in one or the other, never both, so a reader SUMS them. If that were ever
    false, every "total unbinned backlog" number would double-count."""
    import pathlib
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
    import calltree_scenarios as cs                                   # noqa: E402
    from Inbound.dock import Dock, DockSpec
    from Warehouse.layout.Storage_Primitive import viable_storage_units

    a = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=5, coverage=2.0, safety=0.4)
    a.mgr.enable_receiving(Dock(DockSpec(size=1)))
    n = 0
    for order in a.inventory.orders[:12]:
        for unit in viable_storage_units(order.reorder(), 4):
            a.mgr._admit(unit, 'reorder')
            n += 1

    assert a.mgr.dock_depth == n and a.mgr.queue_depth == 0
    a.mgr._receive(deadline=None)
    assert a.mgr.dock_depth == 0 and a.mgr.queue_depth == n
    assert a.mgr.dock_depth + a.mgr.queue_depth == n, 'the two depths overlap'
