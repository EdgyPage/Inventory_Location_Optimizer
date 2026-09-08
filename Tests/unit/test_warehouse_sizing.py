"""test_warehouse_sizing.py — automatic warehouse sizing, SKU sampling, and queue behaviour.

`Inventory_Manager.plan_warehouse` is the step that turns a generated catalogue into a
warehouse: it counts what each SKU needs per BUCKET — the 4-tuple
`(handling, category, storage_size, unit_type)` — sizes aisles to hold it, and samples the
catalogue down (or grows each SKU's `equilibrium_qty` up) to hit a target fill.  Everything
downstream depends on it being exactly right, because its failure mode is not an exception:

  - a bucket short of capacity leaves units permanently queued, and the queue is the only
    place that shows;
  - a bucket with no aisle at all makes a whole (handling, category, tier) unplaceable, and
    those SKUs simply never appear in the warehouse;
  - an over-grown `equilibrium_qty` produces a plan the workers cannot reproduce after the
    DB round-trip, and every worker then silently runs a different warehouse.

Groups
------
    sizing        bucket_requirements, the 60-bucket floor, caps, min_bins, composition
    sampling      per-bucket capacity respected, cross-tier fill, resampling arithmetic
    queue         the headline: restocks drain incrementally and stay bounded
    placement     optimal layout, requeue, reloaders, and the named assignment scorers
    round-trip    the planned inventory survives to a worker process unchanged

Every fixture here DECLARES a stock level
-----------------------------------------
A generated catalogue carries none (ADR-0002): `equilibrium_qty` is a run's declaration, not
a SKU's fact, and `bucket_requirements` raises `UndeclaredStock` rather than defaulting — a
default there would size every bucket for one unit per SKU and build a warehouse an order of
magnitude too small, with no error.  A production run declares through the coverage fixed
point; this file declares explicitly in `_declare`, at the coverage the generator used to
author, so every hand-tuned threshold below still means what it meant.

A note on speeds
----------------
`x_speed`/`y_speed` are **ft/s** and positions are inches, so travel is
`x_phys * sec_per_inch(x_speed) + y_phys * sec_per_inch(y_speed)`
(`Warehouse/kernel/cost_model.py`).  Any test here that recomputes travel independently
must use `sec_per_inch` — see `_D` below.  Two assertions in this file used to carry the
pre-conversion formula (`x_speed * x_phys + ...`) and had been printing FAIL into a green
suite ever since the conversion landed.

    python -m pytest Tests/unit/test_warehouse_sizing.py -q

History: this file used a `check()` harness whose `fail()` body was a `print`, so its 77
assertions could not fail the suite.  Do not re-introduce it.
"""
from __future__ import annotations

import inspect
import math
import os
import random
import sqlite3
import tempfile
import types
from collections import defaultdict
from statistics import mean

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from Optimization.config.sim_config import CONFIG
from Warehouse.layout.Aisle_Dimensions import aisle_width_for, aisle_height_for
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.catalog.Order import Order, StorageHandleConfig
from Warehouse.catalog.Demand import Demand
from Warehouse.kernel.cost_model import sec_per_inch
from Warehouse.generation.generate_inventory import (
    build_inventory_with_profile, DEFAULT_DIM_SPEC, DEFAULT_WEIGHT_SPEC,
    save_inventory_to_db, load_inventory_from_db, Inventory,
)
from Warehouse.inventory.Inventory_Management import (
    Inventory_Manager, Placement, _SIZE_RANKS,
)
from Warehouse.inventory.inventory_common import (
    UndeclaredStock, UnfieldableRequirement,
)
from Warehouse.placement.Assignment_Functions import (
    build_ranked_minimizing_assignment_fn, build_ranked_maximizing_assignment_fn,
    build_uniform_aisle_trip_min_assignment_fn,
    build_cluster_maximizing_assignment_fn, build_cluster_minimizing_assignment_fn,
)
from Warehouse.layout.Storage_Primitive import viable_storage_units, Pallet
from Warehouse.layout.Warehouse_Builder import Warehouse_Builder, WarehouseConfig, AisleConfig

_CATEGORIES = ['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical']
_HANDLINGS  = ['conveyable', 'non-conveyable']
_TIERS      = ['small', 'medium', 'large', 'extra_large']

# Small aisles keep the 60-bucket floor fast.
_AISLE_W = aisle_width_for(2)    # 96
_AISLE_H = aisle_height_for(2)   # 96
_TARGET  = 0.85


def _D(bin_, x_speed: float, y_speed: float) -> float:
    """Travel time (s) to a bin — the SAME expression production uses.

    Speeds are ft/s and positions are inches, so each axis is scaled by `sec_per_inch`.
    Recomputing this with the raw speeds (as this file once did) inverts the relative weight
    of the two axes: at (x=1.0 ft/s, y=0.5 ft/s) the raw form makes x dominate 2:1 while the
    real pace makes y dominate 2:1, so the two orderings disagree on most bins.
    """
    return sec_per_inch(x_speed) * bin_.x_phys + sec_per_inch(y_speed) * bin_.y_phys


def _close(a: float, b: float, rel: float = 1e-9, abs_: float = 1e-6) -> bool:
    """Scale-aware float comparison — Sigma f*D runs to ~1e5, where 1e-6 absolute is noise."""
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


# The coverage this file declares with.  These are the two numbers the generator used to
# author into the catalogue (its retired `EQUILIBRIUM_COVERAGE_BATCHES` /
# `REORDER_SAFETY_BATCHES`), reproduced here verbatim so the sizing thresholds below —
# expected_fill floors, per-tier usage fractions, cap arithmetic — keep their meaning.
_COVERAGE_BATCHES = 10.0
_SAFETY_BATCHES   = 2.0


def _catalogue(n_skus: int, seed: int = 7):
    """A generated catalogue, exactly as it comes off the generator: NO stock levels."""
    return build_inventory_with_profile(
        num_skus=n_skus, seed=seed,
        handling_splits=[0.5, 0.5],
        category_splits=[1 / 6] * 6,
        singleton_fraction=0.3,
        dim_spec=DEFAULT_DIM_SPEC,
        weight_spec=DEFAULT_WEIGHT_SPEC,
    )


def _declare(inv):
    """Declare this file's stock level on every SKU of *inv*, and return it.

    A level is a run's declaration, never the catalogue's (ADR-0002), so a sizing test has
    to make one before `plan_warehouse` will count anything.  An explicit loop rather than
    `simconfig.coverage.rescale_section` because these tests are ABOUT the sizing arithmetic:
    the numbers must stay hand-checkable, and they are exactly what the generator used to
    author —

        Q  = round(10 batches x expected batch demand)
        rp = round(expected x (lead + 2 batches))

    `declare_stock` applies the clamps (Q >= 1; 1 <= rp <= Q-1, rp == 1 at Q == 1), which is
    where the old generator's `max(1, min(eq - 1, ...))` lived.
    """
    for c in inv.orders:
        e = c.expected_batch_demand
        c.declare_stock(round(_COVERAGE_BATCHES * e),
                        round(e * (c.lead_time_mean + _SAFETY_BATCHES)))
    return inv


def _inventory(n_skus: int, seed: int = 7):
    """The catalogue every test here plans from: generated, then declared."""
    return _declare(_catalogue(n_skus, seed))


