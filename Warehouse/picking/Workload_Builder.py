from __future__ import annotations

import math
import operator
import random
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from Warehouse.catalog.Inventory_Builder import Inventory, AffMatrix
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.layout.Warehouse_Builder import Warehouse
from Warehouse.layout.Storage_Primitive import StorageCart, StoreCart
from Warehouse.catalog.Affinity_Store import AffinityStore

_CART_VOLUME: int = StoreCart.capacity()   # default (store) cart volume; overridable per Task

# Module-level cache keyed by affinity dict id so the O(|affinity|) partner-map
# build is paid only once per unique affinity object across all batch calls in a run.
_partner_map_cache: dict[int, dict[int, list[tuple[int, float]]]] = {}


def _get_partner_map(affinity) -> dict[int, list[tuple[int, float]]]:
    """Build sku -> [(partner_sku, lift), ...] from either a dict AffMatrix
    {(i, j): lift} or an AffinityStore (CSR matrix).  Cached per affinity-object id
    so the O(|affinity|) build is paid once per run.  `None` ⇒ {} (pure demand weighting)."""
    if affinity is None:
        return {}
    key = id(affinity)
    cached = _partner_map_cache.get(key)
    if cached is not None:
        return cached
    pm: dict[int, list[tuple[int, float]]] = defaultdict(list)
    if isinstance(affinity, dict):
        for (si, sj), v in affinity.items():
            if si < sj:
                pm[si].append((sj, v))
                pm[sj].append((si, v))
    else:
        # AffinityStore: the CSR matrix is symmetric, so each row i already lists
        # all of sku_i's partners — no si<sj dedup needed.
        m = getattr(affinity, '_matrix', None)
        if m is not None:
            sku_to_idx = affinity._sku_to_idx
            idx_to_sku = {i: s for s, i in sku_to_idx.items()}
            indptr, indices, data = m.indptr, m.indices, m.data
            for s, i in sku_to_idx.items():
                start, end = int(indptr[i]), int(indptr[i + 1])
                if end > start:
                    pm[s] = [(idx_to_sku[int(indices[j])], float(data[j]))
                             for j in range(start, end)]
    result = dict(pm)
    _partner_map_cache[key] = result
    return result


@dataclass
class BatchConfig:
    inventory_size: int
    mean_fraction: float = 0.20   # centre of num_skus distribution as fraction of inventory
    std_fraction: float  = 0.05   # spread of num_skus distribution as fraction of inventory
    # Sampler VERSION, not a tuning knob.  'v1' (default) = the original O(k·N) cumsum
    # sampler — every published run was drawn with it and it must stay byte-identical.
    # 'v2' = the Fenwick-tree sampler (O((k·(1+partners))·log N)): same weight model,
    # different float grouping, therefore a DIFFERENT batch sequence — opting in starts a
    # new results era and gets a distinct batch-cache fingerprint (batch_precompute tags
    # non-v1 samplers into the hash).  Trigger for v2: catalogue growth making the v1
    # precompute wall bind (measured 2026-08-20: 21.6s vs 0.48s per batch at 160k SKUs).
    sampler: str = 'v1'


def _lift_weighted_sample(
    candidates: list,
    k: int,
    affinity: 'AffMatrix | AffinityStore | None',
    rng: random.Random | None = None,
) -> list:
    """Sample k distinct items from candidates without replacement.

    Weight of a candidate B is the conditional-demand model
        weight(B) = demand.relative_frequency(B) · Π lift(A, B)
    over the already-selected partners A of B.  Lift enters MULTIPLICATIVELY (its
    natural sense: lift = 1 = independence ⇒ no change), so demand and affinity stay
    commensurable.  affinity=None ⇒ pure demand weighting (empty partner map).

    Uses a numpy cumsum draw per step + the module-level partner-map cache.  Pass `rng`
    (a `random.Random`) to draw from a dedicated stream; default `None` uses the global
    module.
    """
    r = rng or random
    partner_map = _get_partner_map(affinity)

    n = len(candidates)
    sku_to_idx: dict[int, int] = {c.sku: i for i, c in enumerate(candidates)}
    base_weights = np.fromiter(
        (c.demand.relative_frequency for c in candidates), dtype=np.float64, count=n
    )
    lift_mult = np.ones(n, dtype=np.float64)   # Π lift(A,B) over already-selected partners
    active = np.ones(n, dtype=bool)
    w = np.empty(n, dtype=np.float64)
    selected: list = []

    for _ in range(k):
        np.multiply(base_weights, lift_mult, out=w)   # weight(B) = freq(B) · Π lift(A,B)
        w[~active] = 0.0
        total: float = float(w.sum())
        if total <= 0.0:
            break
        cumw = np.cumsum(w)
        idx = int(np.searchsorted(cumw, r.uniform(0.0, total)))
        if idx >= n:
            idx = n - 1
        chosen = candidates[idx]
        selected.append(chosen)
        active[idx] = False
        for partner_sku, lv in partner_map.get(chosen.sku, []):
            j = sku_to_idx.get(partner_sku)
            if j is not None:
                lift_mult[j] *= lv

    return selected


