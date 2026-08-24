"""Velocity zoning (C): a per-regime candidate-layer toggle that restricts a unit's viable bins
to its velocity band ("like-with-like"), composing with every arm.  OFF ⇒ identity pass-through
(byte-identical).  Tests the band computation, the band filter + spill, the group key, the
per-band sub-index fast path (_band_index / _band_pick), and — end to end — that a full
_stock_per_unit drain under zoning routes each placed unit into a bin of its target band (or a
legit spill band) and empties the queue.

Run:  python -m pytest Tests/test_velocity_zoning.py -q
"""
import random
import types

from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Warehouse_Builder import Warehouse_Builder
from perf_simulation import _build_inventory, _build_warehouse_cfg


def _mgr(enabled=True, n_bands=3):
    m = Inventory_Manager.__new__(Inventory_Manager)     # bypass full construction
    m._zoning_enabled = enabled
    m._zoning_bands = n_bands
    m._sku_band = {}
    m._aisle_band = {}
    # State configure_zoning / the index mutators read (normally set in __init__).
    m._index = {}                 # tier BinKey -> free-bin list
    m._band_index = {}            # tier BinKey -> per-band buckets (built by _build_band_index)
    m._band_pos = {}              # id(bin) -> position in its band bucket
    m._bin_index_pos = {}         # id(bin) -> position in its _index tier list
    m._travel_costs_ready = False
    return m


def _bin(aid):
    return types.SimpleNamespace(location=(aid, 0, 0))


def _unit(sku):
    return types.SimpleNamespace(order=types.SimpleNamespace(sku=sku))


def _ffbin(aid):
    # A bin binkey_of() can read (no .order ⇒ uses the bin branch).
    return types.SimpleNamespace(handling_type='fulfillment', storage_type='fulfillment',
                                 storage_size='ff_small', unit_type='fulfillment',
                                 location=(aid, 0, 0))


def _faisle(aid, w):
    return types.SimpleNamespace(aisle_id=aid, aisle_width=w, bins=[_ffbin(aid)])


def test_configure_zoning_bands_hot_to_shallow():
    """Aisles band by (width, id) shallowest-first; SKUs band by velocity hottest-first."""
    m = _mgr(enabled=False)
    m.warehouse = types.SimpleNamespace(aisles=[_faisle(1, 1600), _faisle(2, 160), _faisle(3, 640)])
    orders = [types.SimpleNamespace(sku=10, demand=types.SimpleNamespace(relative_frequency=0.9, quantity_rate=1.0)),
              types.SimpleNamespace(sku=20, demand=types.SimpleNamespace(relative_frequency=0.1, quantity_rate=1.0)),
              types.SimpleNamespace(sku=30, demand=types.SimpleNamespace(relative_frequency=0.5, quantity_rate=1.0))]
    m.configure_zoning(True, 3, orders)
    # widths 160(id2) < 640(id3) < 1600(id1) → bands 0,1,2
    assert m._aisle_band == {2: 0, 3: 1, 1: 2}
    # velocities 0.9(10) > 0.5(30) > 0.1(20) → bands 0,1,2
    assert (m._sku_band[10], m._sku_band[30], m._sku_band[20]) == (0, 1, 2)


def test_zone_filter_routes_to_matching_band():
    m = _mgr()
    m._aisle_band = {1: 0, 2: 1, 3: 2}       # aisle 1 shallow … 3 deep
    m._sku_band = {100: 0, 200: 2}           # sku 100 hot, 200 cold
    bins = [_bin(1), _bin(2), _bin(3)]
    assert [b.location[0] for b in m._zone_filter(_unit(100), bins)] == [1]   # hot → shallow
    assert [b.location[0] for b in m._zone_filter(_unit(200), bins)] == [3]   # cold → deep


def test_zone_filter_spills_when_band_empty():
    """A hot SKU (band 0) whose band has no free bins spills to the nearest band, never
    returning empty (no unit unplaceable)."""
    m = _mgr()
    m._aisle_band = {2: 1, 3: 2}             # no band-0 aisle available
    m._sku_band = {100: 0}
    got = m._zone_filter(_unit(100), [_bin(2), _bin(3)])
    assert [b.location[0] for b in got] == [2]   # spilled to the nearest band (1)


