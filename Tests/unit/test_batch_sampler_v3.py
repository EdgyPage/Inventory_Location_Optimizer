"""test_batch_sampler_v3.py

Gates for the segment-tree batch sampler (BatchConfig.sampler='v3').

v3 exists for ONE reason: v2 does not draw `k` distinct SKUs.  `Batch.items` is keyed by
sku, so every repeat collapses and the batch silently delivers fewer lines than the era
declared — measured at -8.64% on the reference pair's fulfillment section, 40/40 batches
short.  The cause is `_Fenwick`'s subtractive update (`t[j] += new - old`) under the
conditional-demand model's enormous weight dynamic range: affinity lift is 2.8–5.0 per
partner over a median 35 partners, so `lift_mult` compounds to ~1e20 against base
frequencies of ~1e-6.  Removing such a weight annihilates the small weights aggregated
into the same node (``1e19 + 1e4 == 1e19``) and the node KEEPS the difference as phantom
mass — the Fenwick's total was measured 14.6% above the true sum of its own leaves.

`_SegTree` recomputes every internal node from its two children instead, so no residue can
survive a removal, and its descent enters only subtrees carrying positive mass — making an
already-drawn SKU structurally unreachable rather than merely improbable.

The first test is the ticket's oracle: the same clustered fixture that makes v2 repeat
itself must make v3 deliver exactly what it was asked for.  A uniformly-random partner
graph does NOT reproduce the defect — the lift multiplications spread too thin — so the
fixture is a mutual-partner clique, which is what the real affinity CSR looks like locally.

    cd Tests && python -m pytest unit/test_batch_sampler_v3.py
"""
from __future__ import annotations

import random
from fractions import Fraction

from Warehouse.picking import Workload_Builder as wb
from Warehouse.picking.Workload_Builder import (
    Batch, BatchConfig, _SegTree, _lift_weighted_sample_v2, _lift_weighted_sample_v3)
from Optimization.simdriver.batch_precompute import batch_fingerprint


# ── stubs (the sampler touches only .sku and .demand.relative_frequency/.sample) ──────
class _Demand:
    def __init__(self, freq, qty=2):
        self.relative_frequency = freq
        self.quantity_rate = qty

    def sample(self, rng=None):
        return self.quantity_rate


class _Order:
    def __init__(self, sku, freq):
        self.sku = sku
        self.demand = _Demand(freq)


class _Inventory:
    def __init__(self, orders):
        self.orders = orders


#: The shape that breaks v2, at unit-test size.  CLUSTERING is the essential ingredient:
#: every member of the clique is a partner of every other, so each draw inside it
#: multiplies the survivors' weight by `lift` again and the vector spans ~5**64.
_N, _CLIQUE, _K, _LIFT = 3072, 64, 1536, 5.0


def _clustered_world(seed):
    """(candidates, affinity) — a mutual-partner clique embedded in a plain catalogue."""
    rng = random.Random(seed)
    cands = [_Order(i + 1, max(1e-6, rng.random())) for i in range(_N)]
    aff = {(cands[a].sku, cands[b].sku): _LIFT
           for a in range(_CLIQUE) for b in range(a + 1, _CLIQUE)}
    return cands, aff


def _repeats(selected):
    return len(selected) - len({c.sku for c in selected})


# ── the oracle: v3 delivers k distinct where v2 does not ───────────────────────────────
def test_v3_draws_k_distinct_on_the_fixture_that_breaks_v2():
    """v3 must repeat NOTHING on every seed, and the fixture must be one v2 actually
    fails — otherwise this gate is vacuous and would pass against the defect it exists
    to catch (Tests/README: a test that cannot fail proves nothing)."""
    v2_failing_seeds, v3_repeats = [], 0
    for seed in range(12):
        cands, aff = _clustered_world(seed)
        wb._partner_map_cache.clear()   # cache is keyed by id(affinity); fixtures churn ids
        v2 = _lift_weighted_sample_v2(cands, _K, aff, rng=random.Random(1000 + seed))
        wb._partner_map_cache.clear()
        v3 = _lift_weighted_sample_v3(cands, _K, aff, rng=random.Random(1000 + seed))
        if _repeats(v2):
            v2_failing_seeds.append(seed)
        v3_repeats += _repeats(v3)
        assert len(v3) == _K, f'seed {seed}: v3 returned {len(v3)} of {_K} draws'

    assert v3_repeats == 0, f'v3 repeated a SKU {v3_repeats} times'
    assert len(v2_failing_seeds) >= 6, (
        f'the fixture no longer reproduces the v2 defect (failed on '
        f'{v2_failing_seeds}); this gate would pass vacuously')


