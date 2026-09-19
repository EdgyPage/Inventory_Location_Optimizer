"""test_cell_scope.py -- a cell is applied for a `with` block and undone on exit.

`cells.cell_scope` is the one way the driver applies a what-if cell since the flat work pool
(2026-09-19): every cell's setup runs inside its scope while other cells' units are in
flight, so a cell's CONFIG writes must not outlive its own setup.  The tests pin:

  - NON-VACUITY: inside the scope all four axes are applied (the `_apply_cell` primitive is
    unchanged -- `test_cell_overrides.py` still pins it positionally).
  - RESTORE: on exit every value is back, INCLUDING a key that was absent before (`scheduler`
    is declared by no config module and must be absent again, not None), and the container
    objects `run_simulation` holds by reference are the same objects.
  - REBINDING, NOT REFILLING: a dict captured from CONFIG inside the scope is unchanged after
    exit -- the pool pickles a payload hours after its cell's setup, so a restore that
    refilled the live dict would rewrite the payload before it shipped.
  - NO INHERITANCE: an `inbound=None` cell after an inbound cell sees the run-level keys.
  - Nested scopes compose; an exception inside still restores.

Run:  python -m pytest Tests/unit/test_cell_scope.py -q
"""
from __future__ import annotations

import pytest

from Optimization.config.sim_config import CONFIG
from Optimization.simdriver.cells import Cell, _inbound_axis, cell_scope

_SPLIT = {'k': 3, 'capacity_loss': 0.1}
_INB = Cell('k1_off_lifo', None, {'enabled': False}, 'lpt',
            {'standing_yard': True, 'yard_policy': 'lifo'})


def _pick_configs():
    return [cfg for ch in CONFIG['channels'] for cfg in CONFIG['channels'][ch]['configs']]


def _snapshot():
    return {ch: (CONFIG['channels'][ch]['sizing'].get('aisle_split'),
                 CONFIG['channels'][ch].get('velocity_zoning'),
                 [cfg.get('scheduler', '<absent>') for cfg in CONFIG['channels'][ch]['configs']])
            for ch in CONFIG['channels']}, \
           {k: CONFIG['global'].get(k, '<absent>')
            for k in ('inbound_standing_yard', 'inbound_yard_policy')}


def test_inside_the_scope_every_axis_is_applied():
    with cell_scope(Cell('k3_l10_vel_lpt', _SPLIT, {'enabled': True, 'mode': 'x'}, 'lpt',
                         {'yard_policy': 'lifo'})):
        for ch in CONFIG['channels']:
            assert CONFIG['channels'][ch]['sizing']['aisle_split'] == _SPLIT
            assert CONFIG['channels'][ch]['velocity_zoning'] == {'enabled': True, 'mode': 'x'}
        assert all(cfg['scheduler'] == 'lpt' for cfg in _pick_configs())
        assert CONFIG['global']['inbound_yard_policy'] == 'lifo'


def test_exit_restores_every_value_and_every_container():
    before = _snapshot()
    ids = {ch: (id(CONFIG['channels'][ch]['sizing']), id(CONFIG['channels'][ch]['configs']),
                [id(cfg) for cfg in CONFIG['channels'][ch]['configs']])
           for ch in CONFIG['channels']}
    assert all('scheduler' not in cfg for cfg in _pick_configs()), \
        '`scheduler` is absent from every config module as written -- run on a clean CONFIG'
    with cell_scope(Cell('k3_l10_vel_lpt', _SPLIT, {'enabled': True}, 'lpt',
                         {'standing_yard': True, 'yard_policy': 'lifo'})):
        assert CONFIG['channels']['store']['sizing']['aisle_split'] == _SPLIT   # took
    assert _snapshot() == before
    assert all('scheduler' not in cfg for cfg in _pick_configs()), \
        'an absent key must be absent again on exit, not set to None'
    for ch in CONFIG['channels']:
        sid, cid, cfg_ids = ids[ch]
        assert id(CONFIG['channels'][ch]['sizing']) == sid, f'{ch}: `sizing` was rebound'
        assert id(CONFIG['channels'][ch]['configs']) == cid
        assert [id(cfg) for cfg in CONFIG['channels'][ch]['configs']] == cfg_ids


def test_a_dict_captured_inside_the_scope_is_untouched_by_the_exit():
    """The pool's guarantee: what a payload took from CONFIG at build time is what it ships."""
    with cell_scope(Cell('k1_vel', None, {'enabled': True, 'bands': 3}, 'round_robin')):
        captured = CONFIG['channels']['store']['velocity_zoning']       # the live object
        split_seen = CONFIG['channels']['store']['sizing']['aisle_split']
    assert captured == {'enabled': True, 'bands': 3}, 'the exit refilled the cell\'s dict in place'
    assert CONFIG['channels']['store']['velocity_zoning'] is not captured
    assert split_seen is None


def test_an_inert_cell_after_an_inbound_cell_sees_the_run_level_keys():
    run_level = CONFIG['global'].get('inbound_yard_policy', '<absent>')
    with cell_scope(_INB):
        assert CONFIG['global']['inbound_yard_policy'] == 'lifo'
    with cell_scope(Cell('k1_off', None, {'enabled': False}, 'round_robin', None)):
        assert CONFIG['global'].get('inbound_yard_policy', '<absent>') == run_level, \
            'the inert cell inherited the previous cell\'s inbound policy'


def test_scopes_nest_and_restore_in_order():
    before = _snapshot()
    with cell_scope(Cell('outer', _SPLIT, {'enabled': False}, 'lpt', {'yard_policy': 'lifo'})):
        with cell_scope(Cell('inner', None, {'enabled': True}, 'round_robin', {'yard_policy': 'fifo'})):
            assert CONFIG['global']['inbound_yard_policy'] == 'fifo'
            assert CONFIG['channels']['store']['sizing']['aisle_split'] is None
        assert CONFIG['global']['inbound_yard_policy'] == 'lifo'
        assert CONFIG['channels']['store']['sizing']['aisle_split'] == _SPLIT
        assert all(cfg['scheduler'] == 'lpt' for cfg in _pick_configs())
    assert _snapshot() == before


def test_an_exception_inside_the_scope_still_restores():
    before = _snapshot()
    with pytest.raises(RuntimeError):
        with cell_scope(_INB):
            raise RuntimeError('setup failed')
    assert _snapshot() == before


def test_a_partial_inbound_entry_is_no_longer_refused():
    """The union rule guarded against one cell inheriting another's keys through the
    never-reset CONFIG; the scope makes an omitted key the run-level value, so the rule
    -- and its refusal -- are gone.  The other refusals stand."""
    axis = _inbound_axis({'inbound': [('a', {'standing_yard': True, 'yard_policy': 'lifo'}),
                                      ('b', {'standing_yard': False})]})
    assert [n for n, _ in axis] == ['a', 'b']
    with pytest.raises(ValueError, match='unknown key'):
        _inbound_axis({'inbound': [('a', {'no_such_key': 1})]})
    with pytest.raises(ValueError, match='duplicate'):
        _inbound_axis({'inbound': [('a', {'standing_yard': True}), ('a', {'standing_yard': False})]})
