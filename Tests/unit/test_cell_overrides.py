"""test_cell_overrides.py — a cell changes the run, not the declaration.

`_apply_cell` writes three values per channel for each what-if cell.  Two are
channel-level and land on CONFIG; the third, `scheduler`, is a pick-config field and so
was written into every entry of `CONFIG['channels'][*]['configs']`.

Those entries used to BE the dicts held by `PICK_CONFIG_BY_KEY[…].cfg`.  A
`PickConfigSpec` is a frozen dataclass, which stops you rebinding `.cfg` and does nothing
whatever about mutating the dict it points at.  So running one cell permanently added a
`scheduler` key that no config module declares, carrying whichever value that cell chose,
for the rest of the process — and the next cell, or the next `--spec` in the same process,
inherited it as its starting point.

The fix is one copy, at the boundary where `sim_config` assembles CONFIG from the
registry.  These tests pin both halves: the registry stays pristine however many cells
run, and a cell's effect on CONFIG is still exactly what it was.

Run:  python -m pytest Tests/unit/test_cell_overrides.py -q
"""
from __future__ import annotations

import copy

import pytest

from Optimization.config.sim_config import CONFIG
from Optimization.simconfig import PICK_CONFIG_BY_KEY
from Optimization.simdriver.cells import Cell, _apply_cell, _build_cells


@pytest.fixture
def restore_inbound():
    """The fifth axis lands on CONFIG['global'], which `restore_config` does not cover."""
    from Optimization.config.sim_config import INBOUND_KEYS
    before = {k: CONFIG['global'][k] for k in INBOUND_KEYS}
    yield
    CONFIG['global'].update(before)


@pytest.fixture
def restore_config():
    """_apply_cell mutates the process-wide CONFIG by design; put it back."""
    saved = copy.deepcopy({ch: {'sizing': dict(CONFIG['channels'][ch]['sizing']),
                                'velocity_zoning': CONFIG['channels'][ch].get('velocity_zoning'),
                                'configs': [dict(c) for c in CONFIG['channels'][ch]['configs']]}
                           for ch in CONFIG['channels']})
    yield
    for ch, snap in saved.items():
        CONFIG['channels'][ch]['sizing']['aisle_split'] = snap['sizing'].get('aisle_split')
        CONFIG['channels'][ch]['velocity_zoning'] = snap['velocity_zoning']
        for live, orig in zip(CONFIG['channels'][ch]['configs'], snap['configs']):
            live.clear()
            live.update(orig)


# ── the registry is the declaration ───────────────────────────────────────────────

def test_the_registry_is_untouched_by_a_cell(restore_config):
    """The bug, directly: `scheduler` is declared by no config module, and after a cell ran
    it was in the frozen spec forever."""
    assert 'scheduler' not in PICK_CONFIG_BY_KEY['store'].cfg
    _apply_cell(None, {'enabled': False}, 'lpt')
    assert 'scheduler' not in PICK_CONFIG_BY_KEY['store'].cfg, (
        'a what-if cell wrote through CONFIG into the simconfig registry')


def test_two_cells_in_one_process_do_not_inherit_each_other(restore_config):
    """A frozen spec must read the same before and after an unrelated cell runs, or the
    second `--spec` in a process starts from the first one's leftovers."""
    before = dict(PICK_CONFIG_BY_KEY['store'].cfg)
    _apply_cell({'k': 3, 'capacity_loss': 0.1}, {'enabled': True}, 'lpt')
    _apply_cell(None, {'enabled': False}, 'round_robin')
    assert dict(PICK_CONFIG_BY_KEY['store'].cfg) == before


# ── ...and CONFIG is still the run state ──────────────────────────────────────────

def test_a_cell_still_reaches_every_channel(restore_config):
    _apply_cell({'k': 4, 'capacity_loss': 0.25}, {'enabled': True}, 'lpt')
    for ch in CONFIG['channels']:
        assert CONFIG['channels'][ch]['sizing']['aisle_split'] == {'k': 4, 'capacity_loss': 0.25}
        assert CONFIG['channels'][ch]['velocity_zoning'] == {'enabled': True}
        for cfg in CONFIG['channels'][ch]['configs']:
            assert cfg['scheduler'] == 'lpt'


