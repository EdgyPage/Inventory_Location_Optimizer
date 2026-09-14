"""inventory_zoning.py — velocity zoning: the per-regime "like-with-like" candidate layer.

`ZoningMixin` holds the whole cluster: the band MAPS (`_sku_band`, `_aisle_band`), the
per-band free-bin sub-index (`_band_index`, `_band_pos`), the spill order, the two filters
and the ranked-wave group key.  Mixed into `Inventory_Manager` beside `PlanningMixin`,
`OptimalLayoutMixin` and `ReorderMixin`, so the public API (`configure_zoning`) and every
internal caller are unchanged.

OFF is the identity: `configure_zoning(False)` clears every map, `_group_key` falls back to
the bare `BinKey`, and `_candidates` never calls `_band_pick` -- so a run with zoning off is
byte-identical to one from before zoning existed.

# ── why a mixin and not a collaborator object ─────────────────────────────────────

`Inventory_Manager._index_add` / `._index_remove` mirror EVERY bin mutation into the
per-band sub-index, and they are the hottest path in the system.  A mixin method resolves
exactly as a method defined on the class does; a `self._zoning.` hop would add an attribute
lookup per bin moved, for no reader's benefit.  So the two mirror sites stay inline on the
manager, and `Tests/unit/test_velocity_zoning.py` asserts they stay there.

All instance state is provided by `Inventory_Manager.__init__` -- this module constructs no
manager and imports none, which is what keeps the split a split rather than a cycle.
"""
from __future__ import annotations

import bisect
from collections import defaultdict
from typing import Any

from Warehouse.layout.Storage_Primitive import StorageUnit
from Warehouse.inventory.inventory_common import binkey_of


def _apportion(m: int, weights: list, n: int) -> list:
    """Allocate `m` aisles across `n` bands proportional to `weights`, with a floor of 1 per band
    when `m >= n` (so every band is reachable for the zone-filter spill).  When `m < n`, the first
    `m` (hottest) bands get 1 each and the rest 0 (spill covers the empties).  Largest-remainder."""
    if m <= 0:
        return [0] * n
    if m < n:
        return [1 if j < m else 0 for j in range(n)]
    extra = m - n                                   # after giving 1 per band
    wsum  = sum(weights) or 1
    raw   = [w / wsum * extra for w in weights]
    add   = [int(x) for x in raw]
    order = sorted(range(n), key=lambda j: -(raw[j] - int(raw[j])))
    for i in range(extra - sum(add)):
        add[order[i % n]] += 1
    return [1 + add[j] for j in range(n)]


