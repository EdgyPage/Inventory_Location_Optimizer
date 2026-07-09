"""test_regime_sizing.py — the PER-REGIME warehouse sizing path (plan_warehouse regime_sizing).

Guards the production main() path the older suites don't touch: store and fulfillment sized
INDEPENDENTLY (store demand-driven, fulfillment a FIXED tier distribution), each with its own
bin caps + fill headroom.  Asserts caps are isolated per regime and the ff distribution ratio
+ scale are honored.

Run:  python -m pytest Tests/test_regime_sizing.py -q
"""
from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

from Warehouse.Inventory_Management import Inventory_Manager                # noqa: E402
from Warehouse.generation.generate_inventory import (                      # noqa: E402
    Family, fulfillment_family, build_inventory_from_plan)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}

_COMMON = dict(categories=['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical'],
               handlings=['conveyable', 'non-conveyable'],
               aisle_width=2400, aisle_height=480, sample=False)


def _mixed_orders(n=400, seed=1):
    plan = [Family('food', 0.35, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            Family('clothing', 0.25, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            fulfillment_family(share=0.4, cube_sizes=(4, 6, 8))]
    return build_inventory_from_plan(num_skus=n, plan=plan, seed=seed).orders


def _bins_by_regime(plan):
    store = sum(n for (h, c, s, u), n in plan.capacity.items() if u != 'fulfillment')
    ff    = sum(n for (h, c, s, u), n in plan.capacity.items() if u == 'fulfillment')
    return store, ff


def _plan(orders, regime_sizing):
    return Inventory_Manager.plan_warehouse(orders, regime_sizing=regime_sizing,
                                            rng=random.Random(43), **_COMMON)


def _base_sizing():
    return {
        'store': {'mode': 'demand', 'min_bins': None, 'max_bins': None, 'max_aisles': None,
                  'composition': None, 'fill': 0.875},
        'fulfillment': {'mode': 'fixed',
                        'distribution': {'ff_small': 0.5, 'ff_medium': 0.3, 'ff_large': 0.2},
                        'target_bins': None, 'min_bins': None, 'max_bins': None,
                        'max_aisles': None, 'fill': 0.875},
    }


def test_store_cap_leaves_fulfillment_untouched():
    orders = _mixed_orders()
    bs0, bf0 = _bins_by_regime(_plan(orders, _base_sizing()))
    assert bs0 > 0 and bf0 > 0
    rs = _base_sizing()
    rs['store']['max_bins'] = bs0 // 2
    bs1, bf1 = _bins_by_regime(_plan(orders, rs))
    assert bs1 <= bs0, 'store cap did not shrink store bins'
    assert bf1 == bf0, f'fulfillment bins changed when only store was capped ({bf0} -> {bf1})'


def test_fulfillment_cap_leaves_store_untouched():
    orders = _mixed_orders()
    bs0, bf0 = _bins_by_regime(_plan(orders, _base_sizing()))
    rs = _base_sizing()
    rs['fulfillment']['max_bins'] = bf0 // 2
    bs1, bf1 = _bins_by_regime(_plan(orders, rs))
    assert bf1 <= bf0, 'fulfillment cap did not shrink fulfillment bins'
    assert bs1 == bs0, f'store bins changed when only fulfillment was capped ({bs0} -> {bs1})'


def test_ff_fixed_distribution_honors_ratio_and_scale():
    orders = _mixed_orders()
    rs = _base_sizing()
    rs['fulfillment']['distribution'] = {'ff_small': 0.6, 'ff_medium': 0.3, 'ff_large': 0.1}
    rs['fulfillment']['min_bins'] = 60000
    plan = _plan(orders, rs)
    tiers = {s: n for (h, c, s, u), n in plan.capacity.items() if u == 'fulfillment'}
    # Ratio is honored (small > medium > large) and the total scales toward the requested target.
    assert tiers['ff_small'] > tiers['ff_medium'] > tiers['ff_large'], f'ratio not honored: {tiers}'
    assert sum(tiers.values()) >= 60000 * 0.7, f'ff scale far below target: {sum(tiers.values())}'


def test_regime_sizing_none_matches_no_regressions():
    """A store-only catalog with regime_sizing=None must still size (sanity: sizes, ≥1 bucket)."""
    orders = [c for c in _mixed_orders() if c.storage_handle_config.handling != 'fulfillment']
    plan = Inventory_Manager.plan_warehouse(orders, rng=random.Random(43), **_COMMON)
    assert plan.total_bins > 0 and plan.total_aisles > 0


# ── C-geom: optional fulfillment depth-tiering (per-band aisle geometry) ──────────

def _ff_widths(plan):
    return {ac.aisle_width for ac in plan.warehouse_cfg.aisle_configs
            if ac.unit_type == 'fulfillment'}


def test_depth_classes_none_is_byte_identical():
    """depth_classes absent vs explicitly None ⇒ identical plan (the byte-identical default)."""
    orders = _mixed_orders()
    p0 = _plan(orders, _base_sizing())                      # no depth_classes key
    rs = _base_sizing(); rs['fulfillment']['depth_classes'] = None
    p1 = _plan(orders, rs)
    assert p0.capacity == p1.capacity
    assert p0.total_bins == p1.total_bins and p0.total_aisles == p1.total_aisles
    assert _ff_widths(p0) == _ff_widths(p1) and len(_ff_widths(p0)) == 1


def test_depth_classes_emit_multiple_widths_and_leave_store_untouched():
    """With depth_classes set, ff aisles come in multiple WIDTHS sharing their BinKey, total ff
    bins stay in the same ballpark, the aisle count grows, and STORE is byte-for-byte unchanged."""
    orders = _mixed_orders()
    p0 = _plan(orders, _base_sizing())
    bs0, bf0 = _bins_by_regime(p0)
    rs = _base_sizing()
    rs['fulfillment']['depth_classes'] = [{'columns': 10, 'share': 0.3},
                                          {'columns': 40, 'share': 0.4},
                                          {'columns': 100, 'share': 0.3}]
    p1 = _plan(orders, rs)
    bs1, bf1 = _bins_by_regime(p1)
    assert bs1 == bs0, f'store bins changed by ff depth tiering ({bs0} -> {bs1})'
    assert len(_ff_widths(p0)) == 1 and len(_ff_widths(p1)) > 1, (_ff_widths(p0), _ff_widths(p1))
    assert 0.5 * bf0 <= bf1 <= 1.5 * bf0, f'ff bins wildly off after split: {bf0} -> {bf1}'
    assert p1.total_aisles > p0.total_aisles                 # shallow aisles → more of them
    assert all(n > 0 for (h, c, s, u), n in p1.capacity.items() if u == 'fulfillment')
