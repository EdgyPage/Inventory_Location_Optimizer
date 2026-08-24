"""test_operations_actors.py — who does the work, and the two id spaces.

`Mode` is not a new idea; it has just never been readable. `channels.py` has named its two
pools `'store_machine'` and `'fulfillment_walker'` for as long as they have existed, and
`simconfig/constants.py` comments them as a machine order-picker pool and a human-walker
pool. Nothing anywhere read either name — no forklift, machine, vehicle or equipment concept
existed in the repo. The distinction was real, written down in prose, and inert.

The part with teeth is the **two id spaces**. Every reader that predates a second crew
assumes picker ids are dense and per-crew, and a `uid` that leaked into a `local_id` slot
would land inside `[0, k)` and be silently accepted. They are two named fields for exactly
that reason.

Run:  python -m pytest Tests/unit/test_operations_actors.py -q
"""
from __future__ import annotations

import ast
import inspect
import pickle

import pytest

from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.operations import Crew, Mode, Role, Worker
from Warehouse.operations import roles as roles_mod
from Warehouse.operations import worker as worker_mod

FOOT = SpeedProfile(2.0, 4.0)
MACHINE = SpeedProfile(3.0, 2.0)


# ── roles and modes ───────────────────────────────────────────────────────────────

def test_the_four_combinations_exist():
    assert {r.value for r in Role} == {'pick', 'put'}
    assert {m.value for m in Mode} == {'foot', 'machine'}


def test_a_member_renders_as_its_bare_value():
    """It goes into a DB column and into run metadata; `Mode.MACHINE` is not what belongs
    there."""
    assert f'{Mode.MACHINE}' == 'machine'
    assert f'{Role.PUT}' == 'put'
    assert Mode.FOOT == 'foot'          # str enum: round-trips out of a DB with no shim


@pytest.mark.parametrize('cls,text,want', [(Role, 'pick', Role.PICK),
                                           (Mode, 'machine', Mode.MACHINE)])
def test_of_parses_known_text(cls, text, want):
    assert cls.of(text) is want
    assert cls.of(want) is want          # idempotent on a member


@pytest.mark.parametrize('cls,bad', [(Role, 'picker'), (Mode, 'walking'), (Mode, ''),
                                     (Role, None)])
def test_of_rejects_anything_else_and_lists_the_options(cls, bad):
    """The strict counterpart to being a str enum: `'machien' == Role.PICK` is merely False,
    so a typo in stored data would route an actor nowhere rather than fail."""
    with pytest.raises(ValueError, match='expected one of'):
        cls.of(bad)


# ── a worker ──────────────────────────────────────────────────────────────────────

def test_a_worker_carries_both_ids_separately():
    w = Worker(uid=7, local_id=2, role=Role.PICK, mode=Mode.MACHINE, speed=MACHINE)
    assert (w.uid, w.local_id) == (7, 2)
    assert w.kind == 'pick/machine'


def test_a_worker_is_frozen_and_picklable():
    w = Worker(uid=0, local_id=0, role=Role.PUT, mode=Mode.FOOT, speed=FOOT)
    assert pickle.loads(pickle.dumps(w)) == w
    with pytest.raises(Exception):
        w.uid = 3


@pytest.mark.parametrize('kw', [{'uid': -1, 'local_id': 0}, {'uid': 0, 'local_id': -1}])
def test_a_negative_id_raises(kw):
    with pytest.raises(ValueError, match='negative'):
        Worker(role=Role.PICK, mode=Mode.FOOT, speed=FOOT, **kw)


# ── a crew, and uid allocation across crews ───────────────────────────────────────

def test_a_crews_local_ids_are_dense_from_zero():
    """What `_group_events_by_picker` and `progress_at` both require."""
    crew = Crew(Role.PICK, Mode.MACHINE, MACHINE, size=25)
    assert [w.local_id for w in crew.workers()] == list(range(25))


def test_uids_are_unique_across_two_crews():
    pickers = Crew(Role.PICK, Mode.MACHINE, MACHINE, size=25)
    putters = Crew(Role.PUT, Mode.FOOT, FOOT, size=6)
    a = pickers.workers(0)
    b = putters.workers(pickers.next_uid(0))
    uids = [w.uid for w in a] + [w.uid for w in b]
    assert len(set(uids)) == len(uids) == 31


