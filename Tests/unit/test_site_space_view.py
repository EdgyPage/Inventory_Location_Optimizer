"""test_site_space_view.py — one space view for the site, composed from one per leaf.

Site-dock 20, building 08's answer.  `compose_site_view` is a PURE function over frozen
views, so everything here is a direct call on hand-built views: no manager, no run, and
`SpaceTimeline.freeze` is deliberately not exercised — it is untouched by this ticket and
its own purity pin (`test_space_timeline.py`) still covers it verbatim.

What is pinned, and why each is a silent failure otherwise:

  * **The regime partition.**  A leaf's `empties` is snapshotted from `mgr._index`, which is
    the WHOLE geometry's free index, so every leaf lists the other channel's free bins as
    permanently, falsely available (memory `free-bins-counts-the-whole-geometry`).  A naive
    union imports both leaves' phantoms and a mixed trailer's store units rank against
    fulfillment bins no store putter can reach.
  * **Identity, not equality.**  The frozen view is pinned read-only downstream BY IDENTITY
    (`test_gain_plan.py`: `view.empties[key] is empties_before`), so a rebuilt tuple passes
    every equality test while breaking that pin.
  * **Element-wise versions, never a sum.**  Magnitudes are meaningless by contract and
    summing is lossy in exactly the failing direction: A +1 / B +0 and A +0 / B +1 sum
    identically, which is the "two version-equal freezes straddle a real change" bug the
    eviction fold exists to kill.
  * **A composition of ONE is the view itself.**  Filtering there would move every
    standing-yard run on the books, removing a phantom that belongs to the uncoupled model
    rather than to a defect this function may fix on its way past.
  * **`emptied_at` is keyed by `id(bin)` and the leaves hold TWO warehouses.**  Each leaf
    builds its own, so the id-spaces are independent and CPython recycles ids; a collision
    would hand one leaf's lookup the other leaf's stamp for a different bin.

The trap this test is written against, from memory `lockstep-tests-compare-aggregates-only`:
assert the composed KEY SETS and their tuples, never a count of bins.

Run:  python -m pytest Tests/unit/test_site_space_view.py -q
"""
from __future__ import annotations

import pytest

from Inbound.site_space import compose_site_view
from Inbound.space import SpaceView

_STORE, _FUL = 'store', 'fulfillment'

#: BinKey is a PLAIN TUPLE — `(handling, category, storage_size, unit_category)`.  These
#: are shaped like the real thing so the `regime_of(key)` trap below is the real trap.
_K_STORE = ('conveyable', 'food', 'medium', 'pallet')
_K_FUL = ('conveyable', 'fulfillment', 'singleton', 'fulfillment')


class _Bin:
    """A bin-shaped stand-in: `regime_of` duck-types over exactly these attributes."""

    def __init__(self, key, tag=''):
        self.handling_type, self.storage_type, self.storage_size, self.unit_type = key
        self.tag = tag

    def __repr__(self):
        return f'_Bin({self.tag!r})'


def _view(empties=None, predicted=None, emptied_at=None, released_at=0.0,
          versions=(1, 2, 3), frozen_at=100.0, window=None):
    return SpaceView(empties=empties or {}, emptied_at=emptied_at or {},
                     predicted=predicted or {}, released_at=released_at,
                     versions=versions, frozen_at=frozen_at, window=window)


def _leaves():
    """Two leaves as production builds them: each `empties` carries BOTH regimes' keys,
    because `_index` is the whole geometry's free index and the leaf only simulates its
    own section."""
    s_bins = (_Bin(_K_STORE, 's0'), _Bin(_K_STORE, 's1'))
    f_bins = (_Bin(_K_FUL, 'f0'),)
    s_phantom = (_Bin(_K_FUL, 'phantom-in-store-leaf'),)
    f_phantom = (_Bin(_K_STORE, 'phantom-in-ful-leaf'),)
    store = _view(empties={_K_STORE: s_bins, _K_FUL: s_phantom},
                  predicted={_K_STORE: s_bins}, released_at=10.0, versions=(1, 2, 3))
    ful = _view(empties={_K_FUL: f_bins, _K_STORE: f_phantom},
                predicted={_K_FUL: f_bins}, released_at=20.0, versions=(4, 5, 6))
    return (store, s_bins, s_phantom), (ful, f_bins, f_phantom)


# ── 1. a composition of one ──────────────────────────────────────────────────────

