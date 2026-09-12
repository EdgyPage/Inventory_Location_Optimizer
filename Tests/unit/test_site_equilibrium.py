"""test_site_equilibrium.py — ONE band over the site's put and receiving crews, and the
identity that band rests on.

`equilibrium.site_utilization_clause` exists because a coupled run fields ONE dock and ONE
pool of putters over two channel leaves, so a leaf's seconds over a site crew is a share of
a whole that channel does not own.  Banding it per leaf would publish two verdicts on one
crew that can disagree with each other — the double count reappearing as a reporting
artefact after being fixed by construction.  SITE BANDS, LEAVES REPORT.

**THE LOAD-BEARING TEST IN THIS FILE IS `test_the_site_number_equals_the_two_leaves_sum`.**
The whole argument for one site clause is that `expected_utilization` is LINEAR IN LOAD: a
department's expectation is `load / (crew x S)` by construction, and crew and day are the
SITE's — one crew, one day — so the two channels' expectations sum to the site's exactly,
with no reweighting, and the realized side sums the same way because the loads do.  The
clause's own docstring says that identity is asserted here rather than assumed.  So it is
asserted against `_dept_seconds` computed per leaf, independently, rather than against the
clause restating itself.

What the other tests pin, and what a failure in each means:

  * **The per-channel shares are inside the reading, and they are shares of the SITE
    grant.**  A right site total hides two wrong shares (put-away's site load was 0.9%
    exact while both per-channel bands failed in opposite directions), so the split is the
    only place the pool's fairness rule is visible — and its denominator is the whole site
    crew, never a per-channel one.  If a share were denominated per channel it would read
    roughly twice as high and look like a utilization of its own.
  * **A coupled LEAF drops put and recv.**  `_utilization_clause(..., departments=
    LEAF_DEPARTMENTS)` must produce a reading with `pick` and nothing else.  A leaf that
    kept them would be the second verdict the site clause exists to remove.
  * **Two channels deriving different crews for one department RAISES.**  A site crew is
    ONE crew, derived per pair; two values mean the derivation is no longer per pair and
    the summed expectation is not a site number at all.  Summing anyway is the failure mode
    that looks like a result.
  * **No channel at all raises**, rather than banding an empty site at zero.
  * **A department absent from one channel reads as `absent`.**  A band around 0.0 would
    pass a crew that did nothing and fail one that did anything, and neither is a finding.

Determinism: the per-day seconds are dealt out by a seeded `random.Random` so the window is
lumpy rather than flat — a flat window would make the sum identity hold for the trivial
reason that every day is the same.  The dealing is asserted to be seed-stable in BOTH
directions (same seed identical, different seed different) so the fixture itself cannot rot
into a constant.

Run:  python -m pytest Tests/unit/test_site_equilibrium.py -q
"""
from __future__ import annotations

import random

import pytest

from Optimization.simconfig import equilibrium as eq

#: The site's declared day and band.  Both channels of one site share them by construction —
#: one clock, one grant — which is why the clause reads them off the FIRST channel only.
S = 28800.0
TOL = 0.10

#: The window: three working days, one batch each, plus one day OUTSIDE it on every channel
#: so the windowing is doing something.
DAYS = [0, 1, 2]
OUTSIDE_DAY = 9

#: The site crews.  Different sizes on purpose: a put crew of 3 and a receiving crew of 2
#: give the two departments different grants, so a clause that crossed them would show up.
PUT_CREW = 3
RECV_CREW = 2

#: Expected utilizations per channel, per department.  The SITE expectation is their sum —
#: which is the linearity the clause rests on, so the numbers are chosen to sum to something
#: unmistakable (put 0.65, recv 0.45) rather than to a round half.
EXPECTED = {
    'store':       {'pick': 0.50, 'put': 0.40, 'recv': 0.30},
    'fulfillment': {'pick': 0.35, 'put': 0.25, 'recv': 0.15},
}

#: Per-channel pick crews — genuinely per-channel, which is why `pick` stays on the leaf.
PICKERS = {'store': 5, 'fulfillment': 4}

#: Utilizations and seconds are floats; nothing here compares one with `==`.
_TOL = 1e-9
#: Seconds totals run to ~10^5, so the sum identity is asserted at a relative-ish absolute
#: band rather than at 1e-9: dealing a total across days and adding it back is float
#: addition in a different order, which is allowed to move the last ulps.
_SEC_TOL = 1e-6


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures — one leaf's rows, and the expectations dict `expectations_for` returns
# ══════════════════════════════════════════════════════════════════════════════

