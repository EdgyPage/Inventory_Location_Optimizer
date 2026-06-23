"""inventory_optimal.py — optimal layout, Σf·D objective, full-labor floor + optimal map.

`OptimalLayoutMixin` holds the benchmark/optimal-placement methods.  Mixed into
Inventory_Manager so the public API (`place_optimal`, `optimal_sigma_fd`, `optimal_work`,
`build_optimal_map`, `current_sigma_fd`, `enable_sigma_fd`, `tracked_sigma_fd`) is
unchanged.  All instance state it reads (`self._key`, `self._execute_placement`,
`self._originals`, `self._unavailable`, `self._bin_pref`, `self._map_target`,
`self._sigma_*`) is initialised by Inventory_Manager.__init__.
"""
from __future__ import annotations

from collections import defaultdict, deque

from Carton import Carton
from Storage_Primitive import StorageUnit, viable_storage_units
from cost_model import height_multiplier, handle_var, sec_per_inch
from inventory_common import _SIZE_RANKS, _SIZES_DESCENDING, _equilibrium_qty


class OptimalLayoutMixin:

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

    def _optimal_assign(self, cartons: list[Carton], freq_of: dict,
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
            for c in cartons:
                if c.sku not in self._originals and not getattr(c, '_is_reorder', False):
                    self._originals[c.sku] = c
                    self._initial_quantities[c.sku] = _equilibrium_qty(c)

        # Group units by (handling, category, unit_type); place hottest first so
        # that within each size tier the hottest unit claims the lowest-D bin.
        groups: dict = defaultdict(list)
        for c in cartons:
            for unit in viable_storage_units(c, _equilibrium_qty(c)):
                shc = unit.carton.storage_handle_config
                groups[(shc.handling, shc.category, unit.unit_category)].append(unit)

        sigma = 0.0
        for (handling, category, utype), units in groups.items():
            units.sort(key=lambda u: freq_of.get(u.carton.sku, 0.0), reverse=True)
            for unit in units:
                b = self._take_optimal_bin(bins_by_key, handling, category, utype, unit)
                if b is None:
                    continue                               # warehouse full for this tier
                sigma += freq_of.get(unit.carton.sku, 0.0) * D(b)
                if place:
                    self._execute_placement(unit, b)
        return sigma

    def place_optimal(self, cartons: list[Carton], freq_of: dict,
                      x_speed: float, y_speed: float) -> float:
        """Stock the warehouse at the pure-global-D optimal layout.  Returns the
        optimal Sigma f*D.  Bumps _reorder_placements per unit (the worker discards
        the initial-stock churn with a pop_churn() before the batch loop)."""
        return self._optimal_assign(cartons, freq_of, x_speed, y_speed, place=True)

    def optimal_sigma_fd(self, cartons: list[Carton], freq_of: dict,
                         x_speed: float, y_speed: float) -> float:
        """The minimal achievable Sigma f*D for this warehouse + inventory (the
        yardstick).  Pure computation — does not mutate manager state."""
        return self._optimal_assign(cartons, freq_of, x_speed, y_speed, place=False)

    # ── full-labor optimum (travel + height handling) + optimal map ──────────

    @staticmethod
    def _handle_var(carton, wp) -> float:
        """Per-unit weight/volume handling term v_s (no intercept, no quantity) — the
        height-scalable part of pick effort.  Mirrors Carton.compute_labor_cost."""
        return handle_var(carton.weight, carton.volume(),
                          wp.pick_weight_coef, wp.pick_volume_coef,
                          getattr(wp, 'pick_weight_fn', 'log'),
                          getattr(wp, 'pick_volume_fn', 'log'))

    def _optimal_work_assign(self, cartons: list[Carton], freq_of: dict,
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
        brackets  = getattr(wp, 'height_brackets', ())
        xs, ys    = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch pace
        intercept = wp.pick_intercept

        def _D(b):  return xs * b.x_phys + ys * b.y_phys
        def _M(b):  return height_multiplier(brackets, b.y_phys)

        # quantity-free reference handling so the bin basis pref carries a realistic
        # height-penalty magnitude (V_REF ~ a typical v_s); falls back to 1.0.
        vs = [self._handle_var(c, wp) for c in cartons]
        v_ref = (sum(vs) / len(vs)) if vs else 1.0
        v_by_sku = {c.sku: v for c, v in zip(cartons, vs)}

        bins_by_key: dict = defaultdict(list)
        for b in self.warehouse.bins:
            bins_by_key[self._key(b)].append(b)

        units_by_key: dict = defaultdict(list)
        for c in cartons:
            for unit in viable_storage_units(c, _equilibrium_qty(c)):
                if unit.unit_category == 'singleton':
                    key = (c.storage_handle_config.handling,
                           c.storage_handle_config.category, 'singleton', 'singleton')
                else:
                    key = (c.storage_handle_config.handling,
                           c.storage_handle_config.category,
                           unit.storage_size, unit.unit_category)
                units_by_key[key].append(unit)

        W_var = 0.0
        sku_target: dict[int, list] = defaultdict(list)
        _LAP_CAP = 1200            # exact LAP up to this many units/class; else greedy

        for key, units in units_by_key.items():
            bins = bins_by_key.get(key)
            if not bins:
                continue
            n = len(units)
            a = [freq_of.get(u.carton.sku, 0.0) for u in units]                  # α_s = f (travel)
            # height now scales the WHOLE pick: per-pick handling = M·(intercept + q·v),
            # so the M-coefficient is f·(intercept + q·v), not f·q·v.
            b_ = [freq_of.get(u.carton.sku, 0.0)
                  * (intercept + qty_of.get(u.carton.sku, 0.0) * v_by_sku.get(u.carton.sku, 0.0))
                  for u in units]                                                # β_s = f·(intercept + q·v)
            # candidate bins: lowest-D per height bracket, capped at n (others dominated)
            by_m: dict = defaultdict(list)
            for bn in bins:
                by_m[_M(bn)].append(bn)
            cand: list = []
            for m, lst in by_m.items():
                lst.sort(key=_D)
                cand.extend(lst[:n])
            m_cnt = len(cand)
            if m_cnt == 0:
                continue
            Dc = [_D(bn) for bn in cand]
            Mc = [_M(bn) for bn in cand]

            assigned: list[tuple[int, int]] = []     # (unit_idx, cand_idx)
            if n <= _LAP_CAP and n * m_cnt <= 4_000_000:
                try:
                    import numpy as _np
                    from scipy.optimize import linear_sum_assignment
                    C = (_np.asarray(a)[:, None] * _np.asarray(Dc)[None, :]
                         + _np.asarray(b_)[:, None] * _np.asarray(Mc)[None, :])
                    ri, ci = linear_sum_assignment(C)
                    assigned = list(zip(ri.tolist(), ci.tolist()))
                except Exception:
                    assigned = []
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
                # quantity-free bin basis: travel + M-scaled per-pick floor (intercept + v_ref)
                pref = Dc[j] + Mc[j] * (intercept + v_ref)
                sku_target[units[i].carton.sku].append(pref)

        target = {sku: sum(p) / len(p) for sku, p in sku_target.items() if p}
        return W_var, target

    def optimal_work(self, cartons: list[Carton], freq_of: dict,
                     qty_of: dict, wp) -> float:
        """Minimal achievable expected work W* (travel + height handling) — the floor
        yardstick.  Pure computation; does not mutate manager state."""
        return self._optimal_work_assign(cartons, freq_of, qty_of, wp)[0]

    def build_optimal_map(self, cartons: list[Carton], freq_of: dict,
                          qty_of: dict, wp) -> float:
        """Build the optimal map (the score-match basis) on this manager and return W*.
        Sets `_bin_pref` (quantity-free location score for EVERY bin) and `_map_target`
        (each SKU's optimal preferred score).  Call once at warehouse build, after the
        inventory is assigned."""
        brackets = getattr(wp, 'height_brackets', ())
        xs, ys = sec_per_inch(wp.x_speed), sec_per_inch(wp.y_speed)   # ft/s -> s/inch pace
        intercept = wp.pick_intercept

        vs = [self._handle_var(c, wp) for c in cartons]
        v_ref = (sum(vs) / len(vs)) if vs else 1.0
        # quantity-free bin basis: travel + M-scaled per-pick floor (intercept + v_ref),
        # matching the new height model where M scales the whole at-location pick.
        self._bin_pref = {
            id(b): (xs * b.x_phys + ys * b.y_phys)
                   + height_multiplier(brackets, b.y_phys) * (intercept + v_ref)
            for b in self.warehouse.bins
        }
        w_star, self._map_target = self._optimal_work_assign(cartons, freq_of, qty_of, wp)
        return w_star

    def current_sigma_fd(self, freq_of: dict, x_speed: float, y_speed: float) -> float:
        """Realised demand-weighted within-aisle travel = sum over occupied bins of
        freq[sku] * D(bin).  The primary convergence metric."""
        xp, yp = sec_per_inch(x_speed), sec_per_inch(y_speed)   # ft/s -> s/inch
        s = 0.0
        for b in self._unavailable.values():
            st = b.storage
            if st is not None:
                s += (freq_of.get(st.carton.sku, 0.0)
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
