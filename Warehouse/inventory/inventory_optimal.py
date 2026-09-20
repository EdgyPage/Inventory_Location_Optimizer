"""inventory_optimal.py — optimal layout, Σf·D objective, full-labor floor + optimal map.

`OptimalLayoutMixin` holds the benchmark/optimal-placement methods.  Mixed into
Inventory_Manager so the public API (`place_optimal`, `optimal_sigma_fd`, `optimal_work`,
`build_optimal_map`, `current_sigma_fd`, `enable_sigma_fd`, `tracked_sigma_fd`,
`enable_pick_owed`, `pick_owed`) is unchanged.  All instance state it reads (`self._key`,
`self._execute_placement`, `self._originals`, `self._unavailable`, `self._bin_pref`,
`self._map_target`, `self._sigma_*`, `self._po_*`) is initialised by
Inventory_Manager.__init__.

TWO SCORES OVER ONE PLACEMENT, and the difference is the point.  `optimal_work` is the
FLOOR -- what the planned demand would cost if every unit sat in its labour-optimal bin.
`pick_owed` is what it costs from where the stock actually IS at this instant.  They share
`per_pick`, so the gap between them is a property of the placement and not of two
arithmetics that drifted.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from Warehouse.catalog.Order import Order
from Warehouse.layout.Storage_Primitive import StorageUnit, viable_storage_units
from Warehouse.kernel.cost_model import height_multiplier, handle_var, per_pick, sec_per_inch
from Warehouse.inventory.inventory_common import (
    _SIZE_RANKS, _SIZES_DESCENDING, _equilibrium_qty, _wp_for, binkey_of,
)

log = logging.getLogger(__name__)

#: Which branch a BinKey class actually took in `_optimal_work_assign`.
#:   'lap'          the exact scipy assignment ran
#:   'greedy_gate'  the class was too big for the exact solve (the DESIGNED path at scale)
#:   'greedy_error' the exact solve was attempted and failed — a DEFECT, always logged
#:   'greedy_empty' scipy returned nothing to assign
#: The site described the Map family as solving "the full linear assignment problem".  At
#: production catalogue size most classes are far past the gate, so that description was
#: reporting a branch the run rarely took.  The split is now recorded rather than assumed.
BRANCHES = ('lap', 'greedy_gate', 'greedy_error', 'greedy_empty')


class OptimalLayoutMixin:

    #: Optional per-class observer, `fn(dict) -> None`, called once per BinKey class in
    #: `_optimal_work_assign`.  Default None so production is byte-identical with it off;
    #: the map-precompute measurement CLI sets it to capture the per-class detail.
    _assign_probe = None

    #: Solver split from the last `_optimal_work_assign`: how many BinKey classes and how
    #: many UNITS took the exact branch.  Populated on every call, probe or not, so a
    #: production run can record it without instrumentation.
    _map_lap_stats: dict = {}

    # ── optimal layout (pure global-D) + Sigma f*D objective ─────────────────

    def _take_optimal_bin(self, bins_by_key: dict, handling: str, category: str,
                          unit_type: str, unit: StorageUnit) -> 'Aisle.Bin | None':
        """Pop the lowest-D available bin for *unit*, smallest fitting tier first
        (same tier logic as _candidates, spilling UP only when a tier is empty).
        bins_by_key maps BinKey -> deque of bins pre-sorted by D ascending."""
        if unit_type == 'singleton':
            dq = bins_by_key.get((handling, category, 'singleton', 'singleton'))
            return dq.popleft() if dq else None
        min_rank = _SIZE_RANKS.get(unit.storage_size, 0) if unit.storage_size else 0
        for size in reversed(_SIZES_DESCENDING):          # small -> large
            if _SIZE_RANKS[size] >= min_rank:
                dq = bins_by_key.get((handling, category, size, unit_type))
                if dq:
                    return dq.popleft()
        return None

    def _optimal_assign(self, orders: list[Order], freq_of: dict,
                        x_speed: float, y_speed: float, place: bool) -> float:
        """Pure-global-D optimal layout: per BinKey class, assign the highest
        pick-frequency units to the lowest-D bins (rearrangement-inequality optimum
        for within-aisle travel).  Returns the minimal Sigma f*D.  When place=True,
        commits each unit to its bin and registers originals so reorders work."""
        xp, yp = sec_per_inch(x_speed), sec_per_inch(y_speed)   # ft/s -> s/inch
        D = lambda b: xp * b.x_phys + yp * b.y_phys
        bins_by_key: dict = defaultdict(deque)
        for b in sorted(self.warehouse.bins, key=D):       # D ascending => low-D heads
            bins_by_key[self._key(b)].append(b)

        if place:
            for c in orders:
                if c.sku not in self._originals and not getattr(c, '_is_reorder', False):
                    self._originals[c.sku] = c
                    self._initial_quantities[c.sku] = _equilibrium_qty(c)

        # Group units by (handling, category, unit_type); place hottest first so
        # that within each size tier the hottest unit claims the lowest-D bin.
        groups: dict = defaultdict(list)
        for c in orders:
            for unit in viable_storage_units(c, _equilibrium_qty(c)):
                shc = unit.order.storage_handle_config
                groups[(shc.handling, shc.category, unit.unit_category)].append(unit)

        sigma = 0.0
        for (handling, category, utype), units in groups.items():
            units.sort(key=lambda u: freq_of.get(u.order.sku, 0.0), reverse=True)
            for unit in units:
                b = self._take_optimal_bin(bins_by_key, handling, category, utype, unit)
                if b is None:
                    continue                               # warehouse full for this tier
                sigma += freq_of.get(unit.order.sku, 0.0) * D(b)
                if place:
                    self._execute_placement(unit, b)
        return sigma

    def place_optimal(self, orders: list[Order], freq_of: dict,
                      x_speed: float, y_speed: float) -> float:
        """Stock the warehouse at the pure-global-D optimal layout.  Returns the
        optimal Sigma f*D.  Bumps _reorder_placements per unit (the worker discards
        the initial-stock churn with a pop_churn() before the batch loop)."""
        return self._optimal_assign(orders, freq_of, x_speed, y_speed, place=True)

    def optimal_sigma_fd(self, orders: list[Order], freq_of: dict,
                         x_speed: float, y_speed: float) -> float:
        """The minimal achievable Sigma f*D for this warehouse + inventory (the
        yardstick).  Pure computation — does not mutate manager state."""
        return self._optimal_assign(orders, freq_of, x_speed, y_speed, place=False)

    # ── full-labor optimum (travel + height handling) + optimal map ──────────

    @staticmethod
    def _handle_var(order, wp) -> float:
        """Per-unit weight/volume handling term v_s (no intercept, no quantity) — the
        height-scalable part of pick effort.  Mirrors Order.compute_labor_cost."""
        return handle_var(order.weight, order.volume(),
                          wp.pick_weight_coef, wp.pick_volume_coef,
                          getattr(wp, 'pick_weight_fn', 'log'),
                          getattr(wp, 'pick_volume_fn', 'log'))

    def _optimal_work_assign(self, orders: list[Order], freq_of: dict,
                             qty_of: dict, wp) -> tuple[float, dict]:
        """Exact minimal expected WORK (travel + height-weighted handling) for this
        warehouse + inventory, and each SKU's optimal preferred score.

        Per BinKey class solves the assignment  min Σ f_s·D_b + (f_s·q_s·v_s)·M(y_b)
        exactly (scipy LAP) — the rearrangement/transportation optimum that drives the
        highest height-sensitivity (f·q·v) units to the lowest-M bins and the highest
        frequency to the lowest-D bins.  Returns:
          W*          = Σ (assigned bins) f·(intercept + D_b) + (f·q·v)·M_b
                        (per occupied-bin convention, matching current_sigma_fd)
          sku_target  = {sku → pref(b*)} where b* is the SKU's assigned bin and
                        pref(b) = D_b + M(y_b)·V_REF is the quantity-free bin basis.
        Pure computation — does not mutate manager state.
        """
        # Cost is resolved PER BinKey class inside the loop (each class is one regime), so a
        # mixed warehouse scores store vs fulfillment bins with their own speeds/intercept;
        # handle_var is likewise per-order-regime.  Single-regime runs resolve to one wp.
        vs = [self._handle_var(c, _wp_for(wp, c)) for c in orders]
        v_ref = (sum(vs) / len(vs)) if vs else 1.0   # quantity-free reference (basis magnitude)
        v_by_sku = {c.sku: v for c, v in zip(orders, vs)}

        bins_by_key: dict = defaultdict(list)
        for b in self.warehouse.bins:
            bins_by_key[self._key(b)].append(b)

        units_by_key: dict = defaultdict(list)
        for c in orders:
            for unit in viable_storage_units(c, _equilibrium_qty(c)):
                # binkey_of covers all three families (Singleton's fixed 'singleton'
                # label comes from the unit's own storage_size).
                units_by_key[binkey_of(unit)].append(unit)

        W_var = 0.0
        sku_target: dict[int, list] = defaultdict(list)
        _LAP_CAP = 1200            # exact LAP up to this many units/class; else greedy
        # Which branch each class took, accumulated on the manager so a PRODUCTION run
        # records the split without the probe being armed.  `n_units` is the weight that
        # matters: a report of "k of N classes solved exactly" is misleading when the
        # exact classes are the tiny ones, which at catalogue scale they are.
        stats = {'classes': 0, 'units': 0, 'lap_classes': 0, 'lap_units': 0,
                 'greedy_error': 0, 'cap': _LAP_CAP, 'prod_cap': 4_000_000}

        for key, units in units_by_key.items():
            bins = bins_by_key.get(key)
            if not bins:
                continue
            # Per-regime cost for this BinKey class (store vs fulfillment speeds/intercept).
            w = _wp_for(wp, units[0])
            xs_k, ys_k  = sec_per_inch(w.x_speed), sec_per_inch(w.y_speed)   # ft/s -> s/inch
            intercept_k = w.pick_intercept
            per_item_k  = w.pick_per_item
            brackets_k  = getattr(w, 'height_brackets', ())
            def _Dk(bn, _x=xs_k, _y=ys_k):  return _x * bn.x_phys + _y * bn.y_phys
            def _Mk(bn, _b=brackets_k):     return height_multiplier(_b, bn.y_phys)
            n = len(units)
            a = [freq_of.get(u.order.sku, 0.0) for u in units]                  # α_s = f (travel)
            # height scales the WHOLE pick: per-pick handling = M·(intercept + q·per_item + q·v),
            # so the M-coefficient is f·(intercept + q·per_item + q·v), not f·q·v.
            b_ = [per_pick(freq_of.get(u.order.sku, 0.0), intercept_k,
                           v_by_sku.get(u.order.sku, 0.0), qty_of.get(u.order.sku, 0.0),
                           per_item_k)
                  for u in units]                                # β_s = f·(intercept + q·per_item + q·v)
            # candidate bins: lowest-D per height bracket, capped at n (others dominated)
            by_m: dict = defaultdict(list)
            for bn in bins:
                by_m[_Mk(bn)].append(bn)
            cand: list = []
            for m, lst in by_m.items():
                lst.sort(key=_Dk)
                cand.extend(lst[:n])
            m_cnt = len(cand)
            if m_cnt == 0:
                continue
            Dc = [_Dk(bn) for bn in cand]
            Mc = [_Mk(bn) for bn in cand]

            assigned: list[tuple[int, int]] = []     # (unit_idx, cand_idx)
            # BOTH gate terms are recorded, not just the verdict: `m_cnt` is
            # Σ_brackets min(len(bracket), n), so it is only bounded ABOVE by 3n — which
            # of the two terms binds varies class by class, and a published statement
            # about "the cap" that names only one of them is wrong for half the classes.
            gate_n, gate_prod = n <= _LAP_CAP, n * m_cnt <= 4_000_000
            branch, err, t0 = 'greedy_gate', None, time.perf_counter()
            if gate_n and gate_prod:
                try:
                    import numpy as _np
                    from scipy.optimize import linear_sum_assignment
                    C = (_np.asarray(a)[:, None] * _np.asarray(Dc)[None, :]
                         + _np.asarray(b_)[:, None] * _np.asarray(Mc)[None, :])
                    ri, ci = linear_sum_assignment(C)
                    assigned = list(zip(ri.tolist(), ci.tolist()))
                    branch = 'lap' if assigned else 'greedy_empty'
                except ImportError as exc:
                    # scipy absent: every class silently degrades and the run still looks
                    # fine.  This used to be indistinguishable from "too big to solve".
                    branch, err = 'greedy_error', f'{type(exc).__name__}: {exc}'
                    log.warning('optimal map: scipy unavailable, every eligible BinKey '
                                'class falls back to greedy (%s)', err)
                except Exception as exc:              # noqa: BLE001 - reported, not hidden
                    branch, err = 'greedy_error', f'{type(exc).__name__}: {exc}'
                    log.warning('optimal map: exact solve failed for BinKey %s '
                                '(n=%d, m=%d) — falling back to greedy: %s',
                                key, n, m_cnt, err)
            solve_s = time.perf_counter() - t0
            stats['classes'] += 1
            stats['units'] += n
            if branch == 'lap':
                stats['lap_classes'] += 1
                stats['lap_units'] += len(assigned)
            elif branch == 'greedy_error':
                stats['greedy_error'] += 1
            if self._assign_probe is not None:
                self._assign_probe({'key': key, 'n': n, 'm_cnt': m_cnt,
                                    'gate_n_ok': gate_n, 'gate_prod_ok': gate_prod,
                                    'branch': branch, 'solve_s': solve_s, 'error': err})
            if not assigned:                          # greedy fallback (feasible, near-opt)
                order = sorted(range(n), key=lambda i: b_[i] + a[i], reverse=True)
                pools: dict = defaultdict(deque)
                for j, bn in enumerate(cand):
                    pools[Mc[j]].append(j)
                for m in pools:
                    pools[m] = deque(sorted(pools[m], key=lambda j: Dc[j]))
                for i in order:
                    best = None
                    for m, dq in pools.items():
                        if not dq:
                            continue
                        j = dq[0]
                        cost = a[i] * Dc[j] + b_[i] * Mc[j]
                        if best is None or cost < best[0]:
                            best = (cost, m, j)
                    if best is None:
                        continue
                    _, m, j = best
                    pools[m].popleft()
                    assigned.append((i, j))

            for i, j in assigned:
                # per occupied-bin convention (matches current_sigma_fd):
                #   f·D  +  f·M·(intercept + q·v)   (height scales the whole pick; the
                # intercept now lives inside b_, so it is no longer added bin-independently).
                W_var += a[i] * Dc[j] + b_[i] * Mc[j]
                # quantity-free bin basis: travel + M-scaled per-pick floor at qty=1
                # (intercept + per_item + v_ref)
                pref = Dc[j] + Mc[j] * (intercept_k + per_item_k + v_ref)
                sku_target[units[i].order.sku].append(pref)

        target = {sku: sum(p) / len(p) for sku, p in sku_target.items() if p}
        self._map_lap_stats = stats
        return W_var, target

    def optimal_work(self, orders: list[Order], freq_of: dict,
                     qty_of: dict, wp) -> float:
        """Minimal achievable expected work W* (travel + height handling) — the floor
        yardstick.  Pure computation; does not mutate manager state."""
        return self._optimal_work_assign(orders, freq_of, qty_of, wp)[0]

    def build_optimal_map(self, orders: list[Order], freq_of: dict,
                          qty_of: dict, wp) -> float:
        """Build the optimal map (the score-match basis) on this manager and return W*.
        Sets `_bin_pref` (quantity-free location score for EVERY bin) and `_map_target`
        (each SKU's optimal preferred score).  Call once at warehouse build, after the
        inventory is assigned.

        Also leaves `_map_lap_stats` on the manager — how much of the assignment was
        actually solved exactly rather than greedily.  Read it rather than assuming: at
        production catalogue size the large BinKey classes are far past the exact-solve
        gate, so "the optimal map" is exact on a tail of small classes and near-optimal
        on the bulk.  The return value stays W* alone; a second return would have to be
        unpacked at four call sites for a number only the analysis layer wants."""
        brackets = getattr(wp, 'height_brackets', ())
        xs, ys = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch pace
        intercept = wp.pick_intercept
        per_item  = wp.pick_per_item

        vs = [self._handle_var(c, wp) for c in orders]
        v_ref = (sum(vs) / len(vs)) if vs else 1.0
        # quantity-free bin basis: travel + M-scaled per-pick floor at qty=1
        # (intercept + per_item + v_ref), matching the height model where M scales the
        # whole at-location pick.
        self._bin_pref = {
            id(b): (xs * b.x_phys + ys * b.y_phys)
                   + height_multiplier(brackets, b.y_phys) * (intercept + per_item + v_ref)
            for b in self.warehouse.bins
        }
        w_star, self._map_target = self._optimal_work_assign(orders, freq_of, qty_of, wp)
        return w_star

    def current_sigma_fd(self, freq_of: dict, x_speed: float, y_speed: float) -> float:
        """Realised demand-weighted within-aisle travel = sum over occupied bins of
        freq[sku] * D(bin).  The primary convergence metric."""
        xp, yp = sec_per_inch(x_speed), sec_per_inch(y_speed)   # ft/s -> s/inch
        s = 0.0
        for b in self._unavailable.values():
            st = b.storage
            if st is not None:
                s += (freq_of.get(st.order.sku, 0.0)
                      * (xp * b.x_phys + yp * b.y_phys))
        return s

    def enable_sigma_fd(self, freq_of: dict, x_speed: float, y_speed: float) -> None:
        """Bind the freq map + speeds and seed the incremental Sigma f*D from a
        single full scan.  Afterwards tracked_sigma_fd() is O(1): the running sum is
        maintained on every placement / pick-empty / eviction.  x_speed/y_speed are ft/s;
        _sigma_x/_sigma_y store the per-inch PACE so the incremental updates stay multiplies."""
        self._sigma_freq = freq_of
        self._sigma_x = sec_per_inch(x_speed)
        self._sigma_y = sec_per_inch(y_speed)
        self._sigma_fd = self.current_sigma_fd(freq_of, x_speed, y_speed)

    def tracked_sigma_fd(self) -> float:
        """The incrementally-maintained Sigma f*D (see enable_sigma_fd).  O(1)."""
        return self._sigma_fd

    # ── the pick work owed by the current placement ────────────────────────────────
    # WHY A SECOND ACCUMULATOR.  Sigma f*D above is the convergence metric and is
    # deliberately quantity-blind (a top-up into an occupied bin does not move it) and
    # height-blind.  Neither is a defect there -- it measures how well the OCCUPANCY is
    # arranged.  It cannot answer "what will the planned demand cost to serve from here",
    # which needs the height bracket and the pieces a pick handles, so extending it in
    # place would break the thing it is already trusted for.

    def enable_pick_owed(self, weight_of: dict, qty_of: dict, orders: list[Order], wp) -> None:
        """Bind the planned demand and the pick cost that `pick_owed` scores against.

        `weight_of` is the DEMAND WEIGHT per SKU -- the planned lines the run will actually
        ask for, summed off the precomputed batch script.  It is not `relative_frequency`:
        the script is what the run runs, it is identical across every cell of a matrix (one
        frozen inventory, one seed), and using it means the score answers "against the
        batches this run will field" rather than "against the distribution they were drawn
        from".  A caller with no script (the inline-sampling fallback) may pass the
        frequencies instead; the score is then the expectation rather than the realisation.

        `qty_of` is the planned pieces per pick, and `orders`/`wp` supply the per-SKU
        handling term -- both fixed for the run, so the per-SKU coefficient is resolved
        ONCE here and the per-batch read is a lookup and two multiplies per occupied bin.

        ONE cost expression, shared with `_optimal_work_assign` through `per_pick`: the
        score and the floor it is read against must be the same arithmetic or their ratio
        means nothing.  `wp` is this leaf's channel cost (a channel run sets
        `wp.by_regime = None`), which is why no per-regime resolution happens here."""
        self._po_x = sec_per_inch(wp.x_speed)
        self._po_y = sec_per_inch(wp.y_speed)
        self._po_brackets = tuple(getattr(wp, 'height_brackets', ()) or ())
        intercept = wp.pick_intercept
        per_item = wp.pick_per_item
        # h_s = intercept + q·per_item + q·v_s -- the M-coefficient of one pick, at
        # mult=1 so the bin's own M(y) scales it in `pick_owed`.
        self._po_hand = {
            c.sku: per_pick(1.0, intercept, self._handle_var(c, _wp_for(wp, c)),
                            qty_of.get(c.sku, 0.0), per_item)
            for c in orders}
        self._po_weight = dict(weight_of)

    def pick_owed(self) -> tuple[float, float]:
        """`(seconds owed, unservable weight)` for the planned demand, from where the
        stock is RIGHT NOW.

            seconds owed      = Σ over SKUs with shelf stock of
                                  w_s · mean over that SKU's bins of
                                    [ D(bin) + M(y_bin) · h_s ]
            unservable weight = Σ w_s over planned SKUs with NO shelf stock

        THE MEAN OVER THE SKU'S OWN BINS, not a sum: a line asks for the SKU once and is
        served from one of its locations, so holding the same SKU in four bins does not
        cost four picks.  Summing instead would score a well-stocked SKU as expensive and
        invert the whole metric.

        THE TWO NUMBERS ARE RETURNED TOGETHER AND NEITHER IS COMPLETE ALONE.  A SKU with
        nothing on a shelf contributes NOTHING to the first term -- absence reads as free
        -- so a caller that ranked on `seconds owed` by itself would rank "leave it in the
        yard" first, which is the exact inversion this pairing exists to prevent.  The
        second number is what says how much of the planned demand the first one declined
        to price.  No penalty is fabricated for it here: what an unserved line will
        eventually cost depends on where that stock lands, which this function cannot see.

        NOT COMPARABLE TO `optimal_work`, and not to another run.  The at-location
        arithmetic is the same (`cost_model.per_pick`), the WEIGHT BASIS is not:
        `optimal_work` weights every SKU by its relative frequency over the catalogue, this
        weights the lines the run's own batches ask for.  Measured 206x apart on the priced
        toy.  What it IS comparable across is the arms and cells of ONE run, which draw the
        same script from the same frozen inventory and the same seed -- which is the
        comparison it exists for.

        One pass over the occupied bins, plus one over the SKUs that have any.  That is the
        same order as the conservation ledger's own per-batch walk; it is a separate pass
        rather than a fold into it so this cost model stays inside the inventory layer."""
        if self._po_weight is None:
            return 0.0, 0.0
        cost_sum: dict = defaultdict(float)
        n_bins: dict = defaultdict(int)
        xs, ys, brackets = self._po_x, self._po_y, self._po_brackets
        hand = self._po_hand
        for b in self._unavailable.values():
            st = b.storage
            if st is None:
                continue
            sku = st.order.sku
            cost_sum[sku] += (xs * b.x_phys + ys * b.y_phys
                              + height_multiplier(brackets, b.y_phys) * hand.get(sku, 0.0))
            n_bins[sku] += 1
        owed = 0.0
        unservable = 0.0
        for sku, w in self._po_weight.items():
            n = n_bins.get(sku, 0)
            if n:
                owed += w * (cost_sum[sku] / n)
            else:
                unservable += w
        return owed, unservable

    def placement_bin_map(self) -> dict:
        """`{sku: [(aisle_id, bayX, bayY, qty), ...]}` over the bins occupied RIGHT NOW.

        The input shape `simconfig.expected_travel.PlacementDist.initial` takes, so the
        closed-form expectation can be re-taken over a placement mid-run rather than only
        over the one an arm started with.  That is the cross-check `pick_owed` needs and
        cannot be: the two price a placement by different models -- this one walks every
        SKU's own bins and weights by the planned script, the closed form routes carts
        through aisles and weights by relative frequency -- so agreement between their
        ORDERINGS is evidence about the placement, and agreement between their values
        would only be evidence that one was computed from the other.

        Built from `_unavailable`, the occupied-bin index, NOT from `warehouse.bins`: the
        latter is every bin the geometry has and is two orders of magnitude larger on a
        run with free space, and the empty ones contribute nothing but the walk.
        """
        out: dict = {}
        for b in self._unavailable.values():
            st = b.storage
            if st is None:
                continue
            out.setdefault(st.order.sku, []).append(
                (b.aisle.aisle_id, b.bayX, b.bayY, int(st.quantity)))
        return out
