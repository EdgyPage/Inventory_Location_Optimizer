"""crew_clock — one crew's workers as a list of instants, and the five rules over it.

# ── why this is functions over a list, and not a class ────────────────────────────

Every timed work stream in this simulator needs the same five things: a clock per worker,
"who is free earliest", "is anyone free before the whistle", "when does the last one finish",
and "start the batch over". Put-away had them as five `PutQueue` methods. Receiving needs the
identical five, and two copies of a rule like `min(clocks) < deadline` will drift — one will
say `<=` some day, and the number it produces will be plausible forever.

They are **functions over a bare `list[float]`** rather than a `CrewClock` class for two
reasons, in order. First, `Warehouse/inventory/put_queue.py` states in its own docstring that
three streams must not become three classes; a base class is exactly the hierarchy it refuses,
and it would force `PutQueue` to swap a real slot for a property. Second, `list[float]` is
already the representation everything holds, so nothing has to be migrated and no test that
assigns to `q.clocks` has to change.

`architecture.yml` forbids `wh_kernel -> *`, so this module imports nothing at all. That is
what lets both `Warehouse/inventory/` and `Warehouse/operations/` reach it without inverting
anything.

# ── the two contracts a caller must know ──────────────────────────────────────────

**The clock is BATCH-LOCAL.** Every value here starts at 0 at the top of a batch and is reset
by whoever drains that batch's records. The absolute instant is the caller's business: it
holds a per-arm carry and adds the batch epoch when it writes rows. This is not tidiness --
`Inventory_Manager.drain_putaway_records` records what happened when it was skipped, with the
measurement: batch 7's puts landed at t_local 53,769-64,152 s against a 17,906 s batch, and
the error grows without bound.

**`can_start` is a START gate, not a completion gate.** A worker already past the whistle
begins nothing new; the job in progress when it blows runs to the end. Overtime is therefore
bounded by one job per worker, which is what the physical thing does -- a putter does not set
a pallet down halfway up an aisle, and a receiver does not leave half a pallet on the tail
lift. A completion gate would need the job's duration, which is known only after its
destination is chosen, so it would mean choosing and then un-choosing a placement.
"""
from __future__ import annotations


def new_clocks(size: int, what: str = 'crew') -> list:
    """`size` idle workers. Raises below 1, because a crew of nobody is a configuration
    error rather than a crew that does nothing slowly.

    `what` names the owner in the message -- a queue name, a dock name -- so a bad size in a
    multi-stream configuration says WHICH stream.
    """
    if size < 1:
        raise ValueError(f'{what}: a crew of {size} does no work; size >= 1')
    return [0.0] * size


def size_of(clocks) -> int:
    """How many workers. 0 when unbound, which is every stream that never got a crew."""
    return len(clocks) if clocks else 0


def can_start(clocks, deadline: float | None) -> bool:
    """Is anyone free to BEGIN work before `deadline`?

    `deadline` is on the same batch-local clock these values run on -- a REMAINDER, not an
    instant. That is what lets a caller state a day boundary without knowing the batch epoch,
    and it is also the easiest thing to get wrong: hand this an absolute instant and the
    whistle never fires, so the day reads as unbounded and the cut counter reads as zero,
    which looks exactly like "the boundary cost nothing".

    True when unbound: a stream with no crew has no clock to be past, and every run that does
    not ask for a cut passes None anyway.
    """
    if deadline is None or not clocks:
        return True
    return min(clocks) < deadline


def finish(clocks) -> float:
    """When the LAST worker becomes free, on the batch-local clock. 0.0 when unbound."""
    return max(clocks) if clocks else 0.0


def charge(clocks, dur: float):
    """Book `dur` to whoever is free earliest; return `(start, worker_index)`.

    Greedy list scheduling. Ties break to the lowest index, so a crew of one is exactly a
    serial clock -- which is what makes a size-1 stream byte-identical to the single-clock
    model that preceded this.
    """
    w = min(range(len(clocks)), key=lambda i: (clocks[i], i))
    t0 = clocks[w]
    clocks[w] = t0 + dur
    return t0, w


def reset(clocks) -> None:
    """Restart every worker at 0, IN PLACE.

    In place rather than rebinding, so a caller holding the list keeps the one the owner
    uses. A drain does this per batch and the runner adds the batch epoch back on when it
    writes the rows -- see the module docstring for what happens when it does not.
    """
    if clocks is not None:
        clocks[:] = [0.0] * len(clocks)
