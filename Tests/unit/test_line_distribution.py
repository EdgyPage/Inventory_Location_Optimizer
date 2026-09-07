"""test_line_distribution.py — the line law is STAMPED on the SKU, and every reader reads it.

(.scratch/department-calibration, "Stamp the line distribution on the SKU", graduated from
"Choose the coverage floor" decision 10.)  One object, `Warehouse/catalog/Demand.py:
LineDistribution`, holds the law one pick LINE's quantity is drawn from; the batch sampler,
`expected_travel`, `staffing` and `coverage` are its readers; the inventory file carries it as
two columns through the schema pipeline, and a pre-stamp file reconstructs it at load.  Four
groups of tests:

  * the object against hand computations -- the Poisson identities (`mean = λ + e^-λ`,
    `expected_min` by hand, the survival vector, the quantile), the row round trip, the
    refusals;
  * the construction paths -- `Demand.from_rates` and `Order.build` reconstruct an absent law
    with provenance `assumed`, a stamped law rides `Order.reorder`, a law that agrees with
    neither the authored nor the clamped rate RAISES, and the census names what a catalogue
    carries;
  * BYTE-IDENTICAL: the draws, the batches and the batch-cache fingerprints of a fixed
    catalogue equal the values captured on the commit BEFORE the stamp (the goldens below
    were computed with the `max(1, poisson_sample(λ))` code path and pasted in);
  * the inventory file -- the two columns survive a save/load, a pre-stamp file (the old DDL,
    hashing to the vetted pre-stamp id) loads through the per-vintage override with the law
    reconstructed and the census saying so, and no hand-coded line law is left outside the
    object.

Run:  python -m pytest Tests/unit/test_line_distribution.py -q
"""
from __future__ import annotations

import inspect
import math
import os
import random
import re
import sqlite3

import numpy as np
import pytest

from Optimization.simconfig import coverage as cov
from Optimization.simconfig import expected_travel as et
from Optimization.simconfig import staffing as st
from Optimization.simdriver import workunits
from Optimization.simdriver.batch_precompute import batch_fingerprint
from Schema import shape as _shape
from Warehouse.catalog.Demand import (Demand, LINE_PROVENANCE, LineDistribution,
                                      line_law_census, poisson_sample)
from Warehouse.catalog.Inventory_Builder import Inventory
from Warehouse.catalog.Order import Order
from Warehouse.generation import generate_inventory as gi
from Warehouse.generation.generate_inventory import (DEFAULT_DIM_SPEC, DEFAULT_WEIGHT_SPEC,
                                                     build_inventory_from_plan,
                                                     build_inventory_with_profile)
from Warehouse.generation.generate_mixed_profile import BELL_CREATION_PLAN
from Warehouse.picking.Workload_Builder import Batch, BatchConfig

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mean(lam: float) -> float:
    """E[max(1, Poisson(λ))] by hand: E[X] + P(X = 0)."""
    return lam + math.exp(-lam)


def _p_x_gt(j: int, lam: float) -> float:
    """P(X > j) for X ~ Poisson(λ), by the pmf sum."""
    return 1.0 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(j + 1))


# ═════════════════════════════════════════════════════════════════════════════════════════
# The object
# ═════════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('lam', [0.0, 0.5, 3.0, 12.0])
def test_the_poisson_identities_by_hand(lam):
    L = LineDistribution.poisson(lam)
    assert math.isclose(L.mean(), _mean(lam), rel_tol=1e-12)
    # cdf: nothing below one, then the Poisson cdf (the floor moves P(X = 0) onto q = 1).
    assert L.cdf(0) == 0.0 and L.cdf(-3) == 0.0
    assert math.isclose(L.cdf(1), math.exp(-lam) * (1 + lam), rel_tol=1e-12)
    assert math.isclose(L.cdf(4), 1.0 - _p_x_gt(4, lam), rel_tol=1e-9, abs_tol=1e-12)
    # survival: P(q > 0) = 1, then the Poisson upper tail.
    s = L.survival(5)
    assert s.shape == (5,) and s[0] == 1.0
    for j in range(1, 5):
        assert math.isclose(float(s[j]), _p_x_gt(j, lam), rel_tol=1e-9, abs_tol=1e-12)
    assert L.survival(0).shape == (0,)
    # expected_min: E[min(q, S)] = Σ_{j<S} P(q > j) -- one unit is always delivered.
    assert L.expected_min(0) == 0.0
    assert L.expected_min(1) == 1.0
    assert math.isclose(L.expected_min(2), 1.0 + _p_x_gt(1, lam), rel_tol=1e-12)
    for S in (3, 7, 30):
        assert math.isclose(L.expected_min(S), float(L.survival(S).sum()), rel_tol=1e-9)
    # ...and it converges on the mean as S grows.
    assert math.isclose(L.expected_min(400), L.mean(), rel_tol=1e-9)


