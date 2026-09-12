"""test_coupled_resume_e2e.py — a REAL torn coupled pair, and the resume that repairs it.

Site-dock 22.  `Tests/integration/test_coupled_resume_reconciler.py` plants the four two-leaf
states by hand and asserts the exact filesystem effect of each.  A planted matrix only ever
proves the reconciler reads a tree that file wrote; this one ties it to reality.

THE FAULT IS INJECTED, THE TEAR IS NOT.  A coupled unit writes two leaves and the parent
finalizes them one after the other (`supervisor._run_pool`, iterating the unit's `group_keys`).
`_finalize_config_run` is made to raise on every call after the first, which is what a process
death inside that window does to the tree.  Everything else is production: the real
`_run_workers_flat`, a real `ProcessPoolExecutor`, real spawned workers, real databases.  The
tear is then READ OFF THE TREE rather than asserted from the patch -- exactly one leaf carries
`sim_meta.json`, the other still carries `resume.pkl`.

Memory `resume-architecture-verified-sound` is why this file exists at all: the "strategy-level
reset" log line is NOT evidence that a resume worked, and neither is a clean exit.  What is
evidence, and what this asserts:

  * the resumed tree ends with BOTH leaves complete;
  * every arm database holds exactly ONE run.  `find_run` resolves `ORDER BY run_id LIMIT 1` --
    the OLDEST -- so a leaf that was re-planned over its own finalized output would answer
    every later query from the abandoned run, with no exception and no log line;
  * the repaired run's rows are IDENTICAL to a coupled run that was never killed.  That is the
    claim the whole repair rests on -- a strategy-granularity replay from batch 0 is exact --
    and it is the one thing a log line cannot show.

Run:  python -m pytest Tests/e2e/test_coupled_resume_e2e.py -q
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3

import pytest

from Optimization import run_simulation as rs
import Optimization.simdriver.supervisor as sup
import Optimization.simdriver.workunits as wu
from Optimization.persistence.Picking_Data import load_batch_stats
from Optimization.runschema.sim_manifest import _resume_path, _write_run_spec
from Warehouse.generation import generate_affinity as ga
from Warehouse.generation.generate_inventory import (
    Family, fulfillment_family, build_inventory_from_plan, save_inventory_to_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}
_LABEL = 'mixed'
_PUT_CREW = 2
_PUT_EU = {'store': 0.25, 'fulfillment': 0.75}


def _mixed_dbs(tmp_path):
    """The mixed catalogue + affinity matrix every mixed e2e in this directory builds from."""
    plan = [
        Family('food', 0.4, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
        Family('clothing', 0.3, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
        fulfillment_family(share=0.3, cube_sizes=(4, 6, 8)),
    ]
    inv = build_inventory_from_plan(num_skus=300, plan=plan, seed=1)
    inv_db = str(tmp_path / 'mixed_inv.db')
    save_inventory_to_db(inv, inv_db, {'name': 'mixed', 'num_skus': 300})
    aff_db = str(tmp_path / 'mixed_aff.db')
    conn = ga._init_db(aff_db)
    skus = sorted(c.sku for c in inv.orders)
    rows = []
    for a, b in zip(skus, skus[1:]):
        rows += [(a, b, 1.5), (b, a, 1.5)]
    conn.executemany('INSERT OR REPLACE INTO affinity (sku_i, sku_j, lift) VALUES (?,?,?)', rows)
    conn.commit(); conn.close()
    return inv_db, aff_db


class _Capture(logging.Handler):
    """The run log, as a list.  `run.log` is the only place a multi-hour run's damage is
    visible (memory `pool-run-swallows-dead-arms`), so "a finished leaf was discarded and
    replayed" being findable there is part of what this ticket has to prove."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A coupled run, ready to drive through the real pool.

    `--couple-channels` is declared (never inferred), the era's derived staffing block is
    supplied directly -- coupling is an era feature and a coupled unit without a derived block
    is refused -- and the working-day grid the site put pool needs is on.  No receiving crew,
    so no dock is constructed: the batch-grain refusal this ticket adds must stand on COUPLING
    alone, and a configured dock would refuse for its own reason.
    """
    log = logging.getLogger('coupled-resume-e2e')
    log.setLevel(logging.WARNING)
    cap = _Capture()
    log.addHandler(cap)
    monkeypatch.setitem(rs.CONFIG['global'], 'n_batches', 2)
    monkeypatch.setitem(rs.CONFIG['global'], 'couple_channels', True)
    monkeypatch.setitem(rs.CONFIG['global'], 'releases_per_day', 1)
    monkeypatch.setitem(rs.CONFIG['global'], 'cut_at_day_end', True)
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])
    from Optimization.config import strategies
    monkeypatch.setitem(strategies.CHANNEL_RESTOCKS, 'store', ('fifo',))
    monkeypatch.setitem(strategies.CHANNEL_RESTOCKS, 'fulfillment', ('fifo',))
    for _ch in ('store', 'fulfillment'):
        monkeypatch.setitem(rs.CONFIG['channels'][_ch], 'restocks', ('fifo',))

    inv_db, aff_db = _mixed_dbs(tmp_path)
    build_pair = str(tmp_path / 'build' / _LABEL); os.makedirs(build_pair, exist_ok=True)
    shared = rs.build_shared_assets(
        inv_db, aff_db, log, max_skus=300, min_bins=3000, keyframe_interval=1,
        warehouse_db_path=os.path.join(build_pair, 'warehouse.db'))
    _mixed, channel_runs = rs._channel_runs_for(shared['inventory'])
    assert _mixed, 'the coupled unit needs a mixed catalogue'
    shared['staffing'] = {'derived': {
        'channels': {ch.name: {'pickers': ch.picker.num_pickers} for ch, _ in channel_runs},
        'put': {'crew': _PUT_CREW, 'expected_utilization': dict(_PUT_EU)},
        'receiving': {'crew': 0},
    }}
    try:
        yield dict(log=log, cap=cap, shared=shared, inv_db=inv_db, aff_db=aff_db, tmp=tmp_path)
    finally:
        log.removeHandler(cap)


def _run(site, name, *, skip_completed=False):
    """One cell through the PRODUCTION pool driver, into its own base dir."""
    base = str(site['tmp'] / name)
    os.makedirs(base, exist_ok=True)
    _write_run_spec(base, {'argv': ['coupled-resume-e2e']})
    rs._run_workers_flat(
        [(_LABEL, site['inv_db'], site['aff_db'])], base, {_LABEL: site['shared']},
        1, site['log'], skip_completed=skip_completed, resume_granularity='strategy')
    return base


def _leaf_dirs(base):
    """The pair's two channel-run dirs, in the declared channel order."""
    pair = os.path.join(base, _LABEL)
    return [os.path.join(pair, 'store', 'store'),
            os.path.join(pair, 'ful_calibrated', 'fulfillment')]


