"""test_run_shaping_accessors.py — a tunable is read at call time, never snapshotted.

`sim_config`'s own docstring promises CONFIG is the single source of truth and is mutated
in place, never rebound.  Six module-level scalars broke that promise by capturing CONFIG's
values once, at import:

    _INITIAL_FILL  SEED_WORLD  SEED_BATCHES  N_BATCHES  K_PICKERS  STORE_RESTOCKS

`_INITIAL_FILL` was fixed first, and it is the proof the class is real: `sim_assets` writes
that value into warehouse.db as `target_fill`, so a `--store-fill` override left the run's
own provenance recording the stale number, silently.

The other five had the identical defect and had simply not been caught yet, because
`--n-batches` is the only flag among them and nothing on the run path read `N_BATCHES`.
That is not safety, it is luck: `seed_world` and `seed_batches` decide whether two runs are
comparable at all, and `workunits` derives every channel's batch seed from the base — so
the first CLI flag for either would have been accepted, echoed, recorded in run_spec, and
then ignored by the workers.

Run:  python -m pytest Tests/unit/test_run_shaping_accessors.py -q
"""
from __future__ import annotations

import inspect

import pytest

from Optimization.config import sim_config
from Optimization.config.sim_config import CONFIG


ACCESSORS = ('seed_world', 'seed_batches', 'n_batches', 'k_pickers', 'store_restocks',
             'store_fill', 'ff_fill')

RETIRED = ('_INITIAL_FILL', 'SEED_WORLD', 'SEED_BATCHES', 'N_BATCHES', 'K_PICKERS',
           'STORE_RESTOCKS')


@pytest.fixture
def restore_global():
    saved = dict(CONFIG['global'])
    saved_ch = {ch: dict(CONFIG['channels'][ch]) for ch in CONFIG['channels']}
    yield
    CONFIG['global'].clear()
    CONFIG['global'].update(saved)
    for ch, snap in saved_ch.items():
        CONFIG['channels'][ch].clear()
        CONFIG['channels'][ch].update(snap)


# ── none of them may come back as a scalar ────────────────────────────────────────

@pytest.mark.parametrize('name', RETIRED)
def test_the_import_time_snapshot_is_gone(name):
    assert not hasattr(sim_config, name), (
        f'{name} is a module-level snapshot again; a CLI override written into CONFIG '
        f'will not reach it')


@pytest.mark.parametrize('name', ACCESSORS)
def test_every_run_shaping_value_is_callable(name):
    assert callable(getattr(sim_config, name))


# ── ...and each one actually re-reads ─────────────────────────────────────────────

@pytest.mark.parametrize('name,path,probe', [
    ('seed_world',     ('global', 'seed_world'),   123456),
    ('seed_batches',   ('global', 'seed_batches'), 654321),
    ('n_batches',      ('global', 'n_batches'),    7),
])
def test_a_global_override_reaches_the_accessor(restore_global, name, path, probe):
    CONFIG[path[0]][path[1]] = probe
    assert getattr(sim_config, name)() == probe


def test_a_channel_override_reaches_the_accessor(restore_global):
    CONFIG['channels']['store']['num_pickers'] = 99
    assert sim_config.k_pickers() == 99


# ── the consumer that would have been bitten ──────────────────────────────────────

def test_the_worker_payload_derives_its_seed_at_call_time(restore_global):
    """`workunits` computes each channel's stream as base + offset.  With an import-time base
    a `--seed-batches` flag would be recorded in run_spec and then ignored by the run."""
    import Optimization.simdriver.workunits as wu
    src = inspect.getsource(wu)
    assert 'seed_batches()' in src, 'workunits snapshotted the base batch seed again'
    assert 'SEED_BATCHES' not in src
    assert 'SEED_WORLD' not in src

    CONFIG['global']['seed_batches'] = 4242
    assert sim_config.seed_batches() == 4242


def test_sim_assets_seeds_the_world_at_call_time(restore_global):
    """This is the module that writes provenance into warehouse.db — the same place the
    _INITIAL_FILL bug surfaced."""
    import Optimization.simdriver.sim_assets as sa
    src = inspect.getsource(sa)
    assert 'seed_world()' in src
    assert 'SEED_WORLD' not in src


def test_run_simulation_reexports_the_accessors_not_scalars():
    """Diagnostics and tests reach these through `rs.`; the re-export must not
    reintroduce a snapshot at ITS import time either.

    Only the names run_simulation actually re-exports — `ff_fill` has never had an `rs.`
    consumer and is deliberately not added here just to make a loop symmetric.
    """
    import Optimization.run_simulation as rs
    for name in ('seed_world', 'seed_batches', 'n_batches', 'k_pickers',
                 'store_restocks', 'store_fill'):
        assert callable(getattr(rs, name)), f'rs.{name} is not callable'
    for name in RETIRED:
        assert not hasattr(rs, name), f'rs.{name} came back'
