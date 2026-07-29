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

from Warehouse.catalog.Order import Order
from Warehouse.layout.Aisle_Dimensions import (
    uniform_aisle_bins, catalog_aisle_bins, unit_bin_width,
    FULFILLMENT_BIN_WIDTH, FF_TIER_HEIGHTS, FULFILLMENT_AISLE_HEIGHT,
)
from Warehouse.layout.Warehouse_Builder import AisleConfig, WarehouseConfig
from Warehouse.layout.Storage_Primitive import (
    Pallet, Singleton, FulfillmentBin, viable_storage_units, _max_qty_fits as _sq_max,
)
from Warehouse.kernel.regime import FULFILLMENT, regime_of
from Warehouse.inventory.inventory_common import (
    BinKey, binkey_of, WarehousePlan, _SIZES_DESCENDING, _FF_SIZES_DESCENDING,
    _equilibrium_qty, _max_qty_fitting_size,
)


# ── sizing helpers (shared by the global-legacy and per-regime paths) ───────────
# All take an `eff_fn(bucket) -> bins-per-aisle` and operate on a subset of buckets, so the
# same math sizes the whole warehouse (regime_sizing=None) or one regime partition.

def _comp_weight(bucket: tuple, comp: dict) -> float:
    """Factored basis-vector weight for a bucket: product of the matching dimension weights
    (each dimension defaults to 1.0 when unspecified)."""
    h, cat, size, unit_type = bucket
    w  = comp.get('handling', {}).get(h, 1.0)
    w *= comp.get('category', {}).get(cat, 1.0)
    w *= comp.get('unit', {}).get(unit_type, 1.0)
    if unit_type == 'pallet':
        w *= comp.get('size', {}).get(size, 1.0)
    return w


def _demand_replicas(buckets, req, eff_fn, target_fill) -> dict:
    """Demand-driven replicas: max(1, ceil(bucket_demand / (bins_per_aisle · fill)))."""
    out = {}
    for b in buckets:
        eff = eff_fn(b)
        out[b] = max(1, math.ceil(req.get(b, 0) / (eff * target_fill))) if eff else 1
    return out


def _demand_total(buckets, req, eff_fn, target_fill) -> int:
    """Total bins the demand-driven replicas would occupy (the default scale for ratio modes)."""
    return sum((max(1, math.ceil(req.get(b, 0) / (eff_fn(b) * target_fill))) if eff_fn(b) else 1)
               * eff_fn(b) for b in buckets)


def _ratio_replicas(buckets, weight_fn, eff_fn, target_total: float) -> dict:
    """Replicas allocated proportionally to weight_fn(bucket), scaled to ~target_total bins.
    Used by the composition basis vector (store) and the fixed fulfillment tier distribution."""
    weights = {b: weight_fn(b) for b in buckets}
    tw = sum(weights.values()) or 1.0
    out = {}
    for b in buckets:
        eff = eff_fn(b)
        out[b] = max(1, round(target_total * weights[b] / tw / eff)) if eff else 1
    return out


