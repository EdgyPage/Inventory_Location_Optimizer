"""test_pool_drain_memo.py — the gain evaluator's drain-scoped pool memo moves no placement.

A 400k `place_load` places a whole trailer (~1,000 units) and a load is priced ~2T times a
drain, each time in fresh pools over fresh copy-on-write views.  Since S10 of
`.scratch/inbound-fullscale-perf/` the evaluator hands every view a DRAIN-SCOPED memo per
owner (`_Evaluator._make_pool`, riding `_SHARED_CACHES['_pool_memo']`), and the min-labour
pool reads it for two pure functions of the LIVE books: the partner centroid per (SKU,
aisle) -- only for an aisle its own view has not written -- and the live half of the
per-SKU partner fold (`_partner_deltas`).  The fold's OVERLAY half refolds only the aisles
whose partners differ from the live book's; every other overlaid aisle takes its live
value, which the section at the end holds against the full refold.

Two levels:

  POOL LEVEL (the discriminating one).  What the evaluator does, without the evaluator:
  real ledger books (so the inverse exists and the fold's memo path runs), several VIRTUAL
  opens over the same live books sharing one memo, nothing committed, and unit sequences
  that place a SKU's partners earlier in the same open (so the views' own writes matter).
  Against the same opens with the memo withheld: takes, scores and each open's overlay
  are IDENTICAL.  Non-vacuity: the memo served centroids and folds.  Sabotage, both
  halves: reading the memo for a WRITTEN aisle, and dropping the overlay patch from the
  fold, each diverge on some scene.

  RUN LEVEL.  The meso inbound scenario the copy-on-write equivalence uses digests
  identically with the memo attached and withheld.  (Too small to discriminate the
  sabotages -- which is why the pool level exists.)

Run:  python -m pytest Tests/unit/test_pool_drain_memo.py -q
"""
from __future__ import annotations

import random

import pytest

import Inbound.gain as gain
from Inbound import gain_cow
from Warehouse.inventory.aisle_ledger import AisleLedger
from Warehouse.placement import Assignment_Functions as af

from Tests.unit.test_gain_cow_equivalence import _digest
from Tests.unit.test_minlabor_pool_equivalence import _LAM, _Unit, _aff, _fixture, _wp

_BOOKS = ('aisle_sku_sets', 'aisle_idx_sets', 'aisle_demand_sum', 'aisle_member_pos')


def _scene(seed: int):
    """Bins and orders from the minlabor fixture; live books seeded through a REAL ledger;
    three unit sequences in which partners (1-2, 2-3, 3-4, 4-5, 1-99) follow each other."""
    rng = random.Random(7000 + seed)
    bins, _units, _aff0, idx, fbi, fbs, qbs = _fixture(rng, n_aisles=4, n_units=4)
    orders = {}
    for u in _units:
        orders[u.order.sku] = u.order
    skus = sorted(idx)
    aff, idx = _aff(skus, [(1, 2, 4.0), (1, 99, 6.0), (2, 3, 2.5), (3, 4, 3.0),
                           (4, 5, 2.0), (2, 2, 3.5)])
    led = AisleLedger()
    for aid in range(1, 5):
        for s in rng.sample([k for k in skus if k != 99], 2):
            led.add_sku(aid, s, idx[s], demand=0.3)
            led.add_bin(aid, idx[s], rng.choice((0.0, 20.0, 40.0, 150.0)))
    pool_orders = sorted(orders.values(), key=lambda o: o.sku)
    opens = []
    for _ in range(3):
        seq = []
        for o in rng.sample(pool_orders, len(pool_orders)):
            seq += [_Unit(o) for _ in range(rng.randint(1, 3))]
        opens.append(seq)
    return bins, opens, aff, idx, fbi, fbs, qbs, led


def _drive(scene, maximize: bool, memo: dict | None):
    bins, opens, aff, _idx, fbi, fbs, qbs, led = scene
    live = {'aisle_sku_sets': led.sku_sets, 'aisle_idx_sets': led.idx_sets,
            'aisle_demand_sum': led.demand_sum, 'aisle_member_pos': led.member_pos}
    out = []
    for units in opens:
        views = {n: gain_cow.AISLE_VIEWS[n](live[n]) for n in _BOOKS}
        if memo is not None:
            for v in views.values():
                v.memo = memo
        pool = af._MinLaborPool(list(bins), aff, _wp(), views['aisle_sku_sets'],
                                views['aisle_idx_sets'], views['aisle_demand_sum'],
                                views['aisle_member_pos'], fbi, fbs, qbs, _LAM,
                                maximize=maximize)
        takes = []
        for u in pool.order(list(units)):
            b, score = pool.take(u)
            takes.append((u.order.sku, None if b is None else
                          (b.location[0], b.x_phys, b.y_phys), score))
        mp = views['aisle_member_pos']
        overlay = {a: {k: list(v) for k, v in inner._over.items()}
                   for a, inner in mp._over.items() if inner._over}
        out.append((takes, overlay))
    return out


def _snapshot(led):
    return ({a: sorted(v) for a, v in led.sku_sets.items()},
            {a: {k: list(xs) for k, xs in d.items()} for a, d in led.member_pos.items()})


