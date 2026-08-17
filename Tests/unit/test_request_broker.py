"""test_request_broker.py — the request broker's grant/deny/tally contract (core/requests.py).

The broker sits between every @evaluation's declared `needs=` and the versioned resources.
These tests pin the three promises the module docstring makes, WITHOUT sim DBs: a missing
SOURCE is a Denied sentinel (never an exception), a denial SKIPS the render through the
driver while a grant runs it, and the per-process tally answers "who got what".  The
everything-present path is covered end to end by Tests/e2e/test_channel_runner_smoke.py,
which runs the full analysis suite over real DBs through the same choke point.

Run:  python -m pytest Tests/unit/test_request_broker.py -q
"""
from __future__ import annotations

import logging
import os

import pytest

from Optimization.Performance_Evaluations.core import requests
from Optimization.Performance_Evaluations.core.registry import Evaluation


# ── stand-in contexts (duck-typed exactly as far as the broker reaches) ──────────

class _ConfigCtx:
    """EvalContext stand-in: strategies with db_paths, the broker-visible caches, a logger."""

    def __init__(self, tmp_path, present=('uni_fifo',), absent=()):
        self.strategies = []
        for key in present:
            p = str(tmp_path / f'sim_{key}.db')
            open(p, 'wb').close()                       # exists; never opened by these tests
            self.strategies.append({'key': key, 'db_path': p, 'run_id': 1})
        for key in absent:
            self.strategies.append({'key': key,
                                    'db_path': str(tmp_path / f'sim_{key}.db'),
                                    'run_id': 1})
        self._by_key = {s['key']: s for s in self.strategies}
        self._bcache: dict = {}
        self._tcache: dict = {}
        self._series = None
        self._breakdown = None
        self.log = logging.getLogger('test-broker')
        self.name = 'stub'


class _AggCtx:
    def __init__(self, series_list):
        self.profile_series_list = series_list
        self.log = logging.getLogger('test-broker')


def _ev(key='stub.eval', scope='config', needs=('batch',)):
    return Evaluation(key=key, label='stub', scope=scope, needs=tuple(needs),
                      render=lambda ctx, params: None)


@pytest.fixture(autouse=True)
def _fresh_tally():
    requests.tally_snapshot(reset=True)
    yield
    requests.tally_snapshot(reset=True)


# ── the Denied sentinel ──────────────────────────────────────────────────────────

def test_denied_is_falsy_and_carries_its_reason():
    d = requests.Denied('sim db absent')
    assert not d
    assert d.reason == 'sim db absent'


# ── denial: absence never raises ─────────────────────────────────────────────────

def test_missing_sim_db_denies_every_config_request(tmp_path):
    """Each config-scope request names the absent arm instead of raising — the graceful-failure
    contract.  'batch' is representative; the loop proves the whole vocabulary shares the
    absence probe."""
    ctx = _ConfigCtx(tmp_path, present=('uni_fifo',), absent=('opt_slot',))
    for need in ('batch', 'task', 'series', 'breakdown'):
        got = requests.REQUESTS[('config', need)].compose(ctx)
        assert isinstance(got, requests.Denied), need
        assert 'opt_slot' in got.reason, (need, got.reason)


def test_aggregate_series_denies_on_an_empty_group():
    got = requests.REQUESTS[('aggregate', 'series')].compose(_AggCtx([]))
    assert isinstance(got, requests.Denied)
    assert 'series.json' in got.reason


def test_aggregate_series_grants_the_list_itself():
    series = [{'strategies': []}]
    assert requests.REQUESTS[('aggregate', 'series')].compose(_AggCtx(series)) is series


def test_unknown_need_is_denied_not_raised(tmp_path):
    denials = requests.resolve_needs(_ConfigCtx(tmp_path), _ev(needs=('no_such_resource',)))
    assert set(denials) == {'no_such_resource'}
    assert 'no request named' in denials['no_such_resource'].reason


# ── the driver choke point: denied -> skipped, granted -> rendered ───────────────

def test_driver_skips_the_render_on_denial_and_logs_it(tmp_path, caplog):
    from Optimization.Performance_Evaluations import driver
    calls = []
    ev = Evaluation(key='stub.denied', label='stub', scope='config', needs=('batch',),
                    render=lambda ctx, params: calls.append(1))
    ctx = _ConfigCtx(tmp_path, present=(), absent=('uni_fifo',))
    with caplog.at_level(logging.INFO, logger='test-broker'):
        driver._run_one(ctx, ev, {}, {})
    assert calls == [], 'a denied evaluation must not render'
    denied_lines = [r.message for r in caplog.records if 'DENIED' in r.message]
    assert denied_lines and 'stub.denied' in denied_lines[0]
    assert 'render skipped' in denied_lines[0]


def test_driver_renders_and_logs_granted_when_needs_are_met(caplog):
    from Optimization.Performance_Evaluations import driver
    calls = []
    ev = Evaluation(key='stub.granted', label='stub', scope='aggregate', needs=('series',),
                    render=lambda ctx, params: calls.append(1))
    ctx = _AggCtx([{'strategies': []}])
    with caplog.at_level(logging.INFO, logger='test-broker'):
        driver._run_one(ctx, ev, {}, {})
    assert calls == [1], 'a granted evaluation must render exactly once'
    granted = [r.message for r in caplog.records if '-> granted' in r.message]
    assert granted and 'stub.granted' in granted[0]


# ── the tally ────────────────────────────────────────────────────────────────────

def test_tally_counts_grants_and_denials_per_eval_and_resets(tmp_path):
    requests.resolve_needs(_AggCtx([{'s': 1}]), _ev(key='a.ok', scope='aggregate',
                                                    needs=('series',)))
    requests.resolve_needs(_ConfigCtx(tmp_path, present=(), absent=('x',)),
                           _ev(key='c.gone', needs=('batch',)))
    snap = requests.tally_snapshot(reset=True)
    assert snap['granted'] == {'a.ok': 1}
    assert snap['denied'] == {'c.gone': 1}
    assert requests.tally_snapshot() == {'granted': {}, 'denied': {}}


# ── the composed-SQL half ────────────────────────────────────────────────────────

def test_sql_facade_serves_the_head_vintage_and_refuses_unknown_ids():
    from Schema import identity
    from Schema.dataset import UnsupportedQuery
    head = identity.get('sim_db').declared_id()
    sql = requests.sql('batch_frame', head)
    assert 'SELECT' in sql.upper() and 'batch_stats' in sql
    with pytest.raises(UnsupportedQuery):
        requests.sql('batch_frame', 'no_such_vintage_id')