def test_quantile_is_the_smallest_k_with_cdf_at_least_p():
    L = LineDistribution.poisson(3.0)
    assert L.quantile(0.0) == 1
    for p in (0.05, 0.5, 0.9, 0.999):
        k = L.quantile(p)
        assert L.cdf(k) >= p and (k == 1 or L.cdf(k - 1) < p)
    with pytest.raises(ValueError, match='quantile'):
        L.quantile(1.0)
    # A p above the running sum's float plateau terminates once the tail has underflowed
    # (the review found the naive loop never returned here).
    for lam, p in ((0.1, 0.9999999999999999), (20.0, 0.9999999999999999), (700.0, 0.999)):
        k = LineDistribution.poisson(lam).quantile(p)
        assert isinstance(k, int) and k >= 1


def test_the_row_round_trip_and_the_refusals():
    L = LineDistribution.poisson(4.0)
    fam, params = L.to_row()
    assert fam == 'poisson_max1' and params == '{"lam": 4.0}'
    back = LineDistribution.from_row(fam, params, 'declared')
    assert (back.family, back.params, back.provenance) == ('poisson_max1', {'lam': 4.0}, 'declared')
    assert LineDistribution.from_row(fam, params).provenance == 'declared'
    with pytest.raises(ValueError, match='unknown line-distribution family'):
        LineDistribution('gamma', {'k': 1.0})
    with pytest.raises(ValueError, match='provenance'):
        LineDistribution.poisson(1.0, 'measured')
    with pytest.raises(ValueError, match='lam'):
        LineDistribution('poisson_max1', {})
    with pytest.raises(ValueError, match='lam'):
        LineDistribution('poisson_max1', {'lam': -1.0})
    with pytest.raises(ValueError, match='lam'):
        LineDistribution('poisson_max1', {'lam': 800.0})   # e^-lam underflows: refused
    assert LINE_PROVENANCE == ('declared', 'assumed')


def test_sample_is_knuths_draw_floored_at_one():
    L = LineDistribution.poisson(3.0)
    mine = [L.sample(random.Random(s)) for s in range(40)]
    ref = [max(1, poisson_sample(3.0, random.Random(s))) for s in range(40)]
    assert mine == ref and min(mine) >= 1


# ═════════════════════════════════════════════════════════════════════════════════════════
# The construction paths
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_an_absent_law_is_reconstructed_as_poisson_and_says_so():
    d = Demand.from_rates(0.5, 6.0)
    assert (d.line.family, d.line.params, d.line.provenance) == ('poisson_max1', {'lam': 6.0}, 'assumed')
    o = Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 6.7,
                    equilibrium_qty=5, reorder_point=2)
    # Order.build clamps the rate to an integer; the reconstructed law is at the CLAMPED rate --
    # what every pre-stamp load sampled.
    assert o.demand.quantity_rate == 7
    assert o.demand.line.params == {'lam': 7.0} and o.demand.line.provenance == 'assumed'
    assert Demand().line.provenance == 'assumed'          # the random path says so too


def test_a_stamped_law_is_kept_and_rides_the_reorder():
    stamped = LineDistribution.poisson(6.0)
    o = Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 6.0,
                    equilibrium_qty=5, reorder_point=2, line=stamped)
    assert o.demand.line is stamped and o.demand.line.provenance == 'declared'
    r = o.reorder()
    assert r.demand.line is stamped, 'a restock carries the SKU law, it does not rebuild one'
    assert r.demand.quantity_rate == o.demand.quantity_rate


def test_a_law_authored_at_the_unclamped_rate_follows_the_clamp_and_a_stranger_raises():
    # Authored at 6.7 (the legacy generator draws floats); the clamp makes the rate 7, and
    # the law follows it -- exactly what a saved-and-loaded copy always sampled.
    o = Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 6.7,
                    equilibrium_qty=5, reorder_point=2, line=LineDistribution.poisson(6.7))
    assert o.demand.quantity_rate == 7 and o.demand.line.params == {'lam': 7.0}
    assert o.demand.line.provenance == 'declared'
    # A law that agrees with neither the authored nor the clamped rate is a corrupt stamp.
    with pytest.raises(ValueError, match='stamped line law'):
        Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 6.0,
                    equilibrium_qty=5, reorder_point=2, line=LineDistribution.poisson(9.0))


