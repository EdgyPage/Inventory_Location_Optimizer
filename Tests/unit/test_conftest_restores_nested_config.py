"""test_conftest_restores_nested_config.py -- the autouse CONFIG restore reaches every level.

`Tests/conftest.py::_pristine_config` puts `CONFIG` back after every test.  Its first
version copied two levels -- `CONFIG['global']` and each channel block -- and
`Optimization.simdriver.cells._apply_cell` writes THREE: `channels.<ch>.sizing.aisle_split`
and `scheduler` on every pick-config dict under `channels.<ch>.configs`.  So a test that
applied a split cell left the split on every test after it, with the fixture reporting
nothing, because the nested dicts it "restored" were the same mutated objects.

The two tests below are ORDERED and prove it end to end: the first mutates the third level
the way `_apply_cell` does and asserts its own mutation took (non-vacuity -- a fixture that
forbade the write would pass a restore test for the wrong reason); the second asserts the
next test sees the pristine values AND the same nested dict objects, because
`run_simulation` holds `CONFIG['channels'][ch]['sizing']` by reference and a restore that
rebound it would break what it protects.  pytest runs a module's tests in file order.

Run:  python -m pytest Tests/unit/test_conftest_restores_nested_config.py -q
"""
from __future__ import annotations

from Optimization.config.sim_config import CONFIG
from Optimization.simdriver.cells import _apply_cell

_ids: dict = {}          # container identities recorded by the first test, read by the second


def _pick_configs():
    return [cfg for ch in CONFIG['channels'] for cfg in CONFIG['channels'][ch]['configs']]


def test_1_a_cell_applied_here_writes_the_third_level():
    for ch in CONFIG['channels']:
        assert CONFIG['channels'][ch]['sizing']['aisle_split'] is None, \
            'the pristine config carries no split -- this file must run on a clean CONFIG'
        _ids[ch] = (id(CONFIG['channels'][ch]['sizing']), id(CONFIG['channels'][ch]['configs']),
                    [id(cfg) for cfg in CONFIG['channels'][ch]['configs']])
    assert all('scheduler' not in cfg for cfg in _pick_configs()), \
        '`scheduler` is absent from every config module as written (cells._apply_cell)'
    _apply_cell({'k': 2, 'capacity_loss': 0.15}, {'enabled': False}, scheduler='lpt',
                inbound={'policy': 'lifo'})
    # the mutation TOOK -- the fixture restores, it does not forbid
    for ch in CONFIG['channels']:
        assert CONFIG['channels'][ch]['sizing']['aisle_split'] == {'k': 2, 'capacity_loss': 0.15}
    assert all(cfg['scheduler'] == 'lpt' for cfg in _pick_configs())
    assert CONFIG['global']['inbound_policy'] == 'lifo'
    CONFIG['channels']['store']['sizing']['added_by_test'] = 1     # a key the restore must drop


def test_2_the_next_test_sees_the_pristine_third_level_in_the_same_objects():
    assert _ids, 'test_1 did not run first -- this file relies on pytest file order'
    for ch in CONFIG['channels']:
        sizing = CONFIG['channels'][ch]['sizing']
        assert sizing['aisle_split'] is None, f'{ch}: the split leaked past the fixture'
        assert 'added_by_test' not in sizing
        sid, cid, cfg_ids = _ids[ch]
        assert id(sizing) == sid, f'{ch}: the restore rebound `sizing` instead of refilling it'
        assert id(CONFIG['channels'][ch]['configs']) == cid
        assert [id(cfg) for cfg in CONFIG['channels'][ch]['configs']] == cfg_ids
    assert all('scheduler' not in cfg for cfg in _pick_configs()), \
        'the scheduler written on every pick config leaked past the fixture'
    assert 'inbound_policy' not in CONFIG['global']
