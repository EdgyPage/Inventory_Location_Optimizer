"""test_speed_profile.py — one name for the ft/s → s/inch boundary.

A travel speed is CONFIGURED in ft/s and SPENT in s/inch, because bin positions are inches.
Nothing named that boundary, so 47 separate `sec_per_inch` calls crossed it by hand.

The discipline at those sites was already right — every one hoists the conversion to the top
of its function and passes `x_pace`/`y_pace` down, so it is never per-bin. What was missing
is a name for the hoisted pair. Two bare floats travelling together through a dozen
signatures is the shape that lets an x pace reach a y slot, and it is the shape that cannot
be extended: a putter and an unloader need their own speeds, and `(x_pace, y_pace)` has
nowhere to record whose they are.

Run:  python -m pytest Tests/unit/test_speed_profile.py -q
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import pathlib

import pytest

from Optimization.metrics.Workload import WorkloadParams
from Warehouse.kernel import cost_model
from Warehouse.kernel.cost_model import SpeedProfile, sec_per_inch
from Warehouse.picking.Pick import PickConfig


# ── the conversion happens once, and matches the function it replaces ─────────────

def test_the_pace_is_exactly_sec_per_inch():
    p = SpeedProfile(3.0, 2.0)
    assert p.x_pace == sec_per_inch(3.0)
    assert p.y_pace == sec_per_inch(2.0)
    assert p.paces == (p.x_pace, p.y_pace)


def test_the_axes_do_not_get_swapped():
    """x is horizontal travel ALONG an aisle, y is VERTICAL lift. Different quantities."""
    p = SpeedProfile(x_ft_s=3.0, y_ft_s=2.0)
    assert p.x_pace < p.y_pace, 'the faster speed must have the SMALLER pace'
    assert SpeedProfile(2.0, 3.0) != p


def test_a_non_positive_speed_is_rejected_here_too():
    """`sec_per_inch(0)` is `inf`, and `inf * 0` (a bin on the axis origin) is NaN, which
    silently degrades every min/max to insertion order. Caught at the boundary."""
    for bad in (0.0, -1.0):
        with pytest.raises(ValueError):
            SpeedProfile(bad, 2.0)
        with pytest.raises(ValueError):
            SpeedProfile(2.0, bad)


# ── it is a value: frozen, picklable, comparable ──────────────────────────────────

def test_it_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SpeedProfile(3.0, 2.0).x_ft_s = 9.0


def test_it_survives_the_spawn_boundary():
    """Everything in a worker payload is pickled; a speed that cannot cross is useless."""
    import pickle
    p = SpeedProfile(3.0, 2.0)
    back = pickle.loads(pickle.dumps(p))
    assert back == p and back.paces == p.paces


def test_equality_is_decided_by_the_configured_speeds():
    assert SpeedProfile(3.0, 2.0) == SpeedProfile(3.0, 2.0)
    assert SpeedProfile(3.0, 2.0) != SpeedProfile(3.0, 2.5)


def test_the_derived_paces_stay_out_of_the_constructor():
    """They are computed, not supplied — passing one would be a way to lie about a speed."""
    init_fields = [f.name for f in dataclasses.fields(SpeedProfile) if f.init]
    assert init_fields == ['x_ft_s', 'y_ft_s']


# ── the serialization contract it must NOT break ──────────────────────────────────

def test_pick_config_keeps_x_speed_and_y_speed_as_FIELDS():
    """`run_map_precompute` rebuilds a PickConfig from an ARCHIVED config.json by filtering
    `dataclasses.fields`, and `docs/macros.py` reads `c["x_speed"]` to render the published
    formula. If these stopped being fields both would fall back to defaults SILENTLY, and no
    canary would catch it because none of them reads an archived config."""
    names = {f.name for f in dataclasses.fields(PickConfig)}
    assert {'x_speed', 'y_speed'} <= names
    assert 'speed' not in names, 'speed must stay a derived property, not a field'


def test_workload_params_keeps_them_too():
    names = {f.name for f in dataclasses.fields(WorkloadParams)}
    assert {'x_speed', 'y_speed'} <= names
    assert 'speed' not in names


def test_the_sim_and_its_analytical_mirror_share_one_speed():
    cfg = PickConfig(num_pickers=2, x_speed=3.0, y_speed=2.0)
    assert WorkloadParams.from_pick_config(cfg).speed == cfg.speed


# ── adopted where it matters, and the count only falls ────────────────────────────

@pytest.mark.parametrize('module', [
    'Warehouse.picking.fast_pick',
    'Warehouse.picking.Pick',
    'Optimization.metrics.Workload',
])
def test_the_hot_paths_hoist_the_profile_not_the_bare_function(module):
    import importlib
    src = inspect.getsource(importlib.import_module(module))
    assert '.speed.paces' in src or '.speed\n' in src or '_sp = params.speed' in src, (
        f'{module} no longer hoists a SpeedProfile')


def test_neither_pick_simulation_converts_by_hand_any_more():
    """The two sims are kept byte-for-byte in lockstep; a conversion in one and a profile in
    the other is exactly how that lockstep rots."""
    from Warehouse.picking import Pick, fast_pick
    for mod in (Pick, fast_pick):
        src = inspect.getsource(mod)
        assert 'sec_per_inch(cfg.x_speed)' not in src
        assert 'sec_per_inch(cfg.y_speed)' not in src


def test_the_hand_conversion_count_only_falls():
    """A ratchet on hand-written ft/s → s/inch conversions.

    The budget is the CURRENT count under this test's own counting rule, not a round
    number above it. It was first written as 42 against an actual 21 — 100% headroom, a
    ratchet that could not fail, and a docstring quoting 47 because that figure came from
    a different counting rule (raw occurrences including cost_model.py and Tests/).
    Twenty-eight lines matched at the base commit and seven became the profile, so 21 is
    where it stands. Lower it when you convert more; never raise it.
    """
    root = pathlib.Path(__file__).resolve().parents[2]
    budget = 21
    n = 0
    for sub in ('Warehouse', 'Optimization'):
        for path in sorted((root / sub).rglob('*.py')):
            src = path.read_text(encoding='utf-8')
            # The definition and its own docstring do not count as call sites.
            if path.name == 'cost_model.py':
                continue
            n += sum(1 for line in src.splitlines()
                     if 'sec_per_inch(' in line and not line.lstrip().startswith('#'))
    assert n <= budget, (
        f'{n} hand conversions of ft/s -> s/inch, budget {budget}. Route new code through '
        f'cost_model.SpeedProfile; if you converted some, LOWER the budget in this test.')


# ── the kernel stays importable-in-isolation ──────────────────────────────────────

def test_cost_model_still_imports_nothing_from_the_repo():
    """SpeedProfile lives in cost_model rather than a kernel/speed.py precisely because
    `architecture.yml` forbids `wh_kernel -> *` and the wildcard matches the kernel itself:
    a separate module importing `sec_per_inch` is a boundary violation."""
    tree = ast.parse(inspect.getsource(cost_model))
    for node in ast.walk(tree):
        mod = (node.names[0].name if isinstance(node, ast.Import)
               else (node.module or '') if isinstance(node, ast.ImportFrom) else None)
        if mod and mod.split('.')[0] in ('Warehouse', 'Optimization', 'Schema'):
            pytest.fail(f'cost_model imports {mod}; the kernel may import nothing')