class ZoningMixin:

    # ── velocity zoning setup ────────────────────────────────────────────────

    def configure_zoning(self, enabled: bool, n_bands: int = 3, orders: Any = None,
                         *, mode: str = 'equal', abc: dict | None = None) -> None:
        """Enable/disable velocity zoning and precompute the band maps (once, before stocking).

        mode='equal' (default): SKUs and aisles sliced into n_bands EQUAL-COUNT groups (hottest /
        shallowest = band 0).  mode='abc': SKUs banded by cumulative demand-MASS thresholds
        (abc['mass_thresholds'], manual A/B/C), and aisles allocated per BinKey PROPORTIONAL to each
        band's SKU footprint — so the hot A-band is a SMALL aisle fraction and the cold C-band is
        many rarely-visited aisles (batches skip them).  Disabled ⇒ maps cleared (byte-identical).
        """
        self._zoning_enabled = bool(enabled)
        self._zoning_bands = max(1, int(n_bands))
        self._sku_band = {}
        self._aisle_band = {}
        self._band_index = {}      # rebuilt below when enabled; empty (unused) when off
        self._band_pos = {}
        if not self._zoning_enabled:
            return
        n = self._zoning_bands
        if mode == 'abc' and orders is not None:
            self._build_abc_bands(orders, n, abc)
            self._build_band_index()
            return
        # ── equal-count (default; byte-identical) ─────────────────────────────
        # SKU velocity band (global; hottest = band 0).
        if orders is not None:
            vel = {c.sku: (c.demand.relative_frequency * c.demand.quantity_rate) for c in orders}
            skus = sorted(vel, key=lambda s: -vel[s])
            m = len(skus)
            for i, s in enumerate(skus):
                self._sku_band[s] = min(n - 1, i * n // m) if m else 0
        # Aisle geometry band per BinKey (shallowest/nearest = band 0).
        by_key: dict = defaultdict(list)
        for aisle in self.warehouse.aisles:
            if not aisle.bins:
                continue
            by_key[binkey_of(aisle.bins[0])].append(aisle)
        for _key, aisles in by_key.items():
            aisles.sort(key=lambda a: (getattr(a, 'aisle_width', 0), a.aisle_id))
            m = len(aisles)
            for i, a in enumerate(aisles):
                self._aisle_band[a.aisle_id] = min(n - 1, i * n // m) if m else 0
        self._build_band_index()

    def _build_band_index(self) -> None:
        """Partition each free-bin tier list into per-band buckets, once, after the band maps are
        set.  Anchors the O(#bands) `_band_pick` fast path; maintained incrementally thereafter by
        `_index_add`/`_index_remove`.  Re-derives `_band_pos` from scratch, so it is correct
        regardless of whether bins were registered before or after zoning was enabled."""
        n = self._zoning_bands
        self._band_index = defaultdict(lambda: [[] for _ in range(n)])
        self._band_pos = {}
        for key, lst in self._index.items():
            buckets = self._band_index[key]
            for b in lst:
                band = self._aisle_band.get(b.location[0], 0)
                self._band_pos[id(b)] = len(buckets[band])
                buckets[band].append(b)

    def _build_abc_bands(self, orders: Any, n: int, abc: dict | None) -> None:
        """Manual A/B/C bands: SKUs cut at cumulative demand-mass thresholds; aisles allocated per
        BinKey by each band's SKU footprint (few hot SKUs ⇒ few hot aisles)."""
        cuts = list((abc or {}).get('mass_thresholds') or [])
        # SKU band = # of mass thresholds the cumulative (hottest-first) mass has passed.
        vel = {c.sku: (c.demand.relative_frequency * c.demand.quantity_rate) for c in orders}
        total = sum(vel.values()) or 1.0
        cum = 0.0
        for s in sorted(vel, key=lambda k: -vel[k]):
            cum += vel[s] / total
            self._sku_band[s] = min(n - 1, bisect.bisect_left(cuts, cum))
        # Per-band footprint = global SKU count (hot band has few SKUs → few aisles).
        band_counts: dict = {}
        for band in self._sku_band.values():
            band_counts[band] = band_counts.get(band, 0) + 1
        weights = [band_counts.get(j, 0) for j in range(n)]
        # Aisle allocation per BinKey (shallowest = band 0), floor of 1 per band when m ≥ n so the
        # _zone_filter spill contract holds; when m < n some bands are empty and spill covers them.
        by_key: dict = defaultdict(list)
        for aisle in self.warehouse.aisles:
            if not aisle.bins:
                continue
            by_key[binkey_of(aisle.bins[0])].append(aisle)
        for _key, aisles in by_key.items():
            aisles.sort(key=lambda a: (getattr(a, 'aisle_width', 0), a.aisle_id))
            counts = _apportion(len(aisles), weights, n)
            idx = 0
            for band, cnt in enumerate(counts):
                for _ in range(cnt):
                    self._aisle_band[aisles[idx].aisle_id] = band
                    idx += 1

    def _band_of_unit(self, unit: StorageUnit) -> int:
        return self._sku_band.get(unit.order.sku, 0)

    def _spill_bands(self, target: int):
        """Band visit order for zoning spill: the unit's band, then COLDER (downgrade to the next
        hotness), then HOTTER (upgrade) — the single source shared by _zone_filter and _band_pick."""
        yield from range(target, self._zoning_bands)      # colder-first
        yield from range(target - 1, -1, -1)              # then hotter

    def _zone_filter(self, unit: StorageUnit, bins: list) -> list:
        """Restrict *bins* to the unit's velocity band, spilling COLDER-first then HOTTER-fallback:
        a hot item whose band is full drops to the next available (colder) aisle; a cold item whose
        band is full UPGRADES toward hotter aisles until it is placed.  Never returns empty.

        The O(len(bins)) partition path used by the ranked wave (once per wave) and the direct unit
        tests; the per-unit path uses the O(#bands) _band_pick over the maintained sub-index."""
        target = self._band_of_unit(unit)
        by_band: dict = defaultdict(list)
        for b in bins:
            by_band[self._aisle_band.get(b.location[0], 0)].append(b)
        for band in self._spill_bands(target):
            if by_band.get(band):
                return by_band[band]
        return bins

    def _band_pick(self, unit: StorageUnit, key) -> list:
        """O(#bands) equivalent of _zone_filter via the maintained per-band sub-index: the first
        non-empty band bucket in spill order.  `key` is the tier BinKey from _candidates_raw; the
        caller guarantees the tier is non-empty, so some bucket is non-empty (the fallback to the
        raw tier list is unreachable defensive cover)."""
        buckets = self._band_index[key]
        for band in self._spill_bands(self._band_of_unit(unit)):
            if buckets[band]:
                return buckets[band]
        return self._index.get(key, [])

    def _group_key(self, unit: StorageUnit):
        """Ranked-wave grouping key.  With zoning, sub-group by (BinKey, band) so each sub-wave
        is a single band (the once-per-wave candidate fetch is then band-correct); OFF ⇒ BinKey
        only (byte-identical)."""
        if self._zoning_enabled:
            return (binkey_of(unit), self._band_of_unit(unit))
        return binkey_of(unit)
