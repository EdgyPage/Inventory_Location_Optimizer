"""test_settings_module.py — one place a tunable is written down.

Values were declared in five places: `.env`, the nested `CONFIG` dict, the `simconfig`
registry, `whatif_config.SPECS`, and argparse. `num_pickers` lived in three of them,
`restocks` in three, the cart in two, the hours divisor in five modules, and the two that
decided whether runs were comparable at all had no flag.

`settings.py` is the authoring surface — a flat, annotated list of names — and `CONFIG` is
now a VIEW of it rather than a second declaration. The registry keeps the pick-config
coefficients, correctly: those are a swept axis, and a number that varies within one run is
not a default.

Run:  python -m pytest Tests/unit/test_settings_module.py -q
"""
from __future__ import annotations

import ast
import inspect

import pytest

from Optimization.config import settings, sim_config
from Optimization.config.sim_config import CONFIG


# ── CONFIG is a view, and every value comes from here ─────────────────────────────
#
# Asserted STRUCTURALLY, against the source, not against the live dict.  CONFIG is
# process-wide run state that a CLI flag, a what-if cell and several other tests all
# mutate by design, so `CONFIG[...] == settings.X` is only true until something overrides
# it -- an order-dependent test that passes alone and fails in a suite.  (Exactly the trap
# `test_pick_config_dicts_byte_identical` was already sitting in.)  The claim worth pinning
# is the DERIVATION: the literal names `_s.X` and nothing else.


def _config_sources():
    """{(section, key): the source expression CONFIG assigns it} for the literal."""
    tree = ast.parse(inspect.getsource(sim_config))
    node = next(n for n in tree.body
                if isinstance(n, ast.Assign)
                and any(getattr(t, 'id', '') == 'CONFIG' for t in n.targets))
    out = {}

    def walk(d, prefix):
        for k, v in zip(d.keys, d.values):
            key = getattr(k, 'value', None)
            if isinstance(v, ast.Dict):
                walk(v, prefix + (key,))
            else:
                out[prefix + (key,)] = ast.unparse(v)

    walk(node.value, ())
    return out


@pytest.mark.parametrize('name,path', [
    ('SEED_WORLD',        ('global', 'seed_world')),
    ('SEED_BATCHES',      ('global', 'seed_batches')),
    ('N_BATCHES',         ('global', 'n_batches')),
    ('WORKERS',           ('global', 'workers')),
    ('MAX_SKUS',          ('global', 'max_skus')),
    ('CHECKPOINT_FRAC',   ('global', 'checkpoint_frac')),
    ('KEYFRAME_INTERVAL', ('global', 'keyframe_interval')),
    ('SAMPLER',           ('global', 'sampler')),
    ('SHIFT_SECONDS',     ('global', 'shift_seconds')),
])
def test_a_global_setting_is_the_source_of_its_config_key(name, path):
    assert hasattr(settings, name), f'settings.{name} does not exist'
    assert _config_sources()[path] == f'_s.{name}'


@pytest.mark.parametrize('name,channel,key', [
    ('STORE_PICKERS',    'store',       'num_pickers'),
    ('FF_PICKERS',       'fulfillment', 'num_pickers'),
    ('STORE_CART',       'store',       'cart'),
    ('FF_CART',          'fulfillment', 'cart'),
    ('STORE_FILL',       'store',       'fill'),
    ('FF_FILL',          'fulfillment', 'fill'),
])
def test_a_channel_setting_is_the_source_of_its_config_key(name, channel, key):
    assert hasattr(settings, name)
    assert _config_sources()[('channels', channel, key)] == f'_s.{name}'


def test_the_batch_shape_comes_from_settings_on_both_channels():
    src = _config_sources()
    assert src[('channels', 'store', 'batch', 'mean')] == '_s.STORE_BATCH_MEAN'
    assert src[('channels', 'store', 'batch', 'std')] == '_s.STORE_BATCH_STD'
    assert src[('channels', 'fulfillment', 'batch', 'mean')] == '_s.FF_BATCH_MEAN'
    assert src[('channels', 'fulfillment', 'batch', 'std')] == '_s.FF_BATCH_STD'


def test_no_tunable_in_config_is_still_a_bare_literal():
    """The point of the move.  Only the structural keys -- a regime, the config LIST, a
    seed offset, a sizing shape -- may be anything other than a `_s.` reference or a
    nested dict."""
    allowed_non_settings = {
        ('channels', 'store', 'regime'), ('channels', 'fulfillment', 'regime'),
        ('channels', 'store', 'configs'), ('channels', 'fulfillment', 'configs'),
        ('channels', 'store', 'restocks'), ('channels', 'fulfillment', 'restocks'),
        ('channels', 'store', 'seed_offset'), ('channels', 'fulfillment', 'seed_offset'),
        ('channels', 'store', 'velocity_zoning'),
        ('channels', 'fulfillment', 'velocity_zoning'),
    }
    stragglers = []
    for path, expr in _config_sources().items():
        if path in allowed_non_settings or 'sizing' in path:
            continue
        if not expr.startswith('_s.'):
            stragglers.append(f'{".".join(map(str, path))} = {expr}')
    assert not stragglers, (
        'these CONFIG values are still declared inline rather than in settings.py: '
        + '; '.join(stragglers))


# ── the aliasing this could have introduced ───────────────────────────────────────

