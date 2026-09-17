"""gain — the unload-plan evaluator: EXPECTED FUTURE WORK, faithful-to-arm.

The gain arms ("Name the policy arms", 05) order standing trailers by the put + pick
hours their placements will generate ("Define the inbound objective", 10): serve first
the load whose DEFERRAL to the next drain's pool costs the most.  One `@ordering` entry
per family, called once per drain on the frozen ctx; the entries land in BOTH standing
registries, so an arm sets `INBOUND_YARD_POLICY` and `INBOUND_DOCK_POLICY` to one name.

    gain(t) = E[put + pick work if t's load places from the deferral pool]
            − E[same if it places from the pool NOW]
    take argmax gain (ties -> the handed order, which is arrival order); virtually
    consume what the chosen load would take; repeat.

# ── the deferral pool (the one recorded deviation from the prototype's letter) ────

Decision 4 of the objective names the deferral pool "the NEXT drain's pool (leftovers +
predicted)".  The prototype (ticket 04) approximated leftovers as the CURRENT virtual
pool — which is exact only when the standing loads do not contend, and makes the myopic
arm's gain identically zero (no predicted tier, same pool on both sides: a fake arm,
`fifo` in disguise).  The build therefore prices leftovers as what the OTHER remaining
candidates leave behind: each greedy step first places every remaining load against the
current pool (the now side), then each candidate's deferral pool excludes every bin some
OTHER candidate's now-placement took.  Zero extra placements, symmetric across
candidates (two identical loads still tie -> FIFO), degenerates to the prototype's shape
exactly when takes do not overlap — and gives the myopic arm its real signal, which is
CONTENTION for today's space.  Recorded on the build ticket (14), the 11 precedent.

# ── faithful-to-arm: the fidelity seam ────────────────────────────────────────────

`gain` estimates the bins THIS arm's pool would grant, never an idealized best-bin cost
(10 decision 5).  The seam is `GainBundle`, built and injected by the DRIVER (the
`drain_sku` precedent — the import edges `Inbound -> wh_inventory / wh_placement` are
forbidden, so the broker holds what it is handed):

  * extremal-D arms (tmin / tmax) get the K-CHEAPEST MERGE structure: per BinKey class
    the bins sorted ONCE per plan by the arm's own D, consumed by slicing — proven
    order-equal to the full pool up to co-occurrence near-ties and ~5x cheaper (04
    finding 1/2).  The merge orders units by freq x labor_cost (the pool's priority
    minus the co term — exactly the proven residue).
  * selector arms get `pool_factory`: the arm's own pool class, rebuilt per evaluation
    over COPIES of the aisle bookkeeping (purity: live state is never mutated).
    rank_random's virtual pool must consume no RNG (the seam purity pin), so it prices
    by EXPECTATION over the pool's current aisle heads and consumes via a deterministic
    stand-in selector — the deviation 04 decided and this docstring records.
  * `fifo` gets the UNIFORM adapter.  It is the mandatory phase-2 rider (08) and it has
    no pool at ALL — `_build_uniform` sets only `place_one`, a uniform draw over the
    whole tier `_candidates_raw` returns — so neither adapter above fits.  It needs
    neither: the draw's expectation is EXACT in closed form, because `_pair_cost` is
    affine in a bin's (x, y, height multiplier) and sequential draws without
    replacement leave every unit's bin marginally uniform over the tier as frozen.
    Price = the tier's mean; consumption = a SEAT COUNT, since which bin a uniform
    draw got is worth nothing to the next unit.  See `_place_uniform` (ticket 21).

The bundle reaches the evaluator through an OWNER INDIRECTION — `for_key(BinKey)`, never
the bundle itself.  A single-channel run hands over a `OneOwnerBundle`, whose `for_key`
ignores the key and returns the one bundle it holds, so there is exactly one resolution
rule and no `if coupled:` in the pricing path; the site dock's `SiteGainBundle` (two arms
over one mixed trailer, resolved by the key's own regime) is the same protocol answering
two ways.  See `_Evaluator.b`.

A mixed trailer's score is one SUM in hours, with no per-channel coefficient anywhere:
the objective is put + pick hours from the shared cost model, so a fulfillment hour and a
store hour are worth the same to the site.  That is a CLAIM, not a convention, and
`Tests/unit/test_gain_plan.py` recovers the exchange rate from priced loads and asserts
it is 1 — with a planted per-channel weight to prove the recovery can fail.  The two
regimes' genuinely different pick costs (`wp.by_regime`) are not a counter-example: both
are seconds of the same model, scalarized with one divisor rather than two.

Exhaustion resolves tiers over the `SpaceView` keys the way `_candidates_raw` does:
smallest non-empty fitting tier first, spilling UP (the injected `tier_ranks_for`
carries the tier tables).  Only past total exhaustion does a unit price at the worst
bin of its whole spill chain x 1.5 — a deterministic, knob-free penalty that makes
"seats next drain but not now" defer correctly and cancels when nothing seats anywhere.

# ── purity, and where the pieces come from ────────────────────────────────────────

An entry reads ONLY the frozen ctx: `ctx.space` (the drain's SpaceView) and `ctx.gain`
(the driver-injected bundle, put there by `YardTransit.freeze_ctx`).  It mutates no
manager state, consumes no RNG, and returns a permutation (`bounded_order` raises
otherwise).  Cost-model pricing imports from `wh_kernel` — a legal edge: put travel is
paid once at the put crew's speeds; pick pays E[visits] x (travel + per_pick at height),
E[visits] = quantity / per-visit draw.  No new knobs anywhere in the score; the two
days-denominated knobs below belong to the GATE, which never blends them into hours.

# ── the urgency gate (`gain_gated`) ───────────────────────────────────────────────

`gain_forecast` behind a FIFO gate: trailers within `INBOUND_URGENCY_HORIZON_DAYS` of
crossing `INBOUND_FEE_THRESHOLD_DAYS` of yard time form the URGENT SET, served FIFO
ahead of everyone; the rest follow the plan, which prices its space AFTER the urgent
loads consume.  Hours and days never combine into one scalar — the gate IS the only
legal composition (05's two-separate-scores rule).  The horizon spans the poles: 0 ~
pure gain (only already-overdue trailers jump), >= threshold = pure FIFO.

# ── the futuresight window (`futuresight`) ────────────────────────────────────────

`gain_forecast` reading `ctx.space.window` — the declared-unlawful CLAIRVOYANCE
REFERENCE ("Define the inbound objective" decision 6; the term sharpened by "Decide the
futuresight family's place", 32): a real WMS cannot see undispatched orders, so this arm
never enters the recommendable set.

NOT an upper bound, and the difference is publishable rather than pedantic.  The window
replaces the demand RATE inside an UNCHANGED greedy, so w=inf prices each unit's future
picks exactly while the ORDERING stays a heuristic — it bounds PRICING ACCURACY, never
achievable gain.  A reference arm may therefore finish BEHIND a lawful one, which is a
finding (the binding constraint is the ordering, not the estimate) and not a pathology;
"the lawful arm is at the ceiling" is not a reading this arm supports in either
direction.  The window is
the next `INBOUND_FUTURESIGHT_BATCHES` script batches' realized demand ('all' = the
oracle w=inf), driver-fed on its own view slot; the batch script is i.i.d. draws from
the static rates, so the edge is exactly SAMPLING-NOISE knowledge — which SKUs land,
and their counts.  The build reads it where the static rates otherwise stand, the
PRICING: a SKU absent from the window is not picked in the visible future (put
travel only); a present one prices at its realized per-event draw (window total /
window events) with visits capped at the event count, which is what makes w=inf
honestly the oracle.  The placement MACHINERY stays the arm's own, static rates and
all (faithful-to-arm, 10 decision 5: the real pool cannot see the future, so a
clairvoyant virtual pool would price placements the arm will never make).  The
deferral pool is `gain_forecast`'s — predicted stays one batch deep by charter; the
window never projects bins.
"""
from __future__ import annotations