def test_the_census_names_the_families_and_the_reconstructions():
    a = Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 3.0,
                    equilibrium_qty=5, reorder_point=2, line=LineDistribution.poisson(3.0))
    b = Order.build(2, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 4.0,
                    equilibrium_qty=5, reorder_point=2)
    assert line_law_census([a, b]) == {
        'n_skus': 2, 'families': {'poisson_max1': 2},
        'provenance': {'assumed': 1, 'declared': 1}, 'reconstructed_skus': 1}
    assert line_law_census([]) == {'n_skus': 0, 'families': {}, 'provenance': {},
                                   'reconstructed_skus': 0}


# ═════════════════════════════════════════════════════════════════════════════════════════
# BYTE-IDENTICAL: goldens captured on the commit before the stamp
# ═════════════════════════════════════════════════════════════════════════════════════════

def _golden_inventory(n: int = 20) -> Inventory:
    """The fixed 20-SKU catalogue the goldens were captured on (rates from Random(3))."""
    Order.next_sku = 1
    r = random.Random(3)
    orders = [Order.build(i + 1, 'conveyable', 'seasonal', 10, 10, 10, 10,
                          r.uniform(0.05, 1.0), r.uniform(0.5, 20.0),
                          equilibrium_qty=60, reorder_point=20) for i in range(n)]
    return Inventory(orders)


#: `Batch(BatchConfig(20, 0.5, 0.1), inv, affinity=None, rng=Random(seed)).items`, pre-stamp.
_GOLDEN_BATCHES = {
    7: {14: 10, 3: 2, 11: 1, 8: 2, 2: 9, 12: 15, 4: 23, 10: 10, 5: 9},
    8: {5: 3, 15: 18, 3: 2, 7: 15, 20: 3, 8: 4, 14: 7, 11: 1, 12: 10, 10: 14, 6: 7},
    9: {5: 3, 18: 17, 1: 8, 11: 2, 19: 1, 3: 2, 12: 4, 14: 10},
}
#: `batch_fingerprint(inv, cfg, seed_batches=11, n_batches=5, affinity=None)`, pre-stamp.
_GOLDEN_FINGERPRINT_V1 = '3869322c7a7177046bdfab934ea2da7dda6f5a09'
_GOLDEN_FINGERPRINT_V2 = 'b648a9c0fd40f2abb6770d15c727699e99cd42ff'
#: `[max(1, Demand.from_rates(0.5, lam).sample(rng=Random(seed + i))) for i in range(12)]`.
_GOLDEN_DRAWS = {
    (0.5, 1): [1, 2, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1],
    (3.0, 2): [3, 2, 1, 6, 4, 1, 2, 2, 3, 5, 3, 4],
    (12.0, 3): [8, 9, 16, 8, 8, 11, 7, 13, 12, 11, 11, 13],
}


def test_the_batches_are_byte_identical_to_the_pre_stamp_goldens():
    inv = _golden_inventory()
    # v1 is the golden's sampler; v2 (the production default since 21f3b3c) shares the
    # quantity draw and, affinity-free, the selection -- so it pins the same items.
    for sampler in ('v1', 'v2'):
        cfg = BatchConfig(inventory_size=20, mean_fraction=0.5, std_fraction=0.1, sampler=sampler)
        for seed, items in _GOLDEN_BATCHES.items():
            b = Batch(cfg, inv, affinity=None, rng=random.Random(seed))
            assert b.items == items, f'{sampler} seed {seed}: the batch moved'
            assert list(b.items) == list(items), 'even the selection order is pinned'


def test_the_draws_are_byte_identical_to_the_pre_stamp_goldens():
    for (lam, seed), ref in _GOLDEN_DRAWS.items():
        d = Demand.from_rates(0.5, lam)
        assert [d.sample(rng=random.Random(seed + i)) for i in range(12)] == ref


