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
  * the inventory file -- the two columns survive a save/load; the VINTAGE CHAIN (three
    archived shapes, each reconstructed here and proved by its `observed_id`) still loads,
    with the law reconstructed on the pre-stamp one, the census saying so, and every
    vintage's STOCK DECLARATION still served through the `stock_levels` query (ADR-0002);
    and no hand-coded line law is left outside the object.

Run:  python -m pytest Tests/unit/test_line_distribution.py -q
"""
from __future__ import annotations

import inspect
import json
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
    o = Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 6.7)
    # Order.build clamps the rate to an integer; the reconstructed law is at the CLAMPED rate --
    # what every pre-stamp load sampled.
    assert o.demand.quantity_rate == 7
    assert o.demand.line.params == {'lam': 7.0} and o.demand.line.provenance == 'assumed'
    assert Demand().line.provenance == 'assumed'          # the random path says so too


def test_a_stamped_law_is_kept_and_rides_the_reorder():
    stamped = LineDistribution.poisson(6.0)
    o = Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 6.0, line=stamped)
    assert o.demand.line is stamped and o.demand.line.provenance == 'declared'
    r = o.reorder()
    assert r.demand.line is stamped, 'a restock carries the SKU law, it does not rebuild one'
    assert r.demand.quantity_rate == o.demand.quantity_rate


def test_a_law_authored_at_the_unclamped_rate_follows_the_clamp_and_a_stranger_raises():
    # Authored at 6.7 (the legacy generator draws floats); the clamp makes the rate 7, and
    # the law follows it -- exactly what a saved-and-loaded copy always sampled.
    o = Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 6.7,
                    line=LineDistribution.poisson(6.7))
    assert o.demand.quantity_rate == 7 and o.demand.line.params == {'lam': 7.0}
    assert o.demand.line.provenance == 'declared'
    # A law that agrees with neither the authored nor the clamped rate is a corrupt stamp.
    with pytest.raises(ValueError, match='stamped line law'):
        Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 6.0,
                    line=LineDistribution.poisson(9.0))


def test_the_census_names_the_families_and_the_reconstructions():
    a = Order.build(1, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 3.0,
                    line=LineDistribution.poisson(3.0))
    b = Order.build(2, 'conveyable', 'seasonal', 10, 10, 10, 10, 0.5, 4.0)
    assert line_law_census([a, b]) == {
        'n_skus': 2, 'families': {'poisson_max1': 2},
        'provenance': {'assumed': 1, 'declared': 1}, 'reconstructed_skus': 1}
    assert line_law_census([]) == {'n_skus': 0, 'families': {}, 'provenance': {},
                                   'reconstructed_skus': 0}


# ═════════════════════════════════════════════════════════════════════════════════════════
# BYTE-IDENTICAL: goldens captured on the commit before the stamp
# ═════════════════════════════════════════════════════════════════════════════════════════

def _golden_inventory(n: int = 20) -> Inventory:
    """The fixed 20-SKU catalogue the goldens were captured on (rates from Random(3)).

    It DECLARES NO STOCK (ADR-0002): the goldens below were captured when `Order.build` still
    took `equilibrium_qty=60, reorder_point=20`, and they are unchanged, because a level was
    never an input to the batch sampler -- the draw reads `relative_frequency` and the line
    law, nothing else.  If a golden here ever moves, the sampler regressed; do not repaste it.
    """
    Order.next_sku = 1
    r = random.Random(3)
    orders = [Order.build(i + 1, 'conveyable', 'seasonal', 10, 10, 10, 10,
                          r.uniform(0.05, 1.0), r.uniform(0.5, 20.0)) for i in range(n)]
    assert not any(c.stock_declared() for c in orders), 'a built order declares nothing'
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


# ═════════════════════════════════════════════════════════════════════════════════════════
# The vintage chain: three archived shapes, each rebuilt here and PROVED by its id
# ═════════════════════════════════════════════════════════════════════════════════════════
#
# FROZEN HISTORY, and it can never again be derived from the live source.  Until ADR-0002 the
# whole chain was reconstructed by regex-stripping columns out of `gi._SCHEMA`, which worked
# only while today's DDL was a strict SUPERSET of every archived one.  The stock split broke
# that for good: `equilibrium_qty` / `reorder_point` / `stock_plan` / `pipeline_qty` LEFT
# `cartons` for the new `stock_levels` table, so today's DDL is no longer a superset of any
# vintage below and no amount of stripping can put those four columns back.  The pre-split
# `cartons` is therefore pasted here verbatim -- it is the shape committed at
# `Schema/shapes/inventory_db/025f4b1548a9.json`, a document about files that already exist on
# disk, which is exactly the kind of thing a literal is for.  The two OLDER vintages are still
# derived from it by stripping, because relative to *it* they are strict subsets.
#
# The `observed_id(con) == <ID>` assertion in each test below is the proof that a
# reconstruction is exactly the vetted vintage.  If one fails, the literal is wrong (or a
# vintage constant moved) -- do NOT relax the assertion; it is the only thing standing between
# "we tested the archive" and "we tested a shape we made up".
_PRE_SPLIT_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS cartons (
        sku                   INTEGER PRIMARY KEY,
        handling              TEXT    NOT NULL,
        category              TEXT    NOT NULL,
        length                INTEGER NOT NULL,
        width                 INTEGER NOT NULL,
        height                INTEGER NOT NULL,
        weight                INTEGER NOT NULL,
        relative_frequency    REAL    NOT NULL,
        demand_qty_rate       REAL    NOT NULL,
        -- The LINE LAW, stamped per SKU.  Vintage 0e234fbfc739 predates it and lacks both
        -- columns; its `cartons` override serves them NULL and the loader reconstructs
        -- Poisson(demand_qty_rate) with provenance `assumed`.
        line_family           TEXT    NOT NULL,
        line_params           TEXT,
        expected_batch_demand REAL    NOT NULL DEFAULT 0,
        equilibrium_qty       INTEGER NOT NULL DEFAULT 1,
        reorder_point         INTEGER NOT NULL DEFAULT 1,
        lead_time_mean        REAL    NOT NULL DEFAULT 0.0,
        supply_cv             REAL    NOT NULL DEFAULT 0.0,
        stock_plan            TEXT,
        -- The era's STAMPED lead pipeline, `round(d_s x lead)`.  Vintages 0e234fbfc739 and
        -- 4ff06991df47 predate it; unstamped, `Order.pipeline_allowance` falls back to the
        -- manager's rp x lead / (lead + 1) heuristic.
        pipeline_qty          INTEGER,
        -- Fine-grained family label for distribution plots.
        subtype               TEXT
    );
    CREATE TABLE IF NOT EXISTS run_metadata (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS creation_plan (
        handling       TEXT NOT NULL,
        storage_type   TEXT NOT NULL,
        parameter      TEXT NOT NULL,
        distribution   TEXT NOT NULL,
        params         TEXT,
        handling_share REAL NOT NULL,
        family_share   REAL NOT NULL,
        PRIMARY KEY (handling, storage_type, parameter)
    );
'''


def _pre_split_ddl() -> str:
    """The pre-split `_SCHEMA` (025f4b1548a9): the four level columns still in `cartons` and
    no `stock_levels` table at all.  A LITERAL, for the reason spelled out above."""
    return _PRE_SPLIT_SCHEMA


def _pre_pipeline_ddl() -> str:
    """The pre-pipeline `_SCHEMA` (the line-law vintage, 4ff06991df47): the pre-split DDL with
    the `pipeline_qty` column (and its comment) removed -- the tests below prove that is
    EXACTLY the vetted vintage between the line-law stamp and the line floor."""
    ddl, n = re.subn(r"\n\s*-- The era's STAMPED lead pipeline.*?pipeline_qty\s+INTEGER,", '',
                     _pre_split_ddl(), flags=re.S)
    assert n == 1, 'the DDL comment block anchoring the pipeline column moved'
    assert 'pipeline_qty' not in ddl
    return ddl


def _pre_stamp_ddl() -> str:
    """The pre-stamp `_SCHEMA` (0e234fbfc739): the pre-pipeline DDL with the two line-law
    columns (and their comment) removed too -- and the test below proves that is EXACTLY the
    vetted pre-stamp shape."""
    ddl, n = re.subn(r'\n\s*-- The LINE LAW.*?line_params\s+TEXT,', '', _pre_pipeline_ddl(),
                     flags=re.S)
    assert n == 1, 'the DDL comment block anchoring the pre-stamp reconstruction moved'
    assert 'line_family' not in ddl and 'line_params' not in ddl
    return ddl


def test_every_reconstructed_vintage_hashes_to_the_id_the_family_vets():
    """One place to read the whole chain: three DDLs, three vetted ids, and the current
    writer's own DDL as the fourth -- all four distinct, so no reconstruction has silently
    collapsed onto its neighbour (a strip that removed nothing would still 'load')."""
    ids = {}
    for name, ddl, want in (
            ('pre-stamp',    _pre_stamp_ddl(),    gi.PRE_LINE_LAW_INVENTORY_SCHEMA_ID),
            ('pre-pipeline', _pre_pipeline_ddl(), gi.PRE_PIPELINE_INVENTORY_SCHEMA_ID),
            ('pre-split',    _pre_split_ddl(),    gi.PRE_STOCK_SPLIT_INVENTORY_SCHEMA_ID)):
        con = sqlite3.connect(':memory:')
        con.executescript(ddl)
        ids[name] = _shape.observed_id(con)
        con.close()
        assert ids[name] == want, f'{name}: reconstructed {ids[name]}, vetted {want}'
        assert want in gi.INVENTORY_DB_FAMILY.known_ids, f'{name} is not a vetted vintage'
    ids['current'] = _shape.shape_id(gi.declared_inventory_shape())
    assert len(set(ids.values())) == 4, ids
    # ...and the split is real: the four level columns left `cartons` for `stock_levels`.
    cur = gi.declared_inventory_shape()['tables']
    assert {c['name'] for c in cur['stock_levels']['columns']} == {
        'sku', 'equilibrium_qty', 'reorder_point', 'stock_plan', 'pipeline_qty'}
    assert not ({'equilibrium_qty', 'reorder_point', 'stock_plan', 'pipeline_qty'}
                & {c['name'] for c in cur['cartons']['columns']})


def _levels_for(inv, *, pipeline=None):
    """(equilibrium_qty, reorder_point, stock_plan_json, pipeline_qty) per SKU -- the levels a
    run of that vintage would have written into its `planned_inventory.db`."""
    return [(60 + 3 * i, 20 + i,
             json.dumps([[False, 12, 4], [True, 1, 5]]),
             None if pipeline is None else pipeline + i)
            for i, _ in enumerate(inv.orders)]


def _assert_levels_landed(back, want):
    """Every loaded order carries the file's declaration, under the logical names."""
    assert len(back.orders) == len(want)
    for c, (q, rp, sp, pq) in zip(back.orders, want):
        assert c.stock_declared(), f'SKU {c.sku} lost its declaration on the way out of the file'
        assert c.equilibrium_qty == q and c.reorder_point == rp
        assert c.stock_plan == [(False, 12, 4), (True, 1, 5)] and json.loads(sp)
        assert c.pipeline_qty == pq


