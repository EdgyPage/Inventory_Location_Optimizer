"""test_unit_identity_is_carried.py — the parent reads a unit's identity from its PAYLOAD.

A work-unit uid is positional, and its four slots are `(pair, config, channel, arm)` only
because every unit today is one channel leaf.  The coupled unit (site-dock 02) is
`(label, 'coupled', arm_store, arm_ful)` — same arity, different meanings — so every parent-side
slice of a uid is a seam.  The success path's `expected_pick` attach once sliced it
INDEPENDENTLY of the finalize gate, so 02's carried `group_keys` would not have reached it:
`meta[(label, 'coupled', arm_store)]` raises KeyError inside the success path, and a unit that
SUCCEEDED is logged `strategy FAILED`, recorded unfinished, and never finalizes either leaf.

These tests drive the sim books through the work pool with an inline executor (no real
process pool, no DBs), so what is exercised is exactly the parent-side bookkeeping
(`supervisor.SimBooks` behind `workpool.WorkPool`).

Run:  python -m pytest Tests/integration/test_unit_identity_is_carried.py -q
"""
from __future__ import annotations

import logging

from Optimization.simdriver import supervisor as sv
from Optimization.simdriver import workpool as wp
from Optimization.simdriver.workunits import _stamp_identity

_LOG = logging.getLogger('test_unit_identity')


def _run(remaining, meta, monkeypatch, worker):
    monkeypatch.setattr(sv, '_run_strategy_worker', worker)
    books = sv.SimBooks(_LOG, run_root=None)
    books.register('k1_off', meta)
    with wp.WorkPool(1, _LOG, executor_factory=wp.InlineExecutor, max_retries=0,
                     worker_logging=False,
                     on_success=books.on_success, on_failure=books.on_failure) as pool:
        pool.submit('k1_off', sv.sim_jobs('k1_off', remaining))
        left = pool.finish(rebuild=lambda c: [])
    st = books.cells['k1_off']
    return set(left.get('k1_off', [])), pool.broke, st['done'], st['finalized']


def _res(arm='opt_map', **kw):
    d = dict(strategy=arm, done=3, elapsed=1.0, cons_breaks=0, demand_breaks=0)
    d.update(kw)
    return d


def _payload(group_keys, arm_key):
    return {'group_keys': list(group_keys), 'arm_key': arm_key, 'strategy': arm_key}


def _meta(gk, keys, members=None):
    """One group's meta.  `members` defaults to the per-channel unit uids; a COUPLED group
    takes the unit uids instead, because one coupled unit writes both leaves and neither may
    finalize until every unit succeeded (`workunits._build_work_units`)."""
    sk = {'run_dir': None, 'strategies': [{'key': k} for k in keys]}
    return {gk: {'sim_skeleton': sk,
                 'members': frozenset(members if members is not None
                                      else ((*gk, k) for k in keys))}}


def test_expected_pick_lands_on_the_arm_the_payload_names(tmp_path, monkeypatch):
    gk = ('pairA', 'store', 'store')
    meta = _meta(gk, ['uni_fifo', 'opt_map'])
    uid = (*gk, 'opt_map')
    remaining = [(uid, _payload([gk], 'opt_map'))]
    monkeypatch.setattr(sv, '_finalize_config_run', lambda sk: {})
    failed, broke, done, _fin = _run(remaining, meta, monkeypatch,
                                     lambda sa: _res(expected_pick=41.5))
    assert (failed, broke) == (set(), False) and done == {uid}
    by_key = {s['key']: s for s in meta[gk]['sim_skeleton']['strategies']}
    assert by_key['opt_map']['expected_pick'] == 41.5
    assert 'expected_pick' not in by_key['uni_fifo'], 'attached to the wrong arm'


