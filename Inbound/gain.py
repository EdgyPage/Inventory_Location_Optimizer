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
"""
from __future__ import annotations

from collections import defaultdict
from heapq import merge as _hmerge

from Inbound.priorities import DOCK_POLICIES, YARD_POLICIES, ordering

from Warehouse.kernel.cost_model import height_multiplier, per_pick

#: The entry names that need a driver-injected `GainBundle` on the transit.  The
#: `futuresight` entry is NOT here — it rides its own ticket (13) with its own feed.
GAIN_POLICIES: frozenset = frozenset({'gain_myopic', 'gain_forecast', 'gain_gated'})

_SECONDS_PER_DAY = 86400.0


class GainBundle:
    """Everything arm-specific the evaluator needs, injected by the driver.

    `pool_factory` None selects the merge adapter (extremal-D family, direction
    `minimize`); otherwise the pool adapter calls
    `pool_factory(candidates, aisle_sku_sets, aisle_idx_sets, aisle_demand_sum, wp)`
    with FRESH copies of the three aisle dicts per evaluation.  `expect_heads` prices
    each unit at the mean over `heads_of(pool)` before consuming (rank_random's no-RNG
    expectation).  `wp_of` / `binkey_of` / `tier_ranks_for` are handed over because
    their home (`wh_inventory`) is a forbidden import — the broker rule.

    The two days-denominated knobs ride here because the gate entry has no other
    channel to CONFIG (`inbound_spec()` -> driver -> bundle, the five-seam path).
    """

    __slots__ = ('minimize', 'pool_factory', 'expect_heads', 'heads_of',
                 'aisle_sku_sets', 'aisle_idx_sets', 'aisle_demand_sum',
                 'put_speed', 'wp_of', 'binkey_of', 'tier_ranks_for',
                 'fee_threshold_days', 'urgency_horizon_days')

    def __init__(self, *, put_speed, wp_of, binkey_of, tier_ranks_for,
                 minimize: bool = True, pool_factory=None, expect_heads: bool = False,
                 heads_of=None, aisle_sku_sets=None, aisle_idx_sets=None,
                 aisle_demand_sum=None, fee_threshold_days: float = 2.0,
                 urgency_horizon_days: float = 0.0):
        if expect_heads and (pool_factory is None or heads_of is None):
            raise ValueError('expect_heads prices over the pool\'s aisle heads — it '
                             'needs both pool_factory and heads_of')
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
                 '_sorted_now', '_sorted_pred', '_wp', '_chain_cache', '_worst')

    def __init__(self, bundle: GainBundle, space):
        self.b = bundle
        self.space = space
        #: id(bin) -> consumed by a chosen (or forced) load's now-placement.
        self.taken: set = set()
        #: units priced past total exhaustion — observability, nothing reads it back.
        self.unseated = 0
        self._sorted_now: dict = {}     # key -> bins in arm-D order (merge adapter)
        self._sorted_pred: dict = {}
        self._wp: dict = {}             # own BinKey -> (wp, x_pace, y_pace)
        self._chain_cache: dict = {}    # own BinKey -> spill chain (ascending tiers)
        self._worst: dict = {}          # chain head -> worst bin over the whole chain

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
    def _pair_cost(self, unit, bin_, wp, xk, yk) -> float:
        """put travel (paid once) + E[visits] x (pick travel + per_pick at height).
        A zero demand rate means the unit is never picked: its pick term is ZERO
        (not quantity visits, the maximum possible reading); put is still paid."""
        ps = self.b.put_speed
        put = ps.x_pace * bin_.x_phys + ps.y_pace * bin_.y_phys
        order = unit.order
        q = order.demand.quantity_rate
        if q <= 0:
            return put
        visits = max(1.0, unit.quantity / q)
        hm = height_multiplier(wp.height_brackets, bin_.y_phys)
        at_bin = per_pick(hm, wp.pick_intercept, order.handle_var, q)
        return put + visits * (xk * bin_.x_phys + yk * bin_.y_phys + at_bin)

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
    def place_load(self, units, excluded, predicted: bool):
        """(cost, takes) of placing `units` against the availability that `excluded`
        leaves standing; `predicted` merges the deferral tier in.  Groups by the
        unit's OWN BinKey in load order (the canonical pack order is deterministic),
        spilling up the chain per group."""
        cost = 0.0
        takes: list = []
        avail_cache: dict = {}
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
            if self.b.pool_factory is not None:
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


def plan_order(candidates, bundle, space, *, predicted: bool,
               forced_prefix=(), _ev: _Evaluator | None = None) -> list:
    """10's greedy over the frozen view.  `forced_prefix` is the urgency gate's FIFO
    head — consumed first, unscored.  `_ev` exists ONLY for the Tier-1 sabotage test
    (a pre-warmed evaluator whose sorted structure the test perturbs); production
    callers never pass it."""
    ev = _ev if _ev is not None else _Evaluator(bundle, space)
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
        # The now side: every remaining load against the current pool.
        swept: list = []
        counts: dict = {}
        for t in remaining:
            c, tk = ev.place_load(_load(t), ev.taken, False)
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


# ── registration ──────────────────────────────────────────────────────────────────
# Into BOTH standing registries: an arm sets both knobs to one name (05).  Import-time
# registration rides the package __init__, so any consumer that can name a policy has
# these resolvable; the seeded 'fifo'/'lifo' keys are untouched.
for _registry in (YARD_POLICIES, DOCK_POLICIES):
    _registry['gain_myopic'] = gain_myopic
    _registry['gain_forecast'] = gain_forecast
    _registry['gain_gated'] = gain_gated
del _registry
