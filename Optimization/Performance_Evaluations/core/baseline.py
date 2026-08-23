"""baseline — WHICH arm every comparison is quoted against, declared instead of counted.

`EvalContext.base` was `self.strategies[0]`.  That is correct only because the strategy
list happens to be built baseline-first, which is a property of how the run harness
assembles `sim_result['strategies']` — a fact no chart module can see and none of them
states.  Two things in this package already knew better and said so separately:
`cost/rollup.BASE_RULE = 'fifo'`, and about ninety prose strings naming FIFO in titles and
axis labels.

So the selection is declared here, once, and resolved by matching rather than by index.
The positional answer stays as an explicit FALLBACK with a log line, because two cases
make it the only available answer:

  * `_focus_filter` runs BEFORE the baseline is taken.  Under `focus='opt'` the arm list
    is restricted to `opt_*` and the FIFO arm may not be in it at all.
  * an ad-hoc or synthetic strategy list (tests, notebooks) carries no `assignment` key.

Both are legitimate, and both must be visible when they happen — a comparison quietly
re-baselined against a different arm changes every number on the page without changing
a filename.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Baseline:
    """The do-nothing arm: how to find it, and what to call it on a chart.

    `field`/`value` select it out of the strategy list.  `label` is the legend and
    reference-line text `chartkit.mark_baseline` and `baseline_handle` default to.
    """
    field: str
    value: str
    label: str

    def select(self, strategies) -> dict | None:
        """The declared arm, or None when this strategy list does not contain it."""
        for s in strategies:
            if str(s.get(self.field, '')).lower() == self.value:
                return s
        return None


#: The one declaration.  `fifo` is the do-nothing placement rule — first empty bin, no
#: scoring — which is what makes it the honest floor for both the labor comparison and
#: the compute-cost comparison.
BASELINE = Baseline(field='assignment', value='fifo', label='FIFO baseline')


def resolve(strategies, log=None, *, where: str = '') -> dict | None:
    """The baseline arm for one strategy list, falling back positionally out loud.

    Returns None only for an empty list.  Every other path returns an arm and, when that
    arm is not the declared one, says so at WARNING with the reason a reader would need
    to judge whether it matters.
    """
    if not strategies:
        return None
    declared = BASELINE.select(strategies)
    if declared is not None:
        return declared
    first = strategies[0]
    if log is not None:
        tag = f'{where}: ' if where else ''
        log.warning(
            f'  [baseline] {tag}no arm has {BASELINE.field}={BASELINE.value!r}; falling '
            f'back to the first arm, {first.get("key", "?")!r}. Every comparison on this '
            f'render is quoted against THAT arm — expected under focus=uni/opt, which '
            f'filters the arm list before the baseline is taken.')
    return first
