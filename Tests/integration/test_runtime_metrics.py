"""test_runtime_metrics.py

Locks the runtime-metrics capture:
  - runtime_metrics.record_arm writes one row per arm (arm name parsed to initial/assignment,
    rate = batches/total_s, section totals persisted) and load_rows round-trips it;
  - the (cell,pair,config,channel,arm) key is UNIQUE — a re-run/resume overwrites the row;
  - record_precompute fills the SETUP span on a finished run, migrating and re-stamping
    a DB written before those columns existed.

Synthetic rows only (no sim); Agg backend. Run:  python -m pytest Tests/test_runtime_metrics.py -q
"""
from __future__ import annotations

import os

from Optimization.persistence import runtime_metrics as rm


def _res(elapsed=10.0, done=100, **kw):
    d = dict(elapsed=elapsed, done=done, n_bins=5000, regime_bins=3000, n_aisles=40,
             t_reord=2.0, t_build=1.0, t_pre=0.5, t_sim=4.0, t_extract=1.5, t_inv=0.5, t_save=0.5,
             # 2026-08-19 observability columns (must mirror the worker return dict —
             # a key missing HERE writes 0 and the roundtrip silently under-covers)
             t_sample=0.3, t_task=0.7, t_kf=0.2, p1_s=3.1, p2_s=0.9,
             gc_pause_s=0.25, gc_gen2=7, peak_rss_mib=1234.5, live_objects=250_000)
    d.update(kw)
    return d


def test_record_and_load_roundtrip(tmp_path):
    root = str(tmp_path)
    rm.record_arm(root, 'k1_off_lpt', ('pairA', 'store', 'store', 'opt_rank_cartlabor_norsl'), _res())
    rows = rm.load_rows(root)
    assert len(rows) == 1
    r = rows[0]
    assert (r['cell'], r['pair'], r['config'], r['channel'], r['arm']) == \
        ('k1_off_lpt', 'pairA', 'store', 'store', 'opt_rank_cartlabor_norsl')
    assert r['initial'] == 'opt' and r['assignment'] == 'rank_cartlabor'   # assignment keeps its '_'
    assert abs(r['total_s'] - 10.0) < 1e-9
    assert abs(r['rate'] - 10.0) < 1e-9                                    # 100 batches / 10 s
    assert abs(r['reord_s'] - 2.0) < 1e-9 and abs(r['sim_s'] - 4.0) < 1e-9
    assert r['n_bins'] == 5000 and r['regime_bins'] == 3000
    # observability columns round-trip (kf/gc are overlays; rss/live are nullable)
    assert abs(r['smpl_s'] - 0.3) < 1e-9 and abs(r['task_s'] - 0.7) < 1e-9
    assert abs(r['kf_s'] - 0.2) < 1e-9
    assert abs(r['p1_s'] - 3.1) < 1e-9 and abs(r['p2_s'] - 0.9) < 1e-9
    assert abs(r['gc_pause_s'] - 0.25) < 1e-9 and r['gc_gen2'] == 7
    assert abs(r['peak_rss_mib'] - 1234.5) < 1e-9 and r['live_objects'] == 250_000


def test_missing_observability_keys_write_nulls_not_raises(tmp_path):
    # A crashed or legacy worker result dict lacks the new keys — record_arm must degrade
    # to zeros/NULLs, never raise (the parent wraps it best-effort, but silence here would
    # also silently zero REAL data; this pins the intended degradation).
    root = str(tmp_path)
    legacy = dict(elapsed=5.0, done=10, n_bins=1, regime_bins=1, n_aisles=1,
                  t_reord=1.0, t_build=1.0, t_pre=1.0, t_sim=1.0,
                  t_extract=0.5, t_inv=0.25, t_save=0.25)
    rm.record_arm(root, 'k1_off', ('p', 'store', 'store', 'uni_fifo_norsl'), legacy)
    r = rm.load_rows(root)[0]
    assert r['smpl_s'] == 0.0 and r['kf_s'] == 0.0 and r['gc_gen2'] == 0
    assert r['peak_rss_mib'] is None and r['live_objects'] is None


def test_setup_spans_are_nullable_and_stamped_with_their_provenance(tmp_path):
    """`precomp_s` is measured outside `total_s`, so NULL and 0.0 must stay distinguishable.

    Every other timing column coerces a missing key to 0.0, which is right for a section
    of a loop that ran.  It is wrong here: a worker that never measured the setup phase
    has not observed a zero, and a chart that treats the two alike would report the map
    family's offline solve as free.
    """
    root = str(tmp_path)
    rm.record_arm(root, 'k1_off', ('p', 'store', 'store', 'uni_fifo_norsl'), _res())
    rm.record_arm(root, 'k1_off', ('p', 'store', 'store', 'opt_map_norsl'),
                  _res(t_precompute=41.5, map_lap_pct=0.031))
    by_arm = {r['arm']: r for r in rm.load_rows(root)}
    assert by_arm['uni_fifo_norsl']['precomp_s'] is None
    assert by_arm['uni_fifo_norsl']['precomp_src'] is None
    assert abs(by_arm['opt_map_norsl']['precomp_s'] - 41.5) < 1e-9
    assert by_arm['opt_map_norsl']['precomp_src'] == 'inline'
    assert abs(by_arm['opt_map_norsl']['map_lap_pct'] - 0.031) < 1e-9


