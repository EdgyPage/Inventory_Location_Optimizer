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

`gain_forecast` reading `ctx.space.window` — the declared-unlawful upper-bound
REFERENCE ("Define the inbound objective" decision 6): a real WMS cannot see
undispatched orders, so this arm never enters the recommendable set.  The window is
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

from Inbound.priorities import DOCK_POLICIES, YARD_POLICIES, ordering

from Warehouse.kernel.cost_model import height_multiplier, per_pick

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
FAITHFUL_GAIN_FAMILIES: tuple[str, ...] = ('fifo', 'tmin', 'tmax',
                                           'rank_popularity', 'rank_random')

_SECONDS_PER_DAY = 86400.0


class GainBundle:
    """Everything arm-specific the evaluator needs, injected by the driver.

    Three adapters, and the bundle picks exactly one.  `uniform` selects the uniform
    adapter (`fifo`: no pool, no direction — the tier's mean and a seat count).  Else
    `pool_factory` None selects the merge adapter (extremal-D family, direction
    `minimize`); otherwise the pool adapter calls
    `pool_factory(candidates, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, wp)`
    with FRESH copies of the three aisle dicts per evaluation.  `expect_heads` prices
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
                 'aisle_sku_sets', 'aisle_idx_sets', 'aisle_demand_sum',
                 'put_speed', 'wp_of', 'binkey_of', 'tier_ranks_for',
                 'fee_threshold_days', 'urgency_horizon_days')

    def __init__(self, *, put_speed, wp_of, binkey_of, tier_ranks_for,
                 minimize: bool = True, pool_factory=None, expect_heads: bool = False,
                 heads_of=None, uniform: bool = False, aisle_sku_sets=None,
                 aisle_idx_sets=None, aisle_demand_sum=None,
                 fee_threshold_days: float = 2.0,
                 urgency_horizon_days: float = 0.0):
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
        self.aisle_sku_sets = aisle_sku_sets if aisle_sku_sets is not None else {}
        self.aisle_idx_sets = aisle_idx_sets if aisle_idx_sets is not None else {}
        self.aisle_demand_sum = aisle_demand_sum if aisle_demand_sum is not None else {}
        self.put_speed = put_speed
        self.wp_of = wp_of
        self.binkey_of = binkey_of
        self.tier_ranks_for = tier_ranks_for
        self.fee_threshold_days = float(fee_threshold_days)
        self.urgency_horizon_days = float(urgency_horizon_days)


class _Evaluator:
    """One plan's virtual placement state — built per entry call, dies with it.

    Owns the sort-once structures (per-key bins in arm-D order, computed lazily the
    first time a key is touched and NEVER rebuilt — the structure the Tier-1 sabotage
    test perturbs), the consumed-bin set the greedy advances, and the pricing.  Reads
    the frozen `SpaceView` and the bundle; its only writes are its own bookkeeping.
    """

    __slots__ = ('b', 'space', 'taken', 'unseated',
                 '_sorted_now', '_sorted_pred', '_wp', '_chain_cache', '_worst',
                 '_wr', '_mom')

    def __init__(self, bundle: GainBundle, space, window_rates=None):
        self.b = bundle
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

    # ── per-key parameters ────────────────────────────────────────────────────────
    def _params(self, unit, own_key):
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
        ps = self.b.put_speed
        put = ps.x_pace * x + ps.y_pace * y
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
            q = order.demand.quantity_rate
            if q <= 0:
                return put
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
        """The arm's own pool over COPIES of the aisle bookkeeping — the purity rule:
        a virtual placement may never advance the live dicts."""
        b = self.b
        ass = defaultdict(set)
        for a, v in b.aisle_sku_sets.items():
            ass[a] = set(v)
        ais = defaultdict(set)
        for a, v in b.aisle_idx_sets.items():
            ais[a] = set(v)
        ads = defaultdict(float, b.aisle_demand_sum)
        return b.pool_factory(list(cands), ass, ais, ads, wp)

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
            live = [x for x in bins if id(x) not in used]
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
            f'arms read pending (plans-at-arrival); rank after _receive_standing has '
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
    """10's greedy over the frozen view.  `forced_prefix` is the urgency gate's FIFO
    head — consumed first, unscored.  `window_rates` is the futuresight entry's
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
                           f'(YardTransit.gain_bundle) — the arm bundle carries the '
                           f'faithful-to-arm placement machinery')
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
              and (now - t.arrived_s) / _SECONDS_PER_DAY >= due_days]
    urgent.sort(key=lambda t: (t.arrived_s, t.seq))
    return plan_order(candidates, bundle, space, predicted=True,
                      forced_prefix=urgent)


@ordering
def futuresight(candidates, ctx) -> list:
    """`gain_forecast` reading the window slot (module note) — the declared-unlawful
    upper-bound reference, never recommendable.  An EMPTY window (a run at the end
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
