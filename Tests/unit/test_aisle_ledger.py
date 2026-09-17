"""test_aisle_ledger.py — the ledger's own interface, and the invariant it finally makes assertable.

`AisleLedger` collects ten dicts that were loose attributes on `Inventory_Manager` with an
*add* half spread across ten hand-copied commit blocks in the placement policies and a *drop*
half written twice in reorder.  Two quantities drifted in that arrangement and nothing could
say so, because no single place owned the pair.

What is pinned here:

    1. `reconcile()` actually detects drift — the guard that could not be written before.
    2. It is not vacuous: a sound ledger reports nothing, a damaged one reports the aisle.
    3. `drop_bin` is per-BIN and `drop_sku` is per-SKU, and they are separate on purpose.
    4. `last_when_zero` reproduces the one real difference between the two old copies.
    5. The manager's ten attribute names still reach the ledger's dicts — the aliases for the
       seven aisle dicts, the properties for the three rebound per-SKU products.

`lift_sum` was an eighth aisle dict until 2026-09-16.  It was write-only end to end and was
deleted with the `load_min`/`load_max` family that was its only in-memory reader.

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
    mgr.init_placement_state(affinity)
    mgr.init_demand_state(inventory, WorkloadParams())
    return mgr


def test_the_seven_aisle_aliases_are_the_ledger_s_own_dicts():
    """Aliases, not copies: a write through either name must be visible through the other."""
    mgr = _armed_manager()
    pairs = [(mgr._aisle_sku_sets, mgr.ledger.sku_sets),
             (mgr._aisle_idx_sets, mgr.ledger.idx_sets),
             (mgr._aisle_sku_counts, mgr.ledger.sku_counts),
             (mgr._aisle_member_pos, mgr.ledger.member_pos),
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


# ── the add half (ticket 02 stage B) ──────────────────────────────────────────

def test_a_level_the_family_does_not_maintain_is_not_materialised():
    """`None` is not 0.0, and the difference is a `defaultdict` key.

    Adding 0.0 to an untouched level would CREATE `pick_load_sum[aid]`, and
    `aisle_metrics` writes a row per key it finds.  So a demand-only family passing
    `pick_load=0.0` instead of omitting it would silently add rows to every run of every
    arm that does not balance labour.  This is the same mechanism stage A's third hazard
    turned on, in the opposite direction.
    """
    led = AisleLedger()
    led.add_sku(4, 11, 2, demand=1.5)
    assert 4 in led.demand_sum, 'the maintained level must exist'
    assert 4 not in led.pick_load_sum, 'an omitted level must not gain a key'
    assert 4 not in led.vol_sum

    led.add_sku(5, 12, 3, demand=1.0, pick_load=0.0)
    assert led.pick_load_sum[5] == 0.0
    assert 5 in led.pick_load_sum, 'a level given as 0.0 IS maintained -- key and all'


def test_add_bin_is_per_bin_and_add_sku_is_per_sku():
    """Two bins of one SKU: two positions, one set membership, one priced increment."""
    led = AisleLedger()
    led.add_sku(1, 7, 3, demand=2.0)
    led.add_bin(1, 3, 10.0)
    led.add_bin(1, 3, 20.0)          # a second bin, same SKU -- no second add_sku

    assert led.member_pos[1][3] == [10.0, 20.0]
    assert led.sku_sets[1] == {7}
    assert led.idx_sets[1] == {3}
    assert led.demand_sum[1] == 2.0, 'the level is SKU-once, not bin-once'


def test_add_bin_ignores_a_sku_with_no_matrix_index():
    """`idx is None` means the SKU is not in the affinity matrix; both halves skip it."""
    led = AisleLedger()
    led.add_sku(1, 7, None, demand=2.0)
    led.add_bin(1, None, 10.0)
    assert led.sku_sets[1] == {7}
    assert 1 not in led.idx_sets, 'no index, no idx-set key'
    assert 1 not in led.member_pos, 'no index, nowhere to hang a position'


def test_add_and_drop_are_exact_inverses_and_reconcile_throughout():
    """The property the module exists for, and the one no arrangement of loose dicts had.

    Two SKUs, three bins, priced on all three levels; place them all, then take them all
    back.  `reconcile()` must be quiet at every step and every level must land back on
    0.0 -- not "close to", since these are running float sums and the drop path clamps.
    """
    led = AisleLedger()
    led.sku_demand_product    = {7: 2.5, 8: 1.25}
    led.sku_pick_load_product = {7: 40.0, 8: 10.0}
    led.sku_vol_product       = {7: 3.0, 8: 6.0}

    placements = [(1, 7, 3, 10.0), (1, 7, 3, 20.0), (1, 8, 4, 30.0)]
    for aid, sku, idx, x in placements:
        if not led.holds(aid, sku):
            led.add_sku(aid, sku, idx,
                        demand=led.sku_demand_product[sku],
                        pick_load=led.sku_pick_load_product[sku],
                        vol=led.sku_vol_product[sku])
        led.add_bin(aid, idx, x)
        led.count_bin(aid, sku)
        assert led.reconcile() == [], f'drifted while placing {sku} in {aid}'

    assert led.demand_sum[1] == 2.5 + 1.25
    assert led.pick_load_sum[1] == 50.0
    assert led.vol_sum[1] == 9.0
    assert led.sku_counts[1] == {7: 2, 8: 1}

    for aid, sku, idx, x in reversed(placements):
        led.drop_bin(aid, idx, x)
        led.drop_sku(aid, sku, idx)
        assert led.reconcile() == [], f'drifted while dropping {sku} from {aid}'

    assert led.demand_sum[1] == 0.0
    assert led.pick_load_sum[1] == 0.0
    assert led.vol_sum[1] == 0.0, 'the defect ticket 01 fixed, asserted from the add side'
    assert not led.sku_sets[1] and not led.member_pos[1]


def test_count_bin_is_the_increment_drop_sku_decrements():
    led = AisleLedger()
    led.sku_sets[2].add(9)
    for _ in range(3):
        led.count_bin(2, 9)
    assert led.bin_count(2, 9) == 3
    assert led.drop_sku(2, 9, None) is False
    assert led.drop_sku(2, 9, None) is False
    assert led.drop_sku(2, 9, None) is True, 'the third drop is the last bin'
    assert led.bin_count(2, 9) == 0


# ── `over`: a ledger bound to books somebody else owns ────────────────────────

def test_over_binds_rather_than_copies():
    """The gain evaluator's whole safety argument: a write lands in the caller's dict."""
    mine = {}
    from collections import defaultdict
    sets = defaultdict(set)
    led = AisleLedger.over(sku_sets=sets, sku_demand_product=mine)
    led.add_sku(1, 5)
    assert sets[1] == {5}, 'over() copied instead of binding'
    assert led.sku_demand_product is mine


def test_over_refuses_a_write_to_a_book_it_was_not_handed():
    """A family may only write the books its builder gave it, and the refusal says so.

    The alternative -- a private empty dict per missing book -- ABSORBS the write: no
    error, and the number lands where no reader can follow it. That is the shape of every
    defect this module exists for, so an unbound book refuses instead.
    """
    import pytest
    from collections import defaultdict
    sets = defaultdict(set)
    led = AisleLedger.over(sku_sets=sets)
    assert led.bound == {'sku_sets'}

    with pytest.raises(TypeError) as ei:
        led.add_bin(1, 2, 3.0)               # member_pos was never handed over
    assert 'not bound' in str(ei.value)
    assert not sets, 'the bound book must not have been touched'

    # Reads stay soft, so `reconcile` still answers on a ledger holding three books.
    assert led.member_pos.get(1) is None
    assert led.reconcile() == []


def test_over_refuses_a_book_it_does_not_own():
    """A typo in a copier/view table is an import-time refusal, not a silent no-op."""
    import pytest
    with pytest.raises(TypeError) as ei:
        AisleLedger.over(aisle_sku_sets={})       # the MANAGER's name, not the ledger's
    assert 'aisle_sku_sets' in str(ei.value)
    assert 'sku_sets' in str(ei.value), 'the refusal should name what it does own'


# ── the ratchet: the ledger is the ONLY writer ────────────────────────────────

#: The aisle books, under the names the placement module binds them to: the parameter
#: names the builders take, and the short instance attributes the four pools hoist them
#: into.  Reads through these names are fine and frequent -- the scorers do nothing else.
#: A WRITE through one is the eleventh hand-copy starting.
_BOOK_NAMES = frozenset({
    'aisle_sku_sets', 'aisle_idx_sets', 'aisle_demand_sum', 'aisle_pick_load_sum',
    'aisle_vol_sum', 'aisle_member_pos', 'aisle_sku_counts',
    'self._ass', 'self._ais', 'self._ads', 'self._apl', 'self._avs', 'self._amp',
})

#: Methods that mutate a set / list / dict in place.  `.values()`, `.get()`, `.items()`
#: and `.keys()` are deliberately absent: those are the reads.
_MUTATORS = frozenset({'add', 'append', 'extend', 'update', 'discard', 'remove',
                       'pop', 'popitem', 'clear', 'setdefault', 'insert'})


def _target_root(node) -> str | None:
    """`aisle_sku_sets[aid]` -> 'aisle_sku_sets'; `self._amp[a][i]` -> 'self._amp'."""
    import ast
    while isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f'{node.value.id}.{node.attr}'
    return None


def test_no_placement_policy_writes_an_aisle_book_directly():
    """Eleven hand-copied commit blocks maintained three different subsets between them.

    Two of the three were wrong: `vol_sum` grew forever (it is READ by the cart scorer, so
    it moved real placements) and `lift_sum` decayed forever.  Neither could be asserted,
    because no single place owned the pair -- which is what `AisleLedger` now is.

    This is an AST walk, not a regex, and it is a RATCHET rather than a declaration: the
    declaration is the ledger itself, and this only refuses a caller that goes around it.
    Reads are untouched -- the scorers read these dicts on every candidate and must keep
    doing so (`AisleLedger`'s own docstring records why they are plain attributes).
    """
    import ast
    import pathlib

    import Warehouse.placement.Assignment_Functions as af   # located by import, not by cwd
    src = pathlib.Path(af.__file__).read_text(encoding='utf-8')
    tree = ast.parse(src)
    offences: list[str] = []

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Subscript) and _target_root(t) in _BOOK_NAMES:
                offences.append(f'line {node.lineno}: assigns into {_target_root(t)}[...]')
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _MUTATORS):
            root = _target_root(node.func.value)
            if root in _BOOK_NAMES:
                offences.append(
                    f'line {node.lineno}: calls .{node.func.attr}() on {root}[...]')

    assert not offences, (
        'a placement policy writes an aisle book without going through the ledger '
        '(AisleLedger.add_sku / add_bin / count_bin):\n  ' + '\n  '.join(offences))


def test_the_ratchet_can_actually_fail():
    """Non-vacuity: the walk above must flag the exact shape it exists to catch."""
    import ast
    planted = ast.parse(
        'def f(aisle_demand_sum, aid, fq):\n'
        '    aisle_demand_sum[aid] += fq\n')
    hits = [n for n in ast.walk(planted)
            if isinstance(n, ast.AugAssign) and _target_root(n.target) in _BOOK_NAMES]
    assert len(hits) == 1, 'the walk would not have seen a hand-written increment'

    planted = ast.parse(
        'def f(aisle_sku_sets, aid, sku):\n'
        '    aisle_sku_sets[aid].add(sku)\n')
    hits = [n for n in ast.walk(planted)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr in _MUTATORS and _target_root(n.func.value) in _BOOK_NAMES]
    assert len(hits) == 1, 'the walk would not have seen a hand-written set add'
