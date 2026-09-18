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

__all__ = ['CheckpointBuffer', 'CHANNELS', 'SITE_CHANNELS', 'write_rows']


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

    __slots__ = ('_channels', '_rows', '_con', '_path')

    def __init__(self, channels=CHANNELS) -> None:
        self._channels = tuple(channels)
        self._rows: dict[str, list] = {name: [] for name, _fn, _skip in self._channels}
        # ONE CONNECTION PER ARM, opened on the first write and held until `close`.  It used to
        # be one per flush, and a WAL database's close FOLDS the whole write-ahead log into the
        # main file and deletes it -- so the old shape paid a full fold every checkpoint, which
        # is the very cost `_open_db`'s docstring refuses to pay deliberately.  Measured on 12
        # concurrent arms: holding it cut the write half from 11.1s to 5.6s.
        self._con = None
        self._path: str | None = None

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

    def _write(self, path: str, run_id: int) -> dict:
        """Write every channel in one connection and one commit. Returns the CENSUS.

        The census is `{channel: n_rows}` for the channels that actually carried rows, and it
        exists because `save_s` could not be read as a cost. Eight arms of one toy run wrote
        166,078-166,278 rows each -- a 1.00x spread -- while their `save_s` varied 2.8x, which
        says the section is cost-PER-ROW and not volume. Nothing recorded the denominator, so
        that had to be reconstructed by hand from the databases afterwards. Now it is emitted.

        `len(rows)` is read BEFORE the insert: `executemany` consumes its argument, and one
        channel already passes a generator for a documented memory reason.
        """
        census: dict[str, int] = {}
        con = self._connection(path)
        try:
            for name, insert, skip_when_empty in self._channels:
                rows = self._rows[name]
                if skip_when_empty and not rows:
                    continue
                if rows:
                    census[name] = len(rows)
                insert(con, run_id, rows)
            con.commit()
        except BaseException:
            # A failed flush must not leave a half-open transaction on a connection the NEXT
            # flush would reuse.  Dropping it is what the old per-flush `finally: close()` did
            # for free, and the reuse is what makes it something to do on purpose.
            self._release()
            raise
        return census

    def _connection(self, path: str):
        """This buffer's held connection, opened on demand."""
        if self._con is not None and self._path == path:
            return self._con
        self._release()                      # a different file: never write down two at once
        self._con = _pd._open_db(path)
        self._path = path
        return self._con

    def _release(self) -> None:
        """Close the held connection if there is one.  Idempotent."""
        if self._con is not None:
            con, self._con, self._path = self._con, None, None
            con.close()

    def flush(self, path: str, run_id: int) -> dict:
        """Write an open window and clear it. A no-op when nothing is pending.

        Returns `_write`'s census, or `{}` when there was nothing to write -- and an empty
        census is the honest answer for a no-op flush, not a zero.
        """
        if not self.pending():
            return {}
        census = self._write(path, run_id)
        self.clear()
        return census

    def close(self, path: str, run_id: int) -> dict:
        """Run end: ALWAYS write, open window or not.

        This is the design decision (see the module docstring). A caller may have put rows in
        after the last flush that belong to no window at all — the final day's close-out, the
        censored yard tail — and under the old `if pb:` guard those were lost whenever
        `n_batches` divided the checkpoint cadence.
        """
        try:
            census = self._write(path, run_id)
            self.clear()
        finally:
            # RUN END IS WHERE THE CONNECTION GOES.  A plain close folds the WAL back and
            # removes the sidecars, which is exactly what a finished arm wants -- and it must
            # happen before `build_run_indices` opens its own connection.
            self._release()
        return census

    def clear(self) -> None:
        for name, _fn, _skip in self._channels:
            self._rows[name].clear()


def write_rows(path: str, run_id: int, *, channels=CHANNELS, **rows) -> None:
    """Write rows a caller ALREADY HAS, in one connection and one commit. No window.

    Fifteen named writers stood here until ticket 07 -- eleven `save_<table>` wrappers with
    zero production callers, plus `save_checkpoint_bundle`, `save_site_inbound`,
    `save_shift_days` and `save_yard_trailers` -- and every one of them was the same four
    lines with its channel name baked into its own name:

        con = _open_db(path); _insert_<table>(con, run_id, rows); con.commit(); con.close()

    Here the channel is DATA. That is the difference that matters, not the line count: the
    bundle's characteristic failure was an argument ACCEPTED AND NEVER INSERTED (`work_events`
    was one over 68 databases holding zero rows), and a 16-parameter signature is what made
    that expressible. An unknown name raises here, because the only place a channel exists is
    the table it is inserted from.

    The last four had docstrings arguing at length that they must be separate from the bundle
    -- "the bundle only fires when a batch window is unflushed, and a run whose batch count
    divides evenly by its checkpoint interval has no such window". That was always an argument
    about `if pb:`, and `close()` writing unconditionally is what retired it.

    `None` is SKIPPED rather than refused, which is what the bundle's seven optional keywords
    did: a caller that predates a channel passes nothing and writes no rows.
    """
    buf = CheckpointBuffer(channels)
    for name, r in rows.items():
        if r is not None:
            buf.add(name, r)
    buf.close(path, run_id)