def test_one_contribution_is_returned_by_identity():
    """Not "an equal view" — the SAME object.  Every standing-yard run on the books goes
    through this path, so anything else is a change to runs this ticket does not touch."""
    v = _view(empties={_K_STORE: (_Bin(_K_STORE),)})
    assert compose_site_view([(None, v)]) is v
    assert compose_site_view([(_STORE, v)]) is v


def test_one_contribution_keeps_its_phantom():
    """The other channel's free bins stay in a single leaf's view. That phantom belongs to
    the UNCOUPLED model; removing it here would be this function silently fixing a defect
    it was not asked about, on every run already on disk."""
    (store, _, phantom), _ = _leaves()
    out = compose_site_view([(_STORE, store)])
    assert out.empties[_K_FUL] is phantom


def test_no_contribution_composes_to_none():
    assert compose_site_view([]) is None
    assert compose_site_view([(_STORE, None), (_FUL, None)]) is None


# ── 2. the regime partition ──────────────────────────────────────────────────────

def test_empties_is_partitioned_by_regime():
    """THE POINT OF THE MODULE. Each leaf contributes only its OWN regime's keys, so a
    mixed trailer's store units rank against store bins and its fulfillment units against
    fulfillment bins."""
    (store, s_bins, _), (ful, f_bins, _) = _leaves()
    out = compose_site_view([(_STORE, store), (_FUL, ful)])
    assert set(out.empties) == {_K_STORE, _K_FUL}
    assert out.empties[_K_STORE] is s_bins, 'the store key came from the wrong leaf'
    assert out.empties[_K_FUL] is f_bins, 'the fulfillment key came from the wrong leaf'


def test_neither_leaf_phantom_survives():
    """Non-vacuity for the test above: both phantoms are DROPPED, and they are droppable
    only because the tag decides and the bin checks."""
    (store, _, s_phantom), (ful, _, f_phantom) = _leaves()
    out = compose_site_view([(_STORE, store), (_FUL, ful)])
    live = {id(b) for bins in out.empties.values() for b in bins}
    assert id(s_phantom[0]) not in live
    assert id(f_phantom[0]) not in live


def test_the_tuples_pass_through_by_identity():
    """The frozen view is pinned read-only downstream BY IDENTITY. A rebuilt tuple is equal
    and breaks the pin."""
    (store, s_bins, _), (ful, f_bins, _) = _leaves()
    out = compose_site_view([(_STORE, store), (_FUL, ful)])
    assert out.empties[_K_STORE] is s_bins
    assert out.predicted[_K_STORE] is store.predicted[_K_STORE]
    assert out.predicted[_K_FUL] is ful.predicted[_K_FUL]


def test_regime_of_a_BINKEY_is_the_trap_this_filter_avoids():
    """`BinKey` is a PLAIN TUPLE, so every `getattr` in `regime_of` falls through it.  It
    used to answer 'store' for a FULFILLMENT key — silently, and always in the same
    direction; since site-dock 24 it RAISES, which is why the filter asking the bin and
    never the key is now enforced rather than merely documented."""
    import pytest
    from Warehouse.kernel.regime import regime_of
    for key in (_K_FUL, _K_STORE):
        with pytest.raises(TypeError, match='regime_of_key'):
            regime_of(key)
    # and the value answers correctly, which is what the filter actually uses
    assert regime_of(_Bin(_K_FUL)) == _FUL
    assert regime_of(_Bin(_K_STORE)) == _STORE


def test_predicted_and_emptied_at_are_unioned_not_filtered():
    """`predicted` is projected from a leaf's OWN demand over its OWN SKUs and `emptied_at`
    is harvested from its own reclaims, so both are already leaf-own. Filtering all three
    uniformly would be three times the surface for one real defect."""
    (store, s_bins, _), (ful, f_bins, _) = _leaves()
    store.emptied_at = {1: 5.0}
    ful.emptied_at = {2: 6.0}
    out = compose_site_view([(_STORE, store), (_FUL, ful)])
    assert set(out.predicted) == {_K_STORE, _K_FUL}
    assert out.emptied_at == {1: 5.0, 2: 6.0}


# ── 3. the four scalars ──────────────────────────────────────────────────────────

def test_versions_are_element_wise_and_never_summed():
    """Slot 0 still means "demand changed", slot 1 "the free index changed", slot 2 "a bin
    filled", so a sub-vector cache key keeps working and no fourth counter appears."""
    (store, _, _), (ful, _, _) = _leaves()
    out = compose_site_view([(_STORE, store), (_FUL, ful)])
    assert out.versions == ((1, 4), (2, 5), (3, 6))


