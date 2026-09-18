"""test_checkpoint_buffer.py — every declared channel reaches the file, and close() writes.

`save_checkpoint_bundle` took sixteen parameters over a body that was fifteen lines of
`if rows: _insert_x(...)`, and its own docstring named the failure that shape invites:

> A bundle argument that is accepted and never inserted is this function's characteristic
> failure: `work_events` was one for a while, and the reconciliation that was supposed to
> catch it passed over 68 databases holding zero rows.

`CheckpointBuffer` makes that unexpressible — a channel IS its insert function — and this
file is the ratchet that keeps it so: it walks `CHANNELS`, writes one row down each, and
reads it back OFF A REAL FILE. A channel added without an insert fails here rather than
producing a silent zero, and `yard_drains`, which no test ever read back, is covered by
construction.

The second half is the design decision: **a run-end `close()` always writes**. The old form
guarded its run-end flush with `if pb:` — one of thirteen buffers standing in for "is there
an unflushed window" — and two writers had to be lifted out of it by hand, each with a
paragraph explaining why. Memory `run-end-writers-miss-the-final-flush` records the general
shape: the tail does not fire when `n_batches` divides the checkpoint cadence.

Run:  python -m pytest Tests/unit/test_checkpoint_buffer.py -q
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from Optimization.persistence.Picking_Data import (
    _WORK_EVENT_COLS, BatchStats, create_run, init_run_db)
from Optimization.persistence.checkpoint_buffer import (
    CHANNELS, SITE_CHANNELS, CheckpointBuffer)


#: One representative row per channel, in the shape its insert function unpacks. Written by
#: hand because the point is to exercise the REAL insert; a generated row would only prove
#: the buffer can call something.
def _row(channel: str, rid: int):
    return {
        'batch_stats':     BatchStats(run_id=rid, batch_id=1, duration=1.0, num_tasks=1,
                                      total_items=2, avg_concurrent_pickers=1.0,
                                      picking_pct=0.5, traveling_pct=0.5),
        'task_stats':      _Task(rid),
        'picker_events':   _Attrs(batch_id=1, picker_id=0, time=0.0, event_type='pick',
                                  aisle_id=3, bayX=1, bayY=2, sku=10, quantity=4,
                                  bins_completed=1, total_bins=2, items_picked=5,
                                  total_items=9, pick_travel_x=0.0, pick_travel_y=0.0,
                                  non_pick_travel_x=0.0, non_pick_travel_y=0.0,
                                  cart_move=0.0),
        'picks':           _Attrs(batch_id=1, picker_id=0, sim_time=0.0,
                                  aisle_id=3, bayX=1, bayY=2, sku=10, quantity=4),
        'bin_placements':  _Attrs(batch_id=1, seq=0, aisle_id=3, bayX=1, bayY=2, sku=10,
                                  qty=4, cause='reorder', bin_state='empty',
                                  unit_size='pallet', bin_size='small', score=1.0,
                                  score_rank=1, policy='uni_fifo_norsl'),
        'bin_evictions':   _Attrs(batch_id=1, seq=0, aisle_id=3, bayX=1, bayY=2,
                                  sku=10, qty=4),
        'aisle_metrics':   _Aisle(rid),
        # the one channel whose row is already a bare tuple: `_insert_work_events` is
        # `(run_id, *r)`, so the row IS `_WORK_EVENT_COLS` in order, built from it here so
        # that a column added to the contract fails this file rather than silently
        # shifting every value one place left.
        'work_events':     tuple(_WORK_EVENT_ROW[c] for c in _WORK_EVENT_COLS),
        'reorder_queue':   (1, 'stock', 10, 4, 0, 'pallet', 'small', 'store'),
        'put_queue_state': {'batch_id': 1, 'queue': 'store', 'depth': 2, 'oldest_age': 0.0,
                            'staging': 0, 'admitted': 1, 'placed': 1, 'blocked': 0,
                            'cart_swaps': 0, 'cut': 0},
        'carryover':       (1, 'unplaced', 10, 4),
        'yard_trailers':   (1, 0.0, 1.0, 2.0, 'emptied'),
        'yard_drains':     (1, 2, 4, 1, 0),
        'shift_days':      (0, 100.0, 90.0, 1, 0, 0, 0, 0, 0, 0, 90.0),
        'free_index':      (1, 'conveyable', 'food', 'small', 'pallet', 7),
        'site_receiving':  (1, 2, 3, 0, 4.0),
    }[channel]


class _Attrs:
    # A row read by ATTRIBUTE -- six of the sixteen inserts do, and a seventh
    # (`put_queue_state`) reads by KEY. A stub rather than the production record, so a field
    # the insert reads and the record stopped carrying shows up here as an AttributeError
    # instead of riding a default.
    def __init__(self, **fields):
        self.__dict__.update(fields)


#: `work_events` keyed by column, so the row can be BUILT from `_WORK_EVENT_COLS` rather than
#: counted out against it. Ticket 11 made that tuple the contract both ends read.
_WORK_EVENT_ROW = {
    'batch_id': 1, 'seq': 0, 't_abs': 0.0, 't_local': 0.0, 'shift_index': 0,
    'actor_uid': 'picker:0', 'actor_local': 0, 'role': 'picker', 'mode': 'foot',
    'event_type': 'pick', 'aisle_id': 3, 'sku': 10, 'qty': 4, 'duration': 1.0,
    'source': 'pick',
}


class _Task:
    """The fields `_insert_task_stats` reads. A stub rather than the dataclass, so a field
    added to the record without a column shows up here as an AttributeError."""

    def __init__(self, rid):
        (self.run_id, self.batch_id, self.aisle_id, self.picker_id) = rid, 1, 3, 0
        (self.task_start_time, self.task_end_time, self.duration) = 0.0, 5.0, 5.0
        (self.lift_sum, self.num_bins_visited, self.total_items) = 1.0, 2, 9
        (self.W, self.items_realized, self.bins_realized, self.is_outlier) = 1.0, 9, 2, False


class _Aisle:
    def __init__(self, rid):
        (self.run_id, self.batch_id, self.aisle_id) = rid, 1, 3
        (self.n_skus, self.n_bins, self.demand_sum) = 2, 4, 1.5


def _db():
    path = os.path.join(tempfile.mkdtemp(), 'sim.db')
    init_run_db(path)
    return path, create_run(path, 'uni_fifo_norsl')


def _count(path: str, table: str) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    finally:
        con.close()


# ── the ratchet ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('channel', [c for c, _f, _s in CHANNELS])
def test_every_channel_reaches_the_file(channel):
    """One row down each channel, read back off a REAL file.

    This is the assertion the 16-parameter bundle never had: `work_events` was accepted and
    never inserted, and the reconciliation meant to catch it passed over 68 databases holding
    zero rows. `yard_drains` was never read back by any test at all.
    """
    path, rid = _db()
    buf = CheckpointBuffer()
    buf.append(channel, _row(channel, rid))
    assert buf.pending(), f'{channel}: a row was added and pending() says no'
    buf.flush(path, rid)

    table = {'bin_placements': 'bin_placement', 'bin_evictions': 'bin_eviction'}.get(
        channel, channel)
    assert _count(path, table) == 1, (
        f'{channel}: one row went in and the file has none -- an accepted channel that '
        f'never inserts is exactly what this file exists to refuse')
    assert not buf.pending(), f'{channel}: flush did not clear'


def test_the_channel_set_is_the_writer_and_an_unknown_name_is_refused():
    """A row added to a name with no insert would be dropped silently, which is the same
    failure one level earlier."""
    buf = CheckpointBuffer()
    with pytest.raises(KeyError) as ei:
        buf.add('not_a_table', [(1,)])
    assert 'not_a_table' in str(ei.value)
    assert 'batch_stats' in str(ei.value), 'the refusal should name what it does know'


def test_pending_asks_every_channel_not_one():
    """`if pb:` was one of thirteen buffers used as a proxy for all of them, and two run-end
    writers had to be lifted out of that guard by hand."""
    buf = CheckpointBuffer()
    assert not buf.pending()
    buf.append('free_index', (1, 'conveyable', 'food', 'small', 'pallet', 7))
    assert buf.pending(), 'a channel that is not batch_stats does not register as pending'


# ── the decision ──────────────────────────────────────────────────────────────────

def test_close_writes_with_no_open_batch_window():
    """THE regression this object exists for.

    The final day's close-out and the censored yard tail are appended AFTER the last
    checkpoint flush, so under `if pb:` they were lost whenever `n_batches` divided the
    cadence. Here the window is empty of batch rows by construction and they still land.
    """
    path, rid = _db()
    buf = CheckpointBuffer()
    buf.append('shift_days', _row('shift_days', rid))
    buf.append('yard_trailers', _row('yard_trailers', rid))
    assert not buf.rows('batch_stats'), 'the fixture must have NO open batch window'

    buf.close(path, rid)
    assert _count(path, 'shift_days') == 1, "the final day's close-out was lost"
    assert _count(path, 'yard_trailers') == 1, 'the censored yard tail was lost'


def test_close_writes_even_with_nothing_pending():
    """`close()` is unconditional, which is the decision. A conditional close is the guard
    coming back under a different name."""
    path, rid = _db()
    CheckpointBuffer().close(path, rid)          # must not raise
    assert _count(path, 'batch_stats') == 0


def test_flush_is_the_one_that_may_skip():
    """The asymmetry, stated: `flush` is a window write and no-ops on an empty window;
    `close` is a run end and always writes."""
    path, rid = _db()
    buf = CheckpointBuffer()
    buf.flush(path, rid)                          # no-op, no rows
    assert _count(path, 'batch_stats') == 0


# ── the second adapter ────────────────────────────────────────────────────────────

def test_the_site_channel_set_is_a_real_second_adapter():
    """One table with one channel set would be a table, not a seam. `_SiteDock` kept parallel
    `_yt`/`_yd` accumulators feeding `save_site_inbound` -- a second implementation of this
    concept, one file over."""
    assert {c for c, _f, _s in SITE_CHANNELS} < {c for c, _f, _s in CHANNELS} | {'site_receiving'}
    assert 'site_receiving' in {c for c, _f, _s in SITE_CHANNELS}
    assert 'site_receiving' not in {c for c, _f, _s in CHANNELS}, (
        'the site channel set must differ from the leaf set, or it is not an adapter')

    path, rid = _db()
    buf = CheckpointBuffer(SITE_CHANNELS)
    for name, _fn, _skip in SITE_CHANNELS:
        buf.append(name, _row(name, rid))
    buf.close(path, rid)
    for name, _fn, _skip in SITE_CHANNELS:
        assert _count(path, name) == 1, f'{name}: the site set did not write it'


# ── the order is load-bearing ─────────────────────────────────────────────────────

def test_the_channel_order_is_the_historical_save_order():
    """Rowids depend on it: two tables written in the other order get different `id`
    sequences on the same rows, and `run_digest` orders by every included column.

    Pinned as a literal so a reordering is a deliberate edit here with a reason in the
    commit, not a diff someone skims.
    """
    assert [c for c, _f, _s in CHANNELS] == [
        'batch_stats', 'task_stats', 'picker_events', 'picks',
        'bin_placements', 'bin_evictions', 'aisle_metrics', 'work_events',
        'reorder_queue', 'put_queue_state', 'carryover',
        'yard_trailers', 'yard_drains', 'shift_days', 'free_index',
    ]


# ── the held connection, and the census ───────────────────────────────────────────
#
# The buffer opened a connection per flush until 2026-09-18. It now holds ONE per arm and
# releases it at `close`, which matters because a WAL database's close folds the whole log back
# into the main file and deletes it -- so the old shape paid a full fold every checkpoint. None
# of that was covered: `_connection`, `_release` and the census return appear in no other test,
# and `test_every_channel_reaches_the_file` above calls `flush` and never `close`, so it cannot
# see a connection-lifetime regression at all (`_count` opens its own reader).

def _opens(monkeypatch):
    """Count `_open_db` calls without changing what it returns."""
    from Optimization.persistence import checkpoint_buffer as cb
    n = {'v': 0}
    real = cb._pd._open_db

    def counted(path, *a, **kw):
        n['v'] += 1
        return real(path, *a, **kw)

    monkeypatch.setattr(cb._pd, '_open_db', counted)
    return n


def test_two_flushes_share_one_connection(monkeypatch):
    """The whole point of F4: N checkpoints, one open, one fold."""
    path, rid = _db()
    n = _opens(monkeypatch)
    buf = CheckpointBuffer()
    for b in (1, 2, 3):
        buf.append('batch_stats', _row('batch_stats', rid))
        buf.flush(path, rid)
    assert n['v'] == 1, f'three flushes opened {n["v"]} connections; they must share one'
    buf.close(path, rid)
    assert _count(path, 'batch_stats') == 3


def test_close_releases_the_connection():
    path, rid = _db()
    buf = CheckpointBuffer()
    buf.append('batch_stats', _row('batch_stats', rid))
    buf.flush(path, rid)
    assert buf._con is not None, 'a flush should leave the connection held'
    buf.close(path, rid)
    assert buf._con is None and buf._path is None, (
        'close must release, or the run-end index build opens a second writer')


def test_a_different_path_releases_the_first_connection():
    """Two files are never written down at once."""
    a, rid_a = _db()
    b, rid_b = _db()
    buf = CheckpointBuffer()
    buf.append('batch_stats', _row('batch_stats', rid_a))
    buf.flush(a, rid_a)
    first = buf._con
    buf.append('batch_stats', _row('batch_stats', rid_b))
    buf.flush(b, rid_b)
    assert buf._con is not first and buf._path == b
    buf.close(b, rid_b)
    assert _count(a, 'batch_stats') == 1 and _count(b, 'batch_stats') == 1


def test_a_failed_insert_releases_the_connection_and_reraises(monkeypatch):
    """The old per-flush `finally: close()` rolled back a half-open transaction for free.

    Reuse is what makes that something to do on purpose: a connection carried forward with an
    open transaction would poison the NEXT flush.
    """
    from Optimization.persistence import checkpoint_buffer as cb
    path, rid = _db()
    buf = CheckpointBuffer()
    buf.append('batch_stats', _row('batch_stats', rid))

    def boom(con, run_id, rows):
        raise RuntimeError('insert exploded')

    monkeypatch.setattr(cb._pd, '_insert_batch_stats', boom)
    # the channel tuple captured the real function at import, so patch the bound entry too
    buf._channels = tuple((n, boom if n == 'batch_stats' else f, s)
                          for n, f, s in buf._channels)
    with pytest.raises(RuntimeError, match='insert exploded'):
        buf.flush(path, rid)
    assert buf._con is None, 'a failed flush must not leave a half-open transaction held'


def test_flush_and_close_return_a_census_matching_what_was_written():
    """`rows=` on the log line is this dict summed; nothing else checks it."""
    path, rid = _db()
    buf = CheckpointBuffer()
    buf.append('batch_stats', _row('batch_stats', rid))
    buf.append('batch_stats', _row('batch_stats', rid))
    buf.append('aisle_metrics', _row('aisle_metrics', rid))
    census = buf.flush(path, rid)
    assert census == {'batch_stats': 2, 'aisle_metrics': 1}, census
    assert sum(census.values()) == 3
    assert buf.flush(path, rid) == {}, 'a no-op flush reports {}, not a zero'
    buf.close(path, rid)
