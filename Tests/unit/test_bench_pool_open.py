"""test_bench_pool_open.py -- the pool-open bench still measures what it says it measures.

`Tests/bench/bench_pool_open.py` is the instrument that prices one gain-evaluator pool open
in seconds (`.scratch/inbound-throughput/issues/02`).  Instruments outside a gate have
rotted three times in this repo (CLAUDE.md section 1; memory
`hand-run-test-tiers-rot-silently`), so this smoke form sits in the gate and asserts only
NON-VACUITY at the small shape -- never a timing:

  * the eager and the template opens emit the SAME sequence, per family -- the bench raises
    otherwise, and this pins that it does;
  * at least one unit is seated per family, so the equality is not two empty lists;
  * the prologue split's walk replica finds the same aisles in the same order as the real
    `TierSlice.aisle_buckets()`, so the (a)/(b) split cannot drift from the code it splits;
  * the replica weight is a positive number of bytes that grows with the tier.

Run:  python -m pytest Tests/unit/test_bench_pool_open.py -q
"""
from __future__ import annotations

import pytest

import bench_pool_open as bpo


@pytest.fixture(scope='module')
def scene():
    return bpo.build_scene(bpo.SMALL, seed=3)


def test_the_walk_replica_matches_the_real_prologue(scene):
    sl = scene.tier.slice(scene.excluded)
    assert bpo.walk_only(sl) == list(sl.aisle_buckets())
    assert len(bpo.walk_only(sl)) == scene.shape.aisles     # every aisle survives 10% taken


@pytest.mark.parametrize('family', bpo.FAMILIES)
def test_eager_and_template_opens_seat_the_same_units(scene, family):
    eager = bpo.seat(bpo.open_pool(scene, family, scene.tier.slice(scene.excluded)),
                     scene.units)
    store: dict = {}
    first = bpo.seat(bpo.open_pool(scene, family, scene.tier.slice(scene.excluded, store)),
                     scene.units)
    second = bpo.seat(bpo.open_pool(scene, family, scene.tier.slice(scene.excluded, store)),
                      scene.units)
    assert eager == first == second
    assert any(x is not None for x in eager), f'{family} seated nothing -- vacuous'
    assert store, 'the template store was never used -- the template path went untimed'


def test_measure_reports_every_timing_and_refuses_a_divergent_sequence(scene, monkeypatch):
    m = bpo.measure(scene, repeats=2)
    assert m['bins'] == scene.shape.bins and m['live_aisles'] == scene.shape.aisles
    assert m['prologue_s'] > 0 and m['prologue_walk_s'] > 0
    for fam in bpo.FAMILIES:
        r = m['families'][fam]
        assert r['seated'] >= 1 and r['eager_open_s'] >= 0 and r['template_open_s'] >= 0
    # a template path that emitted something else must raise, not time it
    real_seat = bpo.seat
    calls = {'n': 0}

    def skewed(pool, units):
        calls['n'] += 1
        out = real_seat(pool, units)
        return out if calls['n'] % 2 else out[::-1] + [None]
    monkeypatch.setattr(bpo, 'seat', skewed)
    with pytest.raises(AssertionError, match='different sequence'):
        bpo.measure(scene, repeats=1)


def test_the_replica_weight_is_positive_and_grows_with_the_tier():
    small = bpo.replica_bytes(bpo.SMALL, seed=3)
    bigger = bpo.replica_bytes(bpo.Shape(aisles=48, bins_per_bracket=2, units=6,
                                         catalogue=60), seed=3)
    assert small['bytes'] > 0 and small['bins'] == bpo.SMALL.bins
    assert bigger['bytes'] > small['bytes']
