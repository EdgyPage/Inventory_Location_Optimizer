"""work_events — both work streams, as rows on one absolute axis.

The pick simulation and the put-away crew are simulated INDEPENDENTLY and merged
afterwards. That is the model the user specified — *"assume non-intersection or
non-interference among physical objects, so simulated on merge similar to fast_pick
simulating sequential inventory access"* — and it is the same shape `fast_pick` already
uses across its own pickers: phase 1 runs each actor against a snapshot, phase 2 reconciles.
One level up, the actors are crews rather than pickers.

So this module does not schedule anything. It takes what each stream produced and stamps
both onto one timeline:

    pick rows   from the batch's `PickEvent`s, whose `.time` is already absolute
                (the arm hands every picker the batch epoch as its start)
    put rows    from `Inventory_Manager.drain_putaway_records()`, whose clock is the put
                crew's own and is offset onto the axis here

## Two things that are decisions, not details

**`qty` is signed.** A pick removes inventory and a put adds it, so one row shape reads in
both directions and `SUM(qty)` over a bin is its net movement. A pick with no quantity (a
`task_start`, a `done`) carries NULL, not 0 — absent and zero are different.

**The put crew's clock starts at the batch epoch, not at zero.** The crew works through the
batch's placements back to back; nothing in this model makes it wait for a picker, and
nothing makes a picker wait for it.
"""
from __future__ import annotations

from Warehouse.kernel.timeline import DEFAULT_SHIFT_SECONDS, shift_index

#: Pick events that move inventory.  Everything else (task_start, arrive, task_end, done,
#: cart_swap) is a state change and carries no quantity.
_QTY_EVENTS = frozenset({'pick'})


def pick_rows(events, batch_id, batch_start, crew, *,
              shift_seconds: float = DEFAULT_SHIFT_SECONDS, first_seq: int = 0):
    """Merged-stream rows for one batch's pick events.

    `events` carry ABSOLUTE times already — the arm hands every picker the batch epoch as
    its start — so `t_abs` is `e.time` and `t_local` is the offset from `batch_start`.

    `crew` supplies the role, the mode and the uid space; `e.picker_id` is the dense
    per-crew id and becomes `actor_local`, with `actor_uid` its crew-offset twin.  A picker
    id outside the crew raises rather than being written under someone else's uid.
    """
    workers = crew.workers() if hasattr(crew, 'workers') else crew
    rows = []
    for seq, e in enumerate(events, start=first_seq):
        pid = e.picker_id
        if pid is None or not 0 <= pid < len(workers):
            raise ValueError(
                f'picker_id {pid} is outside the crew of {len(workers)}; a merged-stream '
                f'row cannot be written under another actor\'s uid')
        w = workers[pid]
        qty = -abs(e.quantity) if (e.event_type in _QTY_EVENTS and e.quantity) else None
        rows.append((batch_id, seq, e.time, e.time - batch_start,
                     shift_index(e.time, shift_seconds), w.uid, w.local_id,
                     str(w.role), str(w.mode), e.event_type, e.aisle_id, e.sku, qty,
                     0.0, None))
    return rows


def put_rows(records, batch_id, batch_start, crew, *,
             shift_seconds: float = DEFAULT_SHIFT_SECONDS, first_seq: int = 0):
    """Merged-stream rows for one batch's put-away.

    `records` are `Inventory_Manager.drain_putaway_records()` tuples
    `(t_start, dur, sku, qty, aisle_id, x_phys, y_phys, source)`, whose `t_start` runs on
    the put crew's OWN clock from 0. `batch_start` offsets them onto the arm's axis.

    Put-away is one crew working its queue back to back, so the records are laid out over
    the crew's workers round-robin: with a crew of one — today's default — every row is
    that worker's. The event type is `put`, and `qty` is positive.
    """
    workers = crew.workers() if hasattr(crew, 'workers') else crew
    if not workers:
        raise ValueError('a put crew of zero workers cannot have put anything away')
    rows = []
    for seq, rec in enumerate(records, start=first_seq):
        t_start, dur, sku, qty, aisle_id, _x, _y, source = rec
        w = workers[seq % len(workers)]
        t_abs = batch_start + t_start
        rows.append((batch_id, seq, t_abs, t_abs - batch_start,
                     shift_index(t_abs, shift_seconds), w.uid, w.local_id,
                     str(w.role), str(w.mode), 'put', aisle_id, sku, abs(int(qty)),
                     float(dur), source))
    return rows


def merged(rows):
    """The rows in the order `work_events_merged` declares.

    Kept here as well as in the view so an in-memory caller and a SQL caller cannot
    disagree about what "merged" means. Instant, then role, then mode, then actor, then
    emission order within that actor — `PickEvent.__lt__` compares time alone, and with two
    streams that undeclared tie-break decides whether a pick or a put is read first.
    """
    # Column offsets into a row tuple; see Picking_Data._WORK_EVENT_COLS.
    T_ABS, SEQ, UID, ROLE, MODE = 2, 1, 5, 7, 8
    return sorted(rows, key=lambda r: (r[T_ABS], r[ROLE], r[MODE], r[UID], r[SEQ]))
