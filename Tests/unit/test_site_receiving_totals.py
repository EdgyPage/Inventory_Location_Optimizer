"""test_site_receiving_totals.py — the SITE dock's own per-batch totals, end to end.

Site-dock 15 section 7 asked for the site's receiving total to be written PER BATCH, and
site-dock 25 builds it as `site_receiving`, the third table of a coupled unit's
`<pair>/_site/inbound_<arm-pair>.db`.  It exists to be the other side of a closure: each
leaf's `batch_stats.recv_*` is that leaf's SHARE of a site day, and this is the dock's own
counter for the same day, accrued on different code.  `receiving_report.reconcile_pair` is
what compares them; this file pins the three links that have to hold for that comparison to
mean anything.

  * **The coordinator parks one row per site day, from the dock's OWN counters** — and
    parks it only AFTER the per-channel decomposition closes, so a total nobody could
    account for is a crash rather than a row.
  * **The driver collects it every batch and refuses a day index that is not the batch it
    is collecting.**  One batch IS one site day on a coupled run (the put pool refuses every
    other grid); a silently re-indexed row would file the site's day-3 labour under batch 1
    and put the closure check's per-batch localisation onto the wrong batch for the whole
    run.
  * **The writer puts the rows in the FILE.**  The site rows went through
    `save_site_inbound` until ticket 07 and now ride `SITE_CHANNELS`; both forms have the same
    characteristic failure, a row accepted and never inserted — the reconciliation that was
    supposed to catch one once passed over 68 databases holding zero rows — so every assertion
    here reads the database back rather than the argument.

Run:  python -m pytest Tests/unit/test_site_receiving_totals.py -q
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

from Optimization.persistence import Picking_Data as pdata
from Optimization.persistence.checkpoint_buffer import (
    SITE_CHANNELS, CheckpointBuffer, write_rows)
from Optimization.simdriver import strategy_runner as sr

from Tests.unit.test_site_receiving import _day, _mixed_trailer, _site


# ══════════════════════════════════════════════════════════════════════════════
# 1. the coordinator parks the site day's own totals
# ══════════════════════════════════════════════════════════════════════════════

def _one_site_day(crd, store, ful, index: int = 0):
    """Drive one site day far enough to partition it: arrivals, a drain, then the shares."""
    _, deadline = crd.open_batch(index)
    _mixed_trailer(crd, store, ful)
    # THE DAY'S OWN DEADLINE, not None: the coordinator refuses a drain gated on a different
    # day than the epoch its rows are stamped from.
    crd.receive([store, ful], deadline)
    return [crd.snapshot_for(leaf) for leaf in (store, ful)]


def test_the_site_day_is_parked_with_the_docks_own_counters():
    """One row per partitioned day, stamped with the coordinator's OWN index, carrying the
    dock's `snapshot()` tuple — not a sum of the shares, which is what would make the
    closure a restatement instead of a check."""
    crd, store, ful = _site(day=_day())
    shares = _one_site_day(crd, store, ful, index=0)
    assert sum(s[3] for s in shares) > 0.0, 'nothing was received; the row proves nothing'

    rows = crd.drain_site_totals()
    assert len(rows) == 1, rows
    day, depth, unloaded, cut, seconds = rows[0]
    assert day == 0
    assert unloaded == sum(s[1] for s in shares) > 0
    assert seconds == pytest.approx(sum(s[3] for s in shares))
    assert (depth, cut) == (sum(s[0] for s in shares), sum(s[2] for s in shares))
    assert crd.drain_site_totals() == [], 'the list did not start over; rows will compound'


def test_a_day_that_does_not_close_parks_no_row():
    """The row is recorded AFTER the decomposition's closure assertions, so a site total the
    channels cannot account for raises rather than reaching the site DB. A row parked first
    would survive the crash in a resumed run and be read as a day that balanced."""
    crd, store, ful = _site(day=_day())
    _, deadline = crd.open_batch(0)
    _mixed_trailer(crd, store, ful)
    crd.receive([store, ful], deadline)
    # break the decomposition the way a lost handoff would: one leaf's own receiving
    # seconds vanish while the dock still holds the site total.
    store._recv_seconds = 0.0
    with pytest.raises(RuntimeError, match='never saw'):
        crd.snapshot_for(store)
    assert crd.drain_site_totals() == [], 'a day that did not close was parked anyway'


def test_the_totals_and_the_yard_rows_are_separate_drains():
    """Two site-scoped lists with the same cadence and different content. Collecting one and
    not the other is the compounding failure `drain_site_rows` already warns about."""
    crd, store, ful = _site(day=_day())
    _one_site_day(crd, store, ful)
    assert crd.drain_site_rows(), 'no yard row; the drain did not run'
    assert crd.drain_site_totals(), 'no dock total for a day that drained'


# ══════════════════════════════════════════════════════════════════════════════
# 2. the driver collects every batch, and refuses a mis-indexed day
# ══════════════════════════════════════════════════════════════════════════════

class _FakeCoord:
    """The three drains `_SiteDock.collect` reads, and nothing else."""

    def __init__(self, totals):
        self._totals = list(totals)

    def drain_trailer_stamps(self):
        return []

    def drain_site_rows(self):
        return [(1, 2, 3, 4)]

    def drain_site_totals(self):
        out, self._totals = self._totals, []
        return out


def _site_dock(tmp_path, totals):
    dock = object.__new__(sr._SiteDock)
    dock.coord = _FakeCoord(totals)
    dock.log = logging.getLogger('site-totals-test')
    dock.db_path = str(tmp_path / 'inbound_a__b.db')
    dock.arm_pair = 'a__b'
    dock._run_id = None
    # The three lists are VIEWS onto the dock's own CheckpointBuffer (ticket 07), so this
    # fixture builds one rather than three bare lists -- a fixture that kept bare lists
    # would exercise a dock whose `finish` writes nothing.
    dock._buf = CheckpointBuffer(SITE_CHANNELS)
    dock._yt = dock._buf.rows('yard_trailers')
    dock._yd = dock._buf.rows('yard_drains')
    dock._sr = dock._buf.rows('site_receiving')
    return dock


def test_collect_stamps_the_batch_and_keeps_the_dock_totals(tmp_path):
    d = _site_dock(tmp_path, [(0, 1, 2, 3, 4.0)])
    d.collect(0)
    assert d._sr == [(0, 1, 2, 3, 4.0)]
    assert d._yd == [(0, 1, 2, 3, 4)]


def test_finish_writes_the_dock_totals_it_collected(tmp_path):
    """THE WHOLE CHAIN on the driver's side: collect, then finish, then read the FILE.

    Two separate ways this goes silently wrong and neither raises: `finish`'s early return
    counts only the yard lists, so a batch that unloaded without finishing a trailer writes
    nothing at all; or the rows are collected and never handed to the writer, which is the
    channel's characteristic failure seen from the caller's side.
    """
    d = _site_dock(tmp_path, [(0, 0, 4, 0, 30.4)])
    d.coord.drain_site_rows = lambda: []          # no yard row this batch
    d.coord.standing_trailer_stamps = lambda: []
    d.collect(0)
    assert (d._yt, d._yd) == ([], []), 'the yard lists are not empty; the test is weaker'
    d.finish()
    assert _read_back(d.db_path) == [(0, 0, 4, 0, 30.4)]


def test_a_day_index_that_is_not_the_batch_is_refused(tmp_path):
    """One batch IS one site day, and this is the one place that structural promise is READ
    rather than restated. A row silently re-stamped with the collecting batch would file the
    site's labour under a batch it did not happen in."""
    d = _site_dock(tmp_path, [(3, 1, 2, 3, 4.0)])
    with pytest.raises(RuntimeError, match='one batch IS one site day|site day 3'):
        d.collect(1)
    assert d._sr == [], 'the refused row was appended anyway'


