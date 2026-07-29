"""test_fulfillment_inventory.py — the fulfillment sub-catalog generator.

Covers the pieces added for CLI-driven fulfillment inventories:
  * sample_weight's 'triangular' branch — right-skewed, bounded, reproducible;
  * fulfillment_families() — shares split by cube_fraction (rectangles preferred), rect family emits
    non-cubes and the cube family emits cubes, both carry the fulfillment BinKey and skewed weight;
  * every fulfillment SKU fits a FulfillmentBin (16×16×18 envelope) — no ValueError;
  * end-to-end: build_inventory_from_plan yields ~fraction fulfillment SKUs, all weights <= max, all fit.

Run: python -m pytest Tests/test_fulfillment_inventory.py
"""
from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


from Warehouse.generation.generate_inventory import (          # noqa: E402
    sample_weight, fulfillment_families, build_inventory_from_plan,
    Family, DEFAULT_FF_WEIGHT_SPEC,
)
from Warehouse.layout.Storage_Primitive import FulfillmentBin          # noqa: E402
from Warehouse.kernel.regime import regime_of, FULFILLMENT            # noqa: E402


def _fits_ff_bin(order) -> bool:
    """A single item fits a fulfillment bin iff FulfillmentBin(order, 1) constructs without error."""
    try:
        FulfillmentBin(order, 1)
        return True
    except ValueError:
        return False


# ── triangular weight sampler ────────────────────────────────────────────────────

def test_triangular_weight_bounded_and_skewed():
    rng = random.Random(7)
    spec = {'dist': 'triangular', 'low': 1, 'mode': 2.5, 'high': 10}
    draws = [sample_weight(spec, 5, 5, 5, rng) for _ in range(5000)]
    assert all(1 <= w <= 10 for w in draws)              # bounded to [low, high]
    # mode region (<=4) holds most of the mass; the high tail (>=8) is thin ⇒ right skew.
    near_mode = sum(1 for w in draws if w <= 4)
    tail      = sum(1 for w in draws if w >= 8)
    assert near_mode > len(draws) * 0.5
    assert tail < near_mode


def test_triangular_weight_reproducible():
    spec = {'dist': 'triangular', 'low': 1, 'mode': 2.5, 'high': 10}
    a = [sample_weight(spec, 6, 6, 6, random.Random(99)) for _ in range(50)]
    b = [sample_weight(spec, 6, 6, 6, random.Random(99)) for _ in range(50)]
    assert a == b


def test_default_ff_weight_spec_is_triangular():
    assert DEFAULT_FF_WEIGHT_SPEC['dist'] == 'triangular'
    assert DEFAULT_FF_WEIGHT_SPEC['high'] == 10.0


# ── fulfillment_families ─────────────────────────────────────────────────────────

def test_shares_split_by_cube_fraction():
    fams = fulfillment_families(0.2, cube_fraction=0.3)
    assert len(fams) == 2
    rect, cube = fams
    assert abs(sum(f.share for f in fams) - 0.2) < 1e-9
    assert rect.share > cube.share                       # rectangles preferred at cube_fraction 0.3
    assert abs(rect.share - 0.2 * 0.7) < 1e-9
    assert abs(cube.share - 0.2 * 0.3) < 1e-9
    assert cube.cube_sizes is not None and rect.cube_sizes is None
    assert all(f.handling_override == FULFILLMENT for f in fams)
    assert all(f.weight_spec['dist'] == 'triangular' for f in fams)   # cube weight overridden too


def test_all_cubes_or_all_rects_edge_fractions():
    assert len(fulfillment_families(0.2, cube_fraction=0.0)) == 1     # rect only
    assert len(fulfillment_families(0.2, cube_fraction=1.0)) == 1     # cube only


def _build_family(fam: Family, n: int, seed: int):
    """Build n SKUs from a single family via the real plan builder (share is the only weight)."""
    inv = build_inventory_from_plan(n, [fam], seed=seed)
    return list(inv.orders)


def test_rect_family_mostly_noncubes_cube_family_all_cubes():
    rect, cube = fulfillment_families(1.0, cube_fraction=0.5)
    rects = _build_family(rect, 400, seed=1)
    cubes = _build_family(cube, 400, seed=2)
    noncube = sum(1 for o in rects if not (o.length == o.width == o.height))
    assert noncube > len(rects) * 0.6                    # independent draws ⇒ mostly non-cubes
    assert all(o.length == o.width == o.height for o in cubes)


def test_every_ff_sku_fits_a_fulfillment_bin():
    for fam in fulfillment_families(1.0, cube_fraction=0.4, dim_low=3, dim_high=16):
        for o in _build_family(fam, 300, seed=5):
            assert _fits_ff_bin(o), f'SKU {o.sku} ({o.length}x{o.width}x{o.height}) did not fit'


# ── end-to-end plan with store + fulfillment ─────────────────────────────────────

def test_mixed_plan_fraction_regime_and_weights():
    store = [
        Family('food', share=0.8 * 0.5, handling_split=(0.9, 0.1),
               length_spec={'dist': 'triangular', 'low': 4, 'high': 24, 'mode': 12},
               width_spec={'dist': 'triangular', 'low': 4, 'high': 24, 'mode': 12},
               height_spec={'dist': 'triangular', 'low': 4, 'high': 20, 'mode': 10},
               weight_spec={'dist': 'volume_poisson'}),
        Family('electronic', share=0.8 * 0.5, handling_split=(0.9, 0.1),
               length_spec={'dist': 'normal', 'mean': 10, 'std': 3},
               width_spec={'dist': 'normal', 'mean': 10, 'std': 3},
               height_spec={'dist': 'normal', 'mean': 8, 'std': 3},
               weight_spec={'dist': 'volume_poisson'}),
    ]
    plan = store + fulfillment_families(0.2, cube_fraction=0.3)
    inv = build_inventory_from_plan(6000, plan, seed=42)
    ff = [o for o in inv.orders if regime_of(o) == FULFILLMENT]
    frac = len(ff) / len(inv.orders)
    assert 0.16 < frac < 0.24                             # ~0.20 by construction
    assert all(o.weight <= 10 for o in ff)               # skewed weight capped at 10
    assert all(_fits_ff_bin(o) for o in ff)              # all fulfillment SKUs fit their bins


def test_zero_fraction_gives_no_fulfillment():
    # A plan with no fulfillment families produces zero fulfillment SKUs.
    fam = Family('food', share=1.0, handling_split=(0.9, 0.1),
                 length_spec={'dist': 'uniform', 'low': 4, 'high': 24},
                 width_spec={'dist': 'uniform', 'low': 4, 'high': 24},
                 height_spec={'dist': 'uniform', 'low': 4, 'high': 20},
                 weight_spec={'dist': 'volume_poisson'})
    inv = build_inventory_from_plan(500, [fam], seed=1)
    assert all(regime_of(o) == 'store' for o in inv.orders)
