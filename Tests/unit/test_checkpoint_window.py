"""test_checkpoint_window.py — the counter half of the checkpoint window.

`SectionTimers` took the TIMER half of `_build_leaf`'s checkpoint window into one object.
`CheckpointWindow` takes the other half: the five counters and the window-open instant that
feed the same log line — `reorders`, `units_ordered`, `placed`, `dur_sum`, `dur_count`, and
the `perf_counter` stamp the window opened at.

WHY THIS ONE IS SAFER THAN `SectionTimers` WAS, and it is worth being precise about because
it is the whole justification for doing it. Every value here is consumed by exactly one
reader — the `log.info(...)` f-string at the checkpoint — and by nothing else:

    ckpt_wall = <now> - t_ckpt                      -> only the log line
    ckpt_rate = dur_count_ckpt / ckpt_wall          -> only the log line
    avg_dur   = dur_sum_ckpt / dur_count_ckpt ...   -> only the log line
    reorders_ckpt / units_ordered_ckpt / placed_ckpt -> only the log line

None reaches `save_checkpoint_bundle`, the result dict, `runtime_metrics`, or any DB. They
are not simulation numbers, so this refactor cannot move one — a stronger guarantee than
`SectionTimers`, whose totals do land in `runtime_metrics` columns.

WHY IT IS A SECOND OBJECT AND NOT FOLDED INTO `SectionTimers`. The two always roll together,
which normally argues for one object. They are kept apart because they have different
lifetimes and different contracts: a timer carries a WHOLE-ARM total that outlives every
window and is persisted; a counter here has no run total at all and is never persisted.
Folding them would give half the members a `total()` that means nothing and would reopen the
`SectionTimers` interface, which `Tests/calltree/test_calltree_anchors.py` pins.

THE ARITHMETIC IS PRESERVED EXACTLY, not approximately. `dur_sum` is a running sum in batch
order, `avg_dur` guards on `dur_count` exactly as the original did, and `rate` takes the
ALREADY-COMPUTED wall rather than re-reading the clock, because the original reused its
`ckpt_wall` local. An archived log line is not a contract, but the discipline is.
"""
#: NO sys.path bootstrap here: `Tests/conftest.py` puts the repo root on the path for
#: the whole suite, and CLAUDE.md names it and entry-script bootstraps as the only
#: legal `sys.path.insert` sites.
import pickle

import pytest

from Optimization.simdriver.section_timers import CheckpointWindow


# ── the counters ──────────────────────────────────────────────────────────────────

def test_a_fresh_window_reads_zero_everywhere():
    w = CheckpointWindow(opened_at=100.0)
    for name in CheckpointWindow.COUNTERS:
        assert w.get(name) == 0, f'{name} must start at zero'


def test_add_accumulates_and_leaves_the_others_alone():
    w = CheckpointWindow(opened_at=0.0)
    w.add('placed', 3)
    w.add('placed', 4)
    assert w.get('placed') == 7
    for name in CheckpointWindow.COUNTERS:
        if name != 'placed':
            assert w.get(name) == 0, f'adding to placed moved {name}'


def test_an_undeclared_counter_is_refused_rather_than_created():
    """A typo must not open a sixth counter nothing ever prints."""
    w = CheckpointWindow(opened_at=0.0)
    with pytest.raises(KeyError):
        w.add('palced', 1)
    with pytest.raises(KeyError):
        w.get('palced')


def test_dur_sum_is_a_running_sum_in_batch_order():
    """Not `sum()` of a list: the original was a chain of `+=` in batch order, and a
    different association can differ in the last ulp."""
    durs = [1e-16, 1.0, 1e-16, 2.5, 1e-16]
    w = CheckpointWindow(opened_at=0.0)
    expect = 0.0
    for d in durs:
        w.add('dur_sum', d)
        expect += d
    assert w.get('dur_sum') == expect


# ── the three derived numbers, each preserving its original expression ────────────

def test_wall_is_now_minus_the_open_instant():
    w = CheckpointWindow(opened_at=10.0)
    assert w.wall(33.5) == pytest.approx(23.5)


