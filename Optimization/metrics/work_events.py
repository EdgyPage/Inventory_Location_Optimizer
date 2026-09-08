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
#: cart_swap) is a state change and carries no quantity -- and no duration either: a pick
#: row is an instant, a put or unload row is an interval.  Both absences are NULL rather
#: than 0, because a consumer summing either column must not be handed a plausible total
#: assembled from half the streams.
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
        # duration NULL, not 0.0.  A pick event is an INSTANT -- task_start, pick, done,
        # cut -- not an interval, so it has no duration to report, and saying `0.0` claims
        # it took no time.  Same rule as `qty` above, and for the same reason: a consumer
        # summing this column must get put + receive labour and not a number that looks
        # like a total.  (Until 2026-08-25 the column was NOT NULL and every pick row said
        # 0.0.)
        rows.append((batch_id, seq, e.time, e.time - batch_start,
                     shift_index(e.time, shift_seconds), w.uid, w.local_id,
                     str(w.role), str(w.mode), e.event_type, e.aisle_id, e.sku, qty,
                     None, None))
    return rows


def put_rows(records, batch_id, batch_start, crew, *,
             shift_seconds: float = DEFAULT_SHIFT_SECONDS, first_seq: int = 0,
             crew_start=None, event_type: str = 'put'):
    """Merged-stream rows for one batch's put-away.

    `records` are `Inventory_Manager.drain_putaway_records()` tuples
    `(t_start, dur, sku, qty, aisle_id, x_phys, y_phys, source, worker, queue)`, whose
    `t_start` runs on that WORKER's own clock from 0 within this batch (the drain restarts
    them all).

    ONE QUEUE PER CALL.  Each put-away stream has its own crew, so its worker indices mean
    something only against that crew's roster -- worker 0 of the cart crew and worker 0 of
    the forklift crew are different people.  Handing this function a mixed list would
    silently attribute one stream's work to the other's actors, so it refuses.

    TWO ORIGINS, and they are not the same instant:

      `crew_start`  where the crew actually picks the work up, and what `t_abs` is measured
                    from. The crew is CONTINUOUS: it cannot start batch i's queue before
                    the wave is released, and it cannot start before it finished batch
                    i-1's, so the caller passes `max(batch_start, previous_finish)`.
                    Defaults to `batch_start` for a caller with no carry.
      `batch_start` the wave's release, and what `t_local` is measured from — so `t_local`
                    on a put row reads as "how far into this wave the crew got to it",
                    and EXCEEDS the batch's duration exactly when the crew is running
                    behind. That is information, not an error.

    Why the crew must carry: one putter placing a whole wave's restock takes longer than
    the parallel pick crew takes to pick it, so batch i's put-away genuinely overruns batch
    i+1's release. Restarting at each wave would have the same single worker doing two
    batches at the same instant — measured on a store arm before this carry existed, 16-33
    rows per DB overlapped.

    Each record names the WORKER that did it. The manager gives every put to whichever of
    its crew is free earliest, so the worker is a scheduling outcome, not a function of
    position in the list. This used to round-robin on `seq % len(workers)` while the
    durations came from a single serial clock — so a crew of two reported two people each
    doing every other put, at instants that said one person did all of them. `qty` is
    positive.

    `event_type` is a PARAMETER and not the literal it used to be. `role` comes from the
    worker, so a second stream reusing this body would otherwise write `role='receive'` with
    `event_type='put'`, and `SUM(duration) WHERE role='put'` would disagree with the same
    query on `event_type` with nothing to point at. `recv_rows` below is that second stream;
    it shares this body precisely so the two-origin split, the worker bounds check and the
    one-stream-per-call refusal exist once.
    """
    if crew_start is None:
        crew_start = batch_start
    workers = crew.workers() if hasattr(crew, 'workers') else crew
    if not workers:
        raise ValueError('a put crew of zero workers cannot have put anything away')
    rows = []
    _queues = {r[9] for r in records}
    if len(_queues) > 1:
        raise ValueError(
            f'put_rows got records from {sorted(_queues)}; each queue has its own crew, so '
            f'group by queue and call once per stream')
    for seq, rec in enumerate(records, start=first_seq):
        t_start, dur, sku, qty, aisle_id, _x, _y, source, widx, _queue = rec
        if not 0 <= widx < len(workers):
            raise ValueError(
                f'put record names worker {widx}, but the crew has {len(workers)}; the '
                f'manager was bound with a different size than the crew reporting it')
        w = workers[widx]
        t_abs = crew_start + t_start
        rows.append((batch_id, seq, t_abs, t_abs - batch_start,
                     shift_index(t_abs, shift_seconds), w.uid, w.local_id,
                     str(w.role), str(w.mode), event_type, aisle_id, sku, abs(int(qty)),
                     float(dur), source))
    return rows