# ══════════════════════════════════════════════════════════════════════════════
# 3. the writer puts them in the FILE
# ══════════════════════════════════════════════════════════════════════════════

def _read_back(path: str) -> list:
    con = sqlite3.connect(path)
    try:
        return con.execute('SELECT batch, recv_depth, recv_unloaded, recv_cut, '
                           'recv_seconds FROM site_receiving ORDER BY batch').fetchall()
    finally:
        con.close()


def _site_file(tmp_path, name='inbound_a__b.db'):
    path = str(tmp_path / name)
    pdata.init_run_db(path)
    return path, pdata.create_run(path, 'site', identity={'strategy_key': 'a__b'})


def test_the_rows_are_written_and_read_back(tmp_path):
    """A row accepted and never inserted is the characteristic failure of every writer this
    table has had, so this reads the database rather than the call."""
    path, run_id = _site_file(tmp_path)
    rows = [(0, 1, 10, 0, 76.5), (1, 0, 12, 3, 91.25)]
    write_rows(path, run_id, channels=SITE_CHANNELS, yard_trailers=[], yard_drains=[],
               site_receiving=rows)
    assert _read_back(path) == rows


def test_a_run_with_only_dock_totals_still_writes(tmp_path):
    """A coupled day that unloaded without finishing a trailer produces a dock total and no
    trailer stamp. The early return has to count all three lists or that day is dropped."""
    path, run_id = _site_file(tmp_path)
    write_rows(path, run_id, channels=SITE_CHANNELS, yard_trailers=[], yard_drains=[],
               site_receiving=[(0, 0, 4, 0, 30.4)])
    assert _read_back(path) == [(0, 0, 4, 0, 30.4)]


def test_a_second_row_for_one_batch_raises(tmp_path):
    """A PLAIN INSERT where every neighbour uses `INSERT OR REPLACE`, and the difference is
    the decision: this table has one producer writing once at run end, so a duplicate batch
    is two site days collected under one index rather than a re-flush. `INSERT OR REPLACE`
    would keep the second and drop the first — memory `carryover-two-producers-one-key`, a
    level and a flow sharing a key, where 500 units vanished."""
    path, run_id = _site_file(tmp_path)
    write_rows(path, run_id, channels=SITE_CHANNELS, yard_trailers=[], yard_drains=[],
               site_receiving=[(0, 0, 4, 0, 30.4)])
    with pytest.raises(sqlite3.IntegrityError):
        write_rows(path, run_id, channels=SITE_CHANNELS, yard_trailers=[], yard_drains=[],
                   site_receiving=[(0, 0, 9, 0, 99.9)])


def test_an_uncoupled_sim_db_has_the_table_and_no_rows(tmp_path):
    """EVERY sim DB gains the table (it is one DDL), and every uncoupled one leaves it
    empty. That is the honest statement of "this run had no site", and it is why no earlier
    vintage is missing data — there was no site total to record, so nothing needs an
    optional fill or a per-vintage override."""
    path = str(tmp_path / 'sim_arm.db')
    pdata.init_run_db(path)
    pdata.create_run(path, 'comparison', {})
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='site_receiving'").fetchone()
        assert con.execute('SELECT COUNT(*) FROM site_receiving').fetchone()[0] == 0
    finally:
        con.close()
