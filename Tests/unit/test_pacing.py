"""test_pacing.py — a dispatch order taken from a reference run's own measurements.

The matrix wall is the worker-hours plus one unit, so it is decided by WHICH unit goes
last. On a wide, shallow spec the units differ by more than an order of magnitude (a priced
placement arm against `fifo` measured 39-59x on the campaign), which is why the order is
worth ~7% of a ten-cell run and why getting it backwards is worth the same amount the other
way.

Everything here is result-neutral by construction — the pool runs the same jobs with the
same workers and writes the same files — so a regression is SILENT in the healthy
direction: a weight function that returned 0.0 for everything would finish every run,
byte-identical, with the tail back. The tests are therefore about the ARITHMETIC and the
IDENTITY, both of which have a specific way of being wrong:

  * **`total_s` is the UNIT loop's wall, recorded on BOTH leaves of a coupled unit.**
    Summing it rates a coupled unit at exactly twice its cost and floats every one of them
    to the front of a matrix that mixes unit kinds.
  * **A unit's identity comes from its PAYLOAD, never from slicing its uid.** A uid's four
    slots mean `(pair, config, channel, arm)` on a leaf unit and `(pair, 'coupled',
    arm_store, arm_ful)` on a coupled one.
  * **The channel is `'store'` on a store-only layout, never `''`.**
  * **A resumed unit has less work left than the row describes**, so the loop term scales
    by the remaining fraction. Without that, the nearly-finished arms dispatch first.
  * **The fallback ladder ends on the CELL**, because an arm's cost varies enormously with
    the arm and barely at all with the cell — the near-invariance phase 2's exact-tie
    ranking was showing.

Run:  python -m pytest Tests/unit/test_pacing.py -q
"""
from __future__ import annotations

import logging

from Optimization.simdriver import pacing

_LOG = logging.getLogger('test_pacing')


def _row(cell, pair, cfg, chan, arm, total_s, precomp_s=0.0):
    return {'cell': cell, 'pair': pair, 'config': cfg, 'channel': chan, 'arm': arm,
            'total_s': total_s, 'precomp_s': precomp_s}


def _book(rows):
    return pacing._index(rows)


def _leaf_payload(pair, cfg, chan, arm, *, start_i=0, n_batches=40):
    """A one-leaf unit's payload, in the shape `workunits._stamp_identity` leaves it."""
    return {'group_keys': [(pair, cfg, chan)], 'arm_key': arm, 'strategy': arm,
            'start_i': start_i, 'n_batches': n_batches}


def _coupled_payload(pair, store_cfg, ful_cfg, arm_s, arm_f, *, n_batches=40):
    """A coupled unit's payload, as `_stamp_site_identity` leaves it: two group keys, two
    arms, `arm_key=None`, and the two leaves under `leaves`."""
    return {'group_keys': [(pair, store_cfg, 'store'), (pair, ful_cfg, 'fulfillment')],
            'arm_keys': [arm_s, arm_f], 'arm_key': None, 'n_batches': n_batches,
            'leaves': [{'strategy': arm_s, 'start_i': 0, 'n_batches': n_batches},
                       {'strategy': arm_f, 'start_i': 0, 'n_batches': n_batches}]}


# ── the arithmetic ───────────────────────────────────────────────────────────────────

def test_a_coupled_unit_is_the_MAX_of_its_leaves_not_the_sum():
    """THE ONE THAT INVERTS THE ORDER.  `total_s` is the unit loop's wall and BOTH leaves
    of a coupled unit record it, so a sum rates every coupled unit at 2x and puts them all
    ahead of single leaves that really are longer."""
    book = _book([_row('c1', 'p', 'store', 'store', 'a', 1000.0),
                  _row('c1', 'p', 'ful', 'fulfillment', 'b', 1000.0),
                  _row('c1', 'p', 'store', 'store', 'long', 1500.0)])
    coupled = pacing.weight_of('c1', _coupled_payload('p', 'store', 'ful', 'a', 'b'), book)
    single = pacing.weight_of('c1', _leaf_payload('p', 'store', 'store', 'long'), book)

    assert coupled == 1000.0, f'the coupled unit must be its leaves\' MAX: {coupled}'
    assert single > coupled, (
        f'the 1500 s single leaf must outrank the coupled pair of 1000 s leaves; a SUM '
        f'would make the coupled one 2000 and reverse them ({coupled} vs {single})')


