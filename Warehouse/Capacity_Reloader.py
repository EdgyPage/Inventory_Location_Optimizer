"""Capacity_Reloader.py — bounded re-slot via evict-and-requeue.

Rather than swapping bin contents (and breaking ties), a Capacity_Reloader EVICTS
targeted pallets back into the reorder queue (`manager.requeue_bin`) and reclaims
their bins.  The manager's subsequent ranked drain (in `check_reorders`) then
re-places everything in priority order, so popular items claim the freed prime bins
and the evicted ones settle into their proper rank — no swapper, no tie-breaks.

The number of evictions per pallet aisle is capped at a percent of a reference
aisle's bin capacity (the operational pallet-move labor ceiling).

Three named variants (target selectors):
  - ``promote_popular``  : evict high-frequency pallets sitting in high-W (bad) bins,
                           so the ranked re-drain lifts them into better bins.
  - ``demote_unpopular`` : evict low-frequency pallets sitting in low-W (prime) bins,
                           freeing prime bins for higher-ranked items.
  - ``rebalance``        : both (split the per-aisle budget) — the swap result
                           without a swapper.

The reloader carries its own ``assignment_fn`` / ``batch_assignment_fn`` (the
re-placement policy — usually identical to the manager's reorder fns, which is what
the post-eviction ranked drain actually uses).
"""
from __future__ import annotations

from collections import defaultdict


# ── named target selectors: which occupied pallets to evict, per aisle ───────

def demote_unpopular(occupied, freq_of, bin_sku, W, k):
    """Lowest-frequency pallets in the lowest-W (prime) bins — evicting frees prime
    bins for higher-ranked items."""
    prime_first = sorted(occupied, key=W)               # prime (low-W) bins first
    window = prime_first[:max(k * 4, k)]                 # a prime window to choose from
    window.sort(key=lambda b: freq_of.get(bin_sku.get(id(b)), 0.0))   # least popular first
    return window[:k]


def promote_popular(occupied, freq_of, bin_sku, W, k):
    """Highest-frequency pallets in the highest-W (worst) bins — evicting lets the
    ranked re-drain lift them into better available bins."""
    worst_first = sorted(occupied, key=W, reverse=True)  # worst (high-W) bins first
    window = worst_first[:max(k * 4, k)]
    window.sort(key=lambda b: freq_of.get(bin_sku.get(id(b)), 0.0), reverse=True)  # most popular first
    return window[:k]


def rebalance(occupied, freq_of, bin_sku, W, k):
    """Both: spend half the budget demoting unpopular-in-prime, half promoting
    popular-in-bad (de-duplicated)."""
    kd = max(1, k // 2)
    out = list(demote_unpopular(occupied, freq_of, bin_sku, W, kd))
    chosen = {id(b) for b in out}
    for b in promote_popular(occupied, freq_of, bin_sku, W, k):
        if len(out) >= k:
            break
        if id(b) not in chosen:
            out.append(b); chosen.add(id(b))
    return out[:k]


_SELECTORS = {
    'promote_popular':  promote_popular,
    'demote_unpopular': demote_unpopular,
    'rebalance':        rebalance,
}


class Capacity_Reloader:
    """Evict-and-requeue re-slot policy with a per-aisle move budget."""

    def __init__(self, name: str, target_selector, *,
                 assignment_fn=None, batch_assignment_fn=None,
                 move_limit_pct: float = 0.005,
                 ref_unit_type: str = 'pallet', ref_size: str = 'extra_large'):
        self.name                = name
        self._select             = target_selector
        self.assignment_fn       = assignment_fn          # re-placement policy (≈ manager's)
        self.batch_assignment_fn = batch_assignment_fn
        self.move_limit_pct      = move_limit_pct
        self.ref_unit_type       = ref_unit_type
        self.ref_size            = ref_size
        self._cap: int | None    = None

    def per_aisle_cap(self, warehouse) -> int:
        """K = floor(move_limit_pct × bins in a reference (XL pallet) aisle).  One
        constant across the run; cached."""
        if self._cap is None:
            per: dict[int, int] = defaultdict(int)
            for b in warehouse.bins:
                if b.storage_size == self.ref_size and b.unit_type == self.ref_unit_type:
                    per[b.location[0]] += 1
            self._cap = int(self.move_limit_pct * (max(per.values()) if per else 0))
        return self._cap

    def reload(self, manager, freq_of: dict, x_speed: float, y_speed: float) -> int:
        """Evict up to `per_aisle_cap` targeted pallets per pallet aisle back into
        the queue (+ reclaim their bins).  The caller's ranked drain re-places them.
        Returns the number evicted."""
        cap = self.per_aisle_cap(manager.warehouse)
        if cap <= 0:
            return 0
        W = lambda b: x_speed * b.x_phys + y_speed * b.y_phys
        by_aisle: dict[int, list] = defaultdict(list)
        for b in manager.unavailable:                     # occupied bins
            if b.unit_type == 'pallet' and b.storage is not None:
                by_aisle[b.location[0]].append(b)
        evicted = 0
        for occ in by_aisle.values():
            for b in self._select(occ, freq_of, manager._bin_sku, W, cap):
                manager.requeue_bin(b)
                evicted += 1
        return evicted


# ── named factories ───────────────────────────────────────────────────────────

def promote_popular_reloader(**kw) -> Capacity_Reloader:
    return Capacity_Reloader('promote_popular', promote_popular, **kw)


def demote_unpopular_reloader(**kw) -> Capacity_Reloader:
    return Capacity_Reloader('demote_unpopular', demote_unpopular, **kw)


def rebalance_reloader(**kw) -> Capacity_Reloader:
    return Capacity_Reloader('rebalance', rebalance, **kw)


RELOADERS = {
    'promote_popular':  promote_popular_reloader,
    'demote_unpopular': demote_unpopular_reloader,
    'rebalance':        rebalance_reloader,
}
