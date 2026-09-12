"""test_coupled_resume_reconciler.py — the COUPLED pair's completeness test and torn-pair repair.

Site-dock 22, building site-dock 10's answer.  A coupled work unit finalizes TWO channel
leaves, so a kill can leave a pair that every existing guard reads one leaf at a time and
therefore calls half-complete.  `workunits._reconcile_coupled_unit` is the one place above both
`_prepare_channel_run` calls that can ask the two-leaf question, and it repairs as a side
effect.  What is locked here:

  * THE FOUR-STATE MATRIX, planted on disk.  Both complete -> skip.  Neither started -> nothing.
    Both partial and in step -> nothing (the ordinary per-arm resume owns that).  One leaf
    finalized and the other not -> TORN: un-finalize, discard both leaves' arms, discard the
    unit's site DB, replay from batch 0.
  * THE SKEW.  Each leaf writes its own `_ckpt_<arm>.pkl` inside one batch loop, so a kill
    between leaf A's save and leaf B's leaves the pair one checkpoint apart -- a state that
    wedges the run permanently without this repair, because the worker's "leaves must share one
    batch range" refusal cannot clear itself.
  * THE EXACT FILESYSTEM EFFECT, asserted file by file rather than through the return value: a
    reconciler that returned the right answer and removed the wrong files would be worse than
    one that raised.
  * THE RESUME RECORD.  `reset_strategy_db` cannot touch `resume.pkl`, and an arm whose
    checkpoint was deleted but whose counter survived is planned at `n_batches` over a database
    that no longer exists -- an empty loop, no rows, nothing raised.  The repair is therefore
    checked by PLANNING the repaired tree, not only by listing it.
  * THE `coupled` REFUSAL for batch-grain resume (site-dock 10 section 4), and that it does not
    cost the three starts that must still work.

The end-to-end half -- a real mid-flight kill that produces a torn tree nobody hand-built --
is `Tests/e2e/test_coupled_resume_e2e.py`.  A planted matrix only ever proves the reconciler
reads a tree this file wrote.

Run:  python -m pytest Tests/integration/test_coupled_resume_reconciler.py -q
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from types import SimpleNamespace

import pytest

import Optimization.simdriver.workunits as wu
from Optimization.persistence.Picking_Data import create_run, init_run_db
from Optimization.runschema.sim_manifest import _load_resume, _resume_path, _save_resume
from Optimization.simdriver.strategy_runner import save_worker_checkpoint

_LOG = logging.getLogger('test_coupled_resume_reconciler')

#: The pinned arm suite for a planted pair.  Two ranks, because a one-rank pair cannot tell
#: "the reconciler repaired the rank that disagreed" from "the reconciler repaired everything".
_ARMS = {'store': ['fifo', 'rank_labor'], 'fulfillment': ['ful_fifo', 'ful_rank']}
_N_BATCHES = 100


# ── planting ───────────────────────────────────────────────────────────────────

def _plant_leaf(pair_dir, cfg, channel, arms, *, positions, complete):
    """One channel-run dir in a stated state: a real db per arm, its checkpoint, the resume
    record, and (for a finalized leaf) `sim_meta.json` with the resume file removed -- exactly
    the pair of facts `_finalize_config_run` leaves behind.

    The databases are REAL (`init_run_db` + `create_run`), because the repair's whole point is
    that a replayed arm must not open a second run in a populated file, and an empty table
    diffs clean.
    """
    run_dir = os.path.join(pair_dir, cfg, channel)
    os.makedirs(run_dir, exist_ok=True)
    run_ids = {}
    for arm in arms:
        db = wu._arm_db_path(run_dir, arm)
        init_run_db(db)
        run_ids[arm] = create_run(db, 'comparison', {}, identity={'strategy_key': arm})
        if positions[arm]:
            save_worker_checkpoint(run_dir, arm, positions[arm])
    _save_resume(run_dir, run_ids, dict(positions))
    if complete:
        # `_finalize_config_run`'s order: the marker first, then the resume file.
        with open(os.path.join(run_dir, 'sim_meta.json'), 'w') as f:
            json.dump({'run_dir': run_dir, 'channel': channel, 'strategies': []}, f)
        os.remove(_resume_path(run_dir))
        for arm in arms:                       # finalize also clears every checkpoint
            _ck = os.path.join(run_dir, f'_ckpt_{arm}.pkl')
            if os.path.exists(_ck):
                os.remove(_ck)
    return run_dir


def _plant_pair(tmp_path, *, store, fulfillment, site_dbs=True):
    """A coupled pair's two leaves plus the unit's site DBs, one per rank.

    `store` / `fulfillment` are `(positions_dict, complete_bool)`.  Returns the arguments
    `_reconcile_coupled_unit` is called with plus the site-DB paths, so every test asserts the
    same surface: three things per leaf and one per rank.
    """
    pair_dir = str(tmp_path / 'mixed')
    leaves = [
        (_plant_leaf(pair_dir, 'store', 'store', _ARMS['store'],
                     positions=store[0], complete=store[1]), _ARMS['store']),
        (_plant_leaf(pair_dir, 'ful_calibrated', 'fulfillment', _ARMS['fulfillment'],
                     positions=fulfillment[0], complete=fulfillment[1]), _ARMS['fulfillment']),
    ]
    site = []
    for rank in zip(_ARMS['store'], _ARMS['fulfillment']):
        p = wu._site_db_path(pair_dir, *rank)
        if site_dbs:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'wb') as f:
                f.write(b'SQLite format 3\x00')     # a stand-in: the writer is site-dock 24's
        site.append(p)
    return pair_dir, leaves, site


def _at(n):
    return {a: n for arms in _ARMS.values() for a in arms}


def _survivors(leaves):
    """Every per-arm file still on disk, per leaf — the surface the repair acts on."""
    out = []
    for run_dir, arms in leaves:
        out.append({
            'dbs':   sorted(a for a in arms if os.path.exists(wu._arm_db_path(run_dir, a))),
            'ckpts': sorted(a for a in arms
                            if os.path.exists(os.path.join(run_dir, f'_ckpt_{a}.pkl'))),
            'meta':  os.path.exists(os.path.join(run_dir, 'sim_meta.json')),
            'resume': os.path.exists(_resume_path(run_dir)),
        })
    return out


def _reconcile(pair_dir, leaves, **kw):
    return wu._reconcile_coupled_unit(pair_dir, leaves, _N_BATCHES, _LOG,
                                      tag='mixed/coupled', **kw)


# ── the four-state matrix ──────────────────────────────────────────────────────

def test_both_leaves_complete_skips_and_touches_nothing(tmp_path):
    """The only state that returns True — and it must be inert, because a repair here would
    re-run a FINISHED pair."""
    pair_dir, leaves, site = _plant_pair(
        tmp_path, store=(_at(_N_BATCHES), True), fulfillment=(_at(_N_BATCHES), True))
    before = _survivors(leaves)

    assert _reconcile(pair_dir, leaves) is True

    assert _survivors(leaves) == before, 'a complete pair was modified'
    assert all(os.path.exists(p) for p in site), 'a complete pair lost its site DB'


def test_neither_leaf_started_is_incomplete_and_unrepaired(tmp_path):
    """A pair whose leaf dirs do not exist yet: not complete, and nothing to repair.  The
    reconciler must survive a tree that is simply absent, because `--resume` of a run that
    died before its first prepare is exactly that."""
    pair_dir = str(tmp_path / 'mixed')
    leaves = [(os.path.join(pair_dir, 'store', 'store'), _ARMS['store']),
              (os.path.join(pair_dir, 'ful_calibrated', 'fulfillment'), _ARMS['fulfillment'])]

    assert _reconcile(pair_dir, leaves) is False
    assert not os.path.exists(pair_dir), 'the reconciler created a tree it was only reading'


def test_both_leaves_partial_and_in_step_are_left_alone(tmp_path):
    """The ordinary resume.  Both leaves mid-flight at the same batch in every arm: the
    per-arm strategy-granularity reset inside `_plan_strategy_start` owns the LEAVES, and a
    repair here would be a second reset of the same arms for no reason.

    THE SITE DB IS THE EXCEPTION, and it was wrong here until site-dock 24 (found in review).
    `_plan_strategy_start` resets an arm's `sim_<arm>.db`, its keyframe sibling and its
    checkpoint; it does not know the pair's site DB.  So an in-step PARTIAL pair -- not torn,
    not stale, and therefore "left alone" -- would replay from batch 0 with its site DB
    intact and append a SECOND run of trailer and drain rows to it.  `find_run` resolves
    `ORDER BY run_id LIMIT 1`, the OLDEST run, so every site yard figure would then render
    over the abandoned partial one with no symptom at all.  The discard set is every rank
    that REPLAYS, which is what this pair is.
    """
    pair_dir, leaves, site = _plant_pair(
        tmp_path, store=(_at(30), False), fulfillment=(_at(30), False))
    before = _survivors(leaves)

    assert _reconcile(pair_dir, leaves) is False

    assert _survivors(leaves) == before, 'an in-step partial pair was repaired'
    assert not any(os.path.exists(p) for p in site), (
        'a replaying pair kept its site DB; the replay would append a second run to it and '
        '`find_run` would go on answering from the first')


def test_a_finished_rank_keeps_its_site_db_while_a_partial_one_loses_it(tmp_path):
    """NON-VACUITY for the discard rule: the set is every rank that REPLAYS, not every rank.

    A rank already at `n_batches` in BOTH leaves is planned as a done arm and writes nothing
    more, so its site DB is the finished file the analysis will read.  Discarding it would
    delete a complete result to protect against a replay that is not going to happen — which
    is the opposite error from the one the rule exists for, and just as silent.
    """
    store = {'fifo': _N_BATCHES, 'rank_labor': 40}     # rank 0 finished, rank 1 partial
    ful = {'ful_fifo': _N_BATCHES, 'ful_rank': 40}
    pair_dir, leaves, site = _plant_pair(
        tmp_path, store=(store, False), fulfillment=(ful, False))

    assert _reconcile(pair_dir, leaves) is False

    assert os.path.exists(site[0]), (
        'the FINISHED rank lost its site DB; it replays nothing, so the file it keeps is '
        'the complete result the analysis reads')
    assert not os.path.exists(site[1]), 'the partial rank kept its site DB'


def test_a_torn_pair_is_un_finalized_and_both_leaves_replay(tmp_path):
    """THE STATE THIS FUNCTION EXISTS FOR: store finalized, fulfillment still mid-flight.

    Reachable only by a kill between the two `_finalize_config_run` calls.  The repair is
    whole-dir on BOTH leaves -- a finalized leaf has had its checkpoints cleaned, so there is
    no per-arm position left to compare -- and the complete leaf's marker is removed, which is
    what takes it back out of "complete".
    """
    pair_dir, leaves, site = _plant_pair(
        tmp_path, store=(_at(_N_BATCHES), True), fulfillment=(_at(_N_BATCHES), False))

    assert _reconcile(pair_dir, leaves) is False, 'a torn pair reported itself complete'

    for (run_dir, arms), state in zip(leaves, _survivors(leaves)):
        assert state['dbs'] == [], f'{run_dir} kept arm databases a replay would append to'
        assert state['ckpts'] == [], f'{run_dir} kept checkpoints'
        assert not state['meta'], f'{run_dir} is still finalized'
        assert not state['resume'], f'{run_dir} kept a resume record naming reset arms'
    assert not any(os.path.exists(p) for p in site), \
        'the site DB survived: a replayed unit would append a SECOND run of trailer, drain ' \
        'and door rows to it'


def test_a_torn_pair_repairs_in_the_other_direction_too(tmp_path):
    """The mirror image — fulfillment finalized, store not.  The two leaves are positional
    everywhere else in this harness, so a repair that only ever looked at slot 0 would pass
    the test above and fail here."""
    pair_dir, leaves, site = _plant_pair(
        tmp_path, store=(_at(60), False), fulfillment=(_at(_N_BATCHES), True))

    assert _reconcile(pair_dir, leaves) is False
    for state in _survivors(leaves):
        assert state['dbs'] == [] and state['ckpts'] == []
        assert not state['meta'] and not state['resume']
    assert not any(os.path.exists(p) for p in site)


# ── the skew: two leaves, one arm, two positions ───────────────────────────────

def test_a_skewed_arm_pair_replays_in_both_leaves(tmp_path):
    """Leaf A saved its checkpoint and the kill landed before leaf B saved its own.

    `_run_strategy_worker_impl` refuses a unit whose leaves do not share one batch range, and
    that refusal CANNOT CLEAR ITSELF: the leaf already at `n_batches` is planned as a done arm
    and never reset, its sibling resets to 0, and every later `--resume` reproduces the same
    disagreement.  So the repair is what makes the run resumable at all.

    Only the disagreeing rank is discarded.  The rank that is in step keeps everything, which
    is what separates "repaired the tear" from "wiped the pair".
    """
    store = {'fifo': _N_BATCHES, 'rank_labor': 40}
    ful   = {'ful_fifo': 90, 'ful_rank': 40}        # rank 0 skewed, rank 1 in step
    pair_dir, leaves, site = _plant_pair(
        tmp_path, store=(store, False), fulfillment=(ful, False))

    assert _reconcile(pair_dir, leaves) is False

    st, fl = _survivors(leaves)
    assert st['dbs'] == ['rank_labor'] and st['ckpts'] == ['rank_labor'], \
        f'store leaf: the skewed rank was not the one discarded ({st})'
    assert fl['dbs'] == ['ful_rank'] and fl['ckpts'] == ['ful_rank'], \
        f'fulfillment leaf: the skewed rank was not the one discarded ({fl})'
    assert st['resume'] and fl['resume'], 'the in-step rank lost its resume record'
    assert not os.path.exists(site[0]), 'the skewed rank kept its site DB'
    # THE IN-STEP RANK LOSES ITS SITE DB TOO, and that is not the same claim as the leaf
    # assertions above: the leaves' in-step rank is left to `_plan_strategy_start`, which
    # replays it from batch 0 and knows nothing about the site DB. Only a rank that is
    # FINISHED keeps its file (site-dock 24, found in review).
    assert not os.path.exists(site[1]), (
        'the in-step rank kept its site DB while replaying from batch 0')
    # ...and neither leaf is finalized, so nothing was un-finalized that was not finalized.
    assert not st['meta'] and not fl['meta']


def test_the_resume_record_forgets_only_the_discarded_arm(tmp_path):
    """`reset_strategy_db` cannot reach `resume.pkl`, and the counter there is the planner's
    fallback once the checkpoint is gone.  A record that still named the discarded arm would
    plan it at its old batch over a database that no longer exists: an empty loop, no rows,
    nothing raised."""
    store = {'fifo': _N_BATCHES, 'rank_labor': 40}
    ful   = {'ful_fifo': 90, 'ful_rank': 40}
    pair_dir, leaves, _site = _plant_pair(
        tmp_path, store=(store, False), fulfillment=(ful, False))

    _reconcile(pair_dir, leaves)

    rec = _load_resume(leaves[0][0])
    assert set(rec['run_ids']) == {'rank_labor'}, rec['run_ids']
    assert set(rec['next_batch']) == {'rank_labor'}, rec['next_batch']
    assert rec['next_batch']['rank_labor'] == 40, 'the surviving arm lost its position'


def test_the_repaired_tree_plans_a_fresh_start_with_one_run_per_db(tmp_path):
    """THE PAYOFF, checked by planning rather than by listing.

    A repaired leaf must reach `_plan_strategy_start`'s FRESH branch: no resume entry, no
    database, so `create_run` opens exactly one run.  Two runs in one file has no symptom --
    `find_run` resolves `ORDER BY run_id LIMIT 1`, the ABANDONED one -- which is why the
    duplicate-run refusal exists, and why a repair that left either the db or the resume entry
    behind would be caught here and nowhere else.
    """
    pair_dir, leaves, _site = _plant_pair(
        tmp_path, store=(_at(_N_BATCHES), True), fulfillment=(_at(_N_BATCHES), False))
    _reconcile(pair_dir, leaves)

    for run_dir, arms in leaves:
        resume = _load_resume(run_dir)
        prev_ids = (resume or {}).get('run_ids', {})
        for arm in arms:
            db = wu._arm_db_path(run_dir, arm)
            s = SimpleNamespace(key=arm, run_type='comparison')
            rid, start = wu._plan_strategy_start(
                run_dir, s, _N_BATCHES, db, {}, {}, 'strategy',
                prev_ids.get(arm), 0, resume is not None, _LOG)
            assert start == 0, f'{arm} did not replay from batch 0 after the repair'
            assert isinstance(rid, int)
            con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
            try:
                n = con.execute('SELECT COUNT(*) FROM simulation_runs').fetchone()[0]
            finally:
                con.close()
            assert n == 1, f'{arm} db holds {n} runs after the repair; find_run would answer ' \
                           f'every query from the abandoned one'


# ── the live-retry exemption ───────────────────────────────────────────────────

def test_a_repair_is_declined_while_the_pool_is_still_up(tmp_path):
    """The supervisor's rebuild-and-resubmit passes `mid_flight`, and a tear seen THERE means
    a finalize raised while every unit was already in `done_uids`.  Those units are filtered
    out of the resubmission, so the repair would delete output nothing rebuilds."""
    pair_dir, leaves, site = _plant_pair(
        tmp_path, store=(_at(_N_BATCHES), True), fulfillment=(_at(_N_BATCHES), False))
    before = _survivors(leaves)
    errors = []
    log = SimpleNamespace(warning=lambda m, *a, **k: None,
                          error=lambda m, *a, **k: errors.append(str(m)),
                          info=lambda m, *a, **k: None)

    assert wu._reconcile_coupled_unit(pair_dir, leaves, _N_BATCHES, log,
                                      tag='mixed/coupled', mid_flight=True) is False

    assert _survivors(leaves) == before, 'a live retry repaired a torn pair'
    assert all(os.path.exists(p) for p in site)
    assert errors and 'TORN' in errors[0], f'the declined repair was not reported: {errors}'


# ── the site DB path ───────────────────────────────────────────────────────────

def test_the_site_db_sits_under_the_reserved_pair_subtree(tmp_path):
    """Site-dock 03 section 1.  The pair is the only directory dominating both leaves, the arm
    pair rides in the FILENAME, and the `_` prefix is what every run-tree walker skips -- a
    site DB named `sim_*.db` beside a leaf's would surface as an extra arm of a phantom
    config."""
    from Optimization.runschema.schema import RESERVED_PREFIX
    p = wu._site_db_path(str(tmp_path), 'fifo', 'ful_fifo')
    assert os.path.basename(p) == 'inbound_fifo__ful_fifo.db'
    assert os.path.basename(os.path.dirname(p)).startswith(RESERVED_PREFIX)
    assert os.path.dirname(os.path.dirname(p)) == str(tmp_path), 'the site DB left the pair'


# ── the batch-grain refusal gains a third, independent reason ──────────────────

def test_batch_grain_resume_is_refused_for_a_coupled_leaf(monkeypatch, tmp_path):
    """Site-dock 10 section 4.  Two leaves resume from two checkpoints in two directories, so
    a batch-level resume could lawfully start them on different site DAYS -- a day whose
    shared dock, put pool and receiving clock never existed in either leaf.

    Stated as its OWN reason rather than left to `roll_over` / `receiving`, because neither of
    those is a statement about coupling: a derived receiving crew can round below 1 and the
    carry is a work-day knob any cell may clear, so today's coverage is a coincidence.  Both
    are switched OFF here, which is what makes this test about coupling.
    """
    monkeypatch.setattr(wu, 'init_run_db', lambda p: None)
    monkeypatch.setattr(wu, 'create_run', lambda p, rt, params, identity=None: 999)
    monkeypatch.setattr(wu, 'reset_strategy_db', lambda rd, db, key: None)
    monkeypatch.setattr(wu, 'load_worker_checkpoint', lambda rd, key: 30)
    s = SimpleNamespace(key='fifo', run_type='comparison')

    def plan(gran, prev_id, is_resume, **kw):
        return wu._plan_strategy_start(str(tmp_path), s, _N_BATCHES, 'sim_fifo.db', {}, {},
                                       gran, prev_id, 0, is_resume, _LOG, **kw)

    with pytest.raises(RuntimeError) as exc:
        plan('batch', 7, True, coupled=True)
    msg = str(exc.value)
    assert 'batch-level resume' in msg and 'coupled' in msg, msg
    assert 'site' in msg, f'the refusal does not say what breaks: {msg}'

    # ...and it costs none of the starts that must still work on a coupled leaf.
    assert plan('strategy', 7, True, coupled=True) == (999, 0), \
        'the default granularity stopped replaying a coupled leaf from batch 0'
    assert plan('batch', None, False, coupled=True) == (999, 0), 'a fresh coupled leaf refused'
    monkeypatch.setattr(wu, 'load_worker_checkpoint', lambda rd, key: _N_BATCHES)
    assert plan('batch', 7, True, coupled=True) == (7, _N_BATCHES), \
        'a DONE coupled leaf resumes into nothing and must not be refused'


def test_both_leaves_of_a_site_run_are_prepared_as_coupled(monkeypatch):
    """The refusal is only worth anything if the leaves are actually MARKED.

    `coupled` defaults to False so every uncoupled caller stays byte-identical, which means a
    `_prepare_site_run` that forgot to pass it would produce leaves that happily resume at a
    batch -- and the two would then start on different site days.  Nothing downstream could
    tell: the leaves look exactly like the uncoupled ones they replaced.
    """
    seen = []

    def _fake_prepare(ch, cfg, mixed, shared, pair_dir, log, workers=1,
                      resume_granularity='strategy', coupled=False):
        seen.append((ch.name, coupled))
        return ([{'channel_key': ch.name, 'strategy': 'a', 'n_batches': 2,
                  'put_crew': {'size': 2}, 'recv_crew': None, 'staffing': {}}],
                [{'channel': ch.name}])
    monkeypatch.setattr(wu, '_prepare_channel_run', _fake_prepare)
    channel_runs = [(SimpleNamespace(name='store'), {'name': 'store'}),
                    (SimpleNamespace(name='fulfillment'), {'name': 'ful_calibrated'})]

    units, _sk = wu._prepare_site_run(channel_runs, True, {}, 'pair', _LOG)

    assert len(units) == 1
    assert sorted(seen) == [('fulfillment', True), ('store', True)], \
        f'a leaf of a coupled unit was prepared as uncoupled: {seen}'


def test_the_supervisor_declares_its_retries_mid_flight(monkeypatch, tmp_path):
    """`skip_completed` widens to True on every retry, so without a separate flag the
    reconciler could not tell a `--resume` of a dead run from a live rebuild -- and repairing
    during the latter deletes the output of units already in `done_uids`, which are filtered
    out of the resubmission and never rebuilt."""
    from Optimization.simdriver import supervisor as sup
    gk = ('prof', 'cfg', 'store')
    uids = [(*gk, 'a'), (*gk, 'b')]
    units = [(u, {'strategy': u[3]}) for u in uids]
    meta = {gk: {'sim_skeleton': {'run_dir': str(tmp_path), 'name': 'cfg', 'inventory': 'p',
                                  'strategies': [], 'optimal_sigma_fd': 0.0,
                                  'optimal_work': 0.0},
                 'members': frozenset(uids)}}
    flags = []

    def _fake_build(*a, **k):
        flags.append(k['mid_flight'])
        return units, dict(meta)
    monkeypatch.setattr(sup, '_build_work_units', _fake_build)
    n = {'calls': 0}

    def _fake_pool(remaining, meta_, mw, rec, log, done_uids, finalized, cell='', run_root=None):
        n['calls'] += 1
        if n['calls'] == 1:
            done_uids.add(uids[0])
            return set(), True                     # hard worker death -> rebuild
        for uid, _sa in remaining:
            done_uids.add(uid)
        return set(), False
    monkeypatch.setattr(sup, '_run_pool', _fake_pool)

    sup._supervise([('prof', 'i', 'a')], str(tmp_path), {'prof': {}}, 1, _LOG,
                   log_queue=None, max_tasks_per_child=1, skip_completed=False,
                   max_retries=2, resume_granularity='strategy')

    assert flags == [False, True], \
        f'the first build must not be mid-flight and every retry must be: {flags}'


def test_an_uncoupled_leaf_is_untouched_by_the_new_reason(monkeypatch, tmp_path):
    """The flag-off half: with `coupled` defaulted, a batch-grain resume on a quiet run still
    WARNS and continues.  A refusal that also caught every uncoupled arm would be a silent
    behaviour change on every run in this repo's history."""
    monkeypatch.setattr(wu, 'load_worker_checkpoint', lambda rd, key: 30)
    warned = []
    log = SimpleNamespace(info=lambda m, *a, **k: None,
                          warning=lambda m, *a, **k: warned.append(str(m)))
    s = SimpleNamespace(key='fifo', run_type='comparison')

    assert wu._plan_strategy_start(str(tmp_path), s, _N_BATCHES, 'sim_fifo.db', {}, {},
                                   'batch', 7, 0, True, log) == (7, 30)
    assert warned and 'NOT bit-identical' in warned[0], warned
