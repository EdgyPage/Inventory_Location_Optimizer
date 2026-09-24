"""test_plan_order_merge_replay.py — the frontier replay of a merge-only plan is the set
path, bit for bit.

`Inbound.gain.plan_order` runs a merge-only plan (`tmin`/`tmax`, and any pool family
priced with `force_merge`) through `_plan_order_replay`, which reads every exclusion set
as one integer frontier per tier instead of filtering the sorted tier by a set
(placement-sortmatch S01).  The claim is EXACT equality, not closeness: the greedy picks
a winner with `g > best` on raw floats, and exact gain ties are common below the dock
gate, so a one-ulp drift would flip a plan.

What this file pins:

  1. THE ORACLE.  On randomised scenes built to hit every shape the derivation turns on
     -- equal-D ties inside and across the now and predicted tiers, spill chains shared
     by two BinKey groups, two handling families, a forced prefix, `predicted` on and
     off, `minimize` both ways, loads too big for their chain (unseated units), a
     candidate uniquely longest in a tier and two sharing the longest -- the replay
     returns the same order, the same trace records `[seq, now, defer, gain]`, the same
     final `taken` and the same `unseated` count as `plan_order(..., replay=False)`.
  2. IT ENGAGES.  A merge-only plan never calls the set path's `place_load`; a uniform
     or pool bundle never calls the replay.
  3. SABOTAGE.  Each place the derivation could be wrong, planted, is caught by (1):
     the hole handed back to the unique longest reach (`_defer_reach`), the frontier
     offset (`_slot_at`), and `heapq.merge`'s now-tier-first tie rule.

Run:  python -m pytest Tests/unit/test_plan_order_merge_replay.py -q
"""
from __future__ import annotations

import random

import pytest

import Inbound.gain as gain
from Inbound.gain import _Evaluator, plan_order

from Optimization.metrics.Workload import WorkloadParams

from Tests.unit.test_gain_plan import _WP, _Bin, _Order, _Unit, _bundle, _trailer, _view

_FOOD_M = ('conveyable', 'food', 'medium', 'pallet')
_FOOD_L = ('conveyable', 'food', 'large', 'pallet')
_DRY_M = ('conveyable', 'dry', 'medium', 'pallet')
_DRY_L = ('conveyable', 'dry', 'large', 'pallet')


#: Unit paces (1 inch = 1 second both ways), so D = x + y EXACTLY in floats and two bins
#: at different coordinates can share D -- the only kind of D tie that prices
#: differently (put travel and the height multiplier read x and y separately).
_UNIT_WP = WorkloadParams(x_speed=1 / 12, y_speed=1 / 12)


def _scene(seed):
    """A random merge scene: (trailers, view, wp).  Odd seeds use unit paces and draw
    bins on a few equal-D diagonals (x + y from a small set, different coordinates);
    even seeds use the repo paces and draw x from a few columns (equal D only at equal
    coordinates).  Tier sizes are small against the loads, so chains spill and some
    units go unseated."""
    rng = random.Random(seed)
    unit_pace = seed % 2 == 1
    cols = [rng.choice((0.0, 120.0, 240.0, 480.0, 960.0)) for _ in range(3)]
    diagonals = [rng.choice((300.0, 348.0, 450.0)) for _ in range(2)]
    levels = (0.0, 48.0, 150.0, 300.0)

    def bins(n):
        if unit_pace:
            out = []
            for _ in range(n):
                y = rng.choice(levels)
                out.append(_Bin(rng.randrange(4), rng.choice(diagonals) - y, y))
            return out
        return [_Bin(rng.randrange(4), rng.choice(cols), rng.choice(levels))
                for _ in range(n)]

    orders = [_Order(sku=i + 1, freq=rng.choice((0.1, 0.5, 0.5, 1.0)),
                     qty_rate=rng.choice((0.0, 1.0, 3.0, 8.0)),
                     labor=rng.choice((0.5, 1.0, 1.0, 2.5)),
                     hvar=rng.uniform(0.1, 1.0),
                     category=rng.choice(('food', 'food', 'dry')))
              for i in range(7)]
    empties = {_FOOD_M: bins(rng.randint(0, 9)), _FOOD_L: bins(rng.randint(0, 5)),
               _DRY_M: bins(rng.randint(0, 6)), _DRY_L: bins(rng.randint(0, 3))}
    predicted = {_FOOD_M: bins(rng.randint(0, 4)), _DRY_L: bins(rng.randint(0, 3))}
    trailers = []
    for seq in range(rng.randint(2, 6)):
        units = [_Unit(rng.choice(orders), rng.randint(1, 30),
                       size=rng.choice(('medium', 'medium', 'large')))
                 for _ in range(rng.randint(0, 7))]
        trailers.append(_trailer(seq, 100.0 * seq, units))
    return trailers, _view(empties, predicted=predicted), (_UNIT_WP if unit_pace else _WP)


