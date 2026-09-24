"""test_door_fill_asap.py — door_fill 'asap': a dock door is plugged the instant it frees or a
trailer arrives while it stands free, and which trailer takes it is decided at every plug.

The user's rule (2026-09-23): "I want dock doors to be plugged up ASAP with the decision of what
to work decided after every plug. The decision to keep the yard to a once a day update doesn't
necessarily conflict with that."  Under 'drain' (the default) the yard admits arrivals once a
site day, so a trailer arriving after the drain waits for tomorrow's beside an idle door -- the
3.4-3.8 h of every trailer's yard wait the aisle-churn study measured.

What this file pins:

  1. NOTHING ARRIVES MID-SHIFT -> WHAT PLACEMENT SEES IS THE DRAIN'S.  With a zero lead
     every trailer is in the yard at the drain, and a fifo yard ranking does not move between
     plugs, so 'asap' must hand placement the same queue stream, ledgers and transit census,
     drain by drain, and unload the same units for the same seconds.  Labour START times may
     differ, and in one direction only: under 'drain' a teammate idle on the emptying trailer
     starts the NEXT trailer at their own clock, before its door has freed (two trailers
     worked on one door); 'asap' holds them to the instant the door frees.
  2. A MID-SHIFT ARRIVAL IS PLUGGED AT ITS ARRIVAL.  'drain' leaves it in transit until the
     next drain; 'asap' stages it at dispatch + lead and unloads it in the same shift.
  3. NO RECEIVER STARTS A TRAILER BEFORE IT REACHED ITS DOOR.
  4. EVERY PLUG RE-RANKS.  Under a lifo yard, a trailer that arrived mid-shift takes the next
     freed door ahead of older standing ones; the drain-frozen ranking never saw it.
  5. THE SEAMS REFUSE what the run would not read: 'asap' without the standing yard, and
     'asap' with 'merged' crews.

Run:  python -m pytest Tests/unit/test_door_fill_asap.py -q
"""
from __future__ import annotations

import pytest

from Inbound.trailer import POSITION_VOLUME, Trailer28
from Inbound.transit import YardTransit

from Tests.unit.test_standing_yard import _dispatch, _drain, _manager, _run_scenario


# ── 1. nothing arrives mid-shift: asap is the drain ─────────────────────────────────

@pytest.mark.parametrize('cap', [None, 3])
def test_with_nothing_arriving_mid_shift_asap_is_the_drain(cap):
    runs = {}
    for fill in ('drain', 'asap'):
        tr = YardTransit(Trailer28, lead_s=0.0, doors=1, allocation='split',
                         door_team=cap, door_fill=fill)
        runs[fill] = _run_scenario(_manager(tr, crew=4), deadline=None)
    for b, (d, a) in enumerate(zip(runs['drain'], runs['asap'])):
        for key in ('queue', 'ledgers', 'transit', 'carryover'):
            assert d[key] == a[key], f'drain {b}: {key} diverged with no mid-shift arrival'
        assert d['recv_seconds'] == pytest.approx(a['recv_seconds'])
        units = lambda recs: sorted((sku, qty, round(dur, 9)) for _t0, dur, sku, qty, _w in recs)
        assert units(d['records']) == units(a['records']), f'drain {b}: different unloads'
        # asap only ever starts LATER, never earlier (the door-freed hold)
        assert sum(r[0] for r in a['records']) >= sum(r[0] for r in d['records']) - 1e-9


# ── 2 and 3. a mid-shift arrival is plugged when it arrives ───────────────────────

def _one_late_trailer(fill):
    """A trailer dispatched at 10,000 with an 8-hour lead arrives at 38,800; the drain runs at
    30,000 with a full day to go, so it arrives MID-SHIFT with both doors idle."""
    tr = YardTransit(Trailer28, lead_s=28_800.0, doors=2, allocation='split', door_fill=fill)
    mgr = _manager(tr, crew=4, skus=(101,))
    _dispatch(mgr, 101, 12, POSITION_VOLUME, 10_000.0)
    _drain(mgr, 20_000.0, deadline=28_800.0)       # departs the loader; nothing has arrived
    _drain(mgr, 30_000.0, deadline=28_800.0)       # the arrival falls inside THIS shift
    return tr, mgr


