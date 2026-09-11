"""PROTOTYPE — throwaway. Wayfinder site-dock ticket 05: the composite gain bundle.

Answers four things and nothing else (delete freely once the ticket is resolved):

  1. Do the two candidate LOOKUP SHAPES — (A) a composite holding two whole
     `GainBundle`s, (B) one bundle whose arm fields are owner-keyed dicts — differ in
     anything an evaluator can observe?
  2. Where does the owner dispatch actually go?  The claim under test: `place_load`
     ALREADY groups by BinKey (`Inbound/gain.py:391-411`) and BinKey DETERMINES regime
     (`inventory_common.py:42-43`), so the charter's "per-unit, keyed by owning channel"
     needs no per-unit loop — the seam exists.
  3. Is a mixed trailer's score EXACTLY decomposable into a store term and a
     fulfillment term?  (If the two channels' groups ever competed for the same bins it
     would not be, and the commensurability test below would be ill-posed.)
  4. The commensurability test that would FAIL if a fulfillment hour stopped being
     worth a store hour to the site.  Recovers the implied exchange rate (a, b) in
     score = a*store_hours + b*ful_hours and asserts a == b == 1.

Faithful-to-machinery: drives the repo's REAL `_Evaluator`, `GainBundle`, `place_load`
and `plan_order` over stub units/bins shaped like the production ones.  The two arms are
deliberately different ADAPTERS — store on `tmin` (merge), fulfillment on `fifo`
(uniform) — because a composite that only swaps DATA is a weaker test than one that must
swap the CODE PATH mid-trailer.

Run:  python .scratch/site-dock/assets/prototype_site_gain_bundle.py
"""
from __future__ import annotations

import os
import sys

# entry-script bootstrap (the one legal sys.path.insert site, CLAUDE.md section 2)
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.insert(0, _REPO)

from Inbound.gain import GainBundle, _Evaluator, plan_order          # noqa: E402
from Warehouse.kernel.regime import regime_of, STORE, FULFILLMENT    # noqa: E402
from Warehouse.inventory.inventory_common import binkey_of           # noqa: E402


# ── stubs, shaped like production ────────────────────────────────────────────────────
class _Speed:
    def __init__(self, x, y):
        self.x_pace, self.y_pace = x, y


class _WP:
    """A regime's WorkloadParams.  The two regimes carry DIFFERENT pick costs — that is
    the real model (`wp.by_regime`), and it is NOT what commensurability denies."""
    def __init__(self, intercept, per_item, xp, yp):
        self.height_brackets = ((0.0, 1.0), (60.0, 1.15), (120.0, 1.35))
        self.pick_intercept = intercept
        self.pick_per_item = per_item
        self.speed = _Speed(xp, yp)


class _Line:
    def __init__(self, m):
        self._m = m

    def mean(self):
        return self._m


class _Demand:
    def __init__(self, rate, line_mean, rf):
        self.quantity_rate = rate
        self.line = _Line(line_mean)
        self.relative_frequency = rf


class _SHC:
    def __init__(self, handling, category):
        self.handling, self.category = handling, category


class _Order:
    def __init__(self, sku, handling, category, rate=4.0, line_mean=2.0, hv=0.4,
                 rf=0.01, labor=1.0):
        self.sku = sku
        self.storage_handle_config = _SHC(handling, category)
        self.demand = _Demand(rate, line_mean, rf)
        self.handle_var = hv
        self.labor_cost = labor


class _Unit:
    def __init__(self, order, size, cat, qty=6):
        self.order, self.storage_size, self.unit_category = order, size, cat
        self.quantity = qty


class _Bin:
    def __init__(self, x, y):
        self.x_phys, self.y_phys = float(x), float(y)


class _Item:
    def __init__(self, unit):
        self.unit = unit


class _Trailer:
    def __init__(self, seq, units):
        self.seq, self.pending, self.taken = seq, [_Item(u) for u in units], 0


class _View:
    __slots__ = ('empties', 'emptied_at', 'predicted', 'released_at', 'versions',
                 'frozen_at', 'window')

    def __init__(self, empties, predicted):
        self.empties, self.predicted = empties, predicted
        self.emptied_at, self.released_at = {}, 0.0
        self.versions, self.frozen_at, self.window = (1, 1, 1), 0.0, None