from collections import defaultdict
from heapq import merge as _hmerge
from math import isclose

from Inbound.priorities import DOCK_POLICIES, POLICY_VIEW_NEEDS, YARD_POLICIES, ordering

from Warehouse.kernel.cost_model import height_multiplier, per_pick
from Warehouse.kernel.regime import REGIMES, regime_of_key
from Warehouse.kernel.timeline import SECONDS_PER_DAY
from Warehouse.operations.putaway import put_seconds_at

#: The entry names that need a driver-injected `GainBundle` on the transit.
#: `futuresight` ADDITIONALLY needs the window feed — the driver refuses at startup
#: when its knob is unset or the precomputed script is missing (never silent inline
#: window sampling), so membership here covers only the bundle half.
GAIN_POLICIES: frozenset = frozenset({'gain_myopic', 'gain_forecast', 'gain_gated',
                                      'futuresight'})

#: The placement families `_gain_bundle_for` can build a FAITHFUL bundle for — the other side
#: of the same seam.  A gain policy run against any other restock rule refuses loudly rather
#: than pricing a fiction under that arm's name, so this tuple is the phase-2 selection's
#: constraint: a chosen rule outside it needs the evaluator EXTENDED before it can be swept.
#:
#: Declared here rather than in the driver so the funnel's selector can read it without
#: importing the simulation, and so there is one list rather than a dispatch chain and a
#: remembered copy.  `Tests/unit/test_restock_selection.py` pins it against what the driver
#: actually accepts.
#:
#: `fifo` leads because it is the one entry that is not optional: 08 makes it a mandatory
#: phase-2 rider, and a gain cell builds a bundle for EVERY arm in its set, so without it
#: all five gain cells refuse at worker startup (ticket 21).
#: The three after the ranked pair were added by ticket 20, from the ranking phase 1
#: actually produced (`restock_selection.json`): 08's extension cap of three, exactly
#: consumed, chosen by the selector rather than by the guess this ticket was charted with.
FAITHFUL_GAIN_FAMILIES: tuple[str, ...] = ('fifo', 'tmin', 'tmax',
                                           'rank_popularity', 'rank_random',
                                           'rank_minlabor', 'rank_labor',
                                           'rank_cartlabor')

# ── the seconds->days divisor: IMPORTED, never restated ───────────────────────────
# `SECONDS_PER_DAY` comes from `Warehouse.kernel.timeline` at the top of this module and
# was declared HERE, as a bare `86400.0`, until "Pin the day divisor" (30).  The gate below
# and the fee metric (`frames._ydf`) are the "one knob, two readers" `settings.py` promises
# can never disagree about overdue -- and each was converting seconds to days from its own
# literal, which is the `units.py` failure recurring in a new place.
#
# They cannot import each other: `{forbid: [inbound, evaluations]}` is the rule and it is
# the right one, since receiving must not depend on analysis.  So the shared declaration
# sits in `wh_kernel`, which both may read, and `Tests/unit/test_gain_plan.py` section 4b
# hands the same span to both readers and fails when they disagree.
#
# A CALENDAR day, three times the site day a batch is measured in; `timeline` declares the
# pair side by side and carries the note on which is which.

#: The two days-denominated knobs' defaults, for the same reason the divisor above is
#: imported: they had a literal here AND in `settings.py`, which is two numbers for one
#: meaning.  The declaration is HERE because this package may not import the run harness
#: ({forbid: [inbound, optimization]}), so `settings.INBOUND_FEE_THRESHOLD_DAYS` and
#: `INBOUND_URGENCY_HORIZON_DAYS` read these.  CALENDAR days, like the divisor.
DEFAULT_FEE_THRESHOLD_DAYS: float = 2.0
DEFAULT_URGENCY_HORIZON_DAYS: float = 0.0


# ── the purity rule, stated once per aisle dict ───────────────────────────────────────
# A virtual placement may never advance the LIVE bookkeeping, so the pool adapter opens
# the arm's pool over COPIES.  What "a copy" IS belongs to the DICT, not to the arm that
# reads it -- two families sharing a dict share its copier -- so it is declared here,
# once, keyed by the manager attribute the driver hands over.
#
# The shapes are not interchangeable and the wrong one fails SILENTLY: `dict(d)` over
# `aisle_member_pos` hands the pool the live inner LISTS, and `_MinLaborPool.take`
# appends a column position to one of them -- no error, no symptom, and every later
# placement in the RUN priced against a warehouse that never happened.  So a name with no
# entry here is refused at bundle construction rather than copied by a guess.
#
# Every copy is a `defaultdict` with the factory `Inventory_Manager` declares, because the
# pools index and `+=` an aisle they have not seen (`_ads[aid] += fq`,
# `_amp[aid][idx].append(...)`) exactly as they do against the live dicts.


