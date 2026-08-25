"""test_putaway_provenance.py — where a queued unit came from is declared, not inferred.

`_stock_queue` held bare `StorageUnit`s, fed identically by initial intake
(`enqueue`/`enqueue_all`), reorder arrivals (`_release_to_stock`) and reloader evictions
(`requeue_bin`). Once a unit was in, its origin was gone — so `BinRecorder` recovered
`'reslot'` by holding a set of `id(unit)` and testing membership when the placement landed.

That worked, and only for reasons nothing stated: `requeue_bin` re-queues the IDENTICAL
object, and that object stays alive in the queue, so its id cannot be recycled underneath
the set. Neither is a property any declaration enforces, and a trailer id — the fourth
source — could not have been carried that way at all.

Pinned here:

  1. every enqueue site names a source, checked by scanning the source rather than by
     exercising each path, so a NEW enqueue site cannot slip in untagged;
  2. the vocabulary is closed and an unknown source raises at construction;
  3. an item is immutable, and `respawn` — used by the repack and singleton rescues — is
     the one legal derivation, carrying the origin to every unit split out of a parent;
  4. `_execute_placement` accepts the source and ignores it, so a direct caller that knows
     nothing about the queue is unaffected;
  5. `BinRecorder` reads the declared source instead of its `id()` set, while keeping
     `_in_batch_loop` as the arbiter of intake-vs-reorder — those are two different
     questions and merging them would mislabel a batch-0 reorder as initial.

Run:  python -m pytest Tests/unit/test_putaway_provenance.py -q
"""
from __future__ import annotations

import ast
import inspect
import re

import pytest

from Warehouse.inventory import Inventory_Management as im
from Warehouse.inventory import inventory_reorder as ir
from Warehouse.inventory.inventory_common import PUTAWAY_SOURCES, PutawayItem


class _Unit:
    """Stands in for a StorageUnit — the envelope never looks inside one."""
    __slots__ = ('order', 'quantity')

    def __init__(self, quantity=1):
        self.quantity = quantity
        self.order = None


# ── 1. every enqueue site names a source ─────────────────────────────────────────

def test_only_admit_puts_a_new_unit_on_the_queue():
    """Source-scanned rather than path-exercised: the risk is a NEW site added later, and
    a behavioural test only covers the paths someone remembered to write.

    Tightened when put-away items gained an arrival age. A source is no longer the only
    thing a producer can forget: an unstamped item enters at age -1 and, once anything
    orders by age, sorts wherever -1 happens to fall. `_admit` is the single admission point
    that stamps both, so the question is no longer "does this line mention PutawayItem" but
    "is this line inside `_admit`". The exceptions are RE-entries rather than admissions —
    the two rescues push `respawn(...)`, which inherits the parent's stamp, and the ranked
    drain requeues items it already popped, either whole (`items`) or one at a time through
    `by_unit[...]` when a straggler finds no bin.
    """
    src_im = inspect.getsource(im)
    admit_lines = set()
    for node in ast.walk(ast.parse(src_im)):
        if isinstance(node, ast.FunctionDef) and node.name == '_admit':
            admit_lines = set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    assert admit_lines, '_admit is gone — this ratchet no longer guards anything'

    pattern = re.compile(r'self\._stock_queue\.(append(left)?|extend)\(')
    bare = []
    for mod, src in ((im, src_im), (ir, inspect.getsource(ir))):
        for lineno, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if not pattern.match(stripped):
                continue
            if mod is im and lineno in admit_lines:
                continue                                    # the admission point itself
            if any(t in stripped for t in ('respawn(', 'items', 'by_unit[')):
                continue                                    # re-entry, already stamped
            bare.append(f'{mod.__name__}:{lineno}  {stripped}')
    assert not bare, ('a unit is entering the put-away queue outside _admit, so it carries '
                      'no declared origin and no arrival stamp:\n' + '\n'.join(bare))


def test_the_three_sources_are_all_used():
    """A vocabulary entry nothing produces is a vocabulary entry that will rot."""
    src = inspect.getsource(im) + inspect.getsource(ir)
    for source in PUTAWAY_SOURCES:
        assert f"'{source}'" in src, f'nothing ever enqueues with source={source!r}'


# ── 2. the vocabulary is closed ──────────────────────────────────────────────────

