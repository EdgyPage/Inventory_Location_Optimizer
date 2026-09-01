"""test_yard_metrics.py — the yard's measurement surface, end to end and at its edges.

The build this file pins holds ONE claim above every other: **the database holds raw
stamps and nothing else**, so a finished run's fee axis can be re-reported under a
different `INBOUND_FEE_THRESHOLD_DAYS` without re-simulating.  Everything else here exists
to keep that claim true at the edges where measurement surfaces usually rot:

  * the CENSORED tail — trailers still on site when the run stops.  They are where an
    adversarial ordering concentrates its overage, so dropping them would report `lifo`'s
    fee as clipped rather than concentrated, inverting the arm's own signal.  Two tests:
    the transit emits them, and the runner's flush is not conditional on a batch window.
  * the FLAG-OFF shape — the tables exist on every run of this vintage and hold rows only
    with the standing yard on, so "no yard" and "an empty yard" stay distinguishable.
  * the LEVEL discipline — four columns that must never be summed across drains, and a
    binding-cut statistic that is a count of drains instead (the `recv_cut` scar, 101x on
    a published headline).

Run:  python -m pytest Tests/unit/test_yard_metrics.py -q
"""
from __future__ import annotations

import random
import sqlite3

import numpy as np
import pytest

from Inbound.dock import Dock, DockSpec
from Inbound.pack import packer
from Inbound.trailer import POSITION_VOLUME, Trailer28
from Inbound.transit import DISCARDED, DONE, STANDING, TrailerTransit, YardTransit
from Optimization.persistence import Picking_Data as PD
from Optimization.Performance_Evaluations.common.frames import _cdf, _ddf, _ydf
from Optimization.Performance_Evaluations.common.units import SECONDS_PER_DAY
from Warehouse.catalog.Demand import Demand
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Warehouse_Builder import (
    AisleConfig, Warehouse_Builder, WarehouseConfig)

DAY = float(SECONDS_PER_DAY)


# ── the fixture, borrowed verbatim in shape from test_standing_yard ───────────────

def _order(sku: int, weight: float = 2.0) -> Order:
    c = object.__new__(Order)
    c._sku = sku
    c.storage_type = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group = ('conveyable', 'food')
    c.length, c.width, c.height = 10, 10, 8
    c.weight = weight
    c.demand = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty = 20
    c.reorder_point = 10
    c.lead_time_mean = 0.0
    c.supply_cv = 0.0
    c.expected_batch_demand = 3.2
    return c


def _warehouse():
    Aisle.next_aisle_id = 1
    random.seed(0)
    w, h = aisle_width_for(4), aisle_height_for(6)
    cfg = WarehouseConfig(
        total_aisles=2,
        aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'pallet', w, h, ['medium', 'large'],
                        [0.5, 0.5]),
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ],
    )
    return Warehouse_Builder().from_config(cfg).build()


def _manager(transit, crew: int = 2, skus=(101, 102, 103)):
    mgr = Inventory_Manager(_warehouse())
    for sku in skus:
        mgr._originals[sku] = _order(sku)
    mgr.transit = transit
    mgr.packer = packer
    mgr.enable_receiving(Dock(DockSpec(size=crew, sources=('reorder', 'trailer'))))
    return mgr


def _dispatch(mgr, sku: int, qty: int, vol: int, epoch: float) -> None:
    mgr.transit.dispatch(sku, qty, 0, unit_volume=vol, now_s=epoch)
    mgr._deferred_qty[sku] = mgr._deferred_qty.get(sku, 0) + qty


def _drain(mgr, epoch: float, deadline=None):
    mgr._now_s = epoch
    mgr._receive(mgr._release_arrivals(), deadline)


# ── the transit's own two row sources ─────────────────────────────────────────────

def test_a_finished_trailer_carries_the_door_it_left_through():
    """`status` is STAMPED by the producer, not re-derived from the null pattern.

    Today `discarded` and `standing-but-never-staged` are the same nulls, so a reader
    inferring status from `staged_s IS NULL` would agree with this column — and would stop
    agreeing the day a staged trailer is dropped.  That is the whole argument for the
    column, so it is asserted directly rather than through a derivation that would pass
    either way.
    """
    tr = YardTransit(Trailer28, doors=4)
    mgr = _manager(tr)
    _dispatch(mgr, 101, 6, POSITION_VOLUME, 0.0)
    _drain(mgr, 0.0)
    rows = mgr.drain_yard_trailers()
    assert rows, 'a trailer that unloaded to empty recorded no stamp'
    for seq, arrived, staged, emptied, status in rows:
        assert status == DONE
        assert arrived is not None and staged is not None and emptied is not None
        assert arrived <= staged <= emptied
    # DRAINED, not read: a second call in the same batch must report nothing, or every
    # checkpoint after the first would re-insert the whole run's trailers.
    assert mgr.drain_yard_trailers() == []