# ── copy-on-write, because the eager copy is ~40x larger than the pool needs ───────────
#
# MEASURED, on the real driver at 2,000 SKUs / 20 batches / one leaf, 5,534 pool opens:
#
#     live aisles in aisle_sku_sets  : 46
#     aisles a pool can TOUCH        : mean 1.22, max 3   (1:4726  2:412  3:396)
#     set entries copied per open    : mean 1,683
#     total set entries copied       : 9,313,598
#
# A pool's candidates are the free bins of ONE BinKey, which live in one or two aisles; the
# eager copy walks all forty-six.  The ratio is 37.8x and it is paid `entries x T x tiers`
# times -- 12.59 pool opens per placement, against a yard depth that production never takes
# above 6.  So this is the inbound evaluator's real cost, and it is NOT the O(T^2) candidate
# loop it was assumed to be.
#
# The classes below preserve the purity rule exactly: a virtual placement still never
# advances the live bookkeeping.  They only stop paying for aisles it never looks at.
#
# WHERE THE LAZINESS STOPS, deliberately.  `values()` and `items()` materialize EVERY aisle,
# because a lazy one would hand out the LIVE container and the caller could mutate it -- the
# precise failure the eager copy exists to prevent.  `_RankedAssignPool.__init__` builds
# `set().union(*aisle_idx_sets.values())`, so `rank_random` (its only phase-2 user) gets
# correctness and no win.  Every other ranked family reads by key and gets both.


class _CowView:
    """Copy-on-write over one of the manager's `{aisle: ...}` dicts.

    A virtual placement must never advance the live bookkeeping, and the original way to
    guarantee that was to hand the pool an EAGER copy of the whole dict.  The measurement in
    front of this block is why that stopped: a pool's candidates are the free bins of ONE
    BinKey, living in one or two aisles, while the copy walked all forty-six -- 9.3M set
    entries copied per run, at 12.59 pool opens per placement.

    A view pays for the aisles the pool actually touches instead.  Reads fall through to the
    live dict; the first access to an aisle whose value is MUTABLE materializes that one
    aisle into an overlay, and every write lands in the overlay.  The purity rule is
    unchanged -- it is the price that moved.

    # -- what a subclass owns, and what it does not --------------------------------------

    `__getitem__` ONLY.  How a miss materializes IS the difference between the shapes: a
    float needs no copy at all (it is immutable, so a read can fall straight through), a set
    needs its own set, and the minlabor shape needs a whole inner mapping.  The remaining
    eight methods never differed, and were three byte-identical copies until this base
    existed; `Tests/unit/test_gain_cow_protocol.py` asserts they stay one implementation and
    that `__getitem__` stays three.

    # -- where the laziness stops, deliberately -------------------------------------------

    `values()` and `items()` materialize EVERY aisle, because a lazy one would hand out the
    LIVE container and the caller could mutate it -- the precise failure the eager copy
    existed to prevent.  `_RankedAssignPool.__init__` builds
    `set().union(*aisle_idx_sets.values())`, so `rank_random` (its only phase-2 user) gets
    correctness and no win.  Every other ranked family reads by key and gets both.

    Slotted, and the subclasses re-declare `__slots__ = ()` to stay that way: a view is
    opened per pool, and a stray attribute would be per-pool heap nobody frees.
    """

    __slots__ = ('_live', '_over')

    def __init__(self, live):
        self._live = live
        self._over = {}

    def __getitem__(self, k):
        raise NotImplementedError('a CoW view subclass owns how a miss materializes')

    def __setitem__(self, k, v):
        self._over[k] = v

    def get(self, k, default=None):
        """Read WITHOUT materializing -- which is the whole reason a pool ever calls it.

        The fall-through hands back the LIVE value for a key the overlay has not taken.
        That is read-only by contract: a caller that mutates what `get` returned writes
        into the manager's own dict.  Callers that intend to write use `__getitem__`.
        """
        o = self._over
        return o[k] if k in o else self._live.get(k, default)

    def __contains__(self, k):
        return k in self._over or k in self._live

    def __iter__(self):
        live = self._live
        for k in live:
            yield k
        for k in self._over:
            if k not in live:
                yield k

    def __len__(self):
        return len(self._live) + sum(1 for k in self._over if k not in self._live)

    def keys(self):
        return list(self)

    def items(self):
        return [(k, self[k]) for k in self]     # materializes -- see the class docstring

    def values(self):
        return [self[k] for k in self]          # materializes -- see the class docstring


class _CowFloats(_CowView):
    """`{aisle: float}` -- and floats need NO copy at all.

    A float is immutable, so a read falls straight through to the live dict; only a write
    needs an overlay.  `_ads[aid] += fq` is `__getitem__` then `__setitem__`, and the write
    lands in the overlay rather than on the manager's dict.

    A miss CREATES the entry at 0.0, matching `defaultdict(float)` -- the shape this
    replaces -- so `len()` and iteration order are unchanged.
    """

    __slots__ = ()

    def __getitem__(self, k):
        o = self._over
        if k in o:
            return o[k]
        v = self._live.get(k)
        if v is None and k not in self._live:
            o[k] = 0.0                      # defaultdict(float) creates on access
            return 0.0
        return v


class _CowSets(_CowView):
    """`{aisle: set}`.

    Unlike floats the value is MUTABLE and the caller does `d[aid].add(sku)` -- and
    `__getitem__` cannot tell that from the `sku in d[aid]` two lines above it.  So an
    access materializes that ONE aisle's set.  Bounded by the aisles a pool touches
    (measured 1.22), not by the warehouse (46).
    """

    __slots__ = ()

    def __getitem__(self, k):
        o = self._over
        got = o.get(k)
        if got is None:
            src = self._live.get(k)
            got = o[k] = set(src) if src is not None else set()
        return got


class _CowListsByKey(_CowView):
    """`{aisle: {sku_idx: [x_phys, ...]}}` -- the minlabor shape.

    Two levels down, and the inner lists are appended to (`_amp[aid][idx].append(...)`), so
    a shallow copy would hand the pool the live inner list.  An access materializes that one
    aisle's whole inner mapping, which is still one of forty-six.
    """

    __slots__ = ()

    def __getitem__(self, k):
        o = self._over
        got = o.get(k)
        if got is None:
            src = self._live.get(k)
            inner = defaultdict(list)
            if src is not None:
                for ik, iv in src.items():
                    inner[ik] = list(iv)
            got = o[k] = inner
        return got


def _copy_of_sets(d):
    out = defaultdict(set)
    for a, v in d.items():
        out[a] = set(v)
    return out


def _copy_of_floats(d):
    return defaultdict(float, d)


def _copy_of_lists_by_key(d):
    """Two levels down: {aisle: {sku_idx: [x_phys, ...]}}, and the lists are appended to."""
    out = defaultdict(lambda: defaultdict(list))
    for a, inner in d.items():
        o = out[a]
        for k, xs in inner.items():
            o[k] = list(xs)
    return out


