"""test_analysis_stage_pool.py -- every cell's analysis stages through ONE pool.

`run_analysis.analyze_cells` replaced one-pool-per-cell with three barriers inside it
(2026-09-19, the flat work pool).  What these tests pin, with the job builders, the tree
resolver and the worker faked and a thread executor in place of the spawn pool:

  - every cell's CONFIG jobs are submitted before any cell's aggregate job (the parent
    streams cells; it does not drain one before building the next);
  - a cell's aggregate and site jobs are BUILT only after its config jobs resolved -- they
    read the series docs the config stage wrote -- and one cell never waits for another;
  - a rebuild after a broken pool re-emits the job dicts in hand and never re-runs the
    builders (their pre-pass wipes the output dirs sibling jobs rendered into);
  - the cell's own record from the run descriptor is applied while its jobs are built and
    undone after (the config stage stamps cell-axis inbound keys and the split off CONFIG);
  - tallies and the run-end summaries are per cell;
  - `run_analysis(cell_dir)` -- the standalone CLI and the harness entry -- is the one-cell
    case of the same function, and `workers=1` runs inline without a spawn pool.

Run:  python -m pytest Tests/unit/test_analysis_stage_pool.py -q
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
from concurrent.futures.process import BrokenProcessPool

import pytest

from Optimization import run_analysis as ra
from Optimization.config.sim_config import CONFIG
from Optimization.simdriver import workpool as wp

_LOG = logging.getLogger('test_analysis_stage_pool')


class _Tree:
    def __init__(self, layout=None):
        self.layout = layout or {}


def _job(cell, stage, i, keys=('k',)):
    d = {'stage': stage, 'preset': 'BY_INITIAL', 'set': {}, 'eval_keys': list(keys)}
    d['run_dir' if stage == 'config' else 'out_dir'] = f'{cell}/{stage}{i}'
    return d


@pytest.fixture
def harness(monkeypatch):
    """The builders, resolver and worker faked; a thread executor; a record of everything."""
    rec = {'built': [], 'ran': [], 'lock': threading.Lock()}
    n = {'config': 2, 'aggregate': 1, 'site': 1}
    monkeypatch.setattr(ra, '_tree_for', lambda cell_dir: (_Tree(), cell_dir.rsplit('/', 1)[-1]))
    monkeypatch.setattr(ra, '_apply_run_shape', lambda base_dir, log: None)
    monkeypatch.setattr(ra, '_analysis_executor',
                        lambda k: concurrent.futures.ThreadPoolExecutor(max_workers=k))
    monkeypatch.setattr(wp, '_explain_worker_death', lambda log, mod: None)

    def builder(stage):
        def build(cell_dir, rt, *a, **k):
            cell = cell_dir.rsplit('/', 1)[-1]
            with rec['lock']:
                rec['built'].append((cell, stage))
            return [_job(cell, stage, i) for i in range(n[stage])]
        return build
    monkeypatch.setattr(ra, '_config_jobs', builder('config'))
    monkeypatch.setattr(ra, '_aggregate_jobs', builder('aggregate'))
    monkeypatch.setattr(ra, '_site_jobs', builder('site'))

    def run_job(job):
        with rec['lock']:
            rec['ran'].append((job['cell'], job['stage']))
        return (job.get('run_dir') or job.get('out_dir'), None,
                {'granted': {'k': 1}, 'denied': {}, 'errors': {}, 'era': {}})
    monkeypatch.setattr(ra, '_run_job', run_job)
    rec['n'] = n
    return rec


def _cells(*names):
    return [(c, f'/run/{c}') for c in names]


def test_every_cells_config_jobs_are_submitted_before_any_aggregate_job(harness, monkeypatch):
    gate = threading.Event()
    submits = []

    class _Recording(wp.WorkPool):
        def submit(self, cell, jobs):
            jobs = list(jobs)
            submits.append((cell, sorted({j.payload['stage'] for j in jobs})))
            if cell == 'c2' and submits[-1][1] == ['config']:
                gate.set()                     # both cells' config jobs are in: let them run
            return super().submit(cell, jobs)
    monkeypatch.setattr(ra, 'WorkPool', _Recording)
    real_run = ra._run_job

    def gated(job):
        if job['stage'] == 'config':
            assert gate.wait(10), 'config jobs ran before every cell had submitted'
        return real_run(job)
    monkeypatch.setattr(ra, '_run_job', gated)

    ra.analyze_cells(_cells('c1', 'c2'), _LOG, workers=4)
    assert submits[:2] == [('c1', ['config']), ('c2', ['config'])], submits
    assert sorted(submits[2:]) == [('c1', ['aggregate', 'site']), ('c2', ['aggregate', 'site'])]


def test_aggregate_jobs_are_built_only_after_the_cells_config_jobs_resolved(harness):
    seen_at_build = {}
    orig = ra._aggregate_jobs

    def agg(cell_dir, rt, cell, *a, **k):
        with harness['lock']:
            seen_at_build[cell] = sum(1 for c, s in harness['ran'] if c == cell and s == 'config')
        return orig(cell_dir, rt, cell, *a, **k)
    ra._aggregate_jobs = agg
    try:
        ra.analyze_cells(_cells('c1', 'c2'), _LOG, workers=3)
    finally:
        ra._aggregate_jobs = orig
    assert seen_at_build == {'c1': 2, 'c2': 2}, \
        f'an aggregate stage was built before its config stage finished: {seen_at_build}'
    assert sorted(harness['ran']).count(('c1', 'aggregate')) == 1


def test_one_cell_never_waits_for_another(harness, monkeypatch):
    """c2's config jobs block until c1's AGGREGATE job has run: a stage barrier across cells
    would deadlock this; the per-cell continuation lets c1 finish while c2 is still working."""
    c1_aggregate_ran = threading.Event()
    real_run = ra._run_job

    def run_job(job):
        if job['cell'] == 'c2' and job['stage'] == 'config':
            assert c1_aggregate_ran.wait(10), "c1's aggregate stage waited on c2's config stage"
        out = real_run(job)
        if job['cell'] == 'c1' and job['stage'] == 'aggregate':
            c1_aggregate_ran.set()
        return out
    monkeypatch.setattr(ra, '_run_job', run_job)
    ra.analyze_cells(_cells('c1', 'c2'), _LOG, workers=4)
    assert c1_aggregate_ran.is_set()
    assert sorted(harness['ran']).count(('c2', 'aggregate')) == 1


def test_a_rebuild_after_a_break_never_reruns_the_job_builders(harness, monkeypatch):
    gen = {'broken': False}
    real_run = ra._run_job

    def run_job(job):
        if not gen['broken'] and job['cell'] == 'c1' and job['stage'] == 'config':
            gen['broken'] = True
            raise BrokenProcessPool('hard worker death')
        return real_run(job)
    monkeypatch.setattr(ra, '_run_job', run_job)

    ra.analyze_cells(_cells('c1', 'c2'), _LOG, workers=2)
    assert sorted(harness['built']) == [('c1', 'aggregate'), ('c1', 'config'), ('c1', 'site'),
                                        ('c2', 'aggregate'), ('c2', 'config'), ('c2', 'site')], \
        'a builder ran twice -- its pre-pass would have wiped what sibling jobs rendered'
    ran = sorted(harness['ran'])
    for cell in ('c1', 'c2'):
        assert ran.count((cell, 'config')) >= 2 and ran.count((cell, 'aggregate')) == 1 \
            and ran.count((cell, 'site')) == 1, ran


def test_the_cells_record_is_applied_while_its_jobs_are_built_and_undone_after(harness, monkeypatch):
    layout = {'cells': [{'name': 'k1_off_lpt', 'split': None, 'zoning': {'enabled': False},
                         'scheduler': 'lpt', 'inbound': {'dock_doors': 7}}]}
    monkeypatch.setattr(ra, '_tree_for', lambda cell_dir: (_Tree(layout), 'k1_off_lpt'))
    seen = {}
    orig_cfg, orig_agg = ra._config_jobs, ra._aggregate_jobs

    def cfg(cell_dir, rt, *a, **k):
        seen['config'] = (CONFIG['channels']['store']['configs'][0].get('scheduler'),
                          CONFIG['global'].get('inbound_dock_doors'))
        return orig_cfg(cell_dir, rt, *a, **k)

    def agg(cell_dir, rt, cell, *a, **k):
        seen['aggregate'] = (CONFIG['channels']['store']['configs'][0].get('scheduler'),
                             CONFIG['global'].get('inbound_dock_doors'))
        return orig_agg(cell_dir, rt, cell, *a, **k)
    monkeypatch.setattr(ra, '_config_jobs', cfg)
    monkeypatch.setattr(ra, '_aggregate_jobs', agg)
    before = (CONFIG['channels']['store']['configs'][0].get('scheduler'),
              CONFIG['global'].get('inbound_dock_doors'))
    assert before[0] is None, 'run on a pristine CONFIG'

    ra.analyze_cells(_cells('k1_off_lpt'), _LOG, workers=1)
    assert seen == {'config': ('lpt', 7), 'aggregate': ('lpt', 7)}, seen
    after = (CONFIG['channels']['store']['configs'][0].get('scheduler'),
             CONFIG['global'].get('inbound_dock_doors'))
    assert after == before, 'the cell record leaked past its jobs'


def test_tallies_and_summaries_are_per_cell(harness, caplog):
    with caplog.at_level(logging.INFO, logger=_LOG.name):
        tallies = ra.analyze_cells(_cells('c1', 'c2'), _LOG, workers=2)
    assert tallies['c1']['granted'] == {'k': 4} and tallies['c2']['granted'] == {'k': 4}
    lines = [r.getMessage() for r in caplog.records if '[access] run summary' in r.getMessage()]
    assert [ln[:5] for ln in lines] == ['[c1] ', '[c2] '], lines


def test_run_analysis_is_the_one_cell_case(monkeypatch):
    calls = []
    monkeypatch.setattr(ra, '_tree_for', lambda cell_dir: (_Tree(), 'k1_off'))
    monkeypatch.setattr(ra, 'analyze_cells', lambda items, log, **kw: calls.append((list(items), kw)))
    ra.run_analysis('/run/k1_off', _LOG, workers=3, preset='DEFAULT', granularity='graph')
    assert calls == [([('k1_off', '/run/k1_off')],
                      dict(workers=3, preset='DEFAULT', granularity='graph', cli_set=None, only=()))]


def test_one_worker_runs_inline_without_a_spawn_pool(harness, monkeypatch):
    def never(k):
        pytest.fail('workers=1 must not build a process pool')
    monkeypatch.setattr(ra, '_analysis_executor', never)
    ra.analyze_cells(_cells('c1'), _LOG, workers=1)
    assert sorted(harness['ran']) == [('c1', 'aggregate'), ('c1', 'config'), ('c1', 'config'),
                                      ('c1', 'site')]
