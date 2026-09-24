"""test_perf_probe.py — the inbound path's stopwatch (`Warehouse.kernel.perf_probe`).

What this file pins:

  1. THE ACCUMULATOR: spans add, counters add, a high-water mark only rises, and `drain`
     returns everything since the last drain and leaves the probe empty.
  2. THE INBOUND PATH FEEDS IT: one real standing-yard drain (the `test_standing_yard`
     scene) charges the receive's sub-spans and counts one drain, its yard depth and its
     pulls -- so a column that reads NULL in a run means "no inbound", never "the probe
     was not wired".
  3. IT MOVES NO NUMBER: the same scene run twice, once with the probe drained between
     batches and once not, leaves identical ledgers.

Run:  python -m pytest Tests/unit/test_perf_probe.py -q
"""
from __future__ import annotations

from Warehouse.kernel import perf_probe as probe


def setup_function(_fn):
    probe.drain()


def test_spans_and_counters_accumulate_and_drain_resets():
    probe.add('inb_yplan', 1.5)
    probe.add('inb_yplan', 0.25)
    probe.count('inb_drains')
    probe.count('yard_T_sum', 7)
    probe.high('yard_T_max', 4)
    probe.high('yard_T_max', 2)
    assert probe.span('inb_yplan') == 1.75
    spans, counts = probe.drain()
    assert spans == {'inb_yplan': 1.75}
    assert counts == {'inb_drains': 1, 'yard_T_sum': 7, 'yard_T_max': 4}
    assert probe.drain() == ({}, {}), 'a drain leaves the probe empty'


def _drain_scene():
    from Inbound.trailer import POSITION_VOLUME, Trailer28
    from Inbound.transit import YardTransit
    from Tests.unit.test_standing_yard import _dispatch, _drain, _manager
    tr = YardTransit(Trailer28, lead_s=0.0, doors=1, allocation='split')
    mgr = _manager(tr, crew=4, skus=(101, 102))
    _dispatch(mgr, 101, 12, POSITION_VOLUME, 1_000.0)
    _dispatch(mgr, 102, 12, POSITION_VOLUME, 1_100.0)
    return tr, mgr, _drain


def test_a_real_drain_charges_the_receive_spans_and_counts_it():
    tr, mgr, _drain = _drain_scene()
    probe.drain()
    _drain(mgr, 2_000.0, deadline=28_800.0)
    spans, counts = probe.drain()
    for name in ('inb_freeze', 'inb_pack', 'inb_yplan', 'inb_dplan', 'inb_unload',
                 'inb_handoff'):
        assert name in spans and spans[name] >= 0.0, f'{name} was not charged'
    assert counts.get('inb_drains') == 1
    assert counts.get('yard_T_max', 0) >= 1, 'the yard stood trailers at the drain'
    assert counts.get('yard_pulls', 0) >= 1, 'a door was filled off the yard ranking'


def test_the_probe_moves_no_number():
    def ledgers(drain_between):
        tr, mgr, _drain = _drain_scene()
        for day in (2_000.0, 30_000.0):
            _drain(mgr, day, deadline=28_800.0)
            if drain_between:
                probe.drain()
        return (sorted(mgr._deferred_qty.items()), mgr.queue_depth,
                [(s[0], s[1], s[2], s[3]) for s in tr.drain_stamps()])
    assert ledgers(True) == ledgers(False)