@pytest.mark.parametrize('maximize', [False, True])
@pytest.mark.parametrize('seed', range(12))
def test_memoised_opens_decide_what_unmemoised_opens_decide(maximize, seed):
    scene = _scene(seed)
    before = _snapshot(scene[-1])
    ref = _drive(scene, maximize, memo=None)
    got = _drive(scene, maximize, memo={})
    assert got == ref, f'seed {seed} maximize={maximize}: the drain memo moved a take'
    assert _snapshot(scene[-1]) == before, 'a virtual open wrote the live books'


def test_the_memo_serves_centroids_and_folds(monkeypatch):
    counts = {'centroid': 0, 'fold_hit': 0}
    real_c = af._demand_weighted_partner_centroid

    def centroid(*a, **k):
        counts['centroid'] += 1
        return real_c(*a, **k)

    monkeypatch.setattr(af, '_demand_weighted_partner_centroid', centroid)
    base = 0
    for seed in range(12):
        scene = _scene(seed)
        _drive(scene, False, memo=None)
    base, counts['centroid'] = counts['centroid'], 0
    memos = []
    for seed in range(12):
        memo: dict = {}
        _drive(_scene(seed), False, memo=memo)
        memos.append(memo)
    assert counts['centroid'] < base, (counts, base)
    assert any(k[0] == 'dl' for m in memos for k in m), 'the fold never used the memo'


def _diverges(sabotage, monkeypatch) -> int:
    n = 0
    for seed in range(12):
        for maximize in (False, True):
            ref = _drive(_scene(seed), maximize, memo=None)
            with monkeypatch.context() as mp:
                sabotage(mp)
                got = _drive(_scene(seed), maximize, memo={})
            n += got != ref
    return n


def test_a_memo_read_for_a_written_aisle_is_caught(monkeypatch):
    def sab(mp):
        mp.setattr(gain_cow._CowListsByKey, 'untouched', lambda self, k: True)
    assert _diverges(sab, monkeypatch), 'a stale centroid passed every scene -- vacuous'


def test_a_fold_that_skips_the_overlay_is_caught(monkeypatch):
    real = af._MinLaborPool._partner_deltas

    def skip_overlay(self, row_items, sku=None):
        ais = self._ais
        saved = ais._over
        ais._over = {}
        try:
            return real(self, row_items, sku)
        finally:
            ais._over = saved

    def sab(mp):
        mp.setattr(af._MinLaborPool, '_partner_deltas', skip_overlay)
    assert _diverges(sab, monkeypatch), 'a fold without the overlay passed -- vacuous'


# ── run level ─────────────────────────────────────────────────────────────────────

class _NotAView:
    """Stands in for `_CowView` in the evaluator's isinstance test: no view gets the memo."""


def test_the_drain_memo_changes_no_run_end_state(monkeypatch):
    on, on_res = _digest('uni_rank_minlabor_norsl')
    with monkeypatch.context() as mp:
        mp.setattr(gain._cow, '_CowView', _NotAView)
        off, _ = _digest('uni_rank_minlabor_norsl')
    assert on_res.placements > 0, 'the scenario placed nothing -- vacuous'
    assert on == off, 'the drain memo moved the run end state'


# ── the overlay shortcut in `_partner_deltas` ─────────────────────────────────────

def _full_overlay_deltas(self, row_items, sku=None):
    """`_partner_deltas` before the overlay shortcut: EVERY overlaid aisle refolded."""
    ais = self._ais
    inv = getattr(ais, 'inverse', None)
    over = None
    if inv is None:
        live = getattr(ais, '_live', None)
        inv = getattr(live, 'inverse', None)
        over = getattr(ais, '_over', None)
    if inv is None:
        return None
    deltas: dict = {}
    for ci, w in row_items:
        held = inv.get(ci)
        if held:
            for aid in held:
                deltas[aid] = deltas.get(aid, 0.0) + w
    if over:
        for aid in over:
            members = ais[aid]
            delta = 0.0
            for ci, w in row_items:
                if ci in members:
                    delta += w
            deltas[aid] = delta
    return deltas


@pytest.mark.parametrize('memo', [False, True])
@pytest.mark.parametrize('maximize', [False, True])
@pytest.mark.parametrize('seed', range(12))
def test_the_overlay_shortcut_folds_what_the_full_overlay_folds(
        monkeypatch, memo, maximize, seed):
    with monkeypatch.context() as mp:
        mp.setattr(af._MinLaborPool, '_partner_deltas', _full_overlay_deltas)
        ref = _drive(_scene(seed), maximize, memo=None)
    got = _drive(_scene(seed), maximize, memo={} if memo else None)
    assert got == ref, f'seed {seed} max={maximize} memo={memo}: the shortcut moved a take'


def test_the_shortcut_both_skips_and_refolds(monkeypatch):
    seen = {'skipped': 0, 'refolded': 0}
    real = af._MinLaborPool._partner_deltas

    def counted(self, row_items, sku=None):
        over = getattr(self._ais, '_over', None) or {}
        live_sets = getattr(self._ais, '_live', {})
        rowset = {ci for ci, _w in row_items}
        for aid, got in over.items():
            if rowset & got == rowset & set(live_sets.get(aid, ())):
                seen['skipped'] += 1
            else:
                seen['refolded'] += 1
        return real(self, row_items, sku)

    monkeypatch.setattr(af._MinLaborPool, '_partner_deltas', counted)
    for seed in range(12):
        _drive(_scene(seed), False, memo={})
    assert seen['skipped'] > 0 and seen['refolded'] > 0, seen
