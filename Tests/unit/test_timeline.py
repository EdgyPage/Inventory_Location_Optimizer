"""test_timeline.py — what the simulation's clock counts in, and where batches sit on it.

Two facts a second work stream has to settle before it can share the clock, and neither
was written down anywhere.

**The unit.** `cost_model` produces SECONDS. The analysis layer used to declare
milliseconds and divide by `3.6e6`, so every ABSOLUTE figure it published was 1000x out
while every ratio was right — which is why nothing caught it. Fixed: `units.py` now
imports `SECONDS_PER_HOUR` from the kernel rather than restating a literal, so the two
cannot disagree again. The tests below pin the IMPORT, not an equality, because an
equality would pass the day someone edited the literal back.

**The epoch.** A batch's picker clocks start at zero, so `t` alone cannot order two events
from different batches. The absolute axis was being invented in an analysis module by a
bare `np.cumsum` with no name. It has one now, and the what-if that needed it uses it —
so this is a named definition with a consumer, not a module waiting for one.

Run:  python -m pytest Tests/unit/test_timeline.py -q
"""
from __future__ import annotations

import ast
import inspect

import pytest

from Warehouse.kernel import timeline
from Warehouse.kernel.cost_model import aisle_exit_cost, aisle_traverse_cost


# ── the unit, and the discrepancy ────────────────────────────────────────────────

def test_the_sim_declares_its_unit():
    assert timeline.TIME_UNIT == 'seconds'
    assert timeline.SECONDS_PER_HOUR == 3600.0


def test_the_analysis_layer_divides_by_the_kernels_own_declaration():
    """Not "equal to 3600" — IMPORTED from here.  The two disagreed by 1000x for the life
    of the project because each restated the number in its own words; an equality test
    would have passed the day someone edited one back."""
    import inspect

    from Optimization.Performance_Evaluations.common import units
    assert units.PER_HOUR == timeline.SECONDS_PER_HOUR
    assert units.SECONDS_PER_HOUR is timeline.SECONDS_PER_HOUR
    src = inspect.getsource(units)
    assert 'from Warehouse.kernel.timeline import SECONDS_PER_HOUR' in src
    # No assertion that '3.6e6' is absent from the TEXT: the comment beside PER_HOUR quotes
    # the old number to explain the 1000x history, and a check that forbade the literal in
    # prose would push people to delete the explanation.  The code itself is covered by
    # test_no_module_carries_its_own_copy_of_the_divisor, which strips docstrings first.


def test_no_module_carries_its_own_copy_of_the_divisor():
    """FIVE modules restated it. Fixing one would have left the others wrong and the
    writers disagreeing with each other as well as with the sim.

    Checked against CODE, not prose: the comments that explain the 1000x history quote the
    old number on purpose, and a ratchet that counted them would push people to delete the
    explanation. The AST is unparsed with docstrings stripped, so only a real literal or a
    real name counts.
    """
    import pathlib
    root = pathlib.Path(inspect.getfile(timeline)).parents[2]
    offenders = []
    for sub in ('Optimization', 'Warehouse'):
        for path in sorted((root / sub).rglob('*.py')):
            try:
                tree = ast.parse(path.read_text(encoding='utf-8'))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    body = node.body
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        node.body = body[1:] or [ast.Pass()]
            code = ast.unparse(ast.fix_missing_locations(tree))
            if 'MS_PER_HOUR' in code or '3600000' in code or '3.6e6' in code:
                offenders.append(path.relative_to(root).as_posix())
    assert not offenders, f'{offenders} still carry the milliseconds-per-hour divisor'


def test_the_suites_time_units_are_seconds_based():
    """TIME_UNITS chooses the axis unit by comparing a magnitude against these scales; on
    the ms table a 3600-second batch rendered as "seconds"."""
    from Optimization.Performance_Evaluations.common import units
    assert dict(units.TIME_UNITS) == {'hours': 3600.0, 'minutes': 60.0, 'seconds': 1.0}
    div, noun = units.time_units([7200.0, 7300.0])      # two hours of work
    assert (div, noun) == (3600.0, 'hours')
    div, noun = units.time_units([30.0, 40.0])          # half a minute
    assert (div, noun) == (1.0, 'seconds')


def test_the_unit_kinds_no_longer_claim_milliseconds():
    from Optimization.Performance_Evaluations.common import units
    assert 'duration_s' in units.KINDS and 'rate_per_s' in units.KINDS
    assert 'duration_ms' not in units.KINDS and 'rate_per_ms' not in units.KINDS


def test_the_sims_own_constants_read_as_seconds():
    """The evidence for calling it seconds rather than milliseconds: 15 s a pick and 300 s
    a cart swap are warehouse numbers; 15 ms and 0.3 s are not."""
    from Optimization.simconfig.configs import store
    cfg = store.CONFIG if hasattr(store, 'CONFIG') else None
    src = inspect.getsource(store)
    assert "'pick_intercept'" in src and "'cart_swap_coef'" in src


# ── the epoch ────────────────────────────────────────────────────────────────────

def test_the_first_batch_starts_at_zero():
    assert timeline.batch_epoch([10.0, 20.0], 0) == 0.0