def test_the_batch_cache_fingerprints_are_byte_identical():
    inv = _golden_inventory()
    assert batch_fingerprint(inv, BatchConfig(20, 0.5, 0.1), 11, 5, None) == _GOLDEN_FINGERPRINT_V1
    assert batch_fingerprint(inv, BatchConfig(20, 0.5, 0.1, sampler='v2'), 11, 5, None) \
        == _GOLDEN_FINGERPRINT_V2
    # A DECLARED Poisson stamp hashes like the reconstructed one: the same cache serves both.
    for c in inv.orders:
        c.demand.line = LineDistribution.poisson(c.demand.quantity_rate)      # declared
    assert batch_fingerprint(inv, BatchConfig(20, 0.5, 0.1), 11, 5, None) == _GOLDEN_FINGERPRINT_V1


def test_a_second_family_can_never_be_served_from_a_poisson_cache(monkeypatch):
    inv = _golden_inventory()
    monkeypatch.setattr(LineDistribution, 'FAMILIES', ('poisson_max1', 'stub'))
    inv.orders[3].demand.line = LineDistribution('stub', {'a': 2.0})
    fp = batch_fingerprint(inv, BatchConfig(20, 0.5, 0.1), 11, 5, None)
    assert fp != _GOLDEN_FINGERPRINT_V1
    inv.orders[3].demand.line = LineDistribution('stub', {'a': 3.0})
    assert batch_fingerprint(inv, BatchConfig(20, 0.5, 0.1), 11, 5, None) != fp, \
        'the parameters of a non-Poisson law are part of the fingerprint'


# ═════════════════════════════════════════════════════════════════════════════════════════
# The inventory file
# ═════════════════════════════════════════════════════════════════════════════════════════

def test_the_two_columns_survive_a_save_and_load(tmp_path):
    inv = _golden_inventory(8)
    path = str(tmp_path / 'inv.db')
    gi.save_inventory_to_db(inv, path, {'test': True})
    con = sqlite3.connect(path)
    rows = con.execute('SELECT sku, demand_qty_rate, line_family, line_params FROM cartons '
                       'ORDER BY sku').fetchall()
    con.close()
    assert [r[2] for r in rows] == ['poisson_max1'] * 8
    for sku, rate, _, params in rows:
        assert params == f'{{"lam": {float(rate)}}}'
    back = gi.load_inventory_from_db(path)
    assert len(back.orders) == 8
    for a, b in zip(inv.orders, back.orders):
        assert b.demand.line.provenance == 'declared', 'read from the stamp, not rebuilt'
        assert b.demand.line.params == a.demand.line.params
        assert b.demand.quantity_rate == a.demand.quantity_rate
    assert line_law_census(back.orders)['reconstructed_skus'] == 0
    assert len(gi.load_inventory_from_db(path, limit=3).orders) == 3


def test_both_generator_paths_stamp_a_declared_law_at_the_rate_the_file_carries(tmp_path):
    # The plan build clamps the drawn rate and stamps the law at the clamped value.
    Order.next_sku = 1
    inv = build_inventory_from_plan(num_skus=12, plan=BELL_CREATION_PLAN, seed=3)
    for c in inv.orders:
        assert c.demand.line.provenance == 'declared'
        assert c.demand.line.params == {'lam': float(c.demand.quantity_rate)}
    # The legacy build draws a FLOAT rate; it clamps the scalar and stamps the law at the
    # clamped value too, so the file, the memory and the reload all read one law.
    legacy = build_inventory_with_profile(
        num_skus=12, seed=3, handling_splits=[0.5, 0.5], category_splits=[1 / 6] * 6,
        singleton_fraction=0.3, dim_spec=DEFAULT_DIM_SPEC, weight_spec=DEFAULT_WEIGHT_SPEC)
    for c in legacy.orders:
        assert isinstance(c.demand.quantity_rate, int) and 1 <= c.demand.quantity_rate <= 20
        assert c.demand.line.provenance == 'declared'
        assert c.demand.line.params == {'lam': float(c.demand.quantity_rate)}
    path = str(tmp_path / 'legacy.db')
    gi.save_inventory_to_db(legacy, path, {'test': True})
    con = sqlite3.connect(path)
    on_disk = dict(con.execute('SELECT sku, line_params FROM cartons').fetchall())
    con.close()
    back = gi.load_inventory_from_db(path)
    for a, b in zip(legacy.orders, back.orders):
        assert on_disk[a.sku] == f'{{"lam": {float(a.demand.quantity_rate)}}}'
        assert b.demand.line.params == a.demand.line.params and b.demand.line.provenance == 'declared'
        assert b.demand.quantity_rate == a.demand.quantity_rate


