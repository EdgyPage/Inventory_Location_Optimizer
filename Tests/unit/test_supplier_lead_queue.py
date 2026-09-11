"""test_supplier_lead_queue.py — the supplier lead is served at the ordering site, before the trailer.

`TrailerTransit.dispatch` used to take the SKU's `lead` (batches) and discard it: under the
pipeline every reorder loaded the instant it fired, so a catalogue whose SKUs carried a
supplier lead was declared AT that lead by the coverage record and then run without it.
"Declare the coverage against the inbound lead" (decision 2) made the two stages additive
-- the order waits its SUPPLIER lead at the ordering site, then rides the trailer's own
transit -- and "Chain the supplier lead before the trailer" built it: the flag-off batch
countdown (`BatchTransit`: advance one per `check_reorders`, release at zero) now sits in
FRONT of the trailer's FIFO next-fit loading.

What this file pins:

  1. THE CHAIN.  Lead 0 loads at fire time; lead k loads k drains later, onto the open
     trailer or a fresh one, with `dispatched_s` stamped at the drain it LOADED in -- so
     the trailer's lead starts after the supplier's, and the two add.
  2. FIRE ORDER.  Two orders releasing in the same batch load in the order they fired: a
     released order never queues behind that batch's lead-0 newcomer -- the tiebreak the
     flag-off `BatchTransit` has by construction, checked against it.
  3. THE REAL PHASES.  Driven through `check_reorders`, the lead read off the catalogue's
     `lead_time_mean`: the lead-0 SKU lands on drain 0, the lead-2 SKU on drain 2, on the
     trailer transit exactly as on the batch one.
  4. THE CENSUS.  Depth, merchandise and the snapshot rows count what waits at the site
     (the ledger credited it at fire); `advance` ticks it one batch; a negative remainder
     is a backlog, never clamped.
  5. BYTE-IDENTICAL AT LEAD 0 -- the archive's safety, proven two ways and DRAIN BY DRAIN,
     never as aggregates (memory `lockstep-tests-compare-aggregates-only`): a lockstep
     against a stand-in that dispatches the pre-chain way (`_load` IS the old body), and a
     digest of the whole per-drain record pinned from the last commit before the queue
     existed -- put-queue stream, ledgers, censuses, snapshot rows and every trailer stamp.

Run:  python -m pytest Tests/unit/test_supplier_lead_queue.py -q
"""
from __future__ import annotations

import hashlib

import pytest

from Inbound.dock import Dock, DockSpec
from Inbound.receiving import SiteReceiving
from Inbound.pack import packer
from Inbound.trailer import POSITION_VOLUME, Trailer28, Trailer53
from Inbound.transit import TrailerTransit, YardTransit
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.inventory_reorder import BatchTransit
from test_reorder_accounting import _carton, _small_warehouse
from test_trailer_pipeline import _order, _warehouse

#: One working day at the pilot's 8 h -- the era's drain spacing.
DAY_S = 480 * 60.0


# ── 1. the chain: lead 0 loads now, lead k loads k drains later ──────────────────

def test_lead_zero_loads_at_fire_time_and_lead_two_loads_two_drains_later():
    tr = TrailerTransit(Trailer53, lead_s=0.0)
    tr.dispatch(1, 2, 0, unit_volume=100, now_s=0.0)      # supplier lead 0: on a trailer now
    tr.dispatch(2, 3, 2, unit_volume=100, now_s=0.0)      # supplier lead 2: waits at the site
    assert tr.snapshot() == [(2, 3, 2), (1, 2, 1)]
    assert tr.depth == 2 and tr.merchandise() == 5
    # drain 0: the first arrives (trailer lead 0); the second is still at the site.
    assert tr.release(now_s=0.0) == [[1, 2, 0]]
    assert tr.snapshot() == [(2, 3, 2)] and tr.depth == 1 and tr.merchandise() == 3
    # drain 1: one tick closer, nothing loads.
    tr.advance()
    assert tr.release(now_s=DAY_S) == []
    assert tr.snapshot() == [(2, 3, 1)]
    # drain 2: released, loaded and arrived -- with nothing fired that batch to carry it.
    tr.advance()
    assert tr.release(now_s=2 * DAY_S) == [[2, 3, 0]]
    assert tr.snapshot() == [] and tr.depth == 0 and tr.merchandise() == 0


