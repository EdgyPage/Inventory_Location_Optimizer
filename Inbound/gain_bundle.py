"""gain_bundle — which arm owns a unit, and what the evaluator may ask of it.

One mixed trailer is priced per unit by the OWNING channel's arm, so something has to answer
"whose arm is this?" before anything can be priced. These three do, through one protocol:

- `GainBundle`      — everything arm-specific the evaluator needs, driver-built and
                      driver-injected, because `Inbound -> wh_inventory / wh_placement` are
                      forbidden edges and the broker holds what it is handed.
- `OneOwnerBundle`  — a single-channel run: `for_key` ignores the key and returns the one
                      bundle it holds.
- `SiteGainBundle`  — two arms over one mixed trailer, resolved by the key's own regime.

## Why this is its own module

The deletion test again, and `gain.py:57-60` states it: deleting the trio brings `if coupled:`
back into the PRICING PATH, which is exactly what it exists to prevent. There is one
resolution rule and no coupling branch anywhere below it.
"""
from __future__ import annotations

from math import isclose

from Inbound.gain_cow import AISLE_COPIERS
from Warehouse.kernel.regime import REGIMES, regime_of_key

__all__ = ['GainBundle', 'OneOwnerBundle', 'SiteGainBundle']

#: The two days-denominated knobs ride the bundle because the gate entry has no other channel
#: to CONFIG (`inbound_spec()` -> driver -> bundle, the five-seam path).
DEFAULT_FEE_THRESHOLD_DAYS: float = 2.0
DEFAULT_URGENCY_HORIZON_DAYS: float = 0.0


class GainBundle:
    """Everything arm-specific the evaluator needs, injected by the driver.

    Three adapters, and the bundle picks exactly one.  `uniform` selects the uniform
    adapter (`fifo`: no pool, no direction — the tier's mean and a seat count).  Else
    `pool_factory` None selects the merge adapter (extremal-D family, direction
    `minimize`); otherwise the pool adapter calls `pool_factory(candidates, state, wp)`,
    where `state` is a FRESH copy per evaluation of every live aisle dict the arm's pool
    commits to: `aisle_state` names them (manager attribute -> the live dict) and
    `AISLE_COPIERS` above says how each is copied.  Those names are the seam's whole
    vocabulary, which is what keeps it fixed -- a family that commits to one more dict is
    a driver branch plus one more name, never a wider signature here.  `expect_heads` prices
    each unit at the mean over `heads_of(pool)` before consuming (rank_random's no-RNG
    expectation).  `wp_of` / `binkey_of` / `tier_ranks_for` are handed over because
    their home (`wh_inventory`) is a forbidden import — the broker rule.

    `minimize` is INERT under `uniform`: a uniform draw has no extremal direction to
    sort a tier by, so nothing reads it.  It is left at its default rather than
    refused, because the merge adapter's default is the same value.

    The two days-denominated knobs ride here because the gate entry has no other
    channel to CONFIG (`inbound_spec()` -> driver -> bundle, the five-seam path).
    """

    __slots__ = ('minimize', 'pool_factory', 'expect_heads', 'heads_of', 'uniform',
                 'aisle_state',
                 'put_speed', 'wp_of', 'binkey_of', 'tier_ranks_for',
                 'fee_threshold_days', 'urgency_horizon_days')

    def __init__(self, *, put_speed, wp_of, binkey_of, tier_ranks_for,
                 minimize: bool = True, pool_factory=None, expect_heads: bool = False,
                 heads_of=None, uniform: bool = False, aisle_state=None,
                 fee_threshold_days: float = DEFAULT_FEE_THRESHOLD_DAYS,
                 urgency_horizon_days: float = DEFAULT_URGENCY_HORIZON_DAYS):
        if expect_heads and (pool_factory is None or heads_of is None):
            raise ValueError('expect_heads prices over the pool\'s aisle heads — it '
                             'needs both pool_factory and heads_of')
        if uniform and (pool_factory is not None or expect_heads):
            raise ValueError('the uniform adapter serves a family with NO pool (fifo); '
                             'pool_factory / expect_heads belong to the pool adapter '
                             'and would be silently ignored here')
        self.uniform = bool(uniform)
        self.minimize = bool(minimize)
        self.pool_factory = pool_factory
        self.expect_heads = bool(expect_heads)
        self.heads_of = heads_of
        self.aisle_state = dict(aisle_state) if aisle_state else {}
        unknown = sorted(n for n in self.aisle_state if n not in AISLE_COPIERS)
        if unknown:
            raise ValueError(
                f'no copier for aisle state {unknown}: the pool adapter must hand the '
                f'arm a COPY of every live dict its `take` commits to, and the dict\'s '
                f'shape decides what a copy is.  Add the name to AISLE_COPIERS with its '
                f'shape rather than letting it reach the pool uncopied')
        if self.aisle_state and pool_factory is None:
            raise ValueError(
                'aisle_state is the POOL adapter\'s copy list -- the merge and uniform '
                'adapters open no pool, so state declared here would be copied by nobody '
                'and read by nobody')
        self.put_speed = put_speed
        self.wp_of = wp_of
        self.binkey_of = binkey_of
        self.tier_ranks_for = tier_ranks_for
        self.fee_threshold_days = float(fee_threshold_days)
        self.urgency_horizon_days = float(urgency_horizon_days)


