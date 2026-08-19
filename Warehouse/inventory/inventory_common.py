"""inventory_common.py — leaf types/constants/helpers shared by the Inventory_Manager
mixins and by Assignment_Functions.

Extracted so the manager can be split into cohesive mixin modules
(inventory_planning / inventory_optimal / inventory_reorder) without import cycles:
this module depends only on the low-level warehouse primitives, never on the mixins or
on Inventory_Management itself.  Inventory_Management re-exports these names for
backward compatibility (`from Inventory_Management import Placement, BinKey, ...`).
"""
from __future__ import annotations

import bisect
import operator
import random
from dataclasses import dataclass
from typing import Any, Callable

from Warehouse.catalog.Order import Order
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Storage_Primitive import StorageUnit, Pallet, Storage_Size, FulfillmentBin
from Warehouse.kernel.regime import STORE, FULFILLMENT, regime_of  # noqa: F401  (re-exported for callers)

AssignmentFn = Callable[[StorageUnit, list[Aisle.Bin]], Aisle.Bin | None]

# Takes a list of units and a candidate-bin callback; returns (unit, bin|None)
# pairs in priority order.  All units share the same BinKey group.
RankedAssignmentFn = Callable[
    [list[StorageUnit], Callable[[StorageUnit], list[Aisle.Bin]]],
    list[tuple[StorageUnit, 'Aisle.Bin | None']],
]


class Placement:
    """One named placement policy — the single object a strategy hands the manager.

    ``place_one`` (per-unit: ``(unit, candidates) -> bin|None``) is ALWAYS present; it
    drives the per-unit drain (initial stock, FIFO/cohesion reorders) and places the
    stragglers a ranked wave leaves behind.  ``place_wave``
    (``(units, candidates_fn) -> [(unit, bin|None)]``), when present, makes this a
    RANKED policy: a whole BinKey group is placed at once in pick-effort order.

    Because every strategy sets exactly one ``mgr.placement`` (never None), the ranked
    drain is just a policy that also carries a ``place_wave`` — no special-casing, and
    a future ranked-cohesion policy is expressible the same way.
    """
    __slots__ = ('name', 'place_one', 'place_wave', 'uses_aisle_index', 'order_score')

    def __init__(self, name: str, place_one: AssignmentFn,
                 place_wave: 'RankedAssignmentFn | None' = None,
                 order_score: 'Callable[[Any], float] | None' = None) -> None:
        self.name             = name
        self.place_one        = place_one
        self.place_wave       = place_wave
        # the per-unit fn declares whether it reads mgr._aisle_index (coupling guard)
        self.uses_aisle_index = bool(getattr(place_one, 'uses_aisle_index', False))
        # Per-policy enqueue ordering: (unit)->float, sorted DESCENDING before placement.
        # Decouples queue order from the placement impl so a policy is never forced into
        # an ordering that fights it.  None ⇒ the ranked wave's default pick-effort order.
        self.order_score      = order_score

    @property
    def is_ranked(self) -> bool:
        return self.place_wave is not None


@dataclass
class LoadParams:
    lambda_: float = 1.0   # startup-cost multiplier
    k: float       = 1.0   # pickers per task (normally 1 for single-aisle tasks)
    gamma: float   = 1.5   # congestion exponent


@dataclass
class WarehousePlan:
    """Result of Inventory_Manager.plan_warehouse: a sized warehouse + the
    SKU sample chosen to fill it to target utilization."""
    warehouse_cfg : Any                   # WarehouseConfig
    sampled       : list                  # orders to actually stock
    sku_allowlist : set                   # sku ids in `sampled`
    capacity      : dict                  # BinKey -> bins available in warehouse
    aisle_configs : list                  # the per-replica AisleConfig list
    total_aisles  : int
    total_bins    : int
    expected_fill : float