def recv_rows(records, batch_id, batch_start, crew, *,
              shift_seconds: float = DEFAULT_SHIFT_SECONDS, first_seq: int = 0,
              crew_start=None, event_type: str = 'receive'):
    """Merged-stream rows for one batch's RECEIVING, through `put_rows`' body.

    `records` are `Inventory_Manager.drain_receiving_records()` tuples
    `(t_start, dur, sku, qty, worker)`, widened here to the 10-slot put shape with `None`
    for the aisle and 0.0 for the coordinates -- a dock has no aisle and no position. The
    `source` slot carries `'dock'`, which is where the work happened; the merchandise's own
    origin is already on the put row that follows it.

    A THIN WRAPPER ON PURPOSE. Everything that is easy to get subtly wrong lives in
    `put_rows`: `crew_start` for `t_abs` against `batch_start` for `t_local`, the worker
    bounds check, and the refusal to mix streams in one call. A second implementation would
    have its own copy of each, and the copies would drift somewhere nobody looks.

    Refuses a `Crew` OBJECT rather than its pre-offset worker tuple. `Crew.workers()`
    defaults `first_uid=0`, so passing the crew here would silently re-allocate uids from
    zero and collide with the pick crew -- and nothing downstream could tell: a collision
    passes the bounds check, the DDL has no uniqueness constraint, and the merged view still
    sorts. Any per-actor rollup would then merge two people, and the failure is quietest
    exactly when the receiving crew is small, which is the likely configuration.
    """
    if hasattr(crew, 'workers'):
        raise TypeError(
            'recv_rows needs the receive crew\'s pre-offset worker TUPLE, not the Crew '
            'object: Crew.workers() restarts uids at 0, which collides with the pick crew '
            'and is invisible downstream. Pass crew.workers(first_uid).')
    wide = []
    for t_start, dur, sku, qty, widx in records:
        if int(qty) <= 0:
            raise ValueError(
                f'receive record for sku {sku} carries qty {qty}; an unload moves '
                f'merchandise, and a zero would be stored where a state change stores NULL')
        wide.append((t_start, dur, sku, qty, None, 0.0, 0.0, 'dock', widx, 'dock'))
    return put_rows(wide, batch_id, batch_start, crew, shift_seconds=shift_seconds,
                    first_seq=first_seq, crew_start=crew_start, event_type=event_type)


def repack_rows(records, batch_id, batch_start, crew, *,
                shift_seconds: float = DEFAULT_SHIFT_SECONDS, first_seq: int = 0,
                crew_start=None):
    """`repack` rows for one batch's PUT-AWAY REWORK (ADR-0003), through `recv_rows`' body.

    `records` are `Inventory_Manager.drain_repack_records()` tuples, identical in shape to
    the unload ones and charged on the same crew clock -- a repack IS receiving work, done
    by the receiving crew at the dock's own per-pack price.

    `role` is therefore `'receive'` (it comes from the worker) and only `event_type` differs.
    That split is the whole reason `put_rows` takes the type as a parameter: a reader asking
    "what did receiving cost" sums `role='receive'` and gets unloads AND rework, while one
    asking "how much rework was there" filters `event_type='repack'`. Folding repacks into
    the `'receive'` event type would make the second question unanswerable; giving them
    their own role would make the first one wrong.

    A SEPARATE CALL from `recv_rows` rather than a widened one, because `put_rows` writes a
    single event type per call by construction.
    """
    return recv_rows(records, batch_id, batch_start, crew, shift_seconds=shift_seconds,
                     first_seq=first_seq, crew_start=crew_start, event_type='repack')


def merged(rows):
    """The rows in the order `work_events_merged` declares.

    Kept here as well as in the view so an in-memory caller and a SQL caller cannot
    disagree about what "merged" means. Instant, then BATCH, then role, then mode, then
    actor, then emission order within that actor — `PickEvent.__lt__` compares time alone,
    and with two streams that undeclared tie-break decides whether a pick or a put is read
    first at the same instant.

    `batch_id` is in the key because `seq` restarts at 0 every batch, and consecutive
    batches genuinely TOUCH: the arm advances to `batch_start + duration`, which is exactly
    the last `done` instant, so batch i's final `done` and batch i+1's first `task_start`
    for the same picker share a t_abs, a role, a mode and an actor. Without the batch the
    tie fell through to `seq` — large for the `done`, near 0 for the `task_start` — and
    ordered them backwards, the reverse of the emission order this key promises.
    """
    # Column offsets into a row tuple; see Picking_Data._WORK_EVENT_COLS.
    BATCH, SEQ, T_ABS, UID, ROLE, MODE = 0, 1, 2, 5, 7, 8
    return sorted(rows, key=lambda r: (r[T_ABS], r[BATCH], r[ROLE], r[MODE],
                                       r[UID], r[SEQ]))
