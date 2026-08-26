"""test_carryover_spans_the_dock.py — `carryover_rows` and `queue_contents` agree again.

Two surfaces answer the same question — what merchandise is standing unbinned at the end of
this batch — and until now they disagreed. `queue_contents` emitted a `'dock'` kind;
`carryover_rows` did not, so anyone sizing the backlog from `carryover` silently missed every
unit still on a trailer. On a 200-batch run that was 52,479 rows denied.

The decision taken was to add the dock AND keep the two classes visible, because they are not
the same problem:

  placement failure   'unplaced' / 'held' — was offered a bin (or floor space) and did not
                      get one. A scheduling or capacity problem.
  pre-placement       'dock' — never offered anything. A receiving-throughput problem.

So this file pins BOTH halves of that: the dock is present (or the change did nothing), and it
is still distinguishable (or the change destroyed the column's meaning).

Non-vacuity matters more than usual here. Every assertion below is written so it fails if the
dock rows are absent, and the disjointness check is the one that would catch the real hazard —
double-counting a unit as both docked and unplaced, which would inflate the very backlog number
this change exists to make correct.

Run:  python -m pytest Tests/integration/test_carryover_spans_the_dock.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

from Warehouse.inventory.dock import DockSpec
from Warehouse.layout.Storage_Primitive import viable_storage_units

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402


def _assets(*, receiving=True, size=1, n_skus=200):
    a = cs.build_assets(n_skus=n_skus, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=11, coverage=2.0, safety=0.4)
    if receiving:
        a.mgr.enable_receiving(DockSpec(size=size))
    return a


def _arrive(a, n_orders=30, qty=4):
    """Real reorder arrivals through the real release path. Returns units admitted."""
    n = 0
    for order in a.inventory.orders[:n_orders]:
        for unit in viable_storage_units(order.reorder(), qty):
            a.mgr._admit(unit, 'reorder')
            n += 1
    return n


def _by_reason(rows):
    out: dict = {}
    for _b, reason, _sku, qty in rows:
        out[reason] = out.get(reason, 0) + qty
    return out


# ── the dock is there ─────────────────────────────────────────────────────────────

def test_a_loaded_dock_produces_dock_carryover_rows():
    """THE regression. Before the change this returned an empty list for a dock holding
    every unit that had arrived."""
    a = _assets()
    n = _arrive(a)
    assert n > 0 and a.mgr.dock_depth == n, 'the fixture put nothing on the dock'

    rows = a.mgr.carryover_rows(batch_id=3)
    assert rows, 'a loaded dock produced no carryover rows at all'
    assert _by_reason(rows).get('dock', 0) > 0, (
        f'the dock holds {a.mgr.dock_depth} units and carryover reported none of them; '
        f'reasons present: {sorted(_by_reason(rows))}')
    assert all(r[0] == 3 for r in rows), 'the batch_id was not stamped onto every row'


def test_the_dock_quantity_is_merchandise_pieces_not_storage_units():
    """`carryover.qty` counts PIECES while `dock_depth` counts UNITS, and this row is written
    beside `unplaced` rows that already count pieces. Getting it wrong would put two units of
    account in one column — which is exactly the defect just fixed in `per_run`."""
    a = _assets()
    _arrive(a)
    pieces = sum(it.unit.quantity for it in a.mgr._dock.items)
    got = _by_reason(a.mgr.carryover_rows(0))['dock']
    assert got == pieces, f'reported {got} against {pieces} merchandise pieces on the dock'
    assert pieces != a.mgr.dock_depth, (
        'the fixture has one piece per unit, so this test cannot tell the two units of '
        'account apart — raise the qty per storage unit')


# ── and it is still distinguishable ───────────────────────────────────────────────

def test_docked_and_unplaced_are_separate_reasons_and_do_not_overlap():
    """The whole point of keeping the classes visible: a reader must be able to ask "what did
    placement fail to do" without the trailer backlog in the answer."""
    a = _assets()
    # Unload one wave into the put queues, then land a second on the dock, so BOTH classes
    # are non-empty at the same instant -- with only one populated the separation below
    # would hold trivially.
    _arrive(a, n_orders=30)
    a.mgr._receive(deadline=None)
    _arrive(a, n_orders=15)
    assert a.mgr.dock_depth > 0, 'the second wave did not reach the dock'

    by = _by_reason(a.mgr.carryover_rows(0))
    placement = by.get('unplaced', 0) + by.get('held', 0)
    assert by.get('dock', 0) > 0, f'no dock rows; reasons present: {sorted(by)}'
    assert placement > 0, (
        'nothing is in a put queue, so this test cannot show the classes are separable')
    assert by['dock'] != placement, (
        'the two classes carry the same quantity, so a reader could not tell which number '
        'this test actually verified')


def test_no_sku_is_reported_under_two_reasons_at_once():
    """The hazard the primary key cannot catch. `(batch, reason, sku)` is unique by
    construction once the reasons differ, so a unit counted as BOTH docked and unplaced would
    write two perfectly legal rows and double the backlog."""
    a = _assets()
    _arrive(a)
    a.mgr._receive(deadline=None)
    for order in a.inventory.orders[30:60]:
        for unit in viable_storage_units(order.reorder(), 4):
            a.mgr._admit(unit, 'reorder')

    rows = a.mgr.carryover_rows(0)
    by = _by_reason(rows)
    assert by.get('dock', 0) > 0 and (by.get('unplaced', 0) + by.get('held', 0)) > 0, (
        'both classes must be populated or the overlap check proves nothing')

    total = sum(qty for _b, _r, _s, qty in rows)
    standing = (sum(it.unit.quantity for it in a.mgr._dock.items)
                + sum(it.unit.quantity for q in a.mgr.put_queues for it in q.items)
                + sum(it.unit.quantity for it in a.mgr._held))
    assert total == standing, (
        f'carryover totals {total} against {standing} pieces actually standing — a unit is '
        f'being counted under two reasons, or one is missing entirely')


def test_the_keys_are_unique_so_the_flush_will_not_raise():
    """`_insert_carryover` RAISES on a duplicate `(batch, reason, sku)` in one flush. Adding a
    third producer to this list is exactly the change that could reintroduce that collision,
    so it is checked here rather than discovered in a run."""
    from Optimization.persistence.Picking_Data import _insert_carryover

    a = _assets()
    _arrive(a)
    a.mgr._receive(deadline=None)
    for order in a.inventory.orders[30:60]:
        for unit in viable_storage_units(order.reorder(), 4):
            a.mgr._admit(unit, 'reorder')

    rows = a.mgr.carryover_rows(7)
    keys = [(b, r, s) for b, r, s, _q in rows]
    assert len(keys) == len(set(keys)), 'carryover_rows emitted a duplicate key itself'

    # ...and prove the guard would have caught it, so the check above is not decorative
    with pytest.raises(ValueError, match='two rows'):
        _insert_carryover(None, 1, list(rows) + [rows[0]])


# ── the no-op is still a no-op ────────────────────────────────────────────────────

def test_a_run_with_no_receiving_crew_reports_no_dock_reason():
    """No dock object exists, so there is nothing to walk and nothing to emit. Every run
    before this feature must produce byte-identical carryover rows."""
    a = _assets(receiving=False)
    n = _arrive(a)
    assert a.mgr._dock is None and n > 0

    by = _by_reason(a.mgr.carryover_rows(0))
    assert 'dock' not in by, f'a dockless manager emitted dock rows: {by}'
    assert (by.get('unplaced', 0) + by.get('held', 0)) > 0, (
        'nothing was standing at all, so the absence of dock rows proves nothing')