#: manager attribute -> the EAGER copy of it.  Kept as the frozen reference the copy-on-write
#: views are tested against (`Tests/unit/test_gain_cow_equivalence.py`), and as the fallback a
#: caller can ask for explicitly.  Production no longer opens pools over these -- see
#: `AISLE_VIEWS` below and the measurement in front of `_CowView`.
AISLE_COPIERS = {
    'aisle_sku_sets':      _copy_of_sets,
    'aisle_idx_sets':      _copy_of_sets,
    'aisle_demand_sum':    _copy_of_floats,
    'aisle_pick_load_sum': _copy_of_floats,
    'aisle_vol_sum':       _copy_of_floats,
    'aisle_member_pos':    _copy_of_lists_by_key,
}

#: manager attribute -> how a virtual placement's VIEW of it is made.  Same six names, same
#: shapes, same purity rule; the difference is that a view pays for the aisles the pool touches
#: instead of every aisle in the warehouse.
#:
#: The two tables must cover each other exactly.  A name that has a copier and no view would
#: silently fall back to the eager path and quietly cost 40x; a name with a view and no copier
#: would have no oracle to be tested against.  Asserted immediately below, at import.
#:
#: That check says the tables agree WITH EACH OTHER; it cannot say they cover every book a
#: placement policy writes, because `Inbound/` may not import the placement engine.  The
#: DRIVER makes that comparison instead -- `strategy_runner` checks this table against
#: `AisleLedger.POLICY_BOOKS` at import, and refuses to load if a writable book has no view.
AISLE_VIEWS = {
    'aisle_sku_sets':      _CowSets,
    'aisle_idx_sets':      _CowSets,
    'aisle_demand_sum':    _CowFloats,
    'aisle_pick_load_sum': _CowFloats,
    'aisle_vol_sum':       _CowFloats,
    'aisle_member_pos':    _CowListsByKey,
}

if set(AISLE_VIEWS) != set(AISLE_COPIERS):          # at import, not at the first drain
    raise RuntimeError(
        f'AISLE_VIEWS and AISLE_COPIERS name different dicts: '
        f'{sorted(set(AISLE_COPIERS) ^ set(AISLE_VIEWS))}. Every aisle dict needs both -- the '
        f'view is what production opens pools over, the copier is the oracle it is proven '
        f'against.')


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