def test_a_released_order_is_dispatched_at_the_drain_it_loaded_in_not_the_one_it_fired_in():
    """The trailer's own lead starts when the order LOADS: dispatched at the release drain,
    arrived that much later -- the two stages add, exactly as the record prices them."""
    tr = YardTransit(Trailer53, lead_s=600.0, doors=2)
    tr.dispatch(5, 4, 1, unit_volume=100, now_s=0.0)
    tr.release(now_s=0.0)
    assert tr._at_site == [[5, 4, 1, 100]] and tr._in_transit == []
    # The yard's own census overrides count the site queue too.
    assert tr.depth == 1 and tr.merchandise() == 4 and tr.snapshot() == [(5, 4, 1)]
    tr.advance()
    tr.release(now_s=DAY_S)                    # loads here: dispatched at DAY_S, not at 0
    [t] = tr._in_transit
    assert (t.dispatched_s, t.lead_s, t.arrived_s) == (DAY_S, 600.0, None)
    tr.advance()
    tr.release(now_s=2 * DAY_S)
    assert tr._in_transit == [] and [y.arrived_s for y in tr._yard] == [DAY_S + 600.0]


def test_a_partial_release_keeps_the_rest_in_fire_order():
    """A lead 3 fired before a lead 1: after one tick only the lead 1 loads; the lead 3 is
    still first in the queue's rows and loads two ticks later, alone."""
    tr = TrailerTransit(Trailer53, lead_s=0.0)
    tr.dispatch(11, 1, 3, unit_volume=100, now_s=0.0)
    tr.dispatch(12, 1, 1, unit_volume=100, now_s=0.0)
    tr.release(now_s=0.0)
    tr.advance()
    assert tr.release(now_s=DAY_S) == [[12, 1, 0]]
    assert tr.snapshot() == [(11, 1, 2)]
    tr.advance()
    assert tr.release(now_s=2 * DAY_S) == []
    tr.advance()
    assert tr.release(now_s=3 * DAY_S) == [[11, 1, 0]] and tr.snapshot() == []


def test_lead_zero_never_enters_the_site_queue():
    tr = TrailerTransit(Trailer28, lead_s=0.0)
    for sku in (1, 2, 3):
        tr.dispatch(sku, 5, 0, unit_volume=100, now_s=0.0)
    assert tr._at_site == []


# ── 2. fire order: a released order never queues behind this batch's newcomer ─────

def test_two_orders_releasing_in_one_batch_load_in_fire_order():
    tr = TrailerTransit(Trailer53, lead_s=0.0)
    tr.dispatch(7, 2, 1, unit_volume=100, now_s=0.0)      # fired first; waits one batch
    tr.release(now_s=0.0)
    tr.advance()                                          # due at the next drain
    tr.dispatch(8, 2, 0, unit_volume=100, now_s=DAY_S)     # fired second, lead 0
    assert tr.depth == 1, 'both ride the one open trailer'
    assert tr.release(now_s=DAY_S) == [[7, 2, 0], [8, 2, 0]], 'loading order is fire order'
    # ...which is the tiebreak BatchTransit has by construction: same script, same order.
    bt = BatchTransit()
    bt.dispatch(7, 2, 1)
    bt.release()
    bt.advance()
    bt.dispatch(8, 2, 0)
    assert [e[:2] for e in bt.release()] == [[7, 2], [8, 2]]


def test_a_negative_remainder_is_a_backlog_never_clamped():
    """Two ticks with nothing draining: `-1`, as BatchTransit leaves it -- and the next
    release loads it once, not twice."""
    tr = TrailerTransit(Trailer53, lead_s=0.0)
    tr.dispatch(9, 1, 1, unit_volume=100, now_s=0.0)
    tr.advance()
    tr.advance()
    assert tr.snapshot() == [(9, 1, -1)]
    assert tr.release(now_s=DAY_S) == [[9, 1, 0]]
    assert tr.release(now_s=2 * DAY_S) == []


# ── 3. through the manager's real phases ──────────────────────────────────────────

def _reorder_manager(transit, leads: dict):
    """A real manager over `_carton` SKUs, each drained below its reorder point so the
    first `check_reorders` fires every one of them -- the lead read off `lead_time_mean`
    by `_fire_reorders`, never handed in."""
    mgr = _small_warehouse(0)
    mgr.transit = transit
    if getattr(transit, 'SOURCE', 'reorder') == 'trailer':
        mgr.packer = packer
    for sku, lt in leads.items():
        mgr.enqueue(_carton(sku, eq_qty=20, rp=8, lt=lt))
        mgr._current_quantities[sku] = 5           # drained below rp
        mgr._depleted_skus.add(sku)
    return mgr


