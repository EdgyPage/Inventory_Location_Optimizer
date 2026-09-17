"""batch_state — the five names a batch's two halves share, and nothing that outlives one.

`_build_leaf` binds 113 distinct names at one indent level. Five of them are here because of
a language rule and nothing else: `_replenish` and `_step` are the two halves of one batch,
they must share these values, and `nonlocal` can only rebind a name that already exists in
the enclosing scope. So five values whose LIFETIME IS ONE BATCH were bound in a scope whose
lifetime is one ARM, and three `nonlocal` declarations existed to reach back up to them.

That mismatch is the smell ticket 06 points at, in plain sight:

    _late = 0.0              # how late this batch was against its release slot
    _day_end = None          # the whistle for the day this batch was released into
    _put_base = None         # the put crew's epoch; the SITE's when pooled
    _batch_early = None      # the batch, sampled early for the standing-demand feed
    triggered = ()           # the SKUs this batch reordered

One object instead. The closures mutate its fields rather than rebinding names, so the
`nonlocal` lines go, and the arm's scope carries one name where it carried five.

## What is NOT here

No `begin()` or `reset()`. Every field is unconditionally re-seeded by `_replenish` before
`_step` can read it — `late` and `day_end` by assignment, `put_base` and `early` by an
explicit `= None` followed by a conditional set, `triggered` by `check_reorders` or by
`note_triggered` under coupling. A reset method would be a second writer of the same fact
that happens to be a no-op, which is the shape this whole effort is about deleting.

## Why a dataclass and not a namedtuple

These are WRITTEN by one half and READ by the other, several fields at a time, at different
points in a 600-line body. A frozen record would mean rebuilding it on every write, which
puts the rebinding problem back and adds allocation to the per-batch path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ['BatchState']


@dataclass
class BatchState:
    """Everything one batch's replenish half must hand its pick half. Lives one batch."""

    #: How late this batch was against its release slot, in seconds on the arm clock.
    #: Recorded on the batch row as `released_late`, and read by the equilibrium check --
    #: which is why it belongs to the batch and not to the run (memory
    #: `equilibrium-check-two-traps`: it belongs to the CAPPED day before it).
    late: float = 0.0

    #: The whistle for the day this batch was released into, or None when the day cut is
    #: off. A START gate, not a stop: work already begun runs past it.
    day_end: float | None = None

    #: The put crew's epoch for this batch — the SITE's when a put pool owns the crew, so
    #: that both leaves' rows land on one axis. None until `_replenish` decides.
    put_base: float | None = None

    #: The batch, sampled EARLY, because the standing-demand feed needs its items before
    #: the pick half runs. `_step` reuses this same object rather than re-sampling: a second
    #: draw would be a different batch under any sampler.
    early: Any = None

    #: The SKUs this batch reordered. Set by `check_reorders`, or handed over by the site
    #: coordinator under coupling (`note_triggered`), which reorders for every leaf at once.
    triggered: tuple = ()
