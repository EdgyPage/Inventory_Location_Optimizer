"""test_reorder_phases.py — `check_reorders` is seven phases, and the ORDER is the behaviour.

`check_reorders` used to be six unrelated jobs inline: advance the batch calendar, reclaim
the bins the picks emptied, tick the lead queue, fire reorders, release arrivals, drain the
put-away queue.  Receiving is the seventh, added when inbound got a crew of its own; it sits
between the arrivals and the put drain, because it is LABOUR and the put drain must see what
it unloaded.  There was no way to reclaim bins without also ordering, or to place the
stock queue without also advancing the calendar — which is exactly what a second work stream
(inbound put-away against the same clock) has to be able to do.

Splitting them is only safe if two things are pinned, and neither was before:

  1. **The sequence.** Reordering these changes results, quietly. Firing before the lead tick
     would decrement an order in the same batch it was placed; releasing before firing would
     delay every lead-0 arrival by a batch. A caller that drives the phases itself needs the
     canonical order written down somewhere that fails when it changes.
  2. **The calendar advances exactly once.** `_batch_num` is not just a counter — it is the
     per-reorder RNG key (`random.Random(f'{seed}:{sku}:{batch}')`), so a double tick silently
     redraws every reorder quantity in the run.

Run:  python -m pytest Tests/unit/test_reorder_phases.py -q
"""
from __future__ import annotations

import random

import pytest

from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig

#: the canonical sequence, in the order `check_reorders` must run them
PHASES = ('_tick_batch', 'reclaim_emptied_bins', '_advance_lead_queue',
          '_fire_reorders', '_release_arrivals', '_receive', '_drain_putaway')


def _manager(seed: int = 0) -> Inventory_Manager:
    """A tiny two-aisle warehouse — enough for a manager, no SKUs needed."""
    Aisle.next_aisle_id = 1          # class counter; leaks between tests otherwise
    random.seed(seed)                # Warehouse_Builder draws from the module-level random
    w, h = aisle_width_for(2), aisle_height_for(2)
    cfg = WarehouseConfig(
        total_aisles=2,
        aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'pallet', w, h, ['medium'], None),
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ],
    )
    return Inventory_Manager(Warehouse_Builder().from_config(cfg).build())


def _record_order(mgr, monkeypatch) -> list:
    """Replace every phase with a recorder that still runs the real one."""
    calls: list = []
    for name in PHASES:
        real = getattr(mgr, name)

        def spy(*a, _n=name, _r=real, **k):
            calls.append(_n)
            return _r(*a, **k)

        monkeypatch.setattr(mgr, name, spy)
    return calls


# ── 1. the sequence ──────────────────────────────────────────────────────────────

def test_check_reorders_runs_every_phase_exactly_once_in_order(monkeypatch):
    mgr = _manager()
    calls = _record_order(mgr, monkeypatch)
    mgr.check_reorders()
    assert calls == list(PHASES), (
        'the phase order changed. It is not cosmetic: firing before the lead tick '
        'decrements an order in the batch it was placed, and releasing before firing '
        'delays every lead-0 arrival by a batch.')


def test_the_lead_tick_precedes_the_firing():
    """The narrow property behind the sequence, stated on its own so a future reorder of
    the composition fails with a reason rather than a list diff."""
    assert PHASES.index('_advance_lead_queue') < PHASES.index('_fire_reorders')


def test_the_release_follows_the_firing():
    """A lead-0 reorder must arrive in the batch it was placed; that only works if
    `_release_arrivals` sees the record `_fire_reorders` just appended."""
    assert PHASES.index('_fire_reorders') < PHASES.index('_release_arrivals')


def test_the_drain_is_last():
    """It has to see both the arrivals released this batch and any straggler requeued by
    the reloader before it."""
    assert PHASES[-1] == '_drain_putaway'


# ── 2. the calendar ──────────────────────────────────────────────────────────────

def test_the_batch_calendar_advances_exactly_once_per_call():
    """`_batch_num` is the per-reorder RNG key, so a double tick redraws every quantity."""
    mgr = _manager()
    before = mgr._batch_num
    mgr.check_reorders()
    assert mgr._batch_num == before + 1


def test_the_calendar_is_the_reorder_rng_key():
    """Why the test above matters — asserted against the source, so the coupling cannot be
    removed without this test noticing that its own reason is gone."""
    import inspect
    from Warehouse.inventory import inventory_reorder
    src = inspect.getsource(inventory_reorder.ReorderMixin._fire_reorders)
    assert '_batch_num' in src and 'random.Random' in src


# ── 3. each phase stands alone ───────────────────────────────────────────────────

def test_reclaiming_bins_does_not_advance_the_calendar():
    """THE point of the split: the pick side fills `_pending_reclaim` and the reorder side
    drains it, and a caller must be able to do the second without the first."""
    mgr = _manager()
    before = mgr._batch_num
    mgr.reclaim_emptied_bins()
    assert mgr._batch_num == before


