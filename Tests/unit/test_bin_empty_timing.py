"""test_bin_empty_timing.py — the pick loop knows when a bin ran dry; now it says so.

`_PickMutation` carried `(bin_ref, sku, qty)` and no time, and `_pending_reclaim` was a
bare list of bins, so "which slots freed up, and when" was unrecoverable without
re-deriving it from the event stream. Phase 2 makes that worse than it sounds: it applies
mutations in PICKER order, not time order, so the order bins empty in tells you nothing
about when they did.

The stamp now has its consumer: the standing yard's space timeline harvests it inside
`_reclaim_empty_bins` (the one moment the stamps and the bins meet before both are
wiped) into the frozen `SpaceView` the dock's decisions read. The reclaim itself still
happens once, at the top of the next batch. What is pinned here is that the stamp is
*available*, *correct*, and read at exactly that one place.

Both sims are checked. They are kept byte-for-byte in lockstep on timing by
`test_scheduler` and `test_travel_decomposition`, and a stamp that only one of them
produced would be a fourth way for them to drift.

Run:  python -m pytest Tests/unit/test_bin_empty_timing.py -q
"""
from __future__ import annotations

import ast
import inspect
import random

import pytest

from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Aisle_Dimensions import aisle_height_for, aisle_width_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Warehouse.picking import Pick, fast_pick


