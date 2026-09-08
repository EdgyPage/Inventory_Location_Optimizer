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


# A POOL is a policy's candidate bins for ONE BinKey group, opened once per wave and
# consumed one unit at a time:
#
#     pool = placement.open_pool(candidates, rep)   # rep: ANY unit of the group
#     for unit in pool.order(units):                # the order the POLICY would like
#         bin_, score = pool.take(unit)             # consumes the bin; None when exhausted
#
# `rep` resolves the things that are constant across a BinKey group but need a unit to name
# -- per-regime cost, today.  Any unit of the group gives the same answer, because regime is
# itself a BinKey component; passing one is how the pool says so out loud.
#
# `order` is a REQUEST, not an instruction.  A policy that has an opinion about which unit
# should be served first states it there and the drain decides whether to honour it; that is
# the whole difference from `place_wave`, which returned the order as a fait accompli.  The
# default is queue order.
#
# It is the inversion of `place_wave`.  A wave RETURNS the order it wants, which is how the
# assignment functions came to decide placement ORDER as well as the bin; a pool answers one
# unit at a time and leaves the order to the caller.  That is what lets the drain impose FIFO
# (with a K-oldest window) without touching the choice, and it costs nothing: the snapshot was
# never order-dependent, because every unit in a group shares a BinKey.
#
# `take` returns the SCORE it chose on, so the number the policy optimised is capturable
# without a second scoring pass -- there is no separate "score this bin for this unit" step to
# call later, which is precisely why the old shape could not be observed.
PoolFn = Callable[[list], 'Pool']

#: Where a unit in the put-away queue came from.  Three today; a trailer is the fourth,
#: and the reason this is a named vocabulary rather than a bool.
PUTAWAY_SOURCES = ('intake', 'reorder', 'reslot', 'trailer')


class PutawayItem:
    """One unit waiting for a bin, and where it came from.

    The queue used to hold bare `StorageUnit`s, fed identically by initial intake
    (`enqueue`/`enqueue_all`), reorder arrivals (`_release_to_stock`) and reloader
    evictions (`requeue_bin`).  Once a unit was in, its origin was unrecoverable — so
    `BinRecorder` reconstructed 'reslot' by holding a set of `id(unit)` and testing
    membership at placement time.  That works only because the evicted unit is the
    IDENTICAL object and is still alive; it is not a property anything declares.

    Carrying the source with the work item makes `bin_placement.cause` a fact the queue
    knows rather than one the recorder infers, and it is the field a trailer id will
    occupy when inbound loads become a source of their own.

    `age` is a monotonic arrival stamp, and it exists because "FIFO" stopped being a
    property of the deque the moment the drain got a K-oldest window.  Queue POSITION used
    to be the only record of how long something had waited, which is fine while nothing ever
    re-enters — and three things do: the repack rescue, the singleton rescue, and a group
    requeued whole when the put-away budget runs out.  A stamp survives all three and can be
    asserted; a position cannot even be inspected after the fact.

    It is also what the queue-state snapshots report as `oldest_age`, and the field a
    trailer's arrival time will map onto when inbound becomes a producer.

    Frozen because a queued item's origin must not be editable in flight; `respawn` is the
    one legal derivation, used by the repack and singleton rescues, which split one unit
    into several without changing where any of them came from — or how old any of them is.
    """
    __slots__ = ('unit', 'source', 'age')

    def __init__(self, unit: StorageUnit, source: str = 'intake',
                 age: int = -1) -> None:
        if source not in PUTAWAY_SOURCES:
            raise ValueError(f'unknown put-away source {source!r} '
                             f'(known: {PUTAWAY_SOURCES})')
        object.__setattr__(self, 'unit', unit)
        object.__setattr__(self, 'source', source)
        # -1 = unstamped. The manager stamps on admission; a hand-built item in a test does
        # not have to, and an unstamped item must never silently sort as the oldest thing in
        # the warehouse, so the drain treats -1 as "ask the queue position" rather than 0.
        object.__setattr__(self, 'age', age)

    def __setattr__(self, *_a):
        raise AttributeError('PutawayItem is immutable; use respawn() to derive one')

    def respawn(self, unit: StorageUnit) -> 'PutawayItem':
        """A unit split out of this one during a rescue, keeping the origin AND the age.

        A repack does not make a unit newer.  The pallet that arrived first and had to be
        broken into three should still be put away before a pallet that arrived after it;
        inheriting the stamp is what says so, and it is what keeps the rescue paths from
        becoming a way to jump the queue.
        """
        return PutawayItem(unit, self.source, self.age)

    def __repr__(self) -> str:
        return f'PutawayItem({self.unit!r}, {self.source!r})'


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
    __slots__ = ('name', 'place_one', 'place_wave', 'uses_aisle_index', 'order_score',
                 'open_pool')

    def __init__(self, name: str, place_one: AssignmentFn,
                 place_wave: 'RankedAssignmentFn | None' = None,
                 order_score: 'Callable[[Any], float] | None' = None,
                 open_pool: 'PoolFn | None' = None) -> None:
        self.name             = name
        self.place_one        = place_one
        self.place_wave       = place_wave
        # A POOL policy: the drain owns the order, the pool owns the choice.  Preferred over
        # `place_wave` when present.  See the note beside PoolFn.
        self.open_pool        = open_pool
        # the per-unit fn declares whether it reads mgr._aisle_index (coupling guard)
        self.uses_aisle_index = bool(getattr(place_one, 'uses_aisle_index', False))
        # Per-policy enqueue ordering: (unit)->float, sorted DESCENDING before placement.
        # Decouples queue order from the placement impl so a policy is never forced into
        # an ordering that fights it.  None ⇒ the ranked wave's default pick-effort order.
        self.order_score      = order_score

    @property
    def is_ranked(self) -> bool:
        """Placed a whole BinKey group at once -- by pool or by wave."""
        return self.place_wave is not None or self.open_pool is not None

    @property
    def is_pooled(self) -> bool:
        """The drain may choose the order: this policy answers one unit at a time."""
        return self.open_pool is not None


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