def test_a_trailer_whose_plan_packs_nothing_is_discarded_unstaged():
    tr = YardTransit(Trailer28, doors=4)
    mgr = _manager(tr)
    _dispatch(mgr, 101, 6, POSITION_VOLUME, 0.0)
    # A packer that produces no plans is exactly the shortfall-to-zero case `discard`
    # exists for: there is no work to hold a door open for.
    mgr.packer = lambda rc, qty, deliveries: []
    _drain(mgr, 0.0)
    rows = mgr.drain_yard_trailers()
    assert rows and all(r[4] == DISCARDED for r in rows)
    assert all(r[2] is None for r in rows), 'a discarded trailer never took a door'
    assert all(r[3] is not None for r in rows), 'it still left the yard at a known instant'


def test_the_censored_tail_is_read_and_never_drained():
    """A trailer still on site at run end owes a row, and owes it more than once.

    `standing_stamps` is non-destructive on purpose: nothing has finished, so nothing may
    be forgotten.  A drained version would silently empty on a second checkpoint and the
    run-end flush would write nothing at all.
    """
    tr = YardTransit(Trailer28, doors=1)
    mgr = _manager(tr, crew=1)
    # Three trailers' worth against one door and a zero-length day: everything stands.
    for sku in (101, 102, 103):
        _dispatch(mgr, sku, 6, POSITION_VOLUME, 0.0)
    _drain(mgr, 0.0, deadline=0.0)
    first = mgr.standing_yard_trailers()
    assert first, 'trailers were left on site and none was reported'
    assert all(r[3] is None and r[4] == STANDING for r in first)
    assert mgr.standing_yard_trailers() == first, 'the censored tail was drained'


def test_a_transit_without_a_yard_answers_both_row_sources_with_nothing():
    """The v1 trailer transit has no standing surfaces and is not edited to gain them."""
    mgr = _manager(TrailerTransit(Trailer28, doors=4))
    _dispatch(mgr, 101, 6, POSITION_VOLUME, 0.0)
    _drain(mgr, 0.0)
    assert mgr.drain_yard_trailers() == []
    assert mgr.standing_yard_trailers() == []
    assert mgr.drain_yard_drains() == [], 'a run with no yard recorded a drain row'


def test_every_standing_drain_records_its_two_pairs():
    tr = YardTransit(Trailer28, doors=2)
    mgr = _manager(tr)
    _dispatch(mgr, 101, 6, POSITION_VOLUME, 0.0)
    _drain(mgr, 0.0)
    _drain(mgr, 10_000.0)                      # a quiet drain still records its levels
    rows = mgr.drain_yard_drains()
    assert len(rows) == 2, 'a drain that received nothing recorded no row'
    for yard_start, free_doors, yard_end, remainder in rows:
        assert 0 <= yard_start and 0 <= free_doors <= tr.doors
        assert yard_end >= 0 and remainder >= 0
    assert mgr.drain_yard_drains() == [], 'the drain levels were not reset'


def test_the_start_pair_is_frozen_before_the_door_fill():
    """Contention is a question about the MOMENT OF CHOICE.

    After the door fill there is nothing left to choose, so a `yard_start` read at drain
    end would be zero on exactly the drains where the yard bound hardest — the measurement
    would be silently anti-correlated with the thing it measures.
    """
    tr = YardTransit(Trailer28, doors=1)
    mgr = _manager(tr, crew=1)
    for sku in (101, 102, 103):
        _dispatch(mgr, sku, 6, POSITION_VOLUME, 0.0)
    _drain(mgr, 0.0, deadline=0.0)
    (yard_start, free_doors, yard_end, _rem), = mgr.drain_yard_drains()
    assert yard_start >= 2, 'the frozen census did not see the standing trailers'
    assert free_doors == tr.doors, 'the first drain froze with a door already taken'
    assert yard_end < yard_start, 'the door fill left the end level unchanged'


# ── the DB round trip: the rows must come back OUT of the file ────────────────────

def _fresh_db(tmp_path):
    path = str(tmp_path / 'sim.db')
    PD.init_run_db(path)
    run_id = PD.create_run(path, 'test')
    return path, run_id


