"""Velocity zoning (C): a per-regime candidate-layer toggle that restricts a unit's viable bins
to its velocity band ("like-with-like"), composing with every arm.  OFF ⇒ identity pass-through
(byte-identical).  Tests the band computation, the band filter + spill, and the group key."""
import types

from Warehouse.Inventory_Management import Inventory_Manager


def _mgr(enabled=True, n_bands=3):
    m = Inventory_Manager.__new__(Inventory_Manager)     # bypass full construction
    m._zoning_enabled = enabled
    m._zoning_bands = n_bands
    m._sku_band = {}
    m._aisle_band = {}
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
    from Warehouse.inventory_common import binkey_of
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
    from Warehouse.Inventory_Management import _apportion
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
