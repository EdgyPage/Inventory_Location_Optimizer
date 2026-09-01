"""Driver — scope dispatch + output-dir preparation for the registry.

The flat-pool worker in run_analysis.py builds an EvalContext (config stage) or an
AggregateContext (aggregate stage) and calls run_config / run_aggregate (config granularity)
or run_one (graph granularity).  Directory preparation is done ONCE by the parent pre-pass
(prepare_config_dirs / prepare_aggregate_dir) so no worker races to wipe a shared dir; each
evaluation only writes its own files into the shared figures/ and tables/ tops (the old
single-owner self-wipe pattern is retired — no render wipes anything).

`_run_one` is the sole render entry, which makes it the access-control choke point: every
evaluation's declared `needs=` is resolved through the request broker (core/requests.py)
BEFORE its render runs.  All granted -> render exactly as before (the resolution warmed the
context caches the render reads).  Any denied -> the render is SKIPPED, gracefully, and the
`[access]` log line carries the reason the broker actually hit — a missing resource is an
answerable event, never a crash and never a silent half-figure.

It also catches every render exception, so one broken evaluation cannot sink the rest.
That swallow is deliberate and it was also, for a long time, invisible: the only trace was
a log call from inside a worker whose logger reaches no file.  Every caught exception is
now recorded in the broker's tally and reported at the end of the run, because a grant is
a statement about an evaluation's INPUTS and says nothing about whether anything came out.
"""
import os

from Optimization.Performance_Evaluations.core.registry import EVAL_BY_KEY
from Optimization.Performance_Evaluations.core import artifact_map, requests
from Optimization.Performance_Evaluations.common import io
from Optimization.Performance_Evaluations.common.io import _fresh_dir

_CONFIG_SCOPES = ('per_strategy', 'config')


def prepare_config_dirs(run_dir):
    """Wipe + recreate the per-config shared output dirs exactly once (parent pre-pass).

    The dir list is DERIVED from the registry's `out_subdir` declarations (see
    `artifact_map.config_dirs` for the rule); a golden test pins it to the historical
    literals so the derivation can never silently relocate an output."""
    tops, nested = artifact_map.config_dirs()
    for top in tops:
        _fresh_dir(os.path.join(run_dir, *top.split('/')))
    for sub in nested:
        os.makedirs(os.path.join(run_dir, *sub.split('/')), exist_ok=True)


def prepare_aggregate_dir(out_dir):
    """Wipe + recreate one _aggregate/<pickcfg>/ root once (parent pre-pass)."""
    _fresh_dir(out_dir)


def prepare_run_dir(out_dir):
    """Wipe + recreate the run-root dossier root, then every declared run-scope subdir.

    No shared-top/single-owner rule here, unlike `prepare_config_dirs`: run-scope
    evaluations execute serially in the parent after every cell has finished, so there is
    no worker to race.  One wipe of the root, then `artifact_map.run_dirs()`.
    """
    _fresh_dir(out_dir)
    for sub in artifact_map.run_dirs():
        os.makedirs(os.path.join(out_dir, *sub.split('/')), exist_ok=True)


def resolve_params(ev, overrides, cli_set):
    p = dict(ev.defaults)
    p.update(overrides.get(ev.key, {}))
    p.update(cli_set.get(ev.key, {}))
    return p


def _run_one(ctx, ev, overrides, cli_set):
    io.set_footer(getattr(ctx, 'footer', lambda: None)())   # stamp provenance on every figure
    io.set_current_eval(ev.key)                             # scope figure saves to their owner
    try:
        denials = requests.resolve_needs(ctx, ev)
        if denials:
            reasons = '; '.join(f'{need}: {d.reason}' for need, d in denials.items())
            # An ERA shortfall is not an access failure and must not read as one: the file
            # is present and simply older than the measurement, which no re-run of this
            # stage can fix.  Its own tag, so the two are separable in the log and in the
            # run-end summary.
            if any(isinstance(d, requests.EraUnmet) for d in denials.values()):
                ctx.log.info(f'[era] {ev.key} -> UNAVAILABLE ({reasons}); render skipped')
            else:
                ctx.log.info(f"[access] {ev.key} requested {','.join(ev.needs)} -> "
                             f"DENIED ({reasons}); render skipped")
            return
        if ev.needs:
            ctx.log.info(f"[access] {ev.key} requested {','.join(ev.needs)} -> granted")
        ev.render(ctx, resolve_params(ev, overrides, cli_set))
    except Exception as exc:                                       # noqa: BLE001 — one dies, rest live
        # The log line alone is not enough, and this is the fix for a real, long-lived
        # failure: a worker's logger is not wired to the run's log file, so `agg.sig`
        # raised NameError on every publish run, wrote none of its declared figures, and
        # left no trace anywhere while the run summary reported zero denials.  The tally
        # travels back to the parent with the access counts and is printed at WARNING.
        ctx.log.error(f'  {ev.key} failed: {exc!r}')
        requests.record_error(ev.key, exc)
    finally:
        io.set_current_eval(None)


def run_config(ctx, keys, overrides, cli_set):
    """Run all per_strategy + config evaluations named in `keys` (config granularity)."""
    for k in keys:
        ev = EVAL_BY_KEY.get(k)
        if ev is None or ev.scope not in _CONFIG_SCOPES:
            continue
        _run_one(ctx, ev, overrides, cli_set)


def run_aggregate(ctx, keys, overrides, cli_set):
    """Run all aggregate evaluations named in `keys` (config granularity)."""
    for k in keys:
        ev = EVAL_BY_KEY.get(k)
        if ev is None or ev.scope != 'aggregate':
            continue
        _run_one(ctx, ev, overrides, cli_set)


def run_at_root(ctx, keys, overrides, cli_set):
    """Run all run-scope evaluations named in `keys`, over the whole run root.

    Called by analyze_run AFTER every cell and after the cross-cell what-if writers: these
    evaluations read what those stages produced, so ordering is a data dependency, not a
    preference.  Serial in the parent — there are a handful of them and each one reads the
    entire run, so a pool would buy nothing and cost the wipe-race rule.
    """
    for k in keys:
        ev = EVAL_BY_KEY.get(k)
        if ev is None or ev.scope != 'run':
            continue
        _run_one(ctx, ev, overrides, cli_set)


def run_one(ctx, key, overrides, cli_set):
    """Run a single evaluation by key (graph granularity)."""
    ev = EVAL_BY_KEY.get(key)
    if ev is not None:
        _run_one(ctx, ev, overrides, cli_set)


def config_keys(preset):
    return [k for k in preset['keys']
            if (EVAL_BY_KEY.get(k) and EVAL_BY_KEY[k].scope in _CONFIG_SCOPES)]


def aggregate_keys(preset):
    return [k for k in preset['keys']
            if (EVAL_BY_KEY.get(k) and EVAL_BY_KEY[k].scope == 'aggregate')]


def run_root_keys(preset):
    return [k for k in preset['keys']
            if (EVAL_BY_KEY.get(k) and EVAL_BY_KEY[k].scope == 'run')]
