# 09 - A dispatch order from a reference run's runtime_metrics

Type: research
Status: done

Job.weight is the seam. On a wide, shallow matrix the 3 h units submitted last extend the tail; an estimate per (pair, config, channel, arm) from a reference run's runtime_metrics.db would put them first. Measure the tail on a campaign-shaped toy before building it: the wall is already the worker-hours plus one unit.

## Measured first, then built (2026-09-19)

The measurement the issue asked for, on the ten-cell shape:

| dispatch order                              | matrix wall |
|---|---|
| submission order (the pool bare)            | 5.57 h |
| longest-first jobs                          | 5.37 h |
| ...and heavy cells set up first             | 5.17 h |
| the floor (worker-hours + the longest unit) | 3.40 h |

So the two levers together are worth ~7%, and **ordering the CELLS is the bigger half** —
which is only legal because `cells.cell_scope` made setup order result-neutral.

`Optimization/simdriver/pacing.py` is the build, behind `--pace-from <run root>`. OPT-IN by
decision: a weight is a guess taken from a different run, and a mismatched reference makes
the order worse than the spec order a reader is looking at.

Threaded keyword-only through `sim_jobs` AND through the driver's `_rebuild`, so a run that
survives a broken pool does not silently revert to submission order for the half it has
left.

The two arithmetic traps, both pinned in `Tests/unit/test_pacing.py`:

- `total_s` is the UNIT loop's wall recorded on BOTH leaves of a coupled unit, so a unit is
  the `max` over its rows. A sum rates every coupled unit at 2x and floats them ahead of
  single leaves that really are longer.
- A resumed unit has less work left than the row describes, so the estimate scales by
  `(n_batches - start_i) / n_batches`. Without it a resume dispatches its nearly-finished
  arms first, which is the exact inverse of the point.

Identity comes from the PAYLOAD (`group_keys` / `arm_keys`), never from slicing a uid whose
four slots mean different things on a leaf unit and a coupled one; the channel is `'store'`
on a store-only layout, never `''`.

The ladder ends on the CELL, deliberately: an arm's cost varies enormously with the arm
(39-59x priced against `fifo`) and barely at all with the cell — the near-invariance phase
2's exact-tie ranking was showing — so the cell is the least informative key to match on.

Landed with it: `cell_pos` now carries this cell's position IN THE SPEC. It was the index
into the todo list, so a resume that skipped two cells labelled the third one `1/8`, a
progress line disagreeing with the descriptor and with the log's own CELL banner.

## Not done here

The ~6 min per-cell setup that caps every ordering gain. At 60 s a cell the same levers are
worth 17.8% rather than 7.2%, and cutting it reopens cross-cell asset reuse, which was
deferred on its own merits.