def _pre_stamp_ddl() -> str:
    """The pre-stamp `_SCHEMA`: today's DDL with the two stamped columns (and their comment)
    removed -- and the test below proves that is EXACTLY the vetted pre-stamp shape."""
    ddl, n = re.subn(r'\n\s*-- The LINE LAW.*?line_params\s+TEXT,', '', gi._SCHEMA, flags=re.S)
    assert n == 1, 'the DDL comment block anchoring the pre-stamp reconstruction moved'
    assert 'line_family' not in ddl and 'line_params' not in ddl
    return ddl


def test_a_pre_stamp_file_loads_through_the_override_with_the_law_reconstructed(tmp_path):
    inv = _golden_inventory(6)
    path = str(tmp_path / 'old_inventory.db')
    con = sqlite3.connect(path)
    con.executescript(_pre_stamp_ddl())
    # The reconstruction hashes to the id the family vets: the committed pre-stamp document
    # IS today's DDL minus the two columns, and nothing else moved.
    assert _shape.observed_id(con) == gi.PRE_LINE_LAW_INVENTORY_SCHEMA_ID
    con.executemany(
        'INSERT INTO cartons (sku, handling, category, length, width, height, weight, '
        ' relative_frequency, demand_qty_rate, expected_batch_demand, equilibrium_qty, '
        ' reorder_point, lead_time_mean, supply_cv, stock_plan, subtype) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [(c.sku, 'conveyable', 'seasonal', 10, 10, 10, 10, c.demand.relative_frequency,
          c.demand.quantity_rate + 0.3,          # a legacy float rate: the load clamps it
          0.0, 60, 20, 0.0, 0.0, None, None) for c in inv.orders])
    con.commit()
    con.close()
    back = gi.load_inventory_from_db(path)
    assert len(back.orders) == 6
    for a, b in zip(inv.orders, back.orders):
        assert b.demand.quantity_rate == a.demand.quantity_rate       # clamped, as always
        assert b.demand.line.provenance == 'assumed'
        assert b.demand.line.params == {'lam': float(a.demand.quantity_rate)}
    census = line_law_census(back.orders)
    assert census['reconstructed_skus'] == 6 and census['provenance'] == {'assumed': 6}
    # ...and the reconstructed catalogue draws exactly what the stamped one does.
    cfg = BatchConfig(inventory_size=6, mean_fraction=0.5, std_fraction=0.1)
    for seed in (1, 2):
        assert Batch(cfg, back, affinity=None, rng=random.Random(seed)).items \
            == Batch(cfg, inv, affinity=None, rng=random.Random(seed)).items


def test_the_readers_hold_no_line_law_of_their_own():
    """The grep the ticket asks for: `poisson_sample` is drawn only inside `Demand.py` (and
    `Order._sample_weight`, the WEIGHT law); the Poisson tail (`gammainc`) lives only in
    the object; the three closed forms never touch `quantity_rate`; the sampler floors
    nothing by hand; and every recorded block names the law it read."""
    import io
    import tokenize

    def code(rel):
        """The file's CODE tokens only -- a docstring that mentions the old reading is prose."""
        with open(os.path.join(_ROOT, rel), encoding='utf-8') as fh:
            toks = tokenize.generate_tokens(io.StringIO(fh.read()).readline)
            return ' '.join(t.string for t in toks
                            if t.type not in (tokenize.COMMENT, tokenize.STRING, tokenize.NL,
                                              tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT))
    for rel in ('Optimization/simconfig/expected_travel.py', 'Optimization/simconfig/staffing.py',
                'Optimization/simconfig/coverage.py', 'Warehouse/picking/Workload_Builder.py',
                'Inbound/gain.py'):
        s = code(rel)
        assert 'poisson_sample' not in s and 'gammainc' not in s, rel
        if rel != 'Inbound/gain.py':
            assert 'quantity_rate' not in s, f'{rel} reads the rate scalar instead of the law'
    # gain.py keeps ONE read of the scalar: its documented never-picked sentinel (rate <= 0,
    # an arrival fact); the units per line come from the law.
    g = code('Inbound/gain.py')
    assert g.count('quantity_rate') == 1 and 'order . demand . line . mean ( )' in g
    assert 'max ( 1 , c . demand . sample' not in code('Warehouse/picking/Workload_Builder.py')
    assert "'line_law': line_law_census(inventory.orders)" in inspect.getsource(
        workunits._derive_staffing_for_pair)
    assert 'line_families' in inspect.getsource(st.analytic_pick)
    assert 'line_families' in inspect.getsource(cov.rescale_section)
    assert 'line_families' in inspect.getsource(et.accumulate)
