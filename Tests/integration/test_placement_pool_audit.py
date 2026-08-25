"""test_placement_pool_audit.py — every shipped restock rule, classified.

The audit that closes Phase 1. Before the pool inversion, an assignment function returned
`[(unit, bin)] in priority order`, so 14 of the 17 restock rules were choosing WHO gets
placed first as well as WHERE — put-away that is supposed to be FIFO-with-tolerance, freely
re-sorted by whatever score maximised the assignment function.

This file is the ratchet on the result. It builds every shipped arm and asserts the
classification, so a rule that quietly falls back to the legacy `place_wave` path — where
the order is the function's again and no score is reportable — fails here rather than
producing a plausible number nobody can tell is wrong.

It also pins the two properties the pooled path depends on and nothing else checks:
`prefers_low` is a real declaration on every pool (not the inherited default sitting on a
maximising arm), and `open_pool` really does answer one unit at a time from one snapshot.

Run:  python -m pytest Tests/integration/test_placement_pool_audit.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402

# The classification as of the Phase-1 audit. Moving a rule between these lists is a
# behaviour change and must be a deliberate edit here, with the reason in the commit.
POOLED = {
    'cluster_map', 'cluster_map_rank', 'compaction', 'expansion', 'optmap', 'optmap_rank',
    'ranked_cartlabor', 'ranked_labor', 'ranked_maxlabor', 'ranked_minlabor',
    'ranked_popularity', 'ranked_uniform', 'ranked_max', 'ranked_min',
}
#: No group path at all — these three already drained FIFO and are why "the assignment
#: functions choose the order" was 14 of 17 rather than all 17.
PER_UNIT = {'cohesion_max', 'cohesion_min', 'uniform_fifo'}

#: The arms that optimise UPWARD. `prefers_low` exists so `bin_placement.score_rank` means
#: "best available" rather than "smallest number", and this is the list it must produce.
MAXIMISERS = {'ranked_max', 'ranked_maxlabor', 'expansion'}

ARMS = sorted(k for k in cs.STRATEGY_BY_KEY if k.startswith('uni_'))


@pytest.fixture(scope='module')
def built():
    """One manager per shipped arm. Small — this asks about wiring, not behaviour."""
    out = {}
    for key in ARMS:
        a = cs.build_assets(n_skus=120, bins_per_aisle=20, strategy=key, seed=1,
                            coverage=2.0, safety=0.4)
        out[key] = a
    return out


def _classify(p):
    return 'pooled' if p.is_pooled else 'wave' if p.place_wave is not None else 'per-unit'


def test_no_shipped_rule_still_returns_its_own_order(built):
    """The legacy `place_wave` path must be unreachable in production. It survives only to
    drive the frozen oracles in the equivalence suites."""
    waves = {k: a.mgr.placement.name for k, a in built.items()
             if _classify(a.mgr.placement) == 'wave'}
    assert not waves, f'these rules still hand the drain a pre-ordered wave: {waves}'


def test_the_classification_is_exactly_as_audited(built):
    got_pooled = {a.mgr.placement.name for a in built.values()
                  if _classify(a.mgr.placement) == 'pooled'}
    got_unit = {a.mgr.placement.name for a in built.values()
                if _classify(a.mgr.placement) == 'per-unit'}
    assert got_pooled == POOLED, (f'pooled set moved: +{got_pooled - POOLED} '
                                  f'-{POOLED - got_pooled}')
    assert got_unit == PER_UNIT, (f'per-unit set moved: +{got_unit - PER_UNIT} '
                                  f'-{PER_UNIT - got_unit}')
    assert len(built) == len(POOLED) + len(PER_UNIT), 'an arm is unaccounted for'


def test_every_pool_declares_its_direction(built):
    """A maximising arm that inherited `prefers_low = True` would invert every one of its
    `score_rank` values, silently, with no error anywhere."""
    seen_max = set()
    for a in built.values():
        p = a.mgr.placement
        if not p.is_pooled:
            continue
        pool = p.open_pool(list(a.warehouse.bins)[:40], _any_unit(a))
        assert isinstance(pool.prefers_low, bool), p.name
        if not pool.prefers_low:
            seen_max.add(p.name)
    assert seen_max == MAXIMISERS, f'direction declarations moved: {seen_max}'


def _any_unit(assets):
    from Warehouse.layout.Storage_Primitive import viable_storage_units
    for o in assets.inventory.orders:
        us = viable_storage_units(o, 1)
        if us:
            return us[0]
    raise AssertionError('the catalogue produced no storage unit')


def test_a_pool_consumes_its_snapshot_and_never_reissues_a_bin(built):
    """The property every pool must have regardless of policy: one snapshot in, distinct
    bins out, `(None, None)` once drained. Order-independent, so it survives the FIFO
    window that Phase 2 introduces."""
    for key, a in built.items():
        p = a.mgr.placement
        if not p.is_pooled:
            continue
        unit = _any_unit(a)
        cands = list(a.mgr._candidates(unit))
        if len(cands) < 3:
            continue
        cands = cands[:12]
        pool = p.open_pool(list(cands), unit)
        seen, n = set(), 0
        for _ in range(len(cands)):
            b, _score = pool.take(unit)
            if b is None:
                break
            assert id(b) in {id(c) for c in cands}, f'{key}: invented a bin'
            assert id(b) not in seen, f'{key}: handed out the same bin twice'
            seen.add(id(b))
            n += 1
        assert n > 0, f'{key}: the pool placed nothing from {len(cands)} candidates'
        for _ in range(len(cands) - n + 1):
            pool.take(unit)
        assert pool.take(unit) == (None, None), f'{key}: a drained pool still answers'


def test_the_drain_asks_the_pool_rather_than_obeying_it(built):
    """`_serve_order` is the single place the serve order is decided. It grants the whole
    request today — that is what keeps this commit byte-identical — but it is a call the
    DRAIN makes, and a policy cannot bypass it."""
    from Warehouse.inventory.Inventory_Management import Inventory_Manager
    a = built['uni_rank_labor_norsl']
    unit = _any_unit(a)
    cands = list(a.mgr._candidates(unit))[:20]
    pool = a.mgr.placement.open_pool(list(cands), unit)
    units = [unit] * 3
    assert Inventory_Manager._serve_order(a.mgr, pool, units) == pool.order(units)

    class _Contrary:
        prefers_low = True

        def order(self, units):
            return list(reversed(units))

        def take(self, unit):
            return None, None

    # The drain routes THROUGH the method, so overriding it overrides the policy.
    got = Inventory_Manager._serve_order(a.mgr, _Contrary(), [1, 2, 3])
    assert got == [3, 2, 1], 'the drain is not consulting the pool at all'