#: (fixture seed, rng seed) at which v2 COLLAPSES — it returns a full k draws of which
#: only 123 are distinct.  Pinned deliberately: v2 has a second, different failure mode on
#: this fixture (its tree total goes non-positive and the draw loop breaks early, returning
#: fewer than k), and a short batch from an early break would let the collapse gates below
#: pass without ever exercising a collapse.
_COLLAPSE_FIXTURE, _COLLAPSE_RNG = 0, 1007


def test_batch_delivers_every_requested_line_under_v3():
    """The defect's actual cost lands here: `Batch.items` is a dict keyed by sku, so a
    repeat collapses into one line and the batch is short."""
    cands, aff = _clustered_world(_COLLAPSE_FIXTURE)
    inv = _Inventory(cands)
    for ver, expect_short in (('v2', True), ('v3', False)):
        cfg = BatchConfig(inventory_size=_N, mean_fraction=0.5, std_fraction=0.0,
                          sampler=ver)
        wb._partner_map_cache.clear()
        b = Batch(cfg, inv, affinity=aff, rng=random.Random(_COLLAPSE_RNG))
        k = min(b.num_skus, _N)
        short = k - len(b.items)
        if expect_short:
            assert short > 0, 'the v2 fixture must still be short, or v3 proves nothing'
        else:
            assert short == 0, f'v3 delivered {len(b.items)} lines of {k} requested'


# ── the collapse guard ─────────────────────────────────────────────────────────────────
def test_batch_refuses_a_collapsed_batch_under_v1_and_v3_but_not_v2():
    """A repeat collapses in the sku-keyed `items` dict, which is how the v2 defect stayed
    silent.  v1 and v3 promise distinct draws, so a collapse under them must RAISE; v2 is
    kept runnable on purpose so its archive can still be reproduced."""
    cands, aff = _clustered_world(_COLLAPSE_FIXTURE)
    inv = _Inventory(cands)

    wb._partner_map_cache.clear()
    v2_cfg = BatchConfig(inventory_size=_N, mean_fraction=0.5, std_fraction=0.0,
                         sampler='v2')
    b = Batch(v2_cfg, inv, affinity=aff, rng=random.Random(_COLLAPSE_RNG))
    assert len(b.items) < min(b.num_skus, _N), \
        'v2 must still collapse here, or this test cannot tell silence from safety'

    # A sampler that PROMISES distinctness but hands back a repeat must be refused.  Lend
    # v3's name to v2's draw to produce exactly that, since real v3 never repeats.
    v3_cfg = BatchConfig(inventory_size=_N, mean_fraction=0.5, std_fraction=0.0,
                         sampler='v3')
    saved = wb._SAMPLERS['v3']
    wb._SAMPLERS['v3'] = wb._lift_weighted_sample_v2
    try:
        wb._partner_map_cache.clear()
        Batch(v3_cfg, inv, affinity=aff, rng=random.Random(_COLLAPSE_RNG))
    except RuntimeError as e:
        assert 'collapsed' in str(e), e
    else:
        raise AssertionError('a collapsed batch must refuse under a distinctness-promising '
                             'sampler, not deliver a short batch')
    finally:
        wb._SAMPLERS['v3'] = saved

    # and the real v3 sails through the same fixture
    wb._partner_map_cache.clear()
    ok = Batch(v3_cfg, inv, affinity=aff, rng=random.Random(_COLLAPSE_RNG))
    assert len(ok.items) == min(ok.num_skus, _N)


# ── the two invariants that make v3 correct rather than lucky ──────────────────────────
def test_segtree_total_is_exactly_a_rebuild_where_fenwick_drifts():
    """Every internal node is recomputed from its children, so the root is bit-for-bit
    what a fresh build of the SAME live weights gives — at any dynamic range.  This is the
    property `_Fenwick` loses: it was measured 14.6% out on the reference section."""
    rng = random.Random(11)
    weights = [10.0 ** rng.uniform(-6, 0) for _ in range(1024)]
    tree = _SegTree(weights)
    for step in range(400):
        i = rng.randrange(1024)
        # alternate a colossal lift with a removal: the exact sequence that annihilates
        # small weights inside a shared node
        tree.set(i, weights[i] * 10.0 ** 20 if step % 2 else 0.0)
        assert tree.total() == _SegTree(tree.w).total(), \
            f'step {step}: root diverged from a rebuild of its own leaves'


