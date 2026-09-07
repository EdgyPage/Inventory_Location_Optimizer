# Field the floor: the planner against the line

Type: grilling
Status: resolved

Graduated from [Build the line floor](17-build-the-line-floor.md), whose 40-day check found the
floor DECIDED and STAMPED but not FIELDED on the fulfillment section. HITL: a decision about
what the warehouse must hold, not a build. Skills: `grilling` + `domain-modeling` (the glossary's
*Line floor* and *Base stock* entries promise a shelf a pick's worth deep; the planner does not).

## Question

The coverage rescaling asks for one line per SKU (`Q = L_s`, 1,844,275 units on the reference
pair's fulfillment section), and the warehouse is sized from those levels through the fixed
point. The planner (`Warehouse/inventory/inventory_planning.py`, `_add_run` and the share
rule) then fields what its bucket capacity allows: 1,672,280 units, with **48,466 SKUs (30.3%)
below their own line floor** -- a traced SKU with a floor of 13 was fielded as four singleton
units. Under base stock a shelf below the line is a treadmill: the SKU is picked every day for
exactly its shelf, its rolled-over remainder grows without bound (9 -> 157 units on the traced
SKU over 14 days), the fulfillment leaf demanded 45k -> 125k units a batch against 32k picked,
missed share sat at 0.663 and rising, supply carry stood at 88,857 units on day 39, and the
crew ran at 0.985 with 39 of 40 days capped. The store fielded its floor (Q/L 1.01, 7.9% of
SKUs below) and degraded only through [Let a base-stock top-up reach the shelf](20-let-a-base-stock-top-up-reach-the-shelf.md).

The record already says so: `calibration[<pair>].coverage.final[<channel>].fill` is priced
on the PLANNED levels (0.782 fulfillment vs ~0.9 at the floor), and `planned_sum_q` sits below
the requested `sum_q`. What the run did not have is a rule.

Decide what "the floor" binds:

1. **The planner must field the floor** -- a SKU's planned Q is never below its `L_s`; the
   fixed point sizes the section (aisles, bins, `ff_fill` / `store_fill`) until it does, or
   refuses the run and says which bucket is short. Changes the planner's contract (today it
   grows AND shrinks levels by share) and may grow the fulfillment warehouse.
2. **The floor yields to capacity, and the record says so** -- keep the planner, stamp the
   fielded shortfall (`below_floor_skus`, `below_floor_demand_share`) and read the fill rate
   at the fielded levels as the expectation (the record already does the latter). Base stock
   then runs on a shelf below the line for a third of the section, and the treadmill above is
   the regime's honest outcome, not a defect.
3. **A per-section floor scale** -- `floor_lines` per channel (the fulfillment section's
   singleton bins hold one unit each, so a line-deep shelf is a bin per unit).

Whichever wins must square with the charter's "no bespoke conversions implicit in the
inventory" and with *Equilibrium with headroom*: a section that cannot hold its floor cannot
drain its day.

Done when: the rule is decided and recorded, the planner or the record follows it (a task
ticket if the planner's contract changes), and the glossary's *Line floor* entry says whether
the floor is a promise or a request.

## Answer

**Resolved 2026-09-07 (grilling, two rounds, every recommendation accepted; the user's own
reframing in round 2 -- "would this all be easier if we just got rid of the initial stocks?" --
widened the decision from the planner to the catalogue).** The floor is a PROMISE, and the
catalogue stops carrying stock levels at all.

**Facts first** (fact-finder, on the check run `comparison_20260906_222118`; it reproduced the
run's plan exactly -- 0 of 400,000 SKUs differ from `planned_inventory.db` -- so every number
is on the run's own footing):

- The fulfillment section was NOT too small. Its bin total is demand-derived
  (`_demand_total`) and matched the floor's requirement to within 317 bins. What was wrong is
  the tier MIX: `mode: fixed` spreads that total 0.5 / 0.3 / 0.2 across ff_small / ff_medium /
  ff_large, while `bucket_requirements` at the floor levels needs 11% / 63% / 26%. ff_medium's
  budget (263,925 free bins against 556,691 required) drained in phase 1 of
  `sample_to_capacity`; all 48,466 below-floor SKUs can reach ONLY ff_medium (a 7-12 inch item
  never stacks two into an 18 inch bin). The surplus went to phase-2 growth (64,989 SKUs above
  their floor).
- Demand mode alone does not clear it: re-planned with fulfillment in `mode: demand`, every
  tier's budget exceeds its requirement and 15.8% of SKUs (25,241, again all medium-only) still
  end below floor. The second mechanism is the sampler: `_add_one` charges whichever reachable
  bucket has the most free bins in ABSOLUTE terms, not the tier `viable_storage_units` packed
  the SKU into, so multi-tier SKUs are charged at lower density to buckets budgeted for others,
  those drain, and single-tier SKUs stop 2-6 units short. This is also the whole cause of the
  store's 7.9% (traced on three SKUs; no cap is involved; `_apply_caps` never ran).
- Keeping the fixed split and raising `target_bins` needs ~2.1M fulfillment bins (2x the
  requirement, 1,983 aisles) before no SKU is below floor, most of the extra becoming growth
  (planned sum Q 3.5M against 1.84M requested).
- 94% of fulfillment SKUs and 84% of store SKUs have a floor larger than one bin / pallet of
  themselves holds: "a line on the shelf" is a multi-bin holding almost everywhere.
- Under the era the catalogue's authored levels were ALREADY dead: `rescale_section`
  overwrites Q and rp and clears `stock_plan`; round 0 of the fixed point plans them only to
  seed a line count. Flag-off they live only as the base the planner grows and shrinks by
  share. The generator authors them as `coverage_batches x expected batch demand`
  (`EQUILIBRIUM_COVERAGE_BATCHES`, `REORDER_SAFETY_BATCHES`) -- the bespoke conversion the
  charter forbids and the source of the 1,771-day store (09, 14).

**Decisions.**

1. **The floor is a promise.** Every planned SKU's Q is at least its `L_s`; the fixed point
   sizes the section from those levels as it already does; a section that cannot hold its
   floor REFUSES the run. Rejected: "the floor yields to capacity, stamped" (the equilibrium
   check's own clauses -- missed share not trending, days drained on labour -- cannot pass
   under a treadmill, so it declares an era its verifier can never read in band); a per-channel
   `floor_lines` (a bespoke conversion authored to this catalogue's bin sizes).
2. **The promise is kept by fielding the REQUIREMENT.** `bucket_requirements` (the tier mix
   `viable_storage_units` packs each SKU into at its level) is already the exact per-bucket
   statement of what the levels need. The planner sizes every bucket in demand mode from it and
   fields each SKU at exactly that packing -- no re-choice by "emptiest bucket", no growth, no
   shrink. Every SKU fits by construction; the fill headroom is purely free bins. Rejected:
   demand mode plus a sampler fix (fixes by margin, not by construction); demand mode plus
   over-sizing (pays for the sampler's disagreement in bins).
3. **When it cannot be fielded, refuse at setup**, naming the bucket and the bins short --
   the precedent is the era refusing the legacy crew flags. A cap and a floor are two
   declarations by the same person that contradict; the run does not pick silently.
4. **Exactly the rescaled levels.** `planned_sum_q == sum_q` is an assertion; the fill rate
   priced pre-plan equals the one priced post-plan; a SKU never leaves base stock because a
   tier had slack.
5. **The catalogue carries NO stock levels** (user, round 2). `equilibrium_qty`,
   `reorder_point` and `stock_plan` leave the catalogue. It carries demand (rate, line law,
   lead, supply variability) and geometry; stock is a RUN's declaration (`coverage_days`,
   `safety_days`, `floor_lines`) derived at setup in EVERY mode. The run's
   `planned_inventory.db` keeps carrying the fielded levels and packing -- that is the run's
   own record, and workers load it. A SKU's stock level was never a fact about the SKU.
   Consequence: the era-only rule (the earlier Q5) and the "era forces demand mode" clause
   collapse -- there is ONE planner contract everywhere.
6. **Flag-off runs derive their levels the same way.** The fixed point runs in every mode,
   using the reporting frame (8 h, which exists flag-off) as its day; the flag then only
   decides whether the clock cuts and caps. Rejected: a declared `lines_per_day` flag-off (a
   knob nobody sets right); retiring the flag-off regime (a scope call for another map).
   Cost accepted: ~8 min per pair at setup on the reference catalogue (458 s measured; the
   inputs are fingerprint-cached), and a HARD BREAK for every flag-off run's inventory, the
   goldens included -- ADR-0002.
7. **The catalogue's contract changes as a schema vintage.** The three columns are DROPPED
   from `cartons` (a new `inventory_db` vintage; older vintages served through a `dataset`
   override that ignores them -- the `line_family` precedent), not kept as NULL: a NULL column
   is an authored level that happens to be missing, a dropped one is a contract. The generator
   retires `EQUILIBRIUM_COVERAGE_BATCHES`, `REORDER_SAFETY_BATCHES` and stock-plan authoring
   (the `generate_mixed_profile` formula too); `creation_plan` stays (a generation recipe, not
   a level). The record's `catalogue` block (implied coverage of the authored levels) dies;
   round 0 seeds n from the analytic batch content alone.
8. **The fixed tier distribution is retired.** With demand sizing the only sizing, fulfillment
   `mode: fixed` / `distribution` has no reader. Depth classes and the aisle split SURVIVE
   (they reshape aisles, not the bin count). Rejected: keeping it as a band check (a knob with
   no consumer).
9. **Sizing reads the EMITTED capacity.** Under an aisle split with `capacity_loss`, demand
   replicas inflate by `1 / (1 - loss)` and the promise is checked against what was actually
   emitted: `emitted x fill >= requirement` per bucket. Depth classes need nothing.
10. **Explicit sizing knobs.** `min_bins` is honoured as a floor on bins (extra bins are free
    bins); `max_bins` / `max_aisles` that bind below the requirement refuse per decision 3;
    `target_bins` goes with the fixed distribution.
11. **The record stamps that the promise held.** `coverage.final[<ch>]` gains a `fielded`
    block: `below_floor_skus: 0`, `above_floor_skus: 0`, and a per-bucket table of
    `requirement / capacity / free` bins, so a reader sees the promise was TESTED. `store_fill`
    / `ff_fill` keep 0.85; their meaning becomes "the share of each bucket the levels occupy
    at setup", the rest free bins. Whether 15% free is the right reserve for base-stock
    top-ups belongs to [Let a base-stock top-up reach the shelf](20-let-a-base-stock-top-up-reach-the-shelf.md).

**Graduated (two AFK task tickets):**
[Retire the authored stock levels](22-retire-the-authored-stock-levels.md) and
[Field the requirement](23-field-the-requirement.md) (blocked by 22). The 40-day re-read stays
in the fog until 20 and 21 also land.

**Glossary**: *Line floor* gains its promise sentence; *Stock coverage* says the catalogue
holds no levels. **ADR-0002** records the removal. Fact-finder scripts (`probe.py`, `reach.py`,
`trace.py`, `static.json`, `plan_*.json`) lived in the session scratchpad and are not assets.
