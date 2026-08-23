"""The request broker — consumers ask for a resource BY NAME; this module gets it or says why not.

The `needs=` vocabulary every `@evaluation` already declares ('batch', 'task', 'series',
'breakdown') is the request vocabulary.  (An 'events' request once existed with no declarer —
picker events are streamed per-batch INSIDE the 'breakdown' compose, so a separate grant had
nothing to grant.  Re-adding it is six lines if a consumer ever streams events directly.)  A graph's render never navigates the
versioned inputs itself: the broker is the hard-coded intermediary that knows where each
resource lives (sim DBs via the `Picking_Data` loaders, which serve every vetted vintage
through `Schema.dataset`'s named queries; series/breakdown composed from those frames) and
returns the composed resource — or a `Denied(reason)` when the resource does not exist.

Three rules, in tension and resolved deliberately:

  * A MISSING resource never raises.  `resolve_needs` returns `Denied` sentinels; the driver
    logs `[access] <eval> requested ... -> DENIED (<reason>); render skipped` and moves on.
    Exceptions remain for real corruption — an unvetted schema, an unreadable file — which
    must stop a publish, not be skipped politely (see core/context.py's identity gate).
  * When everything is present, behavior is byte-identical to the pre-broker code: the compose
    functions ARE the old `EvalContext` method bodies, memoised into the same per-context
    caches, so a granted render costs nothing extra and produces the same frames.
  * An EMPTY resource is granted, not denied: `breakdown()` returning `{}` (no steady-state
    batches, a failed reconstruction) is a degraded product its consumers are written to
    tolerate — `travel_handling` renders nothing, the stats suites skip those rows.  Denial is
    reserved for absence of the SOURCE (a sim DB not on disk, no series.json for a group).

The per-process tally (`tally_snapshot`) is what makes "did every consumer get what it asked
for" answerable from the log alone: run_analysis collects each worker's counts and prints a
run-end summary.  Process-global is safe for the same reason `io.set_footer` is — workers are
separate spawned processes, and within one, jobs run sequentially.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

import numpy as np

from Optimization.persistence.Picking_Data import (load_batch_stats, load_task_stats,
                                                   load_picker_events)
from Optimization.metrics.Simulation_Analytics import task_time_breakdown
from Optimization.Performance_Evaluations.common.frames import _bdf, _tdf
from Optimization.Performance_Evaluations.common.series import _build_series


# ── the denial sentinel ──────────────────────────────────────────────────────────

class Denied:
    """Falsy sentinel carrying the reason the broker actually hit.  Never an exception."""
    __slots__ = ('reason',)

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:                                 # pragma: no cover - debug aid
        return f'Denied({self.reason!r})'


# ── the registry ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Request:
    """One named request: bespoke, hard-coded navigation from versioned sources to a resource."""
    name:    str
    scope:   str                        # 'config' | 'aggregate' — which context kind serves it
    compose: Callable                   # compose(ctx) -> resource | Denied


#: (scope, name) -> Request
REQUESTS: dict = {}


def request(name: str, scope: str):
    """Decorator: register `fn` as the compose function for (scope, name)."""
    def _wrap(fn):
        key = (scope, name)
        if key in REQUESTS:
            raise ValueError(f'duplicate request {name!r} for scope {scope!r}')
        REQUESTS[key] = Request(name=name, scope=scope, compose=fn)
        return fn
    return _wrap


# ── per-arm frame loaders (the old EvalContext method bodies, memoised the same way) ──

def batch_frame(ctx, key):
    """One strategy's batch frame, memoised in ctx._bcache — EvalContext.batch_df delegates here."""
    df = ctx._bcache.get(key)
    if df is None:
        s = ctx._by_key[key]
        df = _bdf(load_batch_stats(s['db_path'], s['run_id']))
        ctx._bcache[key] = df
    return df


def task_frame(ctx, key):
    """One strategy's task frame, memoised in ctx._tcache — EvalContext.task_df delegates here."""
    df = ctx._tcache.get(key)
    if df is None:
        s = ctx._by_key[key]
        df = _tdf(load_task_stats(s['db_path'], s['run_id']),
                  ctx.aisle_unittype_map, ctx.aisle_handling_map)
        ctx._tcache[key] = df
    return df


