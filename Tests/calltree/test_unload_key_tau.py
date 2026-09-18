"""test_unload_key_tau.py -- the unload-key instrument still scores what it says it scores.

`unload_key_tau.py` is a measurement instrument, and instruments that live outside a gate here
have rotted three times (CLAUDE.md section 1). This pins the arithmetic on synthetic drains --
a key that reproduces `gain`'s order scores tau +1 and exact n/n, a reversed one scores -1 and
exact 0 -- and the per-trailer keys on a fake trailer, so the 2026-09-18 refutation can be
re-taken on the real workload with the same numbers meaning the same thing.

Run:  python -m pytest Tests/calltree/test_unload_key_tau.py -q
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unload_key_tau as ukt   # noqa: E402


def _drain(seqs, gain_order, values):
    """One drain: candidates `seqs`, gain's order, and one key value per seq."""
    return {'seqs': list(seqs),
            'gain_pos': {s: i for i, s in enumerate(gain_order)},
            'keys': {s: {k: values[s] for k in ukt.KEYS} for s in seqs}}


def test_a_key_that_matches_gain_scores_one_and_a_reversed_one_minus_one():
    agree = _drain([1, 2, 3, 4], [3, 1, 4, 2], {3: 9.0, 1: 7.0, 4: 5.0, 2: 1.0})
    reverse = _drain([1, 2, 3, 4], [3, 1, 4, 2], {3: 1.0, 1: 5.0, 4: 7.0, 2: 9.0})
    r = ukt.score([agree], 'labor')
    assert r['n'] == 1 and r['exact'] == 1 and r['top1'] == 1 and r['taus'] == [1.0]
    r = ukt.score([reverse], 'labor')
    assert r['n'] == 1 and r['exact'] == 0 and r['top1'] == 0 and r['taus'] == [-1.0]


def test_two_candidate_drains_count_for_top1_but_carry_no_tau():
    d = _drain([5, 6], [6, 5], {6: 2.0, 5: 1.0})
    r = ukt.score([d], 'pop')
    assert r == {'n': 1, 'exact': 1, 'top1': 1, 'taus': []}


def test_a_tie_breaks_by_seq_which_is_fifo():
    d = _drain([9, 4, 7], [4, 7, 9], {9: 1.0, 4: 1.0, 7: 1.0})
    assert ukt.key_order(d, 'units') == [4, 7, 9]


def test_keys_of_reads_the_remaining_load_only():
    """Units already taken off the trailer do not count; the four keys read what the
    docstring says they read."""
    def unit(freq, qty, cost):
        order = SimpleNamespace(expected_labor=freq * qty * cost, expected_popularity=freq * qty)
        return SimpleNamespace(order=order, quantity=qty)
    items = [SimpleNamespace(unit=unit(0.5, 4, 2.0)),      # taken already
             SimpleNamespace(unit=unit(0.25, 2, 3.0)),
             SimpleNamespace(unit=unit(0.1, 10, 1.0))]
    t = SimpleNamespace(pending=items, taken=1, arrived_s=120.0, seq=3)
    k = ukt.keys_of(t)
    assert k['labor'] == 0.25 * 2 * 3.0 + 0.1 * 10 * 1.0
    assert k['pop'] == 0.25 * 2 + 0.1 * 10
    assert k['units'] == 12
    assert k['fifo'] == -120.0
    assert ukt.keys_of(SimpleNamespace(pending=None, taken=0, arrived_s=None, seq=1)) == \
        {'labor': 0, 'pop': 0, 'units': 0, 'fifo': -0.0}


def test_the_report_names_every_key(capsys):
    d = _drain([1, 2, 3], [2, 1, 3], {2: 3.0, 1: 2.0, 3: 1.0})
    ukt.report([d], [1, 1, 3])
    out = capsys.readouterr().out
    assert 'depth histogram: {1: 2, 3: 1}' in out
    for name in ukt.KEYS:
        assert f'  {name:6s}' in out
