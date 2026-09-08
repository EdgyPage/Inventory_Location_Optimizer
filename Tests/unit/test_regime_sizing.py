"""test_regime_sizing.py — the PER-REGIME warehouse sizing path (plan_warehouse regime_sizing).

Guards the production main() path the older suites don't touch: store and fulfillment sized
INDEPENDENTLY — both from the bins their DECLARED levels need — each with its own bin caps and
fill headroom.  Asserts the two partitions stay isolated (a cap on one never reaches the
other), that fulfillment's retired fixed tier distribution has not come back, and that the
aisle-shape knobs (depth classes, aisle split) leave the line floor's promise intact.

The catalogue here DECLARES its stock, in one place
---------------------------------------------------
`plan_warehouse` counts bins from every SKU's order-up-to on EVERY path (`sample=False`, which
this file uses throughout, skips only the SKU sampling — never `bucket_requirements`), and a
generated catalogue carries no level: ADR-0002 made a level a RUN's declaration, and
`inventory_common._equilibrium_qty` raises `UndeclaredStock` rather than defaulting to 1.  So
`_mixed_orders` declares one, explicitly, reproducing the retired generator's arithmetic (see
`_declare`) — this file is about SIZING, so the numbers stay hand-checkable and every ratio
asserted below keeps the meaning it was tuned against.

That declaration lives in ONE helper on purpose.  Four tests here are INVARIANCE assertions —
a store cap must not move a fulfillment bin, `depth_classes=None` and `aisle_split k=1` must be
byte-identical — and each compares two plans built from the SAME order list.  A declaration
made per-plan instead of per-catalogue could drift by a unit between the two sides, and the
test would report a sizing regression that was really a fixture bug.

Run:  python -m pytest Tests/unit/test_regime_sizing.py -q
"""
from __future__ import annotations

import math

import pytest

from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.inventory_common import UnfieldableRequirement
from Warehouse.generation.generate_inventory import (
    Family, fulfillment_family, build_inventory_from_plan)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}

_COMMON = dict(categories=['food', 'clothing', 'electronic', 'furniture', 'seasonal', 'chemical'],
               handlings=['conveyable', 'non-conveyable'],
               aisle_width=2400, aisle_height=480, sample=False)

# The coverage this file declares at.  A HISTORICAL SHAPE, not a live formula: it is the
# constant `build_inventory_from_plan` used to author with (`EQUILIBRIUM_COVERAGE_BATCHES`),
# reproduced verbatim so the bin and aisle counts every ratio below is measured against stay
# exactly where they were.  Nothing in production derives a level this way any more — a run
# declares from a coverage in DAYS (`Optimization.simconfig.coverage.rescale_section`).
_COVERAGE_BATCHES = 10.0


def _declare(orders):
    """Declare this file's stock level on every SKU of *orders*, and return them.

    The retired generator's arithmetic, unchanged:

        Q  = round(10 batches x expected batch demand)
        rp = ceil(expected x (lead + 1))          # lead is 0 for every SKU built here

    `declare_stock` — the ONE mutation site for the four level slots — applies the clamps the
    generator spelled out inline (`max(1, ...)` on Q; `max(1, min(Q - 1, ...))` on rp, and
    rp == 1 at Q == 1), so the declared pair is identical to the pair this catalogue used to
    arrive carrying.
    """
    already = [c.sku for c in orders if c.stock_declared()]
    assert not already, (
        f'{len(already)} generated SKU(s) already carry a stock level, e.g. {already[:3]} — '
        f'the catalogue is authoring stock again (ADR-0002) and this fixture is no longer the '
        f'only source of the levels every count below is measured from')
    for c in orders:
        e = c.expected_batch_demand
        c.declare_stock(round(_COVERAGE_BATCHES * e),
                        math.ceil(e * (c.lead_time_mean + 1.0)))
    return orders


