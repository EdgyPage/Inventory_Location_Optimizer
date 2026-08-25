"""timeline — what the simulation's clock is made of, and how batches sit on one axis.

Simulated time is a per-picker `float`, advanced by accumulation at three sites — travel,
cart swap, handling — and merged across pickers by sorting events on `.time`.  It STARTS AT
THE BATCH EPOCH, not at zero: the runner hands every picker `start_times=[arm_clock] * k`
and both loops begin at that `t0`.  (This module said "reborn at 0.0 at the start of every
batch" for a while after the carry landed; it was describing the older model.)

There is no clock OBJECT driving the simulation, and this module still does not add one.
What it adds below is a `WorkDay` and a `ReleaseSchedule`: value objects that answer "when
does batch i start" and "when does its day end", so a caller can schedule against a working
day instead of against the previous batch's makespan.  They compute; they do not run
anything.

## 1. The unit

`cost_model` produces SECONDS and says so: `sec_per_inch` is `1/(12·ft_per_s)` and is
documented as s/inch.  The constants added to the same accumulator read the same way —
`pick_intercept: 15`, `cart_swap_coef: 300` are 15 seconds a pick and five minutes a cart
swap, which are warehouse numbers; at 15 ms and 0.3 s they are not physical.

The ANALYSIS layer used to declare the opposite: `Performance_Evaluations/common/units.py`
said "durations are milliseconds" and divided by `3.6e6` for hours.  3.6e6 SECONDS is 1000
hours, so every ABSOLUTE figure the suite published was 1000x out — and every RATIO was
right, which is exactly why nothing caught it for the life of the project.

That is fixed.  `units.py` now IMPORTS `SECONDS_PER_HOUR` from here instead of restating a
literal, so the sim's unit and the analysis layer's divisor cannot disagree again.

## 2. The epoch

A picker's `t` is absolute only because the runner gives it the epoch to start from.  The
epoch is the running sum of the batch durations before it; the RUNNER computes it directly
now (`arm_clock = bs.batch_start_time + bs.duration`), and the analysis layer's bare
`np.cumsum` is no longer the only place it exists.  `batch_epoch` is that same arithmetic,
named, in a module both the domain and the harness can import.

One case the sum cannot cover on its own: a batch that produces no tasks has a zero
duration, so a schedule -- not a makespan -- is what makes the next one start later.  That
is `ReleaseSchedule`.
"""
from __future__ import annotations

from typing import Iterable, Sequence

#: The unit every duration in the simulator is expressed in.  Named so a second stream
#: joining the clock has something to agree with rather than something to infer.
TIME_UNIT = 'seconds'

#: Seconds per hour, for a consumer converting the sim's own unit honestly.
SECONDS_PER_HOUR = 3600.0


#: Default shift length: an eight-hour day, in the sim's own unit.
DEFAULT_SHIFT_SECONDS = 8 * SECONDS_PER_HOUR


