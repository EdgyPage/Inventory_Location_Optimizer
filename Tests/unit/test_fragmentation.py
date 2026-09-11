"""The stationary-fragmentation closed form (`Optimization/simconfig/fragmentation.py`)
against hand computations, the packer it reproduces, and the record it stamps.

The chain prices what ADR-0003's three rules do to a base-stock shelf -- empty-first
top-up, smallest-first drain, plan-slot packing -- as a Markov chain per SKU class.  These
tests pin the packing against `viable_storage_units` on real orders, the drain rule and
its tie split, the chain on a SKU with a two-outcome line law (hand-computable), the
Poisson day mixture, the section sum over a fixture, and the stamp `era_coverage
.fixed_point` leaves on the record (.scratch/department-calibration, "Derive the
stationary fragmentation closed form").
"""
from __future__ import annotations

import inspect
import json
import logging
import math
import sys

import numpy as np
import pytest

import test_coverage_rescale as tcr
from Optimization.simconfig import fragmentation as fr
from Optimization.simdriver import era_coverage as ec
from Warehouse.catalog.Demand import LineDistribution
from Warehouse.catalog.Order import Order
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.inventory.inventory_common import binkey_of
from Warehouse.layout.Storage_Primitive import Singleton, viable_storage_units


# ── fixtures ────────────────────────────────────────────────────────────────────────────

class TwoOutcome:
    """A line law with two outcomes: `q = 1` w.p. `a`, `q = 3` otherwise.  The chain reads
    a law through `cdf` / `quantile` only, so a two-outcome fake is a legal law here and
    makes every number below computable by hand."""
    family = 'two_outcome'

    def __init__(self, a: float) -> None:
        self.a = float(a)
        self.params = {'a': self.a}

    def cdf(self, k: int) -> float:
        return 0.0 if k < 1 else (self.a if k < 3 else 1.0)

    def quantile(self, p: float) -> int:
        return 3

    def mean(self) -> float:
        return self.a + 3.0 * (1.0 - self.a)


class _RareTwo(TwoOutcome):
    """`q = 1` w.p. 0.999, `q = 2` otherwise: a law that never empties a shelf of 5."""
    family = 'rare_two'

    def __init__(self) -> None:
        super().__init__(0.999)

    def cdf(self, k: int) -> float:
        return 0.0 if k < 1 else (self.a if k < 2 else 1.0)

    def quantile(self, p: float) -> int:
        return 2


def _order(sku, *, freq=0.5, qty=2.0, handling='conveyable', category='seasonal',
           dims=(10, 10, 10), eq=3, rp=2, plan=((True, 3, 1),), lead=0.0):
    """Every sku is explicit, so `Order.next_sku` is never read here."""
    c = Order.build(sku, handling, category, *dims, 10, freq, qty, lead_time_mean=lead,
                    supply_cv=0.0)
    return c.declare_stock(eq, rp, stock_plan=[list(s) for s in plan])


@pytest.fixture()
def section():
    # Two SKUs of one class (same law, plan, rp) and one of another: 2 classes, 3 SKUs.
    return [_order(1, freq=0.5), _order(2, freq=0.25),
            _order(3, freq=0.25, qty=1.0, eq=2, rp=1, plan=((True, 2, 1),))]


# ── the packer's plan branch ────────────────────────────────────────────────────────────

def test_packing_is_the_packers_plan_branch_on_real_orders():
    """For every lot at or below the plan total, `packing` names the same (kind, quantity)
    sequence `viable_storage_units` builds -- store plan with mixed slots, and a
    fulfillment plan (whose flag the packer ignores: every unit is a FulfillmentBin)."""
    store = _order(1, eq=9, rp=8, plan=((False, 4, 2), (True, 1, 1)))
    for r in range(1, 10):
        got = [(isinstance(u, Singleton), u.quantity) for u in viable_storage_units(store, r)]
        assert got == list(fr.packing(fr.plan_slots(store), r)), r
    ff = _order(2, handling='fulfillment', category='fulfillment', dims=(4, 8, 7), eq=7, rp=6,
                plan=((True, 3, 2), (True, 1, 1)))
    for r in range(1, 8):
        got = [u.quantity for u in viable_storage_units(ff, r)]
        assert got == [n0 for _s, n0 in fr.packing(fr.plan_slots(ff), r)], r
    assert fr.packing(fr.plan_slots(store), 0) == ()