def test_the_tables_exist_on_every_run_and_hold_rows_only_with_a_yard(tmp_path):
    """The declared shape is flag-INDEPENDENT; the rows are not.

    An inbound-off run must read as an empty table rather than a missing one — otherwise
    every consumer has to tell a schema question from a configuration one, which is the
    negotiation the capability registry exists to spare them.
    """
    path, run_id = _fresh_db(tmp_path)
    with sqlite3.connect(path) as con:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'yard_trailers', 'yard_drains'} <= names
    assert PD.load_yard_trailers(path, run_id) == []
    assert PD.load_yard_drains(path, run_id) == []


def test_the_bundle_writes_both_yard_tables_and_reads_them_back(tmp_path):
    """A bundle argument accepted and never inserted is this writer's characteristic
    failure — `work_events` was one over 68 databases holding zero rows.  So the file is
    read back, through the same named queries the analysis uses."""
    path, run_id = _fresh_db(tmp_path)
    PD.save_checkpoint_bundle(
        path, run_id,
        batch_stats=[], task_stats=[], picker_events=[], picks=[],
        bin_placements=[], bin_evictions=[], aisle_metrics=[], reorder_queue=[],
        yard_trailers=[(0, 100.0, 200.0, 900.0, DONE),
                       (1, 150.0, None, 300.0, DISCARDED)],
        yard_drains=[(0, 2, 4, 1, 7)])
    trailers = PD.load_yard_trailers(path, run_id)
    assert [t['seq'] for t in trailers] == [0, 1]
    assert trailers[0]['status'] == DONE and trailers[0]['emptied_s'] == 900.0
    assert trailers[1]['staged_s'] is None, 'a never-staged trailer came back non-NULL'
    (drain,) = PD.load_yard_drains(path, run_id)
    assert (drain['batch'], drain['yard_start'], drain['free_doors_start'],
            drain['yard_end'], drain['staged_remainder_end']) == (0, 2, 4, 1, 7)


def test_a_standing_trailer_later_emptied_corrects_its_own_row(tmp_path):
    """The PK makes a re-write a CORRECTION rather than a duplicate.

    Not reachable in a clean run — the tail is read once, at the end — but a re-run of an
    arm over an existing DB is, and a second `standing` row for one trailer would double
    that arm's whole fee.
    """
    path, run_id = _fresh_db(tmp_path)
    PD.save_yard_trailers(path, run_id, [(3, 100.0, 200.0, None, STANDING)])
    PD.save_yard_trailers(path, run_id, [(3, 100.0, 200.0, 800.0, DONE)])
    (row,) = PD.load_yard_trailers(path, run_id)
    assert row['status'] == DONE and row['emptied_s'] == 800.0


# ── derive-late: the whole point of raw stamps ────────────────────────────────────

_ROWS = [
    # emptied a day and a half after arrival — over a 1.0 d threshold, under a 2.0 d one
    {'seq': 0, 'arrived_s': 0.0, 'staged_s': 0.5 * DAY, 'emptied_s': 1.5 * DAY,
     'status': DONE},
    # never staged, gone within the hour
    {'seq': 1, 'arrived_s': 0.0, 'staged_s': None, 'emptied_s': 3600.0,
     'status': DISCARDED},
    # STILL STANDING at a run end of 4 days
    {'seq': 2, 'arrived_s': 1.0 * DAY, 'staged_s': None, 'emptied_s': None,
     'status': STANDING},
]
_RUN_END = 4.0 * DAY


def test_the_same_rows_report_a_different_fee_under_a_different_threshold():
    """THE re-report property, and the reason no overage column exists.

    One set of stamps, two thresholds, two fee totals — with no simulation in between.  A
    stored overage would have pinned the run to whichever threshold it happened to run
    under, and the calibration probe ("where does the threshold sit for a nonzero,
    non-saturated overage?") would have been a sweep instead of a re-read.
    """
    loose = _ydf(_ROWS, _RUN_END, threshold_days=2.0)
    tight = _ydf(_ROWS, _RUN_END, threshold_days=1.0)
    assert float(loose['overage_days'].sum()) == pytest.approx(1.0)   # only the censored
    assert float(tight['overage_days'].sum()) == pytest.approx(2.5)
    assert int(loose['over_threshold'].sum()) == 1
    assert int(tight['over_threshold'].sum()) == 2


