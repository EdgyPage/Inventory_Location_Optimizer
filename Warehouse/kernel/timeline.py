"""timeline — what the simulation's clock is made of, and how batches sit on one axis.

There is no clock object in this simulator, and this module does not add one.  Simulated
time is a per-picker `float` that is reborn at `0.0` at the start of every batch
(`fast_pick._simulate_picker_deferred`, `Pick.PickSimulation._simulate_picker`), advanced
by accumulation at three sites — travel, cart swap, handling — and merged across pickers
by sorting events on `.time`.  A second work stream putting away inbound loads will need
to sit on the same axis, and two facts have to be settled before it can.

## 1. The unit

`cost_model` produces SECONDS and says so: `sec_per_inch` is `1/(12·ft_per_s)` and is
documented as s/inch.  The constants added to the same accumulator read the same way —
`pick_intercept: 15`, `cart_swap_coef: 300` are 15 seconds a pick and five minutes a cart
swap, which are warehouse numbers; at 15 ms and 0.3 s they are not physical.

The ANALYSIS layer declares the opposite.  `Performance_Evaluations/common/units.py` says
"durations are milliseconds" and divides by `3.6e6` for hours.  Both cannot be right, and
`3.6e6` seconds is 1000 hours.

`ANALYSIS_DIVISOR_DISCREPANCY` below records that, with the arithmetic, because a shared
clock has to know what it is counting in.  It is NOT fixed here: correcting the label moves
every absolute number on a published site and is its own piece of work with its own review.
Every RATIO — every percentage, effect size and improvement in the suite — is unaffected,
which is exactly why nothing caught it.

## 2. The epoch

A batch's picker clocks start at zero, so `t` alone cannot order two events from different
batches.  The absolute axis is the running sum of the batch durations before it, and that
sum is currently computed in the ANALYSIS layer with a bare `np.cumsum` — a cross-batch
timeline invented after the fact, in a what-if module, with no name.  `batch_epoch` is that
same arithmetic, named, in a module both the domain and the harness can import.
"""
from __future__ import annotations

from typing import Iterable, Sequence

#: The unit every duration in the simulator is expressed in.  Named so a second stream
#: joining the clock has something to agree with rather than something to infer.
TIME_UNIT = 'seconds'

#: Seconds per hour, for a consumer converting the sim's own unit honestly.
SECONDS_PER_HOUR = 3600.0

#: The divisor `Performance_Evaluations/common/units.py` applies to a sim duration to get
#: "hours".  3.6e6 is milliseconds per hour; the sim emits seconds.
ANALYSIS_DIVISOR = 3.6e6

#: How far apart the two readings are.  1000x — so a published "1.68 hours of labor per
#: batch" is 1,680 hours, and a published "450,000 items/hour" is 450.
#:
#: DELIBERATELY NOT FIXED HERE.  Recorded so the discrepancy is a counted decision rather
#: than a silent one, and so a clock shared with a second work stream starts from a stated
#: unit.  Fixing it relabels every absolute number on a live site; every ratio is
#: unaffected.  `Tests/unit/test_timeline.py` fails if the two constants ever agree, which
#: is the day this entry should be deleted along with the note above.
ANALYSIS_DIVISOR_DISCREPANCY = ANALYSIS_DIVISOR / SECONDS_PER_HOUR


def batch_epoch(durations: Sequence[float], index: int) -> float:
    """Absolute start time of batch `index`: the sum of every batch before it.

    `durations[i]` is batch i's makespan, in the sim's own unit.  Adding this to a
    picker-local `t` puts an event from any batch on one axis — which is what a trailer
    arriving on a schedule, or a slot freeing four batches from now, has to be measured
    against.

    Raises on a negative index rather than silently wrapping, because `durations[-1]` is a
    real batch and would produce a plausible wrong answer.
    """
    if index < 0:
        raise IndexError(f'batch index {index} is negative; there is no batch before the '
                         f'first one')
    return float(sum(durations[:index]))


def epochs(durations: Iterable[float]) -> list[float]:
    """The absolute start time of every batch — `[0, d0, d0+d1, …]`, one per batch.

    The whole-series form, for a caller converting a run to one timeline in a pass.  This
    is the arithmetic `run_whatif_volume` performs with `np.cumsum`, offset by one: cumsum
    gives each batch's END, this gives each batch's START.
    """
    out, running = [], 0.0
    for d in durations:
        out.append(running)
        running += float(d or 0.0)
    return out