def test_rate_takes_the_already_computed_wall():
    """The original reused its `ckpt_wall` local rather than re-reading the clock:

        ckpt_wall = time.perf_counter() - t_ckpt
        ckpt_rate = dur_count_ckpt / ckpt_wall

    Passing the wall in keeps that one-clock-read shape. A `rate()` that read the clock
    itself would divide by a slightly LATER wall than the one printed beside it.
    """
    w = CheckpointWindow(opened_at=0.0)
    for _ in range(8):
        w.add('dur_count', 1)
    assert w.rate(4.0) == pytest.approx(2.0)


def test_avg_dur_guards_on_the_count_exactly_as_the_original_did():
    """`dur_sum / dur_count if dur_count else 0.0` — a window that timed no batch reports
    0.0 rather than raising, which is what the first checkpoint of a skipped-batch run does."""
    w = CheckpointWindow(opened_at=0.0)
    assert w.avg_dur() == 0.0, 'an empty window must not divide by zero'
    w.add('dur_sum', 9.0)
    w.add('dur_count', 4)
    assert w.avg_dur() == pytest.approx(2.25)


def test_rate_does_not_silently_guard_a_zero_wall():
    """The original had NO zero guard on `ckpt_rate`. Adding one here would be a behaviour
    change hiding a clock that did not advance, so the raise is kept deliberately."""
    w = CheckpointWindow(opened_at=0.0)
    w.add('dur_count', 1)
    with pytest.raises(ZeroDivisionError):
        w.rate(0.0)


# ── rolling ───────────────────────────────────────────────────────────────────────

def test_roll_zeroes_every_counter_and_reopens_the_window():
    w = CheckpointWindow(opened_at=5.0)
    for name in CheckpointWindow.COUNTERS:
        w.add(name, 3)
    w.roll(now=20.0)
    for name in CheckpointWindow.COUNTERS:
        assert w.get(name) == 0, f'{name} survived the roll'
    assert w.wall(25.0) == pytest.approx(5.0), 'the window did not reopen at the new instant'


def test_rolling_resets_all_five_together():
    """The original reset the five counters and the stamp in ONE block. A partial reset
    would carry one counter's window into the next line and read as a spike."""
    w = CheckpointWindow(opened_at=0.0)
    w.add('reorders', 1); w.add('units_ordered', 2); w.add('placed', 3)
    w.add('dur_sum', 4.0); w.add('dur_count', 5)
    w.roll(now=1.0)
    assert [w.get(n) for n in CheckpointWindow.COUNTERS] == [0, 0, 0, 0, 0]


# ── the object itself ─────────────────────────────────────────────────────────────

def test_the_counter_vocabulary_is_pinned():
    assert CheckpointWindow.COUNTERS == (
        'reorders', 'units_ordered', 'placed', 'dur_sum', 'dur_count')


def test_it_is_slotted_so_a_typo_cannot_shadow_a_counter():
    w = CheckpointWindow(opened_at=0.0)
    with pytest.raises(AttributeError):
        w.placed = 1


def test_it_survives_the_worker_process_boundary():
    """The leaf that owns one is built inside a spawned worker."""
    w = CheckpointWindow(opened_at=2.0)
    w.add('placed', 6)
    back = pickle.loads(pickle.dumps(w))
    assert back.get('placed') == 6
    assert back.wall(5.0) == pytest.approx(3.0)


# ── the guarantee that makes this refactor free ───────────────────────────────────

def test_no_window_value_is_persisted_anywhere():
    """THE JUSTIFICATION, asserted rather than trusted.

    This refactor is safe because every value it touches dies at the log line. If a future
    change routes one into the DB or the result dict, that safety argument silently stops
    holding — so check that the old names are gone AND that the new object is not reachable
    from anything that writes.
    """
    import inspect
    from Optimization.simdriver import strategy_runner as sr
    src = inspect.getsource(sr)

    for old in ('reorders_ckpt', 'units_ordered_ckpt', 'placed_ckpt',
                'dur_sum_ckpt', 'dur_count_ckpt', 't_ckpt'):
        assert old not in src, f'{old} is back as a loose closure variable'

    # the window must not be handed to any writer
    for writer in ('save_checkpoint_bundle(', 'save_worker_checkpoint(', 'record_arm('):
        for line in src.split('\n'):
            if writer in line:
                assert 'ckpt_win' not in line, (
                    f'the checkpoint window reaches {writer} — its values are log-line only, '
                    f'and persisting one would break the argument that this object cannot '
                    f'move a simulation number')