def _mixed_orders(n=400, seed=1):
    plan = [Family('food', 0.35, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            Family('clothing', 0.25, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            fulfillment_family(share=0.4, cube_sizes=(4, 6, 8))]
    return _declare(build_inventory_from_plan(num_skus=n, plan=plan, seed=seed).orders)


def _bins_by_regime(plan):
    store = sum(n for (h, c, s, u), n in plan.capacity.items() if u != 'fulfillment')
    ff    = sum(n for (h, c, s, u), n in plan.capacity.items() if u == 'fulfillment')
    return store, ff


def _plan(orders, regime_sizing):
    return Inventory_Manager.plan_warehouse(orders, regime_sizing=regime_sizing, **_COMMON)


def _base_sizing():
    return {
        'store': {'min_bins': None, 'max_bins': None, 'max_aisles': None,
                  'composition': None, 'fill': 0.875},
        'fulfillment': {'min_bins': None, 'max_bins': None, 'max_aisles': None,
                        'fill': 0.875},
    }


def _short_units(exc) -> set:
    """The unit types of every short bucket — which PARTITION came up short.

    Read off the exception's STRUCTURED `short` list, never its message: the message
    truncates at twelve buckets (a store cap on this catalogue starves more than that), so a
    substring search over it would turn an absence assertion into a false pass exactly when
    the shortfall got large.  It also returns an empty set if the detail lines ever change
    shape, and `x not in set()` is true.
    """
    return {bucket[3] for bucket, *_ in exc.short}


def test_a_store_cap_refuses_over_store_buckets_only():
    """The two partitions are sized and capped INDEPENDENTLY, and the refusal proves it.

    Halving the store's bins used to shrink the store and leave its levels short; since
    "Field the floor" a cap that binds below a bucket's requirement refuses instead
    (decision 3).  What must still hold is the isolation: a store cap is a statement about
    store buckets, so no fulfillment bucket may appear in the shortfall.
    """
    orders = _mixed_orders()
    bs0, bf0 = _bins_by_regime(_plan(orders, _base_sizing()))
    assert bs0 > 0 and bf0 > 0
    rs = _base_sizing()
    rs['store']['max_bins'] = bs0 // 2
    with pytest.raises(UnfieldableRequirement) as exc:
        _plan(orders, rs)
    short = _short_units(exc.value)
    assert short, 'the refusal names no short bucket, so the assertion below proves nothing'
    assert 'fulfillment' not in short, (
        f'a STORE cap left a fulfillment bucket short: {exc.value.short[:3]}')


def test_a_fulfillment_cap_refuses_over_fulfillment_buckets_only():
    orders = _mixed_orders()
    _bs0, bf0 = _bins_by_regime(_plan(orders, _base_sizing()))
    rs = _base_sizing()
    rs['fulfillment']['max_bins'] = bf0 // 2
    with pytest.raises(UnfieldableRequirement) as exc:
        _plan(orders, rs)
    assert _short_units(exc.value) == {'fulfillment'}, (
        f'a FULFILLMENT cap left a store bucket short: {exc.value.short[:3]}')


def test_a_min_bins_on_one_regime_leaves_the_other_untouched():
    """The invariance the cap tests used to carry, on the knob that still GROWS a partition.
    `min_bins` never breaks the promise (extra bins are free bins), so it is the one that can
    still be read as a plan rather than a refusal."""
    orders = _mixed_orders()
    bs0, bf0 = _bins_by_regime(_plan(orders, _base_sizing()))
    rs = _base_sizing()
    rs['store']['min_bins'] = bs0 * 2
    bs1, bf1 = _bins_by_regime(_plan(orders, rs))
    assert bs1 >= bs0 * 2, f'store min_bins not honoured ({bs0} -> {bs1})'
    assert bf1 == bf0, f'fulfillment bins changed when only store was grown ({bf0} -> {bf1})'


def test_ff_tiers_are_sized_from_the_requirement_not_a_fixed_distribution():
    """Fulfillment is sized exactly as the store is: from what the declared levels need.

    There was a FIXED tier distribution here (0.5 / 0.3 / 0.2 across ff_small / ff_medium /
    ff_large, scaled to a `target_bins`), and it is the whole cause of the finding this map
    was built on: the declared levels needed 11 / 63 / 26, ff_medium's budget drained, and
    every one of the 48,466 SKUs fielded below its line floor could reach ONLY ff_medium
    ("Field the floor", decision 8).  So each tier's capacity must now track ITS OWN
    requirement, and the tier that needs the most bins must have the most.
    """
    # FOUR THOUSAND SKUs, not this file's usual 400, and the count is the test.  At 400 every
    # ff tier lands on the same whole-aisle replica floor and the emitted capacity comes out
    # exactly uniform (1800/1800/1800) against a requirement of 556/1293/1097 — so the
    # requirement's mix is invisible, and a planner that simply gave every tier equal bins
    # would read as correct.  At 4,000 the replica granularity stops dominating and the
    # capacity shares track the requirement's to within 2.4pp.  Costs 0.24s.
    orders = _mixed_orders(n=4000)
    plan   = _plan(orders, _base_sizing())
    tiers = {s: n for (_h, _c, s, u), n in plan.capacity.items() if u == 'fulfillment'}
    need  = {s: n for (_h, _c, s, u), n in plan.requirement.items() if u == 'fulfillment'}
    assert need, 'the fixture declares no fulfillment requirement'
    cap_share  = {s: n / sum(tiers.values()) for s, n in tiers.items()}
    need_share = {s: n / sum(need.values()) for s, n in need.items()}
    # The POSITIVE property: each tier gets the share its own levels ask for.  Asserting only
    # "not the retired 0.5/0.3/0.2 vector" is what let a uniform split through.
    worst = max(abs(cap_share[s] - need_share[s]) for s in tiers)
    assert worst < 0.05, (
        f'ff capacity shares {cap_share} do not track the requirement {need_share} '
        f'(worst {worst:.3f} > 5pp)')
    assert sorted(tiers, key=tiers.get) == sorted(need, key=need.get), (
        f'the tiers rank differently by capacity {tiers} than by requirement {need}')


def test_regime_sizing_none_matches_no_regressions():
    """A store-only catalog with regime_sizing=None must still size (sanity: sizes, ≥1 bucket)."""
    orders = [c for c in _mixed_orders() if c.storage_handle_config.handling != 'fulfillment']
    plan = Inventory_Manager.plan_warehouse(orders, **_COMMON)
    assert plan.total_bins > 0 and plan.total_aisles > 0


def test_an_aisle_splits_capacity_loss_is_paid_for_in_replicas():
    """The split SACRIFICES bins; the promise says the declared levels do not pay for them.

    Sizing inflates a split bucket's demand replicas by `1 / (1 - loss)` ("Field the floor",
    decision 9), so the bins left AFTER the cut still cover the requirement — without it a
    throughway would silently field a section below its line floor, which is the same defect
    a cap causes and the same one this map exists to close.
    """
    orders = _mixed_orders()
    p0 = _plan(orders, _base_sizing())
    for regime, other in (('fulfillment', 'store'), ('store', 'fulfillment')):
        rs = _base_sizing()
        rs[regime]['aisle_split'] = {'k': 2, 'capacity_loss': 0.30}
        plan = _plan(orders, rs)                  # must not refuse
        is_ff = regime == 'fulfillment'

        # FIRST: the split actually fired.  "Did not raise" is not evidence on its own —
        # a change that stopped applying the split to this partition would sacrifice no bin,
        # need no inflation, and pass a promise check that was never under strain.
        assert (len(_configs_by_regime(plan, ff=is_ff))
                > 1.5 * len(_configs_by_regime(p0, ff=is_ff))), (
            f'{regime} aisle count did not grow — the split never applied')

        short = [(b, t) for b, t in plan.fielding.items()
                 if (b[3] == 'fulfillment') == is_ff and t['requirement'] > t['budget']]
        assert not short, f'the split left {len(short)} {regime} bucket(s) short: {short[:3]}'

        b0 = _bins_by_regime(p0)[1 if is_ff else 0]
        b1 = _bins_by_regime(plan)[1 if is_ff else 0]
        # Two-sided: under-inflating breaks the promise, over-inflating buys bins nobody
        # asked for and would pass a one-sided floor silently.
        assert 0.95 <= b1 / b0 <= 1.20, (
            f'{regime} bins went {b0} -> {b1} under a 30% loss; the inflation should cover '
            f'the sacrifice, not overshoot it')


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


def test_depth_classes_never_round_a_bucket_below_its_requirement():
    """A depth split reshapes aisles; it must not spend the declared levels' bins doing it.

    `_ff_depth_split` allocates each class `share x target_bins / eff` aisles, and that
    division rounds.  Rounding DOWN loses bins the bucket was sized for — `_demand_replicas`
    leaves only the ~3% slack of one `ceil` — and the loss is unbounded in the class count:
    measured at 85 to 295 bins short on a single class over this catalogue, which is a
    REFUSED run for a knob whose whole job is aisle shape.  It rounds up now, and this sweeps
    the widths and class counts that used to break, including in combination with an aisle
    split (30 of ~90 configurations refused before the fix; 0 of 42 after).
    """
    orders = _mixed_orders(n=4000, seed=3)
    for cols in (10, 205, 366, 1000):
        for classes in ([{'columns': cols, 'share': 1.0}],
                        [{'columns': 10, 'share': 0.3}, {'columns': cols, 'share': 0.4},
                         {'columns': cols * 2, 'share': 0.3}]):
            for split in (None, {'k': 2, 'capacity_loss': 0.30}):
                rs = _base_sizing()
                rs['fulfillment']['depth_classes'] = classes
                rs['fulfillment']['aisle_split'] = split
                plan = _plan(orders, rs)          # refuses on a rounding loss
                short = [(b, t) for b, t in plan.fielding.items()
                         if b[3] == 'fulfillment' and t['requirement'] > t['budget']]
                assert not short, (
                    f'cols={cols} classes={len(classes)} split={bool(split)} left '
                    f'{len(short)} ff bucket(s) short: {short[:2]}')
                assert len(_ff_widths(plan)) > 1 or len(classes) == 1, (
                    f'cols={cols}: {len(classes)} depth classes emitted one aisle width')


# ── aisle_split: cut aisles into k shorter segments with a capacity loss ──────────

def _configs_by_regime(plan, ff=True):
    return [ac for ac in plan.warehouse_cfg.aisle_configs
            if (ac.unit_type == 'fulfillment') == ff]


def test_aisle_split_k1_is_byte_identical():
    orders = _mixed_orders()
    p0 = _plan(orders, _base_sizing())
    rs = _base_sizing()
    rs['fulfillment']['aisle_split'] = {'k': 1}
    rs['store']['aisle_split'] = {'k': 1}
    p1 = _plan(orders, rs)
    assert p0.capacity == p1.capacity
    assert p0.total_aisles == p1.total_aisles and p0.total_bins == p1.total_bins


def test_aisle_split_ff_shortens_and_grows_bins_preserved():
    orders = _mixed_orders()
    p0 = _plan(orders, _base_sizing())
    bs0, bf0 = _bins_by_regime(p0)
    rs = _base_sizing(); rs['fulfillment']['aisle_split'] = {'k': 2, 'capacity_loss': 0.0}
    p1 = _plan(orders, rs)
    bs1, bf1 = _bins_by_regime(p1)
    assert bs1 == bs0, 'store bins changed by an ff-only aisle split'
    assert len(_configs_by_regime(p1)) > 1.5 * len(_configs_by_regime(p0)), 'ff aisle count did not ~double'
    assert 0.9 * bf0 <= bf1 <= 1.1 * bf0, f'ff bins not preserved at loss=0: {bf0} -> {bf1}'
    assert max(_ff_widths(p1)) < max(_ff_widths(p0)), 'ff aisles not shortened'


def test_aisle_split_capacity_loss_drops_bins():
    # Size the ff warehouse up (min_bins) so per-tier aisle counts are large and the loss is not
    # dominated by the ≥k-segments rounding floor that matters only at tiny tier counts.
    orders = _mixed_orders()
    rs0 = _base_sizing(); rs0['fulfillment']['min_bins'] = 60000
    rs0['fulfillment']['aisle_split'] = {'k': 2, 'capacity_loss': 0.0}
    rs1 = _base_sizing(); rs1['fulfillment']['min_bins'] = 60000
    rs1['fulfillment']['aisle_split'] = {'k': 2, 'capacity_loss': 0.15}
    _, bf0 = _bins_by_regime(_plan(orders, rs0))
    _, bf1 = _bins_by_regime(_plan(orders, rs1))
    assert 0.82 * bf0 <= bf1 <= 0.88 * bf0, f'~15% loss not reflected: {bf0} -> {bf1}'


def test_aisle_split_store_branch():
    orders = _mixed_orders()
    p0 = _plan(orders, _base_sizing())
    bs0, bf0 = _bins_by_regime(p0)
    rs = _base_sizing(); rs['store']['aisle_split'] = {'k': 2, 'capacity_loss': 0.0}
    p1 = _plan(orders, rs)
    bs1, bf1 = _bins_by_regime(p1)
    assert bf1 == bf0, 'fulfillment bins changed by a store-only aisle split'
    assert len(_configs_by_regime(p1, ff=False)) > 1.5 * len(_configs_by_regime(p0, ff=False))


def test_aisle_split_k_bounded():
    from Warehouse.inventory.inventory_planning import MAX_AISLE_SPLIT_K
    orders = _mixed_orders()
    ff0 = len(_configs_by_regime(_plan(orders, _base_sizing())))
    rs = _base_sizing(); rs['fulfillment']['aisle_split'] = {'k': 10_000, 'capacity_loss': 0.0}
    p = _plan(orders, rs)                                    # must not explode / crash
    ff1 = len(_configs_by_regime(p))
    assert ff1 <= ff0 * (MAX_AISLE_SPLIT_K + 1), f'aisle count blew past the k bound: {ff0} -> {ff1}'