#: The unit family whose bins are FORWARD-PICK locations.  Everything else is reserve.
FORWARD_PICK_FAMILY = 'singleton'


def is_forward_pick(obj) -> bool:
    """Whether a unit or a bin belongs to the FORWARD-PICK index rather than reserve.

    `Task.from_batch` drains `_sku_singleton_bins` before `_sku_pallet_bins` "so that
    forward-pick locations are always preferred over reserve locations" -- this is the
    predicate that decides which of those two a bin is filed under.

    Duck-typed through `binkey_of`, which is what lets ONE expression serve both sides.
    It replaced three: `isinstance(unit, Singleton)` on the unit side and
    `bin_.unit_type == 'singleton'` twice on the bin side.  Those agreed, but only because
    `_candidates_raw` matches `unit_type` EXACTLY in every branch, so a unit never reaches
    a bin of another family -- an invariant nothing stated and nothing checked.  Three
    spellings of one concept, two of them on the opposite object from the third, is a
    disagreement with a delay on it.

    FULFILLMENT IS NOT FORWARD-PICK, and that is deliberate rather than overlooked: a
    fulfillment SKU has exactly one bin family, so the two-phase drain is a no-op for it
    and everything lands in the reserve index.  If fulfillment ever grows a forward/reserve
    split, this is the one place that decides it.
    """
    return binkey_of(obj)[3] == FORWARD_PICK_FAMILY


class UndeclaredStock(RuntimeError):
    """A SKU's Order-Up-To target was read before any run declared one."""


def _equilibrium_qty(order: Order) -> int:
    """Return the Order-Up-To target for *order*.

    Reads `equilibrium_qty` if a run has DECLARED one (`Order.declare_stock`); falls back to
    the legacy duck-typed `stock_qty` attribute so old in-memory inventories still work.

    RAISES on an undeclared order rather than defaulting.  It used to answer 1, and a default
    here is the worst possible silence: the catalogue no longer authors a level (ADR-0002), so
    every SKU of a freshly loaded catalogue would answer 1, `bucket_requirements` would size
    every bucket for one unit per SKU, and the run would build a warehouse an order of
    magnitude too small -- with no error, at the one moment nothing downstream can detect it.
    A level is a run's declaration; asking for one before it is made is a bug in the caller.
    """
    q = getattr(order, 'equilibrium_qty', None)
    if q is not None:
        return q
    q = getattr(order, 'stock_qty', None)
    if q is not None:
        return q
    raise UndeclaredStock(
        f'SKU {getattr(order, "sku", "?")}: no stock level has been declared for this order, so '
        f'it has no Order-Up-To target to read. The catalogue carries no levels (ADR-0002): a '
        f'run declares them at setup through Optimization/simconfig/coverage.rescale_section, '
        f'driven by simdriver/era_coverage.fixed_point from sim_assets.build_shared_assets. '
        f'Reading a level before that is what this error exists to catch.')


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
