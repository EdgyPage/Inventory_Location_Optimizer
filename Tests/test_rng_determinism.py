"""test_rng_determinism.py

Locks in the RNG-isolation contract that makes assignment-function comparisons fair:

  Group A — batches: `Batch` (and `Demand.sample`/`poisson_sample`/`_lift_weighted_sample`)
    draw from a caller-supplied `rng`.  With `rng=random.Random(seed_batches + i)`, batch i is a
    pure function of (inventory, affinity, config, seed) — identical across arms regardless of any
    placement/reorder randomness that ran first on the global stream.  This is what guarantees two
    arms over the same warehouse config see the IDENTICAL batch sequence.

  Group B — reorder qty: `check_reorders` draws the received-quantity noise from a per-reorder
    `random.Random((mgr._seed, sku, batch_num))`, so the quantity is deterministic by seed and
    independent of global-stream call order.  Its mean (`ideal = equilibrium - position`) still
    differs across arms because depletion depends on layout — that part is intentionally non-
    deterministic across arms (tasks depend on the bin layout being tested).

  Back-compat: with no `rng`, every helper falls back to the global `random` module and behaves
    exactly as before.

Unlike the legacy `check()`-style suites these use real `assert`s, so a regression fails pytest.

Usage
-----
    cd Tests
    python test_rng_determinism.py        # or: python -m pytest Tests/test_rng_determinism.py
"""
from __future__ import annotations

import os
import random
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Aisle_Dimensions import aisle_width_for, aisle_height_for
from Aisle_Storage import Aisle
from Carton import Carton, StorageHandleConfig
from Demand import Demand, poisson_sample
from Inventory_Management import Inventory_Manager
from Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Workload_Builder import Batch, BatchConfig
from generation.generate_inventory import (
    build_inventory_with_profile,
    DEFAULT_DIM_SPEC,
    DEFAULT_WEIGHT_SPEC,
)


# ── helpers ─────────────────────────────────────────────────────────────────────

