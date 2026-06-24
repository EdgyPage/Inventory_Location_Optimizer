"""inventory_common.py — leaf types/constants/helpers shared by the Inventory_Manager
mixins and by Assignment_Functions.

Extracted so the manager can be split into cohesive mixin modules
(inventory_planning / inventory_optimal / inventory_reorder) without import cycles:
this module depends only on the low-level warehouse primitives, never on the mixins or
on Inventory_Management itself.  Inventory_Management re-exports these names for
backward compatibility (`from Inventory_Management import Placement, BinKey, ...`).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable

from Order import Order
from Aisle_Storage import Aisle
from Storage_Primitive import StorageUnit, Pallet, Storage_Size

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

BinKey = tuple[str, str, str, str]


def _equilibrium_qty(order: Order) -> int:
    """Return the Order-Up-To target for *order*.

    Reads equilibrium_qty if present (new schema); falls back to the legacy
    stock_qty attribute so old in-memory inventories still work correctly.
    """
    return getattr(order, 'equilibrium_qty',
                   getattr(order, 'stock_qty', 1))


def _max_qty_fitting_pallet_size(order: Order, target_size: str) -> int:
    """Return the maximum number of *order* items that stack onto one pallet
    whose storage_size is at most *target_size*.

    Pallet stacking height increases monotonically with quantity, so the
    required storage_size also increases.  We scan from 1 upward until the
    pallet outgrows the target tier and return the last fitting quantity.
    Used by _stock to repack a stranded unit into smaller bins.
    """
    target_rank = _SIZE_RANKS.get(target_size, 0)
    result = 0
    for q in range(1, 10_000):
        try:
            p = Pallet(order, q)
            if _SIZE_RANKS.get(p.storage_size, 99) <= target_rank:
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
