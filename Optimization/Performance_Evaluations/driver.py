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
            ctx.log.info(f"[access] {ev.key} requested {','.join(ev.needs)} -> "
                         f"DENIED ({reasons}); render skipped")
            return
        if ev.needs:
            ctx.log.info(f"[access] {ev.key} requested {','.join(ev.needs)} -> granted")
        ev.render(ctx, resolve_params(ev, overrides, cli_set))
    except Exception as exc:                                       # noqa: BLE001 — one dies, rest live
        ctx.log.error(f'  {ev.key} failed: {exc!r}')
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
