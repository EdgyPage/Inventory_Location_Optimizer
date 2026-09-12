"""putaway_pool — one site pool of putters over two channels' segregated volume.

`PutawayPool` is the site's put-away crew: ONE `list[float]` of worker clocks, bound to
both channels' put queues, so `crew_clock.charge` greedy-books every put to whichever
putter is free earliest ACROSS BOTH CHANNELS.  A putter genuinely takes work from either
queue, with no new scheduling vocabulary — the sharing is identity, not arithmetic.

Segregation survives untouched.  The two managers keep their own queues, carts, geometries
and `_put_seconds`; a cart carries one channel's packs only.  **Only the people are
shared**, which is exactly the charter's "one site pool over segregated volume".

# ── why it lives here, and cannot live anywhere else ──────────────────────────────

`wh_operations -> wh_inventory` is forbidden (`context/architecture.yml:88`), so the
package where crews live cannot drive a manager's drain; `warehouse_core -> inbound` and
`wh_inventory -> inbound` are forbidden in BOTH directions (`:102-103`).  `Inbound/` is
therefore the only package that may sit above two managers — the same argument that put
`SiteReceiving` here.

So this module never imports a manager.  It DUCK-TYPES its leaves and reaches each one
through exactly three public ports:

    leaf.drain_putaway(deadline, charge_cut=False) -> None
        Phase 5 of a completed batch, promoted from `_drain_putaway` for this caller.
    leaf.count_put_cut(deadline) -> None
        What the whistle stopped, charged ONCE per site day against the FULL day.
    leaf.drain_putaway_records(reset_clocks=False)
        Not called here — the driver calls it and reports the result to `note_records`,
        because only the driver knows the batch epoch the rows are stamped from.

# ── the three things this owns that nobody else can ───────────────────────────────

**The day's division.**  With shared clocks and a fixed leaf order, the first leaf drains,
pushes the clocks, and the second starts from wherever the first left them — so the second
would absorb every cut, every day, as a pure artefact of loop order.  That bias would look
exactly like a finding.  Each leaf therefore drains against a CUMULATIVE sub-deadline at
its own share of the day, and then every leaf re-drains against the whole day: the residue
pass IS the charter's "a channel that finishes early releases labour to the other", and it
runs in both directions.  Earlier-to-later is free (the cumulative deadline hands the later
leaf whatever the earlier one did not spend); later-to-earlier is what the residue pass is
for.

**The reset.**  `drain_putaway_records()` resets every queue's clocks, and on a shared list
one leaf's drain resetting the pool before the other has recorded is a silent, plausible-
looking zeroing — every subsequent row wrong and nothing raising.  So on a coupled run the
manager drain stops resetting (`reset_clocks=False`) and the pool resets the one list once
per site day, after BOTH leaves have handed their records over.  Giving that trap a single
owner is the main argument for this module existing at all.

**The site `put_clock`.**  A shared clock list cannot carry two epochs: `put_clock` is the
absolute carry `crew_start` is measured from, so two carries over one list would stamp the
same worker's same second at two different absolute instants.  Under one put crew there is
one put carry, based at the SITE DAY START — `max(day.start_of(i), put_clock)` — never at
either leaf's `batch_start_time`, which would idle the site's putters whenever a pick crew
overran its day and misattribute a picking overrun to put-away's cut.

# ── what is NOT here ──────────────────────────────────────────────────────────────

Within-day interleaving.  The two leaves each run their day as today and draw from one pool
of crew-seconds; a putter is never dispatched between the channels mid-batch.  An
event-driven cadence is out of scope, inherited.

The band.  What the coupled put clause READS — one site number against the summed
expectation, retiring `expected_utilization`'s "single-channel leaves undercut rho" caveat
for put — needs the site analysis stage and belongs to "Re-scope the analysis surfaces".
"""
from __future__ import annotations

from Warehouse.kernel import crew_clock