def test_draining_putaway_does_not_advance_the_calendar_or_order_anything():
    mgr = _manager()
    before, queued = mgr._batch_num, len(mgr._lead_queue)
    mgr._drain_putaway()
    assert mgr._batch_num == before
    assert len(mgr._lead_queue) == queued


def test_every_phase_is_callable_on_a_fresh_manager():
    """None of them may assume a predecessor ran — that assumption is what would make the
    split decorative."""
    for name in PHASES:
        getattr(_manager(), name)()


def test_the_phase_list_here_matches_the_composition():
    """A phase added to `check_reorders` and not to `PHASES` would leave every ordering
    assertion above passing while checking a subset.

    Matched on `self.<name>(` rather than `self.<name>()`: a phase may take arguments —
    `_drain_putaway` takes the day's whistle — and requiring the empty call would have made
    the ratchet fail on a phase it was still checking.
    """
    import inspect
    from Warehouse.inventory import inventory_reorder
    src = inspect.getsource(inventory_reorder.ReorderMixin.check_reorders)
    called = [n for n in PHASES if f'self.{n}(' in src]
    assert called == list(PHASES), f'composition calls {called}'
    # and nothing else that looks like a phase
    import re
    every = re.findall(r'self\.(_?\w+)\(', src)
    assert set(every) == set(PHASES), f'unexpected calls in the composition: {set(every) - set(PHASES)}'


# ── the whistle reaches the labour and nothing above it ───────────────────────────

#: Two sentinels, deliberately different. One shared value would let a crossed wire pass.
_PUT_WHISTLE = 1234.5
_RECV_WHISTLE = 6789.0


def test_each_labour_phase_gets_its_own_whistle_and_the_calendar_gets_none():
    """Two crews, two days, and neither reaches the calendar.

    The invariant is NOT "exactly one phase takes a deadline" — an earlier version of this
    test said that, and it was a statement about how many labour phases happened to exist.
    The real rule, in its own words then and now: **the day bounds the LABOUR, not the
    CALENDAR.** A lead time elapses whether or not anyone is at work, and a trailer that
    arrives at four o'clock has still arrived. If a whistle leaked into `_advance_lead_queue`
    or `_release_arrivals`, a short day would stop time itself rather than stopping a crew.

    Two DISTINCT sentinels are what make this stronger than the test it replaces. Handing
    receiving the put crew's remaining day is arithmetically well-formed and wrong by an
    unrelated crew's overrun, and it surfaces only as a cut count that reads like a
    legitimately short day. With one shared value that wire would pass; with two it fails
    here, which is the first place in the codebase that could see it.
    """
    mgr = _manager()
    seen = {}
    for name in PHASES:
        def cap(*a, _n=name, **kw):
            seen[_n] = (a, kw)
        setattr(mgr, name, cap)

    mgr.check_reorders(put_deadline=_PUT_WHISTLE, recv_deadline=_RECV_WHISTLE)

    assert set(seen) == set(PHASES), 'a phase was not called'
    assert seen['_drain_putaway'] == ((_PUT_WHISTLE,), {}), (
        'the put drain did not get the put crew\'s whistle')
    # `_receive` also takes this batch's arrivals, so its whistle is the LAST positional.
    assert seen['_receive'][0][-1] == _RECV_WHISTLE, (
        f"receiving got {seen['_receive'][0][-1]!r}, not its own whistle — if that is the "
        f"put crew's value, the two crews are sharing a day and receiving is being cut by "
        f"put-away's backlog")

    for name in PHASES:
        if name in ('_drain_putaway', '_receive'):
            continue
        assert seen[name] == ((), {}), (
            f'{name} is CALENDAR and was handed a whistle; a short day would stop time '
            f'rather than stopping a crew')


def test_the_two_whistles_are_not_the_same_sentinel():
    """Non-vacuity for the test above: if these ever became equal, a crossed wire would pass
    it silently and the strengthening would be undone without anything failing."""
    assert _PUT_WHISTLE != _RECV_WHISTLE


def test_both_deadlines_default_to_none():
    """Every caller that predates the day cut, and every run that does not ask for one."""
    mgr = _manager()
    got: dict = {}
    mgr._drain_putaway = lambda d=None: got.setdefault('put', d)
    mgr._receive = lambda a=(), d=None: got.setdefault('recv', d)
    mgr.check_reorders()
    assert got == {'put': None, 'recv': None}


def test_receiving_is_handed_this_batch_arrivals():
    """`_release_arrivals` returns the batch's LoadPlans and `_receive` is their consumer.
    Without this the return value is decorative and the phase counts no deliveries."""
    mgr = _manager()
    mgr._release_arrivals = lambda: ['plan-a', 'plan-b']
    got = []
    mgr._receive = lambda arrivals=(), deadline=None: got.append(list(arrivals))
    mgr.check_reorders()
    assert got == [['plan-a', 'plan-b']]