def series_dict(ctx):
    """The composed series dict, memoised in ctx._series — EvalContext.series delegates here."""
    if ctx._series is None:
        ctx._series = _build_series(ctx.strategies,
                                    {s['key']: batch_frame(ctx, s['key']) for s in ctx.strategies},
                                    {s['key']: task_frame(ctx, s['key']) for s in ctx.strategies})
    return ctx._series


def _strategy_travel_handling(strategies, ss_lo, max_b, n_sample=8):
    """Per-strategy (travel, handling) picker-time totals over a sample of steady-state
    batches, reconstructed from picker_events.  Returns {key: (travel, handling)}.
    (Moved from core/context.py — the broker owns resource navigation.)"""
    lo, hi = max(0, ss_lo), max_b
    if hi < lo:
        return {}
    batch_ids = sorted({int(round(x)) for x in np.linspace(lo, hi, min(n_sample, hi - lo + 1))})
    out = {}
    for s in strategies:
        tr = hd = 0.0
        for b in batch_ids:
            t, h, _ = task_time_breakdown(load_picker_events(s['db_path'], s['run_id'], b))
            tr += t; hd += h
        out[s['key']] = (tr, hd)
    return out


def breakdown_dict(ctx):
    """{key: (travel, handling)}, memoised in ctx._breakdown; {} (logged) on failure so
    dependent graphs degrade gracefully — EvalContext.breakdown delegates here."""
    if ctx._breakdown is None:
        try:
            ctx._breakdown = _strategy_travel_handling(ctx.strategies, ctx.ss_lo(), ctx.maxb())
        except Exception as exc:                                    # noqa: BLE001
            ctx.log.error(f'  task-time breakdown failed for {ctx.name}: {exc!r}')
            ctx._breakdown = {}
    return ctx._breakdown


# ── compose functions: config scope ──────────────────────────────────────────────

def _absent_dbs(ctx) -> list:
    """Arm keys whose sim DB is not on disk — THE denial condition for every config request.
    (An unreadable or unvetted file that IS on disk still raises; that is corruption, not
    absence, and must stop the publish.)"""
    return [s['key'] for s in ctx.strategies
            if not (s.get('db_path') and os.path.exists(s['db_path']))]


def _deny_absent(ctx):
    gone = _absent_dbs(ctx)
    if gone:
        return Denied(f"sim db absent for arm(s): {', '.join(gone)}")
    return None


@request('batch', 'config')
def _batch(ctx):
    """Every strategy's batch frame, materialized (memoised — the render re-reads for free)."""
    denied = _deny_absent(ctx)
    if denied is not None:       # Denied is deliberately FALSY - never truth-test it
        return denied
    return {s['key']: batch_frame(ctx, s['key']) for s in ctx.strategies}


@request('task', 'config')
def _task(ctx):
    denied = _deny_absent(ctx)
    if denied is not None:       # Denied is deliberately FALSY - never truth-test it
        return denied
    return {s['key']: task_frame(ctx, s['key']) for s in ctx.strategies}


@request('series', 'config')
def _series(ctx):
    denied = _deny_absent(ctx)
    if denied is not None:       # Denied is deliberately FALSY - never truth-test it
        return denied
    return series_dict(ctx)


@request('breakdown', 'config')
def _breakdown(ctx):
    denied = _deny_absent(ctx)
    if denied is not None:       # Denied is deliberately FALSY - never truth-test it
        return denied
    return breakdown_dict(ctx)          # {} is a GRANT — see the module docstring


# ── compose functions: aggregate scope ───────────────────────────────────────────

@request('series', 'aggregate')
def _agg_series(ctx):
    """The per-profile series.json list the aggregate stage was built from.

    The grant validates `ctx.profile_series_list` — THAT is the resource.  Consumers may read
    it raw (the aggregate stats suite feeds it to the pairing machinery per profile) or
    composed via `ctx.agg_series()` (cross_profile's merged view); both are the same granted
    resource, and the composed form is a memoised transform of the raw one, not a second
    request."""
    if not ctx.profile_series_list:
        return Denied('no series.json found for this group '
                      '(config.series never ran, or every profile lacked it)')
    return ctx.profile_series_list


# ── compose functions: run scope ─────────────────────────────────────────────────

