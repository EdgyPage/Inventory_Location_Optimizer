"""test_inventory_optimal_solver.py — the map says which branch it actually took.

`_optimal_work_assign` solves each BinKey class exactly (scipy LAP) when it is small
enough, and greedily otherwise.  The published site described the Map family as solving
"the full linear assignment problem", which at production catalogue size is a branch the
run rarely reaches — the large classes are orders of magnitude past the gate.  Nothing
recorded the split, and worse, a bare `except Exception: assigned = []` meant a missing
scipy or a solver crash degraded to greedy with no log line and no flag, indistinguishable
from the designed fallback.

Pinned here:

  1. a class under the gate takes the exact branch, and says so;
  2. a class over the gate takes greedy, and says WHICH gate term stopped it — `m_cnt` is
     only bounded above by 3n, so the two terms do not bind together;
  3. a solver that raises is recorded as `greedy_error` and LOGGED, never silently folded
     into the designed fallback...
  4. ...while still returning exactly what the greedy fallback would have returned, so the
     visibility fix cannot change a result;
  5. absent scipy is its own message, because "the library is not installed" and "the
     solve failed" need different fixes;
  6. with the probe off, nothing about production behaviour changes.

Run:  python -m pytest Tests/unit/test_inventory_optimal_solver.py -q
"""
from __future__ import annotations

import logging

import pytest

from Warehouse.inventory import inventory_optimal as io_mod


# ── a warehouse small enough to solve exactly, built without the sim harness ─────

class _Bin:
    __slots__ = ('x_phys', 'y_phys', 'handling_type', 'storage_type',
                 'storage_size', 'unit_type')

    def __init__(self, x, y):
        self.x_phys, self.y_phys = float(x), float(y)
        self.handling_type, self.storage_type = 'conveyable', 'food'
        self.storage_size, self.unit_type = 'medium', 'pallet'


class _Warehouse:
    def __init__(self, bins):
        self.bins = bins


class _Unit:
    """The StorageUnit surface `_optimal_work_assign` touches."""
    def __init__(self, order):
        self.order = order
        self.storage_size, self.unit_category = 'medium', 'pallet'


class _Order:
    def __init__(self, sku):
        self.sku = sku
        self.storage_handle_config = type(
            'SHC', (), {'handling': 'conveyable', 'category': 'food'})()


class _WP:
    x_speed = y_speed = 1.0
    pick_intercept = 1.0
    pick_per_item = 0.5      # the kernel default (cost_model.DEFAULT_PICK_PER_ITEM)
    height_brackets = ((96, 1.0), (240, 1.2), (float('inf'), 1.4))


class _Mgr(io_mod.OptimalLayoutMixin):
    """Just enough manager for the assignment: the mixin reads `_key` and `warehouse`."""
    def __init__(self, n_bins):
        self.warehouse = _Warehouse([_Bin(i * 10, (i % 3) * 100) for i in range(n_bins)])

    @staticmethod
    def _key(b):
        return (b.handling_type, b.storage_type, b.storage_size, b.unit_type)

    @staticmethod
    def _handle_var(order, wp):
        return 1.0


def _run(mgr, n_units, monkeypatch, probe=None):
    """Drive `_optimal_work_assign` over `n_units` synthetic units of one BinKey class."""
    orders = [_Order(s) for s in range(n_units)]
    monkeypatch.setattr(io_mod, 'viable_storage_units', lambda o, q: [_Unit(o)])
    monkeypatch.setattr(io_mod, '_equilibrium_qty', lambda o: 1)
    monkeypatch.setattr(io_mod, 'binkey_of',
                        lambda u: ('conveyable', 'food', 'medium', 'pallet'))
    if probe is not None:
        mgr._assign_probe = probe
    freq = {o.sku: 1.0 + o.sku for o in orders}
    qty = {o.sku: 1.0 for o in orders}
    return mgr._optimal_work_assign(orders, freq, qty, _WP())


# ── 1-2. the branch, and which gate term chose it ───────────────────────────────

def test_a_small_class_is_solved_exactly(monkeypatch):
    pytest.importorskip('scipy')
    seen = []
    mgr = _Mgr(40)
    _run(mgr, 12, monkeypatch, probe=seen.append)
    assert len(seen) == 1
    rec = seen[0]
    assert rec['branch'] == 'lap'
    assert rec['gate_n_ok'] and rec['gate_prod_ok']
    assert mgr._map_lap_stats['lap_classes'] == 1
    assert mgr._map_lap_stats['lap_units'] == 12


