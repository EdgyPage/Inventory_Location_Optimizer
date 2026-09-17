# 07 - accumulate here, flush 700 lines away, clear by hand

Type: refactor
Status: needs-triage
Blocked by: 06

## Context

Thirteen bare lists are declared at `strategy_runner.py:1849-1862` (`pb pt pe we pk pm pq pqs cov
yt yd sd fi`), appended to across ~40 scattered sites between `2135` and `2623`, flushed at
`2630-2641` via `save_checkpoint_bundle` with 15 keyword arguments, cleared by four statements
naming all thirteen at `2682-2685`, and flushed again with the same 15 keywords at `2705-2712`.

`Optimization/persistence/Picking_Data.py:2971-3042` is the other end: **16 parameters, and a body
that is 15 lines of `if list: _insert_x(con, run_id, list)`.** The interface is as complex as the
implementation. Its own docstring names the consequence:

> A bundle argument that is accepted and never inserted is this function's characteristic failure:
> `work_events` was one for a while, and the reconciliation that was supposed to catch it passed
> over 68 databases holding zero rows.

**`if pb:` is a proxy guard that has already been escaped twice.** It means "is there an unflushed
window", inferred from one of thirteen buffers. Two run-end writers had to be lifted out of it by
hand, each with a paragraph explaining why (`2713-2731`) -- the censored yard tail "is deliberately
OUTSIDE the `if pb:` above... Losing them on a round batch count would report `lifo`'s fee as
CLIPPED rather than concentrated, which inverts the signal the arm exists to produce." Memory
`run-end-writers-miss-the-final-flush` records the general form: the tail does not fire when
`n_batches` divides the checkpoint cadence.

**The test locality is inverted.** Eleven individual `save_*` wrappers -- `save_picks`,
`save_task_stats`, `save_aisle_metrics`, `save_work_events`, `save_bin_placements`,
`save_bin_evictions`, `save_reorder_queue`, `save_put_queue_state`, `save_carryover`,
`save_batch_stats`, `save_picker_events` -- have **zero production callers**. Production goes
exclusively through the bundle. So the tested path is not the production path, while the real risk
surface (15 `if <arg>:` early-skips) is thinly covered and `yard_drains` is never read back at all.
`save_picker_events` has no callers of any kind.

`_SiteDock` keeps its own parallel `_yt`/`_yd` accumulators (`strategy_runner.py:887-888`, extended
at `938-963`) feeding `save_site_inbound` (`:994`) -- a second implementation of the same concept.

## What to build

`Optimization/persistence/` gains a `CheckpointBuffer` owning all three halves:

```
buf.add('carryover', rows)
buf.pending() -> bool        # asks EVERY channel
buf.flush(db_path, run_id)   # drain bin_rec, insert, clear, return seconds
buf.close(db_path, run_id)   # run end: ALWAYS writes
```

The seam is the channel table -- `(name, insert_fn, skip_when_empty)` -- iterated in declared order
so rowids and the run digest hold.

**`close()` is the design decision, stated the way `SectionTimers`' is: a run-end close always
writes, open window or not.** The two hoisted writers come back inside and their two explanatory
paragraphs stop needing to exist.

`_SiteDock`'s accumulators become a second `CheckpointBuffer` with a different channel set -- two
adapters, so the seam is real.

Move `_shift_close_out` (`1269-1309`) to module level beside `ShiftLedger`; it is the last thing
standing between `ShiftLedger` and a fully unit-testable day close.

Delete the 11 orphan `save_*` wrappers and the tests that only reach them.

## Verification

- One parameterised test: for every declared channel, add one row, flush, read the file back,
  assert it is there. Covers all 15 including `yard_drains`, and is a non-vacuity ratchet -- a
  channel added with no insert becomes a failure, not a silent zero.
- A test that a `close()` with an empty window still writes the last day and the censored yard
  tail. That regression is today only observable in `test_standing_yard_e2e.py` and only on a
  batch count that does not divide the cadence.
- `run_digest.py` byte-identity: rowids and insert order must not move.
- `Picking_Data.py` carries `.scratch/architecture-drift/issues/07`. Attribute against baseline.
- Gates 1, 2, 10.
