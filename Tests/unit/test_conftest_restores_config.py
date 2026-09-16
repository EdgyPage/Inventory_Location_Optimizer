"""test_conftest_restores_config.py — the autouse fixture that stops the suite poisoning itself.

`Optimization.config.sim_config.CONFIG` is a module-level dict mutated IN PLACE and shared by the
whole session. That is deliberate — `sim_config`'s docstring explains that the object is never
rebound, so every accessor reads the live value at call time and a CLI override is visible
everywhere. The cost is that a test which writes a key writes it for every test that follows, in
every later file and tier.

THE FAILURE THIS EXISTS FOR, measured rather than supposed. `run_analysis._apply_run_shape`
restores a run's shape onto the live CONFIG and writes `sampler` (falling back to `'v1'`),
`work_day_seconds` and `releases_per_day` (to `None` on a pre-field spec) unconditionally. Nothing
put CONFIG back, so one e2e test flipped the sampler and Noned two globals, and later unit tests
failed — one asserting `sampler == 'v3'`, and six that could not build the argparse parser at all
(`TypeError: unsupported format string passed to NoneType.__format__`).

Every tier passed when run on its own; only the full routine suite failed, and telling the
attributable failures from the pre-existing ones needed a `git archive` control tree.
`Tests/calltree/calltree_inbound_ladder.py` had already recorded the same incident as its reason
for shelling out per rung: "a subprocess is the cheap, total fix — the OS restores the global
state for free". The conftest fixture is that fix, in-process.

THESE TESTS RELY ON ORDER, which is unusual and is the point: the first mutates, the second checks
the mutation is gone. pytest runs tests within a file in definition order, so the pair is a real
before/after. If the fixture is removed, the second test fails — which is the whole contract.

Run:  python -m pytest Tests/unit/test_conftest_restores_config.py -q
"""
from __future__ import annotations

import logging
import json
import os

import Optimization.config.sim_config as sc


#: What the era declares, read ONCE at import — before any test in this file has run, so it is
#: the pristine value rather than whatever a predecessor left behind.
_PRISTINE_SAMPLER = sc.CONFIG['global']['sampler']


def test_a_test_may_still_mutate_config_freely():
    """The fixture RESTORES, it does not FORBID. A test must still see its own writes — half the
    suite sets a CONFIG key deliberately and asserts on the result."""
    sc.CONFIG['global']['sampler'] = 'v1'
    sc.CONFIG['global']['n_batches'] = 99_999
    assert sc.CONFIG['global']['sampler'] == 'v1'
    assert sc.n_batches() == 99_999, 'an accessor must read the live value at call time'


def test_the_next_test_does_not_inherit_it():
    """THE contract. Without the autouse fixture this reads `'v1'` and `99_999` — exactly how one
    e2e test broke a unit test twenty minutes later."""
    assert sc.CONFIG['global']['sampler'] == _PRISTINE_SAMPLER, (
        f"sampler is {sc.CONFIG['global']['sampler']!r}, not the pristine "
        f'{_PRISTINE_SAMPLER!r} — the previous test leaked into this one, so the conftest '
        f'fixture is gone or no longer restores CONFIG')
    assert sc.CONFIG['global']['n_batches'] != 99_999, 'n_batches leaked too'


def test_the_real_leak_is_contained(tmp_path):
    """The production path that caused it, driven directly.

    `_apply_run_shape` on a PRE-FIELD spec (one carrying none of the newer keys) is the exact
    shape that flipped the sampler: its own comment says an absent field means that run's value,
    "never this checkout's default" — correct for the function, lethal for the session.
    """
    import Optimization.run_analysis as ra

    (tmp_path / 'run_spec.json').write_text(json.dumps({'n_batches': 3}), encoding='utf-8')
    try:
        ra._apply_run_shape(str(tmp_path), logging.getLogger('t'))
    except Exception:                      # noqa: BLE001 — the mutation is what matters here
        pass

    assert sc.CONFIG['global']['sampler'] == 'v1', (
        'premise: _apply_run_shape still flips the sampler on a pre-field spec. If this fails '
        'the leak was fixed at the source and this file should be re-pointed, not deleted.')


def test_and_the_leak_did_not_escape_the_test_above():
    """The other half of the pair, on the REAL mutation rather than a hand-written one."""
    assert sc.CONFIG['global']['sampler'] == _PRISTINE_SAMPLER, (
        '_apply_run_shape leaked out of its test — the fixture does not cover the keys that '
        'production code writes, which are the only ones that ever caused this')
    # `work_day_seconds` is legitimately None by default, so its VALUE proves nothing here.
    # What the leak did was write it unconditionally; the sampler above is the observable
    # half, and the parser test below is the symptom the None actually produced.


def test_the_parser_still_builds_which_is_what_actually_broke():
    """Six tests did not merely fail, they ERRORED in setup: `_build_parser()` raised because a
    help f-string formatted a `None` that `_apply_run_shape` had written. That is the symptom a
    reader meets first, so it is pinned separately from the value it comes from."""
    import Optimization.run_simulation as rs

    rs._build_parser()          # must not raise