class _Evaluator:
    """One plan's virtual placement state — built per entry call, dies with it.

    Owns the sort-once structures (per-key bins in arm-D order, computed lazily the
    first time a key is touched and NEVER rebuilt — the structure the Tier-1 sabotage
    test perturbs), the consumed-bin set the greedy advances, and the pricing.  Reads
    the frozen `SpaceView` and the bundle provider; its only writes are its own
    bookkeeping.

    # ── the owner cursor, and why ONE evaluator can serve two leaves ──────────────

    `b` is not a field: it resolves `provider.for_key(self._key)`, and `_key` is set in
    `_params` — which `place_load` calls exactly once per BinKey group, before it
    branches on the adapter, so every read that follows a group's `_params` sees that
    group's arm.  Under `OneOwnerBundle` that is one instance for every key and the
    indirection is inert; under `SiteGainBundle` it is the WHOLE dispatch, at the
    granularity the loop already has.

    The property this rests on: **a spill chain never crosses regimes.**  `_chain`
    varies only `size`, holding `handling` / `category` / `unit_category` fixed, and
    regime is read off exactly those three (`inventory_common`: regime is itself a
    BinKey component).  Every other cache here — `_sorted_now`, `_sorted_pred`, `_wp`,
    `_chain_cache`, `_worst`, `_mom`, `taken` — is BinKey- or bin-id-keyed.  So two
    owners share this object with zero cross-talk, and nothing here needs to know which
    of them it is serving.  Take that property away and the caches, not the cursor, are
    what goes wrong.

    One read precedes any cursor: `place_load` calls `self.b.binkey_of(u)` to key the
    groups in the first place, with `_key` still None.  That is lawful because the
    three site-wide fields (`binkey_of`, `tier_ranks_for`, `put_speed`) are the same
    object for every owner — a provider that made THEM vary per key would be answering
    a different question than this evaluator asks.
    """

    __slots__ = ('_site', '_key', 'space', 'taken', 'unseated',
                 '_sorted_now', '_sorted_pred', '_wp', '_chain_cache', '_worst',
                 '_wr', '_mom')

    def __init__(self, bundle, space, window_rates=None):
        if not hasattr(bundle, 'for_key'):
            raise TypeError(
                f'the evaluator resolves its arm machinery per BinKey owner and always '
                f'goes through for_key() — {type(bundle).__name__} has none.  A '
                f'single-channel run wraps its one GainBundle in OneOwnerBundle (the '
                f'driver does that at injection); there is deliberately no second path '
                f'that reads a bundle directly')
        self._site = bundle
        #: The owner of the group being priced — the cursor `_params` advances.  None
        #: until the first group is keyed; see the class docstring.
        self._key = None
        self.space = space
        #: {sku: (window total, window events)} for the futuresight entry, None for
        #: every lawful arm — swaps the static rates out of `_pair_cost` only.
        self._wr = window_rates
        #: id(bin) -> consumed by a chosen (or forced) load's now-placement.
        self.taken: set = set()
        #: units priced past total exhaustion — observability, nothing reads it back.
        self.unseated = 0
        self._sorted_now: dict = {}     # key -> bins in arm-D order (merge adapter)
        self._sorted_pred: dict = {}
        self._wp: dict = {}             # own BinKey -> (wp, x_pace, y_pace)
        self._chain_cache: dict = {}    # own BinKey -> spill chain (ascending tiers)
        self._worst: dict = {}          # chain head -> worst bin over the whole chain
        self._mom: dict = {}            # (key, predicted, brackets) -> tier means

    # ── the owner cursor ──────────────────────────────────────────────────────────
    @property
    def b(self) -> GainBundle:
        """The arm machinery owning the group being priced (class docstring)."""
        return self._site.for_key(self._key)

    # ── per-key parameters ────────────────────────────────────────────────────────
    def _params(self, unit, own_key):
        # THE CURSOR, unconditionally and ahead of the memo: `b` is a per-owner
        # resolution from here to the end of this group, and the memo below must not
        # be able to skip it (a second group with the same wp would then price
        # against the PREVIOUS group's arm).
        self._key = own_key
        got = self._wp.get(own_key)
        if got is None:
            wp = self.b.wp_of(unit)
            sp = wp.speed
            got = (wp, sp.x_pace, sp.y_pace)
            self._wp[own_key] = got
        return got

    def _chain(self, unit, own_key):
        """The unit's tier spill chain, smallest fitting tier first — exactly
        `_candidates_raw`'s walk, over the injected tier tables."""
        got = self._chain_cache.get(own_key)
        if got is None:
            cat = unit.unit_category
            if cat == 'singleton':
                got = (own_key,)
            else:
                ranks, sizes_desc = self.b.tier_ranks_for(cat)
                min_rank = ranks.get(unit.storage_size, 0) if unit.storage_size else 0
                shc = unit.order.storage_handle_config
                got = tuple((shc.handling, shc.category, size, cat)
                            for size in reversed(sizes_desc)
                            if ranks[size] >= min_rank)
            self._chain_cache[own_key] = got
        return got

    # ── pricing ───────────────────────────────────────────────────────────────────
    def _cost_at(self, unit, x, y, hm, wp, xk, yk) -> float:
        """put travel (paid once) + E[visits] x (pick travel + per_pick at height),
        read at a LOCATION (x, y) with height multiplier `hm`.
        A zero demand rate means the unit is never picked: its pick term is ZERO
        (not quantity visits, the maximum possible reading); put is still paid.

        Under window rates (the futuresight entry) the SKU's REALIZED window demand
        stands where the static rate stands: absent from the window = not picked in
        the visible future, put only; present = per-event draw total/events, visits
        capped at the event count (a unit cannot be visited more often than demand
        events exist — the cap is what makes w=inf honestly the oracle).

        `hm` None reads the bracket step at `y` — a real bin, the only caller that
        existed before the uniform adapter.  A VALUE is a tier's MEAN multiplier, and
        passing one is exactly why the expectation below is exact: this expression is
        AFFINE in x, y and hm, so its mean over a bin set equals its value at the
        set's means.  The step function is evaluated per bin when the moments are
        taken, never on an averaged y (which would be a different, wrong number).
        The sentinel keeps the bracket walk off the put-only path, where it would be
        computed and thrown away for every never-picked unit."""
        # THE SAME EXPRESSION THE SIMULATION BILLS (`putaway.put_seconds_at`), with the
        # handling term dropped BY NAME rather than by not writing it.  `cost=None` is the
        # simplification this module's header records -- put travel paid once at the put
        # crew's speeds -- and `Tests/unit/test_put_seconds_at.py` pins the exact
        # relationship to the billed reading, so the two can no longer part company
        # silently.  Dropping the handling term also drops the HEIGHT MULTIPLIER, which is
        # bin-dependent and therefore does NOT cancel out of a difference between two
        # candidate bins; that is the part worth revisiting, and ticket 16 records it.
        ps = self.b.put_speed
        put = put_seconds_at(x, y, speed=ps, cost=None)
        order = unit.order
        wr = self._wr
        if wr is not None:
            got = wr.get(order.sku)
            if got is None:
                return put
            total, hits = got
            q = total / hits          # >= 1 by construction: batch draws floor at 1
            visits = max(1.0, min(unit.quantity / q, float(hits)))
        else:
            # A zero demand RATE is this evaluator's never-picked sentinel (put only; see
            # the docstring) -- an ARRIVAL fact, not a line quantity.  Production catalogues
            # clamp the rate to >= 1, so it fires only in tests.
            if order.demand.quantity_rate <= 0:
                return put
            # E[units per line] off the SKU's stamped law (>= 1 by construction, like the
            # measured branch above) -- never the rate scalar re-read as a line's units.
            q = order.demand.line.mean()
            visits = max(1.0, unit.quantity / q)
        if hm is None:
            hm = height_multiplier(wp.height_brackets, y)
        # The full at-location model, per-item charge included: what the sim will bill
        # at this bin is what the load is priced at (a sabotage test pins that zeroing
        # `wp.pick_per_item` moves the priced hours).
        at_bin = per_pick(hm, wp.pick_intercept, order.handle_var, q, wp.pick_per_item)
        return put + visits * (xk * x + yk * y + at_bin)

    def _pair_cost(self, unit, bin_, wp, xk, yk) -> float:
        """`_cost_at` read at one real bin — the merge and pool adapters' pricing."""
        return self._cost_at(unit, bin_.x_phys, bin_.y_phys, None, wp, xk, yk)

    def _moments(self, key, predicted: bool, brackets):
        """(mean x, mean y, mean height multiplier) over a tier as FROZEN — the three
        numbers the uniform expectation reads, computed once per (tier, source,
        brackets) and never rebuilt.

        Over the FULL frozen tier, never the excluded-filtered one.  The bins another
        load consumed are a uniformly random subset under this arm, so what is left
        has the same mean in expectation; filtering them out would bias the price by
        exactly the thing the arm does not choose on.  Only the COUNT is filtered
        (`_useat`).  Keyed by the height brackets because the multiplier is the one
        moment that is regime-specific.  Callers only ask for a tier they know is
        non-empty, so there is no empty-set division here."""
        ck = (key, predicted, brackets)
        got = self._mom.get(ck)
        if got is None:
            src = (self.space.predicted if predicted
                   else self.space.empties).get(key, ())
            inv = 1.0 / len(src)
            got = self._mom[ck] = (
                sum(b.x_phys for b in src) * inv,
                sum(b.y_phys for b in src) * inv,
                sum(height_multiplier(brackets, b.y_phys) for b in src) * inv)
        return got

    def _unseated_cost(self, unit, chain, wp, xk, yk) -> float:
        """Past total exhaustion: worst bin over the WHOLE spill chain (both tiers,
        availability-blind so both gain terms price it identically) x 1.5; zero when
        the chain offers no bin anywhere, which then cancels out of the gain."""
        self.unseated += 1
        key0 = chain[0]
        if key0 in self._worst:
            worst = self._worst[key0]
        else:
            worst, wd = None, None
            emp, pred = self.space.empties, self.space.predicted
            for k in chain:
                for b in tuple(emp.get(k, ())) + tuple(pred.get(k, ())):
                    d = xk * b.x_phys + yk * b.y_phys
                    if wd is None or d > wd:
                        worst, wd = b, d
            self._worst[key0] = worst
        if worst is None:
            return 0.0
        return 1.5 * self._pair_cost(unit, worst, wp, xk, yk)

    # ── availability (sort-once / slice-under-consumption) ────────────────────────
    def _tier_sorted(self, key, xk, yk, predicted: bool) -> list:
        cache = self._sorted_pred if predicted else self._sorted_now
        got = cache.get(key)
        if got is None:
            src = (self.space.predicted if predicted else self.space.empties).get(key, ())
            got = sorted(src, key=lambda b: xk * b.x_phys + yk * b.y_phys,
                         reverse=not self.b.minimize)
            cache[key] = got
        return got

    def _avail(self, key, excluded, xk, yk, predicted, cache):
        """One placement's view of a tier: the pre-sorted arrays filtered by the
        excluded set, plus a cursor — shared across the placement's groups so spill
        into an already-touched tier continues where consumption left off."""
        got = cache.get(key)
        if got is None:
            dk = lambda b: xk * b.x_phys + yk * b.y_phys
            lst = [b for b in self._tier_sorted(key, xk, yk, False)
                   if id(b) not in excluded]
            if predicted:
                pred = [b for b in self._tier_sorted(key, xk, yk, True)
                        if id(b) not in excluded]
                lst = list(_hmerge(lst, pred, key=dk, reverse=not self.b.minimize))
            got = [lst, 0]
            cache[key] = got
        return got

    # ── one virtual placement ─────────────────────────────────────────────────────
    def place_load(self, units, excluded, predicted: bool, *, alloc=None):
        """(cost, takes) of placing `units` against the availability that `excluded`
        leaves standing; `predicted` merges the deferral tier in.  Groups by the
        unit's OWN BinKey in load order (the canonical pack order is deterministic),
        spilling up the chain per group.

        `alloc` is the sweep's shared block allocator, read by the uniform adapter
        ONLY (see `_place_uniform`) and inert for the other two.  None gives this
        placement its own, which is what the forced prefix and the deferral side
        want: each of those stands alone, against an `excluded` that already carries
        whatever went before it."""
        cost = 0.0
        takes: list = []
        avail_cache: dict = {}
        if alloc is None:
            alloc = {}
        groups: dict = {}
        order: list = []
        for u in units:
            k = self.b.binkey_of(u)
            g = groups.get(k)
            if g is None:
                groups[k] = g = []
                order.append(k)
            g.append(u)
        for k in order:
            gunits = groups[k]
            wp, xk, yk = self._params(gunits[0], k)
            chain = self._chain(gunits[0], k)
            if self.b.uniform:
                c, tk = self._place_uniform(gunits, chain, wp, xk, yk,
                                            excluded, predicted, avail_cache, alloc)
            elif self.b.pool_factory is not None:
                c, tk = self._place_pool(gunits, chain, wp, xk, yk,
                                         excluded, predicted, avail_cache)
            else:
                c, tk = self._place_merge(gunits, chain, wp, xk, yk,
                                          excluded, predicted, avail_cache)
            cost += c
            takes.extend(tk)
        return cost, takes

    def _place_merge(self, gunits, chain, wp, xk, yk, excluded, predicted, cache):
        """The k-cheapest merge: units in freq x labor_cost priority order take the
        class's extremal-D available bins in order (the pool's priority minus the
        co-occurrence term — the proven residue)."""
        ordered = sorted(gunits, key=lambda u: -(u.order.demand.relative_frequency
                                                 * u.order.labor_cost))
        cost = 0.0
        takes: list = []
        ci, slot = 0, None
        for u in ordered:
            b = None
            while b is None:
                if slot is None:
                    if ci >= len(chain):
                        break
                    slot = self._avail(chain[ci], excluded, xk, yk, predicted, cache)
                lst, cur = slot
                if cur < len(lst):
                    b = lst[cur]
                    slot[1] = cur + 1
                else:
                    slot = None
                    ci += 1
            if b is None:
                cost += self._unseated_cost(u, chain, wp, xk, yk)
            else:
                takes.append(b)
                cost += self._pair_cost(u, b, wp, xk, yk)
        return cost, takes

    def _useat(self, key, excluded, predicted, cache):
        """One placement's view of a tier for the uniform adapter:
        `[seats, predicted weight, the now-list]`.

        Both numbers are fixed at first touch and never recomputed as the tier is
        consumed.  The units draw from ONE pool — the empties merged with whatever
        predicted clears the arm may see — and drawing uniformly from a mixed pool
        leaves its mix proportional in expectation, so the weight does not drift.
        What consumption changes is the SEAT COUNT, and that is the whole mechanism:
        it is the only way an inbound ordering can move a uniform arm's cost."""
        got = cache.get(('unif', key))
        if got is None:
            nowl = [b for b in self.space.empties.get(key, ())
                    if id(b) not in excluded]
            n_pred = 0
            if predicted:
                n_pred = sum(1 for b in self.space.predicted.get(key, ())
                             if id(b) not in excluded)
            total = len(nowl) + n_pred
            got = cache[('unif', key)] = [total,
                                          (n_pred / total) if total else 0.0,
                                          nowl]
        return got

    def _place_uniform(self, gunits, chain, wp, xk, yk, excluded, predicted, cache,
                       alloc):
        """The uniform adapter: `fifo`'s draw, priced by its EXACT expectation and
        consumed as a seat count.

        `_uniform_assignment` picks uniformly from the whole tier `_candidates_raw`
        hands it, so (a) every unit's bin is marginally uniform over that tier as
        frozen — sequential draws WITHOUT replacement leave the marginal untouched,
        which is what makes this an expectation rather than an approximation — and
        (b) which bin a unit got is worth nothing to the next one.  Units therefore
        keep LOAD order here: a uniform draw has no precedence to sort by (contrast
        `_place_merge`, which sorts by the pool's priority).

        The reported takes come from `alloc`, the sweep's shared block allocator, so
        two candidates claim the same bin only once a tier is oversubscribed.  That is
        not bookkeeping taste: the cost above is identity-blind, so identities exist
        ONLY to feed `plan_order`'s leftover model, which unions the OTHER candidates'
        takes.  Independent uniform draws essentially never collide, so hand every
        candidate the same front-of-list bins and that union collapses to one load's
        worth — pricing a whole yard's contention as a single trailer's.  Past the end
        the allocator WRAPS rather than truncating: an oversubscribed tier then marks
        its bins as shared (`n > 1`) for everyone, instead of leaving the last
        candidates empty-handed and making a later arrival look starved purely because
        an earlier one drew its block first.

        RESIDUE, named: the union is exact while a tier's demand fits in it, and exact
        again once every bin is claimed twice; between those it under-excludes,
        because the leftover model hands a candidate back the bins no OTHER
        candidate's block happened to name.  Uniform contention wants a COUNT and the
        seam speaks in identities; this is as close as that seam gets."""
        brackets = wp.height_brackets
        cost = 0.0
        takes: list = []
        idx = 0
        for u in gunits:
            slot = None
            while idx < len(chain):
                slot = self._useat(chain[idx], excluded, predicted, cache)
                if slot[0] > 0:
                    break
                slot, idx = None, idx + 1
            if slot is None:
                cost += self._unseated_cost(u, chain, wp, xk, yk)
                continue
            key = chain[idx]
            slot[0] -= 1
            w = slot[1]
            if w < 1.0:
                price = self._mean_cost(u, self._moments(key, False, brackets),
                                        wp, xk, yk)
                if w:
                    price = (1.0 - w) * price + w * self._mean_cost(
                        u, self._moments(key, True, brackets), wp, xk, yk)
            else:
                # Nothing empty NOW: the seat can only come from a predicted clear.
                price = self._mean_cost(u, self._moments(key, True, brackets),
                                        wp, xk, yk)
            cost += price
            # Takes are drawn from the now-list alone.  A seat the blend attributes to
            # a predicted clear has no bin to name yet, and it never needs one: only a
            # now-side placement's takes are ever read back (`plan_order` advances
            # `taken` from `predicted=False` calls and discards the deferral side's),
            # and on that side the predicted tier is not in the pool at all.
            nowl = slot[2]
            if nowl:
                cur = alloc.get(key, 0)
                takes.append(nowl[cur % len(nowl)])
                alloc[key] = cur + 1
        return cost, takes

    def _mean_cost(self, unit, mom, wp, xk, yk) -> float:
        """`_cost_at` read at a tier's means — the exact expectation of `_pair_cost`
        over that tier, by the affineness `_cost_at` documents."""
        ex, ey, ehm = mom
        return self._cost_at(unit, ex, ey, ehm, wp, xk, yk)

    def _make_pool(self, cands, wp):
        """The arm's own pool over COPY-ON-WRITE VIEWS of the aisle bookkeeping — the purity
        rule holds unchanged: a virtual placement may never advance the live dicts.

        One comprehension, per evaluation: the bundle's `aisle_state` says WHICH live
        dicts this arm's pool commits to and `AISLE_VIEWS` says how each is viewed, so
        a family that touches more of them costs a driver branch and nothing here.

        THIS IS THE PACKAGE'S HOTTEST LINE, and the reason is the TIER loop below rather than
        anything about yard depth.  Measured on the real driver: 12.59 pool opens per
        `place_load` against a mean of 2.8 candidate trailers, so the opens are driven by the
        spill chain, not by the greedy.  The eager copy walked all 46 live aisles to serve a
        pool that touches 1.22 of them -- 9.3 million set-element copies in twenty batches on
        one leaf.  A view pays for what it reads.
        """
        b = self.b
        state = {n: AISLE_VIEWS[n](d) for n, d in b.aisle_state.items()}
        return b.pool_factory(list(cands), state, wp)

    def _place_pool(self, gunits, chain, wp, xk, yk, excluded, predicted, cache):
        """The pool adapter: per tier, the arm's pool (over copies) serves the units
        in its own precedence; a unit the pool cannot seat spills up the chain."""
        b = self.b
        cost = 0.0
        takes: list = []
        rest = list(gunits)
        for key in chain:
            if not rest:
                break
            slot = cache.get(('pool', key))
            if slot is None:
                cands = [x for x in self.space.empties.get(key, ())
                         if id(x) not in excluded]
                if predicted:
                    cands += [x for x in self.space.predicted.get(key, ())
                              if id(x) not in excluded]
                slot = cache[('pool', key)] = (cands, set())
            bins, used = slot
            # MEASURED 8.9% OF THE RECEIVE DRAIN, REMOVING NOTHING.  `used` is only ever added to
            # by a take below, so it is non-empty exactly when a LATER BinKey group spills into a
            # tier this same placement already drew from.  Counted on the real driver over 7,426
            # pool opens: that happened ZERO times -- 8,130,323 elements scanned, none removed.
            #
            # Returning `bins` itself is safe rather than merely cheap: `live` is truthiness-
            # tested and then handed to `_make_pool`, which does `pool_factory(list(cands), ...)`
            # and copies. Nothing mutates it, so the fast path and the comprehension are the same
            # list in the same order -- the guard buys the scan back and changes no result.
            live = bins if not used else [x for x in bins if id(x) not in used]
            if not live:
                continue
            pool = self._make_pool(live, wp)
            nxt: list = []
            for u in pool.order(list(rest)):
                if b.expect_heads:
                    # heads_of returns the pool's live {aisle: head bin} dict.
                    vals = list(b.heads_of(pool).values())
                    if not vals:
                        nxt.append(u)
                        continue
                    price = sum(self._pair_cost(u, hb, wp, xk, yk)
                                for hb in vals) / len(vals)
                else:
                    price = None
                chosen, _score = pool.take(u)
                if chosen is None:
                    nxt.append(u)
                    continue
                used.add(id(chosen))
                takes.append(chosen)
                cost += price if price is not None \
                    else self._pair_cost(u, chosen, wp, xk, yk)
            rest = nxt
        for u in rest:
            cost += self._unseated_cost(u, chain, wp, xk, yk)
        return cost, takes