def _apply_caps(buckets, replicas, eff_fn, min_bins, max_bins, max_aisles, log=None):
    """Scale `replicas` for `buckets` UP to >= min_bins, then DOWN to <= max_bins/max_aisles
    (never below 1 replica/bucket; min_bins wins on conflict), mutating `replicas` in place.
    This is the original global steps 3a/3b, scoped to one bucket set so it can run per regime."""
    if not buckets:
        return 0, 0
    total_aisles = sum(replicas[b] for b in buckets)
    total_bins   = sum(replicas[b] * eff_fn(b) for b in buckets)

    # 3a: scale UP to satisfy a minimum bin count.
    if min_bins is not None and 0 < total_bins < min_bins:
        factor = min_bins / total_bins
        for b in buckets:
            replicas[b] = max(1, math.ceil(replicas[b] * factor))
        total_aisles = sum(replicas[b] for b in buckets)
        total_bins   = sum(replicas[b] * eff_fn(b) for b in buckets)

    # 3b: enforce caps, never trimming below 1/bucket or below the min_bins floor.
    _bins_floor = min_bins if min_bins is not None else 0
    if ((max_aisles is not None and total_aisles > max_aisles) or
        (max_bins   is not None and total_bins   > max_bins and total_bins > _bins_floor)):
        ratios = []
        if max_aisles is not None and total_aisles > max_aisles:
            ratios.append(max_aisles / total_aisles)
        if max_bins is not None and total_bins > max_bins:
            ratios.append(max(max_bins, _bins_floor) / total_bins)
        scale = min(ratios)
        for b in buckets:
            replicas[b] = max(1, round(replicas[b] * scale))
        total_aisles = sum(replicas[b] for b in buckets)
        total_bins   = sum(replicas[b] * eff_fn(b) for b in buckets)

        while (((max_bins   is not None and total_bins   > max_bins) or
                (max_aisles is not None and total_aisles > max_aisles))
               and total_bins > _bins_floor):
            trimmable = [b for b in buckets if replicas[b] > 1]
            if not trimmable:
                break
            b = max(trimmable, key=eff_fn)
            if total_bins - eff_fn(b) < _bins_floor:
                break
            replicas[b] -= 1
            total_aisles -= 1
            total_bins   -= eff_fn(b)

        if log is not None and (
            (max_bins   is not None and total_bins   > max_bins) or
            (max_aisles is not None and total_aisles > max_aisles)):
            log.warning(
                f'  max-bins/max-aisles below structural minimum — cap not honored '
                f'(requested max_bins={max_bins} max_aisles={max_aisles}). Floor is '
                f'{total_aisles} aisles / {total_bins:,} bins: one aisle per '
                f'{len(buckets)} bucket(s) so every SKU is placeable. Proceeding with the floor.')
    return total_aisles, total_bins


def _ff_depth_split(size: str, target_bins: int, depth_classes: list, ff_h: int):
    """Split one fulfillment size tier's target bins across DEPTH classes (shallow…deep aisles
    that share the same BinKey), so a placement wave can route hot SKUs to shallow aisles.

    Each class is {'columns': n, 'share': w}; its aisle_width = n·FULFILLMENT_BIN_WIDTH and it
    holds ~ (share) of `target_bins` worth of bins.  Returns (classes, total_bins) where
    classes = [(aisle_width, n_aisles, eff_per_aisle), …].  Preserves total bins (± rounding)
    while growing the aisle count; guarantees ≥1 aisle per positive-share class so no tier is
    dropped.  Classes with columns < 1 bin width or non-positive share are skipped."""
    tier_h    = FF_TIER_HEIGHTS[size]
    weight_sum = sum(max(0.0, c.get('share', 0.0)) for c in depth_classes) or 1.0
    classes, total = [], 0
    for c in depth_classes:
        cols  = int(c.get('columns', 0))
        share = max(0.0, float(c.get('share', 0.0)))
        if cols <= 0 or share <= 0:
            continue
        a_w = cols * FULFILLMENT_BIN_WIDTH
        eff = catalog_aisle_bins(FULFILLMENT_BIN_WIDTH, tier_h, a_w, ff_h)
        if eff <= 0:
            continue
        n = max(1, round((share / weight_sum) * target_bins / eff))
        classes.append((a_w, n, eff))
        total += n * eff
    return classes, total


# Hard cap on aisle-split segments per aisle — bounds the aisle-count blow-up since _apply_caps'
# max_aisles is enforced BEFORE the split (it runs on replicas, not emitted segments).
MAX_AISLE_SPLIT_K: int = 8


