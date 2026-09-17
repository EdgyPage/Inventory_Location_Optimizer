# 07 - accumulate here, flush 700 lines away, clear by hand

Type: refactor
Status: resolved
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


---

## RESOLVED 2026-09-17  (34fbc392, 323a256b)

Two commits, because they carry different evidence: the first changes the production write
path and is byte-identity verified, the second changes no call site and is not.

### What landed

| | was | now |
|---|---|---|
| accumulators in `strategy_runner` | 13 bare lists | 1 `CheckpointBuffer`, 13 views onto it |
| the flush | 15 keywords + 4 clear statements naming all 13 | `buf.flush(path, run_id)` |
| the run-end write | `if pb:` + 2 writers hoisted out of it by hand | `buf.close(...)`, unconditional |
| the site dock | 3 parallel lists + `save_site_inbound` | the same object over `SITE_CHANNELS` |
| `Picking_Data` writers | 15 named `save_*` | `write_rows(path, run_id, **rows)` |
| `Picking_Data` | -- | **279 lines shorter** |

`close()` is the decision, stated the way `SectionTimers`' is: **a run-end close always
writes**. The proxy guard it replaced had been escaped twice, each escape leaving a paragraph
behind explaining why its writer sat OUTSIDE the guard. Both writers came back inside and both
paragraphs went with the guard -- they were only ever describing the guard.

`write_rows` is the seam's second face. The point is not the deleted lines, it is that the
channel is DATA: the bundle's characteristic failure (an argument accepted and never inserted)
needed a 16-parameter signature to be expressible, and an unknown channel raises now.

### THE DEFECT -- the CALLTREE's `t_save` has been measuring nothing since at least 2026-08-18

**Scope this precisely, because two instruments share the name.** `runtime_metrics`' `save_s`
is a wall-clock stopwatch in `strategy_runner` (`asm.timers.add('save', ...)`), it is alive,
and it is where every published saving number comes from -- the ~31% section share and the
8.72 -> 4.87 s/arm of memory `calltree-framework-first-findings` round 2 are both its. What is
dead is the CALLTREE's `SECTION_MAP` attribution, which is a different instrument answering a
different question (which FUNCTIONS the seconds went to). That split is why nobody noticed: the
name looked healthy in the instrument people read.

Eight of `SECTION_MAP`'s nine `t_save` anchors were the `save_<table>` wrappers this ticket
names as having zero production callers. Production went through `save_checkpoint_bundle`,
which was **never anchored at all**. The ninth, `save_worker_checkpoint`, fires only at a
checkpoint boundary a short capture never reaches.

    capture                                 t_save
    capture__legacy-a   2026-08-18        0.000000
    capture__legacy-b   2026-08-18        0.000000
    capture__legacy-c   2026-08-18        0.000000
    capture__seed-42    2026-09-14        0.000000
    capture__cfg-none   2026-09-14        0.000000
    capture__inbound    2026-09-14        0.000000

Every one -- and `capture__seed-42`'s own run had a live `save_s`. `test_calltree_anchors`
could not catch it: it resolves every anchor NAME, and
every name resolved -- the fourth instance in this effort of memory
`symbol-table-relationship-not-verified-by-symbols`. Nine anchors become two, pointed at
`CheckpointBuffer._write`, plus a ratchet that writes through the production path and asserts
the section moved (**0.026052 s** on a 50-row close, against 0.000000 before). That the fix
works at all is downstream of the decision above: `close()` writing unconditionally is what
lets a capture that never reaches a checkpoint measure the section.

### TWO CORRECTIONS to this ticket

1. **"Move `_shift_close_out` to module level beside `ShiftLedger`."** `shift_ledger.py`'s own
   docstring has a section refusing exactly that -- *"this object holds the state that function
   is CALLED WITH; it makes no judgement about a day, and moving the judgement here would put
   `equilibrium` behind a second door."* The testability half of the ask was right and is
   served: module level in `strategy_runner`, with its two closed-over names (`_release`,
   `log`) as explicit keyword parameters. `Tests/unit/test_shift_ledger.py` now drives the
   whole day boundary with a stub, including the claim that `advance_to`'s returned tuple is
   the close-out's parameter order -- a statement about two functions in two modules that
   nothing had ever checked.

2. **"Delete the 11 orphan wrappers and the tests that only reach them."** There were no such
   tests. Every one of the ~20 call sites uses a wrapper as a DB FIXTURE for something else
   (a loader, an analysis stage, a reconciler), which is why they looked orphaned from
   production and were not dead. Deleting the wrappers therefore meant rewriting call sites,
   not deleting tests -- and `write_rows` is what makes that rewrite a rename rather than
   surgery.

### Verification

| check | result |
|---|---|
| `Tests/unit` + `Tests/integration -k "not gpu"` | 3,188 passed / 2 skipped |
| `Tests/e2e` | 58 passed / 1 skipped (12m11s), incl. `test_standing_yard_e2e` |
| toy run vs baseline, `run_digest.py` | **IDENTICAL**, 136 arms (34fbc392) |
| gates 1-5, 7-10 | green |
| gate 6 (profile-tree) | **red at HEAD, unrelated** -- all nine shape sources byte-identical to HEAD, last touched 2026-09-11 |

**What the digest does NOT cover, and it is worth writing down.** The tiny profile writes
128,044 `carryover` and 51,408 `free_index` rows but **zero** `shift_days`, `yard_trailers`
and `site_receiving`. So IDENTICAL covers the leaf buffer and reaches neither the era nor the
site dock; those rest on `test_era_wiring`, `test_shift_ledger`, `test_site_receiving_totals`,
`test_site_analysis_stage` and `test_standing_yard_e2e`. A digest is an instrument with a
reach, not a proof of a whole refactor.

Gate 5 (preflight) went red on the `strategy_runner` import edit and was re-proved with the
canaries rather than waved through: tree shape UNCHANGED, schema `341e1422457c` still valid.

### What this unblocks

Ticket 14.