class PutawayPool:
    """The site's putters: one clock list, one uid block, one carry, N leaves.

    Constructed by the driver at UNIT scope, where the crew is sized from the staffing
    record, and bound onto each leaf it serves (`mgr.putaway_pool`) — the same injection
    precedent as `enable_receiving`, `mgr.packer` and `mgr.receiving`: the broker holds
    what it is handed, and nothing under `Warehouse/` imports this package.

    `clocks` and `workers` are parallel and index-aligned: clock `w` is worker `w`, which
    is what lets `work_events.put_rows` name the same person in both channels' DBs.  The
    driver mints both, exactly as it mints every other crew — this holds them.
    """

    def __init__(self, clocks: list, workers: tuple, expected_utilization: dict,
                 day, *, channels: tuple, releases_per_day, cut_at_day_end: bool):
        #: THE WORKING-DAY GRID IS A PRECONDITION, not a mode.  The pool divides a DAY
        #: between two channels and resets a batch-local clock list once per site day, so
        #: without a whistle there is nothing to divide, and without one batch per day
        #: "once per site day" is not a thing this loop can say.  The era forces both
        #: (`run_simulation`: `--releases-per-day` is completed to 1 and refused at any
        #: other value under `--shift-drain-or-cap`, which forces the cut on), and every
        #: coupled run derives its crews, so this is unreachable by design rather than by
        #: luck -- which is a reason to state it, not to omit it.
        if not cut_at_day_end:
            raise ValueError(
                'a site put pool needs a working day to divide: `cut_at_day_end` is off, '
                'so no whistle blows, the sub-deadlines are unbounded and the pool '
                'degenerates to the fixed-leaf-order bias it exists to remove')
        if releases_per_day != 1:
            raise ValueError(
                f'a site put pool assumes one batch is one site day; this run releases '
                f'{releases_per_day!r} batch(es) per day. The reset, the division and the '
                f'carry are all denominated in site days, and a grid that puts several '
                f'batches in one would reset the crew mid-day')
        if not channels:
            raise ValueError('a site put pool with no channels has nobody to serve')
        self.clocks = clocks
        self.workers = workers
        self.day = day
        #: The declared leaf ORDER: the order the driver steps them in, and therefore the
        #: order the cumulative sub-deadlines are stacked in and the residue pass runs in.
        self.channels = tuple(channels)
        #: The site carry: where the put crew finished, on the ABSOLUTE axis.  Committed
        #: once per site day, after every leaf has reported, so both leaves of one batch
        #: read the same base.
        self.put_clock: float = 0.0
        self._cum = _cumulative_shares(expected_utilization, self.channels)
        self._leaves: dict = {}
        # Per-batch state.  `_open` is None until the first `open_batch`.
        self._open = None
        self._base = 0.0
        self._deadline = None
        self._owed_drain: set = set()
        self._owed_records: set = set()
        self._finish = None

    def __repr__(self):
        return (f'PutawayPool(crew={len(self.clocks)}, channels={self.channels!r}, '
                f'shares={self._cum!r})')

    # ── binding ───────────────────────────────────────────────────────────────────

    def bind(self, leaf, channel: str) -> None:
        """Serve `channel`'s leaf.  Call once per leaf, before the batch loop.

        The leaf's queues must already be bound to `self.clocks`
        (`enable_putaway_timing(..., clocks=pool.clocks)`) — that binding is what makes
        the pool real, and this only records who the driver will be stepping.
        """
        if channel not in self.channels:
            raise ValueError(
                f'{channel!r} is not one of this pool channels {self.channels!r}; the '
                f'day is divided between the channels it was SIZED from, so a leaf the '
                f'shares do not name has no share to take')
        if channel in self._leaves:
            raise ValueError(
                f'the {channel} leaf is already bound to this pool; two leaves of one '
                f'channel would each take that channel whole share of the day')
        self._leaves[channel] = leaf

    def _channel_of(self, leaf) -> str:
        for ch, lf in self._leaves.items():
            if lf is leaf:
                return ch
        raise ValueError(
            'a leaf drained against this pool without being bound to it; the pool divides '
            'the day between the leaves it knows about, so an unbound one would take a '
            'share nobody accounted for')

    # ── the site day ──────────────────────────────────────────────────────────────

    def open_batch(self, index: int) -> tuple:
        """`(base, deadline)` for site day `index`: the put crew's absolute epoch and the
        whistle as a REMAINDER on the batch-local clocks.

        IDEMPOTENT PER INDEX, and that is the contract: every leaf asks, and every leaf
        must get the same answer, because one clock list cannot carry two epochs.  The
        first call computes; the rest read.

        `base` is `max(day.start_of(index), put_clock)` — the putters start at shift start
        and work what is on the dock, or they carry on from where yesterday's overrun left
        them.  `deadline` is the rest of the day CONTAINING that base, so base + deadline
        is the day's end however far the carry has run.
        """
        if index == self._open:
            return self._base, self._deadline
        if self._owed_drain or self._owed_records:
            raise RuntimeError(
                f'site day {index} opened while day {self._open} still owes '
                f'{sorted(self._owed_drain | self._owed_records)}; the pool resets one '
                f'shared clock list per day and a day that opens early would reset it '
                f'under a leaf that has not recorded yet')
        if not self._leaves:
            raise RuntimeError(
                'no leaf is bound to this pool; bind every leaf before the batch loop, or '
                'the day is divided between people nobody is scheduling')
        self._open = index
        self._base = max(self.day.start_of(index), self.put_clock)
        self._deadline = self.day.remaining(self._base)
        self._owed_drain = set(self._leaves)
        self._owed_records = set(self._leaves)
        self._finish = None
        return self._base, self._deadline

    # ── the two passes ────────────────────────────────────────────────────────────

    def drain(self, leaf, deadline) -> None:
        """Phase 5 for `leaf`, against its share of the day — and, on the last leaf of the
        day, the residue pass and the cut for every leaf.

        Reached from `Inventory_Manager.check_reorders`, which routes phase 5 here when a
        pool is bound exactly as it routes phase 4 to `mgr.receiving`.  `deadline` is the
        whole day's remainder, the value `open_batch` handed the driver; it is CHECKED
        against the pool's own rather than trusted, because a leaf drained against a
        different day than the one the clocks are running on would place work nobody has
        the hours for, and every row of it would look ordinary.

        NEITHER PASS CHARGES THE CUT.  `cut` is a LEVEL — it re-counts the standing queue
        every time it is charged — so two charges inside one batch inflate it where no
        downstream "count the non-zero batches" rule can undo it.  Both drains pass
        `charge_cut=False` and the cut is counted once, below, against the FULL day.
        """
        ch = self._channel_of(leaf)
        if self._open is None:
            raise RuntimeError(
                'a leaf drained before the site day was opened; `open_batch` sets the base '
                'every row is stamped from and the day every sub-deadline is a share of')
        if ch not in self._owed_drain:
            raise RuntimeError(
                f'the {ch} leaf drained twice in site day {self._open}; the second pass is '
                f'the pool own to run, and a leaf running its own would drain against the '
                f'whole day after already spending its share of it')
        if deadline != self._deadline:
            raise RuntimeError(
                f'the {ch} leaf drained against a deadline of {deadline!r} but site day '
                f'{self._open} has {self._deadline!r} left; the two leaves share one clock '
                f'list and must share the day it is measured against')
        self._owed_drain.discard(ch)
        # PASS 1: this leaf's own share, stacked cumulatively.  The share is of the DAY,
        # and the clocks are the day, so the sub-deadline is a point on the same remainder
        # the whole day is stated in.  The last leaf's cumulative share is 1.0, so it
        # drains against the whole day and takes whatever the earlier ones left -- which
        # is the earlier-to-later half of "finishes early releases labour to the other",
        # free, with no second pass involved.
        leaf.drain_putaway(self._cum[ch] * deadline, charge_cut=False)
        if self._owed_drain:
            return
        # PASS 2, THE RESIDUE: every leaf again, against the WHOLE day.  This is the
        # later-to-earlier direction, the one the loop order hides -- an earlier leaf cut
        # by its own share can now spend what a later leaf did not.  A no-op for the last
        # leaf by construction (it already drained against this deadline) and a no-op for
        # anyone whose queue is empty; run over every leaf anyway, so the rule is one rule
        # and not a rule with an exception at the end of the list.
        for c in self.channels:
            lf = self._leaves.get(c)
            if lf is not None:
                lf.drain_putaway(deadline, charge_cut=False)
        # THE CUT, once per leaf per site day, against the full day and AFTER the residue.
        # Per leaf and not summed: a site total would hide exactly the asymmetry the
        # sub-deadlines exist to prevent.
        for c in self.channels:
            lf = self._leaves.get(c)
            if lf is not None:
                lf.count_put_cut(deadline)

    # ── the close ─────────────────────────────────────────────────────────────────

    def note_records(self, leaf, finish) -> None:
        """`leaf` has handed its records over and stamped them; `finish` is the absolute
        instant its last put ended, or None when it recorded nothing.

        Called once per leaf per site day, on BOTH the picked and the skipped path — a
        batch that produced no tasks still ran `check_reorders` and may have put hundreds
        of units away.  When the last leaf reports, the shared clocks reset and the site
        carry is committed: both halves of "the pool resets once per site day, after both
        leaves have drained".

        The carry moves only when somebody actually worked.  No records anywhere means the
        crew is where it was, exactly as an unpooled leaf leaves `put_clock` alone.
        """
        ch = self._channel_of(leaf)
        if ch not in self._owed_records:
            raise RuntimeError(
                f'the {ch} leaf reported records twice in site day {self._open}; the '
                f'second report would reset the shared clocks under the other leaf')
        if self._owed_drain:
            raise RuntimeError(
                f'the {ch} leaf reported records while {sorted(self._owed_drain)} has not '
                f'drained; put-away runs before the rows are stamped, so this ordering '
                f'means a leaf is about to place work against clocks that are about to be '
                f'reset')
        self._owed_records.discard(ch)
        if finish is not None:
            self._finish = finish if self._finish is None else max(self._finish, finish)
        if self._owed_records:
            return
        crew_clock.reset(self.clocks)
        if self._finish is not None:
            self.put_clock = self._finish


