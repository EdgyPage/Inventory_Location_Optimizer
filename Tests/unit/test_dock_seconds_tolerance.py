"""The site dock's seconds closure (`Inbound/receiving._seconds_agree`): float re-association
between the dock's sum and the leaves' cumulative differences grows with the magnitude, so the
check must be relative at scale -- and must still catch a missing unload."""
from __future__ import annotations

from Inbound.receiving import _seconds_agree


def test_the_400k_day_33_gap_is_the_same_labour():
    # the values that killed a unit at 400k, 2.2x demand (S16b): a 2.1e-6 s gap on 1.13e6 s
    assert _seconds_agree(1132205.888689998, 1132205.8886921294)


def test_a_small_day_still_uses_the_absolute_floor():
    assert _seconds_agree(10.0, 10.0 + 5e-7)
    assert not _seconds_agree(10.0, 10.0 + 5e-5)


def test_a_missing_unload_is_caught_at_any_scale():
    one_pack = 12.0                                   # a single unload is seconds
    assert not _seconds_agree(1_132_205.0, 1_132_205.0 - one_pack)
    assert not _seconds_agree(50_000_000.0, 50_000_000.0 - one_pack)
