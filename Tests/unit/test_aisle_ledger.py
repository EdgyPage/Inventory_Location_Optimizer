"""test_aisle_ledger.py — the ledger's own interface, and the invariant it finally makes assertable.

`AisleLedger` collects eleven dicts that were loose attributes on `Inventory_Manager` with an
*add* half spread across ten hand-copied commit blocks in the placement policies and a *drop*
half written twice in reorder.  Two quantities drifted in that arrangement and nothing could
say so, because no single place owned the pair.

What is pinned here:

    1. `reconcile()` actually detects drift — the guard that could not be written before.
    2. It is not vacuous: a sound ledger reports nothing, a damaged one reports the aisle.
    3. `drop_bin` is per-BIN and `drop_sku` is per-SKU, and they are separate on purpose.
    4. `last_when_zero` reproduces the one real difference between the two old copies.
    5. The manager's eleven attribute names still reach the ledger's dicts — the aliases for
       the eight aisle dicts, the properties for the three rebound per-SKU products.

    python -m pytest Tests/unit/test_aisle_ledger.py -q
"""
from __future__ import annotations

import random

from Warehouse.inventory.aisle_ledger import AisleLedger
from Warehouse.layout.Aisle_Storage import Aisle
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.catalog.Inventory_Builder import Inventory
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Warehouse_Builder import AisleConfig, Warehouse_Builder, WarehouseConfig
from Optimization.metrics.Workload import WorkloadParams

from Tests.unit.test_aisle_vol_sum_teardown import _make_carton, _WH_CFG


# ── the invariant ─────────────────────────────────────────────────────────────

def test_reconcile_is_quiet_on_a_sound_ledger():
    led = AisleLedger()
    led.sku_demand_product = {1: 2.0, 2: 3.0}
    led.sku_sets[7].update({1, 2})
    led.demand_sum[7] = 5.0
    assert led.reconcile() == []
    led.assert_sound()          # must not raise


def test_reconcile_detects_a_level_that_drifted_from_its_members():
    """The exact shape of the two shipped defects: a level that no longer equals its members."""
    led = AisleLedger()
    led.sku_vol_product = {1: 10.0, 2: 20.0}
    led.sku_sets[3].update({1, 2})
    led.vol_sum[3] = 30.0
    assert led.reconcile() == [], 'fixture is not sound to begin with'

    led.sku_sets[3].discard(2)          # a SKU left, the level was not decremented
    findings = led.reconcile()
    assert len(findings) == 1, findings
    assert 'vol_sum[3]' in findings[0]
    assert '+20' in findings[0], f'the drift magnitude should be reported: {findings[0]}'

    try:
        led.assert_sound()
    except AssertionError as e:
        assert 'vol_sum[3]' in str(e)
    else:
        raise AssertionError('assert_sound passed on a damaged ledger')


def test_reconcile_skips_a_level_whose_products_were_never_seeded():
    """Flag-off arms never seed pick_load/vol, and an unseeded level is not a finding."""
    led = AisleLedger()
    led.sku_demand_product = {1: 2.0}
    led.sku_sets[1].add(1)
    led.demand_sum[1] = 2.0
    led.vol_sum[1] = 999.0              # garbage, but sku_vol_product is empty
    assert led.reconcile() == []


# ── the two grains ────────────────────────────────────────────────────────────

def test_drop_bin_is_per_bin_and_leaves_the_levels_alone():
    """member_pos tracks a position per LIVE bin; the priced levels are SKU-once."""
    led = AisleLedger()
    led.sku_demand_product = {5: 4.0}
    led.sku_sets[2].add(5)
    led.sku_counts[2][5] = 2
    led.member_pos[2][9].extend([11.0, 22.0])
    led.demand_sum[2] = 4.0

    led.drop_bin(2, 9, 11.0)
    assert led.member_pos[2][9] == [22.0]
    assert led.demand_sum[2] == 4.0, 'drop_bin must not touch a priced level'
    assert 5 in led.sku_sets[2], 'drop_bin must not retire the SKU'

    led.drop_bin(2, 9, 22.0)
    assert 9 not in led.member_pos[2], 'the last position should remove the idx entry'