def test_a_pre_split_file_still_yields_the_levels_its_run_fielded(tmp_path):
    """The vintage BEFORE the stock split (025f4b1548a9, ADR-0002): the declaration is in
    `cartons`, not `stock_levels`.  The `stock_levels` override reads it out of `cartons`, so
    an archived planned inventory loads DECLARED -- levels, packing and the pipeline stamp."""
    inv = _golden_inventory(5)
    want = _levels_for(inv, pipeline=7)
    path = str(tmp_path / 'pre_split.db')
    con = sqlite3.connect(path)
    con.executescript(_pre_split_ddl())
    assert _shape.observed_id(con) == gi.PRE_STOCK_SPLIT_INVENTORY_SCHEMA_ID
    con.executemany(
        'INSERT INTO cartons (sku, handling, category, length, width, height, weight, '
        ' relative_frequency, demand_qty_rate, line_family, line_params, expected_batch_demand, '
        ' equilibrium_qty, reorder_point, lead_time_mean, supply_cv, stock_plan, pipeline_qty, '
        ' subtype) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [(c.sku, 'conveyable', 'seasonal', 10, 10, 10, 10, c.demand.relative_frequency,
          c.demand.quantity_rate, *c.demand.line.to_row(), 0.0, q, rp, 2.0, 0.0, sp, pq, None)
         for c, (q, rp, sp, pq) in zip(inv.orders, want)])
    con.commit()
    con.close()
    back = gi.load_inventory_from_db(path)
    for a, b in zip(inv.orders, back.orders):
        assert b.demand.line.provenance == 'declared' and b.demand.line.params == a.demand.line.params
    _assert_levels_landed(back, want)
    # The stamp is the answer here -- no heuristic runs.
    assert [c.pipeline_allowance() for c in back.orders] == [7, 8, 9, 10, 11]
    assert len(gi.load_inventory_from_db(path, limit=2).orders) == 2   # both reads share the LIMIT


