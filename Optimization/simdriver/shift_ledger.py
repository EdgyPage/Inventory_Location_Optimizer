"""shift_ledger.py — the drain-or-cap working day's close-out state.

WHAT IT REPLACES.  `strategy_runner._build_leaf` carried this as four closure variables --
`_shift_prev_day`, `_shift_cut_today`, `_shift_last_finish`, `_shift_standing` -- written in
one block per batch and read again at run end for the final day, which has no next-day
boundary to close it.

# ── the ordering rule IS the specification ────────────────────────────────────────

    the boundary is tested BEFORE this batch's clocks, cut and depths are folded in, so the
    previous day closes on what ITS last batch left behind and this batch -- the first of the
    new day -- is attributed to the new day.

That sentence was a comment in the batch loop and nothing enforced it.  Here it is the
INTERFACE: `advance_to(day)` answers "is there a day to close, and with what?", and `note()`
folds this batch in.  Calling them in the other order mis-attributes every day's first batch,
and `Tests/unit/test_shift_ledger.py` pins the rule with a sabotage proving the wrong order
is actually visible.

# ── what each field is, and why they reset differently ────────────────────────────

    prev_day     the day currently open; None until the first batch
    cut_today    STICKY within a day (`cut or <this batch>`), reset at a boundary -- once a
                 day has cut work it stays cut however many later batches fit
    last_finish  a running MAX over the three crew clocks, reset at a boundary -- a batch
                 whose crews finished earlier must not pull the day's finish backwards
    standing     a LEVEL, REPLACED each batch and NOT reset at a boundary: it is re-measured
                 every batch, so the carried value is simply the last reading until this
                 day's first batch overwrites it.  Summing a level across batches is the
                 `recv_cut` scar; this one is only ever assigned.

# ── what is deliberately NOT here ─────────────────────────────────────────────────

`_shift_close_out` -- the pure function that turns a snapshot into a DB row and decides
`drained` through `equilibrium.is_drained` -- stays in `strategy_runner`.  This object holds
the state that function is CALLED WITH; it makes no judgement about a day, and moving the
judgement here would put `equilibrium` behind a second door.

FLAG-OFF IS STRUCTURAL.  Every call site sits inside `if _drain_or_cap:`, so a run without
the era flag never constructs one and never calls it.  That is what makes this extraction
byte-identical by construction rather than by argument.

Slotted and picklable, like the other `_build_leaf` state objects: the leaf that owns one is
built inside a spawned worker.
"""
from __future__ import annotations


class ShiftLedger:
    """Per-day close-out state for the drain-or-cap working day.

        shift = ShiftLedger()
        ...
        closed = shift.advance_to(day)        # BOUNDARY FIRST
        if closed is not None:
            rows.append(close_out(*closed))
        shift.note(cut=..., finish=..., standing=...)   # then this batch
        ...
        last = shift.final()                  # at run end: the day nothing closed
    """

    __slots__ = ('prev_day', 'cut_today', 'last_finish', 'standing')

    def __init__(self) -> None:
        self.prev_day = None
        self.cut_today = False
        self.last_finish = 0.0
        self.standing = (0, 0, 0, 0)   # (put queues + held, dock floor, LABOUR, SUPPLY)

    def __repr__(self) -> str:
        return (f'<ShiftLedger day={self.prev_day} cut={self.cut_today} '
                f'finish={self.last_finish:.1f} standing={self.standing}>')

    # ── the boundary ──────────────────────────────────────────────────────────────

    def advance_to(self, day):
        """Move to `day`.  Returns the snapshot of the day that just ENDED, or None.

        None on the first batch (no previous day exists) and within a day.  On a boundary
        the returned tuple is `(day, standing, last_finish, cut)` for the day that ended --
        `_shift_close_out`'s exact parameter order -- and the new day is opened uncut with
        no finish.  Days need not be contiguous: an empty day still consumes a release slot,
        and nothing is invented for the days in between.
        """
        if self.prev_day is None:
            self.prev_day = day
            return None
        if day == self.prev_day:
            return None
        closed = (self.prev_day, self.standing, self.last_finish, self.cut_today)
        self.prev_day = day
        self.cut_today = False
        self.last_finish = 0.0
        return closed

    # ── this batch ────────────────────────────────────────────────────────────────

    def note(self, *, cut: bool, finish: float, standing: tuple) -> None:
        """Fold one batch into the open day.  Call AFTER `advance_to` -- see the docstring."""
        self.cut_today = self.cut_today or bool(cut)
        if finish > self.last_finish:
            self.last_finish = finish
        self.standing = standing

    # ── run end ───────────────────────────────────────────────────────────────────

    def final(self):
        """The still-open day, for the run-end close-out, or None if none ever opened.

        The last day of a run has no next-day boundary, so without this every era run would
        report one day fewer than it worked and the equilibrium check's "every day drained"
        would be read over a window missing its last member.
        """
        if self.prev_day is None:
            return None
        return (self.prev_day, self.standing, self.last_finish, self.cut_today)
