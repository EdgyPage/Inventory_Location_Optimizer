"""Driver — scope dispatch + output-dir preparation for the registry.

The flat-pool worker in run_analysis.py builds an EvalContext (config stage) or an
AggregateContext (aggregate stage) and calls run_config / run_aggregate (config granularity)
or run_one (graph granularity).  Directory preparation is done ONCE by the parent pre-pass
(prepare_config_dirs / prepare_aggregate_dir) so no worker races to wipe a shared dir; each
evaluation only writes its own files (stats/aggregate graphs wipe their own private leaf).
"""
import os

from Performance_Evaluations.core.registry import EVAL_BY_KEY
from Performance_Evaluations.common.io import _fresh_dir

_CONFIG_SCOPES = ('per_strategy', 'config')


def prepare_config_dirs(run_dir):
    """Wipe + recreate the per-config shared output dirs exactly once (parent pre-pass)."""
    _fresh_dir(os.path.join(run_dir, 'per_strategy'))
    _fresh_dir(os.path.join(run_dir, 'compare'))
    for sub in ('faceted', 'overlay', 'top', 'breakdown'):
        os.makedirs(os.path.join(run_dir, 'compare', sub), exist_ok=True)


def prepare_aggregate_dir(out_dir):
    """Wipe + recreate one _aggregate/<pickcfg>/ root once (parent pre-pass)."""
    _fresh_dir(out_dir)


def resolve_params(ev, overrides, cli_set):
    p = dict(ev.defaults)
    p.update(overrides.get(ev.key, {}))
    p.update(cli_set.get(ev.key, {}))
    return p


def _run_one(ctx, ev, overrides, cli_set):
    try:
        ev.render(ctx, resolve_params(ev, overrides, cli_set))
    except Exception as exc:                                       # noqa: BLE001 — one dies, rest live
        ctx.log.error(f'  {ev.key} failed: {exc!r}')


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