def test_segtree_never_returns_a_zero_weight_leaf():
    """The structural guarantee: `find` descends only into subtrees with positive mass, so
    an already-drawn candidate is unreachable however large the float error grows — including
    at `u` exactly 0.0 and exactly the root total, which `random.uniform` can both return."""
    rng = random.Random(5)
    weights = [0.0] * 600
    for i in rng.sample(range(600), 40):
        weights[i] = 10.0 ** rng.uniform(-18, 18)
    tree = _SegTree(weights)
    for _ in range(3000):
        total = tree.total()
        if total <= 0.0:
            break
        for u in (0.0, total, rng.uniform(0.0, total)):
            assert tree.w[tree.find(u)] > 0.0, f'find({u!r}) landed on a dead leaf'
        tree.set(tree.find(rng.uniform(0.0, total)), 0.0)


def test_segtree_find_matches_exact_arithmetic():
    """`find(u)` must return the index a reference computed in EXACT rationals returns.
    Fractions remove float grouping from the reference, so this pins the search rule
    itself, boundaries included.

    The rule is the first index with POSITIVE weight whose prefix sum strictly exceeds u,
    and the last positive index once u reaches the total.  That strictness is a deliberate
    difference from v1, whose `np.searchsorted(cumw, u)` is side='left' and so returns the
    index where the prefix first *reaches* u.  The two disagree only when u lands exactly
    on a prefix boundary — measure zero for a continuous draw — and the strict form is what
    makes a zero-weight leaf unreachable instead of merely unlikely, which is v3's
    whole reason for existing.
    """
    rng = random.Random(13)
    for _ in range(60):
        n = rng.randint(1, 40)
        weights = [float(rng.randint(0, 5)) for _ in range(n)]
        if sum(weights) == 0.0:
            continue
        tree = _SegTree(weights)
        assert tree.total() == sum(weights)
        exact = [Fraction(w) for w in weights]
        last_live = max(i for i, w in enumerate(exact) if w > 0)
        u = Fraction(0)
        while u <= Fraction(sum(weights)):
            run, ref = Fraction(0), None
            for i, w in enumerate(exact):
                run += w
                if run > u and w > 0:
                    ref = i
                    break
            if ref is None:                      # u at or beyond the total
                ref = last_live
            assert tree.find(float(u)) == ref, (weights, u)
            u += Fraction(1, 2)


# ── v3 behaviour mirrors the v1/v2 contract ────────────────────────────────────────────
def test_v3_deterministic_and_without_replacement():
    cands = [_Order(i + 1, max(1e-6, random.Random(3 + i).random())) for i in range(500)]
    a = _lift_weighted_sample_v3(cands, 200, None, rng=random.Random(42))
    b = _lift_weighted_sample_v3(cands, 200, None, rng=random.Random(42))
    assert [c.sku for c in a] == [c.sku for c in b], 'same seed, same sequence'
    assert len({c.sku for c in a}) == 200, 'sampling is without replacement'

    everything = _lift_weighted_sample_v3(cands, 500, None, rng=random.Random(9))
    assert sorted(c.sku for c in everything) == sorted(c.sku for c in cands), \
        'k == n draws every candidate exactly once'


def test_v3_weight_model_favors_heavy_candidates():
    cands = [_Order(1, 1.0)] + [_Order(i + 2, 0.01) for i in range(99)]
    hits = sum(1 for r in range(300)
               if any(c.sku == 1 for c in
                      _lift_weighted_sample_v3(cands, 5, None, rng=random.Random(r))))
    assert hits > 250, f'heavy candidate picked in only {hits}/300 draws'


# ── fingerprint separation ─────────────────────────────────────────────────────────────
def test_fingerprint_v3_distinct_from_v1_and_v2():
    """v3 batches must never be served from — or poison — a v1 or v2 cache file."""
    inv = _Inventory([_Order(i + 1, max(1e-6, random.Random(5 + i).random()))
                      for i in range(30)])
    fps = {v: batch_fingerprint(inv, BatchConfig(inventory_size=30, sampler=v), 100, 10, None)
           for v in ('v1', 'v2', 'v3')}
    assert len(set(fps.values())) == 3, f'fingerprints collided: {fps}'
    assert fps['v1'] == batch_fingerprint(inv, BatchConfig(inventory_size=30), 100, 10, None), \
        'v1 must still hash exactly as it did before any sampler field existed'