def _cumulative_shares(expected_utilization: dict, channels: tuple) -> dict:
    """Each channel's share of the day, stacked in `channels` order.

    `expected_utilization[n]` is `load_n / (crew x S)` by construction, so crew and day
    cancel and the share is just `exp_n / sum(exp)` — the split needs no new record field.
    A single-channel catalogue gets 1.0 and the residue pass is a no-op, so the degenerate
    case is correct for free.

    Stacked (0.4, 1.0) rather than flat (0.4, 0.6) because the clocks are SHARED: the
    second leaf starts from wherever the first left them, so its whistle is a point on the
    same day, not a length of its own.  The last channel's is 1.0 by construction, which
    is what hands it everything the earlier ones did not spend.

    A site expecting NO put work at all gives everyone the whole day: nobody should be
    starved by a script that said nothing was coming, and with no expectation to divide
    there is no fairer division than none.
    """
    missing = [c for c in channels if c not in expected_utilization]
    if missing:
        raise ValueError(
            f'no recorded put expectation for {missing}; the day is divided by '
            f'`derived.put.expected_utilization`, and a channel absent from it would be '
            f'silently starved rather than loudly unsized')
    total = sum(float(expected_utilization[c]) for c in channels)
    if total <= 0.0:
        return {c: 1.0 for c in channels}
    out, run = {}, 0.0
    for c in channels:
        run += float(expected_utilization[c]) / total
        out[c] = run
    # The last one is 1.0 exactly, not 0.9999999999999999: a whistle a float short of the
    # day would cut the last leaf by one job for no modelled reason.
    out[channels[-1]] = 1.0
    return out