def test_packing_refuses_a_lot_past_the_plan_and_an_order_without_a_plan():
    with pytest.raises(ValueError, match='exceeds the plan total'):
        fr.packing(((True, 2, 1),), 3)
    c = Order.build(9, 'conveyable', 'food', 10, 10, 10, 10, 0.5, 2.0)
    c.declare_stock(3, 2)                      # a level, but no plan
    with pytest.raises(ValueError, match='no stock_plan'):
        fr.plan_slots(c)
    # A plan that packs less than the declared level: the ledger would order past it.
    short = _order(10, eq=5, rp=4, plan=((True, 3, 1),))
    with pytest.raises(ValueError, match='must agree'):
        fr.class_key(short)


# ── the drain ───────────────────────────────────────────────────────────────────────────

def test_drain_all_takes_the_smallest_unit_first_for_every_depth_in_one_pass():
    st = ((1, True, 1), (2, False, 4), (4, False, 4))
    d = fr.drain_all(st, 8)
    assert d[1] == {((2, False, 4), (4, False, 4)): 1.0}                 # the 1 is gone
    assert d[2] == {((1, False, 4), (4, False, 4)): 1.0}                 # then 1 off the 2
    assert d[3] == {((4, False, 4),): 1.0}
    assert d[4] == {((3, False, 4),): 1.0}
    assert d[6] == {((1, False, 4),): 1.0}
    assert d[7] == {(): 1.0}                                             # the shelf is bare
    assert d[8] == {}                                                    # nothing can serve it


def test_a_tie_is_split_uniformly_over_the_tied_units_and_branches_merge():
    """Two units of quantity 1 and different kind: draining one is a coin flip; draining
    both lands on ONE state with the whole weight (the orderings merged).  With two tied
    units of one kind against one of another, that kind goes first two times in three."""
    st = ((1, True, 1), (1, False, 4), (3, False, 4))
    d = fr.drain_all(st, 2)
    assert d[1] == {((1, False, 4), (3, False, 4)): 0.5, ((1, True, 1), (3, False, 4)): 0.5}
    assert d[2] == {((3, False, 4),): pytest.approx(1.0)}
    st3 = ((1, True, 1), (1, True, 1), (1, False, 4), (3, False, 4))
    d3 = fr.drain_all(st3, 1)[1]
    assert d3[((1, True, 1), (1, False, 4), (3, False, 4))] == pytest.approx(2.0 / 3.0)
    assert d3[((1, True, 1), (1, True, 1), (3, False, 4))] == pytest.approx(1.0 / 3.0)


# ── the chain by hand ───────────────────────────────────────────────────────────────────

