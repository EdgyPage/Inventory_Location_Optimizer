"""test_section_timers.py — the two-level section accumulator `_build_leaf` keeps by hand.

WHAT THIS FENCES.  `strategy_runner._build_leaf` carried the per-section wall timers as
TWENTY-THREE closure variables: eleven `t_*_ckpt` / `p*_sum_ckpt` window accumulators, the
eleven `t_*_run` / `p*_run` whole-arm totals they fold into, and `t_save_run`, which has no
window at all because a checkpoint's DB write is timed inside the checkpoint block.  Three
separate places had to stay in step — the declarations, the checkpoint fold-and-reset, and
`_finish`'s fold of the final unflushed window — and each of the three names every section.

THE FAILURE THAT SHAPE INVITES is already in this repo's memory: a run-end writer whose fold
sits inside `if pb:` does not fire when `n_batches` divides the checkpoint cadence, and the
final window is discarded in silence.  The same class of defect is recorded against the
`p1`/`p2` split, whose final window WAS silently dropped until the runtime_metrics column add.

So the interface below is chosen to make that defect unrepresentable rather than merely
tested: **`total()` always includes the open window.**  A caller that forgets the last
`roll()` loses nothing, because nothing was ever waiting on the fold — `roll()` only closes
the WINDOW, which exists for the per-checkpoint log line, and the totals are correct at
every instant.  `roll()` is therefore an observability operation, not a correctness one.

EQUIVALENCE WITH THE HAND-KEPT FORM.  Old `t_x_run` was the sum of every FOLDED window, and
`_finish` folded the tail; new `total(x)` is the sum of every window, open one included.
Those are the same number.  Old `t_x_ckpt` was the open window; new `window(x)` is the open
window.  Also the same.  `test_the_totals_survive_a_forgotten_final_roll` is the one that
would have failed on the old shape.
"""
#: NO sys.path bootstrap here: `Tests/conftest.py` puts the repo root on the path for
#: the whole suite, and CLAUDE.md names it and entry-script bootstraps as the only
#: legal `sys.path.insert` sites.
import math

import pytest


from Optimization.simdriver.section_timers import SectionTimers


# ── the window / total split ──────────────────────────────────────────────────────

def test_a_fresh_accumulator_reads_zero_everywhere():
    """Every declared section starts at 0.0 in both halves, as the hand-kept block did."""
    st = SectionTimers()
    for name in SectionTimers.SECTIONS:
        assert st.window(name) == 0.0, f'{name} window must start at zero'
        assert st.total(name) == 0.0, f'{name} total must start at zero'


def test_add_accumulates_into_the_open_window():
    st = SectionTimers()
    st.add('sim', 1.5)
    st.add('sim', 2.25)
    assert st.window('sim') == pytest.approx(3.75)


def test_add_leaves_every_other_section_alone():
    """A section is its own accumulator — the hand-kept form's one virtue, kept."""
    st = SectionTimers()
    st.add('sim', 5.0)
    for name in SectionTimers.SECTIONS:
        if name == 'sim':
            continue
        assert st.window(name) == 0.0, f'adding to sim moved {name}'


def test_roll_closes_the_window_and_leaves_the_total_standing():
    """The checkpoint operation: the log line's window resets, the whole-arm total does not."""
    st = SectionTimers()
    st.add('extract', 4.0)
    st.roll()
    assert st.window('extract') == 0.0, 'roll must zero the window for the next log line'
    assert st.total('extract') == pytest.approx(4.0), 'roll must not lose the folded window'


def test_the_total_includes_the_open_window():
    """THE PROPERTY THE OLD SHAPE LACKED.  A total is correct before any fold, not after."""
    st = SectionTimers()
    st.add('pre', 2.0)
    assert st.total('pre') == pytest.approx(2.0), (
        'a total that only counts folded windows is the run-end-flush defect')


def test_the_totals_survive_a_forgotten_final_roll():
    """The recorded defect, as a test: three windows, only two of them ever closed.

    On the hand-kept form the third window reaches `t_x_run` only through `_finish`'s fold,
    so a run-end writer that never reached that fold reported 8.0 for a section that cost
    12.5.  Here the third window is already in the total.
    """
    st = SectionTimers()
    st.add('reord', 5.0);  st.roll()
    st.add('reord', 3.0);  st.roll()
    st.add('reord', 4.5)                      # the window nobody closes
    assert st.total('reord') == pytest.approx(12.5)


def test_rolling_an_empty_window_is_a_no_op():
    """A checkpoint that timed nothing must not perturb a total (float association matters)."""
    st = SectionTimers()
    st.add('inv', 7.0)
    st.roll()
    before = st.total('inv')
    st.roll(); st.roll()
    assert st.total('inv') == before


# ── the run-only section ──────────────────────────────────────────────────────────

def test_save_is_declared_and_behaves_like_any_other_section():
    """`t_save_run` had no window: the DB write is timed INSIDE the checkpoint block.

    It is declared here anyway and simply never carries an open window in the caller, so
    the result payload can be built by one loop over SECTIONS instead of naming save apart.
    """
    assert 'save' in SectionTimers.SECTIONS
    st = SectionTimers()
    st.add('save', 0.25)
    assert st.total('save') == pytest.approx(0.25)


