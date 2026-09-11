"""test_unit_identity_is_carried.py — the parent reads a unit's identity from its PAYLOAD.

A work-unit uid is positional, and its four slots are `(pair, config, channel, arm)` only
because every unit today is one channel leaf.  The coupled unit (site-dock 02) is
`(label, 'coupled', arm_store, arm_ful)` — same arity, different meanings — so every parent-side
slice of a uid is a seam.  `_run_pool`'s `expected_pick` attach sliced it INDEPENDENTLY of the
finalize gate, so 02's carried `group_keys` would not have reached it: `meta[(label, 'coupled',
arm_store)]` raises KeyError inside the success `try`, and a unit that SUCCEEDED is logged
`strategy FAILED`, added to failed_uids, and never finalizes either leaf.

These tests drive `_run_pool` with an inline fake pool (no real process pool, no DBs), so what
is exercised is exactly the parent-side bookkeeping.

Run:  python -m pytest Tests/integration/test_unit_identity_is_carried.py -q
"""
from __future__ import annotations

import concurrent.futures
import logging

from Optimization.simdriver import supervisor as sv
from Optimization.simdriver.workunits import _stamp_identity

_LOG = logging.getLogger('test_unit_identity')


class _InlinePool:
    """A ProcessPoolExecutor stand-in that runs each submit immediately in this process."""

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, fn, sa):
        fut = concurrent.futures.Future()
        try:
            fut.set_result(fn(sa))
        except BaseException as exc:                                   # noqa: BLE001
            fut.set_exception(exc)
        return fut


def _run(remaining, meta, monkeypatch, worker):
    monkeypatch.setattr(sv.concurrent.futures, 'ProcessPoolExecutor', _InlinePool)
    monkeypatch.setattr(sv, '_run_strategy_worker', worker)
    done, finalized = set(), set()
    failed, broke = sv._run_pool(remaining, meta, 1, 1, _LOG, done, finalized, cell='k1_off')
    return failed, broke, done, finalized


def _res(**kw):
    d = dict(done=3, elapsed=1.0, cons_breaks=0, demand_breaks=0)
    d.update(kw)
    return d


def _payload(group_keys, arm_key):
    return {'group_keys': list(group_keys), 'arm_key': arm_key, 'strategy': arm_key}


def _meta(gk, keys):
    sk = {'run_dir': None, 'strategies': [{'key': k} for k in keys]}
    return {gk: {'sim_skeleton': sk, 'members': frozenset((*gk, k) for k in keys)}}


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
    """
    gk_s, gk_f = ('pairA', 'store', 'store'), ('pairA', 'ful_calibrated', 'fulfillment')
    meta = {**_meta(gk_s, ['opt_map']), **_meta(gk_f, ['opt_rank_labor'])}
    uid = ('pairA', 'coupled', 'opt_map', 'opt_rank_labor')
    remaining = [(uid, _payload([gk_s, gk_f], 'opt_map'))]
    monkeypatch.setattr(sv, '_finalize_config_run', lambda sk: {})
    with caplog.at_level(logging.WARNING):
        failed, broke, done, _fin = _run(remaining, meta, monkeypatch,
                                         lambda sa: _res(expected_pick=41.5))
    assert failed == set(), 'a SUCCEEDED unit was recorded as failed'
    assert done == {uid}
    assert 'strategy FAILED' not in caplog.text
    # one value, two leaves — refused rather than copied onto both arms
    assert 'not attached' in caplog.text
    assert 'expected_pick' not in meta[gk_s]['sim_skeleton']['strategies'][0]
    assert 'expected_pick' not in meta[gk_f]['sim_skeleton']['strategies'][0]


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