def _load_units(trailer) -> list:
    """The candidate's remaining planned load — pending[taken:], the plans-at-arrival
    contract.  A candidate with no plan is a wiring error, never a silent zero: in
    production every yard/staged trailer was planned (or discarded) before any
    ranking runs."""
    pend = trailer.pending
    if pend is None:
        raise ValueError(
            f'gain entry ranked trailer #{trailer.seq} with no pack plan — the gain '
            f'arms read pending (plans-at-arrival); rank after the site drain has '
            f'planned the yard, or set pending on the test trailer')
    return [item.unit for item in pend[trailer.taken:]]


def _window_rates(window) -> dict:
    """Aggregate the view's window into `{sku: (total qty, events)}` — the realized
    demand mass and the number of window batches that carry the SKU.  Once per entry
    call, so the pricing loop reads a dict, not w of them."""
    agg: dict = {}
    for d in window:
        for sku, q in d.items():
            got = agg.get(sku)
            agg[sku] = (q, 1) if got is None else (got[0] + q, got[1] + 1)
    return agg


def plan_order(candidates, bundle, space, *, predicted: bool,
               forced_prefix=(), window_rates=None,
               _ev: _Evaluator | None = None) -> list:
    """10's greedy over the frozen view.  `bundle` is the owner PROVIDER the evaluator
    resolves through (`OneOwnerBundle` on a single-channel run), never a bare
    `GainBundle`.  `forced_prefix` is the urgency gate's FIFO head — consumed first,
    unscored.  `window_rates` is the futuresight entry's
    aggregated window (see `_window_rates`), None for every lawful arm.  `_ev` exists
    ONLY for the Tier-1 sabotage test (a pre-warmed evaluator whose sorted structure
    the test perturbs); production callers never pass it."""
    if _ev is not None and window_rates is not None:
        raise ValueError(
            'plan_order got both a pre-built evaluator and window_rates: the hook '
            'evaluator carries its own pricing, so the window would be silently '
            'dropped (the fake-arm hazard) — build the evaluator with window_rates '
            'instead')
    ev = _ev if _ev is not None else _Evaluator(bundle, space,
                                                window_rates=window_rates)
    loads: dict = {}          # id(trailer) -> remaining planned units, derived once

    def _load(t):
        got = loads.get(id(t))
        if got is None:
            got = loads[id(t)] = _load_units(t)
        return got

    out: list = []
    for t in forced_prefix:
        _c, takes = ev.place_load(_load(t), ev.taken, False)
        ev.taken.update(map(id, takes))
        out.append(t)
    prefix_ids = {id(t) for t in out}
    remaining = [t for t in candidates if id(t) not in prefix_ids]
    while remaining:
        # The now side: every remaining load against the current pool.  One block
        # allocator per round, so a uniform bundle hands each candidate its OWN bins
        # and the leftover model below unions distinct take-sets rather than counting
        # one load's worth T times (`_place_uniform`).  Inert for the other adapters,
        # whose takes are whatever their arm's own preference lands on.
        swept: list = []
        counts: dict = {}
        alloc: dict = {}
        for t in remaining:
            c, tk = ev.place_load(_load(t), ev.taken, False, alloc=alloc)
            ids = set(map(id, tk))
            swept.append((t, c, tk, ids))
            for i in ids:
                counts[i] = counts.get(i, 0) + 1
        # The defer side: leftovers = what the OTHER candidates leave standing
        # (leave-one-out over the sweep's takes — the module note's deviation),
        # plus the predicted tier when the arm may see it.
        best = None
        for t, c, tk, ids in swept:
            others = {i for i, n in counts.items() if n > 1 or i not in ids}
            defer_c, _tk = ev.place_load(_load(t), ev.taken | others, predicted)
            g = defer_c - c
            if best is None or g > best[1]:
                best = (t, g, tk)
        t, _g, tk = best
        ev.taken.update(map(id, tk))
        out.append(t)
        remaining = [r for r in remaining if r is not t]
    return out