def _deal(total: float, n: int, rng: random.Random) -> list[float]:
    """`total` split into `n` LUMPY positive parts that sum back to it.

    Threaded an explicit `rng` rather than drawing from the bare global: every number in
    this file is derived from these parts, so a shared global stream would make the window
    depend on test ordering.
    """
    weights = [rng.uniform(0.3, 1.7) for _ in range(n)]
    scale = total / sum(weights)
    return [w * scale for w in weights]


def _rows(channel: str, *, seed: int, put_s: float | None = None,
          recv_s: float | None = None, pick_s: float | None = None):
    """One leaf's `(batch_rows, work_rows)` — the shapes the clause actually reads.

    `batch_rows` carry `batch_id`, `work_day` and `task_makespan` (the pick department's
    seconds); `work_rows` carry `batch_id`, `role` and `seconds`, with the roles spelled the
    way `_WORK_ROLE` maps them (`put` and `receive`).  A row for a day outside the window
    and a row for a role nobody bands are both included, so "the window" and "the role
    filter" are claims with content.

    The totals default to EXACTLY the channel's expected share of the SITE grant, so the
    summed site reading lands on the summed expectation and the clause passes — which is the
    window every failure test below is a one-value perturbation of.
    """
    rng = random.Random(seed)
    put_s = EXPECTED[channel]['put'] * PUT_CREW * S * len(DAYS) if put_s is None else put_s
    recv_s = (EXPECTED[channel]['recv'] * RECV_CREW * S * len(DAYS)
              if recv_s is None else recv_s)
    pick_s = (EXPECTED[channel]['pick'] * PICKERS[channel] * S * len(DAYS)
              if pick_s is None else pick_s)
    picks = _deal(pick_s, len(DAYS), rng)
    puts = _deal(put_s, len(DAYS), rng)
    recvs = _deal(recv_s, len(DAYS), rng)
    batch_rows = [{'batch_id': d, 'work_day': d, 'task_makespan': picks[i]}
                  for i, d in enumerate(DAYS)]
    # OUTSIDE the window: a whole extra day of every department, which every reading below
    # must ignore.  Without it a clause that never filtered by day would pass everything.
    batch_rows.append({'batch_id': OUTSIDE_DAY, 'work_day': OUTSIDE_DAY,
                       'task_makespan': pick_s})
    work_rows = []
    for i, d in enumerate(DAYS):
        work_rows.append({'batch_id': d, 'role': 'put', 'seconds': puts[i]})
        work_rows.append({'batch_id': d, 'role': 'receive', 'seconds': recvs[i]})
        # A role the utilization clause bands NOTHING on.  `pick` seconds come off
        # `task_makespan`, so a work row spelled 'pick' must not be added to anything.
        work_rows.append({'batch_id': d, 'role': 'pick', 'seconds': 1_000_000.0})
    work_rows.append({'batch_id': OUTSIDE_DAY, 'role': 'put', 'seconds': put_s})
    work_rows.append({'batch_id': OUTSIDE_DAY, 'role': 'receive', 'seconds': recv_s})
    return batch_rows, work_rows


def _expectations(channel: str, *, drop: tuple = (), put_crew: int = PUT_CREW,
                  recv_crew: int = RECV_CREW) -> dict:
    """The `expectations_for` shape, for one channel — the keys the two clauses read.

    Mirrors `expectations_for`'s contract exactly: a department with no crew or no recorded
    expectation is in `absent` with a REASON rather than in `departments` at zero.
    """
    departments, absent = {}, {}
    crews = {'pick': PICKERS[channel], 'put': put_crew, 'recv': recv_crew}
    for dept in eq.DEPARTMENTS:
        if dept in drop:
            absent[dept] = f'no expected value recorded for {channel!r}'
            continue
        spec = {'crew': crews[dept], 'expected': EXPECTED[channel][dept]}
        if dept == 'pick':
            spec['s_pick'] = None
        departments[dept] = spec
    return {'pair': 'p', 'channel': channel, 'day_seconds': S, 'band_tol': TOL,
            'departments': departments, 'absent': absent,
            'flags': {'overridden': False, 'saturated': False}}


def _site(*, seeds=(11, 23), **row_over):
    """The reference coupled site: two leaves' rows and two leaves' expectations."""
    rows = {'store': _rows('store', seed=seeds[0], **row_over.get('store', {})),
            'fulfillment': _rows('fulfillment', seed=seeds[1],
                                 **row_over.get('fulfillment', {}))}
    exps = {'store': _expectations('store'),
            'fulfillment': _expectations('fulfillment')}
    return rows, exps


