# Stamp the line distribution on the SKU

Type: task
Status: resolved

Graduated from [Choose the coverage floor](15-choose-the-coverage-floor.md), decision 10 (the
user's amendment): every distribution-dependent number is read off a law stamped on the SKU,
never re-derived by a consumer. AFK build, flag-off byte-identical. Skills: `codebase-design`
(the object is a deep module with five readers); the `schema-maintainer` agent owns the DDL
change.

## Question

Land a first-class **line distribution** on every SKU -- the law one line's quantity is drawn
from -- and route every reader through it.

- **The object.** Family + parameters with `mean()`, `cdf(k)`, `quantile(p)`,
  `expected_min(S)` (`E[min(X, S)]`) and `sample(rng)`. The one family today is
  `max(1, Poisson(lambda))`; its sampler stays Knuth's (`Demand.poisson_sample`) so the draws
  are unchanged. Held on `Demand` beside the two scalars, which stay.
- **The inventory file.** Two columns on the `cartons` table, family and params JSON, following
  the `creation_plan` precedent -- a DDL change on the `inventory_db` family, so `--sync` before,
  `--accept` after, a per-vintage `dataset.override` for the pre-stamp shape. `generate_inventory`
  writes them; the loader reads them. A pre-stamp vintage (the reference pair) reconstructs
  Poisson(lambda) from `demand_qty_rate` at load, provenance `assumed`, and the staffing record
  names the reconstruction; regeneration is never forced.
- **The readers.** `Order.py`'s `max(1, poisson_sample(lam))`, `Demand.sample`,
  `expected_travel.accumulate` (lambda at ~275), `staffing.batch_content` (`max(1, lambda)` at
  ~129 -- a drift: the exact mean replaces it, the numeric change to era batch content stated
  in the record) and `coverage.line_units` all become calls on the object. No consumer names a
  family.
- **Provenance.** Every derived number that reads the law records the family it was read from.

Done when: the object exists with tests pinning the Poisson identities (`mean = lambda +
e^-lambda`, `expected_min` by hand); the columns ride the schema pipeline and a pre-stamp file
loads with the reconstruction stamped; every reader above is routed through it and a grep finds
no hand-coded line law outside the object; a flag-off run is byte-identical (a test through the
sampler proves the draws are unchanged); the staffing drift is corrected and stated.

## Answer

**LANDED 2026-09-06** (one session, AFK).  Every distribution-dependent number is now read off
one object on the SKU; a grep finds no hand-coded line law outside it.

**The object** -- `Warehouse/catalog/Demand.py:LineDistribution`: family + parameters with
`mean()`, `cdf(k)`, `quantile(p)`, `survival(upto)` (the vector `P(q > j)`, the tail
`expected_travel` reads per bin and `expected_min` sums), `expected_min(S)` and `sample(rng)`;
`to_row()` / `from_row()` are the file's two columns; `provenance` is `declared` (authored or
read from the stamp) or `assumed` (reconstructed).  One family, `poisson_max1` =
`max(1, Poisson(lam))`, sampled by Knuth's `poisson_sample` unchanged.  Held as `Demand.line`
beside the two scalars, which stay; `Demand.sample` IS the law's draw (never below one);
`Demand.from_rates(..., line=None)` and `Order.build(..., line=None)` reconstruct
Poisson(rate) as `assumed`; `Order.reorder` carries the object.  `line_law_census(orders)` is
the record's block.  Tests pin `mean = lam + e^-lam`, `expected_min` by hand, the survival
vector against the pmf sum, the quantile, the round trip and the refusals.

**The inventory file** -- `cartons.line_family TEXT NOT NULL` + `cartons.line_params TEXT`
(the `creation_plan` precedent).  `inventory_db` moved `0e234fbfc739 -> 4ff06991df47` through
the pipeline: `--sync` before the DDL edit, `--accept` after; the outgoing id is vetted as
`PRE_LINE_LAW_INVENTORY_SCHEMA_ID` with its commit-window comment, both shape documents are
committed.  `load_inventory_from_db` now binds the file to ITS OWN vintage (`Schema.dataset
.bind`) and reads the named query `cartons` (`LIMIT :limit`, `-1` = all); the pre-stamp
vintage's `dataset.override` serves the two columns as NULL and the loader passes `line=None`,
so `Order.build` reconstructs Poisson(CLAMPED rate) -- exactly what every pre-stamp load
sampled.  Regeneration is never forced.  `generate_inventory` stamps the law on both generator
paths at the CLAMPED rate (the legacy build clamps its drawn float scalar too, so the file, the
memory and the reload read one law -- a code-review finding).  `inventory_semantics` tags both columns.  The identity test's verification
regex now recognises `dataset.bind(path, 'family')` as a check site.

