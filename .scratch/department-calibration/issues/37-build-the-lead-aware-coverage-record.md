# Build the lead-aware coverage record

Type: task
Status: resolved

Graduated 2026-09-10 from
[Declare the coverage against the inbound lead](36-declare-the-coverage-against-the-inbound-lead.md),
decisions 2-8 and 10. AFK build. Skills: `codebase-design`; `schema-maintainer` if the record's
or the stock declaration's shape moves; `test-developer` for the closed form and the refusal;
`memory-maintainer` for the comparability break. Blocks inbound-optimization's
[Re-verify the gate under the lead-aware record](../../inbound-optimization/issues/26-reverify-the-gate-under-the-lead-aware-record.md)
together with inbound 27 (the dispatch side).

## Question

Not a decision: the build that makes 36 true on the record side. What lands:

- **The lead per SKU, in days.** `coverage.rescale_section` takes a site transit term
  `transit_days` (0 with no pipeline) and reads each SKU's supplier lead as
  `lead_time_mean / releases_per_day` (36 decision 4; the batches-read-as-days bug at
  `coverage.py:171` ends here). `lead_days_s = attr_s + transit_days` feeds `stock_levels` and
  `pipeline_qty` exactly as today's `lead` does. The fixed point (`era_coverage.fixed_point`)
  receives `transit_days` from setup, derived from `inbound_spec()` and `work_day_spec()`:

      transit_days = E[ceil(L / D)] = 1 + sum_{k>=1} (1 - Phi(ln(k D / m) / sigma))

  with `m` the median in seconds, `sigma` the spread, `D` the site day -- exactly 1 at spread
  0, 0 with no trailer type, 1.766 at the pilot regime (480 min, 0.7, 8 h; the run's Little
  read was 1.78 / 1.75). Stamped on the record per pair: `transit_days` (`derived`, beside the
  median, spread and day it was read from) and, per section, the units-weighted mean
  `lead_days` next to the `pipeline_units` the fill already stamps.
- **The lead-aware fill** (36 decision 5). `coverage.fill_rate` prices the shelf a line meets
  as `Q + pipeline_qty - D_L`, where `D_L` is the units of the SKU's prior lines still in
  transit: the observed lead is integer days on the grid (P(ceil(L/D) = k) from the same law,
  `attr_s` added), the count of prior lines inside those k days follows the SKU's line rate
  `n * pi_s` (one batch a day under the era), each line of the stamped line law. The fill is
  `E[min(q, (S - D_L)^+)] / E[q]`, units-weighted as today, with the line share as the per-SKU
  rate (36 decision 6). At lead 0 it must reduce to the current expression EXACTLY -- a test
  proving equivalence, the byte-identical discipline for a flag-off record. `solve_floor_lines`
  absorbs it unchanged: the fill stays a non-decreasing step function of the floor.
- **The refusal** (36 decision 8): under the era with a trailer type declared, any SKU with a
  nonzero `lead_time_mean` refuses at setup, naming inbound 27 -- until 27 chains the supplier
  lead before the trailer, the pipeline discards it. Non-era runs keep reading batches,
  byte-identically; an era run with no trailer type honours the attribute through the batch
  transit and needs no refusal.
- **The audit** (36 decision 10): `equilibrium.expectations_for` and `throughput/audit` read
  the realized order-to-shelf lead per leaf -- `mean_in_transit_pieces` over the window's mean
  units ordered per day, both per batch on `batch_stats` (the ordered flow is what 23 summed by
  hand) -- and print it beside the stamped `lead_days`, no band. The supply band stays
  `SUPPLY_LEVEL_TOL` around `1 - fill(stamped lead)`; `1 - fill(realized lead)` is printed as
  the EXPLAINED level. `_supply_clause`'s verdict is unchanged; the equilibrium report gains
  the three numbers.
- **The record's shape** gains keys under the pair block and `coverage.final[ch]`, so
  `schema-maintainer` if a dataset shape or the `stock_levels` table moves. The per-SKU
  `lead_days_s` need not be a column: it re-derives from the attribute and the stamped
  `transit_days`.
- **The five seams are untouched**: `transit_days` is DERIVED, never a CONFIG key (03
  decision 2). `safety_days` keeps its meaning and its `assumed` 2 (36 decision 7).