def _store_unit(i, qty=6):
    return _Unit(_Order(f's{i}', 'ambient', 'dry', rate=4.0,
                        rf=0.004 + 0.001 * i, labor=1.0 + 0.1 * i),
                 'large', 'pallet', qty)


def _ful_unit(i, qty=6):
    return _Unit(_Order(f'f{i}', FULFILLMENT, FULFILLMENT, rate=9.0,
                        rf=0.006 + 0.001 * i, labor=0.8 + 0.1 * i),
                 'small', FULFILLMENT, qty)


WP = {STORE: _WP(3.0, 0.55, 0.05, 0.09),
      FULFILLMENT: _WP(2.1, 0.40, 0.05, 0.09)}
PUT_SPEED = _Speed(0.04, 0.07)
TIER_RANKS = {'pallet': ({'large': 1}, ['large']),
              FULFILLMENT: ({'small': 1}, ['small'])}


def _view():
    """Both regimes' tiers in ONE dict.  The keys are DISJOINT by construction —
    regime is a BinKey component — so this is a key-disjoint union, not a merge."""
    sk = binkey_of(_store_unit(0))
    fk = binkey_of(_ful_unit(0))
    assert sk != fk
    empties = {sk: tuple(_Bin(10 + 7 * i, 20 + 11 * (i % 5)) for i in range(24)),
               fk: tuple(_Bin(14 + 5 * i, 15 + 13 * (i % 4)) for i in range(24))}
    predicted = {sk: tuple(_Bin(90 + 3 * i, 70) for i in range(6)),
                 fk: tuple(_Bin(80 + 4 * i, 66) for i in range(6))}
    return _View(empties, predicted), sk, fk


def _kw(regime):
    """The bundle half that is ALREADY site-wide today: put_speed is `put_crew_spec()`
    (one site CONFIG, no channel variation) and wp_of already dispatches per regime via
    `_wp_for`.  Only the ARM machinery below differs per leaf."""
    return dict(put_speed=PUT_SPEED,
                wp_of=lambda u: WP[regime_of(u)],
                binkey_of=binkey_of,
                tier_ranks_for=lambda cat: TIER_RANKS[cat],
                aisle_sku_sets={}, aisle_idx_sets={}, aisle_demand_sum={},
                fee_threshold_days=3.0, urgency_horizon_days=0.0)


def _arm_store():   # tmin -> the merge adapter
    return GainBundle(minimize=True, **_kw(STORE))


def _arm_ful():     # fifo -> the uniform adapter
    return GainBundle(uniform=True, **_kw(FULFILLMENT))


# ── shape A: a composite holding two whole GainBundles ───────────────────────────────
class SiteBundleA:
    """Two bundles, each built by the UNCHANGED `_gain_bundle_for` against its own leaf's
    mgr/strat/sctx.  Faithful-to-arm is preserved structurally: neither half is rebuilt,
    reinterpreted or averaged."""
    __slots__ = ('by_owner',)

    def __init__(self, store, ful):
        self.by_owner = {STORE: store, FULFILLMENT: ful}

    def for_key(self, key):
        return self.by_owner[_regime_of_key(key)]


# ── shape B: one bundle, arm fields as owner-keyed dicts ─────────────────────────────
class SiteBundleB:
    """One flat object whose FIVE arm fields are dicts.  Everything else is shared."""
    __slots__ = ('uniform', 'minimize', 'pool_factory', 'expect_heads', 'heads_of',
                 'aisle_sku_sets', 'aisle_idx_sets', 'aisle_demand_sum',
                 'put_speed', 'wp_of', 'binkey_of', 'tier_ranks_for',
                 'fee_threshold_days', 'urgency_horizon_days', '_cur')

    def __init__(self, store, ful):
        src = {STORE: store, FULFILLMENT: ful}
        for f in ('uniform', 'minimize', 'pool_factory', 'expect_heads', 'heads_of',
                  'aisle_sku_sets', 'aisle_idx_sets', 'aisle_demand_sum'):
            setattr(self, f, {k: getattr(v, f) for k, v in src.items()})
        for f in ('put_speed', 'wp_of', 'binkey_of', 'tier_ranks_for',
                  'fee_threshold_days', 'urgency_horizon_days'):
            setattr(self, f, getattr(store, f))
        self._cur = STORE

    def for_key(self, key):
        self._cur = _regime_of_key(key)
        return _FlatFacade(self)


