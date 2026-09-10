# Build the per-bucket free index, the tier spill and the judged rework clause

Type: task
Status: resolved

Graduated 2026-09-10 from
[Band the own-bin share and the free-index depth](32-band-the-own-bin-share-and-free-index.md),
decisions 1-4, 6 and 8. AFK build. Skills: `codebase-design` (the three instruments as one
contract), `schema-maintainer` agent (the `sim_db` vintage and the semantic layer),
`test-developer` for the sabotage tests, `code-reviewer`. ADR-0003 and 32 are the decision
record; do not re-decide.

## Question

Not a decision: the build that makes 32 true. What lands:

- **The tier spill is counted.** `_candidates_raw` already returns the tier it chose; when that
  key's size differs from the unit's own, the placement is a spill. A per-batch flow on
  `batch_stats` (`put_spills`, reset like `put_topups`) and the landing tier beside the unit's
  own on `bin_placement` (so a spill is auditable per row). Singletons cannot spill.
- **The free index per bucket.** A narrow table keyed by BinKey per batch -- `(run_id, batch_id,
  handling, category, size, unit, free)` -- written where `free_bins` is, straight off `_index`.
  It counts only the leaf's own section by construction. `batch_stats.free_bins` keeps its name
  and its whole-geometry meaning; its `sim_semantics` entry says so and names the artefact
  (each leaf reads the other channel's section as free).
- **The vintage.** One `sim_db` shape move through the pipeline (`--sync` before, `--accept`
  after, the outgoing id vetted by name); older vintages served as unknown (None), never as zero
  -- the same rule `_rework_clause` already applies to `free_bins`.
- **The clause judges three events at zero, no knob.** `put_topups`, `put_spills` and the repack
  count each fail `rework` with a sizing message naming the bucket(s) where the free index was
  dry. The per-bucket depth is REPORTED: window minimum and mean per bucket against the setup
  `free` the record already stamps in `coverage.final.<ch>.fielded.buckets`, plus the drawdown
  over the window. The docstring names the readings the decision was drawn from: the corrected
  2026-09-09 numbers in 32's answer (18.0% / 15.2% free at setup, -6,763 / -18,279 over 40 days),
  not the artefact.
- **Every consumer that assembles an explicit column dict** gets the new columns:
  `Performance_Evaluations/common/frames._bdf` (the vacuity trap 24 fell into), the audit's
  table, `Diagnostics/equilibrium_report.py` (a per-bucket table per leaf).
- **Sabotage tests** prove each new term can fail: a forced spill fails the clause through the
  audit's own frame; a forced top-up fails it; a bucket driven dry shows in the per-bucket table
  while the leaf total stays high (the reading the leaf total could not give).

## Done when

- A run with a deep index is byte-identical on every existing surface (the counters are
  observers); a forced spill writes the flow, the row stamp and FAILS the clause via the audit;
  the per-bucket table reproduces the record's setup `free` on batch 0 for every bucket of the
  2026-09-09 run's pair.
- `python -m Optimization.runschema.contract --check`, `test_schema_identity`,
  `test_column_semantics`, `test_schema_compatibility` green; nine gates green; `context/` and
  the architecture layer re-synced by their maintainers.

## Answer

**LANDED 2026-09-10.** Every item on the list is built, and the done-when is met on a real run.

**What landed, against the list.**

- **The tier spill is counted** at the ONE commit point, `_execute_placement`: a landing bin
  whose size tier differs from the unit's own (handling, category and regime already agree
  by construction and by the assert above it), so every path that places -- the ranked wave,
  the per-unit drain, a reslot, initial stocking -- is covered; a falsy unit tier is not a
  spill and singletons never spill.  `batch_stats.put_spills` (a FLOW, reset with the other
  three by `snapshot_putaway_rework`, now a 4-tuple) and the tier pair
  `bin_placement.unit_size` / `bin_size` (NULL `unit_size` on a top-up row: the rung hands
  over `n` items, not the unit they were cut from, and a top-up cannot spill).
- **The free index per bucket**: `Inventory_Manager.free_bin_depth_by_bucket` (every key
  `_index` holds, a dry bucket at 0, sorted) is snapshotted in the runner at the same instant
  as `free_bins`, above the skip guard, and rides the checkpoint bundle into the `free_index`
  table `(run_id, batch_id, handling, category, size, unit, free)` -- ~63 rows per batch, the
  four coordinates the record's `fielded.buckets` stamps its setup `free` under.  The leaf's
  section is the rows in its regime; `batch_stats.free_bins` keeps its name and its
  whole-geometry meaning, and its `sim_semantics` note names the artefact.
- **The vintage**: `sim_db` `02a78953886c` -> `b87cfbb8d041` through the pipeline (`--sync`,
  DDL, `--accept`, the window named); `free_index_frame` named query, `load_free_index`
  negotiating to `[]`, `CONDITIONAL_READS`, three semantic entries; the run-tree shape is
  unchanged (preflight canaries, `5c9bc35db55b`).