def test_a_censored_trailer_is_bounded_by_the_run_end_not_dropped():
    df = _ydf(_ROWS, _RUN_END, threshold_days=2.0)
    row = df[df['seq'] == 2].iloc[0]
    assert bool(row['censored'])
    assert float(row['detention_days']) == pytest.approx(3.0)
    # It has held a door for no measurable time yet, and saying "zero" would let it into
    # the utilization numerator as a trailer that used a door for nothing.
    assert np.isnan(float(row['door_span_days']))
    # And it is IN the fee: this is the row an adversarial ordering concentrates into.
    assert float(row['overage_days']) == pytest.approx(1.0)


def test_the_spans_a_never_staged_trailer_cannot_have_are_nan_not_zero():
    df = _ydf(_ROWS, _RUN_END, threshold_days=2.0)
    row = df[df['seq'] == 1].iloc[0]
    assert np.isnan(float(row['yard_wait_days']))
    assert np.isnan(float(row['door_span_days']))
    assert float(row['detention_days']) > 0.0, 'it was still held while it was here'


def test_an_empty_trailer_frame_still_has_its_columns():
    """A denied render and a crashed one look identical in a log line."""
    df = _ydf([], 0.0, threshold_days=2.0)
    assert df.empty
    for col in ('detention_days', 'overage_days', 'over_threshold', 'censored'):
        assert col in df


# ── the drain frame: levels, and the one derived indicator ────────────────────────

def test_a_binding_cut_counts_drains_and_never_sums_levels():
    """The additive statistic over the binding-cut pair is a COUNT OF DRAINS.

    Summing `yard_end` instead would count one trailer once per batch it waits — the
    identical mistake `recv_cut` made, which published a headline 101x wrong.  The two
    numbers are constructed here to differ, so a regression to the sum cannot pass.
    """
    rows = [
        {'batch': 0, 'yard_start': 3, 'free_doors_start': 0, 'yard_end': 2,
         'staged_remainder_end': 0},
        {'batch': 1, 'yard_start': 2, 'free_doors_start': 0, 'yard_end': 2,
         'staged_remainder_end': 5},
        {'batch': 2, 'yard_start': 0, 'free_doors_start': 4, 'yard_end': 0,
         'staged_remainder_end': 0},
    ]
    df = _ddf(rows)
    assert int(df['binding_cut'].sum()) == 2
    assert int(df['yard_end'].sum()) == 4, 'the fixture no longer separates the two'


def test_a_remainder_alone_is_a_binding_cut():
    """Units left on a staged trailer are unserved inbound work even with an empty yard —
    the drain ran out of day or crew partway through a load."""
    df = _ddf([{'batch': 0, 'yard_start': 0, 'free_doors_start': 3, 'yard_end': 0,
                'staged_remainder_end': 4}])
    assert bool(df['binding_cut'].iloc[0])


# ── demand service: the fold is reason-SELECTIVE ──────────────────────────────────

def _bframe(demanded):
    import pandas as pd
    return pd.DataFrame({'batch_id': list(demanded),
                         'items_demanded': [demanded[b] for b in demanded]})


def test_a_day_cut_is_not_a_missed_sale():
    """`unpicked_daycut` is a STAFFING fact.  Counting it here would let a longer working
    day read as better inbound, which is a conclusion about the wrong lever entirely."""
    rows = [
        {'batch_id': 0, 'reason': 'unpicked_unstocked', 'sku': 1, 'qty': 4},
        {'batch_id': 0, 'reason': 'unpicked_unavailable', 'sku': 2, 'qty': 6},
        {'batch_id': 0, 'reason': 'unpicked_daycut', 'sku': 3, 'qty': 90},
        # a put-side LEVEL sharing the same physical column — never additive with the rest
        {'batch_id': 0, 'reason': 'unplaced', 'sku': 4, 'qty': 50},
    ]
    df = _cdf(rows, _bframe({0: 100}))
    assert int(df['missed_pieces'].iloc[0]) == 10
    assert float(df['missed_share'].iloc[0]) == pytest.approx(10.0)


def test_a_batch_that_demanded_nothing_is_unmeasured_not_perfect():
    df = _cdf([{'batch_id': 0, 'reason': 'unpicked_unstocked', 'sku': 1, 'qty': 0}],
              _bframe({0: 0}))
    assert np.isnan(float(df['missed_share'].iloc[0]))


def test_a_batch_with_no_missed_rows_scores_zero_rather_than_vanishing():
    """Perfect service is a RESULT.  A batch dropped for having no carryover row would
    make an arm's mean share the mean over its worst batches only."""
    df = _cdf([], _bframe({0: 100, 1: 100}))
    assert list(df['batch_id']) == [0, 1]
    assert list(df['missed_pieces']) == [0, 0]
