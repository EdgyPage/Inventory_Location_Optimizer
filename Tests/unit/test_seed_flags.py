"""test_seed_flags.py — the two knobs that decide whether two runs are comparable.

`--workers` changes no result and has always been a flag.  `seed_world` (the warehouse and
catalogue) and `seed_batches` (the demand stream) decide whether two runs can be compared
at all, and had none: you edited `sim_config` and hoped you remembered to put it back.

A flag is not plumbed until it reaches four places, and missing any one of them is silent:

  1. the parser                       — or it is a typo
  2. CONFIG                           — or nothing reads it
  3. `run_spec.json`                  — or the run does not record what it was
  4. `_apply_run_spec` / `_apply_run_shape`
                                      — or a resume, and a standalone re-analysis, quietly
                                        substitute this checkout's values

Place 4 is the one with teeth for `seed_world`: `sim_assets` seeds the warehouse and
catalogue build from it, so re-analysing a run with a different world seed rebuilds a
DIFFERENT warehouse than the sim ran on. That is the wrong-warehouse bug `_apply_run_shape`
was written to close, arriving through a new door.

Run:  python -m pytest Tests/unit/test_seed_flags.py -q
"""
from __future__ import annotations

import inspect
import logging
import pathlib

import pytest

from Optimization.config import sim_config
from Optimization.config.sim_config import CONFIG

SEEDS = ('seed_world', 'seed_batches')
_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def restore_global():
    saved = dict(CONFIG['global'])
    yield
    CONFIG['global'].clear()
    CONFIG['global'].update(saved)


# ── 1. the parser ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('flag', ['--seed-world', '--seed-batches'])
def test_the_flag_is_registered(flag):
    import Optimization.run_simulation as rs
    assert f"'{flag}'" in inspect.getsource(rs), f'{flag} is not on the parser'


def test_the_parser_advertises_both_flags():
    """Through `--help`, because the parser is built inline inside `main()` and there is no
    factory to call.  Extracting one is a real refactor of a 200-line function and does not
    belong in the commit that adds two flags."""
    import subprocess
    import sys
    out = subprocess.run([sys.executable, '-m', 'Optimization.run_simulation', '--help'],
                         capture_output=True, text=True, timeout=180,
                         cwd=str(_ROOT)).stdout
    assert '--seed-world' in out and '--seed-batches' in out
    assert 'NOT comparable' in out, 'the help must say what the world seed costs you'


@pytest.mark.parametrize('dest', SEEDS)
def test_the_flag_defaults_to_none_not_to_the_config_value(dest):
    """`default=None` is what lets `_apply_run_spec` tell "user typed it" from "user did
    not", so an unset flag on a resume does not overwrite the recorded value."""
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    flag = '--' + dest.replace('_', '-')
    i = src.index(f"'{flag}'")
    decl = src[i:i + 200]
    assert 'default=None' in decl, f'{flag} must default to None, not to a CONFIG value'


# ── 2/3/4. plumbed all the way through ────────────────────────────────────────────

@pytest.mark.parametrize('key', SEEDS)
def test_the_seed_is_written_to_run_spec_and_restored_on_resume(key):
    import Optimization.run_simulation as rs
    src = inspect.getsource(rs)
    assert src.count(f"'{key}'") >= 2, (
        f"'{key}' must be written to run_spec AND listed in _apply_run_spec's whitelist")


def test_a_standalone_reanalysis_restores_both_seeds(tmp_path):
    """The one that rebuilds a warehouse.  Without this, re-analysis sizes and seeds from
    whatever this checkout holds."""
    from Optimization.run_analysis import _apply_run_shape
    from Optimization.runschema.sim_manifest import _write_run_spec
    _write_run_spec(str(tmp_path), {'n_batches': 3, 'seed_world': 777, 'seed_batches': 888})
    saved = {k: CONFIG['global'].get(k) for k in ('n_batches', 'sampler', *SEEDS)}
    try:
        _apply_run_shape(str(tmp_path), logging.getLogger('t'))
        assert CONFIG['global']['seed_world'] == 777
        assert CONFIG['global']['seed_batches'] == 888
    finally:
        CONFIG['global'].update(saved)


def test_a_spec_without_the_seeds_leaves_them_untouched(tmp_path):
    """Every run_spec.json written before this commit. Absence must not mean zero."""
    from Optimization.run_analysis import _apply_run_shape
    from Optimization.runschema.sim_manifest import _write_run_spec
    _write_run_spec(str(tmp_path), {'n_batches': 3})
    before = {k: CONFIG['global'][k] for k in SEEDS}
    saved = {k: CONFIG['global'].get(k) for k in ('n_batches', 'sampler')}
    try:
        _apply_run_shape(str(tmp_path), logging.getLogger('t'))
        assert {k: CONFIG['global'][k] for k in SEEDS} == before
    finally:
        CONFIG['global'].update(saved)


# ── and the value actually lands where the run reads it ───────────────────────────

def test_the_override_reaches_the_accessors(restore_global):
    CONFIG['global']['seed_world'] = 31337
    CONFIG['global']['seed_batches'] = 4242
    assert sim_config.seed_world() == 31337
    assert sim_config.seed_batches() == 4242


def test_the_channel_offset_rides_on_the_overridden_base(restore_global):
    """workunits computes each channel's stream as base + offset, so an override must move
    BOTH channels and keep them independent of each other."""
    from Optimization.config.channels import FF_BATCH_SEED_OFFSET
    CONFIG['global']['seed_batches'] = 500
    base = sim_config.seed_batches()
    assert base == 500
    assert base + FF_BATCH_SEED_OFFSET != base, 'the ff offset must keep the streams distinct'
