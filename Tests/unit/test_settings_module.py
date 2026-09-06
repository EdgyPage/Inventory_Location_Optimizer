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
    ('REPORTING_FRAME_SECONDS', ('global', 'shift_seconds')),
    # The declared crews: two flat GLOBAL keys, never a per-channel entry (see
    # test_staffing_params for the seams that follow from that).
    ('STORE_PICKERS',    ('global', 'store_pickers')),
    ('FF_PICKERS',       ('global', 'ff_pickers')),
])
def test_a_global_setting_is_the_source_of_its_config_key(name, path):
    assert hasattr(settings, name), f'settings.{name} does not exist'
    assert _config_sources()[path] == f'_s.{name}'


@pytest.mark.parametrize('name,channel,key', [
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
    assert settings.REPORTING_FRAME_SECONDS == DEFAULT_SHIFT_SECONDS
    assert sim_config.shift_seconds() == settings.REPORTING_FRAME_SECONDS


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
    """The mode is read from CONFIG at call time (the `put_crew_spec` trap is closed): a
    flag or a restore writes `put_crew_mode`, and the speed table follows it."""
    import Optimization.config.settings as st
    g = sim_config.CONFIG['global']
    was = g.get('put_crew_mode')
    try:
        g['put_crew_mode'] = 'machine'
        s = sim_config.put_crew_spec()
        assert (s['mode'], s['x_speed'], s['y_speed']) == (
            'machine', st.PUT_MACHINE_X, st.PUT_MACHINE_Y)
    finally:
        g['put_crew_mode'] = was


def test_the_crew_size_setting_is_actually_read():
    """It was declared and unread — a setting nothing consumes is a lie about what is
    configurable.  It is now the DEFAULT of the `put_crew_size` CONFIG key, which is what
    the accessor reads (and what `--put-crew-size` writes)."""
    import inspect
    assert "'put_crew_size'       : _s.PUT_CREW_SIZE," in inspect.getsource(sim_config), (
        'the CONFIG key is not seeded from the setting')
    g = sim_config.CONFIG['global']
    was = g.get('put_crew_size')          # another test's restore may have left None here
    try:
        g['put_crew_size'] = settings.PUT_CREW_SIZE
        assert sim_config.put_crew_spec()['size'] == settings.PUT_CREW_SIZE
    finally:
        g['put_crew_size'] = was


def test_the_put_crew_accessor_reads_config_not_the_module():
    """THE trap every other accessor's docstring warned about, now closed here too: with no
    CONFIG key a CLI flag writing CONFIG was accepted and ignored forever, and a standalone
    re-analysis sized against this checkout's settings instead of the run's own."""
    g = sim_config.CONFIG['global']
    was = g.get('put_crew_size')
    try:
        g['put_crew_size'] = 3
        assert sim_config.put_crew_spec()['size'] == 3
        assert sim_config.put_crew_spec(size=7)['size'] == 7, 'the derived size wins'
    finally:
        g['put_crew_size'] = was


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
    assert 'put_crew            = put_crew_spec(size=_put_size)' in inspect.getsource(wu)


# -- put_queues_spec: the third sibling, which had no test at all ------------------

def test_the_split_is_off_by_default_and_structurally_so():
    """`None` means "the single default queue", so the no-op is the ABSENCE of a spec rather
    than a spec that happens to describe one queue. A future edit that returned a
    one-queue dict here would be a silent behaviour change with every test still green."""
    assert settings.PUT_QUEUE_SPLIT is False
    assert sim_config.put_queues_spec() is None


def test_the_split_spec_is_read_at_call_time_not_import_time():
    """The seam CONFIG contract: an accessor that snapshotted at import would ignore the CLI
    flag, the run_spec restore and the worker payload all at once — and do it silently."""
    g = sim_config.CONFIG['global']
    was = g.get('put_queue_split')
    try:
        g['put_queue_split'] = True
        spec = sim_config.put_queues_spec()
        assert spec is not None, (
            'CONFIG says the split is on and the accessor still returns None — it is not '
            'reading CONFIG at call time')
    finally:
        g['put_queue_split'] = was
    assert sim_config.put_queues_spec() is None, 'the accessor did not go back'


def test_the_worker_payload_carries_the_queue_split():
    """THE fifth seam, and the one CLAUDE.md records as the one that gets skipped: the pool
    is SPAWN, so a worker re-imports config and gets pristine defaults unless the resolved
    value travels in `_shared`. Skipping it reverts the knob to its default in every worker
    with nothing raising."""
    import inspect
    import Optimization.simdriver.workunits as wu
    assert 'put_queues_spec()' in inspect.getsource(wu), (
        'the split never reaches the worker payload, so a spawned worker will silently run '
        'the single default queue however the run was configured')


def test_the_pick_half_of_the_table_stays_in_the_swept_registry():
    """Only the PUT speeds belong in settings. Duplicating the pick speeds here would
    re-create the very duplication this module removed."""
    src = inspect.getsource(settings)
    assert 'PUT_FOOT_X' in src and 'PUT_MACHINE_X' in src
    assert 'PICK_FOOT_X' not in src and 'PICK_MACHINE_X' not in src