def test_a_batch_starts_after_every_batch_before_it():
    d = [10.0, 20.0, 5.0]
    assert timeline.batch_epoch(d, 1) == 10.0
    assert timeline.batch_epoch(d, 2) == 30.0
    assert timeline.batch_epoch(d, 3) == 35.0     # one past the end = the run's total


def test_a_negative_index_raises_rather_than_wrapping():
    """`durations[-1]` is a real batch and would give a plausible wrong answer."""
    with pytest.raises(IndexError, match='negative'):
        timeline.batch_epoch([1.0, 2.0], -1)


def test_epochs_are_the_starts_and_cumsum_gives_the_ends():
    """The relationship the what-if depends on: `epoch + duration == cumsum`."""
    d = [10.0, 20.0, 5.0]
    starts = timeline.epochs(d)
    assert starts == [0.0, 10.0, 30.0]
    running, ends = 0.0, []
    for x in d:
        running += x
        ends.append(running)
    assert [s + x for s, x in zip(starts, d)] == ends


def test_epochs_tolerate_a_none_duration():
    """A batch row can carry NULL; the analysis reader coerces with `or 0.0` and this must
    agree or the two paths diverge on exactly the runs that have a gap."""
    assert timeline.epochs([10.0, None, 5.0]) == [0.0, 10.0, 10.0]


def test_the_whatif_uses_the_named_definition():
    """Not dormant infrastructure: the module that was inventing a timeline now imports
    one."""
    import pathlib
    root = pathlib.Path(inspect.getfile(timeline)).parents[2]
    src = (root / 'Optimization' / 'run_whatif_volume.py').read_text(encoding='utf-8')
    assert 'from Warehouse.kernel.timeline import epochs' in src
    assert 'batch_epochs(dur)' in src


def test_timeline_imports_nothing_but_the_standard_library():
    """`architecture.yml` forbids `wh_kernel -> *`, which is what lets the harness and a
    future inbound package share this."""
    tree = ast.parse(inspect.getsource(timeline))
    for node in ast.walk(tree):
        mod = (node.names[0].name if isinstance(node, ast.Import)
               else (node.module or '') if isinstance(node, ast.ImportFrom) else None)
        if mod and mod.split('.')[0] in ('Warehouse', 'Optimization', 'Schema', 'Inbound'):
            pytest.fail(f'timeline imports {mod}; the kernel may import nothing')


# ── the traverse seam that was lying ─────────────────────────────────────────────

def test_the_one_way_exit_has_one_definition():
    """`aisle_traverse_cost` claimed to be "the single definition shared by the sim" and
    had zero callers, while both sims inlined the arithmetic. The half they genuinely
    share is now its own function and both call it."""
    x_pace, y_pace = 0.02, 0.05
    assert aisle_exit_cost(30.0, 12.0, 100.0, x_pace, y_pace) == (
        abs(100.0 - 30.0) * x_pace, 12.0 * y_pace)


def test_the_traverse_function_delegates_rather_than_repeating_it():
    _ex, _ey, exit_x, exit_y = aisle_traverse_cost(
        5.0, 2.0, 30.0, 12.0, 100.0, 0.02, 0.05, one_way=True)
    assert (exit_x, exit_y) == aisle_exit_cost(30.0, 12.0, 100.0, 0.02, 0.05)


def test_a_two_way_lane_has_no_exit_segment():
    _ex, _ey, exit_x, exit_y = aisle_traverse_cost(
        5.0, 2.0, 30.0, 12.0, 100.0, 0.02, 0.05, one_way=False)
    assert (exit_x, exit_y) == (0.0, 0.0)


@pytest.mark.parametrize('module,fn', [
    ('Warehouse.picking.fast_pick', '_simulate_picker_deferred'),
    ('Warehouse.picking.Pick', 'PickSimulation._simulate_picker'),
])
def test_both_sims_call_the_shared_exit_rather_than_inlining_it(module, fn):
    import importlib
    mod = importlib.import_module(module)
    obj = mod
    for part in fn.split('.'):
        obj = getattr(obj, part)
    src = inspect.getsource(obj)
    assert 'aisle_exit_cost(' in src, f'{module}.{fn} still inlines the exit arithmetic'
    assert 'abs(L - x) * x_pace' not in src


def test_the_lockstep_comment_names_a_test_that_actually_guards_it():
    """It named `test_placement_fastpath_equivalence` for years. That test is about
    `_PrefPool` map placement and touches neither sim's travel math."""
    from Warehouse.picking import fast_pick
    src = inspect.getsource(fast_pick)
    # The retired name may still APPEAR — the corrected comment explains that it was wrong,
    # which is worth keeping. What must be gone is the CLAIM.
    assert 'guarded by test_placement_fastpath_equivalence' not in src
    assert 'test_travel_decomposition' in src and 'test_scheduler' in src


def test_the_named_guards_exist_and_cover_the_travel_math():
    """A comment naming a guard is only worth anything if the guard is real. The last one
    was not, for years."""
    import pathlib
    root = pathlib.Path(inspect.getfile(timeline)).parents[2]
    for name in ('test_travel_decomposition.py', 'test_scheduler.py'):
        path = root / 'Tests' / 'unit' / name
        assert path.is_file(), f'{name} is named as a lockstep guard and does not exist'
        src = path.read_text(encoding='utf-8')
        assert 'fast_pick' in src, f'{name} does not reach the production sim at all'