# ══════════════════════════════════════════════════════════════════════════════
# 0. The vocabularies, and the fixture's own determinism
# ══════════════════════════════════════════════════════════════════════════════

def test_the_site_and_leaf_department_tuples_partition_the_three():
    """Two tuples, one vocabulary.  `LEAF_DEPARTMENTS` is DERIVED from `SITE_DEPARTMENTS`,
    so a department moved to the site cannot be banded in both places — the derivation is
    the guarantee, and this pins what it currently yields."""
    assert eq.SITE_DEPARTMENTS == ('put', 'recv'), (
        f'the site crews are {eq.SITE_DEPARTMENTS!r}; picking is genuinely per channel and '
        f'coupling the pick floor is a different model')
    assert eq.LEAF_DEPARTMENTS == ('pick',), (
        f'a coupled leaf bands {eq.LEAF_DEPARTMENTS!r}')
    assert set(eq.SITE_DEPARTMENTS) | set(eq.LEAF_DEPARTMENTS) == set(eq.DEPARTMENTS)
    assert not set(eq.SITE_DEPARTMENTS) & set(eq.LEAF_DEPARTMENTS), (
        'a department banded at both scopes is the double count this clause removed')
    assert tuple(d for d in eq.DEPARTMENTS if d in eq.SITE_DEPARTMENTS) \
        == eq.SITE_DEPARTMENTS, 'SITE_DEPARTMENTS is out of DEPARTMENTS order'


def test_the_fixture_window_is_lumpy_and_seed_stable_in_both_directions():
    """The fixture guard.  A flat window would make the sum identity below hold for the
    trivial reason that every day carries the same number."""
    a = _rows('store', seed=11)
    b = _rows('store', seed=11)
    c = _rows('store', seed=12)
    assert a == b, 'the same seed dealt two different windows'
    assert a != c, 'two different seeds dealt the same window; the rng is not threaded'
    puts = [r['seconds'] for r in a[1] if r['role'] == 'put'][:len(DAYS)]
    assert max(puts) - min(puts) > 1.0, (
        f'the dealt put seconds are flat ({puts}); the sum identity would hold trivially')


# ══════════════════════════════════════════════════════════════════════════════
# 1. THE LOAD-BEARING ONE: the site number IS the two leaves' sum
# ══════════════════════════════════════════════════════════════════════════════

def test_the_site_number_equals_the_two_leaves_sum():
    """`expected_utilization` IS LINEAR IN LOAD — asserted, not assumed.

    Both sides of the identity are recomputed here from `_dept_seconds`, the same helper
    the leaf clause uses, so this compares the site clause against the leaf arithmetic
    rather than against itself.  Realized is checked against the sum of the two
    `share_of_site_grant` values because that is the form the report prints, and expected
    against the plain sum of the two channels' expectations.
    """
    rows, exps = _site()
    clause = eq.site_utilization_clause(rows, DAYS, exps)
    for dept in eq.SITE_DEPARTMENTS:
        r = clause.reading[dept]
        leaves = {ch: eq._dept_seconds(rows[ch][0], rows[ch][1], DAYS, dept)
                  for ch in rows}
        assert min(leaves.values()) > 0.0, (
            f'{dept}: a leaf contributed no seconds ({leaves}), so the sum would hold '
            f'with one side ignored')
        assert abs(r['worked_s'] - sum(leaves.values())) < _SEC_TOL, (
            f'{dept}: the site worked {r["worked_s"]!r} s but the leaves worked '
            f'{leaves} summing to {sum(leaves.values())!r}')
        shares = sum(p['share_of_site_grant'] for p in r['per_channel'].values())
        assert abs(r['realized'] - shares) < _TOL, (
            f'{dept}: the site realized {r["realized"]!r} but the per-channel shares of '
            f'the site grant sum to {shares!r}; the two must be one number')
        want = sum(EXPECTED[ch][dept] for ch in rows)
        assert abs(r['expected'] - want) < _TOL, (
            f'{dept}: the site expectation is {r["expected"]!r}, not the sum of the two '
            f'channels\' {want!r}; the summed expectation is the whole linearity claim')


def test_the_reference_site_window_sits_in_band_and_passes():
    """The passing window every failure below is a one-value perturbation of.  Built to sit
    ON the summed expectation, so a clause that summed anything else would already fail."""
    rows, exps = _site()
    clause = eq.site_utilization_clause(rows, DAYS, exps)
    assert clause.name == 'site_utilization'
    assert clause.passed, f'the reference site window failed: {clause.reason}'
    for dept, want in (('put', 0.65), ('recv', 0.45)):
        r = clause.reading[dept]
        assert abs(r['realized'] - want) < _TOL, (
            f'{dept} realized {r["realized"]!r}, expected the summed {want!r}')
        assert r['in_band'] is True
        assert abs(r['delta']) < _TOL, f'{dept} delta {r["delta"]!r} is not ~0'


