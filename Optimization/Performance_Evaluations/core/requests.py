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
                                                   load_picker_events, load_carryover,
                                                   load_yard_drains, load_yard_trailers)
from Optimization.metrics.Simulation_Analytics import task_time_breakdown
from Optimization.Performance_Evaluations.common.frames import _bdf, _cdf, _ddf, _tdf, _ydf
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


class EraUnmet(Denied):
    """A refusal about the DATA ERA, not about a missing resource.

    Kept distinct from a plain `Denied` because the two call for different actions and a
    single bucket taught the reader the wrong one. A denial says a file was not there —
    re-run the stage, fix the path. This says the file IS there and cannot answer the
    question, because the run predates the column or never wrote the rows. There is no
    fixing that from the analysis side: the only honest routes are a new sweep on a vintage
    that records it, or the degraded form with the caveat said out loud.

    It reports under its own `[era]` summary line for the same reason. `[access]` counts
    INPUTS, and an era shortfall arriving in that column would read as a pipeline fault on
    a run that is simply older than the measurement.
    """
    __slots__ = ('missing',)

    def __init__(self, reason: str, missing=()) -> None:
        super().__init__(reason)
        self.missing = tuple(missing)

    def __repr__(self) -> str:                                 # pragma: no cover - debug aid
        return f'EraUnmet({self.reason!r}, missing={self.missing!r})'


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


def _arm_end_s(ctx, key) -> float:
    """When this arm stopped, on its own absolute axis — the CENSORING bound.

    `max(batch_start_time + duration)` rather than the last row's, because DB order is not
    guaranteed sorted (the same reason `_elapsed` argsorts).  0.0 on an empty frame, which
    makes every censored detention 0 — honest for a run with no batches at all, and the
    only case where it can happen.
    """
    df = batch_frame(ctx, key)
    if df.empty:
        return 0.0
    return float((df['batch_start_time'] + df['duration']).max())


def yard_frame(ctx, key):
    """One strategy's per-trailer frame, memoised — spans and the fee proxy derived here."""
    df = ctx._ycache.get(key)
    if df is None:
        s = ctx._by_key[key]
        df = _ydf(load_yard_trailers(s['db_path'], s['run_id']),
                  _arm_end_s(ctx, key), ctx.fee_threshold_days())
        ctx._ycache[key] = df
    return df


def drain_frame(ctx, key):
    """One strategy's per-drain yard frame, memoised."""
    df = ctx._dcache.get(key)
    if df is None:
        s = ctx._by_key[key]
        df = _ddf(load_yard_drains(s['db_path'], s['run_id']))
        ctx._dcache[key] = df
    return df


def missed_frame(ctx, key):
    """One strategy's per-batch demand-service frame, memoised."""
    df = ctx._mcache.get(key)
    if df is None:
        s = ctx._by_key[key]
        df = _cdf(load_carryover(s['db_path'], s['run_id']), batch_frame(ctx, key))
        ctx._mcache[key] = df
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


@request('yard', 'config')
def _yard(ctx):
    """Both yard frames for every arm — `{key: (trailers_df, drains_df)}`.

    DENIED when no arm has a single yard row, and that denial is the honest one: an
    inbound-off run is not a run whose yard was empty, it is a run with no yard, and a
    family of figures asserting zero trailer-days would be a claim about a model that did
    not exist. The driver logs it and skips the render, which is exactly what should
    happen to every run in the archive.
    """
    denied = _deny_absent(ctx)
    if denied is not None:       # Denied is deliberately FALSY - never truth-test it
        return denied
    got = {s['key']: (yard_frame(ctx, s['key']), drain_frame(ctx, s['key']))
           for s in ctx.strategies}
    if not any(not t.empty or not d.empty for t, d in got.values()):
        # EraUnmet, not Denied: nothing is missing. The files were opened and read and
        # simply have no yard in them, which no re-run of this stage can change. The
        # `yard.scorecard` evaluation reaches this path rather than the capability probe
        # because it declares no quantity to be gated ON — its read-outs have no honest
        # DIRECTION, so none of them may be a Quantity — and it must still land in the
        # same bucket as its three siblings or the log tells a reader to fix a pipeline
        # that is working correctly.
        return EraUnmet('no yard rows on any arm (the run predates the yard tables, or '
                        'ran with INBOUND_STANDING_YARD off)', missing=('yard',))
    return got


@request('missed', 'config')
def _missed(ctx):
    """Per-arm demand-service frames.  Granted with ZEROS in them, unlike `yard`.

    An arm with no missed rows genuinely served all its demand, and that is a result rather
    than an absence — so a frame of zeros is a grant, and `_cdf` builds one row per BATCH
    rather than one per carryover row precisely so a perfectly-served batch is present at
    zero instead of missing.

    That is also why this compose cannot be the era check. A pre-`carryover` vintage
    produces the identical frame of zeros — `load_carryover` returns `[]` for "no table" and
    for "nothing missed" alike — so row-emptiness cannot tell a perfect run from an old one,
    and reading zeros off the old one would publish a plausible wrong number. The
    CAPABILITY does tell them apart (it probes the table for rows), both quantities name it,
    and `era_shortfall` runs before this. The check below therefore only ever fires when no
    arm recorded a batch at all.
    """
    denied = _deny_absent(ctx)
    if denied is not None:       # Denied is deliberately FALSY - never truth-test it
        return denied
    got = {s['key']: missed_frame(ctx, s['key']) for s in ctx.strategies}
    if all(df.empty for df in got.values()):
        return Denied('no arm recorded a batch, so there is no demand to have served')
    return got


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
_TALLY: dict = {'granted': {}, 'denied': {}, 'errors': {}, 'era': {}}