class _Fenwick:
    """Prefix-sum tree over non-negative weights: O(log n) point-set and prefix-search.

    Backs the v2 sampler only.  Pure Python by choice: at 160k SKUs the whole v2 draw
    is ~0.5s (vs v1's 21.6s), so numpy adds nothing but a second float-grouping story.
    """
    __slots__ = ('n', 'tree', 'w')

    def __init__(self, weights: list) -> None:
        self.n = len(weights)
        self.w = list(weights)
        t = [0.0] * (self.n + 1)
        for i, v in enumerate(weights, start=1):     # O(n) build
            t[i] += v
            j = i + (i & -i)
            if j <= self.n:
                t[j] += t[i]
        self.tree = t

    def total(self) -> float:
        s, i, t = 0.0, self.n, self.tree
        while i > 0:
            s += t[i]
            i -= i & -i
        return s

    def set(self, i: int, value: float) -> None:
        d = value - self.w[i]
        if d == 0.0:
            return
        self.w[i] = value
        j, t = i + 1, self.tree
        while j <= self.n:
            t[j] += d
            j += j & -j

    def find(self, u: float) -> int:
        """Smallest 0-based index whose prefix sum reaches `u` (the weighted draw)."""
        pos, rem, t = 0, u, self.tree
        bit = 1 << (self.n.bit_length() - 1) if self.n else 0
        while bit:
            nxt = pos + bit
            if nxt <= self.n and t[nxt] < rem:
                pos = nxt
                rem -= t[nxt]
            bit >>= 1
        return min(pos, self.n - 1)


def _lift_weighted_sample_v2(
    candidates: list,
    k: int,
    affinity: 'AffMatrix | AffinityStore | None',
    rng: random.Random | None = None,
) -> list:
    """Fenwick-tree form of `_lift_weighted_sample`: same conditional-demand weight model
    (weight(B) = freq(B) · Π lift(A,B) over selected partners A), O((k·(1+P))·log n)
    instead of v1's O(k·n) full-array passes per draw.

    NOT byte-compatible with v1 — the tree groups float additions differently than v1's
    sequential cumsum, so draws eventually land on different SKUs and the batch sequence
    is a new version (BatchConfig.sampler='v2'; batch_precompute fingerprints it apart).
    Deterministic for a given rng seed, one rng.uniform consumed per draw like v1.
    """
    r = rng or random
    partner_map = _get_partner_map(affinity)

    n = len(candidates)
    sku_to_idx: dict[int, int] = {c.sku: i for i, c in enumerate(candidates)}
    base = [c.demand.relative_frequency for c in candidates]
    lift_mult = [1.0] * n
    active = [True] * n
    fw = _Fenwick(base)
    selected: list = []

    for _ in range(k):
        total = fw.total()
        if total <= 0.0:
            break
        idx = fw.find(r.uniform(0.0, total))
        chosen = candidates[idx]
        selected.append(chosen)
        active[idx] = False
        fw.set(idx, 0.0)
        for partner_sku, lv in partner_map.get(chosen.sku, []):
            j = sku_to_idx.get(partner_sku)
            if j is not None and active[j]:
                lift_mult[j] *= lv
                fw.set(j, base[j] * lift_mult[j])

    return selected


