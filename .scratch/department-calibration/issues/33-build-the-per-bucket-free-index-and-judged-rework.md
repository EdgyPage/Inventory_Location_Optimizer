# Build the per-bucket free index, the tier spill and the judged rework clause

Type: task
Status: open

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
