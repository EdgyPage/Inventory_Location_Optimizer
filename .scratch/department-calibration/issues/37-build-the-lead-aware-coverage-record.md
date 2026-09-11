# Build the lead-aware coverage record

Type: task
Status: open

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