def _require(ctx, name: str):
    """A gain entry without its machinery is a wiring error, loudly — a silently
    inert arm would sweep as `fifo` and publish under its own name."""
    space = getattr(ctx, 'space', None)
    bundle = getattr(ctx, 'gain', None)
    if space is None:
        raise RuntimeError(f'{name} needs the drain\'s frozen SpaceView on ctx.space '
                           f'— it only runs under the standing yard, whose ctx-freeze '
                           f'delivers one')
    if bundle is None:
        raise RuntimeError(f'{name} needs a driver-injected GainBundle on ctx.gain '
                           f'(YardTransit.gain_bundle, wrapped in a OneOwnerBundle) — '
                           f'the arm bundle carries the faithful-to-arm placement '
                           f'machinery')
    return bundle, space


@ordering
def gain_myopic(candidates, ctx) -> list:
    """The unload plan over `ctx.space.empties` only — the predicted tier is
    invisible, so the arm's whole signal is contention for today's space."""
    bundle, space = _require(ctx, 'gain_myopic')
    return plan_order(candidates, bundle, space, predicted=False)


@ordering
def gain_forecast(candidates, ctx) -> list:
    """The unload plan with the deferral pool including `predicted` — the standing
    demand's projected clears, the next drain's pool gain."""
    bundle, space = _require(ctx, 'gain_forecast')
    return plan_order(candidates, bundle, space, predicted=True)