Acceptance, on the reference pair: an inbound-off setup re-declares byte-identically (levels,
floors, fill, warehouse); an inbound-on setup at the pilot regime stamps `transit_days` 1.766,
a fulfillment floor above the lead-zero one, and a fill that clears `sqrt(0.95)`; the refusal
fires on an `lt1` sibling catalogue under the era with the yard on, and not without the yard.
The number the whole build is judged by is 26's residual: the fulfillment supply level within
0.02 of the lead-aware stamp.

Consequence to record with the build (`memory-maintainer`): solving the floor at the lead moves
fulfillment's stock and warehouse above the lead-zero reference, so every inbound-on number
before this build reads under a different era -- the map's fifth comparability break.

## Answer

Resolved 2026-09-10 (AFK build). Landed on `develop` as one commit; the architecture and
context layers regenerate in a separate chore commit, as usual.

### What landed

- **The transit on the day grid.** `Optimization/simconfig/coverage.py:transit_day_law`
  (`E[ceil(L / D)] = 1 + sum_k (1 - Phi(ln(kD/m)/sigma))` with the per-day pmf) reads the
  ticket's table to four decimals: 1.7656 at the pilot, exactly 1 at spread 0 over a one-day
  median, 0 with no trailer. Per SKU `sku_lead_days = lead_time_mean x lead_unit_days +
  transit_days` (`lead_unit_days = 1 / releases_per_day`, 1.0 when none is declared -- the
  flag-off reading of a batch as a day, so the `coverage.py:171` bug ends without moving any
  non-era record). `rescale_section` takes `transit_days` / `lead_unit_days` and stamps
  `transit_days`, `lead_unit_days` and the units-weighted `lead_days`.
- **The lead-aware fill** (decision 5). `fill_rate(orders, n, transit=, lead_unit_days=)`
  prices the shelf a line meets as the order-up-to POSITION `S = Q + pipeline_qty` less the
  SKU's own prior lines still in transit: the observed lead is `K = a + k` grid days (`a` the
  supplier lead rounded, `k` under the transit pmf), the prior lines inside them
  `Poisson(K . n pi_s)` of the stamped line law, so `D_K` is compound Poisson and its mass on
  `0..S-1` is Panjer's recursion, vectorised over SKUs in shelf-size groups
  (`_served_under_lead`; a transit tail under 1e-9 is left unpriced, the recursion is rescaled
  past 1e150 so a seed that underflows never zeroes a shelf). `E[min(q, (S - D)^+)] =
  sum_{u<S} P(q > u) . P(D <= S-1-u)`. A SKU with no lead and no pipeline stamp takes the OLD
  `expected_min` path -- the same floats, not a tolerance -- so a record with no pipeline is
  byte-identical. `solve_floor_lines` takes the same two arguments and the floor is solved at
  the lead; the fill stays a non-decreasing step function of the floor. `LineDistribution.pmf`
  is the law's row-by-row form (the founding family has the vectorised fast path).
- **The record.** `era_coverage.lead_block(inbound_lead_law(), work_day_spec())` is the
  pair's `lead` block (`transit_days`, `provenance: derived`, the trailer type / median /
  spread / day it was read from, `releases_per_day`, `lead_unit_days`); `fixed_point(...,
  lead=)` stamps it, declares every round at it, and per section stamps `fill.lead_days`,
  `fill.transit_days` and `fill.vs_transit` -- the fill as a 12-point curve over the transit
  (the same law, the median scaled; scale 1 IS the stamp) for the audit's explained level.
  `declare_from_record` reads the block back (a record without it declares at transit 0, a
  batch read as a day -- every record through today reproduces). `sim_config.inbound_lead_law()`
  is the guard-free read of the three lead keys: `inbound_spec()`'s crew guard cannot be
  answered before the fixed point has declared, which is why the lead law is factored out;
  the five seams are untouched (`transit_days` is DERIVED, never a CONFIG key).
