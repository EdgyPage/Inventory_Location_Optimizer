"""test_render_error_surfacing.py — a render that raises must not vanish.

`driver._run_one` catches every render exception on purpose: one broken evaluation must
not sink the rest of a 21-minute analysis pass. The catch was also, for the whole life of
this suite, INVISIBLE — its only trace was a `ctx.log.error` call from inside a worker
process whose logger is wired to no file.

What that cost, concretely: `aggregate/sig.py` called `_boot_ci` without importing it, so
on every publish run (`analyze_run`'s default preset is `BY_INITIAL`, the one that reaches
that branch) the evaluation raised `NameError`, produced **none** of its declared figures,
and left no trace in any log — while the run-end summary printed
`all 82 evaluation requests granted, 0 denials`. A grant is a statement about an
evaluation's INPUTS. It says nothing about whether anything came out.

Pinned here:

  1. a raising render is recorded, with its exception, against its own evaluation key;
  2. it does NOT stop the evaluations after it;
  3. a grant is still counted — the two tallies answer different questions and neither
     substitutes for the other;
  4. the per-job reset clears errors too, or a reused worker double-counts at
     `granularity='graph'`;
  5. the tally survives the pickle round trip back to the parent, which is the only reason
     the parent can print it at all;
  6. and the message the NameError was hiding names the reason that actually applies — the
     first thing seeing a swallowed error bought was the discovery that the fallback
     underneath it had been wrong all along.

Run:  python -m pytest Tests/unit/test_render_error_surfacing.py -q
"""
from __future__ import annotations

import logging
import pickle

import pytest

from Optimization import Performance_Evaluations  # noqa: F401 — populate the registry
from Optimization.Performance_Evaluations import driver
from Optimization.Performance_Evaluations.core import requests
from Optimization.Performance_Evaluations.core.registry import (
    EVALUATIONS, EVAL_BY_KEY, Evaluation)


class _Ctx:
    """The surface `_run_one` touches: a logger, a footer, and nothing else."""
    def __init__(self):
        self.log = logging.getLogger('test-render-error')
        self.run_dir = ''

    def footer(self):
        return 'test'


def _ev(key, render, needs=()):
    """An Evaluation built directly — the decorator would register it globally."""
    return Evaluation(key=key, label=key, scope='config', needs=tuple(needs),
                      out_subdir='tables', render=render)


@pytest.fixture(autouse=True)
def _clean_tally():
    requests.tally_snapshot(reset=True)
    yield
    requests.tally_snapshot(reset=True)


def test_a_raising_render_is_recorded_with_its_exception():
    def _boom(ctx, params):
        raise NameError("name '_boot_ci' is not defined")

    driver._run_one(_Ctx(), _ev('zz.boom', _boom), {}, {})
    errs = requests.tally_snapshot()['errors']
    assert 'zz.boom' in errs, 'a swallowed render exception left no trace — the whole bug'
    n, msg = errs['zz.boom']
    assert n == 1
    assert '_boot_ci' in msg, 'the message must name what actually failed'


def test_a_raising_render_does_not_stop_the_ones_after_it():
    """The catch exists for this. Removing it is not the fix; seeing it is."""
    ran = []

    def _boom(ctx, params):
        raise RuntimeError('nope')

    def _ok(ctx, params):
        ran.append(True)

    ctx = _Ctx()
    driver._run_one(ctx, _ev('zz.boom2', _boom), {}, {})
    driver._run_one(ctx, _ev('zz.fine', _ok), {}, {})
    assert ran == [True]
    assert set(requests.tally_snapshot()['errors']) == {'zz.boom2'}


def test_a_grant_and_an_error_are_counted_separately():
    """The failure mode was a run reporting 'all requests granted' beside zero output."""
    def _boom(ctx, params):
        raise ValueError('x')

    driver._run_one(_Ctx(), _ev('zz.boom3', _boom, needs=()), {}, {})
    snap = requests.tally_snapshot()
    assert snap['granted'].get('zz.boom3') == 1, 'inputs really were granted'
    assert 'zz.boom3' in snap['errors'], '...and the render still produced nothing'