def test_two_different_histories_do_not_compose_equal():
    """The failing direction a SUM is lossy in: leaf A +1 / leaf B +0 and A +0 / B +1 sum
    identically, which is exactly the "two version-equal freezes straddle a real change"
    bug the eviction fold was written to kill."""
    a = compose_site_view([(_STORE, _view(versions=(1, 0, 0))),
                           (_FUL, _view(versions=(0, 0, 0)))])
    b = compose_site_view([(_STORE, _view(versions=(0, 0, 0))),
                           (_FUL, _view(versions=(1, 0, 0)))])
    assert a.versions != b.versions


def test_released_at_is_carried_per_regime_and_never_averaged():
    """It is a FACT — the release instant of THAT channel's batch — and the two leaves
    legitimately release at different instants inside one site day. One batch is one site
    day; it is not one site MOMENT. Note the TYPE: a leaf's view carries a float, a
    composed view carries a mapping."""
    (store, _, _), (ful, _, _) = _leaves()
    out = compose_site_view([(_STORE, store), (_FUL, ful)])
    assert out.released_at == {_STORE: 10.0, _FUL: 20.0}
    assert 15.0 not in (out.released_at.values()), 'an average was fabricated'


def test_one_site_epoch_and_a_disagreement_is_refused():
    """The coordinator drains once, so both freezes take the same instant — the ambiguity
    dissolves rather than being resolved. A composed view stamped with one leaf's epoch
    while carrying the other's bins gives the urgency gate a "now" that never happened."""
    (store, _, _), (ful, _, _) = _leaves()
    assert compose_site_view([(_STORE, store), (_FUL, ful)]).frozen_at == 100.0
    ful.frozen_at = 101.0
    with pytest.raises(ValueError, match='froze at different instants'):
        compose_site_view([(_STORE, store), (_FUL, ful)])


# ── 4. the refusals ──────────────────────────────────────────────────────────────

def test_a_futuresight_window_is_refused_rather_than_zipped():
    """The zip rule is settled and written on the refusal; what is refused is SHIPPING it
    unexercised. `_window` is None on every lawful arm, so the code would rot in place
    (memory `hand-run-test-tiers-rot-silently`)."""
    (store, _, _), (ful, _, _) = _leaves()
    store.window = ({1: 2},)
    with pytest.raises(ValueError, match='futuresight window'):
        compose_site_view([(_STORE, store), (_FUL, ful)])


def test_an_untagged_contribution_is_refused_once_there_are_two():
    """The tag is what partitions `empties`. It is absent at one leaf because nothing is
    partitioned there — and this is what stops that absence surviving into the coupled
    case."""
    (store, _, _), (ful, _, _) = _leaves()
    with pytest.raises(ValueError, match='no regime tag'):
        compose_site_view([(None, store), (_FUL, ful)])


def test_two_leaves_claiming_one_regime_are_refused():
    (store, _, _), (ful, _, _) = _leaves()
    with pytest.raises(ValueError, match='same regime'):
        compose_site_view([(_STORE, store), (_STORE, ful)])


def test_an_emptied_at_id_collision_is_refused():
    """The leaves hold SEPARATE warehouses, so `id(bin)` spaces are independent and CPython
    recycles ids. A collision is two different bins, not one bin emptied twice — and
    nothing in production reads this field, which is why it would have been silent."""
    (store, _, _), (ful, _, _) = _leaves()
    store.emptied_at = {7: 1.0}
    ful.emptied_at = {7: 2.0}
    with pytest.raises(ValueError, match='id collision'):
        compose_site_view([(_STORE, store), (_FUL, ful)])


def test_a_heterogeneous_key_is_refused():
    """A BinKey is derived from the bin's own four attributes, so this cannot happen unless
    an index was built by hand — and then the first bin would decide for bins it does not
    describe."""
    mixed = (_Bin(_K_STORE, 'a'), _Bin(_K_FUL, 'b'))
    a = _view(empties={_K_STORE: mixed})
    b = _view(empties={_K_FUL: (_Bin(_K_FUL),)})
    with pytest.raises(ValueError, match='more than one regime'):
        compose_site_view([(_STORE, a), (_FUL, b)])


# ── 5. the wiring: the coordinator really does reach the composer ───────────────

