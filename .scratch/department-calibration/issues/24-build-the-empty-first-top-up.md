# Build the empty-first top-up

Type: task
Status: open

Graduated from [Let a base-stock top-up reach the shelf](20-let-a-base-stock-top-up-reach-the-shelf.md),
decisions 1-7. AFK build. Skills: `codebase-design` (the put-away fallback chain as one
contract), `schema-maintainer` agent (the `sim_db` vintage and the semantic layer),
`test-developer` for the equivalence and the new behaviour, `code-reviewer`,
`memory-maintainer` to amend `empty-bin-preference-is-structural`. ADR-0003 is the decision
record; do not re-decide.

## Question

Not a decision: the build that makes ADR-0003 true. Put-away fills an empty bin first and
consolidates into the SKU's own bin only when no empty bin fits; the rework is priced to the
receiving crew and recorded.

What lands:

- **The fallback chain** in `Inventory_Manager._stock_per_unit`: when `place_one` returns
  None, try the SKU's own bins (most on-hand first; a unit that does not fit whole fills own bins
  to capacity in that order and sends the remainder on), THEN the repack / singleton rescues,
  THEN `pending`. The own-bin write is the fifth mutation site: `storage.quantity += n`,
  `_current_quantities` incremented, `_bin_sku` unchanged, `_cost_putaway` charged as for any
  placement; `Tests/architecture/test_bin_mutation_sites.py` allowlists it by name.
- **Inbound does the repacking.** When a rescue fires, the receiving crew's clock is charged per
  RESULTING pack at the dock's per-pack unload price (`Inbound/unload.py`, `Dock.charge`), no
  travel term, no new coefficient. The dock's `records` / `recv_seconds` see it.
- **The vintage.** One `sim_db` shape move through the pipeline (`--sync` before, `--accept`
  after, the outgoing id vetted by name): `work_events` event type `repack` (role receive,
  duration, qty, sku, rescued source); a `bin_placement` column for the bin's state at landing
  (empty / occupied); `batch_stats` flows `put_topups`, `recv_repacks`, `recv_repacked_packs`
  and level `free_bins`. `sim_semantics` entries for each; older vintages served by a `dataset`
  override (new columns zero, bin state empty).
- **The audit and the record.** The equilibrium audit reports own-bin share and free-index depth
  per day, reported not judged; the staffing record stamps `f_repack = 0`, provenance
  `assumed`, and the check flags any measured repack against it. Put-away expectation unchanged
  at one placement per top-up.
- **Pick-drain order.** `drain_sku` takes the smallest-quantity bin first, ties by location
  order.
- **Memory.** `empty-bin-preference-is-structural` amended: put-away prefers an empty bin, then
  the SKU's own bin, and the no-meeting rule held as an invariant only while reorder lots were
  pallet-sized.

Done when: a unit that fits no empty bin lands in its SKU's own bin before any rescue fires
(unit test); a run whose free index never exhausts is byte-identical to HEAD (equivalence
test over the store-only path); a forced rescue writes a `repack` work event charged to the
receiving crew and the three `batch_stats` flows count it; `Optimization.runschema.contract
--check`, `test_schema_identity`, `test_column_semantics`, `test_schema_compatibility` and
`test_bin_mutation_sites` are green; the memory is amended; `context/` anchors and the
architecture layer are re-synced by their maintainers.