def test_drain_leaves_a_mid_shift_arrival_in_transit():
    tr, mgr = _one_late_trailer('drain')
    assert not mgr.drain_receiving_records(), 'the drain cannot see a trailer still in transit'
    assert tr.next_arrival() is not None, 'it is still on the road until tomorrow\'s drain'


def test_asap_plugs_a_mid_shift_arrival_at_its_arrival_and_nobody_starts_early():
    tr, mgr = _one_late_trailer('asap')
    recs = mgr.drain_receiving_records()
    assert recs, 'asap must unload the trailer in the shift it arrived in'
    stamps = tr.drain_stamps()
    assert len(stamps) == 1
    _seq, arrived, staged, emptied, _status = stamps[0]
    assert arrived == pytest.approx(38_800.0)
    assert staged == pytest.approx(arrived), 'plugged the instant it arrived: a door was free'
    assert emptied > staged
    arrival_local = 38_800.0 - 30_000.0
    assert min(r[0] for r in recs) >= arrival_local - 1e-9, (
        'a receiver started the trailer before it reached its door')


# ── 4. every plug re-ranks ─────────────────────────────────────────────────────────

def _lifo_scene(fill):
    """One door, a lifo yard.  A and B stand in the yard; B takes the door at the 8,000 drain.
    In the 8,500 shift C arrives at +0.2 s, while B still holds the door (B empties at ~0.69 s).
    When B empties, the plug decides between A (arrived 6,000) and C (arrived 8,500.2).

    The test harness resets the receiving crew's clocks before the working drain, as the driver
    does at every batch (`Dock.reset_clocks`)."""
    tr = YardTransit(Trailer28, lead_s=5_000.0, doors=1, allocation='split',
                     yard_policy='lifo', door_fill=fill)
    mgr = _manager(tr, crew=2, skus=(101, 102, 103))
    _dispatch(mgr, 101, 12, POSITION_VOLUME, 1_000.0)       # A: arrives 6,000
    _drain(mgr, 1_000.0, deadline=0.0)
    _dispatch(mgr, 102, 12, POSITION_VOLUME, 2_000.0)       # B: arrives 7,000
    _drain(mgr, 2_000.0, deadline=0.0)
    _dispatch(mgr, 103, 12, POSITION_VOLUME, 3_500.2)       # C: arrives 8,500.2
    _drain(mgr, 3_500.2, deadline=0.0)
    _drain(mgr, 8_000.0, deadline=0.0)                       # A, B land; lifo stages B; no labour
    tr.drain_stamps()
    mgr.receiving.dock.reset_clocks()
    _drain(mgr, 8_500.0, deadline=28_800.0)
    return [s[0] for s in sorted(tr.drain_stamps(), key=lambda s: s[2])]


def test_under_lifo_a_mid_shift_arrival_takes_the_next_freed_door():
    """'asap' re-ranks at the plug over the trailers standing THEN, so C -- the newest --
    takes the door ahead of A.  Trailers are numbered in dispatch order: A=0, B=1, C=2."""
    order = _lifo_scene('asap')
    assert order[:2] == [1, 2], f'lifo must follow B with C (arrived mid-shift); staged {order}'


def test_the_drain_frozen_ranking_never_saw_the_newcomer():
    """Non-vacuity: under 'drain' the same scene follows B with A, because C was still in
    transit at the drain and the frozen ranking held only A."""
    order = _lifo_scene('drain')
    assert order[:2] == [1, 0], f'the drain must follow B with A; staged {order}'


# ── 5. the seams ───────────────────────────────────────────────────────────────────

def test_asap_is_refused_with_merged_crews():
    with pytest.raises(ValueError, match='split'):
        YardTransit(Trailer28, doors=2, allocation='merged', door_fill='asap')


def test_an_unknown_door_fill_is_refused():
    with pytest.raises(ValueError, match='door fill'):
        YardTransit(Trailer28, doors=2, door_fill='sometimes')


def test_the_spec_refuses_asap_without_the_standing_yard():
    from Optimization.config.sim_config import CONFIG, inbound_spec
    g = CONFIG['global']
    saved = {k: g.get(k) for k in ('inbound_door_fill', 'inbound_standing_yard',
                                   'inbound_trailer_type')}
    try:
        g['inbound_door_fill'] = 'asap'
        g['inbound_standing_yard'] = False
        g['inbound_trailer_type'] = '53'
        with pytest.raises(ValueError, match='standing yard'):
            inbound_spec()
    finally:
        g.update(saved)