def test_group_key_off_is_binkey_on_is_binkey_band():
    """Group key adds the band only when zoning is on (byte-identical off)."""
    from Warehouse.inventory.inventory_common import binkey_of
    # binkey_of reads storage_size / unit_category off the UNIT and handling/category off order.
    unit = types.SimpleNamespace(
        storage_size='ff_small', unit_category='fulfillment',
        order=types.SimpleNamespace(
            sku=7,
            storage_handle_config=types.SimpleNamespace(handling='fulfillment', category='fulfillment')))
    off = _mgr(enabled=False)
    assert off._group_key(unit) == binkey_of(unit)
    on = _mgr(enabled=True)
    on._sku_band = {7: 1}
    assert on._group_key(unit) == (binkey_of(unit), 1)


# ── ABC (manual) zone construction + directional spill ───────────────────────────

def _order(sku, freq, qty=1.0):
    return types.SimpleNamespace(
        sku=sku, demand=types.SimpleNamespace(relative_frequency=freq, quantity_rate=qty))


def test_apportion_floor_sum_and_hot_is_small():
    from Warehouse.inventory.Inventory_Management import _apportion
    counts = _apportion(10, [20, 30, 50], 3)     # hot few SKUs, cold many
    assert sum(counts) == 10 and all(c >= 1 for c in counts)   # floor 1 per band
    assert counts[0] < counts[2]                 # hot band gets fewer aisles than cold
    assert _apportion(2, [5, 3, 1], 3) == [1, 1, 0]            # m<n: first m bands get 1
    assert _apportion(0, [1, 1, 1], 3) == [0, 0, 0]


def test_abc_bands_hot_band_is_small():
    """ABC: an 80/20-ish demand mass ⇒ the hot A-band holds few SKUs and few aisles; the cold
    C-band holds many rarely-visited aisles."""
    m = _mgr(enabled=False)
    m.warehouse = types.SimpleNamespace(aisles=[_faisle(i, 100) for i in range(1, 11)])  # 10 aisles
    orders = ([_order(s, 0.035) for s in range(1, 21)]      # 20 hot  → 0.70 mass
              + [_order(s, 0.00667) for s in range(21, 51)]  # 30 warm → 0.20
              + [_order(s, 0.002) for s in range(51, 101)])  # 50 cold → 0.10
    m.configure_zoning(True, 3, orders, mode='abc', abc={'mass_thresholds': [0.7, 0.9]})
    hot_skus = sum(1 for b in m._sku_band.values() if b == 0)
    cold_skus = sum(1 for b in m._sku_band.values() if b == 2)
    assert hot_skus <= 25 and cold_skus >= 40           # hot A-band = few SKUs
    hot_aisles = sum(1 for b in m._aisle_band.values() if b == 0)
    cold_aisles = sum(1 for b in m._aisle_band.values() if b == 2)
    assert 1 <= hot_aisles < cold_aisles                # hot zone small, cold zone large, spill-safe


def test_zone_filter_cold_upgrades_when_cold_full():
    """A cold item whose cold band has no free bins UPGRADES toward hotter aisles."""
    m = _mgr()
    m._aisle_band = {1: 0, 2: 1}         # band 2 (cold) has no available aisle here
    m._sku_band = {200: 2}
    got = m._zone_filter(_unit(200), [_bin(1), _bin(2)])
    assert [b.location[0] for b in got] == [2]           # upgraded to the nearest hotter band (1)


def test_zone_filter_hot_downgrades_to_next_colder():
    """A hot item whose hot band is full drops to the NEXT available (colder) aisle, not straight
    to the coldest."""
    m = _mgr()
    m._aisle_band = {2: 1, 3: 2}         # band 0 (hot) has no available aisle
    m._sku_band = {100: 0}
    got = m._zone_filter(_unit(100), [_bin(2), _bin(3)])
    assert [b.location[0] for b in got] == [2]           # next colder band (1), not the coldest (2)


# ── per-band sub-index fast path (_band_index / _band_pick) ───────────────────────
# The O(#bands) placement path _stock_per_unit uses under zoning: it must return the SAME in-band
# candidate SET as _zone_filter, stay consistent with _index across add/remove, and spill the same.

def _key_of(bin_):
    from Warehouse.inventory.inventory_common import binkey_of
    return binkey_of(bin_)


def test_band_pick_matches_zone_filter_set():
    """_band_pick (via the sub-index) returns the same SET of in-band bins as _zone_filter (via the
    O(B) partition) for every target band."""
    m = _mgr(enabled=True, n_bands=3)
    m._aisle_band = {1: 0, 2: 0, 3: 1, 4: 2, 5: 2}
    bins = [_ffbin(a) for a in (1, 2, 3, 4, 5)]
    key = _key_of(bins[0])
    m._index = {key: list(bins)}
    m._build_band_index()
    for target in range(3):
        m._sku_band = {700: target}
        u = _unit(700)
        via_index  = {id(b) for b in m._band_pick(u, key)}
        via_filter = {id(b) for b in m._zone_filter(u, m._index[key])}
        assert via_index == via_filter and via_index      # same non-empty set