def test_the_site_grant_is_one_crew_times_one_day_times_the_shared_days():
    """The grant is the SITE's, not either leaf's: one crew, the whole declared day, over
    the days the two leaves share by construction."""
    rows, exps = _site()
    clause = eq.site_utilization_clause(rows, DAYS, exps)
    for dept, crew in (('put', PUT_CREW), ('recv', RECV_CREW)):
        r = clause.reading[dept]
        assert r['crew'] == crew, f'{dept} banded a crew of {r["crew"]}, not {crew}'
        assert abs(r['granted_s'] - crew * S * len(DAYS)) < _SEC_TOL, (
            f'{dept} granted {r["granted_s"]!r} s, not {crew} x {S} x {len(DAYS)}')


def test_a_site_out_of_band_fails_and_names_the_department():
    """Non-vacuity for every passing assertion above: the clause CAN fail, and the failure
    is a site number rather than either leaf's."""
    rows, exps = _site(store={'put_s': 0.40 * PUT_CREW * S * len(DAYS) * 2.0})
    clause = eq.site_utilization_clause(rows, DAYS, exps)
    assert not clause.passed, 'a site put load doubled on one leaf stayed in band'
    assert 'put' in clause.reason, f'the reason does not name put: {clause.reason!r}'
    assert clause.reading['put']['in_band'] is False
    assert clause.reading['recv']['in_band'] is True, (
        f'doubling put moved recv too: {clause.reading["recv"]!r}')


# ══════════════════════════════════════════════════════════════════════════════
# 2. The per-channel shares ride INSIDE the site reading, of the SITE grant
# ══════════════════════════════════════════════════════════════════════════════

def test_the_per_channel_shares_are_present_for_every_channel():
    """Printed beside the site figure and never instead of it — so they have to be in the
    reading the report renders, not recomputed by whoever renders it."""
    rows, exps = _site()
    clause = eq.site_utilization_clause(rows, DAYS, exps)
    for dept in eq.SITE_DEPARTMENTS:
        per = clause.reading[dept]['per_channel']
        assert set(per) == set(rows), (
            f'{dept}: the reading carries shares for {sorted(per)}, not {sorted(rows)}')
        for ch, block in per.items():
            assert abs(block['expected'] - EXPECTED[ch][dept]) < _TOL
            assert block['worked_s'] > 0.0


def test_a_per_channel_share_is_of_the_site_grant_not_a_per_channel_one():
    """THE DENOMINATOR IS THE WHOLE SITE CREW, which this channel does not own.

    Under the reference window a channel's share equals its own expectation exactly — that
    is what "linear in load" means — while a share taken over a per-CHANNEL grant would
    read the same seconds against a smaller denominator and come out higher.  Both are
    asserted, so the test fails whichever way the denominator moves.
    """
    rows, exps = _site()
    clause = eq.site_utilization_clause(rows, DAYS, exps)
    for dept, crew in (('put', PUT_CREW), ('recv', RECV_CREW)):
        granted = crew * S * len(DAYS)
        for ch, block in clause.reading[dept]['per_channel'].items():
            assert abs(block['share_of_site_grant'] - block['worked_s'] / granted) < _TOL, (
                f'{dept}/{ch}: share {block["share_of_site_grant"]!r} is not '
                f'{block["worked_s"]!r} over the site grant {granted!r}')
            assert abs(block['share_of_site_grant'] - EXPECTED[ch][dept]) < _TOL, (
                f'{dept}/{ch}: the reference window puts this channel exactly at its own '
                f'expectation {EXPECTED[ch][dept]!r}, but it read '
                f'{block["share_of_site_grant"]!r}')
    # And the site share is genuinely bigger than either leaf's, which is the whole reason
    # a per-leaf band would have disagreed with the site's.
    for dept in eq.SITE_DEPARTMENTS:
        r = clause.reading[dept]
        biggest = max(p['share_of_site_grant'] for p in r['per_channel'].values())
        assert r['realized'] > biggest + _TOL, (
            f'{dept}: the site realized {r["realized"]!r} is not above every leaf share '
            f'({biggest!r}); the shares are not adding up to it')


# ══════════════════════════════════════════════════════════════════════════════
# 3. A coupled LEAF drops put and recv
# ══════════════════════════════════════════════════════════════════════════════