_SIZE_RANKS: dict[str, int] = {
    size: rank
    for rank, size in enumerate(
        sorted(Storage_Size.available_sizes_heights, key=Storage_Size.available_sizes_heights.__getitem__)
    )
}

# Sizes ordered from largest to smallest — used by _candidates for O(1) tier lookup.
_SIZES_DESCENDING: tuple[str, ...] = tuple(
    sorted(_SIZE_RANKS, key=_SIZE_RANKS.__getitem__, reverse=True)
)

# ── fulfillment size tiers (parallel to the pallet tiers above) ──────────────────
# Fulfillment bins have their own short size tiers; the placement spill-up (_candidates)
# and the planner's reachability need per-unit_category rank tables, so derive them from
# FulfillmentBin.TIERS exactly as _SIZE_RANKS is derived from the pallet tiers.
_FF_TIER_HEIGHTS: dict[str, int] = dict(FulfillmentBin.TIERS)
_FF_SIZE_RANKS: dict[str, int] = {
    size: rank
    for rank, size in enumerate(sorted(_FF_TIER_HEIGHTS, key=_FF_TIER_HEIGHTS.__getitem__))
}
_FF_SIZES_DESCENDING: tuple[str, ...] = tuple(
    sorted(_FF_SIZE_RANKS, key=_FF_SIZE_RANKS.__getitem__, reverse=True)
)


# ── unit-class registry (per unit_category) ─────────────────────────────────────
# One row per size-tiered bin family: its StorageUnit class + tier rank tables.
# tier_ranks_for() and _max_qty_fitting_size() read this instead of per-family
# branches/twin functions — adding a bin family = adding a row here.
UNIT_CLASSES: dict[str, tuple] = {
    'pallet':    (Pallet,         _SIZE_RANKS,    _SIZES_DESCENDING),
    FULFILLMENT: (FulfillmentBin, _FF_SIZE_RANKS, _FF_SIZES_DESCENDING),
}


def tier_ranks_for(unit_category: str) -> tuple[dict, tuple]:
    """(size_ranks, sizes_descending) for a unit_category — registry lookup; unknown
    categories (e.g. 'singleton', which has no tiers) fall back to the pallet tables,
    preserving the original behaviour."""
    _cls, ranks, sizes_desc = UNIT_CLASSES.get(unit_category, UNIT_CLASSES['pallet'])
    return ranks, sizes_desc


_by_location = operator.attrgetter('location')


class _SortedBins:
    """A set-of-bins whose iteration order is PERMANENTLY `location` order.

    Replaces the `set[Aisle.Bin]` values of `_sku_singleton_bins` / `_sku_pallet_bins` so
    `Task.from_batch` can drain a SKU's bins without paying `sorted(...)` twice per batch
    SKU (the deep-ladder t_task k≈2.3 offender: batch-SKU count AND per-SKU bin multiplicity
    both grow with the catalogue).  The maintained order is exactly what those sorts
    produced — `location = (aisle_id, bayX, bayY)` is immutable for a bin's lifetime and
    globally unique (monotone aisle ids, unique bay tuples), so iteration here is
    byte-identical to `sorted(old_set, key=location)`.

    Semantics preserved from `set`: identity membership (Aisle.Bin has no __eq__/__hash__),
    `discard` of a non-member is a silent no-op, truthiness/len.  Duplicate `add` is an
    ASSERT rather than a silent dedupe: `_execute_placement` cannot re-place an indexed bin
    (`_index_remove` pops `_bin_index_pos` first), so a duplicate here is a real bug.

    The bisect-with-key idiom mirrors `_index_add`/`_index_remove`
    (Inventory_Management.py) — the in-repo precedent this container copies.
    """
    __slots__ = ('_bins',)

    def __init__(self) -> None:
        self._bins: list = []

    def add(self, bin_) -> None:
        assert bin_ not in self, f'duplicate add of bin {bin_.location} to _SortedBins'
        bisect.insort(self._bins, bin_, key=_by_location)

    def discard(self, bin_) -> None:
        i = bisect.bisect_left(self._bins, bin_.location, key=_by_location)
        # unique keys ⇒ at most one candidate; identity check keeps set semantics exact
        if i < len(self._bins) and self._bins[i] is bin_:
            del self._bins[i]

    def __contains__(self, bin_) -> bool:
        try:
            loc = bin_.location
        except AttributeError:
            return False
        i = bisect.bisect_left(self._bins, loc, key=_by_location)
        return i < len(self._bins) and self._bins[i] is bin_

    def __iter__(self):
        return iter(self._bins)

    def __len__(self) -> int:
        return len(self._bins)

    def __bool__(self) -> bool:
        return bool(self._bins)

    def __repr__(self) -> str:
        return f'_SortedBins({[b.location for b in self._bins]})'


