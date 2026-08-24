"""worker — one actor, and a pool of them, with the two id spaces kept apart.

## The two id spaces, and why there are two

Every existing reader assumes picker ids are **dense and per-crew**: `assign_tasks`
partitions work into `num_pickers` buckets and the bucket index IS the id;
`Simulation_Analytics._group_events_by_picker` allocates exactly `k_pickers` lists and now
RAISES on anything outside `[0, k)`; `_ProgressAPIMixin.progress_at` enumerates
`range(num_pickers)`; and the `picker_events` table and the viewer are built on the same
assumption.

A second crew breaks that the moment it shares an event stream — its actors carry ids from
a different space, and the old `0 <= pid < k_pickers` filter used to swallow them silently.

So a `Worker` carries both:

  `local_id`  dense within its own crew, `0..size-1`.  This is what goes to `picker_events`
              and to everything that predates a second crew.  It is NOT unique across crews.
  `uid`       unique across every crew in the run.  This is what goes to the merged event
              stream, beside a role and a mode, so a row can say WHO without ambiguity.

Keeping them as two named fields rather than one clever encoding is the point: a `uid` that
leaked into a `local_id` slot would land inside `[0, k)` and be accepted.
"""
from __future__ import annotations

from dataclasses import dataclass

from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.operations.roles import Mode, Role


@dataclass(frozen=True)
class Worker:
    """One actor: who it is, what it does, how it moves, and how fast.

    Frozen and picklable — a crew is built in the parent and crosses the spawn boundary in
    the worker payload, like every other value object here.
    """
    uid: int                 # unique across the whole run (merged event stream)
    local_id: int            # dense within this worker's own crew (picker_events)
    role: Role
    mode: Mode
    speed: SpeedProfile

    def __post_init__(self) -> None:
        if self.uid < 0:
            raise ValueError(f'worker uid {self.uid} is negative')
        if self.local_id < 0:
            raise ValueError(f'worker local_id {self.local_id} is negative')

    @property
    def kind(self) -> str:
        """`'pick/machine'` — the (role, mode) pair as one label, for an event row."""
        return f'{self.role}/{self.mode}'


@dataclass(frozen=True)
class Crew:
    """A pool of identical workers: one role, one mode, one speed, a size.

    This is what `Optimization/config/channels.PickerProfile` has always been -- a name, a
    cost profile carrying travel speeds, and a count -- with the two things it was missing
    made explicit, and with no dependency on the run harness so a second stream can reuse it.
    """
    role: Role
    mode: Mode
    speed: SpeedProfile
    size: int

    def __post_init__(self) -> None:
        if self.size < 1:
            raise ValueError(f'a crew of {self.size} does no work; size must be >= 1')

    def workers(self, first_uid: int = 0) -> tuple[Worker, ...]:
        """This crew's workers, `local_id` 0..size-1 and `uid` running from `first_uid`.

        The caller owns uid allocation across crews — pass the previous crew's
        `first_uid + size` — because only the caller knows how many crews there are.  See
        `next_uid`.
        """
        return tuple(Worker(uid=first_uid + i, local_id=i,
                            role=self.role, mode=self.mode, speed=self.speed)
                     for i in range(self.size))

    def next_uid(self, first_uid: int = 0) -> int:
        """The first uid a crew allocated AFTER this one may use."""
        return first_uid + self.size