def test_an_unknown_source_is_refused_at_construction():
    with pytest.raises(ValueError, match='unknown put-away source'):
        PutawayItem(_Unit(), 'trailer')      # the fourth source — not declared yet


def test_the_default_is_intake():
    assert PutawayItem(_Unit()).source == 'intake'


# ── 3. immutable, and respawn is the one derivation ──────────────────────────────

def test_an_item_cannot_be_relabelled_in_flight():
    item = PutawayItem(_Unit(), 'reorder')
    with pytest.raises(AttributeError, match='immutable'):
        item.source = 'reslot'
    with pytest.raises(AttributeError, match='immutable'):
        item.unit = _Unit()


def test_respawn_carries_the_origin_to_a_split_unit():
    """The repack and singleton rescues split one unit into several; a rescued reorder is
    still a reorder."""
    parent = PutawayItem(_Unit(9), 'reorder')
    child = parent.respawn(_Unit(3))
    assert child.source == 'reorder'
    assert child.unit is not parent.unit
    assert isinstance(child, PutawayItem)


def test_both_rescues_respawn_rather_than_pushing_a_bare_unit():
    """The two `appendleft` sites inside `_stock_per_unit`. If either pushed a raw unit the
    queue would hold two shapes and the next `popleft` would fail on `.unit`."""
    src = inspect.getsource(im.Inventory_Manager._stock_per_unit)
    pushes = re.findall(r'self\._stock_queue\.appendleft\((.+?)\)', src)
    assert len(pushes) == 2, f'expected the two rescues, found {pushes}'
    assert all(p.startswith('item.respawn(') for p in pushes), pushes


# ── 4 & 5. the chokepoint and the recorder ───────────────────────────────────────

def test_execute_placement_takes_the_source_as_an_ignorable_keyword():
    sig = inspect.signature(im.Inventory_Manager._execute_placement)
    p = sig.parameters.get('source')
    assert p is not None, 'the one call every put-away funnels through cannot see its origin'
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    assert p.default is None, 'a caller that knows nothing about the queue must still work'


def test_the_recorder_reads_the_declared_source_not_an_identity_set():
    from Optimization.metrics import bin_recorder
    src = inspect.getsource(bin_recorder.BinRecorder.attach)
    assert "source == 'reslot'" in src, 'reslot is no longer taken from the declaration'
    assert 'in self._evicted_units' not in src, \
        'the id()-membership test is back; that is the inference this replaced'


def test_the_recorder_still_separates_intake_from_reorder_by_the_loop_flag():
    """Deliberately NOT taken from the source. Initial stocking is 'intake' at the queue,
    but the recorder's question is WHEN it was placed — and a batch-0 reorder must not be
    labelled 'initial'."""
    from Optimization.metrics import bin_recorder
    src = inspect.getsource(bin_recorder.BinRecorder.attach)
    assert "'reorder' if self._in_batch_loop else 'initial'" in src


def test_the_recorder_forwards_the_source_to_the_real_method():
    """It wraps by instance-rebinding, so dropping the kwarg would silently stop the
    manager seeing it — and nothing downstream would notice."""
    from Optimization.metrics import bin_recorder
    src = inspect.getsource(bin_recorder.BinRecorder.attach)
    assert 'orig_place(unit, bin_, source=source)' in src


def test_every_wrapper_of_the_chokepoint_passes_the_keyword_through():
    """Three modules rebind `_execute_placement` on a manager instance. One of them lives
    in Diagnostics and has no test of its own — it broke on the first run of this change
    and nothing but this check would have caught it."""
    import pathlib
    root = pathlib.Path(inspect.getfile(im)).parents[2]
    wrappers = {
        'Optimization/metrics/bin_recorder.py',
        'Diagnostics/trace_lifecycle.py',
        'Tests/bench/bin_log_harness.py',
    }
    for rel in sorted(wrappers):
        text = (root / rel).read_text(encoding='utf-8')
        assert 'mgr._execute_placement =' in text, f'{rel} no longer wraps the chokepoint'
        defs = re.findall(r'def _execute_placement\(([^)]*)\)', text)
        assert defs, rel
        assert any('source' in d or '**kw' in d for d in defs), \
            f'{rel} rebinds _execute_placement without accepting its source keyword'