def test_band_index_consistent_after_add_remove():
    """After a sequence of _index_add / _index_remove (swap-remove), each band bucket still equals
    the partition of the live _index tier by aisle band — the maintenance invariant _band_pick relies
    on."""
    import collections
    m = _mgr(enabled=True, n_bands=3)
    m._aisle_band = {a: a % 3 for a in range(1, 10)}
    m._index = collections.defaultdict(list)
    m._band_index = collections.defaultdict(lambda: [[] for _ in range(3)])
    bins = [_ffbin(a) for a in range(1, 10)]
    key = _key_of(bins[0])

    def invariant():
        want = collections.defaultdict(set)
        for b in m._index[key]:
            want[m._aisle_band[b.location[0]]].add(id(b))
        for band in range(3):
            assert {id(b) for b in m._band_index[key][band]} == want[band]

    for b in bins:
        m._index_add(b)
    invariant()
    # Remove a spread of bins (exercises swap-remove: middle, an end, another middle).
    for b in (bins[4], bins[8], bins[1], bins[0]):
        m._index_remove(b)
        invariant()


def test_band_pick_spills_through_index():
    """A hot unit whose band-0 bucket is empty spills COLDER via the sub-index, exactly like
    _zone_filter."""
    m = _mgr(enabled=True, n_bands=3)
    m._aisle_band = {2: 1, 3: 2}         # no band-0 aisle
    bins = [_ffbin(2), _ffbin(3)]
    key = _key_of(bins[0])
    m._index = {key: list(bins)}
    m._build_band_index()
    m._sku_band = {100: 0}               # hot unit, band 0 empty
    got = m._band_pick(_unit(100), key)
    assert [b.location[0] for b in got] == [2]           # spilled to the nearest colder band (1)


# ── end-to-end drain: _stock_per_unit routes each unit into its velocity band ─────────────────────
# The tests above lock _band_pick's candidate-SET parity and sub-index maintenance in isolation;
# they do NOT prove the per-unit drain actually CONSUMES the fast path.  This drives a REAL
# Inventory_Manager over a small store warehouse through a full _stock_per_unit drain (zoning ON,
# default uniform-FIFO — the affected arm) and asserts hot SKUs land in hot-band aisles / cold in
# cold when the band has room, spill lands only in the band _band_pick selected, and the queue
# empties.  extra_pct=0.30 ⇒ 3 aisles per BinKey ⇒ all 3 bands are real and roomy (no forced spill).
_ZONE_SEED, _ZONE_SKUS, _ZONE_BPA, _ZONE_EXTRA, _ZONE_BANDS = 42, 1200, 10, 0.30, 3


def _build_zoned_manager(seed, n_skus, bins_per_aisle, extra_pct, n_bands):
    """A real Inventory_Manager over a small store warehouse with equal-count velocity zoning ON and
    the default uniform-FIFO placement, intake still QUEUED (caller drives the drain).  Reuses the
    shared perf_simulation builders so the manager's state dicts are populated the real way."""
    inventory = _build_inventory(n_skus, seed)
    wh_cfg    = _build_warehouse_cfg(n_skus, bins_per_aisle, extra_pct=extra_pct)
    Aisle.next_aisle_id = 1
    random.seed(seed)
    warehouse = Warehouse_Builder().from_config(wh_cfg).build()
    mgr       = Inventory_Manager(warehouse, affinity=None)
    # Build _sku_band / _aisle_band / _band_index the real way (before intake, so the drain runs the
    # fast path over a fully-populated sub-index).
    mgr.configure_zoning(True, n_bands, inventory.orders, mode='equal')
    return mgr, inventory