def test_a_pre_pipeline_file_loads_through_its_override_with_no_stamp(tmp_path):
    """The vintage BEFORE `pipeline_qty` ("Build the line floor", 4ff06991df47): the law is on
    the file, the pipeline is not; the override serves it NULL, and `Order.pipeline_allowance`
    falls back to the manager's heuristic -- what every run of that vintage fired.  Its levels
    are in `cartons` too, and come back declared."""
    inv = _golden_inventory(4)
    want = _levels_for(inv)                       # pipeline_qty: the column does not exist
    path = str(tmp_path / 'law_no_pipeline.db')
    con = sqlite3.connect(path)
    con.executescript(_pre_pipeline_ddl())
    assert _shape.observed_id(con) == gi.PRE_PIPELINE_INVENTORY_SCHEMA_ID
    con.executemany(
        'INSERT INTO cartons (sku, handling, category, length, width, height, weight, '
        ' relative_frequency, demand_qty_rate, line_family, line_params, expected_batch_demand, '
        ' equilibrium_qty, reorder_point, lead_time_mean, supply_cv, stock_plan, subtype) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [(c.sku, 'conveyable', 'seasonal', 10, 10, 10, 10, c.demand.relative_frequency,
          c.demand.quantity_rate, *c.demand.line.to_row(), 0.0, q, rp, 2.0, 0.0, sp, None)
         for c, (q, rp, sp, _) in zip(inv.orders, want)])
    con.commit()
    con.close()
    back = gi.load_inventory_from_db(path)
    assert len(back.orders) == 4
    for a, b in zip(inv.orders, back.orders):
        assert b.demand.line.provenance == 'declared' and b.demand.line.params == a.demand.line.params
    _assert_levels_landed(back, want)
    for c, (_, rp, _, _) in zip(back.orders, want):
        assert c.pipeline_qty is None
        assert c.pipeline_allowance() == round(rp * 2.0 / 3.0)     # the heuristic, byte for byte