def test_a_cell_writes_its_inbound_record_into_GLOBAL_config(restore_config, restore_inbound):
    """Global, not per-channel: the yard and the dock are the SITE's, which is exactly why they
    had to become a cell axis instead of six separate runs."""
    _apply_cell(None, {'enabled': False}, 'lpt',
                {'standing_yard': True, 'yard_policy': 'lifo'})
    assert CONFIG['global']['inbound_standing_yard'] is True
    assert CONFIG['global']['inbound_yard_policy'] == 'lifo'


def test_an_inert_inbound_record_writes_nothing_at_all(restore_config, restore_inbound):
    """None is the byte-identical default every pre-phase-2 spec resolves to. It must not
    write CONFIG's own value back over itself either — writing nothing is the guarantee."""
    _apply_cell(None, {'enabled': False}, 'lpt', {'standing_yard': True})
    _apply_cell(None, {'enabled': False}, 'round_robin', None)
    assert CONFIG['global']['inbound_standing_yard'] is True, (
        'an inert inbound record cleared a key it was never given'
    )


def test_the_zoning_spec_is_copied_per_channel(restore_config):
    """Two channels must not share one dict, or zoning edited for one silently edits both."""
    spec = {'enabled': True}
    _apply_cell(None, spec, 'round_robin')
    seen = [CONFIG['channels'][ch]['velocity_zoning'] for ch in CONFIG['channels']]
    assert all(z == {'enabled': True} for z in seen)
    assert all(z is not spec for z in seen)
    if len(seen) > 1:
        assert seen[0] is not seen[1]


# ── the record form ───────────────────────────────────────────────────────────────

def test_overrides_reports_what_apply_would_write():
    cell = Cell('k4_vel_lpt', {'k': 4, 'capacity_loss': 0.25}, {'enabled': True}, 'lpt')
    assert cell.overrides() == {'aisle_split': {'k': 4, 'capacity_loss': 0.25},
                                'velocity_zoning': {'enabled': True},
                                'scheduler': 'lpt',
                                'inbound': None}


def test_overrides_reports_the_inbound_record_too():
    """The fifth axis is global rather than per-channel, so it stays named as itself in the
    record — a caller diffing two cells must see the policy that separates them."""
    cell = Cell('k1_off_lifo', None, {'enabled': False}, 'lpt', {'yard_policy': 'lifo'})
    assert cell.overrides()['inbound'] == {'yard_policy': 'lifo'}


def test_overrides_hands_out_a_copy_of_the_inbound_record():
    cell = Cell('k1_off_lifo', None, {'enabled': False}, 'lpt', {'yard_policy': 'lifo'})
    cell.overrides()['inbound']['yard_policy'] = 'fifo'
    assert cell.inbound == {'yard_policy': 'lifo'}


def test_overrides_does_not_apply_anything():
    """The point of the record form: inspect a cell without moving the process."""
    before = dict(PICK_CONFIG_BY_KEY['store'].cfg)
    Cell('k1_off', None, {'enabled': False}, 'lpt').overrides()
    assert dict(PICK_CONFIG_BY_KEY['store'].cfg) == before


def test_overrides_hands_out_a_copy_of_the_zoning():
    """Otherwise a caller editing the record edits the cell."""
    cell = Cell('k1_off', None, {'enabled': False}, 'round_robin')
    got = cell.overrides()
    got['velocity_zoning']['enabled'] = True
    assert cell.zoning == {'enabled': False}


def test_every_built_cell_can_describe_itself():
    cells = _build_cells({'zoning': [('off', {'enabled': False}),
                                     ('vel', {'enabled': True})],
                          'ks': [1, 4], 'losses': [0.1],
                          'schedulers': ['round_robin', 'lpt']})
    assert len(cells) > 1
    for c in cells:
        ov = c.overrides()
        assert ov['scheduler'] == c.scheduler
        assert ov['aisle_split'] == c.split
        assert ov['velocity_zoning'] == c.zoning
