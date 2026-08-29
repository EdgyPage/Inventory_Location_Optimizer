# Define the inbound objective

Type: grilling
Status: open

## Question

The charter's score — a trailer's load placement-quality against available space — may be
VACUOUS for trailer ordering: prime freight wants prime bins, but every SKU benefits from
easier pick locations, so every ordering chases the same spots (raised resolving "Design the
space timeline", 03). Decide what the yard/dock decisions actually optimize. Fixed already:
the staging decision anchors to immediately available bins (operationally stable — 03's
record). Open: whether the objective becomes ON-SHELF AVAILABILITY — minimize missed orders
(the `unmet` / `_shortfall` counters the sim already produces; per-SKU demand rides
`Order.demand`) while the best available bins go to the highest-demand items — which also
gives the oracle view something to do; whether "just-in-time unloading" is expressible at all
under the charter's no-deferral rule (a freed door is always filled — the only lever is WHICH
trailer, never WHETHER, so timing value must live inside the ordering); and whether the
decision output stays a per-drain ranking (the pure-key registry contract) or becomes an
UNLOAD PLAN — an ordered list of standing trailers minimizing the total work of future picks
over the SpaceView's two tiers — recomputed at the drain quantum. The user flagged the
sequencing of that plan-based flow as the hard part; getting it concrete is this ticket's
work.

Downstream: the evaluator prototype (04) and the arm roster (05) consume the answer; the
on-shelf-availability columns would land in the yard metrics (07); the funnel's selection
metric (08) may gain the new objective beside total production hours.

Consult `grilling` + `domain-modeling`; `codebase-design` if the unload-plan shape wins.
