"""test_fill_curve_memo.py — the fill curve's per-(group, grid day) memo moves no float.

`coverage._served_under_lead` can share a group's per-grid-day sum between calls that price
the same groups under different transit laws (`era_coverage.fill_curve`'s twelve points; the
stamp rides in as scale 1).  At the 400k reference lead that collapses 1,887 per-day passes
to 534 -- 358 s -> 109 s for the store section (`.scratch/inbound-fullscale-perf/` O9).

What this file pins:

  1. BIT-IDENTICAL: the stamp and every curve point priced with the memo equal the same
     prices without it -- `==`, not `isclose`.
  2. IT IS USED: the memoised curve prices strictly fewer per-day passes than the plain one.
  3. A STALE ENTRY CANNOT BE SERVED: re-declaring the section at a different floor and
     reusing the SAME memo gives exactly what a fresh memo gives (the group key carries
     every input the sum depends on, so a moved shelf misses).

Run:  python -m pytest Tests/unit/test_fill_curve_memo.py -q
"""
from __future__ import annotations

import random

from Optimization.simconfig import coverage as cov
from Optimization.simdriver import era_coverage as ec
from Warehouse.catalog.Order import Order

from test_coverage_rescale import _order

D = 28_800.0
LEAD = {'transit_days': 1.766, 'provenance': 'derived', 'trailer_type': '53',
        'lead_s': 28_800.0, 'lead_sigma': 0.7, 'day_seconds': D, 'releases_per_day': 1,
        'lead_unit_days': 1.0}


def _section(seed=3, n_skus=60):
    Order.next_sku = 1
    rng = random.Random(seed)
    sec = [_order(i, freq=rng.uniform(0.05, 1.0), qty=rng.choice((1.0, 2.0, 4.0, 8.0)),
                  lead=rng.choice((0.0, 1.0, 3.0)), declare=False)
           for i in range(1, n_skus + 1)]
    return sec


def _declare(sec, n, floor):
    cov.rescale_section(sec, n, coverage_days=10.0, safety_days=2.0, floor_lines=floor,
                        transit_days=ec.transit_of(LEAD)['transit_days'])


def _plain_curve(sec, n):
    pts, seen = [], set()
    for sc in sorted(set(ec.FILL_CURVE_SCALES) | {1.0}):
        law = ec.transit_of(LEAD, sc)
        t = float(law['transit_days'])
        if t not in seen:
            seen.add(t)
            pts.append({'transit_days': t,
                        'fill_rate': float(cov.fill_rate(sec, n, transit=law)['fill_rate'])})
    return sorted(pts, key=lambda p: p['transit_days'])


def test_the_memoised_curve_is_the_plain_curve_bit_for_bit(monkeypatch):
    sec, n = _section(), 3.0
    _declare(sec, n, 1.3)
    plain_stamp = cov.fill_rate(sec, n, transit=ec.transit_of(LEAD))
    plain = _plain_curve(sec, n)

    passes = {'n': 0}
    real = cov._line_pmf

    def counting(lines, upto):
        passes['n'] += 1
        return real(lines, upto)

    monkeypatch.setattr(cov, '_line_pmf', counting)
    memo: dict = {}
    stamp = cov.fill_rate(sec, n, transit=ec.transit_of(LEAD), memo=memo)
    curve = ec.fill_curve(sec, n, LEAD, memo=memo)
    memo_groups = passes['n']
    assert stamp == plain_stamp, 'the memoised stamp moved'
    assert curve == plain, 'a memoised curve point moved'

    passes['n'] = 0
    cov.fill_rate(sec, n, transit=ec.transit_of(LEAD))
    _plain_curve(sec, n)
    assert memo_groups < passes['n'], (
        f'the memo priced {memo_groups} groups against {passes["n"]} without it -- unused')


def test_a_moved_shelf_misses_the_memo_rather_than_reusing_it():
    sec, n = _section(seed=5), 3.0
    shared: dict = {}
    _declare(sec, n, 1.2)
    ec.fill_curve(sec, n, LEAD, memo=shared)
    _declare(sec, n, 2.4)                       # every floor-bound shelf moves
    reused = ec.fill_curve(sec, n, LEAD, memo=shared)
    fresh = ec.fill_curve(sec, n, LEAD, memo={})
    assert reused == fresh