def test_a_measured_zero_survives_as_a_zero(tmp_path):
    """A rule with no build step measures ~0 s; that is data, not a missing value."""
    root = str(tmp_path)
    rm.record_arm(root, 'k1_off', ('p', 'store', 'store', 'uni_fifo_norsl'),
                  _res(t_precompute=0.0))
    r = rm.load_rows(root)[0]
    assert r['precomp_s'] == 0.0 and r['precomp_src'] == 'inline'


def test_backfill_updates_an_existing_arm_and_says_it_was_a_backfill(tmp_path):
    root = str(tmp_path)
    uid = ('p', 'store', 'store', 'opt_map_norsl')
    rm.record_arm(root, 'k1_off', uid, _res())
    assert rm.load_rows(root)[0]['precomp_s'] is None
    assert rm.record_precompute(root, 'k1_off', uid, 38.25, 'backfill', map_lap_pct=0.02)
    r = rm.load_rows(root)[0]
    assert abs(r['precomp_s'] - 38.25) < 1e-9
    # The provenance is the whole point: an inline second is contended against the sweep's
    # worker pool, a backfilled one is not, and the two do not form a ratio.
    assert r['precomp_src'] == 'backfill'
    assert abs(r['total_s'] - 10.0) < 1e-9, 'a backfill must not disturb the loop timings'


def test_backfill_migrates_a_db_written_before_the_columns_existed(tmp_path):
    """A finished run cannot be re-simulated, so the backfill has to widen its table.

    CREATE TABLE IF NOT EXISTS cannot add a column, and the file's recorded schema id
    describes its old shape — so the migration must ALTER and then re-stamp, or every
    later read of that run reports an unvetted shape.
    """
    import sqlite3
    import warnings
    root = str(tmp_path)
    uid = ('p', 'store', 'store', 'opt_map_norsl')
    rm.record_arm(root, 'k1_off', uid, _res())
    # Reproduce the older vintage faithfully: drop the columns AND restore the stamp that
    # vintage carried, so this exercises a genuine old file rather than a corrupted new one.
    con = sqlite3.connect(rm.runtime_db_path(root))
    for col, _t in rm._SETUP_COLUMNS:
        con.execute(f'ALTER TABLE runtime DROP COLUMN {col}')
    con.execute("INSERT OR REPLACE INTO schema_meta VALUES ('schema_id', '397b7e750e1d')")
    con.commit()
    con.close()
    with warnings.catch_warnings(record=True) as before:
        warnings.simplefilter('always')
        assert 'precomp_s' not in rm.load_rows(root)[0]
    assert not [w for w in before if 'not vetted' in str(w.message)], \
        'the outgoing vintage must still be vetted, or old runs stop reading cleanly'

    assert rm.record_precompute(root, 'k1_off', uid, 12.5, 'backfill')
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        r = rm.load_rows(root)[0]
    assert abs(r['precomp_s'] - 12.5) < 1e-9 and r['precomp_src'] == 'backfill'
    assert not [w for w in caught if 'not vetted' in str(w.message)], \
        'a migrated run must re-stamp, or it reads unvetted from then on'
    assert abs(r['reord_s'] - 2.0) < 1e-9, 'the migration must not disturb existing rows'


def test_backfill_refuses_to_invent_a_row_or_an_unknown_source(tmp_path):
    import pytest
    root = str(tmp_path)
    rm.record_arm(root, 'k1_off', ('p', 'store', 'store', 'uni_fifo_norsl'), _res())
    # no such arm: a measurement with no simulation behind it must not appear in the table
    assert not rm.record_precompute(root, 'k1_off', ('p', 'store', 'store', 'ghost'),
                                    1.0, 'backfill')
    assert len(rm.load_rows(root)) == 1
    with pytest.raises(ValueError):
        rm.record_precompute(root, 'k1_off', ('p', 'store', 'store', 'uni_fifo_norsl'),
                             1.0, 'guessed')


def test_the_setup_span_is_not_a_section(tmp_path):
    """Stacking `precomp_s` onto SECTIONS would add a span `total_s` does not contain."""
    assert 'precomp_s' not in dict(rm.SECTIONS)
    assert 'precomp_s' in dict(rm.OUTSIDE_TOTAL)
    assert not set(dict(rm.SECTIONS)) & set(dict(rm.OUTSIDE_TOTAL))


def test_key_is_unique_and_overwrites(tmp_path):
    root = str(tmp_path)
    uid = ('pairA', 'store', 'store', 'uni_fifo_norsl')
    rm.record_arm(root, 'k1_off', uid, _res(elapsed=10))
    rm.record_arm(root, 'k1_off', uid, _res(elapsed=20))     # same key → resume/re-run overwrites
    rows = rm.load_rows(root)
    assert len(rows) == 1 and abs(rows[0]['total_s'] - 20.0) < 1e-9


def test_load_absent_is_empty(tmp_path):
    assert rm.load_rows(str(tmp_path / 'nope')) == []