def test_a_coupled_shaped_uid_succeeds_instead_of_being_logged_as_a_failure(monkeypatch, caplog):
    """The regression this seam exists for.

    The unit's uid slots are `(label, 'coupled', arm_store, arm_ful)`; its payload names the two
    leaves it finalizes.  Sliced, `uid[:3]` is `('pairA', 'coupled', 'opt_map')` — a key `meta`
    does not hold, so the success path raised KeyError and the unit was recorded as FAILED.

    A coupled unit returns one result PER LEAF (site-dock 18 built the shape 02 settled), and
    each leaf's own expected day lands on its own arm.  The single-value refusal site-dock 11
    recorded was explicitly a placeholder for that shape; it is gone because the value is no
    longer one.
    """
    gk_s, gk_f = ('pairA', 'store', 'store'), ('pairA', 'ful_calibrated', 'fulfillment')
    uid = ('pairA', 'coupled', 'opt_map', 'opt_rank_labor')
    meta = {**_meta(gk_s, ['opt_map'], members=[uid]),
            **_meta(gk_f, ['opt_rank_labor'], members=[uid])}
    remaining = [(uid, _payload([gk_s, gk_f], None))]
    monkeypatch.setattr(sv, '_finalize_config_run', lambda sk: {})
    with caplog.at_level(logging.WARNING):
        failed, broke, done, _fin = _run(
            remaining, meta, monkeypatch,
            lambda sa: {'leaves': [_res('opt_map', expected_pick=41.5),
                                   _res('opt_rank_labor', expected_pick=17.25)]})
    assert failed == set(), 'a SUCCEEDED unit was recorded as failed'
    assert done == {uid}
    assert 'strategy FAILED' not in caplog.text
    # EACH leaf's day on its OWN arm. A single value copied onto both would put one leaf's
    # expected day on the other's arm, which is the failure the refusal used to prevent.
    assert meta[gk_s]['sim_skeleton']['strategies'][0]['expected_pick'] == 41.5
    assert meta[gk_f]['sim_skeleton']['strategies'][0]['expected_pick'] == 17.25
    # and both leaves finalized, because the one unit is every group's only member
    assert _fin == {gk_s, gk_f}


def test_a_unit_whose_results_do_not_match_its_group_keys_is_refused(monkeypatch, caplog):
    """`group_keys` and the returned `leaves` are POSITIONAL, so a length disagreement means
    nobody can say which result belongs to which leaf.  Refused loudly rather than zipped to
    the shorter side, which would attach one leaf's numbers and silently drop the other's."""
    gk_s, gk_f = ('pairA', 'store', 'store'), ('pairA', 'ful_calibrated', 'fulfillment')
    meta = {**_meta(gk_s, ['opt_map']), **_meta(gk_f, ['opt_rank_labor'])}
    uid = ('pairA', 'coupled', 'opt_map', 'opt_rank_labor')
    remaining = [(uid, _payload([gk_s, gk_f], None))]
    monkeypatch.setattr(sv, '_finalize_config_run', lambda sk: {})
    with caplog.at_level(logging.ERROR):
        failed, _broke, done, _fin = _run(remaining, meta, monkeypatch,
                                          lambda sa: _res('opt_map', expected_pick=41.5))
    assert failed == {uid} and done == set()
    assert 'positional' in caplog.text


def test_the_builder_stamps_exactly_the_slices_it_replaced():
    """Byte-identity at the producer: the uid `_stamp_identity` returns is the tuple the
    builder used to assemble by hand, and what it stamps on the payload is exactly the two
    slices every parent-side reader used to take off that uid."""
    for channel_key, label, cfg in (('store', 'pairA', 'store'),
                                    ('fulfillment', 'pairA', 'ful_calibrated'),
                                    ('', 'pairB', 'store')):          # pre-channel payload
        sa = {'strategy': 'opt_rank_labor_norsl', 'channel_key': channel_key}
        uid = _stamp_identity(sa, label, cfg)
        assert uid == (label, cfg, channel_key, 'opt_rank_labor_norsl')
        assert sa['group_keys'] == [uid[:3]]
        assert sa['arm_key'] == uid[3]

    sa = {'strategy': 'uni_fifo_norsl'}                                # channel_key absent
    assert _stamp_identity(sa, 'pairA', 'store') == ('pairA', 'store', '', 'uni_fifo_norsl')
