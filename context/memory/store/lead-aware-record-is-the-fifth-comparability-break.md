---
name: lead-aware-record-is-the-fifth-comparability-break
description: "since 2026-09-10 (department-calibration 37) the coverage record is declared AT the order-to-shelf lead, so every inbound-on era run before it stamped lead 0 and read under a smaller stock and warehouse; with the pilot pipeline on the reference pair's fulfillment floor moved 1.2668 -> 1.4994 lines and the warehouse 2,536 -> 2,774 aisles; inbound-off is byte-identical"
metadata: 
  node_type: memory
  type: project
  originSessionId: 57815645-cff3-4a1b-a5a8-c6faa255fbef
  modified: 2026-09-11T01:46:13.901Z
---

The fifth comparability break on the department-calibration map (after the per-item charge
fc7a46a5, the placement pools a033aff, ADR-0003's drain order and the derived fill of ticket 35):
**the stock declaration is priced at the expected order-to-shelf lead, not at zero.** Built
2026-09-10 by "Build the lead-aware coverage record" (department-calibration 37) from the
decisions of "Declare the coverage against the inbound lead" (36). Per SKU
`lead_days = lead_time_mean × lead_unit_days + transit_days`: the supplier lead (batches, one a
day under the era) plus the pair's transit on the day grid, `E[ceil(L / D)]` over the trailer's
lognormal law (`coverage.transit_day_law`; 1.7656 site days at the pilot's 480 min / 0.7 / 8 h,
exactly 1 at spread 0, 0 with no trailer type). Levels, the pipeline stamp and the first-pass
fill are priced at it (the fill: the order-up-to POSITION less the SKU's own prior lines still in
transit, compound Poisson by Panjer's recursion) and the floor is SOLVED at it. The record gains
a `lead` block (`era_coverage.lead_block`, the only thing a rebuild needs) and, per section,
`fill.lead_days`, `fill.transit_days` and a `fill.vs_transit` curve the audit reads the EXPLAINED
supply level off.

**The reference pair moved (pilot pipeline on, inbound-off byte-identical to the archive):**

| | inbound off (= every record before) | pilot inbound on |
|---|---|---|
| transit stamped | 0 | 1.7656 d |
| store floor / sum Q | 1.2728 lines / 3,015,242 | 1.3078 / 3,086,462 |
| fulfillment floor / sum Q | 1.2668 / 2,220,097 | 1.4994 / 2,595,593 (pipeline 34,026 u) |
| fill store / fulfillment | 0.97518 / 0.97470 | 0.97519 / 0.97488 |
| warehouse | 2,536 aisles / 2,311,000 bins | 2,774 aisles / 2,505,050 bins |

The store's pipeline stamp stays 0 (its per-SKU demand rounds to nothing over 1.77 days) and its
floor barely moves; fulfillment is where the lead bites. Setup at the pilot regime costs ~35 min
on the reference pair against ~3 min inbound-off (the floor solve is 17 lead-aware fill
evaluations per section; the transit-tail truncation landed after that timing).

**Why:** the first yard-on era run (inbound 23, `comparison_20260910_173151`) failed
fulfillment's supply clause on every arm at 0.148 against a stamped 0.025 with every crew in
band -- the record had declared at lead 0 while the pipeline realized ~1.8 days. Nothing about
that run's crews or warehouse was wrong; its stock was.

**How to apply:**
- An inbound-on era run is comparable to another only if both records carry `coverage.lead`
  with the same `transit_days`; a record without the block was declared at lead 0. Inbound-off
  records are unchanged on either side of the commit (`lead.trailer_type` None, transit 0).
- The inbound regime's median and spread now move the record (floor, levels, warehouse) through
  the closed form -- inbound 25 must pick the regime BEFORE phase 1, and its stock cost reads off
  the record without a run. Doors never enter the form.
- The audit's supply row now carries an "order-to-shelf lead" row: stamped vs realized (Little's
  law, mean `in_transit_qty` over mean `units_ordered` per batch) and the level the realized lead
  explains off the stamped curve. A supply level near the explained value but above the band is
  the yard binding (a campaign effect); a gap the realized lead cannot explain is model error.
- A non-era catalogue with a supplier lead and no trailer keeps its LEVELS byte-identical but its
  stamped `fill` is now priced at that lead (decision 5); no such catalogue exists in the archive.
- Until inbound 27 chains the supplier lead before the trailer, an era run with a trailer type
  REFUSES a catalogue whose SKUs carry a lead (`era_coverage.refuse_discarded_lead`); the
  reference `lt0` pair is unaffected.

See [[inbound-lead-is-not-in-the-coverage-record]],
[[derived-fill-is-the-fourth-comparability-break]], [[nothing-is-lost-under-the-era]],
[[coverage-in-days-floors-the-store-section]], [[config-knob-has-five-seams]].