def _make_carton(sku: int, eq_qty: int, length=8, width=8, height=6,
                 handling='conveyable', category='food', stock_plan=None) -> Order:
    """One hand-built order carrying a hand-built declaration.

    `declare_stock` is the ONE mutation site for the four level slots, so the fixture goes
    through it rather than assigning them; the values are unchanged (rp = eq // 2, floored
    at 1 and capped at eq - 1 by the clamp).
    """
    c = object.__new__(Order)
    c._sku                  = sku
    c.storage_type          = (handling, category)
    c.storage_handle_config = StorageHandleConfig(handling, category)
    c.lift_group            = (handling, category)
    c.length, c.width, c.height, c.weight = length, width, height, 2
    c.demand                = Demand.from_rates(0.8, 4.0)
    c.lead_time_mean        = 0.0
    c.supply_cv             = 0.0
    c.expected_batch_demand = 0.8 * 4.0
    return c.declare_stock(eq_qty, max(1, eq_qty // 2), stock_plan=stock_plan)


def _plan(inv, **kw):
    """The one planning call this file makes.  There was a second (`_plan_kw`) that differed
    only in its sampling seed, so the min_bins / composition tests could not be perturbed by a
    change to the main plan's; planning is DETERMINISTIC since "Field the requirement" (every
    SKU is fielded at exactly its declaration, so there is no contest for capacity to seed)
    and the two collapsed into this one."""
    return Inventory_Manager.plan_warehouse(
        inv.orders,
        categories=_CATEGORIES, handlings=_HANDLINGS,
        aisle_width=_AISLE_W, aisle_height=_AISLE_H,
        target_fill=_TARGET, **kw,
    )


def _build_wh(plan, seed):
    Aisle.next_aisle_id = 1      # class counter — reset or aisle ids leak between tests
    random.seed(seed)            # Warehouse_Builder draws from the module-level random
    return Warehouse_Builder().from_config(plan.warehouse_cfg).build()


def _aff_store(skus, pairs):
    """In-memory AffinityStore with a hand-set symmetric CSR lift matrix.
    skus: ordered list; pairs: list of (sku_i, sku_j, lift)."""
    aff = AffinityStore(':memory:')
    idx = {s: i for i, s in enumerate(skus)}
    rows, cols, data = [], [], []
    for i, j, l in pairs:
        rows += [idx[i], idx[j]]
        cols += [idx[j], idx[i]]
        data += [l, l]
    aff._sku_to_idx = idx
    aff._matrix = csr_matrix((data, (rows, cols)), shape=(len(skus), len(skus)),
                             dtype=np.float32)
    return aff, idx


# ═════════════════════════════════════════════════════════════════════════════
# Sizing: buckets, the floor, caps
# ═════════════════════════════════════════════════════════════════════════════

def test_sizing_refuses_a_catalogue_that_has_declared_no_stock_level():
    """The precondition every other test in this file satisfies through `_declare`.

    A generated catalogue authors no level (ADR-0002), and `bucket_requirements` reads one
    per SKU.  The old default of 1 is the failure this asserts against: it would count one
    unit per SKU, size every bucket for it, and hand back a warehouse an order of magnitude
    too small — no exception, no log line, and nothing downstream able to tell.  Sizing from
    nothing must be an ERROR, so the raise is the contract.
    """
    cat = _catalogue(20, seed=2)
    undeclared = [c.sku for c in cat.orders if not c.stock_declared()]
    assert len(undeclared) == len(cat.orders), (
        f'{len(cat.orders) - len(undeclared)} generated SKU(s) already carry a level; the '
        f'catalogue is authoring stock again and `_declare` is no longer the only source')

    with pytest.raises(UndeclaredStock):
        Inventory_Manager.bucket_requirements(cat.orders)
    with pytest.raises(UndeclaredStock):
        _plan(cat)                       # and the whole planner, not just the counter

    # Declared, the same catalogue sizes normally — so the raise is about the declaration,
    # not about this catalogue.
    assert Inventory_Manager.bucket_requirements(_declare(cat).orders), (
        'a declared catalogue produced no bucket requirements at all')


def test_bucket_requirements_track_the_full_four_part_key():
    """A bucket is `(handling, category, storage_size, unit_type)` — the size tier is part
    of the identity, not an afterthought.

    Dropping the tier would merge a small-pallet requirement into the large-pallet bucket,
    the plan would size the wrong aisles, and the units would arrive with nowhere to go.
    """
    orders = [_make_carton(1, 30, 10, 10, 10),   # pallet, some tier
              _make_carton(2, 5,  6,  6,  4)]    # small enough to be a singleton
    req = Inventory_Manager.bucket_requirements(orders)

    bad_keys = [k for k in req if len(k) != 4]
    assert not bad_keys, f'keys must be 4-tuples (h, c, size, unit_type); got {bad_keys[:2]}'

    # Recount independently, straight from the palletizer.
    manual: dict = defaultdict(int)
    for c in orders:
        shc = c.storage_handle_config
        for u in viable_storage_units(c, c.equilibrium_qty):
            manual[(shc.handling, shc.category, u.storage_size, u.unit_category)] += 1
    assert req == dict(manual), (
        f'bucket_requirements disagrees with a direct viable_storage_units count:\n'
        f'  got    {dict(sorted(req.items()))}\n  expect {dict(sorted(manual.items()))}')


def test_every_one_of_the_sixty_buckets_gets_at_least_one_aisle():
    """A 1-SKU catalogue touches almost no buckets, yet the plan must still build all 60.

    12 (handling, category) pairs x (4 pallet tiers + 1 singleton) = 60.  The floor exists
    so a restock of ANY shape has somewhere to land — a bucket with zero capacity makes
    every future SKU of that shape permanently unplaceable, and the plan gives no warning.
    """
    plan = _plan(_inventory(1, seed=1))

    missing = []
    for h in _HANDLINGS:
        for cat in _CATEGORIES:
            missing += [(h, cat, s, 'pallet') for s in _TIERS
                        if plan.capacity.get((h, cat, s, 'pallet'), 0) <= 0]
            if plan.capacity.get((h, cat, 'singleton', 'singleton'), 0) <= 0:
                missing.append((h, cat, 'singleton', 'singleton'))

    assert not missing, f'{len(missing)}/60 buckets have no capacity, e.g. {missing[:3]}'
    assert len(plan.capacity) == 60, (
        f'{len(plan.capacity)} distinct buckets, expected exactly 60 '
        f'(12 handling x category pairs, 5 tiers each)')


def test_every_sampled_sku_places_and_the_queue_empties():
    """The plan's contract in one line: what it sampled, it can hold.

    If it cannot, the leftovers sit in the queue for the whole run — which is the symptom
    the entire "queue" group below exists to detect at the system level.
    """
    plan = _plan(_inventory(80, seed=3))
    mgr  = Inventory_Manager(_build_wh(plan, 3))
    mgr.enqueue_all(plan.sampled)

    placed  = {b.storage.order.sku for b in mgr.unavailable if b.storage}
    missing = [c.sku for c in plan.sampled if c.sku not in placed]
    assert not missing, (
        f'{len(missing)}/{len(plan.sampled)} sampled SKUs have no occupied bin, '
        f'e.g. {missing[:3]}')
    assert mgr.queue_depth == 0, (
        f'{mgr.queue_depth} units left queued after initial stock — the plan sampled more '
        f'than it sized for')


def test_uncapped_capacity_covers_the_sampled_demand_in_every_bucket():
    """Bucket-by-bucket, not in aggregate: a warehouse with spare capacity OVERALL can still
    be short in one tier, and the surplus in another tier cannot absorb it."""
    plan   = _plan(_inventory(80, seed=5))
    demand = Inventory_Manager.bucket_requirements(plan.sampled)
    over   = [(b, demand[b], plan.capacity.get(b, 0))
              for b in demand if demand[b] > plan.capacity.get(b, 0)]
    assert not over, (
        f'{len(over)} bucket(s) demand more than capacity; (bucket, need, have) = {over[:3]}')


def test_a_bin_cap_that_binds_below_the_requirement_refuses_and_names_the_bucket():
    """A cap and a stock declaration are two statements by the same person that contradict.

    The planner used to honour the cap and let the levels come out short, which is how a
    third of a section came to be fielded below its own line floor — a treadmill under base
    stock, and invisible: every per-bucket capacity assertion in this file stayed green
    through it.  So the run REFUSES ("Field the floor", decision 3), and the message has to
    name the bucket and the shortfall or the operator cannot act on it.
    """
    inv  = _inventory(120, seed=9)
    base = _plan(inv)                              # uncapped, to pick a cap below it
    cap_bins = int(base.total_bins * 0.6)

    with pytest.raises(UnfieldableRequirement) as exc:
        _plan(inv, max_bins=cap_bins)

    # Read the STRUCTURED shortfall, never the prose.  The message names all three possible
    # causes in one sentence and truncates the bucket list at twelve (this cap starves 37),
    # so a substring search over it can neither identify the bucket nor the cause — and it
    # stays green with every per-bucket line deleted.
    short = exc.value.short
    assert short, 'the refusal carries no structured shortfall'
    for bucket, need, cap, budget in short:
        assert len(bucket) == 4, f'not a BinKey: {bucket!r}'
        assert need > budget, (
            f'{bucket} is listed short but needs {need} of {budget} available')
        assert 0 <= budget <= cap, f'{bucket}: budget {budget} outside 0..{cap}'
    starved = {b for b, *_ in short}
    assert starved <= set(base.requirement), (
        f'the refusal names buckets that carry no requirement: {starved - set(base.requirement)}')
    assert all(base.fielding[b]['requirement'] == need for b, need, _c, _bud in short), (
        'the refusal quotes a requirement the uncapped plan disagrees with')


def test_an_aisle_cap_below_the_sixty_bucket_floor_refuses():
    """`max_aisles=10` is impossible — 60 buckets need 60 aisles, one each.

    `_apply_caps` still clamps to that structural floor and warns, because a plan that
    satisfies the cap and cannot hold the inventory is worse than a rejected one.  What has
    changed is that clamping to the floor is no longer the END of the story: 60 aisles hold
    one aisle's worth of every bucket, the declared levels need more than that, and the
    promise check is what turns the warning into a refusal.
    """
    with pytest.raises(UnfieldableRequirement, match=r'cannot hold the stock levels') as exc:
        _plan(_inventory(40, seed=11), max_aisles=10)
    assert all(cap > 0 for _b, _n, cap, _bud in exc.value.short), (
        'a bucket was trimmed to zero capacity — `_apply_caps` stopped honouring the '
        f'one-aisle-per-bucket floor: {exc.value.short[:3]}')


def test_an_uncapped_plan_covers_every_bucket_requirement_at_the_declared_fill():
    """The promise itself, stated the way the planner checks it: emitted x fill >= need.

    THESE ARE RESTATEMENTS, deliberately.  Each one is implied by `plan_warehouse` having
    returned at all — `requirement > budget` IS the refusal condition — so none is an
    independent oracle, and a reader should not mistake them for one.  They earn their place
    against the combined failure the refusal cannot catch on its own: sizing that under-sizes
    AND a promise check that has been removed or defanged.  `expected_fill` is here for a
    different reason: it is the only bound left on that field since the cross-tier fill test
    it used to live in was retired, and it is persisted into a schema-governed column.
    """
    plan = _plan(_inventory(120, seed=9))
    short = [(b, t) for b, t in plan.fielding.items() if t['requirement'] > t['budget']]
    assert not short, f'{len(short)} bucket(s) short of their requirement, e.g. {short[:3]}'
    assert sum(t['requirement'] for t in plan.fielding.values()) == sum(
        plan.requirement.values()), 'the fielding table and the requirement disagree'
    assert all(t['free'] >= 0 for t in plan.fielding.values()), (
        'a bucket reports negative free bins')

    # expected_fill is now "the share of emitted bins the declared levels occupy" — the bins
    # the requirement asks for over the bins built.  It can never exceed the fill the sizing
    # targeted, because every bucket was sized at `ceil(need / (eff x fill))`.
    assert 0.0 < plan.expected_fill <= _TARGET + 1e-9, (
        f'expected_fill {plan.expected_fill:.4f} outside (0, {_TARGET}]')
    assert abs(plan.expected_fill
               - sum(plan.requirement.values()) / plan.total_bins) < 1e-9, (
        'expected_fill is not the requirement over the emitted bins')
    assert _plan(_inventory(120, seed=9), sample=False).expected_fill == 0.0, (
        'a shape-only plan fields nothing, so it has no fill to report')


def test_planning_the_same_catalogue_twice_gives_the_same_plan():
    """Determinism, as a test rather than a docstring claim.

    The planner used to shuffle the orders and hold a seeded contest for bin capacity, so two
    plans of one catalogue differed unless the caller passed the same `rng`.  Fielding the
    declaration removed the contest, and `plan_warehouse` no longer takes a seed at all — so
    the packing, the capacity and the requirement must agree exactly, every time.
    """
    a = _plan(_inventory(80, seed=5))
    b = _plan(_inventory(80, seed=5))
    assert a.capacity == b.capacity and a.requirement == b.requirement
    assert ([c.stock_plan for c in a.sampled] == [c.stock_plan for c in b.sampled]), (
        'two plans of one catalogue packed its SKUs differently')
    assert 'rng' not in inspect.signature(
        Inventory_Manager.plan_warehouse.__func__).parameters, (
        'plan_warehouse took a seed again — planning is deterministic, and a seed that '
        'changes nothing is a knob with no consumer')


def test_min_bins_scales_the_warehouse_up_past_its_natural_size():
    """The lever that makes a warehouse deliberately roomy (a low-contention arm)."""
    inv    = _inventory(60, seed=5)
    base   = _plan(inv)
    target = base.total_bins * 3 + 20000           # well above the natural size
    plan   = _plan(inv, min_bins=target)

    assert plan.total_bins >= target, f'{plan.total_bins} bins < min_bins {target}'
    empty = [k for k, v in plan.capacity.items() if v <= 0]
    assert not empty, f'min_bins scaling starved {len(empty)} bucket(s): {empty[:3]}'


def test_min_bins_wins_when_it_contradicts_max_bins():
    """A config can set both; the floor is the one that keeps the plan feasible."""
    plan = _plan(_inventory(60, seed=5), min_bins=30000, max_bins=5000)
    assert plan.total_bins >= 30000, (
        f'{plan.total_bins} bins — max_bins=5000 was applied over min_bins=30000')


def test_a_composition_vector_sets_the_bin_tier_ratios():
    """`composition` is how an arm asks for a differently-SHAPED warehouse of the same size.

    Without it, tier ratios follow the catalogue's natural palletization and two arms
    comparing "the same warehouse, different bin mix" would be comparing identical
    warehouses.  Tolerances are 5 percentage points: the plan works in whole aisles, so it
    can only approximate a ratio.
    """
    comp = {'unit': {'pallet': 0.7, 'singleton': 0.3},
            'size': {'small': 0.1, 'medium': 0.2, 'large': 0.3, 'extra_large': 0.4}}
    plan = _plan(_inventory(60, seed=5), min_bins=60000, composition=comp)

    bins: dict = defaultdict(int)
    for (_h, _c, s, u), n in plan.capacity.items():
        bins['singleton' if u == 'singleton' else s] += n
    tot = sum(bins.values())

    assert tot >= 60000, f'{tot} bins < min_bins 60000 with a composition vector'
    singleton_frac = bins['singleton'] / tot
    assert abs(singleton_frac - 0.30) < 0.05, (
        f'singleton share {singleton_frac:.1%}, asked for 30% (+/-5pp)')

    pallet_tot = tot - bins['singleton']
    for size, want in [('small', 0.1), ('medium', 0.2), ('large', 0.3), ('extra_large', 0.4)]:
        frac = bins[size] / pallet_tot
        assert abs(frac - want) < 0.05, (
            f'{size} tier is {frac:.1%} of pallet bins, asked for {want:.0%} (+/-5pp)')


def test_the_sampler_and_the_fixed_distribution_are_gone_and_stay_gone():
    """Two retirements, guarded so they cannot drift back in unnoticed.

    `sample_to_capacity` held a contest for bin capacity and re-declared each order at what
    it won; `mode` / `distribution` / `target_bins` spread fulfillment's bins by a fixed
    ratio that ignored the demand mix.  A retired KNOB is the more dangerous of the two: a
    sizing dict is assembled from CONFIG and restored from a run spec, and a key nobody reads
    is how a caller comes to believe it asked for something it did not get.  So the planner
    refuses one rather than ignoring it, and CONFIG must not carry one to refuse.
    """
    assert not hasattr(Inventory_Manager, 'sample_to_capacity'), (
        'sample_to_capacity is back — the planner fields the requirement, it does not sample')
    retired = {'mode', 'distribution', 'target_bins'}
    for ch in ('store', 'fulfillment'):
        present = retired & set(CONFIG['channels'][ch]['sizing'])
        assert not present, f"CONFIG['channels'][{ch!r}]['sizing'] carries {sorted(present)}"

    inv = _inventory(40, seed=11)
    sizing = {'store': {'fill': _TARGET}, 'fulfillment': {'fill': _TARGET}}
    ok = _plan(inv, regime_sizing=sizing)
    with pytest.raises(ValueError, match=r'no longer reads'):
        _plan(inv, regime_sizing={'store': {'fill': _TARGET},
                                  'fulfillment': {'fill': _TARGET, 'mode': 'fixed',
                                                  'distribution': {'ff_small': 1.0}}})
    assert ok.capacity, 'the control plan built nothing, so the refusal above proves little'


def test_a_composition_vector_that_starves_a_bucket_refuses():
    """The basis vector is checked against the promise exactly as a cap is.

    It allocates bins by RATIO, so a warehouse can be the right size overall and still leave
    a bucket short — the same shape of failure as the retired fulfillment tier distribution,
    which spread bins 0.5/0.3/0.2 while the levels needed 11/63/26.  The scale here is well
    inside the boundary (five buckets short, the worst by 200 bins of 319, against a boundary
    that sits between 20,000 and 25,000) so the test measures the rule, not the edge.
    """
    comp = {'unit': {'pallet': 0.7, 'singleton': 0.3},
            'size': {'small': 0.1, 'medium': 0.2, 'large': 0.3, 'extra_large': 0.4}}
    with pytest.raises(UnfieldableRequirement) as exc:
        _plan(_inventory(60, seed=5), min_bins=8000, composition=comp)
    assert len(exc.value.short) >= 2, (
        f'only {len(exc.value.short)} bucket(s) short — this scale is meant to be well '
        f'inside the refusal boundary, not on it: {exc.value.short}')
    worst = max(need - bud for _b, need, _c, bud in exc.value.short)
    assert worst >= 50, f'the worst shortfall is only {worst} bins; the fixture has drifted'


# ═════════════════════════════════════════════════════════════════════════════
# Fielding: the declaration is what the warehouse holds
# ═════════════════════════════════════════════════════════════════════════════

def test_sampling_never_exceeds_per_bucket_capacity():
    """Sampling picks SKUs to fill the plan; it must not pick more of a shape than fits.

    Checked at 150 SKUs — well above the 80-SKU plans elsewhere — because over-commitment
    only appears once the catalogue is big enough to contend for a tier.
    """
    plan   = _plan(_inventory(150, seed=13))
    demand = Inventory_Manager.bucket_requirements(plan.sampled)
    over   = [(b, demand[b], plan.capacity.get(b, 0))
              for b in demand if demand[b] > plan.capacity.get(b, 0)]
    assert not over, (
        f'{len(over)} bucket(s) over-committed by sampling; '
        f'(bucket, sampled, capacity) = {over[:3]}')


def test_fielding_is_the_declaration_exactly_neither_grown_nor_shrunk():
    """The one planner contract, on a whole catalogue: what was declared is what is fielded.

    Both directions were live defects, and both were silent.  Phase 2 GREW 64,989 SKUs into
    leftover capacity, so a level the record called declared was not the level the run held
    and the fill rate priced pre-plan disagreed with the one priced post-plan.  Phase 1's
    emptiest-bucket rule left others SHORT of their own line floor, which under base stock is
    a treadmill — the SKU is picked for exactly its shelf every day and its remainder grows
    without bound.  Neither shows up in a capacity assertion; both show up here.
    """
    inv       = _inventory(80, seed=5)
    declared  = {c.sku: c.equilibrium_qty for c in inv.orders}
    rp_before = {c.sku: c.reorder_point for c in inv.orders}
    plan      = _plan(inv)

    assert len(plan.sampled) == len(inv.orders), (
        f'{len(plan.sampled)} of {len(inv.orders)} orders fielded — the planner drops none')
    for c in plan.sampled:
        slots = c.stock_plan
        assert slots, f'SKU {c.sku} was fielded with no packing plan'
        total = sum(per * count for _flag, per, count in slots)
        assert total == declared[c.sku] == c.equilibrium_qty, (
            f'SKU {c.sku}: declared {declared[c.sku]}, fielded {total}, order-up-to now '
            f'{c.equilibrium_qty} — the planner grew or shrank a level it was handed')
        assert c.reorder_point == rp_before[c.sku], (
            f'SKU {c.sku}: reorder point moved {rp_before[c.sku]} -> {c.reorder_point}; the '
            f'planner has no opinion about the policy it was handed')


def test_the_fielded_packing_is_the_one_the_warehouse_was_sized_from():
    """Sizing and fielding must read the SAME statement, or the promise is a coincidence.

    `bucket_requirements` counts the bins the declared levels need by packing each SKU;
    `field_requirement` records that packing as the SKU's `stock_plan`, so every later reorder
    rebuilds the same tier mix.  Re-counting the fielded orders therefore has to reproduce
    `plan.requirement` bucket for bucket — the old sampler's re-choice of tier is exactly what
    made this false, and it charged buckets budgeted for other SKUs.
    """
    plan = _plan(_inventory(150, seed=13))
    assert Inventory_Manager.bucket_requirements(plan.sampled) == plan.requirement, (
        'the packing the planner FIELDED disagrees with the one it SIZED from')
    for b, n in plan.requirement.items():
        assert n <= plan.capacity.get(b, 0), (
            f'bucket {b} needs {n} bins and {plan.capacity.get(b, 0)} were emitted')


def test_a_sku_is_fielded_whole_at_the_packing_its_level_implies():
    """The ticket's worked example, in the small, against a HAND-COMPUTED oracle.

    A 16x16x20 SKU declared at 13 units packs as four medium pallets of three plus a
    one-item singleton remainder — five bins across two buckets.  Both halves matter: the
    four identical pallets exercise the run-length MERGE, and the singleton remainder is the
    slot the `not isinstance(u, Pallet)` trap silently turned into a partial pallet, charging
    a bucket the warehouse was not sized for.  The emptiest-bucket rule this replaced left
    25,241 single-tier SKUs (15.8% of the reference section) two to six units short of their
    own line floor.
    """
    c = _make_carton(1, eq_qty=13, length=16, width=16, height=20)
    fielded, allow = Inventory_Manager.field_requirement([c])

    assert allow == {c.sku} and len(fielded) == 1, f'{len(fielded)} order(s) fielded'
    assert c.stock_plan == [(False, 3, 4), (True, 1, 1)], (
        f'expected four merged pallets of 3 plus a singleton of 1, got {c.stock_plan}')
    total = sum(per * count for _flag, per, count in c.stock_plan)
    assert total == 13, f'declared 13 units, fielded {total}'
    assert Inventory_Manager.bucket_requirements([c]) == {
        ('conveyable', 'food', 'medium', 'pallet'): 4,
        ('conveyable', 'food', 'singleton', 'singleton'): 1,
    }, f'the fielded packing charges {Inventory_Manager.bucket_requirements([c])}'


# ═════════════════════════════════════════════════════════════════════════════
# Queue: the headline — restocks drain and stay bounded
# ═════════════════════════════════════════════════════════════════════════════

def _drain_loop(mgr, plan, n_batches=30, rng_seed=99):
    """Deplete a random 25% of SKUs per batch and restock; return per-batch queue depths.

    Depletion is done by emptying bins directly rather than through PickSimulation: this
    file is about the SIZING and PLACEMENT paths, and a real pick loop would make the
    depletion rate depend on demand sampling.  (`test_reorder_queue.py` is the test that
    closes the same loop through the simulator.)
    """
    rng  = random.Random(rng_seed)
    skus = [c.sku for c in plan.sampled]
    depths: list[int] = []
    for _ in range(n_batches):
        for sku in rng.sample(skus, max(1, len(skus) // 4)):
            bins = (list(mgr._sku_pallet_bins.get(sku, set()))
                    + list(mgr._sku_singleton_bins.get(sku, set())))
            for b in bins:
                if b.storage is not None:
                    qty = b.storage.quantity
                    b.storage = None
                    mgr._notify_bin_emptied(b)
                    mgr._notify_pick(sku, qty)
        mgr.check_reorders()
        depths.append(mgr.queue_depth)
    return depths


def _assert_bounded(depths, total_bins, label=''):
    tag = f'[{label}] ' if label else ''
    first, second = mean(depths[:15]), mean(depths[15:])
    assert second <= first + 0.05 * total_bins, (
        f'{tag}queue growing: first-half mean {first:.0f} -> second-half mean {second:.0f} '
        f'(slack is 5% of {total_bins} bins)')
    assert max(depths) < 0.20 * total_bins, (
        f'{tag}queue peaked at {max(depths)}, over 20% of the warehouse ({total_bins} bins)')


def test_restocks_do_not_grow_the_queue():
    """The headline regression: 30 batches of aggressive depletion, and the queue is flat.

    A restock that fires but cannot place stays queued and is re-ordered next batch, so the
    queue grows linearly forever.  The run does not fail — it just gets slower and emptier.
    """
    plan = _plan(_inventory(100, seed=21))
    mgr  = Inventory_Manager(_build_wh(plan, 21))
    mgr.enqueue_all(plan.sampled)
    _assert_bounded(_drain_loop(mgr, plan), plan.total_bins)


def test_a_partially_placeable_order_queues_the_remainder_and_drains_as_space_frees():
    """6 singleton bins, 10 singleton units: placement must be PARTIAL, not all-or-nothing.

    Two failure modes this rules out: refusing the whole order because it does not fit
    (the warehouse stays empty), and losing the overflow (the queue is empty and 4 units
    have vanished).  Then freeing exactly one bin must place exactly one more unit.
    """
    Aisle.next_aisle_id = 1
    random.seed(0)
    w   = aisle_width_for(2)      # 96 -> 96//16 = 6 singleton columns
    cfg = WarehouseConfig(total_aisles=1, aisle_splits=[1.0], aisle_configs=[
        AisleConfig('conveyable', 'food', 'singleton', w, 48, ['singleton'], None)])
    wh  = Warehouse_Builder().from_config(cfg).build()
    mgr = Inventory_Manager(wh)
    n_bins = len(wh.bins)
    assert n_bins == 6, f'fixture expects exactly 6 singleton bins, built {n_bins}'

    # 10 singleton units of 1 item each — the packing rides the declaration.
    c = _make_carton(1, eq_qty=10, stock_plan=[(True, 1, 10)])
    mgr.enqueue(c, quantity=10)

    placed = len(mgr.unavailable)
    assert placed == n_bins, (
        f'{placed} of 10 units placed into {n_bins} bins — placement should fill every bin')
    assert mgr.queue_depth == 10 - placed, (
        f'{mgr.queue_depth} queued, expected {10 - placed}: '
        f'{10 - placed - mgr.queue_depth} unit(s) went missing')

    before = mgr.queue_depth
    b = next(b for b in mgr.unavailable if b.storage is not None)
    b.storage = None
    mgr._notify_bin_emptied(b)
    mgr.check_reorders()
    assert mgr.queue_depth == before - 1, (
        f'freed one bin, queue went {before} -> {mgr.queue_depth}; expected exactly one '
        f'unit to drain')


def test_an_oversized_tier_unit_splits_into_a_free_smaller_tier():
    """The rescue path: a unit whose natural tier has no bin is SPLIT rather than queued.

    Without it, a warehouse with only small bins would queue every medium+ unit forever even
    though the stock physically fits — capacity is there, it is just shaped differently.
    """
    Aisle.next_aisle_id = 1
    random.seed(0)
    w, h = aisle_width_for(2), aisle_height_for(4)
    cfg  = WarehouseConfig(total_aisles=1, aisle_splits=[1.0], aisle_configs=[
        AisleConfig('conveyable', 'food', 'pallet', w, h, ['small'], None)])
    wh   = Warehouse_Builder().from_config(cfg).build()
    mgr  = Inventory_Manager(wh)

    # A 48x48x12 order makes a small pallet at qty 1, medium at 2, extra_large at 4.  Find
    # the smallest quantity that forces a >= medium pallet, for which this warehouse has no
    # bin at all.  Deterministic given the dimensions — a fixture precondition, not a skip.
    c = _make_carton(1, eq_qty=1, length=48, width=48, height=12)
    big_q = tier = None
    for q in range(1, 50):
        try:
            t = Pallet(c, q).storage_size
        except ValueError:
            break
        if _SIZE_RANKS[t] >= _SIZE_RANKS['medium']:
            big_q, tier = q, t
            break
    assert big_q is not None, (
        'no quantity of a 48x48x12 order produces a >= medium pallet; the fixture no longer '
        'sets up the rescue it is testing')
    assert _SIZE_RANKS[tier] >= _SIZE_RANKS['medium'], tier

    # Same level, now with the packing that forces the oversized tier — re-declared rather
    # than assigned, because `declare_stock` is the one mutation site for the level slots.
    c.declare_stock(c.equilibrium_qty, c.reorder_point, stock_plan=[(False, big_q, 1)])
    mgr.enqueue(c, quantity=big_q)

    assert mgr.queue_depth == 0, (
        f'{mgr.queue_depth} units queued: the {tier} unit was not split into the small tier')
    assert len(mgr.unavailable) >= 1, 'nothing was placed at all'
    tiers = {b.storage_size for b in mgr.unavailable if b.storage is not None}
    assert tiers <= {'small'}, (
        f'units landed in {tiers}, but only the small tier exists in this warehouse')


def _run_batch_drain(build_fn, label: str):
    """30-batch drain with a ranked wave assignment fn wired in, exactly as strategy_runner
    does it — plus a BinKey-group integrity sweep over the final layout.

    The ranked path (`_stock_ranked`) is a different code path from the per-unit placement
    the tests above exercise: it places a whole wave at once and hands out pre-sorted bins.
    A group violation there would silently put a non-conveyable pallet in a conveyable
    singleton aisle, which no queue metric would ever show.
    """
    inv  = _inventory(80, seed=31)
    plan = _plan(inv)
    mgr  = Inventory_Manager(_build_wh(plan, 31))
    mgr.enqueue_all(plan.sampled)
    assert mgr.queue_depth == 0, (
        f'[{label}] {mgr.queue_depth} units queued after initial stock — the drain test '
        f'starts from a broken plan')

    # Null affinity: the ranked policies use lift only as a minor tie-break, so they are
    # exempt from the _require_affinity guard and rank fine on frequency alone.
    aff = types.SimpleNamespace(_matrix=None, _sku_to_idx={},
                                sum_lift=lambda skus: 0.0,
                                delta_lift_idxs=lambda s, idxs: 0.0)
    wp  = types.SimpleNamespace(x_speed=1.0, y_speed=1.0, pick_intercept=1.0, pick_per_item=0.5,
                                pick_weight_coef=0.5, pick_volume_coef=0.5)
    mgr.placement = Placement('test_ranked', mgr.placement.place_one, build_fn(
        aff, wp, mgr._aisle_sku_sets, mgr._aisle_idx_sets, mgr._aisle_demand_sum,
        freq_by_idx={}, freq_by_sku={}, qty_by_sku={}, beta=1.0))

    _assert_bounded(_drain_loop(mgr, plan, rng_seed=7), plan.total_bins, label)

    # Every placed unit must sit in a bin of its own (handling, category, unit_type) and a
    # tier at least its own size (smallest-fit spills UP, never down).
    violations = []
    for b in mgr.unavailable:
        u = b.storage
        if u is None:
            continue
        shc = u.order.storage_handle_config
        if (b.handling_type != shc.handling or b.storage_type != shc.category
                or b.unit_type != u.unit_category):
            violations.append(('group', b.location, (b.handling_type, b.storage_type, b.unit_type),
                               (shc.handling, shc.category, u.unit_category)))
        elif u.unit_category == 'pallet' and u.storage_size is not None:
            if _SIZE_RANKS[b.storage_size] < _SIZE_RANKS[u.storage_size]:
                violations.append(('tier', b.location, b.storage_size, u.storage_size))
    assert not violations, (
        f'[{label}] {len(violations)} unit(s) outside their BinKey group/tier; '
        f'(kind, bin, got, want) = {violations[:3]}')


def test_ranked_minimizing_drain_stays_bounded_and_in_group():
    _run_batch_drain(build_ranked_minimizing_assignment_fn, 'min')


def test_ranked_maximizing_drain_stays_bounded_and_in_group():
    _run_batch_drain(build_ranked_maximizing_assignment_fn, 'max')


# ═════════════════════════════════════════════════════════════════════════════
# Round-trip: the plan must survive to a worker process
# ═════════════════════════════════════════════════════════════════════════════

def test_the_planned_inventory_reproduces_exactly_after_a_db_round_trip():
    """Mirrors the real multi-process flow, which is where this can break invisibly.

    The main process plans (grown `equilibrium_qty` + cross-tier `stock_plan`), writes a DB,
    and worker processes RELOAD from it.  If `stock_plan` did not persist, each worker would
    fall back to default palletization, target the natural tier only, and queue everything
    the cross-tier fill was counting on — in a subprocess, where the queue depth is not
    printed and nothing raises.

    The contract survives ADR-0002; its FILE does not.  `cartons` no longer has the four
    level columns — a run's declaration rides the separate `stock_levels` table — so the
    round trip is asserted at both ends: the table physically carries a row per planned SKU,
    and every reloaded order comes back DECLARED with the same level and packing.  Without
    the physical check, a save that quietly wrote nothing and a load that quietly read from
    somewhere else would look the same as success.
    """
    plan = _plan(_inventory(120, seed=5))
    planned = Inventory.__new__(Inventory)
    planned.orders = plan.sampled

    db = os.path.join(tempfile.gettempdir(), 'planned_sizing_test.db')
    if os.path.exists(db):
        os.remove(db)
    try:
        save_inventory_to_db(planned, db, {'planned': True})

        conn = sqlite3.connect(db)
        try:
            level_skus  = {r[0] for r in conn.execute('SELECT sku FROM stock_levels')}
            carton_cols = {r[1] for r in conn.execute('PRAGMA table_info(cartons)')}
        finally:
            conn.close()

        reloaded = load_inventory_from_db(db)
    finally:
        if os.path.exists(db):
            os.remove(db)

    assert carton_cols.isdisjoint({'equilibrium_qty', 'reorder_point', 'stock_plan'}), (
        f'`cartons` still carries level columns {sorted(carton_cols & {"equilibrium_qty", "reorder_point", "stock_plan"})} '
        f'— the round trip below may be going through the retired catalogue-authored path')
    assert level_skus == {c.sku for c in plan.sampled}, (
        f'`stock_levels` holds {len(level_skus)} row(s) for {len(plan.sampled)} planned '
        f'SKUs — the run\'s declaration is not reaching the file the workers read')

    assert reloaded.orders, 'nothing reloaded from the planned DB'
    undeclared = [c.sku for c in reloaded.orders if not c.stock_declared()]
    assert not undeclared, (
        f'{len(undeclared)}/{len(reloaded.orders)} reloaded SKUs came back with NO stock '
        f'declaration, e.g. {undeclared[:3]} — a worker would raise UndeclaredStock on them')
    no_plan = [c.sku for c in reloaded.orders if not getattr(c, 'stock_plan', None)]
    assert not no_plan, (
        f'{len(no_plan)}/{len(reloaded.orders)} reloaded SKUs lost their stock_plan, '
        f'e.g. {no_plan[:3]}')

    eq_by_sku = {c.sku: c.equilibrium_qty for c in plan.sampled}
    rp_by_sku = {c.sku: c.reorder_point   for c in plan.sampled}
    changed = [(c.sku, eq_by_sku[c.sku], c.equilibrium_qty)
               for c in reloaded.orders if c.equilibrium_qty != eq_by_sku[c.sku]]
    assert not changed, (
        f'{len(changed)} grown equilibrium_qty value(s) changed in the DB; '
        f'(sku, planned, reloaded) = {changed[:3]}')
    rp_changed = [(c.sku, rp_by_sku[c.sku], c.reorder_point)
                  for c in reloaded.orders if c.reorder_point != rp_by_sku[c.sku]]
    assert not rp_changed, (
        f'{len(rp_changed)} reorder_point value(s) changed in the DB; the declaration is '
        f'both levels, and a worker restocking at the wrong trigger runs empty silently; '
        f'(sku, planned, reloaded) = {rp_changed[:3]}')

    # Worker flow: build the planned warehouse, enqueue the RELOADED orders.
    mgr = Inventory_Manager(_build_wh(plan, 5))
    mgr.enqueue_all(reloaded.orders)
    assert mgr.queue_depth == 0, (
        f'{mgr.queue_depth} units queued by a worker replaying the plan — the cross-tier '
        f'stock_plan did not survive the round trip')
    placed = {b.storage.order.sku for b in mgr.unavailable if b.storage is not None}
    unplaced = [c.sku for c in reloaded.orders if c.sku not in placed]
    assert not unplaced, f'{len(unplaced)} reloaded SKUs unplaced, e.g. {unplaced[:3]}'


# ═════════════════════════════════════════════════════════════════════════════
# Placement: the named assignment scorers
# ═════════════════════════════════════════════════════════════════════════════

def test_uniform_aisle_assignment_randomises_the_aisle_but_minimises_within_it():
    """Two properties that pull in opposite directions, and both must hold.

    The aisle is chosen UNIFORMLY (that is the baseline arm's whole definition — no
    demand signal), but within the chosen aisle the bin is the cheapest available.  A
    version that also randomised the bin would be a different, worse baseline; one that
    also optimised the aisle would not be a baseline at all.
    """
    plan   = _plan(_inventory(80, seed=5))
    mgr    = Inventory_Manager(_build_wh(plan, 5))
    wp     = types.SimpleNamespace(x_speed=1.0, y_speed=1.0)
    assign = build_uniform_aisle_trip_min_assignment_fn(wp, rng=random.Random(1))

    # Find a unit whose candidate bins span >= 2 aisles.  Deterministic for this plan and
    # seed: a fixture precondition, so it asserts rather than skipping — if it ever stops
    # holding, the test below is measuring nothing and must be told, not quietly dropped.
    cand = unit = None
    for c in plan.sampled:
        u  = viable_storage_units(c, c.equilibrium_qty)[0]
        cu = mgr._candidates(u)
        if len({b.location[0] for b in cu}) >= 2:
            cand, unit = cu, u
            break
    assert cand is not None, (
        'no sampled unit has candidate bins in 2+ aisles, so aisle choice cannot be '
        'exercised — the fixture no longer sets up what this test measures')

    chosen_aisles = set()
    for _ in range(40):
        b   = assign(unit, cand)
        aid = b.location[0]
        chosen_aisles.add(aid)
        in_aisle = [x for x in cand if x.location[0] == aid]
        best     = min(_D(x, wp.x_speed, wp.y_speed) for x in in_aisle)
        got      = _D(b, wp.x_speed, wp.y_speed)
        assert got <= best + 1e-9, (
            f'chose a bin at D={got:.4f}s in aisle {aid} when D={best:.4f}s was available '
            f'({len(in_aisle)} candidates there)')

    assert len(chosen_aisles) >= 2, (
        f'40 draws all landed in {chosen_aisles} — the aisle choice is not random')


def test_batch_assignment_gives_the_highest_priority_unit_the_cheapest_bin():
    """The rearrangement-inequality property of a ranked WAVE: sorted by priority, bins
    handed out cheapest-first, and no bin handed out twice.

    Candidate bins are deliberately SHUFFLED before the call.  With them pre-sorted the test
    passes whether or not the internal sort runs at all — which is precisely how it used to
    pass while every travel score was NaN (`y_speed=0.0` -> `sec_per_inch` -> `inf * 0`),
    because a stable sort on all-NaN keys leaves the input order untouched.
    """
    class _B:
        __slots__ = ('location', 'x_phys', 'y_phys')

        def __init__(self, i):
            self.location = (1, i, 0)     # one aisle, so only the bin choice is in play
            self.x_phys   = i             # y_phys 0 -> D is monotone in x_phys
            self.y_phys   = 0

    cands = [_B(i) for i in range(6)]
    random.Random(3).shuffle(cands)

    freqs = [0.1, 0.9, 0.5, 0.7]           # sku i -> relative frequency
    units = [types.SimpleNamespace(order=types.SimpleNamespace(
                sku=i, weight=1, volume=lambda: 1, labor_cost=1.0,   # = pick_intercept (pw=pv=0)
                demand=types.SimpleNamespace(relative_frequency=f)))
             for i, f in enumerate(freqs)]
    aff = types.SimpleNamespace(_matrix=None, _sku_to_idx={})
    wp  = types.SimpleNamespace(x_speed=1.0, y_speed=1.0,
                                pick_intercept=1.0, pick_per_item=0.5,
                                pick_weight_coef=0.0, pick_volume_coef=0.0)

    fn  = build_ranked_minimizing_assignment_fn(
        aff, wp, defaultdict(set), defaultdict(set), defaultdict(float), {}, {}, {}, beta=1.0)
    res = fn(units, lambda u: cands)

    by_sku = {u.order.sku: b for u, b in res}
    # Effort is constant across units, so priority is pure frequency: [1, 3, 2, 0].
    ranking  = sorted(range(4), key=lambda i: freqs[i], reverse=True)
    expected = {sku: rank for rank, sku in enumerate(ranking)}
    for sku in range(4):
        assert by_sku[sku].x_phys == expected[sku], (
            f'sku {sku} (freq {freqs[sku]}, priority rank {expected[sku]}) got the bin at '
            f'x={by_sku[sku].x_phys}, expected x={expected[sku]}')

    ids = [id(b) for _u, b in res]
    assert len(ids) == len(set(ids)), (
        f'{len(ids) - len(set(ids))} bin(s) assigned to more than one unit')


def test_cluster_assignment_co_locates_scatters_and_tie_breaks_on_travel():
    """All three behaviours of the cohesion scorer, on one fixed pair of candidates.

    Aisle 10 is far (x=9) and holds the affinity partner; aisle 20 is near (x=1) and empty.
      - cohesion_max must take 10 — that is what "cluster" means, and it costs travel;
      - cohesion_min must take 20 — the mirror arm, or the two are the same policy;
      - with NO partner placed anywhere, lift is 0 for both and the tie-break falls through
        to travel, so cohesion_max must take the near aisle 20.

    That third case is the one that was failing silently: the stub used `y_speed=0.0`, which
    `sec_per_inch` maps to `inf`, so `inf * y_phys(0)` made every score NaN and the scorer
    returned whichever aisle it saw first.  See
    `test_assignment_functions.test_zero_y_speed_must_not_silently_produce_nan_travel_scores`.
    """
    class _B:
        __slots__ = ('location', 'x_phys', 'y_phys')

        def __init__(self, aid, x):
            self.location = (aid, 0, 0)
            self.x_phys   = x
            self.y_phys   = 0

    aff, idx = _aff_store([1, 2], [(1, 2, 5.0)])          # sku1 <-> sku2, lift 5
    wp    = types.SimpleNamespace(x_speed=1.0, y_speed=1.0)
    fbi   = {idx[2]: 1.0, idx[1]: 0.5}
    fbs   = {1: 0.5, 2: 1.0}
    qbs   = {1: 1.0, 2: 1.0}
    cands = [_B(10, 9.0), _B(20, 1.0)]
    unit  = types.SimpleNamespace(order=types.SimpleNamespace(sku=1))

    def with_partner():
        ss, ii, dd = defaultdict(set), defaultdict(set), defaultdict(float)
        ss[10] = {2}
        ii[10] = {idx[2]}                                 # sku2 already placed in aisle 10
        return ss, ii, dd

    ss, ii, dd = with_partner()
    b = build_cluster_maximizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(unit, cands)
    assert b.location[0] == 10, (
        f'cohesion_max chose aisle {b.location[0]}; it must pay the extra travel to sit with '
        f'its partner in aisle 10')

    ss, ii, dd = with_partner()
    b = build_cluster_minimizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(unit, cands)
    assert b.location[0] == 20, (
        f'cohesion_min chose aisle {b.location[0]}; it must scatter away from the partner')

    ss, ii, dd = defaultdict(set), defaultdict(set), defaultdict(float)   # nothing placed
    b = build_cluster_maximizing_assignment_fn(aff, wp, ss, ii, dd, fbi, fbs, qbs)(unit, cands)
    assert b.location[0] == 20, (
        f'cohesion_max chose aisle {b.location[0]} with no partner anywhere; with lift tied '
        f'at 0 the tie-break is travel, so the near aisle 20 must win')


def test_the_batch_sampler_correlates_co_picked_skus_and_refuses_a_bad_affinity():
    """Affinity must actually change the batches, and a broken store must not be ignored.

    A sampler that silently fell back to uniform would make every affinity-driven arm a
    duplicate of the baseline, and the comparison would report a real-looking 0% difference.
    """
    from Warehouse.picking.Workload_Builder import Batch, BatchConfig

    orders = [_make_carton(i, 30) for i in range(1, 7)]   # conveyable/food
    for c in orders:
        c.demand = Demand.from_rates(0.9, 4.0)             # freq 0.9 -> usually a candidate
    inv    = types.SimpleNamespace(orders=orders)
    aff, _ = _aff_store([1, 2, 3, 4, 5, 6], [(1, 2, 8.0)])  # strong lift between sku1 & sku2
    cfg    = BatchConfig(inventory_size=6, mean_fraction=0.5, std_fraction=0.0)

    def cooccur(affinity, n=400, seed=1):
        random.seed(seed)                                  # Batch draws from module-level random
        both = sum(1 for _ in range(n)
                   if {1, 2} <= set(Batch(cfg, inv, affinity=affinity).items))
        return both / n

    p_aff, p_uni = cooccur(aff), cooccur(None)
    assert p_aff > p_uni + 0.05, (
        f'sku1 & sku2 co-occur in {p_aff:.1%} of affinity batches vs {p_uni:.1%} uniform — '
        f'a lift of 8.0 is not moving the sampler')

    try:
        Batch(cfg, inv, affinity='not-an-affinity')
    except TypeError:
        pass
    else:
        raise AssertionError(
            'Batch accepted a str as an affinity store and fell back to uniform sampling '
            'silently — an affinity arm would become a copy of the baseline')


def test_init_lift_state_backfills_the_aisle_sets_from_existing_stock():
    """Stock placed before an affinity store exists leaves the aisle sets EMPTY.

    That is the bug surface: initial stocking runs affinity-free, so a cohesion scorer wired
    in afterwards would see an empty warehouse and place its first wave as if nothing had
    been stocked.  `init_lift_state` is the repair, and both halves of it matter — the SKU
    sets (used by the aisle guard) and the index sets (used by the CSR lift query).
    """
    plan = _plan(_inventory(80, seed=5))
    wh   = _build_wh(plan, 5)
    mgr  = Inventory_Manager(wh)                          # affinity=None during stocking
    mgr.enqueue_all(plan.sampled)

    populated = sum(1 for v in mgr._aisle_sku_sets.values() if v)
    assert populated == 0, (
        f'{populated} aisles already have SKU sets after affinity-free stocking — this test '
        f'no longer starts from the state init_lift_state exists to repair')

    aff, _ = _aff_store([c.sku for c in plan.sampled], [])   # full sku_to_idx, no pairs
    mgr._affinity = aff
    mgr.init_lift_state(aff)

    placed  = {b.storage.order.sku for b in wh.bins if b.storage is not None}
    in_sets = set().union(*mgr._aisle_sku_sets.values()) if mgr._aisle_sku_sets else set()
    assert in_sets == placed, (
        f'aisle_sku_sets covers {len(in_sets)} SKUs, {len(placed)} are actually placed; '
        f'missing {sorted(placed - in_sets)[:3]}, spurious {sorted(in_sets - placed)[:3]}')
    assert sum(len(v) for v in mgr._aisle_idx_sets.values()) > 0, (
        'aisle_idx_sets is empty, so every CSR lift query returns 0 and cohesion is blind')


# ═════════════════════════════════════════════════════════════════════════════
# Placement: the optimal layout and the Sigma f*D objective
# ═════════════════════════════════════════════════════════════════════════════

def test_the_optimal_layout_minimises_sigma_fd_and_is_monotone_in_travel():
    """`place_optimal` is the yardstick every strategy is scored against, so it has to be
    both OPTIMAL and CORRECTLY MEASURED — five separate claims:

      1. it beats the uniform layout (else the yardstick is not a floor);
      2. `place_optimal` returns what it actually realised (`current_sigma_fd`);
      3. `current_sigma_fd` matches an independent sum over the warehouse's own bins —
         this catches an occupied bin missing from `_unavailable`, which would understate
         the realised cost of every strategy including this one;
      4. `optimal_sigma_fd` (pure computation) agrees with `place_optimal` (which mutates);
      5. within each BinKey class, frequency is non-increasing as travel rises — the
         rearrangement-inequality structure the optimum is built from.

    (3) and (5) are the two assertions that were failing silently: both recomputed travel as
    `x_speed * x_phys + y_speed * y_phys`, but production converts ft/s to a per-inch pace
    first.  At (1.0, 0.5) ft/s that inverts which axis dominates, so (3) was off by 6.6x and
    (5) reported 59 violations of an ordering that in fact holds exactly.  See `_D`.
    """
    x, y = 1.0, 0.5
    plan = _plan(_inventory(120, seed=11))
    freq = {c.sku: c.demand.relative_frequency for c in plan.sampled}

    mgr_u = Inventory_Manager(_build_wh(plan, 11))
    mgr_u.enqueue_all(plan.sampled)
    sig_u = mgr_u.current_sigma_fd(freq, x, y)

    wh_o  = _build_wh(plan, 11)
    mgr_o = Inventory_Manager(wh_o)
    opt   = mgr_o.place_optimal(plan.sampled, freq, x, y)
    sig_o = mgr_o.current_sigma_fd(freq, x, y)

    # 1 + 2
    assert sig_o <= sig_u + 1e-6, (
        f'optimal Sigma f*D {sig_o:.1f} is worse than uniform {sig_u:.1f}')
    assert _close(opt, sig_o), (
        f'place_optimal returned {opt:.6f} but realised {sig_o:.6f}')

    # 3 — independent of _unavailable: iterate the warehouse's own bin list.
    indep = sum(freq.get(b.storage.order.sku, 0.0) * _D(b, x, y)
                for b in wh_o.bins if b.storage is not None)
    assert _close(indep, sig_o), (
        f'current_sigma_fd {sig_o:.6f} != independent sum over wh.bins {indep:.6f} — '
        f'_unavailable and the warehouse disagree about which bins are occupied')

    # 4
    q = Inventory_Manager(_build_wh(plan, 11)).optimal_sigma_fd(plan.sampled, freq, x, y)
    assert _close(q, opt), (
        f'optimal_sigma_fd (no mutation) {q:.6f} != place_optimal {opt:.6f}')

    # 5
    by_key: dict = defaultdict(list)
    for b in wh_o.bins:
        if b.storage is not None:
            by_key[mgr_o._key(b)].append(b)
    violations = []
    for key, bins in by_key.items():
        bins.sort(key=lambda b: _D(b, x, y))
        fs = [freq.get(mgr_o._bin_sku[id(b)], 0.0) for b in bins]
        violations += [(key, i, fs[i], fs[i + 1])
                       for i in range(len(fs) - 1) if fs[i] + 1e-9 < fs[i + 1]]
    assert not violations, (
        f'{len(violations)} place(s) where a HIGHER-frequency SKU sits further out than a '
        f'lower-frequency one within a BinKey class; (key, i, f_i, f_i+1) = {violations[:3]}')

    total_units = sum(len(viable_storage_units(c, c.equilibrium_qty)) for c in plan.sampled)
    occupied    = sum(1 for b in wh_o.bins if b.storage is not None)
    assert occupied == total_units, (
        f'{occupied} bins occupied for {total_units} units — the optimal layout dropped '
        f'{total_units - occupied}')


def test_requeue_bin_frees_the_bin_without_losing_the_inventory_position():
    """Eviction moves stock from ON-HAND to ON-ORDER; it must not destroy any.

    `requeue_bin` is what the Capacity_Reloaders call, dozens of times per batch.  If it
    dropped the unit instead of re-queuing it, inventory would leak steadily and the only
    symptom would be a fill rate that drifts down over a long run.  Position (on-hand +
    queued + deferred) is the conserved quantity, so that is what is asserted.
    """
    x, y = 1.0, 0.5
    plan = _plan(_inventory(120, seed=17))
    freq = {c.sku: c.demand.relative_frequency for c in plan.sampled}
    wh   = _build_wh(plan, 17)
    mgr  = Inventory_Manager(wh)
    mgr.place_optimal(plan.sampled, freq, x, y)

    victim = next(b for b in wh.bins if b.unit_type == 'pallet' and b.storage is not None)
    sku    = mgr._bin_sku[id(victim)]
    qty    = victim.storage.quantity

    def position():
        return (mgr._current_quantities.get(sku, 0) + mgr._queued_qty.get(sku, 0)
                + mgr._deferred_qty.get(sku, 0))

    pos0    = position()
    onhand0 = mgr._current_quantities.get(sku, 0)
    qlen0   = len(mgr._stock_queue)

    mgr.pop_churn()                              # discard prior churn so `rm` is this move only
    mgr.requeue_bin(victim)
    reload_moves, _ = mgr.pop_churn()

    assert victim.storage is None, f'bin {victim.location} still holds stock after requeue'
    assert id(victim) not in mgr._unavailable, 'requeued bin still listed as unavailable'
    assert id(victim) in mgr._bin_index_pos, (
        f'bin {victim.location} freed but never returned to the available index — it is now '
        f'lost to the warehouse')
    assert len(mgr._stock_queue) == qlen0 + 1, (
        f'stock queue {qlen0} -> {len(mgr._stock_queue)}; the evicted unit was not re-queued')
    assert mgr._current_quantities.get(sku, 0) == onhand0 - qty, (
        f'on-hand for sku {sku}: {onhand0} -> {mgr._current_quantities.get(sku, 0)}, '
        f'expected a drop of exactly {qty}')
    assert position() == pos0, (
        f'inventory position for sku {sku} changed {pos0} -> {position()}; an eviction moves '
        f'stock between buckets, it does not create or destroy it')
    assert reload_moves == 1, f'requeue counted {reload_moves} reload moves, expected 1'


def test_capacity_reloaders_respect_their_budget_and_lower_sigma_fd():
    """The three named reloaders, each checked on three properties, plus one convergence run.

    Per variant: it carries its own `name` (the run label is built from it), it evicts
    within the per-aisle budget (an unbudgeted reloader would churn the whole warehouse
    every batch), and it leaves SINGLETON bins alone — reloading is a pallet-only operation
    because singleton bins hold one item and moving them buys nothing.

    Then the convergence claim that justifies the whole mechanism: starting from an
    ANTI-optimal layout (placed by NEGATED frequency, so the hottest SKUs are furthest out),
    40 rounds of rebalance + a ranked re-drain must measurably lower Sigma f*D.
    """
    from Warehouse.placement.Capacity_Reloader import (
        promote_popular_reloader, demote_unpopular_reloader, rebalance_reloader, RELOADERS)

    x, y = 1.0, 0.5
    inv  = _inventory(120, seed=23)
    plan = _plan(inv)
    freq = {c.sku:  c.demand.relative_frequency for c in plan.sampled}
    neg  = {c.sku: -c.demand.relative_frequency for c in plan.sampled}

    assert set(RELOADERS) == {'promote_popular', 'demote_unpopular', 'rebalance'}, (
        f'RELOADERS registry is {sorted(RELOADERS)} — strategies.py looks these up by name')

    for make, nm in [(demote_unpopular_reloader, 'demote_unpopular'),
                     (promote_popular_reloader, 'promote_popular'),
                     (rebalance_reloader, 'rebalance')]:
        wh  = _build_wh(plan, 23)
        mgr = Inventory_Manager(wh)
        mgr.place_optimal(plan.sampled, neg, x, y)
        rl  = make(move_limit_pct=0.5)
        cap = rl.per_aisle_cap(wh)
        n_pallet_aisles = sum(1 for a in wh.aisles if a.unit_type == 'pallet')
        singles_before  = {id(b) for b in wh.bins
                           if b.unit_type == 'singleton' and b.storage is not None}

        mgr.pop_churn()
        rl.reload(mgr, freq, x, y)
        moves, _ = mgr.pop_churn()
        singles_after = {id(b) for b in wh.bins
                         if b.unit_type == 'singleton' and b.storage is not None}

        assert rl.name == nm, f'reloader name is {rl.name!r}, expected {nm!r}'
        assert moves > 0, f'{nm}: evicted nothing from an anti-optimal layout'
        assert moves <= cap * n_pallet_aisles, (
            f'{nm}: {moves} evictions exceeds the budget of {cap} x {n_pallet_aisles} '
            f'pallet aisles = {cap * n_pallet_aisles}')
        assert singles_before == singles_after, (
            f'{nm}: {len(singles_before ^ singles_after)} singleton bin(s) changed — '
            f'reloading must be pallet-only')

    # rebalance + ranked re-drain must converge from an anti-optimal start.
    wh  = _build_wh(plan, 23)
    mgr = Inventory_Manager(wh, affinity=None)
    mgr.place_optimal(plan.sampled, neg, x, y)
    aff, _ = _aff_store([c.sku for c in plan.sampled], [])   # empty-lift store (co_occur = 0)
    mgr._affinity = aff
    mgr.init_lift_state(aff)
    mgr.init_demand_state(inv)

    fbs = {c.sku: c.demand.relative_frequency for c in plan.sampled}
    qbs = {c.sku: c.demand.quantity_rate      for c in plan.sampled}
    wp  = types.SimpleNamespace(x_speed=x, y_speed=y, pick_intercept=1.0, pick_per_item=0.5,
                                pick_weight_coef=0.0, pick_volume_coef=0.0)
    mgr.placement = Placement('test_ranked', mgr.placement.place_one,
                              build_ranked_minimizing_assignment_fn(
                                  aff, wp, mgr._aisle_sku_sets, mgr._aisle_idx_sets,
                                  mgr._aisle_demand_sum, {}, fbs, qbs))

    sig0 = mgr.current_sigma_fd(freq, x, y)
    rl   = rebalance_reloader(move_limit_pct=0.5)
    for _ in range(40):
        rl.reload(mgr, freq, x, y)
        mgr.check_reorders()                                 # ranked drain re-places evictions
    sig1 = mgr.current_sigma_fd(freq, x, y)

    assert sig1 < sig0, (
        f'40 rounds of rebalance + ranked re-drain left Sigma f*D at {sig1:.0f}, no better '
        f'than the anti-optimal start {sig0:.0f}')


def test_incremental_sigma_fd_tracks_the_full_recompute_through_every_mutation():
    """`tracked_sigma_fd` is an O(1) running sum maintained by hand at each mutation site.

    That is the whole point (a full recompute per batch is O(bins)) and also the whole risk:
    a mutation path that forgets to update it drifts away from the truth silently, and the
    convergence metric every strategy is judged on quietly becomes wrong.  Each of the four
    paths is checked against a fresh full recompute.
    """
    x, y = 1.0, 0.5
    plan = _plan(_inventory(120, seed=29))
    freq = {c.sku: c.demand.relative_frequency for c in plan.sampled}
    wh   = _build_wh(plan, 29)
    mgr  = Inventory_Manager(wh)
    mgr.enqueue_all(plan.sampled)
    mgr.enable_sigma_fd(freq, x, y)

    def assert_tracks(stage):
        tracked, full = mgr.tracked_sigma_fd(), mgr.current_sigma_fd(freq, x, y)
        assert _close(tracked, full), (
            f'after {stage}: tracked {tracked:.6f} != full recompute {full:.6f} '
            f'(drift {tracked - full:+.6f})')

    assert_tracks('enable_sigma_fd')

    # pick-empty path (mirrors fast_pick phase 2: clear storage, then notify)
    victim = next(b for b in wh.bins if b.storage is not None)
    victim.storage = None
    mgr._notify_bin_emptied(victim)
    assert_tracks('a bin-empty')

    # eviction path: requeue a pallet (-), then the re-drain re-places it (+)
    mgr.requeue_bin(next(b for b in wh.bins
                         if b.storage is not None and b.unit_type == 'pallet'))
    assert_tracks('an eviction')
    mgr._stock()
    assert_tracks('the re-drain')
