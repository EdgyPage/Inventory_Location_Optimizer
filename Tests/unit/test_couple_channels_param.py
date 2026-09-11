"""test_couple_channels_param.py — the site-dock switch reaches a real run, through its seams.

`--couple-channels` decides whether a work unit drives ONE channel leaf or two.  A config knob
in this repo has five wirings and missing any one of them fails silently (memory:
`config-knob-has-five-seams`):

  1. declared in `settings.py`
  2. threaded into `CONFIG`, via an accessor read at CALL time
  3. a CLI flag
  4. recorded in `run_spec.json` AND restored on resume
  5. carried in `workunits._shared`, the picklable worker payload

The fifth is STRUCTURAL here, and that is worth pinning rather than leaving to be noticed: a
worker does not read a coupling flag at all.  Its payload either carries `leaves` or it does
not, and a leaf is a leaf because it is inside one -- so there is no value for a spawned
worker to silently default.  What the worker payload does carry is the consequence: the site
crews at unit scope and on neither leaf, which `Tests/e2e/test_coupled_unit_e2e.py` asserts
against a real prepared unit.

There is a sixth thing this knob owes, and it is why the resume clause is tested here: a run
that resumed WITHOUT it would rebuild per-channel units over a tree whose leaves were written
by coupled ones.

Run:  python -m pytest Tests/unit/test_couple_channels_param.py -q
"""
from __future__ import annotations

import inspect

import pytest

from Optimization.config import settings as _s
from Optimization.config.sim_config import CONFIG, couple_channels


@pytest.fixture()
def restore():
    """CONFIG is mutated in place and shared; put it back however the test exits."""
    before = CONFIG['global'].get('couple_channels')
    yield CONFIG['global']
    CONFIG['global']['couple_channels'] = before


# ── seam 1: declared ──────────────────────────────────────────────────────────────

def test_the_default_is_uncoupled():
    """Off by default, and it must always be: coupling changes how the site's crews are
    FIELDED, so it is a results boundary and can never be a silent default."""
    assert _s.COUPLE_CHANNELS is False


def test_settings_names_the_flag_beside_the_knob():
    assert '--couple-channels' in inspect.getsource(_s)


# ── seam 2: CONFIG, read at call time ─────────────────────────────────────────────

def test_the_accessor_reads_config_and_not_the_module(restore):
    """The trap `put_crew_spec` falls into. Reading `_s.COUPLE_CHANNELS` directly would make
    the CLI flag -- which writes CONFIG -- accepted and ignored forever."""
    restore['couple_channels'] = True
    assert couple_channels() is True
    restore['couple_channels'] = False
    assert couple_channels() is False
    assert '_s.' not in inspect.getsource(couple_channels)


def test_it_is_not_derived_from_the_inbound_flag(restore):
    """The obvious rule -- "coupling rides the inbound flag" -- is WRONG, and the wrongness is
    the kind that reads as correct: site-dock 06 couples every cell of the campaign including
    its inbound-OFF pole, so a run can be coupled with no trailers at all. The accessor must
    therefore mention no inbound key."""
    src = inspect.getsource(couple_channels)
    for name in ('inbound', 'trailer', 'INBOUND'):
        assert name not in src.split('"""')[-1], (
            f'the accessor derives coupling from {name!r}; it is DECLARED, and an '
            f'inbound-off coupled cell is exactly what the campaign runs')


# ── seam 3: the CLI ───────────────────────────────────────────────────────────────

def test_the_knob_has_a_flag():
    from Optimization import run_simulation
    assert "'--couple-channels'" in inspect.getsource(run_simulation), (
        'the switch is reachable only by editing settings.py, which is in SHAPE_SOURCES -- '
        'changing a VALUE there trips the run-tree preflight')


# ── seam 4: recorded, restored on resume, and stamped into the tree ───────────────

def test_recorded_in_the_run_spec_and_restored_on_resume():
    from Optimization import run_simulation
    src = inspect.getsource(run_simulation)
    assert src.count("'couple_channels'") >= 3, (
        'couple_channels must be ASSIGNED from args, WRITTEN to run_spec and RESTORED on '
        'resume; a run that resumed without it would rebuild per-channel units over a tree '
        'whose leaves were written by coupled ones')


def test_the_marker_reaches_the_run_tree():
    """`run_layout.json`'s `coupled` is what a downstream tool reads -- the run spec is not on
    anyone's path. It is written through the same predicate the unit builder uses, so the
    descriptor and the units cannot disagree about whether a run is a site."""
    from Optimization import run_simulation
    from Optimization.runschema import sim_manifest
    assert 'coupled=couple_channels()' in inspect.getsource(run_simulation)
    assert "'coupled'" in inspect.getsource(sim_manifest.write_run_layout)


def test_the_declared_shape_carries_the_field():
    """The descriptor's schema and the writer are checked against each other by
    `Tests/integration/test_run_layout.py`; this is the catalogue half, which nothing else
    verifies against the writer (site-dock 14's finding)."""
    import json
    import pathlib
    import yaml
    schema = json.load(open('Optimization/schemas/run_layout.schema.json', encoding='utf-8'))
    assert 'coupled' in schema['properties']
    cat = yaml.safe_load(pathlib.Path('context/artifacts.yml').read_text(encoding='utf-8'))
    fields = cat['artifacts']['run_layout']['fields']
    assert 'coupled' in fields, 'context/artifacts.yml does not list the field the writer emits'


# ── seam 5: the worker payload, which is STRUCTURAL ───────────────────────────────

def test_the_worker_reads_no_coupling_flag():
    """A spawned worker re-imports `sim_config` and gets pristine defaults, so a knob it had
    to READ would be the fifth-seam defect. There is nothing to read: the unit payload either
    carries `leaves` or it does not."""
    import ast

    from Optimization.simdriver import strategy_runner as sr
    body = ast.unparse(ast.parse(inspect.getsource(sr._run_strategy_worker_impl)))
    assert "args.get('leaves') is not None" in body, (
        'the driver no longer decides coupling from the payload SHAPE')
    assert 'couple_channels' not in body, (
        'the worker reads the coupling flag; a spawned worker would get the default and '
        'drive one leaf of a two-leaf unit')
    leaf = ast.unparse(ast.parse(inspect.getsource(sr._build_leaf)))
    assert 'couple_channels' not in leaf, 'a leaf reads the coupling flag'