def _reorder_drains(mgr, drains: int = 4) -> list:
    """Per `check_reorders`: what fired, the deferred ledger and the transit's rows."""
    out = []
    for d in range(drains):
        fired = mgr.check_reorders(now_s=d * DAY_S)
        out.append((fired, dict(mgr._deferred_qty), sorted(mgr.transit.snapshot())))
    return out


def test_through_check_reorders_lead_zero_lands_on_drain_0_and_lead_two_on_drain_2():
    """Four SKUs through the real port: leads 0 and 2, plus the quantization edge -- the
    ledger rounds `lead_time_mean` to batches (0.4 -> 0, 1.5 -> 2), and the trailer transit
    lands each SKU on the drain the batch transit does."""
    leads = {1: 0.0, 2: 2.0, 3: 0.4, 4: 1.5}
    trailer = _reorder_drains(_reorder_manager(TrailerTransit(Trailer53, lead_s=0.0), leads))
    batch = _reorder_drains(_reorder_manager(BatchTransit(), leads))
    assert trailer == batch, 'the trailer transit serves the supplier lead as the batch one does'
    fired, deferred, rows = trailer[0]
    assert sorted(fired) == [1, 2, 3, 4]
    assert deferred[1] == 0 and deferred[3] == 0, 'lead 0 (and a lead rounding to 0) landed'
    assert deferred[2] > 0 and deferred[4] > 0, 'a lead rounding to 2 waits like a lead of 2'
    assert rows == [(2, deferred[2], 2), (4, deferred[4], 2)]
    assert trailer[1][2] == [(2, deferred[2], 1), (4, deferred[4], 1)]
    assert trailer[2][1][2] == 0 and trailer[2][1][4] == 0 and trailer[2][2] == [], \
        'two drains later: landed'


# ── 4. byte-identical at lead 0: the lockstep and the archive's digest ────────────

class _DiscardingTransit(TrailerTransit):
    """The pre-chain `dispatch`: the lead ignored, the order loaded the instant it fires.
    `_load` IS the old body, so the two sides share every byte downstream of `dispatch` and
    this lockstep proves exactly one thing: at lead 0 the queue stays empty and its census
    terms sum to zero.  It is the DISCRIMINATOR for the digest test below, not the archive
    comparison -- that is the digest's job."""
    __slots__ = ()

    def dispatch(self, sku, qty, lead, unit_volume=None, now_s=None):
        self._load(sku, qty, unit_volume, now_s)


class _DiscardingYard(YardTransit):
    __slots__ = ()

    def dispatch(self, sku, qty, lead, unit_volume=None, now_s=None):
        self._load(sku, qty, unit_volume, now_s)


def _manager(transit, skus=(101, 102, 103)):
    mgr = Inventory_Manager(_warehouse())
    for sku in skus:
        mgr._originals[sku] = _order(sku)
    mgr.transit = transit
    mgr.packer = packer
    dock = Dock(DockSpec(size=2, sources=('reorder', 'trailer')))
    mgr.enable_receiving(dock)
    # The standing drain lives on the coordinator, so a manager that will be driven
    # through it needs one bound -- the same injection the driver does. Harmless on a
    # v1/BatchTransit manager, which never reaches the standing branch.
    mgr.receiving = SiteReceiving(dock, transit)
    return mgr


