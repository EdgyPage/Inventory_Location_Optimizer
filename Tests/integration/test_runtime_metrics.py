"""test_runtime_metrics.py

Locks the runtime-metrics capture + graphs (Phase 4):
  - runtime_metrics.record_arm writes one row per arm (arm name parsed to initial/assignment,
    rate = batches/total_s, section totals persisted) and load_rows round-trips it;
  - the (cell,pair,config,channel,arm) key is UNIQUE — a re-run/resume overwrites the row;
  - run_runtime_graphs.run emits the ranked + section-breakdown PNGs from synthetic rows.

Synthetic rows only (no sim); Agg backend. Run:  python -m pytest Tests/test_runtime_metrics.py -q
"""
from __future__ import annotations

import os

from Optimization.persistence import runtime_metrics as rm
from Optimization import run_runtime_graphs as rg


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


def test_key_is_unique_and_overwrites(tmp_path):
    root = str(tmp_path)
    uid = ('pairA', 'store', 'store', 'uni_fifo_norsl')
    rm.record_arm(root, 'k1_off', uid, _res(elapsed=10))
    rm.record_arm(root, 'k1_off', uid, _res(elapsed=20))     # same key → resume/re-run overwrites
    rows = rm.load_rows(root)
    assert len(rows) == 1 and abs(rows[0]['total_s'] - 20.0) < 1e-9


def test_load_absent_is_empty(tmp_path):
    assert rm.load_rows(str(tmp_path / 'nope')) == []


def test_graphs_emitted(tmp_path):
    root = str(tmp_path)
    for cell in ('k1_off_rr', 'k1_off_lpt'):                 # >1 cell → by-cell chart too
        for ch in ('store', 'fulfillment'):
            for arm in ('uni_fifo_norsl', 'uni_rank_labor_norsl'):
                rm.record_arm(root, cell, ('mixed__bell_lt0', ch, ch, arm),
                              _res(elapsed=(10.0 if 'fifo' in arm else 16.0)))
    out = rg.run(root)
    assert out and os.path.isdir(out)
    pngs = set(f for f in os.listdir(out) if f.endswith('.png'))
    assert {'runtime_slowest_arms.png', 'runtime_by_assignment.png',
            'runtime_by_warehouse.png', 'runtime_section_breakdown.png',
            'runtime_by_cell.png'} <= pngs


def test_graphs_skip_without_db(tmp_path):
    assert rg.run(str(tmp_path)) is None                     # no runtime_metrics.db → graceful skip
