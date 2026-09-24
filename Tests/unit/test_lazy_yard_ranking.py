"""test_lazy_yard_ranking.py — the yard's pull queue is the eager plan's prefix, and only
pays for what it pulls.

`Inbound.gain.plan_order_iter` yields the greedy's winners one round at a time, and a drain
reads the yard ranking through `YardTransit.yard_ranking`: a `priorities.LazyRanking` over
that generator for a gain entry, a deque over the old list for everything else.  A drain
stages its free doors plus the refills its unload reaches, so the rounds nobody pulls are
never priced -- 5x fewer yard placements on the meso deep rung, 16x under the asap fill
(`.scratch/inbound-fullscale-perf/` O1).

What this file pins:

  1. PREFIX IDENTITY: for every prefix length p, the first p trailers the generator yields
     are the first p of `plan_order`'s list -- on the merge replay path, the set path and a
     pool family, with and without a forced prefix.  Non-vacuous: the scenes' eager orders
     differ from arrival order.
  2. IT IS LAZY: one pull prices one round (2T placements), not T rounds.
  3. THE SEAM: a gain policy's `yard_ranking` is a `LazyRanking` whose pulls equal
     `yard_order`; a key policy's is a deque of the same list; a traced drain stays eager.
  4. THE PERMUTATION CHECK SURVIVES THE MOVE TO THE PULL: a foreign, duplicated, missing or
     extra trailer raises at the pull that exposes it; the bound's remainder follows in
     arrival order; `len` counts what is still to come.

Run:  python -m pytest Tests/unit/test_lazy_yard_ranking.py -q
"""
from __future__ import annotations

from collections import deque
from itertools import islice

import pytest

import Inbound.gain as gain
from Inbound.priorities import LazyRanking, bounded_order, ordering
from Inbound.transit import YardTransit
from Warehouse.kernel import perf_probe

from Tests.unit.test_gain_plan import _bundle, _drain_pair, _frozen_scene
from Tests.unit.test_plan_order_merge_replay import _scene


def _plans(n_seeds=40):
    """(label, trailers, bundle, view, predicted, prefix, replay) over three plan paths."""
    for seed in range(n_seeds):
        trailers, view, wp = _scene(seed)
        prefix = seed % 3 if len(trailers) > 2 else 0
        for predicted in (False, True):
            for replay in (True, False):
                yield (f'merge s{seed} p{predicted} r{replay}', trailers,
                       _bundle(wp_of=lambda u, _wp=wp: _wp), view, predicted, prefix, replay)
    for seed in range(6):
        trailers, view, bundle, _opens = _frozen_scene(seed)
        for predicted in (False, True):
            yield (f'pool s{seed} p{predicted}', trailers, bundle, view, predicted, 0, True)


def _kw(trailers, view, predicted, prefix, replay):
    return dict(predicted=predicted, forced_prefix=trailers[:prefix], replay=replay)


# ── 1. prefix identity ─────────────────────────────────────────────────────────────

def test_every_prefix_of_the_generator_is_the_eager_plans_prefix():
    compared = reordered = 0
    for label, trailers, bundle, view, predicted, prefix, replay in _plans():
        kw = _kw(trailers, view, predicted, prefix, replay)
        rest = trailers[prefix:]
        want = [t.seq for t in gain.plan_order(rest, bundle, view, **kw)]
        reordered += want != [t.seq for t in trailers]
        for p in range(len(want) + 1):
            got = [t.seq for t in islice(gain.plan_order_iter(rest, bundle, view, **kw), p)]
            assert got == want[:p], f'{label}: pulled {got}, eager prefix {want[:p]}'
            compared += 1
    assert compared > 400
    assert reordered > 20, (
        f'only {reordered} scenes rank off arrival order -- a generator that yielded the '
        f'input as given would pass the prefix check')


# ── 2. laziness ────────────────────────────────────────────────────────────────────

def test_one_pull_prices_one_round():
    trailers, view, bundle, _opens = _frozen_scene(3)
    T = len(trailers)
    assert T >= 3, 'the scene must hold a real choice'
    perf_probe.drain()
    it = gain.plan_order_iter(trailers, bundle, view, predicted=True)
    next(it)
    _s, one = perf_probe.drain()
    gain.plan_order(trailers, bundle, view, predicted=True)
    _s, full = perf_probe.drain()
    assert one == {'plan_rounds': 1, 'plan_places': 2 * T}
    assert full['plan_rounds'] == T
    assert full['plan_places'] == T * (T + 1)


# ── 3. the seam ────────────────────────────────────────────────────────────────────

def test_a_gain_yard_ranking_is_a_lazy_queue_over_the_same_order():
    tr, ctx, _pair = _drain_pair('gain_forecast')
    want = tr.yard_order(ctx)
    q = tr.yard_ranking(ctx)
    assert isinstance(q, LazyRanking)
    assert len(q) == len(want) and bool(q)
    got = [q.popleft() for _ in range(len(want))]
    assert got == want and len(q) == 0 and not q
    with pytest.raises(IndexError):
        q.popleft()


def test_a_key_policy_and_a_traced_drain_stay_eager():
    tr, ctx, _pair = _drain_pair('gain_forecast')
    fifo = YardTransit(doors=2, yard_policy='fifo', dock_policy='fifo')
    fifo._yard.extend(tr._yard)
    fctx = fifo.freeze_ctx()
    q = fifo.yard_ranking(fctx)
    assert isinstance(q, deque) and list(q) == fifo.yard_order(fctx)
    ctx.plan_trace = []
    q = tr.yard_ranking(ctx)
    assert isinstance(q, deque), 'a traced drain records every round, so it cannot be lazy'
    assert len(ctx.plan_trace) == 1


# ── 4. the permutation check, per pull ─────────────────────────────────────────────

class _T:
    def __init__(self, seq):
        self.seq = seq


def _entry(make):
    @ordering
    def entry(candidates, ctx, lazy=False):
        out = make(candidates)
        return out if lazy else list(out)
    entry.LAZY = True
    return entry


@pytest.mark.parametrize('make, pulls, match', [
    (lambda c: iter([_T(99)] + c[1:]), 1, 'foreign'),
    (lambda c: iter([c[0], c[0], c[2]]), 2, 'duplicated'),
    (lambda c: iter(c[:2]), 3, 'stopped after 2 of 3'),
    (lambda c: iter(c + [_T(98)]), 3, 'more than the 3'),
])
def test_a_bad_generator_raises_at_the_pull_that_exposes_it(make, pulls, match):
    cands = [_T(i) for i in range(3)]
    q = bounded_order(cands, _entry(make), None, None, lazy=True)
    assert isinstance(q, LazyRanking)
    for _ in range(pulls - 1):
        q.popleft()
    with pytest.raises(ValueError, match=match):
        q.popleft()


def test_the_bounds_remainder_follows_in_arrival_order():
    cands = [_T(i) for i in range(5)]
    q = bounded_order(cands, _entry(lambda c: iter(reversed(c))), None, 2, lazy=True)
    assert len(q) == 5
    got = [q.popleft().seq for _ in range(5)]
    assert got == [1, 0, 2, 3, 4]
    assert got == [t.seq for t in bounded_order(
        cands, _entry(lambda c: iter(reversed(c))), None, 2)]