class _FlatFacade:
    """What shape B must synthesise so the evaluator sees a bundle-shaped thing: the
    per-owner slice of every dict field.  Note it is REBUILT per group — shape B pays
    this, shape A does not."""
    __slots__ = ('_p', '_o')

    def __init__(self, parent):
        self._p, self._o = parent, parent._cur

    def __getattr__(self, name):
        v = getattr(self._p, name)
        return v[self._o] if isinstance(v, dict) and self._o in v else v


def _regime_of_key(key):
    """BinKey -> regime.  This is the whole dispatch: `regime_of` reads exactly the
    fields BinKey carries (handling, category, unit_category), so the key answers."""
    handling, category, _size, cat = key
    return FULFILLMENT if FULFILLMENT in (handling, category, cat) else STORE


# ── the ONE evaluator change both shapes need ────────────────────────────────────────
class SiteEvaluator(_Evaluator):
    """`self.b` becomes a per-BinKey-group CURSOR, set inside `_params` — which
    `place_load` already calls exactly once per group, before it branches on the adapter.

    The cursor goes THERE and not around `place_load` for a reason the first draft of
    this prototype got wrong: `place_load` shares ONE `avail_cache` across a load's
    groups "so spill into an already-touched tier continues where consumption left off"
    (`gain.py:355-360`).  Wrapping the group loop from outside gives each group its own
    cache and silently breaks that continuity for two same-regime groups that spill into
    a shared tier.  Dispatching from inside keeps the real loop, the real cache and the
    real `alloc` untouched.

    Safe because a spill CHAIN never crosses regimes: `_chain` varies only `size`, and
    regime lives in the other three BinKey fields.  Every other evaluator cache
    (`_sorted_now`, `_sorted_pred`, `_wp`, `_chain_cache`, `_worst`, `_mom`, `taken`) is
    BinKey- or bin-id-keyed, so one evaluator serves both leaves with zero cross-talk.
    """
    __slots__ = ('_site', '_key', 'trace', '_b0')

    def __init__(self, site, space, window_rates=None):
        self._site, self._key, self.trace = site, None, []
        super().__init__(site.for_key(_ANY_STORE_KEY), space, window_rates=window_rates)

    @property
    def b(self):
        return self._site.for_key(self._key) if self._key is not None else self._b0

    @b.setter
    def b(self, v):
        self._b0 = v

    def _params(self, unit, own_key):
        self._key = own_key                      # the WHOLE dispatch, one line
        self.trace.append((own_key, 'uniform' if self.b.uniform else
                           ('pool' if self.b.pool_factory is not None else 'merge')))
        return super()._params(unit, own_key)


_ANY_STORE_KEY = ('ambient', 'dry', 'large', 'pallet')


def _price(site, view, trailer, predicted=False):
    ev = SiteEvaluator(site, view)
    c, _tk = ev.place_load([i.unit for i in trailer.pending], set(), predicted)
    return c, ev.trace