- **The refusal** (decision 8). `era_coverage.refuse_discarded_lead`: under the era with a
  trailer type, a catalogue with `round(lead_time_mean) >= 1` on any SKU refuses at setup
  naming inbound 27. Not refused: non-era (keeps reading batches, byte-identically) and an
  era run with no trailer type (the batch transit honours the attribute; the tiny `lt1` pair
  declares at `lead_days` 1.0 and solves its floor above the `lt0` sibling's).
- **The audit** (decision 10). `equilibrium.realized_lead` (Little's law: mean
  `in_transit_qty` over mean `units_ordered` per batch, one batch a day under the era;
  `units_ordered` added to the batch frame and `SEMANTIC_USES`) and `fill_at` (linear on the
  stamped curve, held at the ends). `expectations_for` returns `lead_days`, `transit_days`,
  `fill_vs_transit` (None pre-lead); the `supply` clause's reading gains a `lead` block
  (stamped, realized, in-transit mean, ordered mean, explained `1 - fill(realized - attr)`)
  with the band and the verdict UNCHANGED; `summarize` prints
  `lead stamped X d / realized Y d, explained level Z`; the audit's inspection table gains
  an "order-to-shelf lead" row under the supply share (stamped / realized / no band /
  "reported, not judged . explains supply Z") only where a lead was stamped or measured, so a
  pre-lead archive's table keeps its rows.

### Acceptance on the reference pair (`catalogue_reference_lt0`, setup through `build_shared_assets`)

| | inbound off | pilot inbound on (53, 480 min, 0.7, 4 doors) |
|---|---|---|
| `lead.transit_days` | 0 | **1.7656** |
| store floor / sum Q / fill | 1.2728 / 3,015,242 / 0.975185 | 1.3078 / 3,086,462 / 0.975192 |
| fulfillment floor / sum Q / fill | 1.2668 / 2,220,097 / 0.974701 | **1.4994** / 2,595,593 / **0.974879** (>= sqrt(0.95)) |
| fulfillment pipeline stamped | 0 | 34,026 units |
| warehouse | 2,536 aisles / 2,311,000 bins | 2,774 aisles / 2,505,050 bins |
| wall | 188 s | 2,136 s (before the transit-tail truncation) |

The inbound-off record equals `comparison_20260910_173151`'s coverage record in every
level, floor, fill, round and fielded bucket (the only difference is the fragmentation's
timing stamp); it gains only the `lead` block and the three new keys. The fulfillment
curve reads 0.99384 at transit 0 down to 0.88014 at 10.7 days -- so 23's 0.148 supply level
was never a fill the lead-zero record could explain, and 26's residual has a number to be
judged against. The `lt1` refusal was exercised on a generated 90-SKU sibling (no `lt1`
catalogue exists on disk beside the reference pair); it fires with the trailer type under the
era and not without.

### Tests

`Tests/unit/test_lead_aware_coverage.py` (21 tests: the law by hand and at the table, the
per-SKU lead, byte-identity at lead 0 as the same floats, Poisson-by-hand under a one-day
transit, a two-point mixture, a seeded Monte-Carlo of the compound-Poisson form to 0.25%, the
fast path against `pmf`, the floor higher at the lead, the block / curve / rebuild / old
record, the refusal in every direction, `fill_at`, `realized_lead`, the supply reading, the
audit row, the batch frame, a mixed catalogue sharing one transit, and the tiny pair through
the setup path inbound off / on / `lt1`). Two existing tests adjusted for the record's new key
and the longer loop body. Reviewed by the project code reviewer and the test reviewer; their
findings (an unconditional audit row, the untruncated transit tail, the Panjer underflow, the
`>=` where `>` was claimed, no mixed catalogue) are all folded in.

### Consequences

- **The fifth comparability break** -- memory
  `lead-aware-record-is-the-fifth-comparability-break`: every inbound-on era run before this
  build stamped lead 0 and read under a smaller stock and warehouse; inbound-off is unchanged.
  The regime's median and spread now move the record, so inbound 25 chooses before phase 1.
- The `lead_unit_days != 1` case is a stated limitation: the fill's grid rounding
  (`round(attr x unit)`) and the ledger's batch rounding agree only at one release a day,
  which the era pins; nothing declares another schedule.
- `Tests/architecture` reports drift until the regeneration chain runs (nine new public
  functions and the new test file to catalogue) -- the architecture-maintainer's chore commit.