def test_the_chain_on_a_two_outcome_law_matches_the_hand_computation():
    """One singleton of 3 (Q = 3, base stock).  A line of 1 (w.p. a) drains 1 off the 3
    and the top-up of 1 opens a second bin; from there another 1 drains the fresh unit and
    re-opens one (2 bins stays 2); a line of 3 empties the shelf and refills the fielded
    state.  So pi(1 bin) = 1 - a, pi(2 bins) = a, E[extra] = a; after any j >= 1 lines
    the expected extra is exactly a."""
    a = 0.3
    ch = fr.SkuChain(TwoOutcome(a), ((True, 3, 1),))
    assert ch.Q == 3
    assert ch.rp == 2
    assert ch.rho == pytest.approx(1.0 - a)
    assert ch.kinds == [(True, 1), (True, 2), (True, 3)]
    assert ch.states == 2
    assert ch.solver == 'power'
    assert ch.stationary_extra == pytest.approx(a, abs=1e-9)
    assert ch.bins_law == pytest.approx({1: 1.0 - a, 2: a}, abs=1e-9)
    assert ch.stationary_units == pytest.approx([a, 0.0, 1.0], abs=1e-9)
    assert ch.trajectory_units[0] == pytest.approx([0.0, 0.0, 1.0])
    for j in (1, 2, 5, fr.J_MAX):
        assert ch.units_at_lines(j) == pytest.approx([a, 0.0, 1.0], abs=1e-9), j
    assert ch.units_at_lines(fr.J_MAX + 7) is ch.stationary_units
    # The day mixture: a * P(at least one line by day t) = a * (1 - e^{-lambda t}).
    lam, t = 0.1, 5.0
    at = ch.units_at_days(lam, [0.0, t])
    assert at[0] == pytest.approx([0.0, 0.0, 1.0])
    assert at[1][0] == pytest.approx(a * (1.0 - math.exp(-lam * t)), rel=1e-9)


def test_base_stock_is_the_default_and_a_reorder_point_below_it_drains_the_shelf():
    """An above-floor SKU (rp < Q - 1) does not reorder after every line, so its shelf
    spends time below the fielding: fewer expected bins than base stock on the same plan."""
    line = LineDistribution.poisson(2.0)
    base = fr.SkuChain(line, ((False, 4, 3),))
    above = fr.SkuChain(line, ((False, 4, 3),), rp=4)
    assert base.rp == 11
    assert above.rp == 4
    assert base.stationary_bins > len(base.f) > above.stationary_bins
    for ch in (base, above):
        assert sum(ch.stationary.values()) == pytest.approx(1.0, abs=1e-9)
        assert ch.stationary_units.sum() == pytest.approx(ch.stationary_bins, abs=1e-9)


def test_a_plan_of_single_item_units_never_fragments():
    """Every unit holds one item, so bins equal on-hand: no extra, whatever the law."""
    ch = fr.SkuChain(LineDistribution.poisson(6.0), ((False, 1, 8),))
    assert ch.stationary_extra == pytest.approx(0.0, abs=1e-12)
    assert ch.states == 1
    assert ch.solver == 'trivial'


def test_the_solver_falls_back_to_the_iterate_when_the_direct_solve_declines(monkeypatch):
    """A slow mixer that the direct solve refuses ends on the capped iterate, and says so."""
    monkeypatch.setattr(fr, 'POWER_SWITCH', 5)
    monkeypatch.setattr(fr, 'POWER_MAX', 20)
    monkeypatch.setattr(fr.SkuChain, '_direct', staticmethod(lambda PT, n, pin: None))
    c = _order(1, eq=5, rp=4, plan=((False, 4, 1), (True, 1, 1)))
    ch = fr.SkuChain(_RareTwo(), fr.plan_slots(c))
    assert ch.solver == 'power_capped'
    assert ch.iterations == 20
    assert sum(ch.stationary.values()) == pytest.approx(1.0, abs=1e-12)


def test_line_pmf_truncates_and_renormalises_the_tail_and_refuses_an_empty_law():
    line = LineDistribution.poisson(3.0)
    pmf = fr.line_pmf(line)
    assert pmf[0] == 0.0
    assert pmf.sum() == pytest.approx(1.0, abs=1e-12)
    assert len(pmf) == line.quantile(1.0 - fr.LINE_TAIL) + 1
    short = fr.line_pmf(line, q_max=2)
    assert short == pytest.approx([0.0, line.cdf(1), line.cdf(2) - line.cdf(1)]) or \
        short.sum() == pytest.approx(1.0)
    assert short.sum() == pytest.approx(1.0, abs=1e-12)

    class Empty:
        def cdf(self, k):
            return 0.0

        def quantile(self, p):
            return 3

    with pytest.raises(ValueError, match='no mass'):
        fr.line_pmf(Empty())

    class AtZero:
        def cdf(self, k):
            return 0.5 if k < 1 else 1.0

        def quantile(self, p):
            return 3

    with pytest.raises(ValueError, match='mass at zero'):
        fr.line_pmf(AtZero())


