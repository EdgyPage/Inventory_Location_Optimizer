# Field the floor: the planner against the line

Type: grilling
Status: open

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