def _aisle_split(a_w: int, target_bins: int, eff_fn, bin_width: int,
                 k, capacity_loss):
    """Cut an aisle of width `a_w` (holding `target_bins` bins for its bucket) into `k` shorter
    segments of width ~a_w/k, modeling cross-aisle throughways that consume `capacity_loss` of the
    bins.  Under one-way lanes, shorter aisles ⇒ less x-traversal.

    Returns (seg_w, n_segments, seg_eff, cap_total) or None (no split ⇒ byte-identical caller path).
    `eff_fn(width) -> bins-per-segment` (uniform_aisle_bins or catalog_aisle_bins bound to the
    tier).  k is bounded to ≤ a_w // bin_width (≥1 column/segment) and ≤ MAX_AISLE_SPLIT_K so the
    emitted aisle count cannot blow up.  Total usable bins ≈ target_bins·(1-capacity_loss) — bins are
    genuinely SACRIFICED (no compensatory aisles added), so aggressive loss raises fill by design."""
    loss = float(capacity_loss or 0.0)
    if not k or int(k) <= 1 or loss >= 1.0:
        return None
    max_k = max(1, a_w // bin_width)                 # can't have more segments than bin columns
    k_eff = min(int(k), max_k, MAX_AISLE_SPLIT_K)
    if k_eff <= 1:
        return None
    seg_w   = a_w // k_eff
    seg_eff = eff_fn(seg_w)
    if seg_eff <= 0:
        return None
    usable  = target_bins * (1.0 - loss)             # loss applied BEFORE the round
    n_seg   = max(k_eff, round(usable / seg_eff))    # ≥ k_eff so at least one full split happens
    return seg_w, n_seg, seg_eff, n_seg * seg_eff


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
            for u in viable_storage_units(c, _equilibrium_qty(c)):
                req[binkey_of(u)] += 1
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
        ff_aisle_width  : int | None = None,   # fulfillment aisle geometry (short shelves);
        ff_aisle_height : int | None = None,   # default width=aisle_width, height=~6 ft
        target_fill  : float = 0.85,
        min_bins     : int | None = None,
        max_bins     : int | None = None,
        max_aisles   : int | None = None,
        composition  : dict | None = None,
        regime_sizing: dict | None = None,
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

        regime_sizing: optional PER-REGIME sizing that overrides the single
        min_bins/max_bins/max_aisles/target_fill/composition scalars — a dict
        {'store': {...}, 'fulfillment': {...}} where each sub-dict may carry
        'mode' ('demand' | 'fixed'), 'min_bins', 'max_bins', 'max_aisles', 'fill',
        'composition', and (fulfillment 'fixed' mode) a tier 'distribution'
        {ff_small/ff_medium/ff_large: weight} + 'target_bins'.  When None (the
        default), the warehouse is sized globally exactly as before — every direct
        caller / test is unaffected.  When given, store and fulfillment are sized
        and capped INDEPENDENTLY (store demand-driven by default; fulfillment a
        fixed tier distribution scaled to target_bins/min/max), each with its own
        fill headroom.
        """
        req = cls.bucket_requirements(orders)

        # Fulfillment is its own self-contained regime (short shelves, small bins); size it
        # only when the inventory actually contains fulfillment items.
        has_ff = any(regime_of(c) == FULFILLMENT for c in orders)
        ff_w = ff_aisle_width  if ff_aisle_width  is not None else aisle_width
        ff_h = ff_aisle_height if ff_aisle_height is not None else FULFILLMENT_AISLE_HEIGHT

        # 1: enumerate every bucket with a ≥1 floor.
        bucket_list: list[tuple] = []     # (handling, category, size, unit_type)
        for h in handlings:
            for cat in categories:
                for size in _SIZES_DESCENDING:          # 4 pallet tiers
                    bucket_list.append((h, cat, size, 'pallet'))
                bucket_list.append((h, cat, 'singleton', 'singleton'))
        if has_ff:
            # One bucket per fulfillment tier under the single ('fulfillment','fulfillment')
            # family — NOT crossed with the store handlings/categories (no junk buckets).
            for size in _FF_SIZES_DESCENDING:
                bucket_list.append((FULFILLMENT, FULFILLMENT, size, FULFILLMENT))

        def _eff(bucket: tuple) -> int:
            _h, _c, size, unit_type = bucket
            if unit_type == FULFILLMENT:
                return catalog_aisle_bins(FULFILLMENT_BIN_WIDTH, FF_TIER_HEIGHTS[size], ff_w, ff_h)
            return uniform_aisle_bins(unit_type, size, aisle_width, aisle_height)

        # 2+3: base replicas then cap enforcement.  Two paths: a single GLOBAL pass
        # (regime_sizing None — byte-identical to the original) or INDEPENDENT per-regime
        # sizing (store demand/composition; fulfillment a fixed tier distribution), each
        # with its own caps + fill headroom.
        if regime_sizing is None:
            if composition is not None:
                target_total = (float(min_bins) if min_bins
                                else float(_demand_total(bucket_list, req, _eff, target_fill)))
                replicas = _ratio_replicas(
                    bucket_list, lambda b: _comp_weight(b, composition), _eff, target_total)
            else:
                replicas = _demand_replicas(bucket_list, req, _eff, target_fill)
            _apply_caps(bucket_list, replicas, _eff, min_bins, max_bins, max_aisles, log)
            fill_for = lambda b: target_fill
        else:
            store_buckets = [b for b in bucket_list if b[3] != FULFILLMENT]
            ff_buckets    = [b for b in bucket_list if b[3] == FULFILLMENT]
            scfg = regime_sizing.get('store', {}) or {}
            fcfg = regime_sizing.get('fulfillment', {}) or {}
            s_fill = scfg.get('fill', target_fill)
            f_fill = fcfg.get('fill', target_fill)

            # store partition: composition basis vector, else demand-driven.
            s_comp = scfg.get('composition')
            if s_comp is not None:
                s_target = (float(scfg['min_bins']) if scfg.get('min_bins')
                            else float(_demand_total(store_buckets, req, _eff, s_fill)))
                replicas = _ratio_replicas(
                    store_buckets, lambda b: _comp_weight(b, s_comp), _eff, s_target)
            else:
                replicas = _demand_replicas(store_buckets, req, _eff, s_fill)
            _apply_caps(store_buckets, replicas, _eff,
                        scfg.get('min_bins'), scfg.get('max_bins'), scfg.get('max_aisles'), log)

            # fulfillment partition: a FIXED tier distribution scaled to target_bins
            # (default: min_bins, else the demand-derived ff total), else demand-driven.
            if has_ff:
                if fcfg.get('mode', 'fixed') == 'fixed':
                    dist = fcfg.get('distribution') or {}
                    f_target = (fcfg.get('target_bins') or fcfg.get('min_bins')
                                or _demand_total(ff_buckets, req, _eff, f_fill))
                    replicas.update(_ratio_replicas(
                        ff_buckets, lambda b: dist.get(b[2], 0.0), _eff, float(f_target)))
                else:
                    replicas.update(_demand_replicas(ff_buckets, req, _eff, f_fill))
                _apply_caps(ff_buckets, replicas, _eff,
                            fcfg.get('min_bins'), fcfg.get('max_bins'), fcfg.get('max_aisles'), log)
            fill_for = lambda b: (f_fill if b[3] == FULFILLMENT else s_fill)

        # Optional fulfillment DEPTH tiering (regime_sizing path only): split each ff size
        # tier's aisles into shallow/deep shapes.  None/absent ⇒ single-width (byte-identical).
        ff_depth_classes = ((regime_sizing.get('fulfillment', {}) or {}).get('depth_classes')
                            if regime_sizing is not None else None)
        # Optional AISLE SPLIT per regime: cut each aisle into k shorter segments (throughway
        # construction) with a capacity loss.  None/{'k':1} ⇒ no split (byte-identical).
        ff_split    = ((regime_sizing.get('fulfillment', {}) or {}).get('aisle_split')
                       if regime_sizing is not None else None)
        store_split = ((regime_sizing.get('store', {}) or {}).get('aisle_split')
                       if regime_sizing is not None else None)

        # Build per-replica AisleConfig list + capacity map.
        aisle_configs: list = []
        capacity: dict[BinKey, int] = {}

        def _emit(h, cat, unit_type, a_w, a_h, sizes_arg, bin_w, bin_hs,
                  n_aisles, eff_at_aw, eff_fn, bin_width, split) -> int:
            """Emit `n_aisles` AisleConfigs of width a_w — OR, when `split` is set, cut each into k
            shorter segments (throughway construction) via _aisle_split.  Returns bins emitted.
            split=None/{'k':1} ⇒ emits exactly `n_aisles` at a_w ⇒ byte-identical."""
            sp = _aisle_split(a_w, n_aisles * eff_at_aw, eff_fn, bin_width,
                              (split or {}).get('k'), (split or {}).get('capacity_loss')) if split else None
            if sp:
                seg_w, n_seg, _seg_eff, cap_total = sp
                for _ in range(n_seg):
                    aisle_configs.append(AisleConfig(h, cat, unit_type, seg_w, a_h, sizes_arg,
                                                     None, bin_width=bin_w, bin_heights=bin_hs))
                return cap_total
            for _ in range(n_aisles):
                aisle_configs.append(AisleConfig(h, cat, unit_type, a_w, a_h, sizes_arg,
                                                 None, bin_width=bin_w, bin_heights=bin_hs))
            return n_aisles * eff_at_aw

        for b in bucket_list:
            h, cat, size, unit_type = b
            eff = _eff(b)
            rep = replicas[b]
            split = ff_split if unit_type == FULFILLMENT else store_split
            if unit_type == FULFILLMENT and ff_depth_classes:
                # Split this tier's target bins across depth classes (differing WIDTH aisles sharing
                # this BinKey); aisle_split, if set, cuts EACH depth-class aisle further.
                classes, _cap = _ff_depth_split(size, rep * eff, ff_depth_classes, ff_h)
                eff_fn = lambda w, _s=size: catalog_aisle_bins(FULFILLMENT_BIN_WIDTH,
                                                               FF_TIER_HEIGHTS[_s], w, ff_h)
                cap_b = 0
                for (a_w, n_aisles, eff_c) in classes:
                    cap_b += _emit(h, cat, unit_type, a_w, ff_h, [size], FULFILLMENT_BIN_WIDTH,
                                   {size: FF_TIER_HEIGHTS[size]}, n_aisles, eff_c, eff_fn,
                                   FULFILLMENT_BIN_WIDTH, split)
                capacity[b] = cap_b
                continue
            if unit_type == FULFILLMENT:
                sizes_arg, a_w, a_h = [size], ff_w, ff_h
                bin_w, bin_hs = FULFILLMENT_BIN_WIDTH, {size: FF_TIER_HEIGHTS[size]}
                eff_fn = lambda w, _s=size: catalog_aisle_bins(FULFILLMENT_BIN_WIDTH,
                                                               FF_TIER_HEIGHTS[_s], w, ff_h)
                bin_width = FULFILLMENT_BIN_WIDTH
            else:
                sizes_arg = ['singleton'] if unit_type == 'singleton' else [size]
                a_w, a_h  = aisle_width, aisle_height
                bin_w, bin_hs = None, None
                eff_fn = lambda w, _u=unit_type, _s=size: uniform_aisle_bins(_u, _s, w, aisle_height)
                bin_width = unit_bin_width(unit_type)
            capacity[b] = _emit(h, cat, unit_type, a_w, a_h, sizes_arg, bin_w, bin_hs,
                                rep, eff, eff_fn, bin_width, split)

        # Totals from the ACTUAL emitted configs/capacity (identical to Σrep·eff when no split;
        # correct when a tier was split into differing-width / shorter aisles).
        total_aisles = len(aisle_configs)
        total_bins   = sum(capacity.values())

        # 4: sample SKUs to fill capacity to target_fill.  Skipped when sample=
        # False (e.g. analysis only needs the warehouse shape + aisle maps, not
        # a restocked inventory) — this avoids re-stocking the whole inventory.
        if sample:
            sampled, allowlist = cls.sample_to_capacity(
                orders, capacity, target_fill=target_fill, fill_for=fill_for, rng=rng)
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
        fill_for    : Any = None,
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
        # Per-bucket fill headroom: fill_for(bucket) when supplied (per-regime store vs ff),
        # else the single target_fill (byte-identical to the original scalar path).
        free   = {b: int(cap * (fill_for(b) if fill_for is not None else target_fill))
                  for b, cap in capacity.items()}

        def _reachable(c: Order) -> list[tuple[BinKey, int, bool]]:
            """(bucket, qty_per_unit, is_singleton) options this order can fill.
            qty_per_unit is the quantity whose pallet lands exactly in that tier,
            so a full pallet of it never needs a _fit recheck at fill time."""
            shc  = c.storage_handle_config
            opts: list[tuple[BinKey, int, bool]] = []
            if regime_of(c) == FULFILLMENT:
                # Fulfillment orders reach only their own ff tiers (never pallet/singleton).
                # The bool flag is unused for ff (every unit is a FulfillmentBin) — kept for
                # tuple shape / stock_plan compatibility.
                for size in _FF_SIZES_DESCENDING:
                    q = _max_qty_fitting_size(c, size, FULFILLMENT)
                    if q > 0 and FulfillmentBin(c, q).storage_size == size:
                        opts.append(((shc.handling, shc.category, size, FULFILLMENT), q, True))
                return opts
            for size in _SIZES_DESCENDING:
                q = _max_qty_fitting_size(c, size, 'pallet')
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
            # Capped final slot: a smaller quantity can drop a pallet/ff unit into a
            # SMALLER tier, so charge the bucket it ACTUALLY lands in.  Branch on the
            # bucket's unit_type (robust for pallet vs singleton vs fulfillment).
            per = cap_qty
            if per <= 0:
                return False
            unit_type = b[3]
            if unit_type == 'singleton':
                actual_b = b
            elif unit_type == FULFILLMENT:
                actual_b = binkey_of(FulfillmentBin(c, per))
            else:
                actual_b = binkey_of(Pallet(c, per))
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
