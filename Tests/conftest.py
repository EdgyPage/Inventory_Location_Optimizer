"""Tests/conftest.py — single sys.path bootstrap for the whole suite.

Replaces the per-file ``sys.path.insert(...)`` boilerplate that every test used
to carry.  Project imports are package-absolute: ``from Warehouse.catalog.Order import
Order``, ``from Optimization import channels``.  Tests/ itself stays a
NON-package (no __init__.py): pytest's default prepend import mode puts each
test file's own directory on sys.path, which is what keeps helper imports
between sibling tests (``from test_fulfillment_channels import _mixed_inventory``)
and the bare sibling imports inside Tests/gpu/ working with zero per-file setup.
"""
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Tests/bench holds shared scenario builders (perf_simulation._build_inventory etc.)
# that tests import by bare name (test_index_equivalence).  bench_* files are not
# collected (not test_*), so this adds helpers only — no extra tests.
_BENCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'bench')
if _BENCH not in sys.path:
    sys.path.insert(0, _BENCH)

import pytest  # noqa: E402  -- after the sys.path bootstrap above, deliberately


@pytest.fixture(autouse=True)
def _pristine_config():
    """Put `CONFIG` back after every test.

    `Optimization.config.sim_config.CONFIG` is a module-level dict MUTATED IN PLACE and
    shared by the whole session -- that is deliberate, and `sim_config`'s own docstring
    explains why (the object is never rebound, so every accessor reads the live value at
    call time and a CLI override is visible everywhere).  The cost is that a test which
    writes a key writes it for every test that follows, in every later FILE and TIER.

    THE FAILURE THIS FIXES, measured rather than supposed.  `run_analysis._apply_run_shape`
    restores a run's shape onto the live CONFIG and writes three keys unconditionally --
    `sampler` (falling back to 'v1'), `work_day_seconds` and `releases_per_day` (to None
    when the spec predates them).  Each line is correct for its purpose and says so in its
    own comment; nothing put CONFIG back.  So one e2e test flipped the sampler and Noned
    two globals, and twenty minutes later a unit test asserting `sampler == 'v3'` failed,
    along with six that could not even build the argparse parser
    (`TypeError: unsupported format string passed to NoneType.__format__`).

    Every tier passed when run on its own.  The full routine suite reported 13 failures,
    six of which were this, and telling them apart needed a `git archive` control tree.
    `Tests/calltree/calltree_inbound_ladder.py`'s docstring had already recorded the same
    incident -- naming the function, the key and the twelve-minute delay -- as the reason
    that CLI shells out to a subprocess per rung: *"a subprocess is the cheap, total fix
    -- the OS restores the global state for free."*  This is that fix, in-process.

    WHY AUTOUSE RATHER THAN PER-FILE.  `Tests/unit/test_config_reaches_the_worker.py`
    already carries exactly this fixture, correct, for one file.  The leak is not caused by
    the tests that know they mutate CONFIG -- those unwind -- but by production code called
    from a test that had no reason to think it was mutating anything.  A per-file opt-in
    cannot cover that, because the file that needs it is the one nobody suspects.

    IT RESTORES, IT DOES NOT FORBID.  A test may still mutate CONFIG freely and see its own
    changes; only the NEXT test is protected.  A test that depends on a predecessor's
    mutation will start failing -- that is a finding, not a regression.

    EVERY LEVEL, NOT TWO.  The first version of this fixture copied `CONFIG['global']` and
    each channel block one level deep, and `cells._apply_cell` writes THREE levels down:
    `channels.<ch>.sizing.aisle_split` and `scheduler` on every pick-config dict in
    `channels.<ch>.configs`.  A test that applied a split cell left the split on every test
    after it, and the two-level restore put the same nested dict objects back untouched.
    So the snapshot is recursive over dicts and lists (`_snapshot_containers`) and the
    restore walks the SAME containers in place (`_restore_containers`): a nested dict or
    list that existed before the test keeps its identity, its contents are put back, keys
    the test added are removed.  Leaves that are neither dict nor list -- regimes, tuples,
    strings, numbers -- are shared by reference, not copied: `regime` objects are compared
    by identity in places, and a `copy.deepcopy` would have handed the next test a stranger.
    `Tests/unit/test_conftest_restores_nested_config.py` is the ordered pair that proves it.

    Cheap by construction: a walk over a few dozen dicts per test, taken only if `sim_config`
    has already been imported.  A test that never touches the run harness pays one failed
    lookup in `sys.modules`.
    """
    sc = sys.modules.get('Optimization.config.sim_config')
    if sc is None:
        yield                      # nothing imported it; nothing to protect
        return
    cfg = sc.CONFIG
    before = _snapshot_containers(cfg)
    try:
        yield
    finally:
        # in place rather than rebinding: the whole design rests on CONFIG being the SAME
        # object everywhere, so a fixture that replaced it -- or replaced the nested dicts
        # `run_simulation` and the cell applier hold by reference -- would break exactly
        # what it is protecting.
        _restore_containers(cfg, before)


def _snapshot_containers(obj):
    """A copy of `obj` down every dict and list; anything else is the same object."""
    if isinstance(obj, dict):
        return {k: _snapshot_containers(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_snapshot_containers(v) for v in obj]
    return obj


def _same_kind(a, b):
    return (isinstance(a, dict) and isinstance(b, dict)) or \
           (isinstance(a, list) and isinstance(b, list))


def _restore_containers(live, saved):
    """Put `saved` (a `_snapshot_containers` result) back INTO `live`, keeping every dict and
    list that both sides hold at the same position as the same object."""
    if isinstance(live, dict) and isinstance(saved, dict):
        for k in [k for k in live if k not in saved]:
            del live[k]
        for k, sv in saved.items():
            lv = live.get(k)
            if _same_kind(lv, sv):
                _restore_containers(lv, sv)
            else:
                live[k] = sv
        return
    if isinstance(live, list) and isinstance(saved, list):
        if len(live) != len(saved):
            live[:] = saved
            return
        for i, sv in enumerate(saved):
            lv = live[i]
            if _same_kind(lv, sv):
                _restore_containers(lv, sv)
            else:
                live[i] = sv
        return
    raise TypeError(f'container mismatch: {type(live).__name__} vs {type(saved).__name__}')