def test_a_coupled_leaf_clause_bands_pick_alone():
    """SITE BANDS, LEAVES REPORT.  A leaf that kept put and recv would publish a second
    verdict on one crew that can disagree with the site's."""
    batch_rows, work_rows = _rows('store', seed=11)
    clause = eq._utilization_clause(batch_rows, work_rows, DAYS, _expectations('store'),
                                    departments=eq.LEAF_DEPARTMENTS)
    assert set(clause.reading) == {'pick'}, (
        f'a coupled leaf banded {sorted(clause.reading)}; only pick is the leaf\'s')
    assert clause.reading['pick']['in_band'] is True
    assert abs(clause.reading['pick']['realized'] - EXPECTED['store']['pick']) < _TOL


def test_the_default_leaf_clause_still_bands_all_three():
    """Non-vacuity for the narrowing: UNCOUPLED behaviour is untouched, so the `departments`
    argument has to be what removed them rather than an expectation that went missing."""
    batch_rows, work_rows = _rows('store', seed=11)
    clause = eq._utilization_clause(batch_rows, work_rows, DAYS, _expectations('store'))
    assert set(clause.reading) == set(eq.DEPARTMENTS), (
        f'the default leaf clause banded {sorted(clause.reading)}')
    for dept in eq.DEPARTMENTS:
        assert clause.reading[dept]['in_band'] is True, (
            f'{dept} out of band on the reference leaf window: {clause.reading[dept]!r}')


def test_the_leaf_and_site_clauses_mean_the_same_thing_by_worked():
    """One helper, two clauses — `_dept_seconds` was factored out precisely so the leaf and
    the site cannot drift into different definitions of 'worked'."""
    batch_rows, work_rows = _rows('store', seed=11)
    leaf = eq._utilization_clause(batch_rows, work_rows, DAYS, _expectations('store'))
    for dept in eq.DEPARTMENTS:
        want = eq._dept_seconds(batch_rows, work_rows, DAYS, dept)
        assert abs(leaf.reading[dept]['worked_s'] - want) < _SEC_TOL, (
            f'{dept}: the leaf clause worked {leaf.reading[dept]["worked_s"]!r}, '
            f'_dept_seconds says {want!r}')


# ══════════════════════════════════════════════════════════════════════════════
# 4. The refusals, and the absence that is not one
# ══════════════════════════════════════════════════════════════════════════════

def test_two_channels_deriving_different_crews_for_one_department_raises():
    """A site crew is ONE crew, derived per pair.  Two values mean the derivation is no
    longer per pair, and the summed expectation is then not a site number at all — summing
    anyway would produce a plausible figure for a warehouse that does not exist."""
    rows, exps = _site()
    exps['fulfillment']['departments']['put']['crew'] = PUT_CREW + 1
    with pytest.raises(ValueError, match=r'different put crews'):
        eq.site_utilization_clause(rows, DAYS, exps)


def test_a_crew_disagreement_on_receiving_raises_too():
    """Same rule, the other site crew — so the check is over the department loop rather
    than hardcoded at put."""
    rows, exps = _site()
    exps['store']['departments']['recv']['crew'] = RECV_CREW + 2
    with pytest.raises(ValueError, match=r'different recv crews'):
        eq.site_utilization_clause(rows, DAYS, exps)


def test_an_empty_rows_by_channel_raises():
    """A site with no channel has no crew to band.  The report accumulates both leaves
    before calling this, so an empty mapping is an accumulation that found nothing — which
    must not read as a site that worked zero seconds."""
    with pytest.raises(ValueError, match='no channel has no crew to band'):
        eq.site_utilization_clause({}, DAYS, {})


def test_a_department_absent_from_one_channel_reads_as_absent_rather_than_banding():
    """A band around 0.0 would pass a crew that did nothing and fail one that did anything,
    and neither is a finding.  The absence names the CHANNEL it came from, because half a
    site expectation is not a smaller expectation, it is a wrong one."""
    rows, exps = _site()
    exps['fulfillment'] = _expectations('fulfillment', drop=('put',))
    clause = eq.site_utilization_clause(rows, DAYS, exps)
    put = clause.reading['put']
    assert set(put) == {'absent'}, (
        f'put was banded despite one channel having no expectation: {put!r}')
    assert 'fulfillment' in put['absent'], (
        f'the absence does not name the channel it came from: {put["absent"]!r}')
    assert clause.reading['recv']['in_band'] is True, (
        'a missing put expectation took receiving down with it')
    assert clause.passed, (
        f'an absent department failed the clause rather than being reported: '
        f'{clause.reason!r}')


if __name__ == '__main__':                                        # pragma: no cover
    import sys
    sys.exit(pytest.main([__file__, '-v']))