class OneOwnerBundle:
    """The single-channel bundle provider: `for_key` ignores the key and hands back
    the ONE `GainBundle` it holds — that same instance, never a copy.

    The evaluator always resolves its arm machinery through `for_key`, so a run with
    one channel needs something to resolve THROUGH.  This is it, and it is why there is
    no second path: a bare bundle is refused at `_Evaluator.__init__` rather than
    sniffed for, so the pricing hot path has one shape whether or not the site dock's
    composite is what answers.  Byte-identity is structural — the object the evaluator
    reads is the object the driver built.

    `key` is unused BY CONSTRUCTION, not by accident: one owner has one answer.  The
    cursor hands it `None` before the first group is keyed (`_Evaluator.b`), which this
    adapter answers like any other key.
    """

    __slots__ = ('bundle',)

    def __init__(self, bundle: GainBundle):
        if not isinstance(bundle, GainBundle):
            raise TypeError(
                f'OneOwnerBundle wraps exactly one GainBundle — got '
                f'{type(bundle).__name__}.  A provider handed in here (a second wrap, '
                f'or the site composite) would resolve to itself and silently price '
                f'every owner with whatever the outer lookup returned')
        self.bundle = bundle

    def for_key(self, key):
        return self.bundle

    # ── the site-wide half, read off the provider ─────────────────────────────────
    # The urgency gate reads its two days-denominated knobs from `ctx.gain` itself —
    # it composes hours and days ABOVE any one owner's placement machinery, so it has
    # no BinKey to resolve with and must not pick an arbitrary owner's copy.  They are
    # site CONFIG (`inbound_spec()` -> driver -> bundle), so forwarding is exact here;
    # a provider with two owners owes a REFUSAL when their copies disagree, and that is
    # `SiteGainBundle.bind`'s — not this adapter's, which has nothing to disagree with.
    @property
    def fee_threshold_days(self) -> float:
        return self.bundle.fee_threshold_days

    @property
    def urgency_horizon_days(self) -> float:
        return self.bundle.urgency_horizon_days


#: The bundle fields that belong to the SITE rather than to one owner, and how a
#: disagreement is detected.  Two groups, because they fail two different ways:
#:
#:   * `_SITE_PURE` are read at a **None cursor** — `place_load` calls `binkey_of` to key
#:     the groups before any owner is resolved, and `_chain` reads `tier_ranks_for` for a
#:     group whose cursor names the owner but whose spill chain must be the same walk for
#:     everyone.  They are pure functions handed down by the driver from ONE module-level
#:     definition, so IDENTITY is the honest test: two equivalent functions would still be
#:     two answers to a question the evaluator asks once.
#:   * `_SITE_PACED` are the FLOAT knobs read off the bundle itself — the gate's two
#:     days-denominated ones, which it reads above any owner's placement machinery.
#:     Compared with a TOLERANCE, never `==`, as is the one put crew's pair of paces;
#:     those are checked in the function below rather than named here, because they sit
#:     one level down, on `put_speed`.
_SITE_PURE: tuple[str, ...] = ('binkey_of', 'tier_ranks_for')
_SITE_PACED: tuple[str, ...] = ('fee_threshold_days', 'urgency_horizon_days')