def test_the_unit_cap_and_the_product_cap_are_reported_separately(monkeypatch):
    """Which term binds varies by class; a report naming only one is wrong for the rest."""
    seen = []
    mgr = _Mgr(40)
    monkeypatch.setattr(io_mod.OptimalLayoutMixin, '_assign_probe', None, raising=False)
    # 1300 units > the 1200 unit cap, but 1300 * 40 candidate bins is well under 4e6:
    # the UNIT cap binds alone, which the product-cap-only story would miss.
    _run(mgr, 1300, monkeypatch, probe=seen.append)
    rec = seen[0]
    assert rec['branch'] == 'greedy_gate'
    assert rec['gate_n_ok'] is False
    assert rec['gate_prod_ok'] is True
    assert mgr._map_lap_stats['lap_units'] == 0
    assert mgr._map_lap_stats['units'] == 1300      # units, not classes, is the weight


# ── 3-4. a failed solve is visible, and changes nothing ─────────────────────────

def _boom(*_a, **_k):
    raise RuntimeError('cost matrix went sideways')


def test_a_failed_solve_is_recorded_and_logged_not_silently_greedy(monkeypatch, caplog):
    pytest.importorskip('scipy')
    import scipy.optimize
    monkeypatch.setattr(scipy.optimize, 'linear_sum_assignment', _boom)
    seen = []
    mgr = _Mgr(40)
    with caplog.at_level(logging.WARNING, logger=io_mod.__name__):
        _run(mgr, 12, monkeypatch, probe=seen.append)
    assert seen[0]['branch'] == 'greedy_error'
    assert 'cost matrix went sideways' in seen[0]['error']
    assert mgr._map_lap_stats['greedy_error'] == 1
    assert any('falling back to greedy' in r.getMessage() for r in caplog.records), \
        'a solver failure must reach the log, or it is the old silent degradation'


def test_the_failure_path_returns_exactly_what_greedy_would_have(monkeypatch):
    """Making a fallback visible must not make it a different fallback."""
    pytest.importorskip('scipy')
    import scipy.optimize

    over_gate = _run(_Mgr(40), 1300, monkeypatch)          # designed greedy
    monkeypatch.setattr(scipy.optimize, 'linear_sum_assignment', _boom)
    errored = _run(_Mgr(40), 1300, monkeypatch)            # greedy after a failure
    assert errored[0] == pytest.approx(over_gate[0])
    assert errored[1] == pytest.approx(over_gate[1])


def test_absent_scipy_reports_itself_distinctly(monkeypatch, caplog):
    """'not installed' and 'solve crashed' need different fixes, so they read differently."""
    import builtins
    real_import = builtins.__import__

    def _no_scipy(name, *a, **k):
        if name.startswith('scipy'):
            raise ImportError('No module named scipy')
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, '__import__', _no_scipy)
    seen = []
    mgr = _Mgr(40)
    with caplog.at_level(logging.WARNING, logger=io_mod.__name__):
        _run(mgr, 12, monkeypatch, probe=seen.append)
    monkeypatch.undo()
    assert seen[0]['branch'] == 'greedy_error'
    assert 'ImportError' in seen[0]['error']
    assert any('scipy unavailable' in r.getMessage() for r in caplog.records)


# ── 5-6. the probe is opt-in and inert ──────────────────────────────────────────

def test_the_probe_is_off_by_default_and_changes_no_result(monkeypatch):
    pytest.importorskip('scipy')
    assert io_mod.OptimalLayoutMixin._assign_probe is None, \
        'an always-on probe is instrumentation in the byte-identical path'
    quiet = _run(_Mgr(40), 12, monkeypatch)
    probed = _run(_Mgr(40), 12, monkeypatch, probe=lambda _rec: None)
    assert quiet[0] == pytest.approx(probed[0])
    assert quiet[1] == pytest.approx(probed[1])


def test_stats_are_populated_without_a_probe(monkeypatch):
    """A production run records the split with no instrumentation armed."""
    mgr = _Mgr(40)
    _run(mgr, 30, monkeypatch)
    s = mgr._map_lap_stats
    assert s['classes'] == 1 and s['units'] == 30
    assert s['cap'] == 1200 and s['prod_cap'] == 4_000_000
    assert set(io_mod.BRANCHES) == {'lap', 'greedy_gate', 'greedy_error', 'greedy_empty'}