@request('runtime', 'run')
def _run_runtime(ctx):
    """Per-arm wall-clock compute rows from the run root's runtime metrics DB.

    Denied when the DB is absent — a re-analysis of a run made before it existed is a
    normal thing to do, and the cost figures should be skipped with a reason rather than
    rendered empty.
    """
    rows = ctx.runtime_rows()
    if not rows:
        return Denied('no runtime metrics at the run root '
                      '(the run predates the DB, or the supervisor never wrote it)')
    return rows


@request('whatif', 'run')
def _run_whatif(ctx):
    """The cross-cell what-if comparison rows — the only place a scheduler pair is joined.

    Denied on a single-cell run, where there is no second cell to compare against and the
    what-if writers correctly emit nothing.
    """
    rows = ctx.whatif_rows('whatif_volume_csv')
    if not rows:
        return Denied('no cross-cell what-if rows (single-cell run, or the what-if '
                      'writers have not run yet)')
    return rows


@request('catalogue', 'run')
def _run_catalogue(ctx):
    """The run's own frozen catalogue — what the inventory model REALLY produced.

    Not the generator's parameters and not the config's averages: the per-SKU rows the
    simulation actually stocked, which is the only population a published distribution
    may be computed over.
    """
    pairs = ctx.catalogue_dbs()
    if not pairs:
        return Denied('no frozen catalogue under the run root')
    return pairs


# ── resolution + the access tally ────────────────────────────────────────────────

#: Per-process grant/denial counts, keyed by evaluation key.  Snapshotted (and reset) by
#: run_analysis._run_job so each worker's counts travel back to the parent for the run-end
#: summary.  See the module docstring for why process-global is safe here.
#:
#: `errors` rides the same channel, and it is not an afterthought.  `driver._run_one`
#: catches every render exception so one bad evaluation cannot sink the rest — but the
#: only trace was a `ctx.log.error` from inside a WORKER, and a worker's logger is not
#: wired to the run's log file.  The result: `aggregate.sig` raised `NameError` on every
#: publish run of this suite, produced not one of its declared figures, appeared in no log,
#: and the summary line printed "all 82 evaluation requests granted, 0 denials".  A grant
#: is a statement about the INPUTS; it says nothing about whether anything came out.
_TALLY: dict = {'granted': {}, 'denied': {}, 'errors': {}}


def record_error(eval_key: str, exc: BaseException) -> None:
    """Record that an evaluation's render raised.  Called only by `driver._run_one`.

    Stores `(count, last_repr)` so the run-end summary can name both how often and what.
    """
    n, _prev = _TALLY['errors'].get(eval_key, (0, ''))
    _TALLY['errors'][eval_key] = (n + 1, repr(exc))


def resolve_needs(ctx, ev) -> dict:
    """Resolve every request `ev.needs` declares against `ctx`.  Returns {} when all granted
    (resources are materialized into the context's caches as a side effect), else
    {need: Denied} for exactly the requests that could not be served.  Tallies either way.
    """
    scope = ev.scope if ev.scope in ('aggregate', 'run') else 'config'
    denials = {}
    for need in ev.needs:
        req = REQUESTS.get((scope, need))
        if req is None:
            denials[need] = Denied(f'no request named {need!r} for scope {scope!r}')
            continue
        got = req.compose(ctx)
        if isinstance(got, Denied):
            denials[need] = got
    bucket = 'denied' if denials else 'granted'
    _TALLY[bucket][ev.key] = _TALLY[bucket].get(ev.key, 0) + 1
    return denials


def tally_snapshot(reset: bool = False) -> dict:
    """A picklable copy of this process's access tally; optionally reset (per-job snapshots).

    The reset is load-bearing at `granularity='graph'`: a worker is reused across jobs, so
    without it every later job re-reports the earlier jobs' counts.
    """
    snap = {'granted': dict(_TALLY['granted']), 'denied': dict(_TALLY['denied']),
            'errors': dict(_TALLY['errors'])}
    if reset:
        _TALLY['granted'] = {}
        _TALLY['denied'] = {}
        _TALLY['errors'] = {}
    return snap


# ── the composed-SQL half ────────────────────────────────────────────────────────

def sql(name: str, schema_id: str, family: str = 'sim_db') -> str:
    """The composed SQL statement that would serve named query `name` on `schema_id` — for
    consumers that want the statement rather than frames (the run_whatif_* trio pattern).
    Delegates to `Schema.dataset.sql_for`; raises `UnsupportedQuery` rather than guessing."""
    from Schema import dataset as _dataset
    return _dataset.sql_for(family, name, schema_id)