# ── the vocabulary, pinned ────────────────────────────────────────────────────────

def test_the_section_vocabulary_is_exactly_the_runtime_metrics_columns():
    """The names are a CONTRACT with `runtime_metrics` and with the checkpoint log line.

    `Tests/calltree/test_calltree_anchors.py` pins the same vocabulary from the other side
    (`SECTION_MAP` against the log line), so a section renamed here without renaming it
    there breaks a gate rather than silently re-labelling an archived column.
    """
    assert SectionTimers.SECTIONS == (
        'reord', 'build', 'sample', 'task', 'kf', 'pre', 'sim',
        'extract', 'inv', 'save', 'p1', 'p2',
        'inb_pre', 'inb_freeze', 'inb_pack', 'inb_yplan', 'inb_dplan',
        'inb_unload', 'inb_handoff', 'put', 'put_open')


def test_an_undeclared_section_is_refused_rather_than_created():
    """A typo must not silently open a thirteenth section nothing ever reads."""
    st = SectionTimers()
    with pytest.raises(KeyError):
        st.add('extrct', 1.0)
    with pytest.raises(KeyError):
        st.total('extrct')


# ── the payload the parent writes runtime_metrics from ────────────────────────────

def test_totals_renders_every_section_under_its_t_prefixed_name():
    """`_finish` builds its result dict from this, so the keys ARE the payload's keys."""
    st = SectionTimers()
    st.add('sim', 1.0); st.add('p1', 0.5); st.roll()
    st.add('sim', 2.0)
    out = st.totals()
    assert out['t_sim'] == pytest.approx(3.0)
    assert out['p1_s'] == pytest.approx(0.5), 'the fast_pick split keeps its p*_s column name'
    assert out['t_save'] == 0.0
    assert set(out) == {'t_reord', 't_build', 't_sample', 't_task', 't_kf', 't_pre',
                        't_sim', 't_extract', 't_inv', 't_save', 'p1_s', 'p2_s',
                        't_inb_pre', 't_inb_freeze', 't_inb_pack', 't_inb_yplan',
                        't_inb_dplan', 't_inb_unload', 't_inb_handoff', 't_put',
                        't_put_open'}


def test_the_totals_payload_is_a_plain_picklable_dict_of_floats():
    """It crosses a process boundary in the worker result, so no views and no defaultdicts."""
    import pickle
    st = SectionTimers()
    st.add('build', 1.0)
    out = st.totals()
    assert type(out) is dict
    assert all(type(v) is float for v in out.values())
    assert pickle.loads(pickle.dumps(out)) == out


# ── float discipline: the accumulator must not re-associate ───────────────────────

def test_accumulation_order_matches_a_plain_running_sum():
    """Byte-identical discipline: the total must be the SAME float the old `+=` chain made.

    Not approx — exactly.  The old code added each window into a running total in fold
    order; anything that sums a list or re-associates can differ in the last ulp, and an
    archived runtime_metrics row would stop reproducing.
    """
    windows = [0.1, 0.2, 0.30000000000000004, 0.7, 1e-17, 3.3]
    st = SectionTimers()
    expect = 0.0
    for w in windows:
        st.add('task', w)
        st.roll()
        expect += w
    assert st.total('task') == expect


def test_within_window_accumulation_also_matches_a_running_sum():
    """Same rule one level down: many `add`s inside one window are one running sum."""
    deltas = [1e-16, 1.0, 1e-16, 2.5, 1e-16]
    st = SectionTimers()
    expect = 0.0
    for d in deltas:
        st.add('pre', d)
        expect += d
    assert st.window('pre') == expect
    assert st.total('pre') == expect


def test_a_rolled_total_is_the_running_sum_of_windows_not_of_deltas():
    """The fold is (base + window), exactly as `t_x_run += t_x_ckpt` was — one add per roll.

    TWO WINDOWS, NOT ONE, and that is the whole point of the test.  With a single window the
    two candidate associations are ARITHMETICALLY IDENTICAL — folding the window gives
    `0.0 + (a+b+c)` and summing the deltas gives `((0+a)+b)+c`, which is the same float — so
    a one-window version of this test passes under either implementation and proves nothing.
    It takes a second window for them to diverge.

    The values below are chosen so they actually do diverge:

        fold-windows : (0.0 + (1e-16 + 1.0)) + (-1.0 + 1e-16) == 1.1102230246251565e-16
        sum-deltas   : (((0.0 + 1e-16) + 1.0) + -1.0) + 1e-16 == 1e-16

    Asserted with `==`, deliberately, against this repo's "floats compare with a tolerance"
    rule: the quantity under test IS the last-ulp behaviour, and a tolerance would admit
    exactly the drift the test exists to forbid.  An archived `runtime_metrics` row has to
    keep reproducing bit for bit.
    """
    a, b, c, d = 1e-16, 1.0, -1.0, 1e-16

    st = SectionTimers()
    st.add('kf', a); st.add('kf', b)
    w1 = st.window('kf')
    st.roll()
    st.add('kf', c); st.add('kf', d)
    w2 = st.window('kf')
    st.roll()

    fold_windows = (0.0 + w1) + w2
    sum_deltas = (((0.0 + a) + b) + c) + d
    assert fold_windows != sum_deltas, (
        'the chosen values no longer distinguish the two associations, so this test cannot '
        'tell them apart — pick values where they diverge')
    assert st.total('kf') == fold_windows
    assert st.total('kf') != sum_deltas