def test_the_per_job_reset_clears_errors_too():
    """At granularity='graph' a worker is reused; without the reset it double-reports."""
    def _boom(ctx, params):
        raise KeyError('k')

    driver._run_one(_Ctx(), _ev('zz.boom4', _boom), {}, {})
    assert requests.tally_snapshot(reset=True)['errors']
    assert requests.tally_snapshot()['errors'] == {}


def test_the_tally_survives_the_trip_back_to_the_parent():
    """It is returned from a spawned worker, so it must pickle."""
    def _boom(ctx, params):
        raise RuntimeError('carried home')

    driver._run_one(_Ctx(), _ev('zz.boom5', _boom), {}, {})
    snap = requests.tally_snapshot()
    assert pickle.loads(pickle.dumps(snap))['errors']['zz.boom5'][1] == snap['errors']['zz.boom5'][1]


# ── the two evaluations this was written against ────────────────────────────────

def test_aggregate_sig_can_reach_its_interval_helper():
    """`agg.sig`'s by-initial branch calls `_boot_ci`; it must be bound in that module.

    The `by_initial` fork is the DEFAULT for `analyze_run`'s preset, so this is the
    publish path, not a corner.
    """
    import Optimization.Performance_Evaluations.aggregate.sig as agg_sig
    assert hasattr(agg_sig, '_boot_ci'), \
        'the by-initial branch raises NameError and writes none of its figures'


def test_the_by_initial_absence_names_the_reason_that_actually_applies():
    """Fixing the NameError exposed the message behind it, and it was wrong.

    `_render_by_initial` reported "no uni/opt pairs" for every empty result. On the
    two-inventory sweep this suite publishes from there are 17 uni/opt pairs; the real
    reason is that `compute_aggregate_by_initial` needs `MIN_PAIRED_PROFILES` profiles and
    the sweep has two. An absence stated with the wrong cause sends the next reader
    looking for a bug in the arm naming.
    """
    from Optimization.Performance_Evaluations.aggregate.tables import (
        MIN_PAIRED_PROFILES, paired_profile_diagnosis)

    def _profile(keys):
        return {'strategies': [{'key': k} for k in keys]}

    # the token is a PREFIX ('uni_rank_labor'), which is what `_initial_of` parses
    paired = ['uni_fifo', 'opt_fifo', 'uni_rank_labor', 'opt_rank_labor']

    # the real shape: pairs exist, profiles do not
    msg = paired_profile_diagnosis([_profile(paired)] * (MIN_PAIRED_PROFILES - 1))
    assert 'pair' in msg and str(MIN_PAIRED_PROFILES) in msg
    assert 'no uni/opt pairs' not in msg, 'the retired message was a false statement here'

    # the shape the retired message described — it must still be sayable
    unpaired = paired_profile_diagnosis([_profile(['uni_fifo', 'uni_rank_labor'])] * 5)
    assert 'pair' in unpaired and 'none' in unpaired.lower()

    assert 'no profiles' in paired_profile_diagnosis([])

    # and the two diagnoses must not be the same sentence
    assert msg != unpaired


def test_layout_travel_reads_the_floor_attribute_that_exists():
    """`EvalContext` exposes `optimal`; `optimal_sigma_fd` is the key inside sim_result."""
    import inspect
    from Optimization.Performance_Evaluations.core.context import EvalContext
    from Optimization.Performance_Evaluations.layout import travel
    src = inspect.getsource(travel)
    assert "getattr(ctx, 'optimal_sigma_fd'" not in src, \
        'reading a non-existent attribute makes the layout floor unconditionally absent'
    assert hasattr(EvalContext, 'optimal') or 'self.optimal' in \
        inspect.getsource(EvalContext.__init__)
