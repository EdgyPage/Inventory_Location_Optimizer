"""test_sim_inventory_boundary.py — everything the pick layer says to the inventory manager.

A future inbound stream needs to watch picks happen: a forecaster of upcoming bin slots
has to know what was taken and when a bin ran dry. The good news is that the seam already
exists and is narrow — the whole of `Warehouse/picking/` reaches the manager through
exactly **three notifications** and **two reads**, and every one of those five is an
instance attribute, so an observer attaches by rebinding on the instance exactly as
`Optimization/metrics/bin_recorder.py::BinRecorder.attach` already does for the put-away
side.

So this commit adds no mechanism. Adding a listener list beside a working, proven
monkey-patch would be a second way to do one thing, and this repo already carries one
piece of validated-but-unconsumed infrastructure it regrets. What it adds is the thing
that was actually missing: the boundary written down, and a test that fails when it
widens.

Modelled on `test_bin_mutation_sites.py`, which does the same job for bin writes.

Two categories, deliberately separate:

  NOTIFICATIONS — the pick layer telling the manager something happened. These are the
  observation points. A fourth one is a real design change and should be argued for.

  READS — the pick layer reaching into manager state to build its work. These are an
  encapsulation leak (both touch a private index) and are listed so they stay visible
  rather than accumulating. They are NOT observation points.

Run:  python -m pytest Tests/architecture/test_sim_inventory_boundary.py -q
"""
from __future__ import annotations

import ast
import os
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PICKING = _ROOT / 'Warehouse' / 'picking'

#: attribute -> why the pick layer calls it.  THE observation surface.
NOTIFICATIONS = {
    '_notify_pick': 'a SKU was depleted by qty — the reorder trigger',
    '_notify_bin_emptied': 'a bin ran dry, with the absolute second it happened',
    '_apply_picks_batch': 'the legacy sim\'s batched form of the two above',
}

#: attribute -> why the pick layer reads it.  NOT observation points; an encapsulation
#: leak kept visible.  `Task.from_batch` walks the manager's private per-SKU bin indexes to
#: decide which bin each line is picked from — routing logic that reads placement state.
READS = {
    '_sku_singleton_bins': 'Task.from_batch drains singleton bins before pallet bins',
    '_sku_pallet_bins': 'Task.from_batch, the pallet half of the same decision',
}


def _manager_touches() -> dict:
    """{attribute: [(file, line)]} for every manager attribute `Warehouse/picking/` uses.

    Walked with `ast`: the receiver is named `mgr`, `manager`, or `self._manager`
    depending on the module, and a text scan would either miss one or catch the prose.
    """
    found: dict[str, list] = {}
    for path in sorted(_PICKING.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            recv = node.value
            is_mgr = (
                (isinstance(recv, ast.Name) and recv.id in ('mgr', 'manager'))
                or (isinstance(recv, ast.Attribute) and recv.attr == '_manager')
            )
            if is_mgr:
                found.setdefault(node.attr, []).append((path.name, node.lineno))
    return found


# ── the boundary is exactly what is declared ─────────────────────────────────────

def test_the_pick_layer_touches_no_manager_attribute_that_is_not_declared():
    touched = set(_manager_touches())
    declared = set(NOTIFICATIONS) | set(READS)
    extra = touched - declared
    assert not extra, (
        f'the pick layer reaches the manager through {sorted(extra)}, which nothing '
        f'declares. If it is a NOTIFICATION it is a new observation point and belongs in '
        f'NOTIFICATIONS with a reason; if it is a READ it widens an encapsulation leak '
        f'and belongs in READS. Either way it is a design decision, not an import.')


def test_every_declared_touch_is_still_real():
    """A stale entry here would licence a boundary that has already moved."""
    touched = set(_manager_touches())
    stale = (set(NOTIFICATIONS) | set(READS)) - touched
    assert not stale, f'declared but no longer used by the pick layer: {sorted(stale)}'


def test_the_scan_is_not_vacuous():
    touched = _manager_touches()
    assert len(touched) >= 5, f'the ast walk found only {sorted(touched)}'
    assert '_notify_pick' in touched


# ── the three notifications are attachable ───────────────────────────────────────

def test_every_notification_is_an_instance_attribute_an_observer_can_rebind():
    """This is the whole reason no listener list is needed. `BinRecorder.attach` already
    wraps the put-away side by rebinding two bound methods on the manager instance; the
    pick side works identically, and a slot forecaster attaches the same way."""
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    for name in NOTIFICATIONS:
        assert hasattr(Inventory_Manager, name), name
        # a plain function on the class, so `mgr.name = wrapper` shadows it per instance
        attr = getattr(Inventory_Manager, name)
        assert callable(attr) and not isinstance(attr, (staticmethod, property)), name


def test_the_established_attach_pattern_is_still_the_one_in_use():
    """If `BinRecorder` stops rebinding on the instance, the argument above evaporates and
    the pick side needs its own answer."""
    src = (_ROOT / 'Optimization' / 'metrics' / 'bin_recorder.py').read_text(encoding='utf-8')
    assert 'mgr._execute_placement =' in src
    assert 'mgr.requeue_bin =' in src


def test_no_listener_registry_was_added():
    """Guards the decision, not the code. A second mechanism beside a working one is how
    a codebase ends up with two half-used observer paths — and the repo already carries
    one piece of validated-but-unconsumed infrastructure it regrets (see the GPU broker).
    Delete this test deliberately if a registry is ever genuinely needed."""
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    banned = [n for n in dir(Inventory_Manager)
              if any(w in n.lower() for w in ('listener', 'subscribe', 'observer'))]
    assert not banned, f'a listener mechanism appeared: {banned}'


# ── the reads are a leak, and stay small ─────────────────────────────────────────

def test_the_pick_layer_reads_only_the_two_bin_indexes():
    """Both are private. `Task.from_batch` is doing routing off placement state, which is
    arguably the manager's job — recorded here so the leak cannot grow quietly while an
    inbound stream is being added beside it."""
    touched = _manager_touches()
    reads = {a for a in touched if a in READS}
    assert reads == set(READS)
    assert all(a.startswith('_') for a in READS), 'a public read would not be a leak'