def test_the_two_channels_do_not_share_a_zoning_dict():
    """One `ZONING_OFF` literal now feeds both channels. A shallow copy would have made
    them share the nested `abc` dict, so editing one channel's thresholds would silently
    move the other's — the two used to be separate literals."""
    s = CONFIG['channels']['store']['velocity_zoning']
    f = CONFIG['channels']['fulfillment']['velocity_zoning']
    assert s == f
    assert s is not f
    assert s['abc'] is not f['abc']
    assert s['abc'] is not settings.ZONING_OFF['abc']


def test_editing_one_channels_zoning_leaves_the_other_alone():
    s = CONFIG['channels']['store']['velocity_zoning']
    f = CONFIG['channels']['fulfillment']['velocity_zoning']
    before = list(f['abc']['mass_thresholds'])
    s['abc']['mass_thresholds'].append(0.99)
    try:
        assert f['abc']['mass_thresholds'] == before
        assert settings.ZONING_OFF['abc']['mass_thresholds'] == before
    finally:
        s['abc']['mass_thresholds'].pop()


# ── it is a declaration, not logic ────────────────────────────────────────────────

def test_settings_declares_values_and_does_not_compute_them():
    """A settings file that computes is a settings file you have to run to read.  Imports
    and plain assignments only — no functions, classes, conditionals or loops."""
    tree = ast.parse(inspect.getsource(settings))
    for node in tree.body:
        assert isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign,
                                 ast.Expr)), (
            f'settings.py contains {type(node).__name__} at line {node.lineno}; it is a '
            f'variables list, not a module that computes')


def test_it_moves_the_run_trees_source_fingerprint():
    """Listed in SHAPE_SOURCES, or a config edit stops tripping preflight — the exact bug
    the run_whatif_volume note in contract.py records."""
    from Optimization.runschema.contract import SHAPE_SOURCES
    assert 'Optimization/config/settings.py' in SHAPE_SOURCES


# ── and the duplications it collapsed stay collapsed ──────────────────────────────

def test_the_picker_counts_have_one_source():
    """num_pickers lived in three places: constants, CONFIG, and each pick-config dict."""
    from Optimization.simconfig.constants import _FF_PICKERS, _STORE_PICKERS
    assert settings.STORE_PICKERS is _STORE_PICKERS
    assert settings.FF_PICKERS is _FF_PICKERS


def test_the_shift_length_comes_from_the_kernels_declaration():
    from Warehouse.kernel.timeline import DEFAULT_SHIFT_SECONDS
    assert settings.SHIFT_SECONDS == DEFAULT_SHIFT_SECONDS
    assert sim_config.shift_seconds() == settings.SHIFT_SECONDS


def test_pick_config_coefficients_are_NOT_here():
    """They are a swept AXIS, not a setting: every variant runs in one sweep, so they
    belong to the registry.  A number that varies within a run is not a default."""
    src = inspect.getsource(settings)
    for coef in ('pick_intercept', 'pick_weight_coef', 'cart_swap_coef', 'x_speed'):
        assert coef not in src, f'{coef} is a swept axis; it belongs in simconfig/configs/'


# ── the (role x mode) speed table ─────────────────────────────────────────────────

def test_the_put_crews_speed_comes_from_its_mode():
    """A crew labelled `foot` must not be costed at the store's machine speed. It was:
    the runner built the put crew with `speed=pick_cfg.speed`, so on a store run every
    put row said mode='foot' while its duration had been computed at 3/2 ft/s."""
    spec = sim_config.put_crew_spec()
    assert spec['mode'] == settings.PUT_CREW_MODE
    if spec['mode'] == 'foot':
        assert (spec['x_speed'], spec['y_speed']) == (settings.PUT_FOOT_X, settings.PUT_FOOT_Y)
    else:
        assert (spec['x_speed'], spec['y_speed']) == (settings.PUT_MACHINE_X,
                                                      settings.PUT_MACHINE_Y)


def test_switching_the_put_mode_switches_the_speed():
    import Optimization.config.settings as st
    was = st.PUT_CREW_MODE
    try:
        st.PUT_CREW_MODE = 'machine'
        s = sim_config.put_crew_spec()
        assert (s['mode'], s['x_speed'], s['y_speed']) == (
            'machine', st.PUT_MACHINE_X, st.PUT_MACHINE_Y)
    finally:
        st.PUT_CREW_MODE = was


def test_the_crew_size_setting_is_actually_read():
    """It was declared and unread — a setting nothing consumes is a lie about what is
    configurable."""
    assert sim_config.put_crew_spec()['size'] == settings.PUT_CREW_SIZE


def test_the_runner_builds_the_put_crew_from_the_payload_not_the_pick_config():
    import inspect
    import Optimization.simdriver.strategy_runner as sr
    src = inspect.getsource(sr)
    assert "args.get('put_crew')" in src
    assert 'mode=_Mode.FOOT, speed=pick_cfg.speed' not in src, (
        'the put crew is borrowing the pick crew\'s speed again')


def test_the_worker_payload_carries_the_put_crew():
    import inspect
    import Optimization.simdriver.workunits as wu
    assert 'put_crew            = put_crew_spec()' in inspect.getsource(wu)


def test_the_pick_half_of_the_table_stays_in_the_swept_registry():
    """Only the PUT speeds belong in settings. Duplicating the pick speeds here would
    re-create the very duplication this module removed."""
    src = inspect.getsource(settings)
    assert 'PUT_FOOT_X' in src and 'PUT_MACHINE_X' in src
    assert 'PICK_FOOT_X' not in src and 'PICK_MACHINE_X' not in src
