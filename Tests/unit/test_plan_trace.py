"""test_plan_trace.py -- the plan-trace probe records the exact plan and changes nothing.

The probe (`.scratch/inbound-throughput/issues/03`) prices two candidate reductions of the
gain evaluator beside the exact plan on the same frozen drain -- the merge rung forced onto
a pool family, and the top-m lazy plan -- and writes what it saw to a site-scope sidecar.
Its whole value rests on four claims, each of which could fail silently:

  1. INERT: with no sink, an entry calls exactly the `plan_order` it called before the
     probe existed, and computes no reduction; with a sink, the order the entry RETURNS is
     still the exact plan's, so a traced cell ranks byte-identically.
  2. FAITHFUL: the recorded exact order is the returned one, every round's recorded winner
     is that round's argmax, and the round sequence reproduces the order.
  3. THE REDUCTIONS ARE WHAT THEY CLAIM: top-m with m at least the candidate count IS the
     exact plan (so its only approximation is staleness), and its placement count is the
     formula the ticket prices; the merge rung really bypasses the pool.
  4. THE DRIVER WRITES WHAT IT ARMS: a site dock arms the transit's sink on traced batches
     only, and flushes one JSON line per plan tagged with batch and arm pair.

The scene is the labour-family one (`test_gain_bundle_labor_families.py`): real
`rank_cartlabor` / `rank_minlabor` bundles built by the driver's own `_gain_bundle_for`, so
the pool families the phase-2 winner pair runs are the ones under test.

Run:  python -m pytest Tests/unit/test_plan_trace.py -q
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import Inbound.gain as gain
from Inbound.gain import OneOwnerBundle, plan_order, plan_order_topm
from Inbound.priorities import DockContext
from Tests.unit.test_gain_bundle_labor_families import _bundle_for, _ctx, _mgr, _scene
from Tests.unit.test_gain_plan import _bundle


FAMILIES = ('rank_cartlabor', 'rank_minlabor')


def _setup(family, seed=0, n_trailers=6):
    orders, aff, _bins, trailers, view = _scene(seed=seed, n_trailers=n_trailers)
    bundle = OneOwnerBundle(_bundle_for(family, _mgr(orders, aff), _ctx(orders, aff)))
    ctx = DockContext(doors=4, free_doors=4, yard_depth=len(trailers))
    ctx.space, ctx.gain, ctx.gain_cache = view, bundle, {}
    return trailers, bundle, view, ctx


def _seqs(ts):
    return [t.seq for t in ts]


# ── 1. inert ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('entry', ['gain_myopic', 'gain_forecast'])
def test_without_a_sink_the_entry_is_the_plan_it_always_was(family, entry, monkeypatch):
    trailers, bundle, view, ctx = _setup(family)
    want = plan_order(trailers, bundle, view, predicted=(entry == 'gain_forecast'))
    called = []
    monkeypatch.setattr(gain, 'plan_order_topm',
                        lambda *a, **k: called.append(1) or (list(a[0]), 0))
    got = getattr(gain, entry)(list(trailers), ctx)
    assert _seqs(got) == _seqs(want)
    assert not called, 'a reduction ran with no sink armed -- the probe is not inert'
    assert ctx.plan_trace is None


@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('seed', [0, 1, 2])
def test_with_a_sink_the_returned_order_is_still_the_exact_plan(family, seed):
    trailers, bundle, view, ctx = _setup(family, seed=seed)
    want = plan_order(trailers, bundle, view, predicted=True)
    ctx.plan_trace, ctx.ranking = [], 'yard'
    got = gain.gain_forecast(list(trailers), ctx)
    assert _seqs(got) == _seqs(want)
    assert len(ctx.plan_trace) == 1


# ── 2. faithful ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('family', FAMILIES)
def test_the_record_reproduces_the_exact_plan_round_by_round(family):
    trailers, bundle, view, ctx = _setup(family, seed=1)
    ctx.plan_trace, ctx.ranking = [], 'dock'
    got = gain.gain_myopic(list(trailers), ctx)
    rec = ctx.plan_trace[0]
    assert rec['entry'] == 'gain_myopic' and rec['ranking'] == 'dock'
    assert rec['predicted'] is False and rec['T'] == len(trailers) and rec['pool'] is True
    assert rec['exact'] == _seqs(got)
    assert [r['winner'] for r in rec['rounds']] == rec['exact']
    for i, r in enumerate(rec['rounds']):
        assert len(r['cands']) == len(trailers) - i
        best = max(g for _s, _n, _d, g in r['cands'])
        # the FIRST candidate at the max wins (the strict `>`): arrival order breaks ties
        first_best = next(s for s, _n, _d, g in r['cands'] if g == best)
        assert r['winner'] == first_best
        for _s, now, defer, g in r['cands']:
            assert g == defer - now
    n = len(trailers)
    assert rec['calls']['exact'] == n * (n + 1)
    assert sorted(rec['merge']) == sorted(rec['exact'])
    for m, order in rec['topm'].items():
        assert int(m) < n and sorted(order) == sorted(rec['exact'])
        assert rec['calls'][f'top{m}'] < rec['calls']['exact']
    assert set(rec['wall_s']) >= {'exact', 'merge'}
    json.dumps(rec)                      # the driver writes it as one JSON line


# ── 3. the reductions ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('family', FAMILIES)
@pytest.mark.parametrize('predicted', [False, True])
def test_top_m_at_least_the_candidate_count_is_the_exact_plan(family, predicted):
    trailers, bundle, view, _ctx_ = _setup(family, seed=2)
    n = len(trailers)
    want = plan_order(trailers, bundle, view, predicted=predicted)
    got, calls = plan_order_topm(trailers, bundle, view, predicted=predicted, m=n)
    assert _seqs(got) == _seqs(want)
    assert calls == n * (n + 1)


def test_top_m_costs_two_t_then_two_m_a_round():
    trailers, bundle, view, _c = _setup('rank_cartlabor', seed=0, n_trailers=6)
    _got, calls = plan_order_topm(trailers, bundle, view, predicted=False, m=2)
    # round one prices all 6 twice; rounds with 5, 4, 3, 2 remaining price 2 twice; the
    # last round prices its 1 remaining twice
    assert calls == 2 * 6 + 2 * 2 * 4 + 2 * 1
    with pytest.raises(ValueError, match='m >= 1'):
        plan_order_topm(trailers, bundle, view, predicted=False, m=0)


def test_the_merge_rung_bypasses_the_pool(monkeypatch):
    trailers, bundle, view, _c = _setup('rank_cartlabor', seed=0)

    def boom(*a, **k):
        raise AssertionError('the pool adapter ran under force_merge')
    monkeypatch.setattr(gain._Evaluator, '_place_pool', boom)
    got = plan_order(trailers, bundle, view, predicted=False, force_merge=True)
    assert sorted(_seqs(got)) == sorted(_seqs(trailers))
    with pytest.raises(AssertionError, match='pool adapter ran'):
        plan_order(trailers, bundle, view, predicted=False)


def test_bundle_uses_pool_tells_a_pool_family_from_the_merge_one():
    _t, pooled, _v, _c = _setup('rank_cartlabor')
    assert gain.bundle_uses_pool(pooled) is True
    assert gain.bundle_uses_pool(_bundle()) is False            # extremal-D: merge IS it
    assert gain.bundle_uses_pool(SimpleNamespace()) is None


# ── 4. the driver ────────────────────────────────────────────────────────────────

def _bare_site_dock(tmp_path):
    from Optimization.simdriver.strategy_runner import _SiteDock
    sd = _SiteDock.__new__(_SiteDock)
    sd.arm_pair = 'a__b'
    sd.transit = SimpleNamespace(plan_trace=None)
    sd._trace = None
    armed = []
    sd.release = SimpleNamespace(day_of=lambda i: i)
    sd.coord = SimpleNamespace(open_batch=lambda day: (0.0, 1.0),
                               drain=lambda leaves, **k: armed.append(sd.transit.plan_trace)
                               or {},
                               leaves=())
    pool = SimpleNamespace(open_batch=lambda day: (0.0, 1.0))
    return sd, pool, armed


def test_the_site_dock_arms_traced_batches_only_and_writes_one_line_per_plan(tmp_path):
    sd, pool, armed = _bare_site_dock(tmp_path)
    path = str(tmp_path / '_site' / 'plan_trace_a__b.jsonl')
    sd.arm_trace(path, 3)
    for i in range(7):
        sd.drive(i, pool)
        if sd.transit.plan_trace is not None:
            sd.transit.plan_trace.append({'entry': 'gain_myopic', 'exact': [i]})
        sd.flush_trace(i)
    assert [a is not None for a in armed] == [True, False, False, True, False, False, True]
    lines = [json.loads(ln) for ln in open(path, encoding='utf-8')]
    assert [ln['batch'] for ln in lines] == [0, 3, 6]
    assert all(ln['arm_pair'] == 'a__b' and ln['exact'] == [ln['batch']] for ln in lines)
    # re-arming (a retried unit replays from batch 0) truncates rather than appending
    sd.arm_trace(path, 3)
    assert open(path, encoding='utf-8').read() == ''
    with pytest.raises(ValueError, match='positive cadence'):
        sd.arm_trace(path, 0)


def test_an_unarmed_site_dock_never_hands_a_drain_a_sink(tmp_path):
    sd, pool, armed = _bare_site_dock(tmp_path)
    for i in range(3):
        sd.drive(i, pool)
        sd.flush_trace(i)
    assert armed == [None, None, None]


def test_the_transit_hands_its_sink_to_the_drain_and_labels_the_ranking():
    from Inbound.transit import YardTransit
    from Inbound.trailer import Trailer53
    tr = YardTransit(Trailer53, yard_policy='fifo', dock_policy='fifo')
    tr.gain_bundle = object()
    ctx = tr.freeze_ctx()
    assert ctx.plan_trace is None
    tr.yard_order(ctx)
    assert ctx.ranking is None                     # unarmed: not even the label is written
    sink = []
    tr.plan_trace = sink
    ctx = tr.freeze_ctx()
    assert ctx.plan_trace is sink
    tr.yard_order(ctx)
    assert ctx.ranking == 'yard'
    tr.dock_order(ctx)
    assert ctx.ranking == 'dock'


def test_the_spec_refuses_a_cadence_that_is_not_a_positive_count():
    from Optimization.config.sim_config import _plan_trace_every
    assert _plan_trace_every(None) is None and _plan_trace_every(4) == 4
    for bad in (0, -1, 2.5, True, '4'):
        with pytest.raises(ValueError, match='INBOUND_PLAN_TRACE'):
            _plan_trace_every(bad)