def test_the_local_ids_of_two_crews_deliberately_COLLIDE():
    """The reason there are two spaces at all. Both crews number from 0, so `local_id` alone
    cannot identify an actor once a second crew exists — and the old
    `0 <= pid < k_pickers` filter would have accepted the putter as picker 0."""
    a = Crew(Role.PICK, Mode.MACHINE, MACHINE, size=3).workers(0)
    b = Crew(Role.PUT, Mode.FOOT, FOOT, size=3).workers(3)
    assert [w.local_id for w in a] == [w.local_id for w in b] == [0, 1, 2]
    assert {w.uid for w in a}.isdisjoint({w.uid for w in b})


def test_an_empty_crew_raises():
    with pytest.raises(ValueError, match='does no work'):
        Crew(Role.PICK, Mode.FOOT, FOOT, size=0)


def test_a_crew_hands_out_its_own_speed():
    crew = Crew(Role.PICK, Mode.FOOT, FOOT, size=2)
    assert all(w.speed == FOOT for w in crew.workers())
    assert FOOT != MACHINE, 'a walker and a machine must not share a profile'


# ── the boundary that lets a future inbound package reuse this ────────────────────

# ── the two real pools, which now say what they are ──────────────────────────────

def _channels():
    from Optimization.config.channels import build_channels
    from Optimization.config.sim_config import STORE_CONFIGS, _build_pick_cfg
    store = _build_pick_cfg(STORE_CONFIGS[0], num_pickers=25)
    return {c.name: c for c in build_channels(store, 25, include_fulfillment=True)}


def test_the_pool_names_finally_match_a_readable_mode():
    """`store_machine` and `fulfillment_walker` were strings nothing read."""
    chs = _channels()
    assert chs['store'].picker.mode is Mode.MACHINE
    assert chs['fulfillment'].picker.mode is Mode.FOOT
    assert all(c.picker.role is Role.PICK for c in chs.values())


def test_a_pool_converts_to_a_crew_carrying_its_own_speed():
    for ch in _channels().values():
        crew = ch.picker.crew()
        assert crew.size == ch.picker.num_pickers
        assert crew.speed == ch.picker.cost.speed
        assert [w.local_id for w in crew.workers()] == list(range(crew.size))


def test_the_two_modes_have_genuinely_different_profiles():
    chs = _channels()
    assert chs['store'].picker.cost.speed != chs['fulfillment'].picker.cost.speed


def test_the_configured_speeds_are_physically_backwards_and_that_is_recorded():
    """A FINDING, pinned rather than fixed.

    Naming the mode made this legible for the first time: the `machine` pool is configured
    to lift at 2 ft/s and the `foot` pool at 4 ft/s — a person raising a load twice as fast
    as an order-picker. The fulfillment file is named `ful_calibrated`, so these are very
    likely calibration residuals wearing physical names.

    Re-valuing them changes every fulfillment result and is a DATA decision, not a
    refactor, so this test asserts today's values and exists to be deleted by whoever makes
    it. It fails if someone changes them quietly.
    """
    chs = _channels()
    machine = chs['store'].picker.cost.speed
    foot = chs['fulfillment'].picker.cost.speed
    assert (machine.x_ft_s, machine.y_ft_s) == (3, 2)
    assert (foot.x_ft_s, foot.y_ft_s) == (2, 4)
    assert foot.y_ft_s > machine.y_ft_s, (
        'the walker no longer out-lifts the machine — if that was deliberate, delete this '
        'test and the note in Warehouse/operations/README.md')


def test_a_profile_that_does_not_care_still_builds():
    """Tests and Diagnostics construct PickerProfile positionally; the new fields default."""
    from Optimization.config.channels import PickerProfile
    from Warehouse.picking.Pick import PickConfig
    p = PickerProfile('anon', PickConfig(num_pickers=1), 1)
    assert (p.role, p.mode) == (Role.PICK, Mode.FOOT)


# ── the boundary that lets a future inbound package reuse this ────────────────────

@pytest.mark.parametrize('mod', [roles_mod, worker_mod])
def test_operations_imports_only_the_kernel(mod):
    """`architecture.yml` forbids wh_operations -> optimization / wh_picking / wh_inventory /
    wh_placement, so a Warehouse/inbound/ can build a crew of unloaders without inverting a
    dependency. This is the same claim, asserted at the source."""
    tree = ast.parse(inspect.getsource(mod))
    for node in ast.walk(tree):
        name = (node.names[0].name if isinstance(node, ast.Import)
                else (node.module or '') if isinstance(node, ast.ImportFrom) else None)
        if not name:
            continue
        parts = name.split('.')
        if parts[0] == 'Optimization':
            pytest.fail(f'{mod.__name__} imports the run harness ({name})')
        if parts[0] == 'Warehouse' and parts[1] not in ('kernel', 'operations'):
            pytest.fail(f'{mod.__name__} imports {name}; operations may use only wh_kernel')