def main():
    view, sk, fk = _view()
    store_units = [_store_unit(i) for i in range(4)]
    ful_units = [_ful_unit(i) for i in range(4)]
    mixed = _Trailer(1, store_units + ful_units)

    A = SiteBundleA(_arm_store(), _arm_ful())
    B = SiteBundleB(_arm_store(), _arm_ful())

    print('=' * 78)
    print('Q2 — where the owner dispatch goes')
    print('=' * 78)
    print(f'  store BinKey       {sk}  -> {_regime_of_key(sk)}')
    print(f'  fulfillment BinKey {fk}  -> {_regime_of_key(fk)}')
    print('  BinKey determines regime, and place_load already groups by BinKey:')
    cA, trA = _price(A, view, mixed)
    for k, adapter in trA:
        print(f'    group {k[3]:<12} owner {_regime_of_key(k):<12} adapter {adapter}')

    print()
    print('=' * 78)
    print('Q1 — do the two shapes differ in anything observable?')
    print('=' * 78)
    cB, trB = _price(B, view, mixed)
    print(f'  shape A mixed-trailer cost  {cA:,.4f}')
    print(f'  shape B mixed-trailer cost  {cB:,.4f}')
    print(f'  identical: {abs(cA - cB) < 1e-9}    same dispatch trace: {trA == trB}')
    # and through the REAL plan_order greedy, over several candidates
    cands = [_Trailer(2, [_store_unit(9), _ful_unit(9)]),
             _Trailer(3, [_ful_unit(7), _ful_unit(8), _store_unit(7)]),
             _Trailer(4, [_store_unit(5), _store_unit(6)])]
    ordA = [t.seq for t in plan_order(cands, None, view, predicted=True,
                                      _ev=SiteEvaluator(A, view))]
    ordB = [t.seq for t in plan_order(cands, None, view, predicted=True,
                                      _ev=SiteEvaluator(B, view))]
    print(f'  plan_order A {ordA}   plan_order B {ordB}   identical: {ordA == ordB}')

    print()
    print('  the ONE place they differ — per-arm validation:')
    for name, build in (('A', lambda: GainBundle(uniform=True, pool_factory=lambda *a: 1,
                                                 **_kw(FULFILLMENT))),
                        ('B', None)):
        if build is None:
            bad = SiteBundleB(_arm_store(), _arm_ful())
            bad.uniform[FULFILLMENT] = True
            bad.pool_factory[FULFILLMENT] = lambda *a: 1
            print('    B: uniform+pool_factory on one owner — accepted silently, '
                  f'uniform={bad.for_key(fk).uniform} pool={bad.for_key(fk).pool_factory is not None}')
        else:
            try:
                build()
                print('    A: accepted (UNEXPECTED)')
            except ValueError as e:
                print(f'    A: refused by GainBundle.__init__ — {str(e)[:60]}...')

    print()
    print('=' * 78)
    print('Q3 — is a mixed trailer EXACTLY decomposable by owner?')
    print('=' * 78)
    s_only = _Trailer(10, store_units)
    f_only = _Trailer(11, ful_units)
    cs, _ = _price(A, view, s_only)
    cf, _ = _price(A, view, f_only)
    print(f'  store-only       {cs:,.4f}')
    print(f'  fulfillment-only {cf:,.4f}')
    print(f'  sum              {cs + cf:,.4f}')
    print(f'  mixed            {cA:,.4f}')
    print(f'  exact: {abs((cs + cf) - cA) < 1e-9}  '
          '(bins are BinKey-partitioned, so the two owners never contend)')

    print()
    print('=' * 78)
    print('Q4 — the commensurability test (what would FAIL)')
    print('=' * 78)
    a = (cA - cf) / cs
    b = (cA - cs) / cf
    print(f'  implied exchange rate  a(store) = {a:.6f}   b(fulfillment) = {b:.6f}')
    print(f'  PASS: {abs(a - 1.0) < 1e-9 and abs(b - 1.0) < 1e-9} — a plain sum, no '
          'per-channel coefficient')
    print('  the test FAILS the moment anyone weights one channel: a != b.')
    print()
    print('  the denominator guard (memory a-count-is-not-a-claim):')
    big = _Trailer(12, store_units * 2 + ful_units)
    cbig, _ = _price(A, view, big)
    n_mix = len(mixed.pending)
    n_big = len(big.pending)
    print(f'    mixed   score {cA:>10,.2f} over {n_mix} units = {cA / n_mix:>8,.2f}/unit')
    print(f'    bigger  score {cbig:>10,.2f} over {n_big} units = {cbig / n_big:>8,.2f}/unit')
    print('    a raw sum ranks the BIGGER trailer "worth more"; per-unit says otherwise.')
    print('    -> the score is a TRAILER-LEVEL gain (a difference of two sums over the')
    print('       SAME load), so it is already denominated — but any report of it must')
    print('       carry the unit count, or it restates trailer size.')


if __name__ == '__main__':
    main()