def _site_wide_disagreement(ref: GainBundle, other: GainBundle):
    """`(field, ref value, other value)` for the first SITE-WIDE field two owners answer
    differently, or None when they agree on all of them.

    `put_speed` is checked on the two paces the evaluator actually READS (`x_pace` /
    `y_pace`), not on the profile object: the driver builds a `SpeedProfile` per leaf from
    one payload record, so two equal-valued instances are the lawful case and an identity
    test here would refuse every coupled run.
    """
    for name in _SITE_PURE:
        a, b = getattr(ref, name), getattr(other, name)
        if a is not b:
            return (name, a, b)
    for axis in ('x_pace', 'y_pace'):
        a = getattr(ref.put_speed, axis)
        b = getattr(other.put_speed, axis)
        if not isclose(a, b, rel_tol=1e-12, abs_tol=1e-12):
            return (f'put_speed.{axis}', a, b)
    for name in _SITE_PACED:
        a, b = getattr(ref, name), getattr(other, name)
        if not isclose(a, b, rel_tol=1e-12, abs_tol=1e-12):
            return (name, a, b)
    return None


class SiteGainBundle:
    """The site dock's bundle provider: ONE `GainBundle` per channel, dispatched by the
    BinKey's OWN regime — the composite of 05 decision 1.

    A mixed trailer's store units must be priced by the store arm's pool and its
    fulfillment units by the fulfillment arm's.  `place_load` already groups a load by
    BinKey and `regime_of_key` reads the regime straight off that key (regime is itself a
    BinKey component), so the charter's "per-unit, keyed by owning channel" needs no
    per-unit loop: the seam is one level coarser and free.  This is the SAME owner
    resolution the site receiving coordinator makes at its step-4 handoff
    (`regime_of(item.unit)`) — one fact, read through the key the loop already holds.

    # ── two whole bundles, not one bundle with keyed fields ───────────────────────

    Each owner is the WHOLE `GainBundle` `_gain_bundle_for` built for that leaf, from that
    leaf's own `mgr` / `strat` / `sctx`.  `_gain_bundle_for` is called twice and is
    otherwise untouched, which is what keeps each half FAITHFUL TO ITS ARM structurally
    rather than by argument: neither is rebuilt, reinterpreted or averaged, so the
    composite adds nothing neither arm would do.  Owner-keyed FIELDS on one bundle were
    the rejected alternative — `GainBundle.__init__`'s cross-field refusals (an
    `expect_heads` with no pool; a `uniform` carrying one) would silently stop applying,
    because the five arm fields would no longer sit on one object to be checked against
    each other.

    # ── what is NOT keyed, and the refusal that makes that safe ───────────────────

    Half the bundle is already site-wide: `binkey_of` and `tier_ranks_for` are pure,
    `put_speed` is the one put crew's (`put_crew_spec()` — one site CONFIG), and the
    gate's `fee_threshold_days` / `urgency_horizon_days` are site CONFIG too.  `wp_of`
    needs no keying because on the payload this code actually receives the two owners'
    copies are IDENTICAL FUNCTIONS OF DIFFERENT DATA and the refusal below covers them:
    `workunits` sets `ch_wp.by_regime = None` on every mixed-catalogue channel leaf, so
    `_wp_for(wp, unit)` returns that leaf's own `wp` unchanged and dispatches nothing.
    (An earlier draft of this paragraph said `_wp_for` "ALREADY dispatches per regime".
    It does not, here — the per-regime table is nulled one layer up, and it is the leaf's
    own regime-pure `wp` that makes the answer right.  A false justification is how the
    next change goes wrong.)

    So those five are REFUSED at `bind` when two owners disagree, rather than resolved.
    They cannot differ on a lawful run — both come from one `inbound_spec()` and one
    `put_crew` record — which is exactly why a silent first-wins would never be noticed.
    Two of them are also what makes `for_key(None)` honest: the evaluator resolves with a
    None cursor to key its groups in the first place, and answering that with the
    first-bound owner is only lawful because every owner answers it the same.
    """

    __slots__ = ('_owners',)

    def __init__(self):
        #: regime -> that channel's GainBundle, in BIND order.  The first entry is the
        #: site-wide reference every later bind is checked against, and the one a None
        #: cursor is answered with.
        self._owners: dict = {}

    def __repr__(self):
        return f'SiteGainBundle(owners={tuple(self._owners)!r})'

    @property
    def owners(self) -> tuple:
        """The bound regimes, in bind order — observability for the driver and the tests."""
        return tuple(self._owners)

    def bind(self, regime: str, bundle: GainBundle) -> None:
        """Serve `regime`'s units with `bundle`.  Called once per leaf, at leaf build, by
        the driver that owns both leaves — the same shape `SiteReceiving.bind` and
        `PutawayPool.bind` have, and for the same reason: a leaf cannot see the site.
        """
        if not isinstance(bundle, GainBundle):
            raise TypeError(
                f'a SiteGainBundle owner is exactly one GainBundle — got '
                f'{type(bundle).__name__} for {regime!r}.  A provider bound here (a '
                f'OneOwnerBundle, or this composite itself) would resolve through a '
                f'SECOND lookup and price every key with whatever that one returned')
        if regime not in REGIMES:
            raise ValueError(
                f'{regime!r} is not a storage regime {REGIMES!r}; the regime a bundle '
                f'binds under is what a BinKey resolves TO, so a name outside the set '
                f'would never be reached and its channel would silently price against '
                f'the other one, or refuse on the first mixed group')
        if regime in self._owners:
            raise ValueError(
                f'the {regime} arm is already bound to this site bundle; two owners of '
                f'one channel means one of the two leaves would price every one of its '
                f'own units with the other leaf\'s placement machinery')
        if self._owners:
            ref = next(iter(self._owners.values()))
            bad = _site_wide_disagreement(ref, bundle)
            if bad is not None:
                name, a, b = bad
                raise ValueError(
                    f'the site bundle\'s owners disagree on {name}: '
                    f'{tuple(self._owners)[0]} says {a!r} and {regime} says {b!r}.  That '
                    f'field is the SITE\'s, not a channel\'s — the gate composes hours and '
                    f'days above any one owner and has no BinKey to resolve with, and the '
                    f'pure lookups are read before any group is keyed at all.  Both come '
                    f'from one spec, so a disagreement means the payload was assembled by '
                    f'hand; picking one would be a first-wins nobody would ever see')
        self._owners[regime] = bundle

    def for_key(self, key) -> GainBundle:
        """The arm machinery owning `key`'s group — `regime_of_key`, never `regime_of`.

        A BinKey is a plain tuple, so `regime_of` would fall through every getattr and
        answer 'store' for a fulfillment key, silently and always in the same direction
        (`Inbound/site_space.py` hit the same trap on the bin side).

        `None` is the evaluator's pre-cursor read — `place_load` calls `binkey_of` to form
        the groups before any of them is keyed — and it is answered with the FIRST-BOUND
        owner.  That is exact rather than arbitrary: `bind` refuses owners whose site-wide
        fields differ, so every owner answers those reads identically.
        """
        if key is None:
            if not self._owners:
                raise ValueError(
                    'this site bundle has no owner bound yet; the driver binds one per '
                    'leaf at leaf build, before the batch loop, so an empty lookup means '
                    'the composite reached a drain without its leaves')
            return next(iter(self._owners.values()))
        regime = regime_of_key(key)
        got = self._owners.get(regime)
        if got is None:
            raise ValueError(
                f'a {regime!r} unit was priced at a site dock whose gain bundle serves '
                f'{tuple(self._owners)!r}; a load reaching the evaluator with no owner '
                f'for its regime would otherwise be priced under another channel\'s arm '
                f'and ranked on a pool that cannot grant it a single bin (key {key!r})')
        return got

    # ── the site-wide half, read off the provider ─────────────────────────────────
    # The urgency gate reads these two off `ctx.gain` itself: it composes hours and days
    # ABOVE any one owner's placement machinery, so it has no BinKey to resolve with.  It
    # gets the reference owner's copy, which `bind` has proven is every owner's copy —
    # the obligation `OneOwnerBundle` states and this class discharges.
    @property
    def fee_threshold_days(self) -> float:
        return self.for_key(None).fee_threshold_days

    @property
    def urgency_horizon_days(self) -> float:
        return self.for_key(None).urgency_horizon_days
