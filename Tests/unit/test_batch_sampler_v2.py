"""test_batch_sampler_v2.py

Gates for the flag-gated Fenwick batch sampler (BatchConfig.sampler, default 'v1').

The v1 path must stay untouched by the feature: the default dispatch calls the ORIGINAL
`_lift_weighted_sample`, and `batch_fingerprint` for a v1 config hashes exactly what it
hashed before the field existed (the run-level proof is the tiny digest pair; this pins
the mechanism).  v2 is a NEW batch-sequence version: deterministic per seed, without
replacement, weight-model faithful, and fingerprinted apart so v2 batches can never be
served from a v1 cache file.

    cd Tests && python -m pytest unit/test_batch_sampler_v2.py
"""
from __future__ import annotations

import random

from Warehouse.picking import Workload_Builder as wb
from Warehouse.picking.Workload_Builder import (
    Batch, BatchConfig, _Fenwick, _lift_weighted_sample_v2)
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


def _world(n, seed=7):
    rng = random.Random(seed)
    return [_Order(i + 1, max(1e-6, rng.random())) for i in range(n)]


# ── flag semantics ─────────────────────────────────────────────────────────────────────
def test_default_config_dispatches_to_v1(monkeypatch):
    calls = []
    orig = wb._lift_weighted_sample
    monkeypatch.setitem(wb._SAMPLERS, 'v1',
                        lambda *a, **kw: calls.append('v1') or orig(*a, **kw))
    monkeypatch.setitem(wb._SAMPLERS, 'v2',
                        lambda *a, **kw: calls.append('v2') or orig(*a, **kw))
    cfg = BatchConfig(inventory_size=50)
    assert cfg.sampler == 'v1', 'the default must be the byte-identical original'
    Batch(cfg, _Inventory(_world(50)), affinity=None, rng=random.Random(1))
    assert calls == ['v1']

    Batch(BatchConfig(inventory_size=50, sampler='v2'),
          _Inventory(_world(50)), affinity=None, rng=random.Random(1))
    assert calls == ['v1', 'v2']


def test_unknown_sampler_raises():
    try:
        Batch(BatchConfig(inventory_size=10, sampler='v3'),
              _Inventory(_world(10)), affinity=None, rng=random.Random(1))
    except ValueError as e:
        assert 'sampler' in str(e)
        return
    raise AssertionError('an unknown sampler version must refuse, not guess')


# ── v2 behavior ────────────────────────────────────────────────────────────────────────
def test_v2_deterministic_and_without_replacement():
    cands = _world(500, seed=3)
    a = _lift_weighted_sample_v2(cands, 200, None, rng=random.Random(42))
    b = _lift_weighted_sample_v2(cands, 200, None, rng=random.Random(42))
    assert [c.sku for c in a] == [c.sku for c in b], 'same seed, same sequence'
    assert len({c.sku for c in a}) == 200, 'sampling is without replacement'

    everything = _lift_weighted_sample_v2(cands, 500, None, rng=random.Random(9))
    assert sorted(c.sku for c in everything) == sorted(c.sku for c in cands), \
        'k == n draws every candidate exactly once'


def test_v2_weight_model_favors_heavy_candidates():
    # one candidate 100x the weight of the rest: across many small draws it must be
    # picked far more often (weight model sanity, not a distribution-identity claim)
    cands = [_Order(1, 1.0)] + [_Order(i + 2, 0.01) for i in range(99)]
    hits = sum(1 for r in range(300)
               if any(c.sku == 1 for c in
                      _lift_weighted_sample_v2(cands, 5, None, rng=random.Random(r))))
    assert hits > 250, f'heavy candidate picked in only {hits}/300 draws'


def test_fenwick_matches_linear_reference_exactly():
    # integer-valued weights make every partial sum exactly representable, so the tree
    # and a sequential scan must agree on EVERY threshold including bucket boundaries.
    rng = random.Random(13)
    for _ in range(50):
        n = rng.randint(1, 40)
        weights = [float(rng.randint(0, 5)) for _ in range(n)]
        fw = _Fenwick(weights)
        total = sum(weights)
        assert fw.total() == total
        u = 0.0
        while u <= total:
            run = 0.0
            ref = n - 1
            for i, w in enumerate(weights):
                run += w
                if run >= u:
                    ref = i
                    break
            assert fw.find(u) == ref, (weights, u)
            u += 0.5


# ── fingerprint separation ─────────────────────────────────────────────────────────────
def test_fingerprint_v1_unchanged_and_v2_distinct():
    inv = _Inventory(_world(30, seed=5))
    base = BatchConfig(inventory_size=30)
    explicit_v1 = BatchConfig(inventory_size=30, sampler='v1')
    v2 = BatchConfig(inventory_size=30, sampler='v2')
    fp_default = batch_fingerprint(inv, base, 100, 10, None)
    fp_v1 = batch_fingerprint(inv, explicit_v1, 100, 10, None)
    fp_v2 = batch_fingerprint(inv, v2, 100, 10, None)
    assert fp_default == fp_v1, 'v1 must hash exactly as before the field existed'
    assert fp_v2 != fp_v1, 'v2 batches must never be served from a v1 cache file'
