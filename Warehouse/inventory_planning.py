"""inventory_planning.py — warehouse sizing/sampling (pre-instantiation).

`PlanningMixin` holds the static/class methods that size a warehouse FROM an inventory
before any manager instance or warehouse exists.  Mixed into Inventory_Manager so the
public API (`Inventory_Manager.plan_warehouse(...)`, `.bucket_requirements(...)`,
`.sample_to_capacity(...)`) is unchanged.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any

from Order import Order
from Aisle_Dimensions import uniform_aisle_bins
from Warehouse_Builder import AisleConfig, WarehouseConfig
from Storage_Primitive import Pallet, Singleton, viable_storage_units, _max_qty_fits as _sq_max
from inventory_common import (
    BinKey, WarehousePlan, _SIZES_DESCENDING,
    _equilibrium_qty, _max_qty_fitting_pallet_size,
)


class PlanningMixin:

    # ── warehouse planning (pre-instantiation) ────────────────────────────────
    # These size a warehouse FROM an inventory, before any manager instance or
    # warehouse exists, so they are static/class methods.  They guarantee every
    # (handling, category, size, unit_type) bucket has at least one aisle, so
    # every SKU is structurally placeable, then add demand-driven replicas and
    # sample SKUs to fill to a target utilization.

    @staticmethod
    def bucket_requirements(orders: list[Order]) -> dict[BinKey, int]:
        """Exact bin count per (handling, category, storage_size, unit_type)
        bucket, computed by running each order through viable_storage_units at
        its equilibrium_qty.  This is the authoritative per-tier demand."""
        req: dict[BinKey, int] = defaultdict(int)
        for c in orders:
            shc = c.storage_handle_config
            for u in viable_storage_units(c, _equilibrium_qty(c)):
                req[(shc.handling, shc.category, u.storage_size, u.unit_category)] += 1
        return dict(req)

    @classmethod
    def plan_warehouse(
        cls,
        orders      : list[Order],
        *,
        categories   : list[str],
        handlings    : list[str],
        aisle_width  : int,
        aisle_height : int,
        target_fill  : float = 0.85,
        min_bins     : int | None = None,
        max_bins     : int | None = None,
        max_aisles   : int | None = None,
        composition  : dict | None = None,
        sample       : bool = True,
        rng          : random.Random | None = None,
        log          : Any = None,
    ) -> 'WarehousePlan':
        """Size a warehouse to fit *orders* under the given constraints.

        1. Enumerate the full bucket set (every handling×category gets 4 pallet
           size tiers + 1 singleton) — the structural floor that guarantees
           every SKU has a place.
        2. Replica per bucket either from demand (default,
           max(1, ceil(demand/(eff·target_fill)))) or from an explicit
           *composition* basis vector (bins ∝ weight).
        3. Scale up to >= min_bins, then down to <= max_bins/max_aisles (never
           below 1 replica/bucket; min_bins wins if it conflicts with max).
        4. Sample SKUs to fill the resulting capacity to target_fill.

        composition: optional factored basis vector of *bin* ratios — a dict with
        any of the keys 'handling', 'category', 'size', 'unit', each mapping a
        value to a relative weight (missing values default to 1.0).  The per-bucket
        weight is the product of the matching dimension weights; bins are allocated
        proportionally.  Total scale comes from min_bins (or demand if min_bins is
        unset).  Example:
            {'unit': {'pallet': 0.7, 'singleton': 0.3},
             'size': {'small': 0.1, 'medium': 0.2, 'large': 0.3, 'extra_large': 0.4}}
        """
        req = cls.bucket_requirements(orders)

        # 1: enumerate every bucket with a ≥1 floor.
        bucket_list: list[tuple] = []     # (handling, category, size, unit_type)
        for h in handlings:
            for cat in categories:
                for size in _SIZES_DESCENDING:          # 4 pallet tiers
                    bucket_list.append((h, cat, size, 'pallet'))
                bucket_list.append((h, cat, 'singleton', 'singleton'))

        def _eff(bucket: tuple) -> int:
            _h, _c, size, unit_type = bucket
            return uniform_aisle_bins(unit_type, size, aisle_width, aisle_height)

        def _comp_weight(bucket: tuple) -> float:
            """Factored basis-vector weight for a bucket (product of dimension
            weights; each dimension defaults to 1.0 when unspecified)."""
            h, cat, size, unit_type = bucket
            w  = composition.get('handling', {}).get(h, 1.0)
            w *= composition.get('category', {}).get(cat, 1.0)
            w *= composition.get('unit', {}).get(unit_type, 1.0)
            if unit_type == 'pallet':
                w *= composition.get('size', {}).get(size, 1.0)
            return w

        # 2: base replicas — demand-driven, or proportional to a composition vector.
        replicas: dict[tuple, int] = {}
        if composition is not None:
            weights = {b: _comp_weight(b) for b in bucket_list}
            tw      = sum(weights.values()) or 1.0
            demand_bins = sum(
                (max(1, math.ceil(req.get(b, 0) / (_eff(b) * target_fill))) if _eff(b) else 1) * _eff(b)
                for b in bucket_list)
            target_total = float(min_bins) if min_bins else float(demand_bins)
            for b in bucket_list:
                eff = _eff(b)
                desired = target_total * weights[b] / tw     # desired bins for b
                replicas[b] = max(1, round(desired / eff)) if eff else 1
        else:
            for b in bucket_list:
                eff = _eff(b)
                need = req.get(b, 0)
                replicas[b] = max(1, math.ceil(need / (eff * target_fill))) if eff else 1

        total_aisles = sum(replicas.values())
        total_bins   = sum(r * _eff(b) for b, r in replicas.items())

        # 3a: scale UP to satisfy a minimum bin count.
        if min_bins is not None and total_bins < min_bins:
            factor = min_bins / total_bins
            for b in replicas:
                replicas[b] = max(1, math.ceil(replicas[b] * factor))
            total_aisles = sum(replicas.values())
            total_bins   = sum(r * _eff(b) for b, r in replicas.items())

        # 3b: enforce caps, never trimming a bucket below its floor of 1, and
        #     never below min_bins (a min_bins > max_bins request keeps the min).
        _bins_floor = min_bins if min_bins is not None else 0
        if ((max_aisles is not None and total_aisles > max_aisles) or
            (max_bins   is not None and total_bins   > max_bins and total_bins > _bins_floor)):
            ratios = []
            if max_aisles is not None and total_aisles > max_aisles:
                ratios.append(max_aisles / total_aisles)
            if max_bins is not None and total_bins > max_bins:
                ratios.append(max(max_bins, _bins_floor) / total_bins)
            scale = min(ratios)
            for b in replicas:
                replicas[b] = max(1, round(replicas[b] * scale))
            total_aisles = sum(replicas.values())
            total_bins   = sum(r * _eff(b) for b, r in replicas.items())

            # greedy trim largest-bin trimmable bucket (replicas>1) to hit caps,
            # but never drop total_bins below the min_bins floor.
            while (((max_bins   is not None and total_bins   > max_bins) or
                    (max_aisles is not None and total_aisles > max_aisles))
                   and total_bins > _bins_floor):
                trimmable = [b for b in bucket_list if replicas[b] > 1]
                if not trimmable:
                    break   # every bucket at floor — cannot shrink further
                b = max(trimmable, key=_eff)
                if total_bins - _eff(b) < _bins_floor:
                    break   # one more trim would breach the min_bins floor
                replicas[b] -= 1
                total_aisles -= 1
                total_bins   -= _eff(b)

            if log is not None and (
                (max_bins   is not None and total_bins   > max_bins) or
                (max_aisles is not None and total_aisles > max_aisles)):
                log.warning(
                    f'  max-bins/max-aisles below structural minimum — cap not '
                    f'honored (requested max_bins={max_bins} max_aisles={max_aisles}). '
                    f'Floor is {total_aisles} aisles / {total_bins:,} bins: one aisle '
                    f'per {len(bucket_list)} (handling,category,size,unit_type) buckets '
                    f'so every SKU is placeable. Proceeding with the floor.')

        # Build per-replica AisleConfig list + capacity map.
        aisle_configs: list = []
        capacity: dict[BinKey, int] = {}
        for b in bucket_list:
            h, cat, size, unit_type = b
            eff = _eff(b)
            rep = replicas[b]
            capacity[b] = rep * eff
            sizes_arg = ['singleton'] if unit_type == 'singleton' else [size]
            for _ in range(rep):
                aisle_configs.append(
                    AisleConfig(h, cat, unit_type, aisle_width, aisle_height,
                                sizes_arg, None))

        # 4: sample SKUs to fill capacity to target_fill.  Skipped when sample=
        # False (e.g. analysis only needs the warehouse shape + aisle maps, not
        # a restocked inventory) — this avoids re-stocking the whole inventory.
        if sample:
            sampled, allowlist = cls.sample_to_capacity(
                orders, capacity, target_fill=target_fill, rng=rng)
            total_units = sum(
                len(viable_storage_units(c, _equilibrium_qty(c))) for c in sampled)
            expected_fill = total_units / total_bins if total_bins else 0.0
        else:
            sampled, allowlist = [], set()
            expected_fill = 0.0

        n = len(aisle_configs)
        splits = [1.0 / n] * n if n else []
        warehouse_cfg = WarehouseConfig(
            total_aisles  = n,
            aisle_splits  = splits,
            aisle_configs = aisle_configs,
        )
        return WarehousePlan(
            warehouse_cfg = warehouse_cfg,
            sampled       = sampled,
            sku_allowlist = allowlist,
            capacity      = capacity,
            aisle_configs = aisle_configs,
            total_aisles  = n,
            total_bins    = total_bins,
            expected_fill = expected_fill,
        )

    @staticmethod
    def sample_to_capacity(
        orders     : list[Order],
        capacity    : dict[BinKey, int],
        *,
        target_fill : float = 0.85,
        rng         : random.Random | None = None,
    ) -> tuple[list[Order], set]:
        """Assign each order a multi-tier stock_plan that fills bin capacity.

        A order's units are spread across the EMPTIEST bin tiers it can reach:
        flexible (small-footprint) items can be palletized into any tier their
        geometry permits; rigid (large) items only reach the large tiers they
        genuinely require.  Each plan slot is a (is_singleton, qty_per_unit)
        pair; the slots sum to the order's (possibly grown) equilibrium_qty.

        Allocation: phase 1 is round-robin so every SKU gets at least one unit
        first (placeability) and reaches its base equilibrium; phase 2 fills the
        leftover capacity per (handling, category) group in BULK — distributing
        each bin tier's free space across the orders that can reach it in one
        step rather than one bin at a time.  The plan is stored on the order as
        run-length slots (is_singleton, qty_per_unit, count) so
        viable_storage_units — and therefore every reorder — reproduces the exact
        tier mix.

        Performance: each order's reachable tiers (and the qty that lands a full
        pallet in each) are computed once via Pallet._fit (O(N) fits).  Full
        pallets reuse that cached tier, so no _fit runs in the fill loops; only a
        capped final slot (phase 1, ≤1 per order) needs a fit.

        Returns (sampled_cartons, sampled_sku_ids).
        """
        _rng   = rng or random
        free   = {b: int(cap * target_fill) for b, cap in capacity.items()}

        def _reachable(c: Order) -> list[tuple[BinKey, int, bool]]:
            """(bucket, qty_per_unit, is_singleton) options this order can fill.
            qty_per_unit is the quantity whose pallet lands exactly in that tier,
            so a full pallet of it never needs a _fit recheck at fill time."""
            shc  = c.storage_handle_config
            opts: list[tuple[BinKey, int, bool]] = []
            for size in _SIZES_DESCENDING:
                q = _max_qty_fitting_pallet_size(c, size)
                if q > 0 and Pallet(c, q).storage_size == size:
                    opts.append(((shc.handling, shc.category, size, 'pallet'), q, False))
            sq = _sq_max(c, Singleton)
            if sq > 0:
                opts.append(((shc.handling, shc.category, 'singleton', 'singleton'), sq, True))
            return opts

        order: list[Order] = list(orders)
        _rng.shuffle(order)
        reach   = {id(c): _reachable(c) for c in order}
        plans   : dict[int, list[tuple[bool, int, int]]] = {id(c): [] for c in order}
        qty_sum : dict[int, int] = {id(c): 0 for c in order}
        eq0     = {id(c): _equilibrium_qty(c) for c in order}
        shc_of  = {id(c): c.storage_handle_config for c in order}

        def _add_run(c: Order, is_single: bool, per: int, count: int, bucket: BinKey) -> None:
            """Append `count` units of `per` items to c's plan, charging `bucket`.
            Merges with the previous run if it is the same (is_single, per)."""
            plan = plans[id(c)]
            if plan and plan[-1][0] == is_single and plan[-1][1] == per:
                last = plan[-1]
                plan[-1] = (is_single, per, last[2] + count)
            else:
                plan.append((is_single, per, count))
            qty_sum[id(c)] += per * count
            free[bucket]    = free.get(bucket, 0) - count

        def _add_one(c: Order, cap_qty: int | None) -> bool:
            """Add ONE pallet/singleton in c's emptiest reachable bucket with
            budget.  cap_qty caps the slot quantity to land the final slot exactly
            on equilibrium.  Returns False when no reachable bucket has space."""
            opts = [(b, per, isng) for (b, per, isng) in reach[id(c)] if free.get(b, 0) > 0]
            if not opts:
                return False
            b, per, isng = max(opts, key=lambda o: free[o[0]])
            if cap_qty is None or cap_qty >= per:
                # Full pallet — tier is the cached bucket, no _fit needed.
                _add_run(c, isng, per, 1, b)
                return True
            # Capped final slot: a smaller quantity can drop a pallet into a
            # SMALLER tier, so charge the bucket it ACTUALLY lands in.
            per = cap_qty
            if per <= 0:
                return False
            if isng:
                actual_b = b
            else:
                shc = shc_of[id(c)]
                actual_b = (shc.handling, shc.category, Pallet(c, per).storage_size, 'pallet')
            if free.get(actual_b, 0) <= 0:
                return False
            _add_run(c, isng, per, 1, actual_b)
            return True

        # Phase 1 (round-robin): every order up to its base equilibrium.
        progress = True
        while progress:
            progress = False
            for c in order:
                if qty_sum[id(c)] >= eq0[id(c)]:
                    continue
                gap = eq0[id(c)] - qty_sum[id(c)]
                if _add_one(c, cap_qty=gap):
                    progress = True

        # Phase 2 (bulk): fill leftover space per (handling, category) group.
        # For each bin tier with free budget, distribute it across the orders in
        # the group that can reach it (full pallets only → exact tier, no _fit).
        groups: dict[tuple, list[Order]] = defaultdict(list)
        for c in order:
            shc = shc_of[id(c)]
            groups[(shc.handling, shc.category)].append(c)

        for gcartons in groups.values():
            bucket_reachers: dict[BinKey, list[tuple[Order, int, bool]]] = defaultdict(list)
            for c in gcartons:
                for (b, per, isng) in reach[id(c)]:
                    bucket_reachers[b].append((c, per, isng))
            for b, lst in bucket_reachers.items():
                avail = free.get(b, 0)
                if avail <= 0 or not lst:
                    continue
                n          = len(lst)
                base_share = avail // n
                remainder  = avail - base_share * n
                for i, (c, per, isng) in enumerate(lst):
                    share = base_share + (1 if i < remainder else 0)
                    if share > 0:
                        _add_run(c, isng, per, share, b)

        selected = [c for c in order if plans[id(c)]]
        for c in selected:
            base = eq0[id(c)]
            f    = (c.reorder_point / base) if base else 0.5
            total = qty_sum[id(c)]
            c.stock_plan      = plans[id(c)]
            c.equilibrium_qty = total
            c.reorder_point   = max(1, min(total - 1, round(f * total)))
        return selected, {c.sku for c in selected}
