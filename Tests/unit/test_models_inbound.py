"""The inbound flow model (`Optimization/simconfig/models/inbound.py`): the next-fit pallet law
against the loader it summarises, the trailer arithmetic, and the crew composition.  The grid
verification (predicted trailers/day within 2% of the site yard) lives in the study (S04c)."""
from __future__ import annotations

import math
import random

import pytest

from Inbound.trailer import POSITION_VOLUME, LoadPallet, Trailer53
from Optimization.simconfig.models import inbound


def test_constants_match_the_trailer_model():
    assert inbound.PALLET_VOLUME == POSITION_VOLUME
    assert inbound.POSITIONS_53 == Trailer53.pallet_positions


def _next_fit_pallets(items):
    """The loader's own rule, item by item: `LoadPallet.add` until an item does not fit."""
    n, pal = 1, LoadPallet()
    for v in items:
        if pal.add(0, 1, v) == 0:
            n, pal = n + 1, LoadPallet()
            assert pal.add(0, 1, v) == 1
    return n


def test_next_fit_law_matches_the_loader():
    rng = random.Random(9)
    items = [int(rng.lognormvariate(math.log(1_200), 0.9)) + 1 for _ in range(60_000)]
    items = [v for v in items if v < POSITION_VOLUME]
    V, ev, ev2 = inbound.item_moments([(1, v) for v in items])
    r = inbound.INBOUND.evaluate({'V': V, 'e_v': ev, 'e_v2': ev2, 'positions': 26,
                                  'releases': 1, 'cv': 0.0, 'recv_load': 0.0, 'put_load': 0.0,
                                  'S': 28_800.0, 'rho_recv': 1.0, 'rho_put': 1.0})
    # the renewal form's residual E[v^2]/2E[v] under-counts next-fit's waste on a heavy tail
    # by ~2% (lognormal sigma 0.9 here; the grid read 1-2% low too) -- a named bias, held at 3%
    loader = _next_fit_pallets(items)
    assert r['pallets'] == pytest.approx(loader, rel=0.03)
    assert r['pallets'] <= loader


def test_each_release_ships_its_ceiling():
    # no spread: exactly the ceiling of each release's share
    assert inbound.expected_trailers(0.84, 0.0) == 1.0
    assert inbound.expected_trailers(3.2, 0.0, releases=2) == 2 * math.ceil(1.6)
    # a wide day-to-day spread on a busy dock: x + r/2 (the uniform fractional part)
    assert inbound.expected_trailers(25.6, 0.3) == pytest.approx(25.6 + 0.5, abs=0.02)
    # E[ceil] = sum_k P(X > k) against a Monte Carlo of the same normal
    rng = random.Random(1)
    draws = [max(0.0, rng.gauss(1.4, 0.3 * 1.4)) for _ in range(200_000)]
    assert inbound.expected_trailers(1.4, 0.3) == pytest.approx(
        sum(math.ceil(d) for d in draws) / len(draws), abs=0.01)


def test_crews_are_the_records_crew_size():
    r = inbound.INBOUND.evaluate({'V': 0.0, 'e_v': 0.0, 'e_v2': 0.0, 'positions': 26,
                                  'releases': 1, 'cv': 0.0, 'recv_load': 1_335_412.0,
                                  'put_load': 100.0, 'S': 28_800.0, 'rho_recv': 0.85,
                                  'rho_put': 0.85})
    assert r['recv_crew'] == math.ceil(1_335_412.0 / (28_800 * 0.85))
    assert r['put_crew'] == 1
    assert r['pallets'] == 0.0