def test_a_stamped_pipeline_survives_the_file_round_trip(tmp_path):
    """The CURRENT vintage: a run's declaration goes out through `stock_levels` and comes back
    whole, stamp included."""
    inv = _golden_inventory(4)
    for i, c in enumerate(inv.orders):
        # 0 is a stamp; None would be "not stamped".  The four slots move together now.
        c.declare_stock(60, 20, stock_plan=[(False, 12, 4)], pipeline_qty=i * 3)
    path = str(tmp_path / 'stamped.db')
    gi.save_inventory_to_db(inv, path, {'test': True})
    back = gi.load_inventory_from_db(path)
    assert all(c.stock_declared() for c in back.orders)
    assert [c.pipeline_qty for c in back.orders] == [0, 3, 6, 9]
    assert [c.pipeline_allowance() for c in back.orders] == [0, 3, 6, 9]
    assert [c.equilibrium_qty for c in back.orders] == [60] * 4
    assert [c.reorder_point for c in back.orders] == [20] * 4
    assert all(c.stock_plan == [(False, 12, 4)] for c in back.orders)


def test_a_current_catalogue_with_an_empty_stock_levels_loads_undeclared(tmp_path):
    """The case the vintage tests do NOT cover, and the whole point of ADR-0002: a GENERATED
    catalogue declares nothing.  `stock_levels` exists and is empty -- no row means no
    declaration, as against a NULL column, which would read as an authored level that happens
    to be missing -- and the orders come back with the four slots UNSET."""
    inv = _golden_inventory(4)
    path = str(tmp_path / 'catalogue.db')
    gi.save_inventory_to_db(inv, path, {'test': True})
    con = sqlite3.connect(path)
    assert con.execute('SELECT COUNT(*) FROM stock_levels').fetchone()[0] == 0, \
        'a generated catalogue wrote a level'
    assert con.execute('SELECT COUNT(*) FROM cartons').fetchone()[0] == 4   # the SKUs are there
    con.close()
    back = gi.load_inventory_from_db(path)
    assert len(back.orders) == 4
    for c in back.orders:
        assert not c.stock_declared()
        with pytest.raises(AttributeError):
            c.equilibrium_qty                      # unset, not defaulted to 1
        assert c.pipeline_allowance() == 0         # lead 0 and no stamp
    # ...and the demand side is untouched by the absent declaration.
    for a, b in zip(inv.orders, back.orders):
        assert b.demand.line.params == a.demand.line.params
        assert b.demand.quantity_rate == a.demand.quantity_rate


def test_a_pre_stamp_file_loads_through_the_override_with_the_law_reconstructed(tmp_path):
    inv = _golden_inventory(6)
    want = _levels_for(inv)                       # neither the law nor the pipeline exists here
    path = str(tmp_path / 'old_inventory.db')
    con = sqlite3.connect(path)
    con.executescript(_pre_stamp_ddl())
    # The reconstruction hashes to the id the family vets: the committed pre-stamp document
    # IS the pre-split DDL minus three columns, and nothing else moved.
    assert _shape.observed_id(con) == gi.PRE_LINE_LAW_INVENTORY_SCHEMA_ID
    con.executemany(
        'INSERT INTO cartons (sku, handling, category, length, width, height, weight, '
        ' relative_frequency, demand_qty_rate, expected_batch_demand, equilibrium_qty, '
        ' reorder_point, lead_time_mean, supply_cv, stock_plan, subtype) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [(c.sku, 'conveyable', 'seasonal', 10, 10, 10, 10, c.demand.relative_frequency,
          c.demand.quantity_rate + 0.3,          # a legacy float rate: the load clamps it
          0.0, q, rp, 0.0, 0.0, sp, None)
         for c, (q, rp, sp, _) in zip(inv.orders, want)])
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
    # The levels ride out of `cartons` through the override, unstamped (lead 0 -> allowance 0).
    _assert_levels_landed(back, want)
    assert all(c.pipeline_allowance() == 0 for c in back.orders)
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
