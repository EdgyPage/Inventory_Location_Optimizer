"""test_affinity_lift_invariant.py

Pins the affinity-correction invariant (commit fd29fa7): lift is a multiplier where
1 = independence, so every co-location objective sums (lift − 1), not raw lift.  These
tests lock that convention into the central helpers so a future edit can't silently
regress to raw lift, and they verify the seed (init_lift_state via sum_lift) matches the
incremental maintenance (2·delta_lift_idxs) used by the reorder path.

    cd Tests && python -m pytest test_affinity_lift_invariant.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, 'Warehouse'))
sys.path.insert(0, os.path.join(_ROOT, 'Optimization'))

from Affinity_Store import AffinityStore
from Assignment_Functions import _demand_weighted_delta_lift


def _store(pairs):
    """In-memory AffinityStore from undirected (sku_a, sku_b, lift) pairs (written both ways)."""
    rows = []
    for a, b, v in pairs:
        rows += [(a, b, v), (b, a, v)]
    st = AffinityStore(':memory:')
    st._conn.executemany('INSERT OR REPLACE INTO affinity VALUES (?,?,?)', rows)
    st._conn.commit()
    st._load_matrix()
    return st


def test_delta_lift_idxs_is_excess_over_independence():
    st = _store([(1, 2, 3.0), (1, 3, 1.5), (2, 3, 2.0)])
    idx = st._sku_to_idx
    # partners of 1 among {2,3}: (3−1)+(1.5−1) = 2.5
    assert abs(st.delta_lift_idxs(1, {idx[2], idx[3]}) - 2.5) < 1e-4
    assert abs(st.delta_lift_idxs(1, {idx[2]}) - 2.0) < 1e-4
    assert st.delta_lift_idxs(1, set()) == 0.0          # empty member set → 0
    assert st.delta_lift_idxs(1, {idx[1]}) == 0.0       # self pair not stored → 0


def test_sum_lift_subtracts_nnz():
    st = _store([(1, 2, 3.0), (1, 3, 1.5), (2, 3, 2.0)])
    # ordered-pair convention (each undirected pair twice): 2·[(3−1)+(1.5−1)+(2−1)] = 7.0
    assert abs(st.sum_lift([1, 2, 3]) - 7.0) < 1e-4
    assert abs(st.sum_lift([1, 2]) - 4.0) < 1e-4        # only pair (1,2): 2·(3−1)
    assert st.sum_lift([1]) == 0.0


def test_demand_weighted_delta_lift_weights_excess_by_freq():
    st = _store([(1, 2, 3.0), (1, 3, 2.0)])
    idx = st._sku_to_idx
    freq_by_idx = {idx[2]: 2.0, idx[3]: 0.5}
    # (3−1)·2.0 + (2−1)·0.5 = 4.5  (independence partners would add 0)
    got = _demand_weighted_delta_lift(st, 1, {idx[2], idx[3]}, freq_by_idx)
    assert abs(got - 4.5) < 1e-4


def test_seed_equals_incremental_rebuild():
    """The load-balancing invariant: init_lift_state seeds _aisle_lift_sum with
    sum_lift(all_skus); the reorder path maintains it by adding 2·delta_lift_idxs as each
    SKU joins.  Both must equal Σ(lift−1) over ordered pairs — otherwise the maintained
    lift_sum drifts from a fresh rebuild."""
    pairs = [(1, 2, 3.0), (1, 3, 1.5), (2, 3, 2.0), (2, 4, 4.0), (3, 4, 1.2)]
    st = _store(pairs)
    idx = st._sku_to_idx
    skus = [1, 2, 3, 4]
    seed = st.sum_lift(skus)
    inc, members = 0.0, set()
    for s in skus:
        inc += 2.0 * st.delta_lift_idxs(s, members)     # both (s,m) and (m,s) directed pairs
        members.add(idx[s])
    assert abs(seed - inc) < 1e-4