def _wp_for(wp, obj):
    """Resolve the WorkloadParams for *obj*'s storage regime from a mixed-warehouse
    ``wp.by_regime`` map, falling back to *wp* itself.  Placement/assignment sites process
    one regime at a time (a BinKey group / a single unit), so resolving from that entity is
    exact; single-regime runs carry no map and return *wp* unchanged (store byte-identical)."""
    by = getattr(wp, 'by_regime', None)
    if by:
        return by.get(regime_of(obj), wp)
    return wp


BinKey = tuple[str, str, str, str]


def binkey_of(obj) -> BinKey:
    """The 4-tuple BinKey of a StorageUnit or an Aisle.Bin (duck-typed like regime_of).

    Units read (handling, category) from their order's storage_handle_config plus their
    own (storage_size, unit_category) — Singleton's fixed 'singleton' label comes from
    Singleton.storage_size itself, so no special-casing.  Bins read their four mirror
    attributes.  One constructor for the key that used to be hand-built at ~8 sites.
    """
    order = getattr(obj, 'order', None)
    if order is not None:                    # StorageUnit (Pallet/Singleton/FulfillmentBin)
        shc = order.storage_handle_config
        return (shc.handling, shc.category, obj.storage_size, obj.unit_category)
    return (obj.handling_type, obj.storage_type, obj.storage_size, obj.unit_type)


def _equilibrium_qty(order: Order) -> int:
    """Return the Order-Up-To target for *order*.

    Reads equilibrium_qty if present (new schema); falls back to the legacy
    stock_qty attribute so old in-memory inventories still work correctly.
    """
    return getattr(order, 'equilibrium_qty',
                   getattr(order, 'stock_qty', 1))


def _max_qty_fitting_size(order: Order, target_size: str,
                          unit_category: str = 'pallet') -> int:
    """Max number of *order* items that stack into ONE unit of *unit_category* whose
    storage_size is at most *target_size* (the pallet/fulfillment twins, unified via
    the UNIT_CLASSES registry).

    Stacking height increases monotonically with quantity, so the required
    storage_size also increases: scan from 1 upward until the unit outgrows the
    target tier and return the last fitting quantity.  Used by _stock to repack a
    stranded unit into smaller bins and by the planner's reachability pass.
    """
    unit_cls, ranks, _sizes = UNIT_CLASSES.get(unit_category, UNIT_CLASSES['pallet'])
    target_rank = ranks.get(target_size, 0)
    result = 0
    for q in range(1, 10_000):
        try:
            u = unit_cls(order, q)
            if ranks.get(u.storage_size, 99) <= target_rank:
                result = q
            else:
                break   # size is monotone-increasing — stop early
        except ValueError:
            break
    return result


def _uniform_assignment(unit: StorageUnit, candidates: list[Aisle.Bin]) -> Aisle.Bin | None:
    """Pick uniformly at random from the candidate bin list.

    candidates is pre-filtered by _candidates() to the correct handling type,
    storage category, unit type, and largest available size tier.  Picking
    randomly within that filtered set uniformly distributes placements across
    the matching bin locations.
    """
    return random.choice(candidates) if candidates else None