def test_poisson_weights_are_a_distribution_with_the_tail_in_the_last_column():
    W = fr.poisson_weights([0.0, 0.5, 40.0], 8)
    assert W.shape == (3, 9)
    assert W.sum(axis=1) == pytest.approx([1.0, 1.0, 1.0])
    assert W[0] == pytest.approx([1.0] + [0.0] * 8)                  # mean 0: no line
    assert W[1, 1] == pytest.approx(0.5 * math.exp(-0.5))
    assert W[2, 8] > 0.999                                         # mean 40: all in the tail
    wide = fr.poisson_weights([2.0], fr.J_MAX + 10)                # past the cached table
    assert wide.shape == (1, fr.J_MAX + 11)
    assert wide.sum() == pytest.approx(1.0)


# ── the section ─────────────────────────────────────────────────────────────────────────

def test_section_sums_share_chains_by_class_and_reconcile_with_the_fielding(section):
    out = fr.section_fragmentation(section)
    assert out['n_skus'] == 3
    assert out['n_classes'] == 2
    # The fielded count per bucket IS the planner's requirement for the same orders.
    req = Inventory_Manager.bucket_requirements(section)
    assert {tuple(b): r['fielded'] for b, r in out['buckets'].items()} == \
        {tuple(b): n for b, n in req.items()}
    # The section sum is the chains' extra weighted by their members.
    members = {}
    for c in section:
        members[fr.class_key(c)] = members.get(fr.class_key(c), 0) + 1
    expect = sum(ch.stationary_extra * members[k] for k, ch in out['chains'].items())
    assert out['expected_extra'] == pytest.approx(expect, abs=1e-9)
    assert sum(r['expected_extra'] for r in out['buckets'].values()) == \
        pytest.approx(out['expected_extra'], abs=1e-9)
    assert out['positive_lead_skus'] == 0
    assert out['expected_extra_at'] == []


def test_class_key_separates_regime_law_plan_and_reorder_point():
    base = _order(1)
    assert fr.class_key(_order(2)) == fr.class_key(base)                       # same class
    assert fr.class_key(_order(3, rp=1)) != fr.class_key(base)                 # rp alone
    assert fr.class_key(_order(4, qty=3.0)) != fr.class_key(base)              # law alone
    assert fr.class_key(_order(5, plan=((True, 2, 1), (True, 1, 1)))) != fr.class_key(base)
    ff = _order(6, handling='fulfillment', category='fulfillment', dims=(4, 8, 7))
    assert fr.class_key(ff) != fr.class_key(base)                              # regime alone
    assert fr.class_key(ff)[0] is True
    # A fulfillment section runs through the same seam and lands in its own tier family.
    out = fr.section_fragmentation([ff])
    assert all(b[3] == 'fulfillment' for b in out['buckets'])
    lead = _order(7, lead=2.0)
    sub_day = _order(8, lead=0.3)                    # the ledger rounds this to lead 0
    assert fr.section_fragmentation([lead, sub_day, base])['positive_lead_skus'] == 1


