"""test_aisle_routing_rows.py — the row-wise `_aisle_routing` is the per-column one, bit for bit.

`expected_travel._aisle_routing` prices one aisle's routing chain: per column, the
probability the picker enters, the expected span it climbs, and the vertical entry from
where the last column left it.  It is called for every aisle of a section per quadrature
node, inside the per-arm `[era]` expected-day stamp -- 4.2M `np.insert` calls per 400k arm
before `.scratch/inbound-fullscale-perf/` S10 computed every column's row terms at once and
left only the state recursion as a loop.

The per-column form is frozen below as the oracle.  Equality is `==` on all three returned
floats: the stamp is persisted and digested, so "close" would be a comparability break.
Row lengths run past 8 on purpose -- numpy's pairwise summation only departs from a plain
left fold there -- and the scenes include unvisited columns, single-row aisles, tiny
rates and both exit rules.

Run:  python -m pytest Tests/unit/test_aisle_routing_rows.py -q
"""
from __future__ import annotations

import numpy as np
import pytest

from Optimization.simconfig import expected_travel as et


def _oracle(m, a, one_way):
    """The per-column `_aisle_routing` as it stood before S10, verbatim."""
    C, R = a.C, a.R
    v = 1.0 - np.exp(-m)
    u = 1.0 - np.prod(1.0 - v, axis=1)
    surv = np.cumprod((1.0 - u)[::-1])[::-1]
    p_visit = 1.0 - surv[0]
    if p_visit < 1e-15:
        return 0.0, 0.0, 0.0
    tail_above = np.append(surv[1:], 1.0)
    xs = np.array([a.x_of(c) for c in range(1, C + 1)], float)
    ex = p_visit * a.length if one_way else float((xs * u * tail_above).sum())
    ys = np.array([0.0] + [a.y_of(r) for r in range(1, R + 1)])
    D = np.abs(ys[:, None] - ys[None, :])
    state = np.zeros(R + 1); state[0] = 1.0
    ey = 0.0
    for c in range(C):
        uc = u[c]
        if uc < 1e-15:
            continue
        vr = v[c]
        q = vr / vr.sum()
        above = np.cumprod((1.0 - vr)[::-1])[::-1]; above = np.append(above[1:], 1.0)
        below = np.cumprod(1.0 - vr); below = np.insert(below[:-1], 0, 1.0)
        e_high = float((ys[1:] * vr * above).sum()) / uc
        e_low = float((ys[1:] * vr * below).sum()) / uc
        entry = float(state @ D[:, 1:] @ q)
        ey += uc * (entry + (e_high - e_low))
        state = (1.0 - uc) * state + uc * np.concatenate(([0.0], q))
    if one_way:
        ey += float(state @ ys)
    return float(p_visit), float(ex), float(ey)


def _scene(rng, C, R, kind):
    if kind == 'dense':
        m = rng.gamma(0.6, 0.4, size=(C, R))
    elif kind == 'sparse':
        m = rng.gamma(0.4, 0.05, size=(C, R)) * (rng.random((C, R)) < 0.2)
    else:                                                    # 'tiny': near the 1e-15 guards
        m = rng.random((C, R)) * 1e-9 * (rng.random((C, R)) < 0.5)
    if C > 2:
        m[rng.integers(0, C)] = 0.0                          # an unvisited column
    return m


_SHAPES = [(1, 1), (3, 1), (1, 7), (40, 4), (120, 6), (60, 8), (33, 9), (25, 12),
           (17, 17), (9, 33)]


@pytest.mark.parametrize('C, R', _SHAPES)
@pytest.mark.parametrize('kind', ['dense', 'sparse', 'tiny'])
@pytest.mark.parametrize('one_way', [False, True])
def test_rows_at_once_equal_the_per_column_loop(C, R, kind, one_way):
    rng = np.random.default_rng(C * 1000 + R * 10 + len(kind))
    a = et.AisleGeom(aisle_id=1, key=('conveyable', 'food', 'medium', 'pallet'), C=C, R=R,
                     x_step=48, y_step=62)
    for scale in (0.05, 1.0, 40.0):
        m = _scene(rng, C, R, kind) * scale
        want = _oracle(m, a, one_way)
        got = et._aisle_routing(m, a, one_way)
        assert got == want, (C, R, kind, one_way, scale, got, want)


def test_the_scenes_reach_the_regimes_that_matter():
    """Non-vacuity: some scene is priced (not the p_visit guard), some column is skipped
    by the uc guard, and some row is longer than numpy's pairwise block (8)."""
    rng = np.random.default_rng(7)
    priced = skipped = 0
    for C, R in _SHAPES:
        m = _scene(rng, C, R, 'sparse')
        v = 1.0 - np.exp(-m)
        u = 1.0 - np.prod(1.0 - v, axis=1)
        priced += (1.0 - np.prod(1.0 - u)) >= 1e-15
        skipped += int((u < 1e-15).sum())
    assert priced and skipped
    assert max(R for _C, R in _SHAPES) > 8
