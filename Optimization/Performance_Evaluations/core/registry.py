"""The Evaluation descriptor + @evaluation decorator + module-level registry.

Mirrors the registry style of Optimization/strategies.py (a dataclass per item, a flat
list, and a by-key dict), but populated by a decorator so a graph module self-registers
on import.  A graph's `render(ctx, params)` does the actual plotting; the descriptor
carries only the metadata the driver needs to schedule it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Evaluation:
    key:        str                          # 'compare.top_metric'
    label:      str
    scope:      str                          # 'per_strategy' | 'config' | 'aggregate'
    needs:      tuple = ()                    # subset of {'batch','task','events','series','breakdown'}
    defaults:   dict = field(default_factory=dict)
    out_subdir: str = ''                     # relative dir under the run/agg root
    by_initial: bool = False                 # stats-only structural fork (uni-vs-opt per fn)
    render:     Callable = None              # render(ctx, params) -> None


EVALUATIONS: list[Evaluation] = []
EVAL_BY_KEY: dict[str, Evaluation] = {}


def evaluation(*, key, label, scope, needs=(), defaults=None,
               out_subdir='', by_initial=False):
    """Decorator: register the wrapped render fn as an Evaluation.

    Returns the plain function unchanged so it stays directly unit-testable.
    """
    def _wrap(fn):
        ev = Evaluation(key=key, label=label, scope=scope, needs=tuple(needs),
                        defaults=dict(defaults or {}), out_subdir=out_subdir,
                        by_initial=by_initial, render=fn)
        if key in EVAL_BY_KEY:
            raise ValueError(f'duplicate evaluation key {key!r}')
        EVALUATIONS.append(ev)
        EVAL_BY_KEY[key] = ev
        return fn
    return _wrap