def _run(trailers, view, wp, *, minimize, predicted, prefix, replay):
    ev = _Evaluator(_bundle(minimize=minimize, wp_of=lambda u: wp), view)
    trace: list = []
    order = plan_order(trailers[prefix:], None, view, predicted=predicted,
                       forced_prefix=trailers[:prefix], _ev=ev, trace=trace,
                       replay=replay)
    return [t.seq for t in order], trace, set(ev.taken), ev.unseated


def _cases(n_seeds):
    for seed in range(n_seeds):
        trailers, view, wp = _scene(seed)
        for minimize in (True, False):
            for predicted in (False, True):
                prefix = seed % 3 if len(trailers) > 2 else 0
                yield seed, trailers, view, wp, minimize, predicted, prefix


def _diverged(n_seeds=150):
    """(first diverging case, number of cases compared).  Whether the scenes still hit
    the shapes the derivation turns on is its own test, below."""
    compared = 0
    for seed, trailers, view, wp, minimize, predicted, prefix in _cases(n_seeds):
        kw = dict(minimize=minimize, predicted=predicted, prefix=prefix)
        want = _run(trailers, view, wp, replay=False, **kw)
        got = _run(trailers, view, wp, replay=True, **kw)
        compared += 1
        if want != got:
            return (seed, minimize, predicted, prefix), compared
    return None, compared


# ── 1. the oracle ──────────────────────────────────────────────────────────────────

def test_the_replay_is_the_set_path_bit_for_bit():
    bad, compared = _diverged(n_seeds=150)
    assert bad is None, f'replay diverged from the set path on case {bad}'
    assert compared == 600


def test_the_scenes_exercise_every_shape_the_derivation_turns_on():
    """Non-vacuity for (1): count, over the same scenes, the shapes that decide the
    frontier arithmetic.  Read off the set path, so this cannot share a bug with the
    replay."""
    shapes = dict(unique_longest=0, shared_longest=0, unseated=0, prefix=0,
                  spill=0, tie_now_pred=0)
    for seed, trailers, view, wp, minimize, predicted, prefix in _cases(150):
        order, trace, taken, unseated = _run(
            trailers, view, wp, minimize=minimize, predicted=predicted, prefix=prefix,
            replay=False)
        shapes['unseated'] += unseated > 0
        shapes['prefix'] += prefix > 0
        # Per-candidate reach in one tier, re-derived from a fresh now-placement.
        ev = _Evaluator(_bundle(minimize=minimize, wp_of=lambda u: wp), view)
        reach = []
        for t in trailers[prefix:]:
            _c, tk = ev.place_load([i.unit for i in t.pending], set(), False)
            reach.append(len([b for b in tk if b in view.empties[_FOOD_M]]))
            if any(b in view.empties[_FOOD_L] for b in tk) and any(
                    u.storage_size == 'medium' for u in (i.unit for i in t.pending)):
                shapes['spill'] += 1
        positive = sorted((r for r in reach if r > 0), reverse=True)
        if len(positive) >= 2:
            shapes['unique_longest' if positive[0] > positive[1] else
                   'shared_longest'] += 1
        if wp is _UNIT_WP and predicted:
            now_xy = {(b.x_phys, b.y_phys) for b in view.empties[_FOOD_M]}
            now_d = {b.x_phys + b.y_phys for b in view.empties[_FOOD_M]}
            if any(b.x_phys + b.y_phys in now_d and (b.x_phys, b.y_phys) not in now_xy
                   for b in view.predicted.get(_FOOD_M, ())):
                shapes['tie_now_pred'] += 1
    for name, n in shapes.items():
        assert n >= 10, f'the scenes produced only {n} {name!r} cases'


# ── 2. it engages, and only where it may ──────────────────────────────────────────

