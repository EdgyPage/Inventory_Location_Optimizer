"""test_analysis_granularity.py -- the analysis job count scales with the cell's contents at
`graph` granularity and does not at `config`.

`analyze_run --granularity` has two settings whose only documentation was a help string:
`config` (default) emits one job per channel run, so a large `--workers` idles (memory
`analyze-run-granularity-worker-saturation`: the default emits 4 jobs per cell and 24 workers
idle ~20); `graph` emits one job per (channel run, evaluation).  Nothing asserted either
count, so the setting could silently collapse to one shape -- and the slow re-analysis
would look like a machine problem.  This pins both on a scratch tree with the heavy asset
build and the directory pre-pass patched out.

Run:  python -m pytest Tests/unit/test_analysis_granularity.py -q
"""
from __future__ import annotations

import json
import logging
import os

import pytest

from Optimization import run_analysis as ra
from Optimization.Performance_Evaluations import driver
from Optimization.Performance_Evaluations.presets import PRESETS

_LOG = logging.getLogger('test_analysis_granularity')


def _leaf(root, pair, config, channel):
    d = os.path.join(root, pair, config, channel)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'sim_meta.json'), 'w') as f:
        json.dump({'name': config, 'run_dir': d, 'inventory': pair, 'channel': channel,
                   'inv_db': 'i.db', 'aff_db': 'a.db',
                   'strategies': [{'key': 'uni_fifo_norsl', 'db_path': 'x.db', 'run_id': 1}]}, f)


class _FakeTree:
    """Only what `_config_jobs` reads: a leaf's sim_meta path."""
    def leaf_path(self, run, artifact):
        assert artifact == 'sim_meta'
        return os.path.join(run.path, 'sim_meta.json')


@pytest.fixture()
def cell(tmp_path, monkeypatch):
    root = str(tmp_path / 'k1_off')
    _leaf(root, 'prof_a', 'store', 'store')
    _leaf(root, 'prof_a', 'ful', 'fulfillment')
    _leaf(root, 'prof_b', 'store', 'store')
    _leaf(root, 'prof_b', 'ful', 'fulfillment')
    # the heavy parts: the per-pair asset build and the output-dir pre-pass
    monkeypatch.setattr(ra, 'build_shared_assets',
                        lambda *a, **k: {key: None for key in ra._SLIM_KEYS})
    monkeypatch.setattr(ra, 'regime_sizing_from_config', lambda: None)
    monkeypatch.setattr(ra, '_coverage_record', lambda pair: None)
    monkeypatch.setattr(driver, 'prepare_config_dirs', lambda run_dir: None)
    return root


def _jobs(cell, granularity):
    return ra._config_jobs(cell, _FakeTree(), 'BY_INITIAL', granularity, {}, _LOG)


def test_config_granularity_emits_one_job_per_channel_run_carrying_every_key(cell):
    keys = driver.config_keys(PRESETS['BY_INITIAL'])
    assert len(keys) > 1, 'the preset has one key -- the two granularities would coincide'
    jobs = _jobs(cell, 'config')
    assert len(jobs) == 4                                    # 2 pairs x 2 channel runs
    assert all(j['eval_keys'] == keys for j in jobs)


def test_graph_granularity_emits_one_job_per_channel_run_and_key(cell):
    keys = driver.config_keys(PRESETS['BY_INITIAL'])
    jobs = _jobs(cell, 'graph')
    assert len(jobs) == 4 * len(keys)
    assert all(len(j['eval_keys']) == 1 for j in jobs)
    # every (run, key) exactly once
    seen = {(j['run_dir'], j['eval_keys'][0]) for j in jobs}
    assert len(seen) == len(jobs)


def test_the_graph_count_grows_with_the_cell_and_the_config_count_grows_only_with_runs(cell):
    """The property the help string claims: adding evaluations changes only `graph`."""
    keys = driver.config_keys(PRESETS['BY_INITIAL'])
    cfg, graph = _jobs(cell, 'config'), _jobs(cell, 'graph')
    assert len(graph) / len(cfg) == len(keys)
    _leaf(cell, 'prof_c', 'store', 'store')                  # one more channel run
    assert len(_jobs(cell, 'config')) == len(cfg) + 1
    assert len(_jobs(cell, 'graph')) == len(graph) + len(keys)