- **The clause judges three events at zero, no knob**: `put_spills` and `put_topups` fail
  `rework` regardless of whether the record carries an `f_repack` (decision 4: the zero is the
  ADR's claim, hard-coded), the repack as before; each reason names the bucket(s) whose
  per-bucket depth read 0 in the window, or says the depth is unrecorded on that vintage.
  The depth is REPORTED per bucket against `setup_free` (new on `expectations_for`, off
  `coverage.final.<ch>.fielded.buckets`, keyed `handling/category/size/unit`): first, last,
  min, mean, drawdown (first - last) and the dry batches; only the record's buckets when
  there is a record (the other section is exactly the artefact).  `summarize` prints the
  driest bucket and the section drawdown; the audit's inspection table gains five rows per
  arm (three events, the section's free index, the driest bucket); `equilibrium_report`
  prints a per-bucket table per arm (`--no-buckets` to silence).  The docstring names the
  corrected 2026-09-09 readings the decision was drawn from.
- **Every explicit column dict**: `frames._bdf` (`put_spills`, None-filled), the new
  `frames._fidf` / `requests.free_index_frame` / `EvalContext.free_index_df`, and the audit's
  `_verdict_for` passing `free_rows` into `check_rows`.
- **Sabotage tests** (`Tests/unit/test_free_index_by_bucket.py`, 23): a forced spill is
  counted, stamped on its row (`small` into `medium`) and fails the clause both through the
  frames and through `audit._verdict_for` over a real file; a forced top-up fails it with no
  `f_repack`; a bucket driven dry is named while the whole-geometry total sits at 1,050; the
  per-bucket rows partition the total and keep the dry bucket at 0; the bundle writes the
  file; two faked vintages read the new columns as None.

**THE CHECK** (`comparison_20260910_100637`: the 2026-09-09 command on the reference pair,
`--n-batches 2`, both channels, both fifo arms):

- **Batch 0 reproduces the record's setup `free` on EVERY bucket of the pair**: 60 store
  buckets summing 220,817 and 3 fulfillment buckets summing 187,793, 0 mismatches (the 63
  rows per batch carry both sections; each leaf's own section is the record's 60 / 3).
- **Byte-identical on every existing surface**: batches 0-1 of `batch_stats` (items,
  duration, makespan, placements, sigma, `free_bins`), `picks` and `bin_placement` are
  IDENTICAL to `comparison_20260909_204522` on all four leaves -- the counters are observers.
- Spills 0 / 0 and top-ups 0 / 0 on every leaf, so the planner fields the declaration without
  a single tier spill at setup; 1,010,139 (store) / 1,059,249 (fulfillment) placement rows
  with the tier pair agreeing on all of them, none NULL.
- The per-bucket table over two days already shows 32's mechanism where the total could not:
  the store section draws down +121 with `conveyable/food/small` -157 and
  `conveyable/food/singleton` **+130** (a drained remnant returns its bin), fulfillment +561
  across `ff_small` / `ff_medium` / `ff_large` (-102 / -291 / -168); driest store bucket
  `non-conveyable/electronic/medium` at 378 of 379.  `rework=ok` on every leaf; the audit
  rendered both leaves with the new rows.

**THE ONE THING THE TICKET (AND 24 BEFORE IT) GOT WRONG: "older vintages served as
unknown (None) through the optional-fill".**  The optional fill only answers THROUGH AN
OVERRIDE.  `dataset.query` fills a missing optional column after the SQL runs, but the
canonical SQL names every column of `_BATCH_COLS`, so a file lacking one is `UnsupportedQuery`,
`_query_rows` returns None, and `load_batch_stats` falls to its frozen legacy body -- whose
answer is the DATACLASS default, 0.  So `free_bins` read **0, not None, on every
`798778f4fae1` file** from 2026-09-08 until today, exactly the "exhausted index" reading the
docstrings promised never to give; the faked-vintage test proved it before the fix.  Fixed
with the pipeline's own pattern: `_batch_frame_sql` builds the select list minus the absent
columns and two `batch_frame` overrides are registered, for `02a78953886c` (minus
`put_spills`) and `798778f4fae1` (minus the five ADR-0003 columns); the legacy body now sets
`free_bins` / `put_spills` to None explicitly (it serves only unvetted archive vintages, none
of which has either); `BATCH_UNKNOWN_ON_OLDER_VINTAGES` names the two so the type gate
(`test_written_columns_are_readable`, already RED at HEAD on `free_bins`) asserts the None
instead of the column's INTEGER.  Memory `optional-fill-only-answers-through-an-override`;
the schema-maintainer procedure gained the rule.

**From the two reviews** (`code-reviewer`, `test-reviewer`; nothing critical):
batch 0 carries initial stocking's spills and top-ups and is JUDGED -- deliberate and now
documented at both sites (a spill while fielding the declaration is the planner's promise
broken one bucket early; `pop_churn` is drained before the loop, this is not); an EMPTY
`fielded.buckets` is treated like an absent one so the run's own rows still show; the audit's
call site and the runner seam gained their own tests (a hand-built frame proves neither); the
free-index row was split in two after the single row measurably overflowed two columns on
the 60-bucket leaf.  Noted, not done: `tables/tidy._NON_METRIC` melts `put_spills` like
`put_topups` before it, a pre-existing pattern for a published table to decide.

**Verification.** 1,882 unit (before the module) + 23 new; 426 integration + e2e, 2 skipped;
preflight canaries end to end (tree unchanged, fingerprint refreshed); contract, profile
tree, context, memory, path and docref guards green; `test_schema_identity`,
`test_column_semantics`, `test_written_columns_are_readable` green.  Three PRE-EXISTING
failures, verified at HEAD and left alone: `test_schema_compatibility`'s consumer scan walks
two stale `.claude/worktrees/` copies and its conditional-reader scan flags
`_shift_day_select`'s docstring (24 recorded both), and `Tests/calltree`'s minlabor oracle
(memory `hand-run-test-tiers-rot-silently`).  `context/` and the architecture layer:
maintainers run after the commit, output left in the tree for review.