def _arm_rows(leaf_dir):
    """`{arm: [batch_stat rows]}` for one finalized leaf, read through its own sim_meta.json
    so the run_id comes from the run rather than from an assumption about numbering."""
    with open(os.path.join(leaf_dir, 'sim_meta.json')) as f:
        meta = json.load(f)
    return {s['key']: load_batch_stats(s['db_path'], s['run_id'])
            for s in meta['strategies']}


def _n_runs(db_path):
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        return con.execute('SELECT COUNT(*) FROM simulation_runs').fetchone()[0]
    finally:
        con.close()


def test_a_real_mid_flight_kill_tears_a_coupled_pair_and_resume_repairs_it(site):
    """Kill between the two finalizes, then resume — and compare against a run never killed."""
    # ── 1. the control: a coupled run that is never interrupted ───────────────────
    clean = _run(site, 'clean')
    clean_leaves = _leaf_dirs(clean)
    assert all(os.path.exists(os.path.join(d, 'sim_meta.json')) for d in clean_leaves), \
        'the control run did not finalize both leaves'
    control = [_arm_rows(d) for d in clean_leaves]
    assert all(rows for leaf in control for rows in leaf.values()), 'the control wrote no rows'

    # ── 2. the fault: _finalize_config_run dies after the FIRST leaf ──────────────
    # A coupled unit finalizes its two group keys back to back, so raising on every call after
    # the first is what a process death inside that window leaves behind.  The safety sweep at
    # the end of `_supervise` calls the same function, so it dies too -- as it would.
    _real = sup._finalize_config_run
    _calls = {'n': 0}

    def _die_after_the_first(sim_skeleton):
        _calls['n'] += 1
        if _calls['n'] == 1:
            return _real(sim_skeleton)
        raise OSError('killed between the two leaves of a coupled unit')

    sup._finalize_config_run = _die_after_the_first
    try:
        torn = _run(site, 'torn')
    finally:
        sup._finalize_config_run = _real
    assert _calls['n'] >= 2, 'the injected fault never fired -- nothing was torn'

    # THE TEAR, read off the TREE rather than asserted from the patch.
    torn_leaves = _leaf_dirs(torn)
    _meta = [os.path.exists(os.path.join(d, 'sim_meta.json')) for d in torn_leaves]
    assert sum(_meta) == 1, f'the kill did not produce a torn pair: sim_meta.json {_meta}'
    _done, _open = (torn_leaves[0], torn_leaves[1]) if _meta[0] \
        else (torn_leaves[1], torn_leaves[0])
    assert not os.path.exists(_resume_path(_done)), 'the finalized leaf kept its resume file'
    assert os.path.exists(_resume_path(_open)), \
        'the unfinalized leaf lost its resume file -- that is a different tear'
    # Both leaves genuinely RAN: the tear is in the bookkeeping, not in the simulation.
    for d in torn_leaves:
        assert [f for f in os.listdir(d) if f.startswith('sim_') and f.endswith('.db')], \
            f'{d} holds no arm database, so nothing was torn there'

    # ── 3. the resume, through the same production driver ─────────────────────────
    site['cap'].lines.clear()
    _run(site, 'torn', skip_completed=True)

    # It is not silent: the discard is findable in the run log.
    _said = [ln for ln in site['cap'].lines if 'TORN coupled pair' in ln]
    assert _said, f'the repair left no trace in the run log: {site["cap"].lines}'
    assert any('replaying' in ln for ln in _said), _said

    # ── 4. what the resumed tree must be ──────────────────────────────────────────
    for d in torn_leaves:
        assert os.path.exists(os.path.join(d, 'sim_meta.json')), f'{d} did not finalize'
        assert not os.path.exists(_resume_path(d)), f'{d} is finalized but still resumable'
    repaired = [_arm_rows(d) for d in torn_leaves]

    # ONE RUN PER DATABASE.  The un-finalized leaf was re-planned over its own finished
    # output; without the reset it would have opened a SECOND run in each file, and
    # `find_run` (ORDER BY run_id LIMIT 1) would answer every later query from the abandoned
    # one.  No exception, no log line -- only this count says so.
    for d in torn_leaves:
        with open(os.path.join(d, 'sim_meta.json')) as f:
            for s in json.load(f)['strategies']:
                assert _n_runs(s['db_path']) == 1, \
                    f"{s['key']} db holds {_n_runs(s['db_path'])} runs after the repair"

    # ── 5. the repair is EXACT, which is why refusing the resume would buy nothing ─
    for leaf_clean, leaf_repaired, d in zip(control, repaired, torn_leaves):
        assert set(leaf_clean) == set(leaf_repaired), f'{d}: arm set moved'
        for arm, rows in leaf_clean.items():
            got = leaf_repaired[arm]
            assert len(got) == len(rows) == rs.CONFIG['global']['n_batches'], \
                f'{d}/{arm}: replayed {len(got)} batches against the control\'s {len(rows)}'
            assert got == rows, (
                f'{d}/{arm}: the replayed arm does not match a run that was never killed. '
                f'A strategy-granularity replay from batch 0 is supposed to be exact; first '
                f'differing batch: '
                f'{next(i for i, (a, b) in enumerate(zip(got, rows)) if a != b)}')