def test_a_partial_lot_migrates_the_plans_remainder_down_a_tier():
    """Plan: one pallet of 4 and one singleton of 1 (Q = 5), an item 16 x 16 x 10: a
    pallet of 1 lies 10 high (`small`) while a pallet of 4 has to stack to 16 or more
    (`medium`), and the singleton of 1 still fits its 16 x 16 slot.  A line of 1 drains
    the singleton and the top-up of 1 packs as a pallet of 1 -- a different bucket, a
    smaller tier.  Under a law that never reaches 5 (`_RareTwo`) the singleton is gone
    for good; the rare line of 2 then thins the pallet of 4 (its bin keeps `medium`) and
    adds a pallet of 2 (`small`), and the chain settles on a remnant of 3 with two pallets of 1:
    5 states, 3 bins, one extra.  That law mixes only through its line of 2, once in a
    thousand lines: the pinned direct solve's case."""
    c = _order(1, dims=(16, 16, 10), eq=5, rp=4, plan=((False, 4, 1), (True, 1, 1)))
    c.demand.line = _RareTwo()
    single, small, two, four = (fr.bucket_of(c, k) for k in
                                ((True, 1), (False, 1), (False, 2), (False, 4)))
    assert single[2] == 'singleton'
    assert small[2] == 'small'
    assert four[2] == 'medium'                       # the full pallet's tier is not the 1's
    assert two[2] == 'small'                         # two still lie flat within 48
    out = fr.section_fragmentation([c])
    chain = out['chains'][fr.class_key(c)]
    assert chain.states == 5
    assert chain.solver == 'direct', (chain.solver, chain.iterations)
    assert sum(chain.stationary.values()) == pytest.approx(1.0, abs=1e-12)
    assert out['buckets'][single]['expected_extra'] == pytest.approx(-1.0, abs=1e-9)
    assert out['buckets'][small]['expected_extra'] == pytest.approx(2.0, abs=5e-3)
    # The pallet of 4 thins to a remnant but keeps its bin; the pallet of 2 is transient.
    assert out['buckets'][four]['expected_extra'] == pytest.approx(0.0, abs=5e-3)
    assert out['expected_extra'] == pytest.approx(1.0, abs=5e-3)


def test_the_transient_reads_the_line_share_or_the_rate_a_caller_hands_in(section):
    """At 100 lines a day over three SKUs a day is ~5 lines per SKU, well inside the
    64-line trajectory, so day 0.05 must sit strictly between the fielding and the
    stationary law (a day 10 would already read the stationary tail exactly)."""
    n, t = 100.0, 0.05
    share = fr.section_fragmentation(section, lines_per_day=n, days=[0, t])
    assert share['expected_extra_at'][0] == pytest.approx(0.0, abs=1e-12)
    assert 0.1 < share['expected_extra_at'][1] < share['expected_extra'] - 0.1
    # The same numbers through the seam, from the share itself.
    W = sum(c.demand.relative_frequency for c in section)
    rates = {c.sku: n * c.demand.relative_frequency / W for c in section}
    seam = fr.section_fragmentation(section, days=[0, t], lines_per_day_by_sku=rates)
    assert seam['expected_extra_at'] == pytest.approx(share['expected_extra_at'], rel=1e-9)
    # A SKU the map does not name sees no line.
    quiet = fr.section_fragmentation(section, days=[t], lines_per_day_by_sku={})
    assert quiet['expected_extra_at'] == [pytest.approx(0.0, abs=1e-12)]
    with pytest.raises(ValueError, match='lines_per_day'):
        fr.section_fragmentation(section, days=[1])


def test_the_module_is_pure():
    """Orders in, floats out: no CONFIG, no file, no RNG -- read off what it imported."""
    imported = {m for m in sys.modules if m.startswith('Optimization.config')}
    names = {v.__name__ for v in vars(fr).values() if inspect.ismodule(v)}
    assert not names & imported
    assert not any(n in ('random', 'os', 'io', 'pathlib') for n in names)
    assert 'CONFIG' not in vars(fr)


# ── the stamp on the record ─────────────────────────────────────────────────────────────