def test_the_setup_span_is_added_and_the_loop_span_is_what_scales():
    """`precomp_s` is measured OUTSIDE the batch loop, so a resume pays it again in full
    however far through the loop it starts."""
    book = _book([_row('c1', 'p', 'store', 'store', 'a', 1000.0, precomp_s=100.0)])
    fresh = pacing.weight_of('c1', _leaf_payload('p', 'store', 'store', 'a'), book)
    assert fresh == 1100.0, fresh

    # Half done: the whole estimate scales, which is the approximation the module states.
    half = pacing.weight_of(
        'c1', _leaf_payload('p', 'store', 'store', 'a', start_i=20, n_batches=40), book)
    assert half == 550.0, half
    assert half < fresh


def test_a_nearly_finished_resumed_unit_sinks_to_the_back():
    """Without the remaining-work scale, a resume dispatches its almost-done arms FIRST —
    the exact inverse of what the order is for."""
    book = _book([_row('c1', 'p', 'store', 'store', 'slow', 3600.0),
                  _row('c1', 'p', 'store', 'store', 'quick', 600.0)])
    nearly = pacing.weight_of(
        'c1', _leaf_payload('p', 'store', 'store', 'slow', start_i=39, n_batches=40), book)
    fresh_quick = pacing.weight_of('c1', _leaf_payload('p', 'store', 'store', 'quick'), book)
    assert nearly == 90.0, nearly
    assert fresh_quick > nearly, (
        'the 10-minute unit with all its work left must outrank the 1-hour unit with one '
        'batch left')


def test_a_finished_unit_at_start_i_equal_to_n_batches_keeps_its_full_weight():
    """The guard for a degenerate scale: `start_i == n_batches` would multiply by zero and
    hide a unit that the resume planner will in fact re-run from batch 0."""
    book = _book([_row('c1', 'p', 'store', 'store', 'a', 1000.0)])
    w = pacing.weight_of(
        'c1', _leaf_payload('p', 'store', 'store', 'a', start_i=40, n_batches=40), book)
    assert w == 1000.0, w


# ── identity ─────────────────────────────────────────────────────────────────────────

def test_identity_comes_from_the_payload_not_from_a_uid_slice():
    """A coupled unit's uid is `(pair, 'coupled', arm_store, arm_ful)`. A positional read
    would look for a config named `coupled` and an arm that is the other leaf's, find
    nothing, and weight every coupled unit at the global mean."""
    book = _book([_row('c1', 'p', 'store', 'store', 'a', 900.0),
                  _row('c1', 'p', 'ful', 'fulfillment', 'b', 400.0),
                  _row('c1', 'other', 'other', 'store', 'z', 5.0)])
    payload = _coupled_payload('p', 'store', 'ful', 'a', 'b')
    assert pacing.weight_of('c1', payload, book) == 900.0

    leaves = pacing._leaves_of(payload)
    assert [(p, c, ch, a) for p, c, ch, a, _s, _n in leaves] == [
        ('p', 'store', 'store', 'a'), ('p', 'ful', 'fulfillment', 'b')], leaves
    assert 'coupled' not in {c for _p, c, _ch, _a, _s, _n in leaves}


def test_a_store_only_leaf_is_channel_store_not_empty():
    """`''` is what a tree with no `<channel>` DIRECTORY looks like; `'store'` is what the
    group key and the runtime row both carry. A lookup normalised to empty would miss every
    row of every store-only run and silently fall to the global mean."""
    book = _book([_row('c1', 'p', 'store', 'store', 'a', 777.0),
                  _row('c1', 'q', 'ful', 'fulfillment', 'z', 23.0)])
    got, why = pacing._leaf_estimate(book, 'c1', 'p', 'store', 'store', 'a')
    assert (got, why) == (777.0, 'this arm, in this cell'), (got, why)

    # Spelled `''`, the exact rung misses and the answer comes from a looser one -- which is
    # how this would fail in production: a plausible number from the wrong place.
    empty, why_empty = pacing._leaf_estimate(book, 'c1', 'p', 'store', '', 'a')
    assert why_empty != 'this arm, in this cell', (empty, why_empty)


# ── the ladder ───────────────────────────────────────────────────────────────────────

def test_the_exact_key_wins_and_the_ladder_falls_through_in_order():
    rows = [_row('c1', 'p', 'store', 'store', 'a', 100.0),
            _row('c2', 'p', 'store', 'store', 'a', 200.0),
            _row('c2', 'p', 'store', 'store', 'b', 300.0),
            _row('c2', 'q', 'store', 'store', 'z', 400.0),
            _row('c2', 'q', 'ful', 'fulfillment', 'y', 900.0)]
    book = _book(rows)

    # 1. exact
    assert pacing._leaf_estimate(book, 'c1', 'p', 'store', 'store', 'a')[0] == 100.0
    # 2. the same arm on the same leaf, in another cell
    assert pacing._leaf_estimate(book, 'c9', 'p', 'store', 'store', 'a')[0] == 150.0
    # 3. the leaf's mean arm -- what a SELF-PACED resume answers from
    assert pacing._leaf_estimate(book, 'c9', 'p', 'store', 'store', 'new')[0] == 200.0
    # 4. the config+channel across pairs
    got, why = pacing._leaf_estimate(book, 'c9', 'newpair', 'store', 'store', 'new')
    assert got == 250.0, (got, why)
    # last: the cell, then everything
    assert pacing._leaf_estimate(book, 'c1', 'np', 'nc', 'nch', 'na')[0] == 100.0
    assert pacing._leaf_estimate(book, 'zz', 'np', 'nc', 'nch', 'na')[0] == 380.0