def record_error(eval_key: str, exc: BaseException) -> None:
    """Record that an evaluation's render raised.  Called only by `driver._run_one`.

    Stores `(count, last_repr)` so the run-end summary can name both how often and what.
    """
    n, _prev = _TALLY['errors'].get(eval_key, (0, ''))
    _TALLY['errors'][eval_key] = (n + 1, repr(exc))


def era_shortfall(ctx, ev):
    """`EraUnmet` when this run cannot answer a quantity this evaluation draws, else None.

    THE RUNTIME HALF OF THE ERA GATE. `core/era.py` proves statically that every quantity's
    read is either version-free or names a capability; this asks the far narrower question
    that only a file can answer — does THIS run carry it. The gap between the two is a
    vetted vintage that has the table and no rows, which is common enough to have been
    measured (`reorder_queue`: 68 of 166 arms) and which nothing else would notice: the
    figure would simply not appear, with an INFO line either way.

    Only config-scope evaluations are checked, because only they read a sim DB — the
    aggregate scope consumes series documents this same analysis wrote, and the run scope
    reads the run root. A scope with no capability probe available answers None rather than
    guessing, which is the same rule `_deny_absent` follows for a file it cannot see.
    """
    if not ev.quantities or not hasattr(ctx, 'capabilities'):
        return None
    from Optimization.Performance_Evaluations.core import quantities as _quantities
    # WHAT IS GATED FIRST, then the probe.  An evaluation drawing only version-free
    # quantities must not open a connection per arm to be told what its own declaration
    # already says, and the ordering is what makes that structural rather than incidental.
    gated = {}
    for key in ev.quantities:
        q = _quantities.BY_KEY.get(key)
        if q is not None and q.capability:
            gated.setdefault(q.capability, []).append(key)
    if not gated:
        return None
    have = ctx.capabilities()
    want = {cap: keys for cap, keys in gated.items() if cap not in have}
    if not want:
        return None
    parts = ', '.join(f'{cap} (for {", ".join(sorted(ks))})'
                      for cap, ks in sorted(want.items()))
    return EraUnmet(
        f'this run cannot answer {parts} — the vintage predates those tables, or the arms '
        f'wrote no rows into them. Not fixable from here: re-run on a vintage that records '
        f'it, or report the degraded form with the caveat',
        missing=sorted(want))


def resolve_needs(ctx, ev) -> dict:
    """Resolve every request `ev.needs` declares against `ctx`.  Returns {} when all granted
    (resources are materialized into the context's caches as a side effect), else
    {need: Denied} for exactly the requests that could not be served.  Tallies either way.

    The ERA check runs first and short-circuits: composing a resource this run cannot answer
    would pay for every arm's frames to discover what the capability probe already knows.
    """
    era = era_shortfall(ctx, ev)
    if era is not None:
        _TALLY['era'][ev.key] = _TALLY['era'].get(ev.key, 0) + 1
        return {'era': era}
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
    # A compose can raise the era flag too — a table present with no rows in it is an era
    # fact a static capability list cannot see, and it must be counted as one wherever it
    # is discovered rather than by which code path found it.
    if any(isinstance(d, EraUnmet) for d in denials.values()):
        bucket = 'era'
    else:
        bucket = 'denied' if denials else 'granted'
    _TALLY[bucket][ev.key] = _TALLY[bucket].get(ev.key, 0) + 1
    return denials


def tally_snapshot(reset: bool = False) -> dict:
    """A picklable copy of this process's access tally; optionally reset (per-job snapshots).

    The reset is load-bearing at `granularity='graph'`: a worker is reused across jobs, so
    without it every later job re-reports the earlier jobs' counts.
    """
    snap = {'granted': dict(_TALLY['granted']), 'denied': dict(_TALLY['denied']),
            'errors': dict(_TALLY['errors']), 'era': dict(_TALLY['era'])}
    if reset:
        _TALLY['granted'] = {}
        _TALLY['denied'] = {}
        _TALLY['errors'] = {}
        _TALLY['era'] = {}
    return snap


# ── the composed-SQL half ────────────────────────────────────────────────────────

def sql(name: str, schema_id: str, family: str = 'sim_db') -> str:
    """The composed SQL statement that would serve named query `name` on `schema_id` — for
    consumers that want the statement rather than frames (the run_whatif_* trio pattern).
    Delegates to `Schema.dataset.sql_for`; raises `UnsupportedQuery` rather than guessing."""
    from Schema import dataset as _dataset
    return _dataset.sql_for(family, name, schema_id)
