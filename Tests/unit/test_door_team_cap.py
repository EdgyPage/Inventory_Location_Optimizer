"""test_door_team_cap.py — the door-team cap is trailer physics, and off it changes nothing.

`INBOUND_DOOR_TEAM` declares how many receivers can support ONE trailer's unload and pack at
once (inbound-optimization, "Cap the door team"; the physics comes from "Decide the contention
regime under the derived crew", decision 3).  Every worker is additive and the steps inside an
unload are not modelled, so the cap is what stands in for the liftgate, the trailer aisle and
the pack bench.

What this file pins, in the order the risk runs:

  1. THE DEAL IS EVEN, THEN CUT.  `partition` keeps its round-robin split and each team is
     truncated at the cap, so 22 receivers over three staged trailers is 8/7/7 (the cap does
     not bind) and over two is 10/10 with two standing idle (it does).  A GREEDY deal —
     10/10/2 — would be a different rule, and the one 25 rejected: it makes the cap bind at
     every door count and turns dock priority into a capacity grant.
  2. BOTH ALLOCATION MODES.  The cap belongs to the TRAILER, so 'merged' under a cap is one
     team of `cap` on one trailer, not the whole gang.
  3. THE REASSIGNMENT RESPECTS THE CAP.  A freed team SPREADS over the (1)-(2)-(3) targets,
     each taking up to its own room, instead of being handed whole to one — so a freed team
     idles rather than pushing a trailer that is already at the cap to twice it, which is what
     the pre-cap step (3) would have done.
  4. BYTE-IDENTICAL WITH THE CAP OFF, drain by drain against the uncapped dealing — the same
     comparison shape the spread-zero path is proven with, never aggregates (memory
     `lockstep-tests-compare-aggregates-only`).
  5. THE SPEC SEAM refuses a cap the run would not read: a cap without the standing yard is
     the v1 drain, which never deals teams at all.

Run:  python -m pytest Tests/unit/test_door_team_cap.py -q
"""
from __future__ import annotations

import pytest

from Inbound.trailer import POSITION_VOLUME, Trailer28
from Inbound.transit import TrailerTransit, YardTransit

from Tests.unit.test_standing_yard import (
    _dispatch, _drain, _ledgers, _manager, _queue_stream, _run_scenario)


def _teams_of(recs) -> dict:
    """{sku: {worker indices that charged it}} — who worked which trailer."""
    by: dict = {}
    for _t0, _dur, sku, _qty, w in recs:
        by.setdefault(sku, set()).add(w)
    return by