def _inventory(num_skus: int = 80, seed: int = 42):
    return build_inventory_with_profile(
        num_skus=num_skus, seed=seed,
        handling_splits=[0.5, 0.5],
        category_splits=[1 / 6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
        equilibrium_coverage_batches=10.0,
        reorder_safety_batches=2.0,
    )


def _batch_cfg(num_skus: int = 80) -> BatchConfig:
    return BatchConfig(inventory_size=num_skus, mean_fraction=0.3, std_fraction=0.05)


def _fingerprint(b: Batch):
    """Everything random about a batch, hashable for equality."""
    return (b.num_skus, b.threshold, tuple(sorted(b.items.items())))


def _burn_global(n: int = 137, seed: int | None = None) -> None:
    """Consume the global random stream by an arbitrary amount (mimics another arm's
    placement/reorder draws happening before a batch is built)."""
    if seed is not None:
        random.seed(seed)
    for _ in range(n):
        random.random()
    random.gauss(0.0, 1.0)
    random.uniform(0.0, 1.0)


def _small_warehouse() -> Inventory_Manager:
    Aisle.next_aisle_id = 1
    random.seed(0)
    w = aisle_width_for(4)
    h = aisle_height_for(6)
    cfg = WarehouseConfig(
        total_aisles=4,
        aisle_splits=[0.25] * 4,
        aisle_configs=[
            AisleConfig('conveyable',     'food', 'pallet',    w, h, ['medium', 'large'], [0.5, 0.5]),
            AisleConfig('non-conveyable', 'food', 'pallet',    w, h, ['medium', 'large'], [0.5, 0.5]),
            AisleConfig('conveyable',     'food', 'singleton', w, h, ['singleton'], None),
            AisleConfig('non-conveyable', 'food', 'singleton', w, h, ['singleton'], None),
        ],
    )
    wh = Warehouse_Builder().from_config(cfg).build()
    return Inventory_Manager(wh)


def _make_carton(sku: int, eq_qty: int, rp: int, lt: float, supply_cv: float) -> Carton:
    c = object.__new__(Carton)
    c._sku                  = sku
    c.storage_type          = ('conveyable', 'food')
    c.storage_handle_config = StorageHandleConfig('conveyable', 'food')
    c.lift_group            = ('conveyable', 'food')
    c.length = 8
    c.width  = 8
    c.height = 6
    c.weight = 2
    c.demand = Demand.from_rates(0.8, 4.0)
    c.equilibrium_qty       = eq_qty
    c.reorder_point         = rp
    c.lead_time_mean        = lt
    c.supply_cv             = supply_cv
    c.expected_batch_demand = 0.8 * 4.0
    return c


def _fire_reorder_qty(mgr_seed: int, supply_cv: float, burn: bool = False) -> int:
    """Fire one reorder for a fixed (eq, position) and return the ordered quantity.

    Lead time > 0 routes the order into _deferred_qty without placement muddying the
    accounting, so the returned delta is exactly the quantity the noise produced.
    position = on_hand(6) + on-order(6) = 12 == rp  ⇒  fires;  ideal = eq(30) - 12 = 18.
    """
    mgr = _small_warehouse()
    mgr._seed = mgr_seed
    sku = 31
    c = _make_carton(sku, eq_qty=30, rp=12, lt=5.0, supply_cv=supply_cv)
    mgr._originals[sku] = c
    mgr._current_quantities[sku] = 6
    mgr._queued_qty[sku] = 6
    if burn:
        _burn_global()
    before = mgr._deferred_qty.get(sku, 0)
    mgr._depleted_skus.add(sku)
    mgr.check_reorders()
    return mgr._deferred_qty.get(sku, 0) - before


# ═════════════════════════════════════════════════════════════════════════════
# Group A — batches identical across arms (immune to the global stream)
# ═════════════════════════════════════════════════════════════════════════════

def test_batch_rng_immune_to_global_stream() -> None:
    """Same batch RNG seed → identical batch, no matter how much the GLOBAL stream was
    consumed in between (this is what makes batch i identical across arms)."""
    inv, cfg = _inventory(), _batch_cfg()

    b1 = Batch(cfg, inv, affinity=None, rng=random.Random(7))
    _burn_global(seed=999)                       # simulate another arm's placement/reorder draws
    b2 = Batch(cfg, inv, affinity=None, rng=random.Random(7))

    assert _fingerprint(b1) == _fingerprint(b2)


def test_batch_rng_immune_to_global_stream_with_affinity() -> None:
    """Same property on the affinity (lift-weighted) selection path."""
    inv, cfg = _inventory(), _batch_cfg()
    skus = [c.sku for c in inv.cartons]
    affinity = {(skus[i], skus[i + 1]): 5.0 for i in range(0, min(len(skus) - 1, 20), 2)}

    b1 = Batch(cfg, inv, affinity=affinity, rng=random.Random(3))
    _burn_global(seed=123)
    b2 = Batch(cfg, inv, affinity=affinity, rng=random.Random(3))

    assert _fingerprint(b1) == _fingerprint(b2)


def test_two_arms_see_identical_batch_sequence() -> None:
    """Proxy for the cross-arm guarantee: two 'arms' burn DIFFERENT amounts of global
    randomness between batches (as differing placement/reorder draws would), yet with a
    per-batch rng=Random(seed_batches+i) they observe the identical batch sequence."""
    inv, cfg, seed_batches, n = _inventory(), _batch_cfg(), 1337, 25

    def arm(noise_per_batch) -> list:
        random.seed(98765)                       # each arm starts from its own world seed
        seq = []
        for i in range(n):
            _burn_global(n=noise_per_batch(i))   # arm-specific global consumption
            b = Batch(cfg, inv, affinity=None, rng=random.Random(seed_batches + i))
            seq.append(_fingerprint(b))
        return seq

    arm_a = arm(lambda i: 3 * i + 1)             # grows each batch (e.g. more reorders)
    arm_b = arm(lambda i: 7)                      # flat
    assert arm_a == arm_b


def test_different_batch_seeds_differ() -> None:
    """Distinct batch seeds should produce distinct batches (the RNG is actually wired)."""
    inv, cfg = _inventory(), _batch_cfg()
    fps = {_fingerprint(Batch(cfg, inv, affinity=None, rng=random.Random(s)))
           for s in range(1, 9)}
    assert len(fps) > 1


def test_batch_default_rng_uses_global_and_is_reproducible() -> None:
    """Back-compat: with no rng, Batch uses the global module and is reproducible by
    re-seeding it (existing callers/notebooks are unaffected)."""
    inv, cfg = _inventory(), _batch_cfg()
    random.seed(2024)
    b1 = Batch(cfg, inv, affinity=None)
    random.seed(2024)
    b2 = Batch(cfg, inv, affinity=None)
    assert _fingerprint(b1) == _fingerprint(b2)


# ═════════════════════════════════════════════════════════════════════════════
# Group A — Demand.sample / poisson_sample rng plumbing
# ═════════════════════════════════════════════════════════════════════════════

def test_poisson_sample_rng_is_deterministic_and_isolated() -> None:
    seq_a = [poisson_sample(5.0, rng=random.Random(11)) for _ in range(20)]
    _burn_global(seed=42)
    seq_b = [poisson_sample(5.0, rng=random.Random(11)) for _ in range(20)]
    assert seq_a == seq_b


def test_poisson_sample_default_uses_global() -> None:
    random.seed(7)
    a = [poisson_sample(5.0) for _ in range(20)]
    random.seed(7)
    b = [poisson_sample(5.0) for _ in range(20)]
    assert a == b


def test_demand_sample_rng_isolated() -> None:
    d = Demand.from_rates(0.8, 6.0)
    a = [d.sample(rng=random.Random(99)) for _ in range(20)]
    _burn_global(seed=1)
    b = [d.sample(rng=random.Random(99)) for _ in range(20)]
    assert a == b


# ═════════════════════════════════════════════════════════════════════════════
# Group B — reorder quantity deterministic by seed, independent of global stream
# ═════════════════════════════════════════════════════════════════════════════

def test_reorder_qty_reproducible_by_seed() -> None:
    """Same mgr._seed → identical ordered quantity, every run."""
    assert _fire_reorder_qty(mgr_seed=4, supply_cv=0.2) == _fire_reorder_qty(mgr_seed=4, supply_cv=0.2)


def test_reorder_qty_immune_to_global_stream() -> None:
    """Burning the global stream before the reorder must not change the quantity
    (the noise is keyed on (seed, sku, batch), not the global cursor)."""
    base  = _fire_reorder_qty(mgr_seed=4, supply_cv=0.2, burn=False)
    noisy = _fire_reorder_qty(mgr_seed=4, supply_cv=0.2, burn=True)
    assert base == noisy


def test_reorder_qty_varies_with_seed() -> None:
    """Different seeds produce different noise (so restocks can split across units)."""
    qtys = {_fire_reorder_qty(mgr_seed=s, supply_cv=0.25) for s in range(1, 12)}
    assert len(qtys) > 1


def test_reorder_qty_cv_zero_is_exact_ideal() -> None:
    """supply_cv == 0 → exactly ideal (18), for any seed, with no RNG drawn at all."""
    for s in range(5):
        assert _fire_reorder_qty(mgr_seed=s, supply_cv=0.0) == 18


# ═════════════════════════════════════════════════════════════════════════════
# Runner
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    print(f'\n{"="*62}\n  RNG determinism tests\n{"="*62}')
    failed = 0
    for t in tests:
        try:
            t()
            print(f'  PASS  {t.__name__}')
        except AssertionError as e:
            failed += 1
            print(f'  FAIL  {t.__name__}  ({e})')
    print(f'{"="*62}')
    print('  All passed.' if failed == 0 else f'  {failed} FAILED')
    sys.exit(0 if failed == 0 else 1)