def test_an_empty_book_weighs_nothing_which_is_submission_order():
    """The default path. An empty book must cost nothing and change nothing."""
    assert pacing.weight_of('c1', _leaf_payload('p', 'store', 'store', 'a'), {}) == 0.0
    assert pacing.load_pace('', _LOG) == {}


def test_an_unreadable_reference_is_a_warning_not_a_failure(tmp_path):
    """A worse dispatch order, never a failed run."""
    assert pacing.load_pace(str(tmp_path / 'nope'), _LOG) == {}


def test_rows_with_no_measured_time_are_ignored():
    """A row written for an arm that never ran carries 0.0, and averaging it in would drag
    every fallback rung toward zero — which reads as "this is cheap"."""
    book = _book([_row('c1', 'p', 'store', 'store', 'a', 0.0),
                  _row('c1', 'p', 'store', 'store', 'b', 1000.0)])
    assert pacing._leaf_estimate(book, 'c1', 'p', 'store', 'store', 'b')[0] == 1000.0
    assert pacing._leaf_estimate(book, 'c1', 'p', 'store', 'store', 'a')[0] == 1000.0, (
        'the zero row must not answer for itself, nor drag the leaf mean to 500')


# ── the jobs and the cells ───────────────────────────────────────────────────────────

class _Job:
    def __init__(self, payload):
        self.payload = payload
        self.weight = 0.0


def test_weigh_jobs_stamps_in_place_and_is_a_no_op_without_a_book():
    book = _book([_row('c1', 'p', 'store', 'store', 'a', 1000.0),
                  _row('c1', 'p', 'store', 'store', 'b', 10.0)])
    jobs = [_Job(_leaf_payload('p', 'store', 'store', 'b')),
            _Job(_leaf_payload('p', 'store', 'store', 'a'))]
    pacing.weigh_jobs('c1', jobs, book)
    assert [j.weight for j in jobs] == [10.0, 1000.0]

    bare = [_Job(_leaf_payload('p', 'store', 'store', 'a'))]
    pacing.weigh_jobs('c1', bare, {})
    assert bare[0].weight == 0.0


class _Cell:
    def __init__(self, name):
        self.name = name


def test_cells_are_set_up_heaviest_first_and_the_spec_order_is_untouched():
    """Ordering the CELLS is the bigger half of the measured gain, and it is legal only
    because `cell_scope` made setup order result-neutral."""
    cells = [_Cell('light'), _Cell('heavy'), _Cell('middle')]
    book = _book([_row('light', 'p', 'store', 'store', 'a', 10.0),
                  _row('heavy', 'p', 'store', 'store', 'a', 1000.0),
                  _row('middle', 'p', 'store', 'store', 'a', 100.0)])
    got = pacing.cell_order(cells, {}, book, _LOG)
    assert [c.name for c in got] == ['heavy', 'middle', 'light']
    # the caller's list is not mutated -- the descriptor keeps spec order
    assert [c.name for c in cells] == ['light', 'heavy', 'middle']

    # NON-VACUITY: no book, no reordering.
    assert [c.name for c in pacing.cell_order(cells, {}, {}, _LOG)] == \
        ['light', 'heavy', 'middle']


def test_cell_order_prefers_the_units_it_is_given_over_the_cell_mean():
    """When the driver already knows a cell's units, the sum over them beats a mean over
    the cell: two cells can have the same mean unit and very different unit COUNTS."""
    cells = [_Cell('few'), _Cell('many')]
    book = _book([_row('few', 'p', 'store', 'store', 'a', 100.0),
                  _row('many', 'p', 'store', 'store', 'a', 100.0)])
    units = {'few': [_leaf_payload('p', 'store', 'store', 'a')],
             'many': [_leaf_payload('p', 'store', 'store', 'a') for _ in range(5)]}
    assert [c.name for c in pacing.cell_order(cells, units, book, _LOG)] == ['many', 'few']
    # ...and with no units the cell rung answers, which cannot tell them apart, so the
    # tie-break is the name -- deterministic, which is what matters.
    assert [c.name for c in pacing.cell_order(cells, {}, book, _LOG)] == ['few', 'many']