def _deal(*, doors, crew, cap, skus, qty=120):
    """The DEAL alone: `{sku: [worker, ...]}` as dealt at ctx-freeze.

    Read through a one-start drain (`deadline=1e-9`).  The whistle is a START gate, so every
    dealt worker begins exactly one unload on the trailer it was dealt to and nothing empties
    — which is the only way to observe the deal itself.  Reading a full drain instead measures
    the deal CONVOLVED with every reassignment, and the two are different claims: a worker that
    appears on two trailers there was dealt to one of them.

    Each trailer is one full Trailer28 of 20 packs, comfortably more than any team here, so a
    short team is a dealing fact rather than a trailer that ran out of work.
    """
    tr = YardTransit(Trailer28, lead_s=0.0, doors=doors, allocation='split', door_team=cap)
    mgr = _manager(tr, crew=crew, skus=skus)
    epoch = 10_000.0
    for sku in skus:
        _dispatch(mgr, sku, qty, POSITION_VOLUME // 10, epoch)
    _drain(mgr, epoch, deadline=1e-9)
    return {sku: sorted(w) for sku, w in _teams_of(mgr.drain_receiving_records()).items()}


# ── 1. the deal: even, then cut ───────────────────────────────────────────────────

def test_the_deal_stays_even_when_the_cap_does_not_bind():
    """22 receivers, three staged trailers, cap 10: the even split is 8/7/7, every team is
    under the cap, and nobody idles.  A GREEDY deal would read 10/10/2 here — which is what
    the ticket's acceptance line said and what its rule, and 25's decision, do not."""
    dealt = _deal(doors=3, crew=22, cap=10, skus=(101, 102, 103))
    assert sorted(len(w) for w in dealt.values()) == [7, 7, 8], (
        f'the deal must stay EVEN and then be cut, never greedily filled: got '
        f'{ {s: len(w) for s, w in dealt.items()} }')
    assert set().union(*map(set, dealt.values())) == set(range(22)), (
        'every receiver must have been dealt — at 8/7/7 the cap of 10 binds on nobody')


def test_the_cap_binds_when_the_even_split_would_exceed_it():
    """The same crew over TWO doors: even is 11/11, the cap cuts both to 10, and exactly two
    receivers are dealt to nobody and stand idle for the drain."""
    dealt = _deal(doors=2, crew=22, cap=10, skus=(101, 102))
    assert sorted(len(w) for w in dealt.values()) == [10, 10], 'the cap must cut to 10/10'
    idle = set(range(22)) - set().union(*map(set, dealt.values()))
    assert len(idle) == 2, f'two receivers must stand idle; {len(idle)} did'


def test_the_same_crew_and_doors_uncapped_deals_everyone():
    """Non-vacuity for the test above: without the cap the identical scenario deals 11/11 and
    nobody idles, so the idling is the CAP and not the fixture."""
    dealt = _deal(doors=2, crew=22, cap=None, skus=(101, 102))
    assert sorted(len(w) for w in dealt.values()) == [11, 11]
    assert set().union(*map(set, dealt.values())) == set(range(22))


# ── 2. the cap belongs to the trailer, not the dealing rule ───────────────────────

def test_merged_under_a_cap_is_one_team_of_cap():
    """'merged' is a pooled gang on ONE trailer at a time, so a cap there means the gang IS
    `cap` receivers and the rest of the crew idles — the cap is a property of the trailer."""
    tr = YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='merged', door_team=3)
    mgr = _manager(tr, crew=10, skus=(101, 102))
    epoch = 10_000.0
    for sku in (101, 102):
        _dispatch(mgr, sku, 120, POSITION_VOLUME // 10, epoch)
    _drain(mgr, epoch)
    worked = {w for _t0, _d, _sku, _q, w in mgr.drain_receiving_records()}
    assert worked == {0, 1, 2}, f'the merged gang must be capped at 3, got {sorted(worked)}'


def test_merged_uncapped_still_uses_the_whole_crew():
    """Non-vacuity for the merged cap: the same scenario uncapped employs all ten."""
    tr = YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='merged')
    mgr = _manager(tr, crew=10, skus=(101, 102))
    epoch = 10_000.0
    for sku in (101, 102):
        _dispatch(mgr, sku, 120, POSITION_VOLUME // 10, epoch)
    _drain(mgr, epoch)
    worked = {w for _t0, _d, _sku, _q, w in mgr.drain_receiving_records()}
    assert worked == set(range(10))


# ── 3. the reassignment respects the cap ──────────────────────────────────────────

def _two_trailer_reassignment(cap):
    """T0 empties early with NO replacement in the yard, so the freed team can only go to
    T1 — which already has a team.  That is reassignment step (3), the one the pre-cap code
    extended unconditionally.  Returns {sku: [worker, ...]}."""
    tr = YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split', door_team=cap)
    mgr = _manager(tr, crew=4, skus=(101, 102))
    epoch = 10_000.0
    _dispatch(mgr, 101, 12, POSITION_VOLUME, epoch)            # T0: 2 packs, empties first
    _dispatch(mgr, 102, 120, POSITION_VOLUME // 10, epoch)     # T1: 20 packs, still standing
    _drain(mgr, epoch)
    return {sku: sorted(w) for sku, w in _teams_of(mgr.drain_receiving_records()).items()}


def test_a_freed_team_idles_rather_than_pushing_a_trailer_past_its_cap():
    """crew=4, cap=2: the deal is 2/2, and when T0 empties there is nowhere legal for its
    team to go — T1 is already at the cap — so the two freed receivers idle.  This is step
    (3), where the pre-cap code extended the target's team unconditionally."""
    dealt = _two_trailer_reassignment(cap=2)
    assert dealt[101] == [0, 2] and dealt[102] == [1, 3], (
        f'the freed team joined a trailer already at its cap: {dealt}')


def test_the_same_scenario_uncapped_does_reassign():
    """Non-vacuity: uncapped, T0's freed team joins T1 exactly as it always did — so the
    test above pins the CAP, not a scenario in which no reassignment ever fires."""
    dealt = _two_trailer_reassignment(cap=None)
    assert dealt[102] == [0, 1, 2, 3], (
        f'the freed team never joined the standing trailer: {dealt}')


def test_a_freed_team_fills_a_fresh_door_up_to_the_cap():
    """doors=2, crew=6, cap=3, three trailers: T0 empties, the yard-pull stages T2, and T0's
    freed team of three takes it — three receivers on the new door, never six, and drawn from
    T0's own team rather than from T1's."""
    tr = YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split', door_team=3)
    mgr = _manager(tr, crew=6)
    epoch = 10_000.0
    per = POSITION_VOLUME
    _dispatch(mgr, 101, 12, per, epoch)            # T0: few packs, empties first
    _dispatch(mgr, 102, 120, per // 10, epoch)     # T1: ~10x the packs
    _dispatch(mgr, 103, 60, per // 5, epoch)       # T2: waits in the yard
    _drain(mgr, epoch)
    teams = _teams_of(mgr.drain_receiving_records())
    assert len(teams[103]) == 3, (
        f'the reassignment ignored the cap on the fresh door: {sorted(teams[103])}')
    assert teams[103].isdisjoint(teams[102]), (
        "the new door drew receivers off the standing trailer's team, not off the freed one")


# ── 4. the cap OFF is byte-identical, drain by drain ──────────────────────────────

@pytest.mark.parametrize('allocation', ('split', 'merged'))
def test_the_uncapped_path_is_byte_identical_to_an_explicit_none(allocation):
    """`door_team=None` must run the former dealing VERBATIM, not an equivalent of it: every
    record, the queue stream, both ledgers and the censuses, compared drain by drain against
    a transit constructed exactly as the pre-cap driver constructed one."""
    before = _manager(YardTransit(Trailer28, lead_s=0.0, doors=2, allocation=allocation),
                      crew=3)
    after = _manager(YardTransit(Trailer28, lead_s=0.0, doors=2, allocation=allocation,
                                 door_team=None), crew=3)
    got_b, got_a = _run_scenario(before), _run_scenario(after)
    for b, (x, y) in enumerate(zip(got_b, got_a)):
        assert y['records'] == x['records'], f'drain {b}: unload records diverged'
        assert y['snapshot'] == x['snapshot'], f'drain {b}: (depth, unloaded, cut, s)'
        assert y['transit'] == x['transit'], f'drain {b}: transit census diverged'
        assert y['carryover'] == x['carryover'], f'drain {b}: carryover diverged'
        assert y['queue'] == x['queue'], f'drain {b}: the put-queue STREAM diverged'
        assert y['ledgers'] == x['ledgers'], f'drain {b}: a ledger diverged'
    assert sum(len(d['records']) for d in got_b) > 0, 'the scenario did no work'


def test_a_cap_above_the_crew_is_also_byte_identical():
    """A cap nobody can reach must cost nothing: cap 99 against a crew of 3 deals exactly as
    uncapped, which is what makes the truncation a no-op rather than a reordering."""
    plain = _manager(YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split'), crew=3)
    wide = _manager(YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split',
                                door_team=99), crew=3)
    for b, (x, y) in enumerate(zip(_run_scenario(plain), _run_scenario(wide))):
        assert y['records'] == x['records'], f'drain {b}'
        assert y['queue'] == x['queue'] and y['ledgers'] == x['ledgers'], f'drain {b}'


def test_a_binding_cap_really_does_move_the_labour():
    """Non-vacuity for both lockstep tests above: with a cap that BITES, the records differ —
    so the equalities are a property of the off path, not of the comparison."""
    plain = _manager(YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split'), crew=4)
    capped = _manager(YardTransit(Trailer28, lead_s=0.0, doors=2, allocation='split',
                                  door_team=1), crew=4)
    got_p, got_c = _run_scenario(plain), _run_scenario(capped)
    assert any(y['records'] != x['records'] for x, y in zip(got_p, got_c)), (
        'a cap of one changed no labour stamp — the fixture never dealt a team of two')
    # ...and it stays LABOUR: the handoff is canonical, so placement physics never moves.
    for b, (x, y) in enumerate(zip(got_p, got_c)):
        assert y['queue'] == x['queue'], (
            f'drain {b}: the cap moved the put-queue STREAM — that is placement physics')
        assert y['ledgers'] == x['ledgers'], f'drain {b}: the cap moved a ledger'


def test_the_v1_transit_never_reads_the_cap():
    """The non-standing path has no `door_team` attribute at all, so `_receive` reads None
    through its getattr and the v1 drain is untouched by construction."""
    assert not hasattr(TrailerTransit(Trailer28, lead_s=0.0), 'door_team')


# ── 5. the spec seam ──────────────────────────────────────────────────────────────

def test_a_cap_without_the_standing_yard_fails_loudly(monkeypatch):
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'recv_crew_size', 2)
    monkeypatch.setitem(g, 'inbound_door_team', 10)
    monkeypatch.setitem(g, 'inbound_standing_yard', False)
    with pytest.raises(ValueError, match='INBOUND_DOOR_TEAM'):
        inbound_spec()


def test_a_zero_cap_fails_loudly(monkeypatch):
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'recv_crew_size', 2)
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_door_team', 0)
    with pytest.raises(ValueError, match='at least 1 receiver'):
        inbound_spec()
    with pytest.raises(ValueError, match='at least 1 receiver'):
        YardTransit(Trailer28, door_team=0)


def test_a_wellformed_cap_rides_the_spec(monkeypatch):
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    monkeypatch.setitem(g, 'inbound_trailer_type', '28')
    monkeypatch.setitem(g, 'recv_crew_size', 2)
    monkeypatch.setitem(g, 'inbound_standing_yard', True)
    monkeypatch.setitem(g, 'inbound_door_team', 10)
    assert inbound_spec()['door_team'] == 10


def test_the_declared_default_is_uncapped():
    """The knob's seams are pinned for the whole family by `test_inbound_params.py` (the key
    list IS `CONFIG`'s inbound keys, every key has a flag, every flag defaults from CONFIG),
    so what is left to assert here is the one thing specific to this knob: its declared
    default is None. That is what makes every archived run, and every run that does not ask
    for the cap, byte-identical rather than merely close (memory
    `config-knob-has-five-seams` for why a default that looks deliberate is the hazard)."""
    from Optimization.config import settings as _s
    from Optimization.config.sim_config import CONFIG
    assert _s.INBOUND_DOOR_TEAM is None
    assert CONFIG['global']['inbound_door_team'] is None


# ── 6. the reported metric: the dock's parallelism ceiling ────────────────────────

class _Ctx:
    """The two things `dock_ceiling` reads, and nothing else: the stamped run values and the
    staffing record's derived receiving crew."""

    def __init__(self, cap, doors, crew):
        self.sim_result = {'inbound_door_team': cap, 'inbound_dock_doors': doors}
        self._crew = crew

    def staffing_expectations(self):
        if self._crew is None:
            return None
        return {'day_seconds': 28800.0, 'band_tol': 0.1,
                'departments': {'recv': {'crew': self._crew, 'expected': 0.8}},
                'absent': {}, 'flags': {}}

    # The REAL accessor, borrowed rather than reimplemented: a stub that computed the
    # ceiling itself would pass while the shipped one was wrong.
    def dock_ceiling(self):
        from Optimization.Performance_Evaluations.core.context import EvalContext
        return EvalContext.dock_ceiling(self)


def _ceiling(cap, doors, crew):
    from Optimization.Performance_Evaluations.core.context import EvalContext
    return EvalContext.dock_ceiling(_Ctx(cap, doors, crew))


def test_the_dock_ceiling_is_cap_times_doors_over_the_crew():
    """The campaign's own numbers: ten receivers a door, four doors, a derived crew of 22 —
    the dock can seat 40 of 22, so the cap never binds on the crew as a whole and a receiving
    utilization below its band is not the dock's doing."""
    assert _ceiling(10, 4, 22) == pytest.approx(40.0 / 22.0)
    # ...and below 1.0 the dock IS the narrower resource: two doors seat 20 of 22.
    assert _ceiling(10, 2, 22) == pytest.approx(20.0 / 22.0)


def test_an_uncapped_or_unrecorded_run_has_no_ceiling():
    """None, never a number: an uncapped dock seats everyone, and every run before the cap
    existed was uncapped — so None is honest for both and needs no fallback that says so."""
    assert _ceiling(None, 4, 22) is None
    assert _ceiling(10, None, 22) is None
    assert _ceiling(10, 4, None) is None, 'flag-off derives no crew, so there is no share'


def test_the_receiving_row_reports_the_ceiling_and_never_bands_it():
    """The audit's receiving row carries the ceiling beside its band reading ("Decide the
    contention regime under the derived crew", 4).  It is a REPORT: the row's in-band/below
    verdict is unchanged by it, and it prints whether or not it binds — "the dock could seat
    everyone" is what makes a below-band reading damning rather than explained."""
    from Optimization.Performance_Evaluations.throughput import audit
    from Optimization.simconfig import equilibrium as _eq
    import pandas as pd

    util = _eq.Clause('utilization', True,
                      {'pick': {'crew': 2, 'expected': 0.30, 'realized': 0.31,
                                'delta': 0.01, 'in_band': True},
                       'put': {'absent': 'no site crew derived'},
                       'recv': {'crew': 22, 'expected': 0.85, 'realized': 0.60,
                                'delta': -0.25, 'in_band': False}})
    empty = {'supply': _eq.Clause('supply', True, {}),
             'labour': _eq.Clause('labour', True, {}),
             'rework': _eq.Clause('rework', True, {})}
    verdict = _eq.Verdict(False, 0, 1, {'utilization': util, **empty})
    exp = {'day_seconds': 28800.0, 'band_tol': 0.10,
           'departments': {'pick': {'crew': 2, 'expected': 0.30},
                           'recv': {'crew': 22, 'expected': 0.85}},
           'absent': {'put': 'no site crew derived'}, 'flags': {}}
    sdf = pd.DataFrame({'day': [0], 'capped': [0], 'overtime_s': [0.0]})

    class _AuditCtx(_Ctx):
        base = {'key': 'other'}

    def _recv_reading(cap, doors):
        ctx = _AuditCtx(cap, doors, 22)
        rows = audit._rows(ctx, {'key': 'arm', 'label': 'arm'}, sdf, verdict, exp)
        return next(r[-1] for r in rows if r[-6] == 'receiving')

    assert _recv_reading(10, 2) == 'below band · dock seats 91%'
    assert _recv_reading(10, 4) == 'below band · dock seats 182%'
    assert _recv_reading(None, 4) == 'below band', 'an uncapped run must gain no suffix'


def test_the_receiver_busy_share_is_denominated_in_work_days():
    """Receiver-seconds over crew × day_seconds × WORK DAYS — the same grant the equilibrium
    check measures against, so the two reports cannot mean different things by "busy".

    The denominator is the whole test.  The sim clock only advances through working hours, so
    an arm of ten 8-hour work days spans 3.3 CALENDAR days; denominating on the calendar span
    (which is right for DOOR utilization, and was reached for here) over-reads the share by
    three times and prints an impossible number.  It printed 184% on a real run before this
    was fixed.  So the fixture is built so the two denominators disagree, and only the work-day
    one gives the answer arithmetic demands.
    """
    import pandas as pd
    from Optimization.Performance_Evaluations.yard import scorecard

    S = 28800.0          # an 8-hour day inside a 24-hour calendar day

    class _BusyCtx(_Ctx):
        def __init__(self, crew, recv_seconds, work_days):
            super().__init__(10, 4, crew)
            self._recv, self._days = recv_seconds, work_days

        def batch_df(self, key):
            return pd.DataFrame({'recv_seconds': self._recv, 'work_day': self._days})

    # 22 receivers × 28800 s × 10 work days granted; 2 days' worth of crew-seconds worked.
    ctx = _BusyCtx(22, [22 * S, 22 * S] + [0.0] * 8, list(range(10)))
    assert scorecard._receiver_busy(ctx, 'arm') == '20%', (
        'the share must be over crew × day_seconds × work days; a calendar-span denominator '
        'reads 3x high here')
    # Distinct days, not batches: two batches on one day is still one day's grant.
    two_a_day = _BusyCtx(1, [S / 2, S / 2], [0, 0])
    assert scorecard._receiver_busy(two_a_day, 'arm') == '100%'
    # '-' rather than 0%: "not measured" is a different and weaker claim than "measured, and
    # it was nothing".
    assert scorecard._receiver_busy(_BusyCtx(None, [1.0], [0]), 'arm') == '-'
    assert scorecard._receiver_busy(_BusyCtx(22, [], []), 'arm') == '-'


def test_the_scorecard_row_still_matches_its_header():
    """Two columns were added to a table whose header and rows are built in different
    places; a row of the wrong width renders as a silently shifted table rather than an
    error.  Also pins that the door count is read from the RUN's record, not re-derived
    from the drain frame — the ceiling is computed from the recorded one, and two door
    counts in one table is what that fallback used to risk."""
    import pandas as pd
    from Optimization.Performance_Evaluations.yard import scorecard

    class _RowCtx(_Ctx):
        def __init__(self):
            super().__init__(10, 4, 22)

        def batch_df(self, key):
            return pd.DataFrame({'recv_seconds': [1000.0, 2000.0],
                                 'work_day': [0, 1],
                                 'batch_start_time': [0.0, 28800.0],
                                 'duration': [28800.0, 28800.0]})

    tdf = pd.DataFrame({'censored': [0, 1], 'door_span_days': [0.5, 0.25]})
    # `free_doors_start` maxes at 2 here while the run RECORDED 4 doors: the recorded value
    # must win, or a resumed arm prints a door count below the one the ceiling used.
    ddf = pd.DataFrame({'free_doors_start': [2, 1], 'yard_start': [3, 5],
                        'yard_end': [0, 1], 'staged_remainder_end': [0, 0],
                        'binding_cut': [False, True]})
    row = scorecard._row(_RowCtx(), {'key': 'arm', 'label': 'arm'}, tdf, ddf)
    assert len(row) == len(scorecard._COLS), (
        f'the row is {len(row)} cells against {len(scorecard._COLS)} headers')
    cells = dict(zip(scorecard._COLS, row))
    assert cells['doors'] == '4', 'the recorded door count must win over the derived one'
    assert cells['dock ceiling'] == '182%'
    assert cells['receiver busy'].endswith('%')


def test_every_phase2_axis_entry_states_the_cap():
    """`_inbound_axis` refuses a partial entry, and the inbound-OFF anchor must CLEAR the cap
    rather than inherit it — `inbound_spec()` refuses a cap without the standing yard, so an
    anchor carrying it would raise at spec build instead of running."""
    from Optimization.config.whatif_config import (PHASE2_DOOR_TEAM, PILOT_RUN_DEFAULTS,
                                                   phase2_inbound_axis)
    axis = phase2_inbound_axis()
    assert PILOT_RUN_DEFAULTS['inbound_door_team'] == PHASE2_DOOR_TEAM
    for name, over in axis:
        assert 'door_team' in over, f'entry {name!r} does not state door_team'
        assert over['door_team'] == (None if name == 'inb_off' else PHASE2_DOOR_TEAM)