#: Five fired orders, one per drain: a full pup plus a remainder, a small lot, an odd
#: volume, and two repeats so lots merge and split the way a run's do.
PLAN = [(101, 15, POSITION_VOLUME), (102, 4, POSITION_VOLUME // 3), (103, 20, 900),
        (101, 6, POSITION_VOLUME), (102, 9, POSITION_VOLUME // 3)]


def _drains(mgr, lead: int = 0) -> list:
    """Five drains of the pipeline through the manager's phase wrappers, recorded per
    drain: the exact put-queue stream, the ledgers, the transit census and (standing
    yard) every trailer stamp, finished and censored."""
    per_drain = []
    for i, (sku, qty, vol) in enumerate(PLAN):
        epoch = i * DAY_S
        mgr._now_s = epoch
        mgr._advance_lead_queue()
        mgr.transit.dispatch(sku, qty, lead, unit_volume=vol, now_s=epoch)
        mgr._deferred_qty[sku] = mgr._deferred_qty.get(sku, 0) + qty
        plans = mgr._release_arrivals()
        mgr._receive(plans, None)
        stamps = (mgr.transit.drain_stamps() + mgr.transit.standing_stamps()
                  if getattr(mgr.transit, 'STANDING', False) else [])
        per_drain.append((
            [(p.source, p.age, p.unit.order.sku, p.unit.quantity) for p in mgr._stock_queue],
            dict(mgr._deferred_qty), dict(mgr._queued_qty), dict(mgr._queued_sku_counts),
            mgr.transit.depth, mgr.transit.merchandise(), mgr.transit.snapshot(), stamps,
        ))
    return per_drain


def _v1():
    return TrailerTransit(Trailer28, lead_s=0.0)


def _v1_old():
    return _DiscardingTransit(Trailer28, lead_s=0.0)


def _yard():
    return YardTransit(Trailer28, lead_s=600.0, lead_sigma=0.7, lead_seed=42, doors=2)


def _yard_old():
    return _DiscardingYard(Trailer28, lead_s=600.0, lead_sigma=0.7, lead_seed=42, doors=2)


@pytest.mark.parametrize('new, old', [(_v1, _v1_old), (_yard, _yard_old)], ids=['v1', 'yard'])
def test_at_lead_zero_the_chained_transit_is_lockstep_with_the_discarding_one(new, old):
    lhs, rhs = _drains(_manager(new())), _drains(_manager(old()))
    assert lhs == rhs, 'the supplier queue perturbed the lead-0 pipeline -- the archive is not safe'
    assert any(d[0] for d in lhs), 'the scenario placed nothing; it proves nothing'


#: What the pipeline BEFORE the supplier queue produced on the scenario above, drain by
#: drain (`_drains` over `_manager(_v1())` and `_manager(_yard())`), as one digest --
#: computed on 6eaf30fc, the last commit without `_at_site`, and pinned here so
#: "byte-identical at lead 0" is a comparison against the ARCHIVE's code, not only against
#: a stand-in.  It hashes everything the scenario touches: `Inbound/transit.py`, the packer,
#: the dock, `Trailer28`/`POSITION_VOLUME`, the `_order`/`_warehouse` fixtures borrowed
#: from `test_trailer_pipeline.py`, and (yard) numpy's `Generator` stream behind the lead
#: draw -- so a change to any of those moves it too.  If it moves while the stand-in
#: lockstep above still passes, the cause is upstream of the supplier queue.  Regenerate it
#: ONLY for a commit that deliberately breaks the pipeline's byte-identity, say so in that
#: commit, and use (from the repo root):
#:   python -c "import sys; sys.path[:0] = ['.', 'Tests/unit']; import test_supplier_lead_queue as t; print({n: t._digest(t._drains(t._manager(b()))) for n, b in (('v1', t._v1), ('yard', t._yard))})"
GOLDEN = {'v1': '3c704e27dbec59c9eb8dd4c7433614cde34ba39a665b0d90a3aa47654a245876',
          'yard': '0e4619981a0f2ad3a404b9d2ab47788764608e340a8b85b7d81931d597a802c1'}


def _digest(per_drain: list) -> str:
    return hashlib.sha256(repr(per_drain).encode()).hexdigest()


@pytest.mark.parametrize('name, build', [('v1', _v1), ('yard', _yard)])
def test_at_lead_zero_the_drain_by_drain_record_is_the_archives(name, build):
    assert _digest(_drains(_manager(build()))) == GOLDEN[name], (
        f"{name}: the drain-by-drain record is not the archive's -- if "
        f'test_at_lead_zero_the_chained_transit_is_lockstep_with_the_discarding_one still '
        f'passes, the cause is upstream of the supplier queue (the packer, the dock, the '
        f"trailer types, test_trailer_pipeline's fixtures or numpy's Generator stream); see "
        f'the GOLDEN note before regenerating')


# ── 5. a supplier lead moves the SAME scenario, by exactly its drains ─────────────

@pytest.mark.parametrize('build', [_v1, _yard], ids=['v1', 'yard'])
def test_a_supplier_lead_delays_every_load_by_that_many_drains(build):
    """The five-order script at supplier lead 1: each order rides the drain after the one
    it fired in, so the put-queue stream at drain d is the lead-0 stream at drain d-1
    (compared by provenance, SKU and quantity: the age is the drain's, by design).  On the
    yard the trailer's lead is still drawn per seq and seq is preserved -- trailer N is
    still the Nth to load -- so the shift holds there too, spread and all."""
    at0 = _drains(_manager(build()), lead=0)
    at1 = _drains(_manager(build()), lead=1)
    strip = lambda stream: [(src, sku, qty) for src, _age, sku, qty in stream]
    assert at1[0][0] == []
    for d in range(1, len(PLAN)):
        assert strip(at1[d][0]) == strip(at0[d - 1][0])
    # The site row leads the snapshot; on the yard a trailer may still ride its own lead.
    assert at1[-1][6][0] == (102, 9, 1), 'the last order is still at the site when the run ends'