def _mgr(seed: int = 0) -> Inventory_Manager:
    Aisle.next_aisle_id = 1
    random.seed(seed)
    w, h = aisle_width_for(2), aisle_height_for(2)
    cfg = WarehouseConfig(
        total_aisles=2, aisle_splits=[0.5, 0.5],
        aisle_configs=[
            AisleConfig('conveyable', 'food', 'pallet', w, h, ['medium'], None),
            AisleConfig('conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ])
    return Inventory_Manager(Warehouse_Builder().from_config(cfg).build())


def _a_bin(mgr):
    return next(iter(mgr._index[next(iter(mgr._index))]))


# ── the stamp is carried ─────────────────────────────────────────────────────────

def test_the_deferred_mutation_carries_the_pick_time():
    m = fast_pick._PickMutation(bin_ref=object(), sku=1, qty=2, time=41.5)
    assert m.time == 41.5


def test_the_stamp_defaults_so_an_older_caller_still_constructs():
    assert fast_pick._PickMutation(bin_ref=object(), sku=1, qty=2).time == 0.0


def test_fast_pick_stamps_the_mutation_with_the_picks_own_clock():
    """`t` at the moment the pick completed — not the task's start, not the batch end."""
    src = inspect.getsource(fast_pick._simulate_picker_deferred)
    body = src[src.index('mutations.append('):]
    assert 'time=t' in body[:200], 'the mutation is stamped with something other than t'
    # and the append must come AFTER the pick's own time has been added
    assert src.index('t             += _pick_time') < src.index('mutations.append(')


def test_phase_two_passes_the_stamp_to_the_manager():
    src = inspect.getsource(fast_pick.DeferredPickSimulation.run)
    assert '_notify_bin_emptied(bin_, at=mut.time)' in src, (
        'Phase 2 applies mutations in PICKER order, so if it drops the stamp here the '
        'instant a bin emptied is gone for good')


def test_the_reference_sim_stamps_its_empties_too():
    """A stamp only one sim produced would be a fourth way for the lockstep to drift."""
    src = inspect.getsource(Pick.PickSimulation._simulate_picker)
    assert 'empties.append((bin_, time))' in src


# ── the manager records it ───────────────────────────────────────────────────────

def test_notify_records_the_stamp_against_the_bin():
    mgr = _mgr()
    b = _a_bin(mgr)
    mgr._notify_bin_emptied(b, at=12.25)
    assert mgr._emptied_at[id(b)] == 12.25
    assert b in mgr._pending_reclaim


def test_a_caller_with_no_clock_is_still_accepted():
    """The stamp is metadata, not a precondition — a test or a legacy path may have none."""
    mgr = _mgr()
    b = _a_bin(mgr)
    mgr._notify_bin_emptied(b)
    assert b in mgr._pending_reclaim
    assert id(b) not in mgr._emptied_at


def test_the_batched_path_accepts_both_shapes():
    """`_apply_picks_batch` is the legacy sim's hook and a public-ish notification point."""
    mgr = _mgr()
    bins = list(mgr._index[next(iter(mgr._index))])[:2]
    mgr._apply_picks_batch([], [(bins[0], 3.5), bins[1]])
    assert mgr._emptied_at[id(bins[0])] == 3.5
    assert id(bins[1]) not in mgr._emptied_at
    assert bins[0] in mgr._pending_reclaim and bins[1] in mgr._pending_reclaim


def test_the_stamps_expire_with_the_bins_they_describe():
    """They describe exactly what is in `_pending_reclaim`; without this the map grows by
    one entry per emptied bin for the length of the run."""
    mgr = _mgr()
    b = _a_bin(mgr)
    mgr._notify_bin_emptied(b, at=9.0)
    assert mgr._emptied_at
    mgr.reclaim_emptied_bins()
    assert mgr._emptied_at == {}
    assert mgr._pending_reclaim == []


def test_the_reclaim_loop_still_iterates_bare_bins():
    """`_reclaim_empty_bins` is a ~7k-iteration loop with every attribute hoisted out of
    it. The stamps went in a parallel map precisely so an unpack did not land in there."""
    src = inspect.getsource(Inventory_Manager._reclaim_empty_bins)
    assert 'for bin_ in self._pending_reclaim:' in src


def test_the_id_key_is_justified_by_bin_lifetime():
    """A Bin is owned by the Warehouse for the whole run and never collected, so its id is
    a stable key. A StorageUnit is not — which is why put-away provenance rides in a
    PutawayItem rather than a map like this one. The distinction is the whole reason both
    choices are defensible, so it is stated where someone will read it."""
    src = inspect.getsource(Inventory_Manager.__init__)
    assert '_emptied_at' in src
    assert 'never' in src and 'PutawayItem' in src, \
        'the id-key justification is no longer recorded beside the declaration'


# ── it is read at exactly one place ──────────────────────────────────────────────

def test_reclaim_harvest_is_the_one_legal_reader_of_the_stamp():
    """Replaces `test_nothing_reads_the_stamp_to_make_a_decision_yet`, exactly as that
    test's docstring said to once something read the stamp. The reader is the space
    timeline's reclaim-harvest in `_reclaim_empty_bins` — the one moment the stamps and
    the bins meet before both are wiped. A second read site in `Warehouse/` means
    someone is consuming the stamp outside the harvest contract (a per-decision rescan,
    or a decision made from un-harvested state) and must be argued for here.

    Walked with `ast` rather than grepped: the declaration and the write sites all
    mention the name in prose as well as in code, and a text scan cannot tell an
    explanatory docstring from a load.
    """
    import pathlib
    root = pathlib.Path(inspect.getfile(Inventory_Manager)).parents[2]
    reads_by_function = []
    reads_anywhere = 0
    for path in sorted((root / 'Warehouse').rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        # every write target and every `.clear()` receiver, by node identity
        written = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Subscript) and _is_stamp(t.value):
                        written.add(id(t.value))
            elif isinstance(node, ast.AnnAssign) and _is_stamp(node.target):
                written.add(id(node.target))
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'clear' and _is_stamp(node.func.value)):
                written.add(id(node.func.value))
        reads_anywhere += sum(1 for node in ast.walk(tree)
                              if _is_stamp(node) and id(node) not in written)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if _is_stamp(node) and id(node) not in written:
                    reads_by_function.append((path.name, fn.name))
    assert reads_by_function, (
        'nothing reads the stamp any more — the reclaim-harvest seam is gone')
    assert set(reads_by_function) == {('inventory_reorder.py', '_reclaim_empty_bins')}, (
        'the stamp is read outside the reclaim-harvest: '
        + ', '.join(f'{f}:{fn}' for f, fn in sorted(set(reads_by_function))))
    assert reads_anywhere == len(reads_by_function), (
        'a module-level read escaped the per-function attribution above')


def _is_stamp(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == '_emptied_at'
            and isinstance(node.value, ast.Name) and node.value.id == 'self')