def test_stock_per_unit_zoning_routes_each_unit_to_its_target_band():
    """A full _stock_per_unit drain under zoning routes every placed unit into a bin whose aisle band
    equals the unit's target velocity band when that band has free capacity (hot SKUs → shallow/hot
    aisles, cold → deep), spills only into the band _band_pick selected when the target band is full,
    and fully drains the queue.  Locks the end-to-end behaviour the O(#bands) _band_pick fast path
    exists to serve — the unit tests above cover only its candidate-SET parity, not the drain."""
    mgr, inventory = _build_zoned_manager(
        _ZONE_SEED, _ZONE_SKUS, _ZONE_BPA, _ZONE_EXTRA, _ZONE_BANDS)

    # Non-vacuity guard: the drain must take the zoning fast path (the `elif self._zoning_enabled`
    # branch → _band_pick), not the OFF _candidates branch, the travel-index branch, or the ranked
    # wave — otherwise a silent fallback could pass this test trivially.
    assert mgr._zoning_enabled is True
    assert mgr._travel_costs_ready is False
    assert mgr.placement.is_ranked is False, mgr.placement.name
    assert mgr.placement.uses_aisle_index is False, mgr.placement.name

    # Delegating spies (observe, never stub): capture at each placement decision the unit's target
    # band + whether that band still had a free bin, and the band _band_pick actually returned; then
    # at commit read the band of the bin the unit landed in.  Instance-attribute shadowing → the
    # spies receive the exact (unit, key)/(unit, bin_) args _stock_per_unit passes.
    picks: dict[int, tuple[int, int | None, bool]] = {}
    orig_band_pick = mgr._band_pick

    def band_pick_spy(unit, key):
        target      = mgr._band_of_unit(unit)
        target_free = bool(mgr._band_index[key][target])   # pre-removal: the bin is still in-bucket
        chosen      = orig_band_pick(unit, key)
        chosen_band = mgr._aisle_band.get(chosen[0].location[0], 0) if chosen else None
        picks[id(unit)] = (target, chosen_band, target_free)
        return chosen

    mgr._band_pick = band_pick_spy

    routed: list[tuple[int, int | None, bool, int]] = []
    orig_execute = mgr._execute_placement

    def execute_spy(unit, bin_, **kw):
        actual_band = mgr._aisle_band.get(bin_.location[0], 0)
        rec = picks.pop(id(unit), None)
        assert rec is not None, f'placed unit sku={unit.order.sku} bypassed the _band_pick fast path'
        target, chosen_band, target_free = rec
        routed.append((target, chosen_band, target_free, actual_band))
        orig_execute(unit, bin_, **kw)

    mgr._execute_placement = execute_spy

    random.seed(_ZONE_SEED + 1)
    mgr.enqueue_all(inventory.orders, quantity=1)         # queues N units and drives the drain

    # ── the queue fully drained ───────────────────────────────────────────────────
    assert len(mgr._stock_queue) == 0, (
        f'{len(mgr._stock_queue)} units left queued after the zoned drain (expected 0)')

    # ── the fast path was actually consumed for every placement ───────────────────
    # 1 unit / SKU with ample capacity ⇒ every SKU places, each via _band_pick (guaranteed by the
    # rec-is-not-None assert in execute_spy).  A non-full `routed` would mean a silent fallback.
    assert len(routed) == _ZONE_SKUS, (
        f'{len(routed)} placements routed via _band_pick, expected {_ZONE_SKUS}')

    # ── per-unit routing invariant ────────────────────────────────────────────────
    hot_to_hot = 0                      # target band had room  → landed in the target band
    spilled    = 0                      # target band was full  → landed in the chosen spill band
    matched_bands: set[int] = set()
    for target, chosen_band, target_free, actual_band in routed:
        # place_one (uniform FIFO) draws from the exact bucket _band_pick returned, so the landed
        # band is that bucket's band — placement can never leak across the band boundary.
        assert actual_band == chosen_band, (
            f'unit target={target} landed in band {actual_band}, '
            f'but _band_pick selected band {chosen_band}')
        if target_free:
            assert actual_band == target, (
                f'target band {target} had free capacity, yet the unit landed in band '
                f'{actual_band} — like-with-like routing broken')
            hot_to_hot += 1
            matched_bands.add(target)
        else:
            assert actual_band != target, (
                f'target band {target} reported full, yet the unit still landed in it')
            spilled += 1

    # Non-vacuity: the drain routed straight to target for the bulk of units, and BOTH extremes
    # fired — hot SKUs (band 0) reached band-0 aisles AND cold SKUs (band n-1) reached band n-1
    # aisles — so this is genuine like-with-like routing, not a degenerate single-band collapse.
    assert hot_to_hot > 0 and hot_to_hot + spilled == _ZONE_SKUS, (hot_to_hot, spilled, _ZONE_SKUS)
    assert 0 in matched_bands and (_ZONE_BANDS - 1) in matched_bands, (
        f'expected target-band matches spanning hot band 0 .. cold band {_ZONE_BANDS - 1}, '
        f'got matches only in bands {sorted(matched_bands)}')