def test_freeze_views_contributes_one_untagged_view_per_timeline():
    """The tag is absent at ONE leaf because a composition of one partitions nothing — and
    `compose_site_view` refuses an untagged contribution the moment there are two, so the
    absence cannot survive into the coupled case."""
    from Inbound.receiving import SiteReceiving

    class _Timeline:
        def __init__(self):
            self.frozen = None

        def freeze(self, mgr, epoch):
            self.frozen = _view(frozen_at=epoch)
            return self.frozen

    class _Leaf:
        def __init__(self, tl):
            self.space_timeline = tl

    class _Standing:
        #: The coordinator refuses a non-standing transit at construction, so the fake has
        #: to carry the one surface that decision is made on and nothing else.
        STANDING = True

    tl = _Timeline()
    got = SiteReceiving(dock=None, transit=_Standing())._freeze_views([_Leaf(tl)], 42.0)
    assert got == [(None, tl.frozen)]
    assert got[0][1].frozen_at == 42.0
    assert SiteReceiving(dock=None,
                         transit=_Standing())._freeze_views([_Leaf(None)], 42.0) == []


def test_a_real_drain_puts_the_composed_view_on_the_frozen_ctx(monkeypatch):
    """NON-VACUITY for the byte-identity proof. Every seeded `fifo` key ignores `ctx.space`,
    so a composer that returned the wrong thing — or was never reached at all — would move
    no number and no existing test would notice. This asserts the view that reached the
    policy seam IS the leaf's own frozen view, by identity."""
    from Inbound.transit import YardTransit
    from Inbound.trailer import Trailer28
    from Inbound.space import SpaceTimeline
    from Warehouse.picking.Workload_Builder import drain_sku
    from Tests.unit.test_standing_yard import _dispatch, _manager

    # Patched on the CLASS: `YardTransit` uses __slots__, so an instance attribute is
    # read-only and the spy has to sit where the method really lives.
    seen = []
    real = YardTransit.freeze_ctx

    def _spy(self):
        ctx = real(self)
        seen.append(ctx)
        return ctx

    monkeypatch.setattr(YardTransit, 'freeze_ctx', _spy)

    mgr = _manager(YardTransit(Trailer28, lead_s=0.0, doors=4), crew=2)
    tl = SpaceTimeline(drain_sku)
    tl.attach(mgr)
    _dispatch(mgr, 101, 40, 10, 0.0)
    before = tl.views_built
    mgr.receiving.receive((mgr,), None)

    assert tl.views_built == before + 1, 'the drain never froze a view at all'
    assert seen, 'the drain never froze a ctx'
    view = seen[-1].space
    assert view is not None, 'the composed view never reached `ctx.space`'
    assert isinstance(view, SpaceView) and view.frozen_at == 0.0
    # A composition of ONE is the view itself, so `released_at` is still a leaf's SCALAR
    # here and not the composed mapping -- which is how this test would notice the
    # single-leaf path quietly starting to compose.
    assert not isinstance(view.released_at, dict)


def test_the_drain_routes_through_the_composer_seam(monkeypatch):
    """The one claim the single-leaf byte-identity property makes unobservable: at one
    contribution the composer IS identity, so a drain that went back to calling `freeze`
    directly would move no number and every other test here would still pass. Asserted on
    the SEAM being used rather than on the source containing a string — a source scan is
    satisfied by `if False:` and by a commented-out call alike."""
    from Inbound.receiving import SiteReceiving
    from Inbound.transit import YardTransit
    from Inbound.trailer import Trailer28
    from Inbound.space import SpaceTimeline
    from Warehouse.picking.Workload_Builder import drain_sku
    from Tests.unit.test_standing_yard import _dispatch, _manager

    calls = []
    real = SiteReceiving._freeze_views

    def _spy(self, leaves, epoch):
        out = real(self, leaves, epoch)
        calls.append((tuple(leaves), epoch, len(out)))
        return out

    monkeypatch.setattr(SiteReceiving, '_freeze_views', _spy)

    mgr = _manager(YardTransit(Trailer28, lead_s=0.0, doors=4), crew=2)
    SpaceTimeline(drain_sku).attach(mgr)
    _dispatch(mgr, 101, 40, 10, 0.0)
    mgr.receiving.receive((mgr,), None)

    assert len(calls) == 1, f'the drain reached the composer seam {len(calls)} time(s)'
    seen_leaves, epoch, n = calls[0]
    assert seen_leaves == (mgr,) and n == 1
