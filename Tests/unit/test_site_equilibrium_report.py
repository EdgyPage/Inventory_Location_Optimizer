"""test_site_equilibrium_report.py — the report's two-leaf accumulation, and its refusals.

Site-dock 24.  `equilibrium.py` stays a pure rows-in library ("no CONFIG, no settings, no
run tree" — and it means it), so the REPORT is the half that walks the tree, accumulates
both leaves of a coupled pair and bands their put and receiving crews ONCE.  `_site_verdicts`
is that accumulation's consumer, and it is a module-level function over already-loaded rows
— the same shape `bucket_table` has, and the same reason it can be tested here without a
run tree.

TWO REFUSALS, and both are about a denominator rather than about a value:

  * HALF A SITE is a wrong denominator, not a small one.  `granted = crew x S x n_days` is
    the whole site crew's grant whether one channel reached the clause or two, so banding
    one leaf's seconds against it reports the site as idle.
  * TWO WINDOWS are two grants.  A leaf that closed fewer days would be banded over days it
    never ran in, and the clause would still return a pass/FAIL.

Run:  python -m pytest Tests/unit/test_site_equilibrium_report.py -q
"""
from __future__ import annotations

from Diagnostics.equilibrium_report import _site_reading, _site_verdicts

#: A working day and a band, in the shape `expectations_for` returns.
_S = 28800.0
_TOL = 0.05
_DAYS = [0, 1, 2]

#: Per channel: the seconds its crews worked in each of the three days.  LOPSIDED, so a
#: clause that banded one leaf alone could not accidentally land on the site number.
_WORKED = {'store': {'put': 9000.0, 'recv': 4000.0},
           'fulfillment': {'put': 3000.0, 'recv': 1000.0}}
_CREW = {'put': 2, 'recv': 2}


def _rows(channel):
    """`(batch_rows, work_rows)` for one leaf: one batch per work day, and one work row per
    department per day at that channel's rate."""
    batch = [{'batch_id': d, 'work_day': d, 'task_makespan': 100.0} for d in _DAYS]
    work = []
    for d in _DAYS:
        for dept, role in (('put', 'put'), ('recv', 'receive')):
            work.append({'batch_id': d, 'role': role,
                         'seconds': _WORKED[channel][dept]})
    return batch, work


def _exp(channel, *, crew=None, drop=()):
    crew = crew or _CREW
    departments = {}
    absent = {}
    for dept in ('pick', 'put', 'recv'):
        if dept in drop:
            absent[dept] = 'no site crew derived'
            continue
        if dept == 'pick':
            departments[dept] = {'crew': 5, 'expected': 0.1}
            continue
        # The RECORDED expectation is this channel's own share; the two SUM to the site's,
        # which is the identity the whole single-band argument rests on.
        share = (len(_DAYS) * _WORKED[channel][dept]) / (crew[dept] * _S * len(_DAYS))
        departments[dept] = {'crew': crew[dept], 'expected': share}
    return {'day_seconds': _S, 'band_tol': _TOL, 'departments': departments,
            'absent': absent}


def _site_rows(*, windows=None, keys=None, drop_channel=None, crews=None):
    windows = windows or {'store': _DAYS, 'fulfillment': _DAYS}
    keys = keys or {'store': 'uni_fifo', 'fulfillment': 'opt_lpt'}
    crews = crews or {'store': _CREW, 'fulfillment': _CREW}
    by_channel = {}
    for ch in ('store', 'fulfillment'):
        if ch == drop_channel:
            continue
        by_channel[ch] = (_rows(ch), _exp(ch, crew=crews[ch]), windows[ch], keys[ch])
    return {('k1', 'pairA', 0): by_channel}


def _run(site_rows):
    out = []
    return _site_verdicts(site_rows, out.append), out


# ── 1. the happy path: one band, two shares, one label ────────────────────────────

def test_a_complete_pair_is_banded_once_and_labelled_by_its_arm_pair():
    """The label is the arm PAIR in declared channel order — the same stem the site DB's
    own filename carries — because a rank is what IDENTIFIES the pair and a reader
    recognises it by its arms."""
    results, out = _run(_site_rows())
    assert list(results) == ['k1/pairA/site/uni_fifo__opt_lpt'], list(results)
    assert len(out) == 1 and 'site_utilization=ok' in out[0]


def test_the_two_halves_may_be_named_DIFFERENTLY_and_still_be_one_site():
    """The diagonal is rank against rank (site-dock 06), so the two halves of one pair carry
    different arm keys on any run whose channels sweep different rule subsets.  Keyed on the
    RANK, they reach one clause; keyed on the arm key they would each sit in their own
    bucket, every bucket would be one channel short, and the site clause would be reported
    as skipped for the entire run."""
    results, out = _run(_site_rows(keys={'store': 'uni_fifo',
                                         'fulfillment': 'ful_rank_labor'}))
    assert list(results) == ['k1/pairA/site/uni_fifo__ful_rank_labor']
    assert 'skipped' not in out[0]


def test_the_shares_are_reported_beside_the_site_number():
    results, _out = _run(_site_rows())
    reading = next(iter(results.values()))['reading']
    for dept in ('put', 'recv'):
        assert set(reading[dept]['per_channel']) == {'store', 'fulfillment'}
    line = _site_reading(type('C', (), {'reading': reading})())
    assert 's=' in line and 'f=' in line, line


# ── 2. the two refusals ───────────────────────────────────────────────────────────

def test_half_a_site_is_skipped_rather_than_banded():
    """A wrong denominator, not a small one: the grant is the whole site crew's whether one
    channel reached the clause or two."""
    results, out = _run(_site_rows(drop_channel='fulfillment'))
    assert results == {}
    assert 'only' in out[0] and 'half a site' in out[0]


def test_two_windows_are_two_grants_and_are_skipped():
    """`granted = crew x S x n_days` — a leaf that closed fewer days would be banded over
    days it never ran in, and the clause would still return a pass/FAIL."""
    results, out = _run(_site_rows(windows={'store': _DAYS,
                                            'fulfillment': [0, 1]}))
    assert results == {}
    assert 'different windows' in out[0]


def test_a_site_that_derived_two_crews_for_one_department_raises():
    """A site crew is ONE crew, derived per pair; two would mean the derivation is no longer
    per pair and the summed expectation is not a site number at all."""
    import pytest
    with pytest.raises(ValueError, match='different put crews'):
        _run(_site_rows(crews={'store': _CREW,
                               'fulfillment': {'put': 3, 'recv': 2}}))