def test_a_merge_plan_never_calls_the_set_path(monkeypatch):
    calls = {'set': 0}
    real = _Evaluator.place_load

    def counting(self, *a, **k):
        calls['set'] += 1
        return real(self, *a, **k)

    monkeypatch.setattr(_Evaluator, 'place_load', counting)
    trailers, view, _wp = _scene(3)
    plan_order(trailers, _bundle(), view, predicted=True)
    assert calls['set'] == 0, 'a merge-only plan fell back to the set path'
    plan_order(trailers, _bundle(), view, predicted=True, replay=False)
    assert calls['set'] > 0, 'non-vacuity: the set path does call place_load'


@pytest.mark.parametrize('kw', [dict(uniform=True)])
def test_a_non_merge_bundle_never_replays(monkeypatch, kw):
    monkeypatch.setattr(gain, '_plan_order_replay',
                        lambda *a, **k: pytest.fail('replayed a non-merge plan'))
    trailers, view, _wp = _scene(3)
    plan_order(trailers, _bundle(**kw), view, predicted=True)


def test_a_pool_family_replays_only_when_priced_with_the_merge_rung(monkeypatch):
    """A pool bundle is priced by its own pool -- never replayed -- unless the caller
    asks for the merge rung (`force_merge`, the plan-trace probe's reduction), which
    IS a merge plan and replays, bit for bit."""
    from Tests.unit.test_gain_plan import _frozen_scene
    trailers, view, bundle, _opens = _frozen_scene(7)
    calls = []
    real = gain._plan_order_replay
    monkeypatch.setattr(gain, '_plan_order_replay',
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    plan_order(trailers, bundle, view, predicted=True)
    assert not calls, 'a pool-priced plan must not replay'
    for predicted in (False, True):
        runs = []
        for replay in (True, False):
            trace: list = []
            order = plan_order(trailers, bundle, view, predicted=predicted,
                               force_merge=True, trace=trace, replay=replay)
            runs.append(([t.seq for t in order], trace))
        assert runs[0] == runs[1], f'force_merge replay diverged (predicted={predicted})'
    assert calls, 'non-vacuity: the force_merge plans did replay'


def test_a_predicted_bin_that_is_also_empty_takes_the_set_path(monkeypatch):
    """The one way a predicted bin could sit in an exclusion set -- by also being an
    empty bin of its tier -- is invisible to the frontier form, so the plan declines."""
    monkeypatch.setattr(gain, '_plan_order_replay',
                        lambda *a, **k: pytest.fail('replayed over a shared bin'))
    trailers, view, _wp = _scene(3)
    shared = view.empties[_FOOD_M][:1] or (_Bin(0, 1.0),)
    view2 = _view({**view.empties, _FOOD_M: list(view.empties[_FOOD_M]) + list(shared)},
                  predicted={_FOOD_M: list(shared)})
    plan_order(trailers, _bundle(), view2, predicted=True)


# ── 3. sabotage: each planted error is caught by the oracle ────────────────────────

def test_dropping_the_hole_is_caught(monkeypatch):
    monkeypatch.setattr(gain, '_defer_reach', lambda s, i: s[0])
    bad, _ = _diverged(n_seeds=60)
    assert bad is not None, (
        'handing the unique longest reach NO hole must change some plan, or the '
        'oracle cannot see the leave-one-out rule')


def test_an_off_by_one_frontier_is_caught(monkeypatch):
    real = _Evaluator._slot_at

    def shifted(self, key, start, *a):
        return real(self, key, lambda k: start(k) + 1 if start(k) else 0, *a)

    monkeypatch.setattr(_Evaluator, '_slot_at', shifted)
    bad, _ = _diverged(n_seeds=60)
    assert bad is not None, 'an off-by-one frontier must change some plan'


def test_the_now_tier_first_tie_rule_is_caught(monkeypatch):
    """`heapq.merge` gives equal D to the NOW tier first.  Swap the iterables (the
    predicted tier first) and some plan must move -- the scenes plant equal D across
    the two tiers on purpose."""
    real_merge = gain._hmerge

    def swapped(a, b, **k):
        return real_merge(b, a, **k)

    monkeypatch.setattr(gain, '_hmerge', swapped)
    bad = None
    for seed, trailers, view, wp, minimize, predicted, prefix in _cases(150):
        if not predicted:
            continue
        kw = dict(minimize=minimize, predicted=True, prefix=prefix)
        monkeypatch.setattr(gain, '_hmerge', real_merge)
        want = _run(trailers, view, wp, replay=False, **kw)
        monkeypatch.setattr(gain, '_hmerge', swapped)
        got = _run(trailers, view, wp, replay=True, **kw)
        if want != got:
            bad = seed
            break
    assert bad is not None, 'swapping the merge tie rule must change some plan'