# ── the object itself ─────────────────────────────────────────────────────────────

def test_it_is_slotted_so_a_typo_cannot_shadow_a_section():
    """`st.t_sim = 1.0` must raise, not quietly create an attribute nothing reads."""
    st = SectionTimers()
    with pytest.raises(AttributeError):
        st.t_sim = 1.0


def test_it_survives_the_worker_process_boundary():
    """The leaf is built in a spawned worker; nothing here may be unpicklable."""
    import pickle
    st = SectionTimers()
    st.add('sim', 2.0); st.roll(); st.add('sim', 1.0)
    back = pickle.loads(pickle.dumps(st))
    assert back.total('sim') == pytest.approx(3.0)
    assert back.window('sim') == pytest.approx(1.0)


def test_nan_and_inf_are_not_special_cased():
    """No clamping, no guards: a timer that went wrong must show, not be laundered to 0."""
    st = SectionTimers()
    st.add('sim', float('inf'))
    assert math.isinf(st.total('sim'))


# ── the lap cursor ────────────────────────────────────────────────────────────────
# `_build_leaf` kept the cursor itself, as a seventh closure variable `_t`, and every one
# of the eight timing sites read the same three-statement incantation:
#
#     _now = time.perf_counter(); timers.add('reord', _now - _t); _t = _now
#
# The cursor is the TIMERS' state, not the caller's: every use of `_t` was a lap against
# this object and nothing else ever read it. Owning it removes eight chances to forget the
# `_t = _now`, which silently attributes one section's time to the next as well.

def test_start_opens_a_lap_at_a_given_instant():
    st = SectionTimers()
    st.start(now=100.0)
    st.split('sim', now=103.5)
    assert st.window('sim') == pytest.approx(3.5)


def test_split_advances_the_cursor_so_laps_do_not_overlap():
    """The forgotten `_t = _now` is the defect this removes: without the advance the next
    section is charged its own time PLUS everything before it."""
    st = SectionTimers()
    st.start(now=0.0)
    st.split('reord', now=1.0)
    st.split('sim', now=3.0)
    assert st.window('reord') == pytest.approx(1.0)
    assert st.window('sim') == pytest.approx(2.0), 'the second lap must not re-charge the first'


def test_split_can_charge_SEVERAL_sections_the_same_delta():
    """`build` is the sum of `sample` and `task`, so each of those two sites charged BOTH --
    one `_dt` added twice, never two clock reads."""
    st = SectionTimers()
    st.start(now=0.0)
    st.split('sample', 'build', now=2.0)
    st.split('task', 'build', now=5.0)
    assert st.window('sample') == pytest.approx(2.0)
    assert st.window('task') == pytest.approx(3.0)
    assert st.window('build') == pytest.approx(5.0), 'build is the sum of its two sub-splits'


def test_split_returns_the_delta_it_charged():
    st = SectionTimers()
    st.start(now=10.0)
    assert st.split('pre', now=14.25) == pytest.approx(4.25)


def test_split_reads_the_clock_when_no_instant_is_given():
    """Production passes no `now`; the injectable one exists so a test need not sleep."""
    st = SectionTimers()
    st.start()
    dt = st.split('sim')
    assert dt >= 0.0
    assert st.window('sim') == dt


def test_an_undeclared_section_is_refused_by_split_too():
    st = SectionTimers()
    st.start(now=0.0)
    with pytest.raises(KeyError):
        st.split('extrct', now=1.0)


def test_the_cursor_is_not_a_section_and_does_not_reach_the_payload():
    """A cursor in `totals()` would be a perf_counter stamp written to a DB column."""
    st = SectionTimers()
    st.start(now=5.0)
    st.split('sim', now=6.0)
    assert set(st.totals()) == {'t_reord', 't_build', 't_sample', 't_task', 't_kf', 't_pre',
                                't_sim', 't_extract', 't_inv', 't_save', 'p1_s', 'p2_s',
                                't_inb_pre', 't_inb_freeze', 't_inb_pack', 't_inb_yplan',
                                't_inb_dplan', 't_inb_unload', 't_inb_handoff', 't_put',
                                't_put_open'}


def test_split_charges_the_open_window_and_rolls_with_it():
    """A lap is an ordinary `add`: it lands in the open window and the totals still include it."""
    st = SectionTimers()
    st.start(now=0.0)
    st.split('sim', now=2.0)
    assert st.window('sim') == pytest.approx(2.0)
    st.roll()
    assert st.window('sim') == 0.0
    assert st.total('sim') == pytest.approx(2.0)
