"""checkpoint_buffer — accumulate, flush, clear: one object instead of thirteen lists.

`strategy_runner` declared THIRTEEN bare lists, appended to them across ~40 scattered sites,
flushed them through a 15-keyword call, cleared them with four statements naming all thirteen,
and flushed them again with the same 15 keywords at run end. `save_checkpoint_bundle` is the
other end: sixteen parameters over a body that is fifteen lines of
`if rows: _insert_x(con, run_id, rows)`. **The interface was as complex as the implementation.**

Its own docstring names the characteristic failure:

> A bundle argument that is accepted and never inserted is this function's characteristic
> failure: `work_events` was one for a while, and the reconciliation that was supposed to
> catch it passed over 68 databases holding zero rows.

That cannot happen here. A channel IS its insert function — `CHANNELS` is the only place a
name exists — so an accepted-but-never-inserted argument is not expressible, and
`Tests/unit/test_checkpoint_buffer.py` walks the table and reads every channel back off a
real file.

## `close()` is the design decision

**A run-end close ALWAYS writes**, open window or not. `SectionTimers` made the same call for
the same reason and states it the same way.

The old form guarded its run-end flush with `if pb:` — "is there an unflushed window",
inferred from one of the thirteen buffers. That proxy has been escaped twice, and each escape
left a paragraph behind explaining why its writer sits OUTSIDE the guard: the final day's
close-out, and the censored yard tail, which "would report `lifo`'s fee as CLIPPED rather than
concentrated, which inverts the signal the arm exists to produce". Memory
`run-end-writers-miss-the-final-flush` records the general shape: the tail does not fire when
`n_batches` divides the checkpoint cadence.

With `close()` writing unconditionally there is nothing to sit outside.

## The channel table is the seam, and its ORDER is load-bearing

`CHANNELS` is iterated in declared order, and that order is `save_checkpoint_bundle`'s
historical call order. Per-table rowids and the run digest depend on it: two tables written in
the other order get different `id` sequences on the same rows. Reordering this tuple is a
behaviour change, not tidying.

`skip_when_empty` mirrors what the individual writers did: three of them early-returned on an
empty list and the rest did not. It moves no row either way (the tables all pre-exist from
`init_run_db`); it is preserved because an empty `executemany` still costs a statement
preparation on a ~1 GB WAL file, and because parity is cheaper to keep than to re-argue.
"""
from __future__ import annotations

from typing import Callable

from Optimization.persistence import Picking_Data as _pd

__all__ = ['CheckpointBuffer', 'CHANNELS', 'SITE_CHANNELS']


#: `(name, insert_fn, skip_when_empty)`, IN SAVE ORDER. See the module docstring: the order is
#: the historical one and rowids depend on it.
CHANNELS: tuple[tuple[str, Callable, bool], ...] = (
    ('batch_stats',     _pd._insert_batch_stats,     False),
    ('task_stats',      _pd._insert_task_stats,      False),
    ('picker_events',   _pd._insert_picker_events,   False),
    ('picks',           _pd._insert_picks,           False),
    ('bin_placements',  _pd._insert_bin_placements,  True),
    ('bin_evictions',   _pd._insert_bin_evictions,   True),
    ('aisle_metrics',   _pd._insert_aisle_metrics,   False),
    ('work_events',     _pd._insert_work_events,     True),
    ('reorder_queue',   _pd._insert_reorder_queue,   True),
    ('put_queue_state', _pd._insert_put_queue_state, True),
    ('carryover',       _pd._insert_carryover,       True),
    ('yard_trailers',   _pd._insert_yard_trailers,   True),
    ('yard_drains',     _pd._insert_yard_drains,     True),
    ('shift_days',      _pd._insert_shift_days,      True),
    ('free_index',      _pd._insert_free_index,      True),
)

#: The SITE dock's own channel set — the second adapter, which is what makes this a seam
#: rather than one object with a table. `_SiteDock` kept parallel `_yt`/`_yd` accumulators
#: feeding `save_site_inbound`: a second implementation of this concept, one file over.
SITE_CHANNELS: tuple[tuple[str, Callable, bool], ...] = (
    ('yard_trailers',   _pd._insert_yard_trailers,   True),
    ('yard_drains',     _pd._insert_yard_drains,     True),
    ('site_receiving',  _pd._insert_site_receiving,  True),
)


class CheckpointBuffer:
    """Rows waiting for a checkpoint, and the three things anybody does with them.

    `add` accumulates, `pending()` asks EVERY channel rather than one of them, `flush` writes
    an open window, `close` writes at run end whether or not one is open.
    """

    __slots__ = ('_channels', '_rows')

    def __init__(self, channels=CHANNELS) -> None:
        self._channels = tuple(channels)
        self._rows: dict[str, list] = {name: [] for name, _fn, _skip in self._channels}

    # ── accumulate ────────────────────────────────────────────────────────────────────

    def add(self, channel: str, rows) -> None:
        """Extend one channel. An unknown name is a REFUSAL, not a silently dropped row."""
        try:
            self._rows[channel].extend(rows)
        except KeyError:
            raise KeyError(
                f'no checkpoint channel {channel!r}; known channels are '
                f'{[n for n, _f, _s in self._channels]}. A row added to a name with no '
                f'insert is the failure this object exists to make impossible.') from None

    def append(self, channel: str, row) -> None:
        """One row. `add` for the common case of many."""
        self.add(channel, (row,))

    def rows(self, channel: str) -> list:
        """The live list for one channel — for a caller that must read what it accumulated
        (the batch count the checkpoint cadence compares against)."""
        return self._rows[channel]

    # ── ask ───────────────────────────────────────────────────────────────────────────

    def pending(self) -> bool:
        """Is ANYTHING unflushed?

        The old form asked `if pb:` — one buffer as a proxy for all thirteen — and two
        run-end writers had to be lifted out of that guard by hand. This asks every channel.
        """
        return any(self._rows[name] for name, _fn, _skip in self._channels)

    def __len__(self) -> int:
        return sum(len(self._rows[n]) for n, _f, _s in self._channels)

    # ── write ─────────────────────────────────────────────────────────────────────────

    def _write(self, path: str, run_id: int) -> None:
        con = _pd._open_db(path)
        try:
            for name, insert, skip_when_empty in self._channels:
                rows = self._rows[name]
                if skip_when_empty and not rows:
                    continue
                insert(con, run_id, rows)
            con.commit()
        finally:
            con.close()

    def flush(self, path: str, run_id: int) -> None:
        """Write an open window and clear it. A no-op when nothing is pending."""
        if not self.pending():
            return
        self._write(path, run_id)
        self.clear()

    def close(self, path: str, run_id: int) -> None:
        """Run end: ALWAYS write, open window or not.

        This is the design decision (see the module docstring). A caller may have put rows in
        after the last flush that belong to no window at all — the final day's close-out, the
        censored yard tail — and under the old `if pb:` guard those were lost whenever
        `n_batches` divided the checkpoint cadence.
        """
        self._write(path, run_id)
        self.clear()

    def clear(self) -> None:
        for name, _fn, _skip in self._channels:
            self._rows[name].clear()