@ordering
def gain_gated(candidates, ctx) -> list:
    """`gain_forecast` behind the FIFO urgency gate (module note): the URGENT SET is
    served FIFO ahead of the plan; hours and days meet ONLY here.  A stampless
    trailer (no clock reached the drain) accrues no yard days and is never urgent."""
    bundle, space = _require(ctx, 'gain_gated')
    now = space.frozen_at
    due_days = bundle.fee_threshold_days - bundle.urgency_horizon_days
    urgent = [t for t in candidates
              if t.arrived_s is not None
              and (now - t.arrived_s) / SECONDS_PER_DAY >= due_days]
    urgent.sort(key=lambda t: (t.arrived_s, t.seq))
    return plan_order(candidates, bundle, space, predicted=True,
                      forced_prefix=urgent)


@ordering
def futuresight(candidates, ctx) -> list:
    """`gain_forecast` reading the window slot (module note) — the declared-unlawful
    CLAIRVOYANCE reference, never recommendable; it bounds pricing accuracy and NOT
    achievable gain, so it may legitimately finish behind a lawful arm (32).  An EMPTY
    window (a run at the end
    of its script) is legal and prices every pick term zero; a MISSING one (None)
    means no feed ran, and ranking anyway would silently be `gain_forecast` under
    this arm's name — the same fake-arm hazard `_require` exists for."""
    bundle, space = _require(ctx, 'futuresight')
    window = space.window
    if window is None:
        raise RuntimeError(
            'futuresight needs the window feed on ctx.space.window — the driver '
            'slices it from the precomputed batch script when the arm is named '
            '(INBOUND_FUTURESIGHT_BATCHES set, script present).  None means no feed '
            'ran; an empty window at the end of the script is (), which is legal')
    return plan_order(candidates, bundle, space, predicted=True,
                      window_rates=_window_rates(window))


# ── registration ──────────────────────────────────────────────────────────────────
# Into BOTH standing registries: an arm sets both knobs to one name (05).  Import-time
# registration rides the package __init__, so any consumer that can name a policy has
# these resolvable; the seeded 'fifo'/'lifo' keys are untouched.
for _registry in (YARD_POLICIES, DOCK_POLICIES):
    _registry['gain_myopic'] = gain_myopic
    _registry['gain_forecast'] = gain_forecast
    _registry['gain_gated'] = gain_gated
    _registry['futuresight'] = futuresight
del _registry

# ...AND THE VIEW FIELDS EACH ONE READS, in the same breath as the entry itself.  This is
# the arm's half of the coupled-composability join (`POLICY_VIEW_NEEDS`): the composer
# declares what a two-leaf composition carries, these declare what the entry opens, and
# `site_space.uncomposable_policies` is the only place the two meet.  Read off the entries
# above rather than guessed — `plan_order` touches `predicted` on every path, but a
# `predicted=False` arm is DEFINED not to see it (the docstring says so), and a need is
# what the policy's answer depends on, not what an inner frame happens to dereference.
POLICY_VIEW_NEEDS['gain_myopic'] = frozenset({'empties'})
POLICY_VIEW_NEEDS['gain_forecast'] = frozenset({'empties', 'predicted'})
#: `frozen_at` is the urgency gate's "now" — the one place hours and days meet (05).
POLICY_VIEW_NEEDS['gain_gated'] = frozenset({'empties', 'predicted', 'frozen_at'})
#: The window is the whole arm: `futuresight` RAISES on a None one rather than falling
#: back to `gain_forecast` under its own name, so a composition that dropped it silently
#: would not be a degraded arm, it would be a dead one.
POLICY_VIEW_NEEDS['futuresight'] = frozenset({'empties', 'predicted', 'window'})
