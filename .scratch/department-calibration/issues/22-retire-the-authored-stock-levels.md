# Retire the authored stock levels

Type: task
Status: resolved

Graduated from [Field the floor](19-field-the-floor.md), decisions 5-8. AFK build. Skills:
`codebase-design` (the catalogue's contract), `schema-maintainer` agent (the `inventory_db`
vintage and the profiles tree), `test-developer` for the goldens. Memory
`stock-plan-overrides-packing` describes the mechanism this ticket removes and must be retired
with it (`memory-maintainer`).

## Question

Not a decision: the build that makes decision 5 true. The catalogue stops carrying
`equilibrium_qty`, `reorder_point` and `stock_plan`; stock is a run's declaration derived at
setup in every mode.

What lands:

- **Schema.** The three columns leave `cartons` as a new `inventory_db` vintage through the
  pipeline (`--sync` before, `--accept` after, the outgoing id vetted by name); the two older
  vintages are served through a `dataset` override that ignores the columns (the `line_family`
  precedent). `inventory_semantics` follows. `creation_plan` stays. `Order.build` no longer
  takes levels; the `Order` slots stay (the run's planned inventory fills them).
- **Generator.** `EQUILIBRIUM_COVERAGE_BATCHES`, `REORDER_SAFETY_BATCHES`, the level formulas
  in `generate_inventory.py` (both paths) and `generate_mixed_profile.py`, and stock-plan
  authoring are removed. The pallet/singleton packing becomes a setup fact (the requirement's
  packing, ticket 23). The profiles-tree schema (`Schema.profile_tree --check`) and its golden
  are re-captured.
- **Derivation in every mode.** `sim_assets.build_shared_assets` runs `era_coverage.fixed_point`
  whenever it samples, not only under `era_on()`; the day it uses flag-off is the reporting
  frame. Round 0 no longer plans "the catalogue's own levels": it seeds n from the analytic
  batch content and the record's `catalogue` block (implied coverage) is gone. The
  `coverage_days` / `safety_days` / `floor_lines` knobs are read in every mode.
- **Readers.** Every consumer that read a catalogue level (`sim_assets` avg logs,
  `workunits` stats, `Warehouse_Data` averages, the `catalog/` evaluations, diagnostics)
  reads the run's planned inventory or is dropped; none reads the catalogue.
- **Tests.** Thirty test files touch levels; the ones that author them build orders through
  the derivation instead. Placement and profile-tree goldens are re-captured ONCE with the
  break named in the commit (the ADR-0001 precedent).

Done when: a generated catalogue has no level columns; a flag-off run and an era run on the
same pair derive identical levels at the same declared coverage; `Schema.profile_tree --check`,
`Optimization.runschema.contract --check`, `test_schema_identity`, `test_column_semantics` and
`test_schema_compatibility` are green; the memory is retired; ADR-0002's consequences section
matches what landed.

## Answer

**LANDED 2026-09-07.** The catalogue carries no stock levels; a level is a run's declaration,
made in every mode and recorded by the run that made it.

**The declaration is a TABLE, not four dropped columns.** `cartons` lost `equilibrium_qty`,
`reorder_point` and `stock_plan` -- and `pipeline_qty` with them, which the ticket did not name:
it is the same kind of fact (the lead pipeline the coverage rescaling stamps, written to a planned
inventory only), and leaving it behind would have kept exactly the always-NULL column decision 7
rejected. A new `stock_levels` table (`sku` + those four) holds the declaration. A generated
catalogue leaves it EMPTY, and that emptiness is the contract -- no row means no declaration
(`Order.stock_declared()` is False) -- while both files stay ONE family and one shape, only their
rows differing. `inventory_db` moved `025f4b1548a9` -> `4536857cb860`; the outgoing vintage stays
vetted and its `stock_levels` override reads the four columns out of ITS `cartons`, so an archived
planned inventory still yields the levels its run fielded, under the same logical names, with no
consumer branching on a version. Verified on a reconstructed pre-split file.

**`Order.declare_stock` is the one mutation site** (with `stock_declared()`); `Order.build` takes
no level; `Order.reorder()` carries a declaration when there is one and no longer invents `1/1`
when there is not.

**The dangerous half was the DEFAULT, not the drop.** `inventory_common._equilibrium_qty` answered
1 for an undeclared order, and the warehouse bin count is demand-derived from the levels on EVERY
path -- `sample=False` skips only the SKU sampling, never `bucket_requirements`. An undeclared
catalogue would therefore have sized every bucket for one unit per SKU and built a warehouse an
order of magnitude too small, silently. It now raises `UndeclaredStock`, and
`inventory_reorder` raises the same way where a missing reorder point used to mean "this SKU never
replenishes, and nothing says so". New memory: `warehouse-size-comes-from-the-levels`.

**Three consequences the ticket did not anticipate, each found by something breaking loudly:**

1. **A rebuild must re-declare.** `run_analysis` and `run_map_precompute` re-plan a finished run's
   warehouse from its ORIGINAL catalogue. They now pass that run's own
   `staffing.calibration[<pair>].coverage` to `build_shared_assets(coverage_record=...)`, which
   applies ONE `rescale_section` at the recorded `lines_per_day`
   (`era_coverage.declare_from_record`) -- exact, and a second rather than the eight minutes a
   re-run of the fixed point would cost, which would also have re-derived from this checkout's
   geometry instead of the one the run fielded. Without a record it REFUSES; `run_analysis` no
   longer lets that become a silent "Config stage: 0 job(s)".
2. **So the declaration must be RECORDED in every mode** (`workunits._record_coverage`), including
   at the multi-cell FREEZE (`scenario.py`), which is where such a run declares -- every later cell
   loads the frozen inventory and declares nothing. Preflight's 2-cell canary is what caught this:
   a complete run tree and an entirely empty analysis one.
3. **Round 0 of the fixed point had to stop planning.** There is no authored level to plan, so it
   is now an ANALYTIC SEED (`era_coverage.seed_lines`): the section priced at its analytic seconds
   per unit, no geometry, no travel term. That starts `n` too high, which is the direction
   `next_guess`'s bracketing needs. The record's `catalogue` block died with the levels it read and
   `coverage.implied_coverage` is deleted.

**One planner contract means one pipeline contract**: `load_run_inventory` lost its `era` parameter
and no longer clears the stamp flag-off.

**Verification.** A flag-off run and an era run on the same pair derive IDENTICAL levels and the
identical warehouse (120 SKUs, 66 aisles / 71,700 bins both ways) -- the ticket's own acceptance
claim, measured. A rebuild from the record reproduces the run's warehouse exactly; a rebuild
without one refuses. Archived vintages still load their levels. `Tests/unit` 1,783 passed;
`Tests/integration` + `Tests/e2e` green except three e2e harnesses that hand-assemble a run tree,
now fixed to record the declaration through the production function. Gates green: `verify_context`,
`verify_architecture`, `verify_site --fast`, `runschema.contract --check`, `profile_tree --check`,
`verify_memory`, `path_guard`, `docref_guard`. THREE FAILURES ARE PRE-EXISTING and were proved so
by running them at HEAD in a clean worktree: `test_the_evaluation_attribution_matches_the_registry_both_ways`
(`throughput.audit` unattributed), `test_every_loader_that_reads_a_conditional_table_is_declared`
(`_shift_day_select`), and `test_every_consumer_that_declares_requirements_is_validated_here`,
which fails only because a stale git worktree under `.claude/worktrees/` is scanned as source.

**Retired**: the memory `stock-plan-overrides-packing` (its generation-time half was the mechanism
this ticket removed; the packing and split-penalty halves survive, re-aimed).
