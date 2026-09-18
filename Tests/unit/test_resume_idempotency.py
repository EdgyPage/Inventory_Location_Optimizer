"""test_resume_idempotency.py — a resumed run must be byte-identical to an uninterrupted one.

TWO defects, both found by one experiment: kill a tiny run mid-flight, resume it, and digest
the result against a clean run. It came back COMPLETE and it came back DIFFERENT, twice over.

## Defect 1 — the warehouse record was written again

A tiny run killed at 76 of 136 arms and restarted with `--resume` came back complete — and
`run_digest` still said DIFFERS:

    DIFF .../warehouse.db/aisle_type_stats   (rows 63 vs 126)   <- exactly 2x
    DIFF .../warehouse.db/warehouse_stats    (rows  1 vs   2)   <- exactly 2x

Every per-arm sim DB was clean, so the arm-level resume markers were doing their job. What
duplicated was the WAREHOUSE record: `sim_assets` re-runs pair setup on resume and called
`save_warehouse_stats` unconditionally, which did a plain INSERT and took `lastrowid` as a fresh
`warehouse_id`.

`resume-architecture-verified-sound` records that a mid-flight kill "resumes to 272/272". That is
COMPLETENESS, and it had been read as safety. It is not IDENTITY. Anything that sums
`aisle_type_stats.total_bins` reads a warehouse of twice its real size — silently, and only on
the runs that had a failure, which is the worst possible place for a defect to hide.

That distinction is load-bearing right now: accepting worker recycling rests on resume being a
safeguard, and a safeguard that quietly changes the data is not one.

Run:  python -m pytest Tests/unit/test_resume_idempotency.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from Optimization.persistence.Warehouse_Data import init_warehouse_db, save_warehouse_stats

_ROWS = [dict(handling_type='conveyable', category='food', unit_type='pallet',
              replica_count=2, eff_bins_per_aisle=10, total_bins=20,
              size_small_pct=1.0, size_medium_pct=0.0,
              size_large_pct=0.0, size_xlarge_pct=0.0)]

_KW = dict(inventory_db='inv.db', n_skus=10, n_pallet=5, n_singleton=5, total_aisles=2,
           total_bins=20, expected_fill=0.5, target_fill=0.5, max_aisles=None,
           max_bins=None, avg_eq_qty=1.0, avg_rp=1.0, aisle_rows=_ROWS)


@pytest.fixture()
def wh(tmp_path):
    p = str(tmp_path / 'warehouse.db')
    init_warehouse_db(p)
    return p


def _counts(path):
    con = sqlite3.connect(path)
    try:
        return (con.execute('SELECT COUNT(*) FROM warehouse_stats').fetchone()[0],
                con.execute('SELECT COUNT(*) FROM aisle_type_stats').fetchone()[0])
    finally:
        con.close()


def test_writing_the_same_warehouse_twice_records_it_once(wh):
    """THE DEFECT. Pair setup re-runs on `--resume`; the second call must be a no-op."""
    first = save_warehouse_stats(wh, warehouse_fingerprint='fp-abc', **_KW)
    second = save_warehouse_stats(wh, warehouse_fingerprint='fp-abc', **_KW)
    assert first == second, 'the resume must reuse the existing warehouse_id, not mint a new one'
    assert _counts(wh) == (1, 1), f'the warehouse record was duplicated: {_counts(wh)}'


def test_a_genuinely_different_warehouse_is_still_recorded(wh):
    """NON-VACUITY. A guard that swallows every second write would pass the test above."""
    a = save_warehouse_stats(wh, warehouse_fingerprint='fp-abc', **_KW)
    b = save_warehouse_stats(wh, warehouse_fingerprint='fp-OTHER', **_KW)
    assert a != b, 'two different warehouses must get two ids'
    assert _counts(wh) == (2, 2)


def test_a_null_fingerprint_still_inserts(wh):
    """An older file has no fingerprint, so it cannot be deduplicated.

    Treating NULL as "already seen" would collapse two genuinely different warehouses into one,
    which is a worse failure than the duplication this file exists to prevent.
    """
    a = save_warehouse_stats(wh, warehouse_fingerprint=None, **_KW)
    b = save_warehouse_stats(wh, warehouse_fingerprint=None, **_KW)
    assert a != b
    assert _counts(wh) == (2, 2)


def test_the_aisle_rows_belong_to_the_returned_id(wh):
    """The rows must hang off the id the caller is handed, on both the insert and the reuse."""
    wid = save_warehouse_stats(wh, warehouse_fingerprint='fp-abc', **_KW)
    save_warehouse_stats(wh, warehouse_fingerprint='fp-abc', **_KW)
    con = sqlite3.connect(wh)
    try:
        owners = [r[0] for r in con.execute('SELECT warehouse_id FROM aisle_type_stats')]
    finally:
        con.close()
    assert owners == [wid], f'aisle rows point at {owners}, not the returned id {wid}'


# ── the runtime row: an empty result must not clobber a real one ──────────────────
#
# The SECOND defect the same experiment found. Once the warehouse duplication was fixed, the
# digest still said DIFFERS, and the cause was `runtime.batches` reading 0 instead of 6 on
# exactly the arms the resume found already complete. Those arms run an EMPTY LOOP -- the marker
# working as designed -- so their result dict carries `done = 0`, and `INSERT OR REPLACE` then
# overwrote a true row from the first process with an empty one.
#
# `batches` is not in the digest's runtime exclusions, which is why it was caught. It matters
# beyond the digest: per-arm quantities are normalised by `batches`, so a resumed run would
# divide by the wrong denominator, silently, only on runs that had a failure.

# WHAT IS LEFT, AND WHY IT IS NOT A BUG. After both fixes the same experiment leaves 2 of 136
# arms at batches=0 (it was 10). Those are arms that FINISHED in a worker but whose result had
# not reached the parent when it was killed, so no row was ever written for them. `batches=0` is
# the honest reading for a process that ran nothing -- `rate` is batches/total_s and depends on
# that meaning -- and the true figure was never captured, so it cannot be reconstructed. A hard
# kill costs some telemetry. It no longer costs any SIMULATION output: every sim DB and the
# warehouse DB compare identical.


def _rt(tmp_path, arm='uni_fifo_norsl'):
    from Optimization.persistence import runtime_metrics as rm
    return rm, dict(run_root=str(tmp_path), cell='k1', pair='p', config='c',
                    channel='store', arm=arm)


def test_a_resumed_empty_arm_does_not_erase_its_real_row(tmp_path):
    rm, kw = _rt(tmp_path)
    rm.record_arm(res={'done': 6, 'elapsed': 12.0}, **kw)
    rm.record_arm(res={'done': 0, 'elapsed': 0.0}, **kw)      # the resume's empty loop
    (row,) = rm.load_rows(str(tmp_path))
    assert row['batches'] == 6, (
        f"batches read {row['batches']} after a resume; an empty result replaced a real one")


def test_an_arm_the_parent_never_recorded_still_inserts(tmp_path):
    """NON-VACUITY. A guard that simply dropped every empty result would pass the test above
    and lose an arm the parent died before recording -- a missing row is worse than a zero."""
    rm, kw = _rt(tmp_path, arm='uni_map_norsl')
    rm.record_arm(res={'done': 0, 'elapsed': 0.0}, **kw)
    rows = rm.load_rows(str(tmp_path))
    assert len(rows) == 1 and rows[0]['batches'] == 0


def test_a_real_result_still_replaces_an_earlier_real_one(tmp_path):
    """A re-run that actually ran batches must update, or resume could never correct a row."""
    rm, kw = _rt(tmp_path)
    rm.record_arm(res={'done': 3, 'elapsed': 6.0}, **kw)
    rm.record_arm(res={'done': 6, 'elapsed': 12.0}, **kw)
    (row,) = rm.load_rows(str(tmp_path))
    assert row['batches'] == 6, 'a genuine re-run must still update the row'
