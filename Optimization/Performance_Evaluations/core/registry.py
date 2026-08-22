"""The Evaluation descriptor + @evaluation decorator + module-level registry.

Mirrors the registry style of Optimization/config/strategies.py (a dataclass per item, a flat
list, and a by-key dict), but populated by a decorator so a graph module self-registers
on import.  A graph's `render(ctx, params)` does the actual plotting; the descriptor
carries only the metadata the driver needs to schedule it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Evaluation:
    key:        str                          # 'headline.top_vs_baseline'
    label:      str
    scope:      str                          # 'per_strategy' | 'config' | 'aggregate'
    needs:      tuple = ()                    # subset of {'batch','task','series','breakdown'}
    defaults:   dict = field(default_factory=dict)
    out_subdir: str | tuple = ''             # relative dir(s) under the run/agg root; a tuple
                                             # declares a multi-dir owner, '' declares the
                                             # root itself.  Figure evals DERIVE this from
                                             # `family` (figures/<family>) — see the decorator.
    family:     str | None = None            # chart family (core/families.py); None for
                                             # non-figure evals (series/tables writers)
    views:      tuple = ()                   # the family views this eval emits — validated
                                             # per save by chartkit against the grammar
    by_initial: bool = False                 # stats-only structural fork (uni-vs-opt per fn)
    render:     Callable = None              # render(ctx, params) -> None


EVALUATIONS: list[Evaluation] = []
EVAL_BY_KEY: dict[str, Evaluation] = {}


def evaluation(*, key, label, scope, needs=(), defaults=None,
               out_subdir=None, family=None, views=(), by_initial=False):
    """Decorator: register the wrapped render fn as an Evaluation.

    A figure eval declares `family=` and its out_subdir is DERIVED (figures/<family>) —
    passing both is an error, so a family's folder can never be retyped inconsistently.
    Non-figure evals declare out_subdir explicitly ('tables', or '' for the leaf root).
    Returns the plain function unchanged so it stays directly unit-testable.
    """
    def _wrap(fn):
        sub = out_subdir
        if family is not None:
            from Optimization.Performance_Evaluations.core.families import (
                FAMILIES, figures_subdir)
            if sub is not None:
                raise ValueError(f'{key}: declare family= OR out_subdir=, not both')
            bad = tuple(v for v in views if v not in FAMILIES[family]['views'])
            if bad:
                raise ValueError(f'{key}: views {bad} not allowed in family {family!r}')
            sub = figures_subdir(family)
        ev = Evaluation(key=key, label=label, scope=scope, needs=tuple(needs),
                        defaults=dict(defaults or {}), out_subdir=sub or '',
                        family=family, views=tuple(views),
                        by_initial=by_initial, render=fn)
        if key in EVAL_BY_KEY:
            raise ValueError(f'duplicate evaluation key {key!r}')
        EVALUATIONS.append(ev)
        EVAL_BY_KEY[key] = ev
        return fn
    return _wrap
