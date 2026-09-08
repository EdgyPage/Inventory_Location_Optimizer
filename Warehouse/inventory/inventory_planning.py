"""inventory_planning.py — warehouse sizing/fielding (pre-instantiation).

`PlanningMixin` holds the static/class methods that size a warehouse FROM an inventory
before any manager instance or warehouse exists.  Mixed into Inventory_Manager so the
public API is `Inventory_Manager.plan_warehouse(...)`, `.bucket_requirements(...)`,
`.field_requirement(...)`.

THE PLANNER HAS ONE CONTRACT (department-calibration, "Field the floor" decisions 1-4 and
8-11, built by "Field the requirement").  `bucket_requirements` states, per bin bucket,
exactly what the run's declared stock levels need; every bucket is sized from that statement;
and every SKU is then FIELDED at exactly its declaration, packed the way that statement
counted it.  There is no re-choice, no growth into leftover capacity, and no shrink to fit —
so `planned_sum_q == sum_q`, and a fill rate priced before the plan equals one priced after.

That makes the line floor a PROMISE rather than a request.  The planner used to size a
warehouse and then sample SKUs into whatever it had built, which is how a third of the
reference pair's fulfillment section came to be fielded below its own line floor: a shelf
below the line is a treadmill under base stock, and the equilibrium check's clauses cannot
pass under one.  Where the promise cannot be kept — a cap, a composition vector or an aisle
split that leaves a bucket short — the run REFUSES (`UnfieldableRequirement`) and names the
bucket and the bins short, rather than fielding less in silence.

`store_fill` / `ff_fill` therefore mean "the share of each bucket the declared levels occupy
at setup"; the rest is free bins, which is the headroom a base-stock top-up lands in.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from Warehouse.catalog.Order import Order
from Warehouse.layout.Aisle_Dimensions import (
    uniform_aisle_bins, catalog_aisle_bins, unit_bin_width,
    FULFILLMENT_BIN_WIDTH, FF_TIER_HEIGHTS, FULFILLMENT_AISLE_HEIGHT,
)
from Warehouse.layout.Warehouse_Builder import AisleConfig, WarehouseConfig
from Warehouse.layout.Storage_Primitive import (
    Singleton, FulfillmentBin, viable_storage_units,
)
from Warehouse.kernel.regime import FULFILLMENT, regime_of
from Warehouse.inventory.inventory_common import (
    BinKey, binkey_of, WarehousePlan, UnfieldableRequirement,
    _SIZES_DESCENDING, _FF_SIZES_DESCENDING, _equilibrium_qty,
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


def _demand_replicas(buckets, req, eff_fn, fill_fn) -> dict:
    """Demand-driven replicas: max(1, ceil(bucket_requirement / (bins_per_aisle · fill))).

    `fill_fn(bucket)` is the EFFECTIVE fill — the declared `store_fill`/`ff_fill` already
    divided down by an aisle split's capacity loss (`_split_inflation`), so a split's
    sacrificed bins are paid for in replicas here rather than discovered as a shortfall at
    the promise check."""
    out = {}
    for b in buckets:
        eff = eff_fn(b)
        out[b] = max(1, math.ceil(req.get(b, 0) / (eff * fill_fn(b)))) if eff else 1
    return out


def _demand_total(buckets, req, eff_fn, fill_fn) -> int:
    """Total bins the demand-driven replicas would occupy (the default scale for ratio modes)."""
    return sum(r * eff_fn(b) for b, r in _demand_replicas(buckets, req, eff_fn, fill_fn).items())


def _ratio_replicas(buckets, weight_fn, eff_fn, target_total: float) -> dict:
    """Replicas allocated proportionally to weight_fn(bucket), scaled to ~target_total bins.
    Used by the store's composition basis vector -- the only caller left since fulfillment's
    fixed tier distribution was retired."""
    weights = {b: weight_fn(b) for b in buckets}
    tw = sum(weights.values()) or 1.0
    out = {}
    for b in buckets:
        eff = eff_fn(b)
        out[b] = max(1, round(target_total * weights[b] / tw / eff)) if eff else 1
    return out


def structural_bin_floor(handlings, categories, aisle_width, aisle_height) -> tuple[int, int]:
    """(aisles, bins) the STORE cannot go below, for any catalogue: one aisle per bucket.

    Buckets are the handling x category x tier cross-product, so this floor is a property of
    the CONFIGURATION, not of the SKUs — capping SKUs does not lower it.  `_apply_caps` never
    trims a bucket below one replica (every SKU must be placeable), so a `--s-max-bins` under
    this number is unachievable by construction.  Exposed so a CLI can say so in milliseconds
    instead of mid-build, per pair, after the inventory load.
    """
    aisles = bins = 0
    for _h in handlings:
        for _c in categories:
            for size in _SIZES_DESCENDING:
                bins += uniform_aisle_bins('pallet', size, aisle_width, aisle_height)
                aisles += 1
            bins += uniform_aisle_bins('singleton', 'singleton', aisle_width, aisle_height)
            aisles += 1
    return aisles, bins


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
    classes = [(aisle_width, n_aisles, eff_per_aisle), …].  Preserves total bins (rounding UP)
    while growing the aisle count; guarantees ≥1 aisle per positive-share class so no tier is
    dropped.  Classes with columns < 1 bin width or non-positive share are skipped.

    The aisle count rounds UP for the same reason `_aisle_split`'s does: the bins a bucket was
    sized for are the bins its declared levels need, and `_demand_replicas` leaves only the
    ~3% slack of one `ceil`.  Rounding each class down spends that slack on nothing — measured
    at 85 to 295 bins short on a single depth class over a 4,000-SKU catalogue, which is a
    REFUSED run.  Over-emitting is free bins; under-emitting is a run that will not start."""
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
        n = max(1, math.ceil((share / weight_sum) * target_bins / eff - 1e-9))
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
    emitted aisle count cannot blow up.  Total usable bins ≈ target_bins·(1-capacity_loss): the
    bins in the throughways are genuinely sacrificed HERE.

    WHAT THE SPLIT NOW MEANS, which is not what it used to.  The loss no longer reaches the
    inventory: `_split_inflation` adds the compensatory aisles at SIZING time, so a split
    bucket is asked for `1/(1-loss)` times the replicas and the run still holds its declared
    levels ("Field the floor", decision 9 — the line floor is a promise, and a throughway is
    not a reason to break it).  A split arm therefore trades AISLES for travel, not capacity
    for travel; it used to raise fill by shrinking the shelf under a fixed stock, and an
    archived `ks` sweep measured that older trade, so its results are not comparable to a
    new one."""
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
    usable  = target_bins * (1.0 - loss)             # loss applied BEFORE the rounding
    # ROUND UP, not to nearest: `capacity_loss` is the declared sacrifice, and rounding the
    # segment count down sacrifices a second, undeclared slice on top of it — 1,800 ff_large
    # bins at a 30% loss came out 1,200 rather than 1,260, a realized 33%.  That extra is
    # what `_split_inflation` cannot price (it knows the declared loss, not the rounding), and
    # it landed as a bucket 47 bins short of the levels the run had declared.
    n_seg   = max(k_eff, math.ceil(usable / seg_eff - 1e-9))   # ≥ k_eff: one full split at least
    return seg_w, n_seg, seg_eff, n_seg * seg_eff


def _split_inflation(split, a_w: int, bin_width: int) -> float:
    """`1 / (1 - capacity_loss)` when an aisle split would actually cut this bucket's aisles,
    else 1.0 — the factor its demand replicas inflate by so the bins the split SACRIFICES do
    not come out of the declared levels ("Field the floor", decision 9).

    Mirrors `_aisle_split`'s own admission test (k > 1 after bounding by columns-per-segment
    and MAX_AISLE_SPLIT_K, loss < 1); a split that would not apply inflates nothing, so a
    configuration the emitter ignores never over-sizes the warehouse either.
    """
    if not split:
        return 1.0
    k = split.get('k')
    loss = float(split.get('capacity_loss') or 0.0)
    if not k or int(k) <= 1 or loss <= 0.0 or loss >= 1.0:
        return 1.0
    if min(int(k), max(1, a_w // bin_width), MAX_AISLE_SPLIT_K) <= 1:
        return 1.0
    return 1.0 / (1.0 - loss)


class PlanningMixin:

    # ── warehouse planning (pre-instantiation) ────────────────────────────────
    # These size a warehouse FROM an inventory, before any manager instance or
    # warehouse exists, so they are static/class methods.  They guarantee every
    # (handling, category, size, unit_type) bucket has at least one aisle, so
    # every SKU is structurally placeable, then add the replicas the declared
    # levels need and field every SKU at exactly its declaration.

    @staticmethod
    def _packing(orders: list[Order]) -> tuple[dict, dict]:
        """One pass over the catalogue answering BOTH halves of the planner's contract:
        `({bucket: bins needed}, {id(order): [(is_singleton, qty_per_unit, count), ...]})`.

        Sizing and fielding are the same statement — what the declared levels need, and how
        they are packed to need it — so they are computed together rather than by two passes
        that could drift apart.  (They also each cost about a second per 20k SKUs, and the
        coverage fixed point re-plans a 400k-SKU catalogue every round.)

        The run-length flag is tested POSITIVELY (`isinstance(u, (Singleton, FulfillmentBin))`)
        and never as `not isinstance(u, Pallet)`: both of those classes SUBCLASS Pallet, so the
        negative test calls every singleton a pallet — which repacks a remainder as a partial
        pallet and charges a bucket the warehouse was not sized for.
        """
        req: dict[BinKey, int] = defaultdict(int)
        slots: dict[int, list] = {}
        for c in orders:
            runs: list[tuple[bool, int, int]] = []
            for u in viable_storage_units(c, _equilibrium_qty(c)):
                req[binkey_of(u)] += 1
                is_single = isinstance(u, (Singleton, FulfillmentBin))
                if runs and runs[-1][0] == is_single and runs[-1][1] == u.quantity:
                    runs[-1] = (is_single, u.quantity, runs[-1][2] + 1)
                else:
                    runs.append((is_single, int(u.quantity), 1))
            slots[id(c)] = runs
        return dict(req), slots

    @classmethod
    def bucket_requirements(cls, orders: list[Order]) -> dict[BinKey, int]:
        """Exact bin count per (handling, category, storage_size, unit_type)
        bucket, computed by running each order through viable_storage_units at
        its equilibrium_qty.  This is the authoritative per-tier demand — and,
        since "Field the requirement", also the packing the run fields."""
        return cls._packing(orders)[0]

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
        log          : Any = None,
    ) -> 'WarehousePlan':
        """Size a warehouse to hold *orders* at their DECLARED levels, and field them there.

        1. Enumerate the full bucket set (every handling×category gets 4 pallet
           size tiers + 1 singleton) — the structural floor that guarantees
           every SKU has a place.
        2. Replica per bucket either from the requirement (default,
           max(1, ceil(requirement/(eff·fill)))) or from an explicit
           *composition* basis vector (bins ∝ weight).
        3. Scale up to >= min_bins, then down to <= max_bins/max_aisles (never
           below 1 replica/bucket; min_bins wins if it conflicts with max).
        4. Check the promise on the EMITTED capacity — `capacity · fill >= requirement`
           per bucket — and REFUSE (`UnfieldableRequirement`) when a bucket is short.
        5. Field every SKU at exactly its declaration, packed as `bucket_requirements`
           counted it (`field_requirement`).  Skipped when sample=False, which wants only
           the warehouse SHAPE; the sizing and the promise check still run, because both
           read the declaration rather than the fielding.

        Planning is DETERMINISTIC: there is no sampling left to seed.  A caller that used to
        pass `rng` was seeding a SKU contest for capacity that no longer happens.

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
        'min_bins', 'max_bins', 'max_aisles', 'fill', 'composition', and the two
        aisle-shape knobs ('depth_classes', 'aisle_split').  When None (the
        default), the warehouse is sized globally.  When given, store and fulfillment are
        sized and capped INDEPENDENTLY, each with its own fill headroom.

        Both regimes are sized from the requirement.  Fulfillment's fixed tier distribution
        ('mode', 'distribution', 'target_bins') is RETIRED: it spread a demand-derived bin
        total 0.5/0.3/0.2 across the three tiers while the declared levels needed 11/63/26,
        which is how ff_medium came to be short by 293k bins on a section that was, in total,
        the right size ("Field the floor", decision 8).  Depth classes and the aisle split
        survive — they reshape aisles, not the bin count.
        """
        # ONE pass answers both halves: what the declared levels need per bucket (sizing)
        # and how each SKU is packed to need it (fielding).  Two passes could drift apart;
        # this way the promise the check enforces is true by construction.
        req, packing = cls._packing(orders)

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

        # Optional fulfillment DEPTH tiering (regime_sizing path only): split each ff size
        # tier's aisles into shallow/deep shapes.  None/absent ⇒ single-width.
        ff_depth_classes = ((regime_sizing.get('fulfillment', {}) or {}).get('depth_classes')
                            if regime_sizing is not None else None)
        # Optional AISLE SPLIT per regime: cut each aisle into k shorter segments (throughway
        # construction) with a capacity loss.  None/{'k':1} ⇒ no split.
        ff_split    = ((regime_sizing.get('fulfillment', {}) or {}).get('aisle_split')
                       if regime_sizing is not None else None)
        store_split = ((regime_sizing.get('store', {}) or {}).get('aisle_split')
                       if regime_sizing is not None else None)

        def _split_for(bucket: tuple):
            """(aisle_split cfg, aisle width, bin width) for a bucket — what `_split_inflation`
            needs to know whether this bucket's aisles get cut, and by how much."""
            if bucket[3] == FULFILLMENT:
                return ff_split, ff_w, FULFILLMENT_BIN_WIDTH
            return store_split, aisle_width, unit_bin_width(bucket[3])

        # 2+3: base replicas then cap enforcement.  Two paths: a single GLOBAL pass
        # (regime_sizing None) or INDEPENDENT per-regime sizing, each with its own caps +
        # fill headroom.  Both size from `req` unless an explicit composition basis vector
        # asks for a differently-SHAPED warehouse; the promise check below is what keeps a
        # composition (or a cap) from quietly starving a bucket.
        if regime_sizing is None:
            fill_for = lambda b: target_fill
        else:
            scfg = regime_sizing.get('store', {}) or {}
            fcfg = regime_sizing.get('fulfillment', {}) or {}
            # The retired fixed-distribution knobs REFUSE rather than being ignored.  A
            # sizing dict is assembled from CONFIG and restored from a run spec, and a key
            # nobody reads any more is the "silently reverts to its default" failure this
            # repo has been bitten by: the caller believes it asked for a tier mix and gets
            # the requirement's.  Say so instead ("Field the floor", decision 8).
            _retired = {'mode', 'distribution', 'target_bins'}
            for _name, _cfg in (('store', scfg), ('fulfillment', fcfg)):
                _found = sorted(_retired & set(_cfg))
                if _found:
                    raise ValueError(
                        f'regime_sizing[{_name!r}] carries {_found}, which the planner no '
                        f'longer reads. Both regimes are sized from what the run\'s declared '
                        f'levels need; the fixed tier distribution was retired because it '
                        f'spread bins 0.5/0.3/0.2 while the levels needed 11/63/26, leaving '
                        f'30% of a section below its line floor. Drop the key.')
            s_fill = scfg.get('fill', target_fill)
            f_fill = fcfg.get('fill', target_fill)
            fill_for = lambda b: (f_fill if b[3] == FULFILLMENT else s_fill)

        def _fill_eff(bucket: tuple) -> float:
            """The bucket's declared fill, divided down by the bins its aisle split sacrifices
            — so sizing asks for the replicas that leave the requirement covered AFTER the cut."""
            return fill_for(bucket) / _split_inflation(*_split_for(bucket))

        if regime_sizing is None:
            if composition is not None:
                target_total = (float(min_bins) if min_bins
                                else float(_demand_total(bucket_list, req, _eff, _fill_eff)))
                replicas = _ratio_replicas(
                    bucket_list, lambda b: _comp_weight(b, composition), _eff, target_total)
            else:
                replicas = _demand_replicas(bucket_list, req, _eff, _fill_eff)
            _apply_caps(bucket_list, replicas, _eff, min_bins, max_bins, max_aisles, log)
        else:
            store_buckets = [b for b in bucket_list if b[3] != FULFILLMENT]
            ff_buckets    = [b for b in bucket_list if b[3] == FULFILLMENT]

            # store partition: composition basis vector, else demand-driven.
            s_comp = scfg.get('composition')
            if s_comp is not None:
                s_target = (float(scfg['min_bins']) if scfg.get('min_bins')
                            else float(_demand_total(store_buckets, req, _eff, _fill_eff)))
                replicas = _ratio_replicas(
                    store_buckets, lambda b: _comp_weight(b, s_comp), _eff, s_target)
            else:
                replicas = _demand_replicas(store_buckets, req, _eff, _fill_eff)
            _apply_caps(store_buckets, replicas, _eff,
                        scfg.get('min_bins'), scfg.get('max_bins'), scfg.get('max_aisles'), log)

            # fulfillment partition: demand-driven, exactly as the store is.
            if has_ff:
                replicas.update(_demand_replicas(ff_buckets, req, _eff, _fill_eff))
                _apply_caps(ff_buckets, replicas, _eff,
                            fcfg.get('min_bins'), fcfg.get('max_bins'), fcfg.get('max_aisles'), log)

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

        # 4: THE PROMISE.  Every bucket's EMITTED capacity, at its declared fill, must hold
        # the bins the declared levels need.  Sizing from `req` satisfies this by
        # construction, so a shortfall here means a second declaration overrode the first —
        # a cap, a composition basis vector, or an aisle split whose loss the inflation did
        # not cover — and the run says which bucket rather than fielding less in silence
        # ("Field the floor", decisions 3 and 9).  A bucket in `req` with no capacity at all
        # is the same failure at its limit: a SKU whose handling/category the caller did not
        # enumerate has nowhere in this warehouse to be.
        fielding: dict[BinKey, dict] = {}
        short: list = []
        for b in sorted(set(req) | set(capacity), key=repr):
            need   = int(req.get(b, 0))
            cap    = int(capacity.get(b, 0))
            budget = math.floor(cap * fill_for(b) + 1e-9)
            fielding[b] = {'requirement': need, 'capacity': cap,
                           'budget': int(budget), 'free': cap - need}
            if need > budget:
                short.append((b, need, cap, int(budget)))
        if short:
            _lines = '\n'.join(
                f'    {h}/{cat}/{size}/{unit}: needs {need:,} bins, '
                f'{cap:,} emitted x fill = {bud:,} available ({need - bud:,} short)'
                for (h, cat, size, unit), need, cap, bud in short[:12])
            raise UnfieldableRequirement(
                f'{len(short)} bucket(s) cannot hold the stock levels this run declared, so '
                f'the line floor cannot be kept:\n{_lines}'
                + (f'\n    ... and {len(short) - 12} more' if len(short) > 12 else '')
                + '\nThe warehouse is sized from the declaration, so something overrode that '
                  'sizing: a max_bins/max_aisles cap, a composition basis vector, an aisle '
                  'split, or a depth-class split. Raise the cap (or lower coverage_days / '
                  'floor_lines) -- fielding less than the floor is a treadmill under base '
                  'stock, not a smaller run.',
                short)

        # 5: field every SKU at exactly its declaration.  Skipped when sample=False (e.g.
        # analysis only needs the warehouse shape + aisle maps, not a restocked inventory) —
        # this avoids re-packing the whole inventory.
        if sample:
            sampled, allowlist = cls.field_requirement(orders, packing)
            expected_fill = sum(req.values()) / total_bins if total_bins else 0.0
        else:
            sampled, allowlist = [], set()
            expected_fill = 0.0

        n = total_aisles
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
            requirement   = req,
            fielding      = fielding,
        )

    @classmethod
    def field_requirement(cls, orders: list[Order], slots: dict | None = None
                          ) -> tuple[list[Order], set]:
        """Field every order at exactly its declared level, packed exactly as
        `bucket_requirements` counted it.  Returns (fielded_orders, their sku ids).

        `slots` is the packing half of `_packing`, keyed by `id(order)`.  `plan_warehouse`
        passes the one it already computed to size the buckets, which is what makes the two
        halves the same statement rather than two passes that agree by luck; omit it and this
        computes its own.

        THE PACKING IS THE REQUIREMENT.  `bucket_requirements` states what the declared
        levels need by running each order through `viable_storage_units` at its
        order-up-to; this runs the same call and RECORDS the answer as the order's
        `stock_plan`, so the bins the warehouse was sized for are the bins the run fields,
        and every later reorder rebuilds the same tier mix.  The two agree by construction
        rather than by margin.

        WHAT THIS REPLACED, and why (department-calibration, "Field the floor", decision 2).
        This used to be `sample_to_capacity`: it sized bin capacity independently, then held
        a contest for it — phase 1 round-robined every SKU into its EMPTIEST reachable
        bucket until it reached its order-up-to, phase 2 shared out whatever was left, and
        the order was re-declared at whatever it won.  Both halves broke the floor.  The
        emptiest-bucket rule charged multi-tier SKUs at low density to buckets budgeted for
        others, so single-tier SKUs stopped two to six units short of their own line floor —
        25,241 of them (15.8%) on the reference pair even with every tier's budget above its
        requirement.  Phase 2 then grew 64,989 other SKUs above their declaration, so a
        level the run recorded as declared was not the level it fielded.  A shelf below the
        line is a treadmill under base stock, and a level grown past the declaration is a
        coverage nobody chose.

        So there is no contest left to hold: no re-choice of tier, no growth into leftover
        capacity, no shrink to fit.  Every SKU fits because `plan_warehouse` sized its
        buckets from this same statement and REFUSED the run if it could not.  Nothing here
        is random — the old `rng` seeded the contest, and the contest is gone.

        `Order.declare_stock` is the one mutation site for the four level slots (ADR-0002);
        the order-up-to, the reorder point and the pipeline stamp pass through unchanged,
        because the planner no longer has an opinion about them.
        """
        packing = cls._packing(orders)[1] if slots is None else slots
        fielded: list[Order] = []
        for c in orders:
            q    = _equilibrium_qty(c)
            runs = packing[id(c)]
            if not runs:
                # `_build_units` answers [] when a SKU's geometry fits no unit class, and an
                # empty plan fields nothing.  Say so HERE: left alone it surfaces two hundred
                # lines away as "the section fielded fewer units than it declared", which
                # points at the planner rather than at the one SKU that cannot be packed.
                raise UnfieldableRequirement(
                    f'SKU {c.sku} ({c.length}x{c.width}x{c.height}, '
                    f'{c.storage_handle_config.handling}/{c.storage_handle_config.category}) '
                    f'fits no pallet, singleton or fulfillment unit, so its declared '
                    f'{q} unit(s) cannot be fielded anywhere in the warehouse.')
            c.declare_stock(q, c.reorder_point, stock_plan=runs,
                            pipeline_qty=getattr(c, 'pipeline_qty', None))
            fielded.append(c)
        return fielded, {c.sku for c in fielded}