_SAMPLERS = {'v1': _lift_weighted_sample, 'v2': _lift_weighted_sample_v2}


class Batch:
    def __init__(
        self,
        config: BatchConfig,
        inventory: Inventory,
        affinity: AffMatrix | AffinityStore | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        # Dedicated batch stream: pass `rng=random.Random(seed_batches + i)` so batch
        # i is a pure function of (inventory, affinity, config) and is identical across
        # arms, immune to whatever placement/reorder randomness ran first. Default
        # `None` keeps the global `random` module (back-compatible).
        r = rng or random

        # Batch SIZE (distinct SKUs / order lines) ~ Normal(mean_fraction·N,
        # std_fraction·N), bounded to [1, N].  No eligibility cutoff: the size follows the
        # requested mean/sd directly (a small "low-pick" batch from the left tail is fine),
        # and WHICH SKUs fill it is demand- (relative_frequency) and lift-weighted below.
        mean = config.mean_fraction * config.inventory_size
        std  = config.std_fraction  * config.inventory_size
        self.num_skus: int = max(1, min(config.inventory_size, round(r.gauss(mean, std))))

        candidates = inventory.orders           # every SKU is eligible
        k = min(self.num_skus, len(candidates))

        # Selection is always demand-weighted (by demand.relative_frequency); an
        # AffinityStore/dict additionally multiplies in lift toward partners of already-
        # chosen SKUs.  affinity=None ⇒ pure demand weighting (empty partner map).  An
        # unhandled affinity type raises rather than silently degrading.
        if k <= 0:
            selected = []
        elif affinity is None or isinstance(affinity, (dict, AffinityStore)):
            _sampler = _SAMPLERS.get(getattr(config, 'sampler', 'v1'))
            if _sampler is None:
                raise ValueError(
                    f'BatchConfig.sampler must be one of {sorted(_SAMPLERS)}; got '
                    f'{config.sampler!r}. Refusing to guess a batch-sequence version.')
            selected = _sampler(candidates, k, affinity, rng=r)
        else:
            raise TypeError(
                f'Batch affinity must be dict, AffinityStore, or None; got '
                f'{type(affinity).__name__}. Refusing to silently fall back to '
                f'uniform sampling.')

        self.items: dict[int, int] = {c.sku: max(1, c.demand.sample(rng=r)) for c in selected}

        # For a plain dict, store it directly for use in analytics.
        # For AffinityStore, lift_sum is computed on-demand per task in
        # extract_task_stats — pre-loading all batch pairs would fetch O(k²) rows
        # from a potentially huge DB on every batch creation.
        self.aff: AffMatrix = affinity if isinstance(affinity, dict) else {}


# ── deterministic bin ordering ───────────────────────────────────────────────
# Sort key for the `Task.from_batch` drain loops.  `Aisle.Bin.location` is the canonical
# (aisle_id, bayX, bayY) spatial triple — the key the run tree, every DB and the viewer
# already index bins by — so ordering on it needs no extra state and is stable under any
# insertion order.  `attrgetter` keeps the hot loop at C speed; a Python lambda pays an
# interpreter frame per bin, and this runs once per SKU per batch.
_bin_location = operator.attrgetter('location')

# The maintained-order container the manager indexes bins in; iteration is already
# location order, so from_batch's per-SKU sort is skipped for it (leaf import — no cycle:
# inventory_common depends only on warehouse primitives).
from Warehouse.inventory.inventory_common import _SortedBins  # noqa: E402


def drain_sku(singleton_bins, pallet_bins, qty: int, out) -> int:
    """THE SIM'S OWN DRAIN RULE for one SKU, extracted so a forecast shares it by
    construction: singleton bins before pallet bins, each tier in `location` order,
    take = min(remaining, bin quantity).

    Accumulates per-bin takes into `out` (a `defaultdict(int)` keyed by the bin object)
    and returns the demand no bin could satisfy.  Two callers, deliberately:
    `Task.from_batch` builds the pick tasks from it, and the standing yard's space
    timeline (`Inbound/space.py`) projects predicted-clear bins with it — handed this
    function by the driver, because the import edge `Inbound -> wh_picking` is forbidden.
    One body means the projection is exact rather than a re-implementation that can
    drift; `_rederive_plan` below is explicitly NOT this rule (its own docstring warns
    its distribution can differ).

    Pure: reads bin state, writes only `out`, consumes no RNG.  A `_SortedBins`
    container already iterates in `location` order so its sort is skipped; raw
    sets/lists (test stand-ins, legacy callers) still get the explicit sort — the
    determinism contract is the ORDER, not the container
    (see test_task_bin_selection_determinism).
    """
    remaining = qty
    if not isinstance(singleton_bins, _SortedBins):
        singleton_bins = sorted(singleton_bins, key=_bin_location)
    for bin_ in singleton_bins:
        if remaining <= 0:
            break
        available = bin_.storage.quantity if bin_.storage is not None else 0
        take = min(remaining, available)
        if take > 0:
            out[bin_] += take
            remaining -= take
    if not isinstance(pallet_bins, _SortedBins):
        pallet_bins = sorted(pallet_bins, key=_bin_location)
    for bin_ in pallet_bins:
        if remaining <= 0:
            break
        available = bin_.storage.quantity if bin_.storage is not None else 0
        take = min(remaining, available)
        if take > 0:
            out[bin_] += take
            remaining -= take
    return remaining


def _rederive_plan(path: list, items: dict[int, int]) -> list[int]:
    """Per-bin quantities for a `Task` built without an explicit plan.

    The same greedy `from_batch` uses -- walk the path, take what the bin has up to the
    SKU's remaining demand -- so a hand-built Task behaves like a real one.  It can pick a
    DIFFERENT distribution than `from_batch` would when the path order differs from the
    drain order, which is exactly why the real caller passes its plan instead.
    """
    remaining = dict(items)
    out: list[int] = []
    for b in path:
        st = getattr(b, 'storage', None)
        if st is None:
            out.append(0)
            continue
        sku = st.order.sku
        take = min(remaining.get(sku, 0), st.quantity)
        remaining[sku] = remaining.get(sku, 0) - take
        out.append(take)
    return out


class Task:
    """Single-aisle ordered pick sequence derived from a Batch."""

    def __init__(
        self,
        aisle_id: int,
        path: list[Aisle.Bin],
        items: dict[int, int],
        cart: type[StorageCart] = StoreCart,
        bin_qty: dict[int, int] | None = None,
    ) -> None:
        self.aisle_id: int          = aisle_id
        self.path: list[Aisle.Bin]  = path         # bins in visit order
        self.items: dict[int, int]  = items         # sku -> quantity for this aisle
        # PER-BIN plan, aligned to `path`.  `items` is the per-AISLE total for a SKU, and
        # reading it once per bin is how a SKU in several bins of one aisle came to be
        # picked once PER BIN -- measured at 6.7% more units picked than demanded, with the
        # two sims disagreeing (demand 8 over two bins of 5: PickSimulation took 16 and did
        # not even cap at bin stock; fast_pick took 10).  `from_batch` always knew the right
        # answer; it just did not pass it on.
        #
        # `bin_qty` is keyed by id(bin) because the caller builds it before
        # `_plan_aisle_path` reorders the bins, and the reorder can differ from the drain
        # order (both agree on bayX, but the within-column bayY direction depends on entry
        # distance).  Re-deriving here would therefore distribute a SKU's units across a
        # DIFFERENT set of bins than the drain chose.
        self.planned: list[int] = (
            [bin_qty.get(id(b), 0) for b in path] if bin_qty is not None
            else _rederive_plan(path, items))
        x_trav = 0.0
        y_trav = 0.0
        for i in range(len(path) - 1):
            x_trav += abs(path[i].x_phys - path[i+1].x_phys)
            y_trav += abs(path[i].y_phys - path[i+1].y_phys)
        self.x_traversed: float = x_trav   # physical units
        self.y_traversed: float = y_trav   # physical units
        # Snapshot the (weight, volume, qty, y_phys) pick lines NOW, while the path bins
        # still hold stock.  The sim depletes these bins (storage→None) during run(), so
        # computing them later (in extract_task_stats) would drop emptied bins and zero out
        # the analytical workload W.  Captured here so W reflects the real picks at all heights.
        # The analytical mirror of the pick loop, and it had the SAME per-bin overcount:
        # `items[sku]` here is the aisle total.  W is compared against realised labor by
        # the equivalence suites, so both had to move together or the comparison breaks.
        self.pick_lines: list[tuple[int, int, int, float]] = [
            (b.storage.order.weight, b.storage.order.volume(), q, b.y_phys)
            for b, q in zip(path, self.planned)
            if b.storage is not None and q > 0
        ]
        # Build volume lookup from path bins.  A SKU in items may have no bin
        # in this path when all its bins are pending reclaim (emptied last batch).
        # Fall back to the order volume from the first bin found anywhere in the
        # path for that SKU — missing SKUs keep volume=0 which underestimates
        # carts_required, so use a secondary lookup from any path bin.
        sku_to_vol: dict[int, int] = {}
        for b in path:
            if b.storage is not None:
                sku = b.storage.order.sku
                if sku not in sku_to_vol:
                    sku_to_vol[sku] = b.storage.order.volume()
        # For SKUs in items not covered by path bins, approximate with any
        # non-None path bin's order volume (they share the same aisle type,
        # so dimensions are at least in the same order of magnitude).
        fallback_vol = next(
            (b.storage.order.volume() for b in path if b.storage is not None), 1
        )
        total_vol: int = sum(
            sku_to_vol.get(sku, fallback_vol) * qty for sku, qty in items.items()
        )
        self.carts_required: int = math.ceil(total_vol / cart.capacity()) if total_vol > 0 else 0

    @staticmethod
    def from_batch_with_shortfall(
            batch: Batch, warehouse: Warehouse, manager=None,
            cart: type[StorageCart] = StoreCart) -> tuple[list[Task], dict[int, int]]:
        """`from_batch`, plus the demand it could NOT satisfy: `{sku: units_short}`.

        A second entry point rather than a wider return type: `from_batch` has ~30 call
        sites and none of them want the extra value, so churning all of them for no
        behaviour change is the more expensive mistake.  One drain serves both, so the
        tasks are identical either way.

        THE SHORTFALL IS PRE-SIMULATION.  It is demand this batch could not reach on the
        shelf -- not demand a picker ran out of time for.  Those are different failures
        with different fixes, and only this one is knowable before the sim runs.

        Until now it was a loop-local named `remaining`, overwritten on the next SKU.  So
        an arm that placed nothing for a SKU and an arm that placed 500 of it produced
        indistinguishable records, and a rule that fails to pick looked cheap.
        """
        short: dict[int, int] = {}
        tasks = Task.from_batch(batch, warehouse, manager, cart, _shortfall=short)
        return tasks, short

    @staticmethod
    def from_batch(batch: Batch, warehouse: Warehouse, manager=None,
                   cart: type[StorageCart] = StoreCart,
                   _shortfall: dict[int, int] | None = None) -> list[Task]:
        """Decompose a Batch into one Task per aisle.

        `_shortfall`, when given, accumulates `{sku: units_short}` for demand no bin could
        satisfy.  Underscored because callers should reach for `from_batch_with_shortfall`
        rather than pass their own dict; it exists so one drain serves both entry points.

        For each SKU in the batch, singleton bins are drained before pallet bins
        so that forward-pick locations are always preferred over reserve locations.

        If manager is provided its pre-built _sku_singleton_bins/_sku_pallet_bins
        dicts are used directly, skipping the O(N_all_bins) warehouse scan that
        would otherwise rebuild the index on every batch.

        DETERMINISM — the manager index is `dict[int, set[Aisle.Bin]]`, and `Aisle.Bin`
        defines no `__hash__`/`__eq__`, so it hashes by identity and a set iterates in
        MEMORY-ADDRESS order.  Whenever a SKU has more on-hand bins than the batch
        quantity drains — the normal case — that order decides WHICH bins are taken,
        hence which aisles, tasks, travel and depletion follow.  Under spawn + ASLR the
        addresses differ per process, so two identical-seed runs of the same arm used to
        produce different `batch_stats`.  Iterating in `location` order — the canonical
        (aisle_id, bayX, bayY) spatial key persisted everywhere — makes the selection a
        pure function of warehouse state.  Cost is O(k log k) on k bins of ONE SKU; see
        `Tests/unit/test_task_bin_selection_determinism.py`.
        """
        # Distribute each batch quantity: drain singleton bins before pallet bins
        bin_pick: defaultdict[Aisle.Bin, int] = defaultdict(int)

        if manager is not None:
            # O(N_batch_skus) — uses maintained index, no full warehouse scan.  The
            # per-SKU walk is `drain_sku`, THE drain rule (see its docstring — the space
            # timeline's projection shares the same body, which is what makes that
            # forecast exact by construction).
            for sku, qty in batch.items.items():
                remaining = drain_sku(
                    manager._sku_singleton_bins.get(sku, ()),
                    manager._sku_pallet_bins.get(sku, ()),
                    qty, bin_pick)
                # Whatever is still `remaining` is demand no bin could satisfy.
                if _shortfall is not None and remaining > 0:
                    _shortfall[sku] = _shortfall.get(sku, 0) + remaining
        else:
            # Fallback: O(N_all_bins) scan — used when no manager is available.
            # Already deterministic, and it now agrees with the branch above: `warehouse.bins`
            # is emitted in `location` order, and the singleton-first sort below is STABLE,
            # so this yields singletons in location order then pallets in location order —
            # the same rule the sorted index drain applies.  (Before the sort was added the
            # two branches disagreed, so a manager-less caller saw a different selection.)
            sku_to_bins: dict[int, list[Aisle.Bin]] = defaultdict(list)
            for bin_ in warehouse.bins:
                if bin_.storage is not None:
                    sku_to_bins[bin_.storage.order.sku].append(bin_)
            for bins in sku_to_bins.values():
                bins.sort(key=lambda b: 0 if b.unit_type == 'singleton' else 1)
            for sku, qty in batch.items.items():
                remaining = qty
                for bin_ in sku_to_bins.get(sku, []):
                    if remaining <= 0:
                        break
                    available = bin_.storage.quantity if bin_.storage is not None else 0
                    take = min(remaining, available)
                    if take > 0:
                        bin_pick[bin_] += take
                        remaining -= take
                # Same accounting as the indexed branch above -- the two selection paths
                # already agree on WHICH bins are drained, so they must agree on what is
                # left over too, or a manager-less caller reports a different shortfall.
                if _shortfall is not None and remaining > 0:
                    _shortfall[sku] = _shortfall.get(sku, 0) + remaining

        aisle_bins:  dict[int, list[Aisle.Bin]] = defaultdict(list)
        aisle_items: dict[int, dict[int, int]]  = defaultdict(dict)
        for bin_, take in bin_pick.items():
            aisle_id = bin_.location[0]
            aisle_bins[aisle_id].append(bin_)
            sku = bin_.storage.order.sku  # type: ignore[union-attr]
            aisle_items[aisle_id][sku] = aisle_items[aisle_id].get(sku, 0) + take

        plan_by_bin = {id(b): take for b, take in bin_pick.items()}
        tasks = []
        for aisle_id, bins in aisle_bins.items():
            path = _plan_aisle_path(bins)
            if path:   # guard: skip tasks with empty paths (all bins emptied mid-build)
                tasks.append(Task(aisle_id, path, aisle_items[aisle_id], cart=cart,
                                  bin_qty=plan_by_bin))
        return tasks


def _plan_aisle_path(bins: list[Aisle.Bin]) -> list[Aisle.Bin]:
    """Order bins by bayX; within each x-column traverse bayY in whichever
    direction (ascending or descending) minimises entry distance from the
    current position."""
    by_x: dict[int, list[Aisle.Bin]] = defaultdict(list)
    for b in bins:
        by_x[b.location[1]].append(b)

    path: list[Aisle.Bin] = []
    current_y: int = 0

    for x in sorted(by_x.keys()):
        group = sorted(by_x[x], key=lambda b: b.location[2])
        y_low  = group[0].location[2]
        y_high = group[-1].location[2]

        if abs(current_y - y_low) <= abs(current_y - y_high):
            ordered = group                  # ascending y
        else:
            ordered = list(reversed(group))  # descending y

        path.extend(ordered)
        current_y = ordered[-1].location[2]

    return path