def test_drop_sku_subtracts_only_on_the_last_bin():
    led = AisleLedger()
    led.sku_demand_product = {5: 4.0}
    led.sku_sets[2].add(5)
    led.idx_sets[2].add(9)
    led.sku_counts[2][5] = 2
    led.demand_sum[2] = 4.0

    assert led.drop_sku(2, 5, 9) is False, 'not the last bin'
    assert led.sku_counts[2][5] == 1
    assert led.demand_sum[2] == 4.0

    assert led.drop_sku(2, 5, 9) is True, 'the last bin'
    assert 5 not in led.sku_sets[2] and 9 not in led.idx_sets[2]
    assert led.demand_sum[2] == 0.0


def test_last_when_zero_is_the_difference_between_the_two_old_copies():
    """The hot twin's bare `else` treated n == 0 like n == 1; the cold path's `elif` did not."""
    cold = AisleLedger()
    cold.sku_sets[1].add(3)
    assert cold.drop_sku(1, 3, None) is False, 'cold path must ignore n == 0'
    assert 3 in cold.sku_sets[1], 'cold path must leave the SKU resident'

    hot = AisleLedger()
    hot.sku_sets[1].add(3)
    assert hot.drop_sku(1, 3, None, last_when_zero=True) is True
    assert 3 not in hot.sku_sets[1]


def test_lift_delta_is_taken_against_the_post_discard_index_set():
    """A precomputed scalar would be measured against the wrong set — hence the callable."""
    led = AisleLedger()
    led.sku_sets[4].add(6)
    led.idx_sets[4].update({6, 7})
    led.sku_counts[4][6] = 1
    led.lift_sum[4] = 100.0

    seen: list[set] = []

    def probe(idx_set):
        seen.append(set(idx_set))
        return 10.0

    led.drop_sku(4, 6, 6, probe)
    assert seen == [{7}], f'delta must see the set AFTER the discard, saw {seen}'
    assert led.lift_sum[4] == 90.0


# ── the manager still reaches the same dicts ──────────────────────────────────

def _armed_manager(seed: int = 42):
    Aisle.next_aisle_id = 1
    random.seed(seed)
    wh        = Warehouse_Builder().from_config(_WH_CFG).build()
    affinity  = AffinityStore(':memory:')
    mgr       = Inventory_Manager(wh, affinity=affinity)
    inventory = Inventory([_make_carton(sku=i) for i in range(1, 6)])
    random.seed(seed + 1)
    mgr.enqueue_all(inventory.orders)
    mgr.init_lift_state(affinity)
    mgr.init_demand_state(inventory, WorkloadParams())
    return mgr


def test_the_eight_aisle_aliases_are_the_ledger_s_own_dicts():
    """Aliases, not copies: a write through either name must be visible through the other."""
    mgr = _armed_manager()
    pairs = [(mgr._aisle_sku_sets, mgr.ledger.sku_sets),
             (mgr._aisle_idx_sets, mgr.ledger.idx_sets),
             (mgr._aisle_sku_counts, mgr.ledger.sku_counts),
             (mgr._aisle_member_pos, mgr.ledger.member_pos),
             (mgr._aisle_lift_sum, mgr.ledger.lift_sum),
             (mgr._aisle_demand_sum, mgr.ledger.demand_sum),
             (mgr._aisle_pick_load_sum, mgr.ledger.pick_load_sum),
             (mgr._aisle_vol_sum, mgr.ledger.vol_sum)]
    for alias, owned in pairs:
        assert alias is owned, 'the manager attribute detached from the ledger'


def test_the_three_products_survive_the_rebind_in_init_demand_state():
    """`init_demand_state` REBINDS these; a plain alias would silently detach there."""
    mgr = _armed_manager()
    assert mgr._sku_demand_product is mgr.ledger.sku_demand_product
    assert mgr._sku_pick_load_product is mgr.ledger.sku_pick_load_product
    assert mgr._sku_vol_product is mgr.ledger.sku_vol_product
    assert mgr._sku_vol_product, 'the fixture seeded nothing, so this proves nothing'


def test_a_real_manager_s_ledger_reconciles_after_setup():
    """The end-to-end form of the invariant, on a manager built the production way."""
    mgr = _armed_manager()
    assert mgr.ledger.reconcile() == []
    mgr.ledger.assert_sound()
