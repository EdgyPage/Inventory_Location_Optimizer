"""test_lead_time_unit.py — the one stream still quantized to batches, on purpose.

The pick clock, put-away and `work_events.t_abs` all count SECONDS on a continuous axis.
The inbound pipeline does not: `lead_time_mean` is a count of BATCHES and
`_advance_lead_queue` ticks it once per `check_reorders`, so a reorder arrives at a batch
boundary rather than at an instant.

Two units in one simulator is the kind of thing that is either a decision or a bug, and the
difference has to be legible. It is a decision:

  * converting to seconds moves every restock result on every arm, and nothing consumes a
    finer-grained arrival today;
  * batch quantization is honest for a wave-picking model — replenishment lands between
    waves;
  * the trailer/dock feature is what makes an arrival a scheduled instant, and it should
    pick the representation with the trailer model in hand rather than inherit one chosen
    now.

This test exists so the decision cannot rot into an assumption. It fails if the tick stops
being one batch, which is the moment the note above needs rewriting.

Run:  python -m pytest Tests/unit/test_lead_time_unit.py -q
"""
from __future__ import annotations

import inspect

import pytest

from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.kernel.timeline import TIME_UNIT


def test_the_unit_is_declared_and_is_not_the_simulators():
    """Both halves matter: that it is stated, and that it differs from everything else."""
    assert Inventory_Manager.LEAD_TIME_UNIT == 'batches'
    assert TIME_UNIT == 'seconds'
    assert Inventory_Manager.LEAD_TIME_UNIT != TIME_UNIT


def test_the_tick_is_one_batch():
    src = inspect.getsource(Inventory_Manager._advance_lead_queue)
    assert 'entry[2] -= 1' in src, (
        'the lead tick is no longer a single decrement per batch — if it is now in seconds, '
        'update LEAD_TIME_UNIT, its note, and this test')


def test_one_check_reorders_advances_every_in_transit_order_by_exactly_one():
    m = Inventory_Manager.__new__(Inventory_Manager)
    m._lead_queue = [[1, 10, 3], [2, 5, 1], [3, 7, 0]]
    m._advance_lead_queue()
    assert [e[2] for e in m._lead_queue] == [2, 0, -1]


def test_an_order_can_go_past_zero_rather_than_clamping():
    """`_release_arrivals` releases on `<= 0`, so a negative is an order that arrived while
    nothing drained it — not an error, and clamping would hide the backlog."""
    m = Inventory_Manager.__new__(Inventory_Manager)
    m._lead_queue = [[1, 10, 0]]
    for _ in range(3):
        m._advance_lead_queue()
    assert m._lead_queue[0][2] == -3


def test_the_decision_is_written_down_where_the_tick_is():
    """A unit mismatch with no explanation beside it reads as an oversight, and the next
    person 'fixes' it into a whole-archive re-run."""
    src = inspect.getsource(Inventory_Manager._advance_lead_queue)
    assert 'LEAD_TIME_UNIT' in src
    mod = inspect.getsource(inspect.getmodule(Inventory_Manager._advance_lead_queue))
    i = mod.index('LEAD_TIME_UNIT')
    note = mod[max(0, i - 1800):i]
    for claim in ('QUANTIZED TO BATCHES', 'DELIBERATE', 'trailer'):
        assert claim in note, f'the note no longer explains {claim!r}'