**The readers**, each a call on the object: the batch sampler (`Workload_Builder.Batch`:
`c.demand.sample(rng=r)`, no hand `max(1, ...)`) -- the ticket named `Order.py`'s
`max(1, poisson_sample(lam))`, which is the WEIGHT law (`_sample_weight`, a physical attribute)
and is left as is with a comment saying so; `expected_travel.accumulate` (`line.survival`;
`_poisson_tail` and the `gammainc` import deleted); `staffing.analytic_pick` (`line.mean()`);
`coverage.daily_demand` (`line.mean()`; `line_units` deleted).  **Provenance**: `analytic`,
`coverage.final[<ch>]` and `SectionRates` carry `line_families`; the calibration block gains
`line_law` (the census: families, provenance counts, `reconstructed_skus`), so the staffing
record names a reconstruction.

**The clamp rule** (`Order.build`): a stamped Poisson law whose rate equals the CLAMPED scalar
is kept; one authored at the unclamped float follows the clamp (provenance kept); one agreeing
with neither RAISES -- the scalar is the law's parameter and every reader sees one value.

**Byte-identical, proven**: goldens captured on the pre-stamp commit -- three seeded batches'
`items`, the v1 and v2 batch-cache fingerprints, and direct draws at three rates -- are pasted
into `Tests/unit/test_line_distribution.py` and reproduce exactly.  `batch_fingerprint` hashes
a law only when it is NOT `poisson_max1` (whose parameter is the `qty` array already hashed),
so every existing cache file stays valid and a future family can never be served from a
Poisson cache.

**The drift, corrected -- and the ticket's claim corrected with it.**  `analytic_pick` read a
line as `max(1, lam)`; it now reads `lam + e^-lam`, so the recorded `analytic` block
(`units_per_line`, `seconds_per_unit`, `analytic_s_pick`) moves by the `e^-lam` term per SKU.
The era's BATCH CONTENT does not move: `era_coverage.stage_a` feeds `batch_content` with
`expected_travel`'s `units_per_line`, which already summed the exact tail.  So "the numeric
change to era batch content" is nil; the change is to the script-only analytic prediction the
record carries beside the expectation.

**Verification**: 20 new tests (17 functions); `test_coverage_rescale`, `test_staffing_derivation`,
`test_expected_travel`, `test_batch_sampler_v2`, `test_rng_determinism`,
`test_equilibrium_reorder`, `test_mixed_catalog_channels`, `test_warehouse_sizing`,
`test_era_wiring`, `test_viewer_named_queries`, `test_reorder_queue`, `test_per_item_charge`,
`test_equilibrium_check`, e2e `test_batch_precompute`, integration `test_profile_tree_golden`
green.  Gates: `verify_context`, `path_guard`, `docref_guard`, `runschema.contract`, the full
`preflight` (both canaries; tree unchanged, fingerprint refreshed), `profile_tree --write`
(source fingerprint), `test_schema_identity`, `test_column_semantics`, and
`test_schema_compatibility` -- 210 of 211: the one failure is its consumer sweep picking up a
stray `.claude/worktrees/peaceful-wiles-3853f3/` worktree left by another session, not this
change.  One PRE-EXISTING ordering failure reproduced at HEAD in a clean worktree
(`test_batch_sampler_v2::test_config_sampler_reaches_both_production_construction_sites`
after `test_coverage_rescale` + `test_era_wiring`: CONFIG's sampler leaks to `v1`) -- flagged
as its own task, not touched here.

**Left as is, on purpose**: the velocity rankings (`freq x quantity_rate`, `Inventory_Management`,
`strategy_runner`, `workunits` yardsticks, `run_map_precompute`) read the scalar as a WEIGHT,
not as a line's units, and moving them would re-rank placements; `expected_batch_demand` stays
`freq x rate`.  ROUTED after the code review found it: `Inbound/gain.py`'s unmeasured branch
estimated visits from `quantity_rate` as units per line (a λ=1 SKU priced at +37% visits); it
now reads `line.mean()` -- flag-on (inbound) only, the store path untouched, and the inbound
campaign is held until the era anyway.  Its documented `rate <= 0` never-picked sentinel stays
(an ARRIVAL fact, not a line quantity; production rates are clamped to >= 1, so it fires only
in tests).  The review also bounded `quantile` (a `p` above the
running sum's float plateau never returned) and capped `lam` at 700 (`e^-lam` underflows).
Glossary: *Line distribution* now says where the law lives.
