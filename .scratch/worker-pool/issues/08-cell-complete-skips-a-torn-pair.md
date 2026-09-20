# 08 - _cell_complete answers complete on a torn coupled pair

Type: task
Status: done

cells._cell_complete returns True on ONE sim_meta.json per pair, so a resumed multi-cell run skips a cell whose coupled pair is torn (one leaf finalized, one not) before _reconcile_coupled_unit could repair it. Found while retargeting Tests/e2e/test_coupled_resume_e2e.py, which drives scenario._run_cells directly and never meets the skip. Fix: a cell is complete when every LEAF the layout declares is complete (workunits._leaf_is_complete over the pair's channel-run dirs), and a test that plants a torn pair under a two-cell tree and resumes through _run_whatif_matrix.

## Resolution (2026-09-19)

`_cell_complete` is deleted. `runschema.RunTree.cell_is_complete(cell, pairs)` replaces it,
in the resolver because that layer owns tree enumeration and because
`scripts/archive_cells.py` had already grown a second, stricter copy of the same predicate
— two answers to one question, with the weaker one deciding what a resume could skip.

Four things it does that the old one did not:

- **Counts a pair's leaves as a UNION, not a product.** Store configs make
  `<store_cfg>/store`, fulfillment configs make `<ff_cfg>/fulfillment`. A cross-product
  rule would make every mixed cell permanently incomplete, which kills the skip and turns
  every resume into a full re-run.
- **Reads mixedness PER PAIR, from disk.** The wanted count comes from the `config_json`
  records the setup pass wrote, because the descriptor's channel list is derived from
  CONFIG before any catalogue is read and a run really can be mixed for one inventory pair
  and store-only for another.
- **Refuses a half-written setup rather than shrinking the target.** If a pair's config
  count is neither the store-only shape nor the mixed one, setup itself was interrupted.
- **Refuses on an in-flight file.** `resume_pkl` / `checkpoint_pkl`, named through the
  contract, so a leaf killed inside its finalize window cannot read finished.

The skip decision is now `scenario._cells_to_run`, a named function so the test drives the
production selector rather than a copy of its rule. It is GUARDED: a root with no contract
descriptor skips nothing.

Beside it, a contradiction guard after the pool: a cell that entered `todo`, planned zero
units and still fails the predicate is recorded `unfinished[cell] = [('unplanned',)]`. The
pool reports no failure when it was given no work, so without this a run exits 0 for ever,
resume after resume, with the cell never completing.

`archive_cells.cell_state` delegates, and `archivable` stopped reasoning from cell
ordering — the flat pool made every cell overlap, so "a later cell started" is no longer
evidence that an earlier one is finished. The in-flight probe is the direct evidence that
replaced it.

Tests: `Tests/unit/test_cell_is_complete.py` (14) — the torn mixed pair, the finalize
window, a mixed pair beside a store-only one in one cell, the half-written setup, the
delegation, the ordering premise, and a genuinely finished cell IS complete, without which
every other assertion is satisfied by a constant False.