def test_stamp_fragmentation_writes_every_bucket_row_and_the_section_block(section):
    Inventory_Manager.field_requirement(section)
    req = Inventory_Manager.bucket_requirements(section)
    fielded = {'buckets': [{'handling': b[0], 'category': b[1], 'size': b[2], 'unit': b[3],
                            'requirement': n, 'capacity': 2 * n, 'free': n}
                           for b, n in sorted(req.items(), key=lambda kv: repr(kv[0]))]}
    # A bucket the plan built but no SKU reaches: stamped 0.0, never left unset.
    fielded['buckets'].append({'handling': 'conveyable', 'category': 'seasonal',
                               'size': 'extra_large', 'unit': 'pallet',
                               'requirement': 0, 'capacity': 5, 'free': 5})
    frag = ec.stamp_fragmentation(fielded, section, logging.getLogger('t'), name='store')
    assert all('expected_extra' in r for r in fielded['buckets'])
    assert fielded['buckets'][-1]['expected_extra'] == 0.0
    assert sum(r['expected_extra'] for r in fielded['buckets']) == \
        pytest.approx(frag['expected_extra'], abs=1e-9)
    block = fielded['fragmentation']
    assert block['provenance'] == 'derived'
    assert block['method'] == 'stationary_chain'
    assert block['expected_extra'] == pytest.approx(frag['expected_extra'])
    assert block['n_classes'] == 2
    assert block['capped_classes'] == 0
    assert block['positive_lead_skus'] == 0
    assert block['stationary_bins_per_sku'] > block['fielded_bins_per_sku']
    json.dumps(fielded)                                            # the record is JSON


def test_stamp_fragmentation_raises_on_a_bucket_the_plan_never_built(section):
    """The realistic drift: the plan's table lacks ONE bucket a lot can pack into."""
    Inventory_Manager.field_requirement(section)
    req = Inventory_Manager.bucket_requirements(section)
    rows = [{'handling': b[0], 'category': b[1], 'size': b[2], 'unit': b[3],
             'requirement': n, 'capacity': 2 * n, 'free': n} for b, n in req.items()]
    assert len(rows) >= 1
    with pytest.raises(ValueError, match='never built'):
        ec.stamp_fragmentation({'buckets': rows[1:]}, section, logging.getLogger('t'),
                               name='store')


def test_fixed_point_stamps_the_fragmentation_on_the_record(monkeypatch):
    """The loop stamps `expected_extra` on every bucket row and the section block: the fake
    planner and stage A of `test_coverage_rescale` stand in for sizing, the real
    `field_requirement` and the real chain do the rest.  The levels are kept in the tens
    (`coverage_days=0.1`) so the chains are small; the stamp does not depend on the size.
    The call is unconditional inside the per-spec loop -- the era decides whether the clock
    cuts, never whether the record derives -- which the source pin below states."""
    section = [tcr._order(1, freq=0.5, qty=4.0), tcr._order(2, freq=0.25, qty=2.0),
               tcr._order(3, freq=0.25, qty=8.0)]
    monkeypatch.setattr(ec, 'seed_lines', tcr._fake_seed(60.0))
    monkeypatch.setattr(ec, 'stage_a', tcr._fake_stage_a(lambda sq: 60.0))
    specs = [ec.ChannelSpec('store', None, None, 'store')]
    _plan, _meta, _sa, record = ec.fixed_point(
        section, lambda: (tcr._Plan(section), tcr._Meta()), specs, coverage_days=0.1,
        safety_days=0.05, floor_lines=None, inputs={}, day_seconds=28800.0,
        log=logging.getLogger('t'), max_rounds=2)
    assert max(c.equilibrium_qty for c in section) < 40
    fielded = record['final']['store']['fielded']
    assert fielded['fragmentation']['provenance'] == 'derived'
    assert all('expected_extra' in r for r in fielded['buckets'])
    assert sum(r['expected_extra'] for r in fielded['buckets']) == \
        pytest.approx(fielded['fragmentation']['expected_extra'], abs=1e-9)
    json.dumps(record)
    src = inspect.getsource(ec.fixed_point)
    # The post-loop stamping pass: the last `for s in specs:` before `record['final']`.
    loop = src[src.rindex('for s in specs:', 0, src.index('record[\'final\']')):]
    assert 'stamp_fragmentation(fielded, section, log' in loop