def shift_index(t_abs: float, shift_seconds: float = DEFAULT_SHIFT_SECONDS) -> int:
    """Which fixed-length shift the absolute instant `t_abs` falls in.  0-based.

    A REPORTING FRAME OVER A CONTINUOUS CLOCK, and that is the whole contract: fixed-length
    shifts label the timeline, they do not schedule against it.  Work never pauses at the
    whistle and no task is split at a boundary — a task that spans one is recorded once,
    under the shift it STARTED in, because that is where the actor picked it up.

    This is what "work carries over" means in a model whose scheduling atom is a whole
    task: the second shift picks up mid-aisle exactly where the first left off.

    A DISPATCH-AFFECTING boundary is a different model, and it is `WorkDay` below, not this.
    Do not reach for `_stock(budget=)` as its seam even though an earlier version of this
    docstring pointed there: it defers a whole WAVE and never truncates one, which is the
    wrong granularity for a cut that has to stop a picker mid-task — and it has no
    production caller, so it is an untested path as well.

    Raises on a non-positive shift, which would otherwise divide by zero or label every
    instant shift 0 with no warning.
    """
    if shift_seconds <= 0:
        raise ValueError(f'shift_seconds must be positive, got {shift_seconds}')
    return int(t_abs // shift_seconds)


def shift_offset(t_abs: float, shift_seconds: float = DEFAULT_SHIFT_SECONDS) -> float:
    """How far into its own shift the absolute instant `t_abs` is, in seconds."""
    if shift_seconds <= 0:
        raise ValueError(f'shift_seconds must be positive, got {shift_seconds}')
    return float(t_abs % shift_seconds)


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


# ── the working day ───────────────────────────────────────────────────────────────
#
# `shift_index` above LABELS a continuous clock and never schedules against it, and that
# contract is restated in five other places including a persisted DDL comment.  So the
# dispatch-affecting concept gets its own name.  A WorkDay is a length and an origin; it
# decides when a day ENDS, which is the instant a picker has to be stopped at.


class WorkDay:
    """A fixed-length working day on the absolute axis.

    `length` seconds long, starting at `origin`.  Days are back to back: there is no gap
    between the end of one and the start of the next, because a gap would need a calendar
    and this model has none.  Overnight is expressed by making the day shorter, not by
    inserting emptiness between days.

    Pure arithmetic.  Nothing here reads or advances a clock -- a caller passes an instant
    in and gets an instant back, which is what makes it testable without a simulation.
    """

    __slots__ = ('length', 'origin')

    def __init__(self, length: float = DEFAULT_SHIFT_SECONDS, origin: float = 0.0):
        if length <= 0:
            raise ValueError(f'a working day of {length}s never ends; length must be > 0')
        self.length = float(length)
        self.origin = float(origin)

    def __repr__(self):
        return f'WorkDay(length={self.length!r}, origin={self.origin!r})'

    def __eq__(self, other):
        return (isinstance(other, WorkDay) and other.length == self.length
                and other.origin == self.origin)

    def __hash__(self):
        return hash((self.length, self.origin))

    def index_of(self, t_abs: float) -> int:
        """Which day the absolute instant falls in, 0-based from `origin`.

        An instant BEFORE the origin belongs to a negative day rather than to day 0.  Day 0
        is a real interval, and folding everything earlier into it would make the first day
        look longer than the rest.
        """
        import math
        return int(math.floor((float(t_abs) - self.origin) / self.length))

    def start_of(self, index: int) -> float:
        return self.origin + index * self.length

    def end_of(self, index: int) -> float:
        """The instant the day CLOSES -- which is the next day's start.

        Half-open by construction: an instant exactly at `end_of(i)` belongs to day i+1, and
        `index_of(end_of(i)) == i + 1`.  Anything else double-counts the boundary instant.
        """
        return self.origin + (index + 1) * self.length

    def remaining(self, t_abs: float) -> float:
        """Seconds left in the day containing `t_abs`.  Never negative, never zero.

        Exactly at a boundary the answer is a WHOLE day, not zero: the instant belongs to
        the day that is opening. Returning zero there would stop a picker that has just
        started rather than one that has run out of time.
        """
        return self.end_of(self.index_of(t_abs)) - float(t_abs)

    def fits(self, t_abs: float, duration: float) -> bool:
        """Does work of `duration` starting at `t_abs` finish before its day closes?"""
        return float(duration) <= self.remaining(t_abs)


# ── when batches are released ─────────────────────────────────────────────────────


class ReleaseSchedule:
    """When batch `i` is allowed to start.

    Two modes, and the default is the one that reproduces today:

      CONTINUOUS (`per_day=None`)  batch i starts when batch i-1 finished.  The schedule has
                                   no opinion, so `release_at` returns the ready instant it
                                   was handed.  This is the current runner, exactly.
      PACED (`per_day=N`)          the day is cut into N equal slots and batch i is released
                                   at its slot, or when the previous batch finished if that
                                   is later.  A batch that produced no tasks still consumes
                                   its slot, which is the whole point: an empty batch must
                                   advance the axis, and a makespan of zero cannot.

    `release_at` takes `ready_at` rather than reading a clock, so the schedule stays a pure
    function and the runner keeps owning the arm's state.
    """

    __slots__ = ('day', 'per_day')

    def __init__(self, day: 'WorkDay | None' = None, per_day: int | None = None):
        if per_day is not None and per_day < 1:
            raise ValueError(f'per_day must be >= 1 or None, got {per_day}')
        self.day = day if day is not None else WorkDay()
        self.per_day = per_day

    def __repr__(self):
        return f'ReleaseSchedule(day={self.day!r}, per_day={self.per_day!r})'

    @property
    def is_continuous(self) -> bool:
        return self.per_day is None

    @property
    def cadence(self) -> float:
        """Seconds between releases.  The whole day when the schedule is continuous, which
        is not a release interval so much as the absence of one."""
        return self.day.length if self.per_day is None else self.day.length / self.per_day

    def release_at(self, index: int, ready_at: float) -> float:
        """When batch `index` starts, given the arm is free at `ready_at`.

        NEVER EARLIER THAN `ready_at`.  A schedule says when work may begin, not when a
        picker teleports: if the previous batch overran its slot, this batch starts late and
        the schedule has been missed.  `missed_by` reports that rather than hiding it.
        """
        if index < 0:
            raise IndexError(f'batch index {index} is negative')
        if self.per_day is None:
            return float(ready_at)
        return max(float(ready_at), self.day.origin + index * self.cadence)

    def scheduled_at(self, index: int) -> float:
        """Where the schedule WANTED batch `index`, ignoring whether the arm was free.

        `release_at` clamps to the ready instant; the difference between the two is the only
        record that a slot was missed, so it needs its own accessor.
        """
        if index < 0:
            raise IndexError(f'batch index {index} is negative')
        return self.day.origin + index * self.cadence

    def missed_by(self, index: int, ready_at: float) -> float:
        """How late batch `index` is against its slot.  0.0 when on time or continuous."""
        if self.per_day is None:
            return 0.0
        return max(0.0, float(ready_at) - self.scheduled_at(index))

    def day_of(self, index: int) -> int:
        """Which working day batch `index` is scheduled into.  Always 0 when continuous,
        because a continuous schedule has no day structure to place it in."""
        if self.per_day is None:
            return 0
        return index // self.per_day
