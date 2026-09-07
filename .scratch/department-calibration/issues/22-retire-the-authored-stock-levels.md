# Retire the authored stock levels

Type: task
Status: open

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
