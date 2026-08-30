# Design the phased funnel

Type: grilling
Status: open

## Question

Design the two-phase evaluation replacing the multiplicative sweep. Phase 1: inbound-off runs
(matching the historical simulation shape) select the top-k placement/scheduling arms — decide
the selection metric (labor cost? which scope?), k (charter sketch: top 5 of the ~34/36-arm
suite), and how the selection is recorded so phase 2 can consume it. Phase 2: inbound-on runs
sweep top-k × the inbound policy arms — decide how those cells are generated: is the cell
generation machinery redesigned to be phase-aware (a selection stage between phases), or are
the two phases simply two runs with a hand-carried arm list? Constraints from the charter:
byte-identical determinism throughout, resume at one uniform grain across the operation
(refusal-until-clean), and the two simulation levels (inbound on / inbound off) must coexist
in one config surface. The `Cell` NamedTuple already has room for another axis (memory:
`putaway-seams-for-inbound`); `analyze_run`'s cross-cell what-if writers are the phase-1
reading surface.

## Comments

2026-08-27, from resolving "Design the standing-dock mechanics" (01): the user named the
objective — minimize TOTAL PRODUCTION HOURS = unload + put + pick — as the evaluation frame,
reported beside the fee proxy. A strong candidate answer to this ticket's "selection metric
(labor cost? which scope?)" question; argue phase 1's selection metric and phase 2's comparison
surface against it. Per-crew hours are already separable via the work_events queue
discriminator. Honesty note from 01: total unload hours vary across arms only through the
reorder feedback loop, so the first-order lever is placement quality buying put + pick hours.

2026-08-29, from resolving "Design the space timeline" (03): "Define the inbound objective"
(10) may add ON-SHELF AVAILABILITY (missed orders) beside total production hours as a
comparison surface; check its resolution before fixing the selection metric.

2026-08-29, from resolving "Define the inbound objective" (10): resolved — the selection
metric stays TOTAL PRODUCTION HOURS; missed share is REPORTED beside hours and the fee proxy
in the phase-2 comparison surface, never a selection metric (if a future-work arm beats FIFO
on hours while missed share degrades, the funnel must surface it, not average it away).
Expectation to carry into the design: inbound gradients concentrate on pool-arm rows
(order-blind restock arms — fifo, cmax/cmin — have no bin-quality channel), so phase 1's
top-k must not be read as "the k most inbound-sensitive arms".

2026-08-29, from resolving "Name the policy arms and their knobs" (05): the phase-2 inbound
axis is FIXED at six named arms — `fifo`, `lifo`, `gain_myopic`, `gain_forecast`,
`gain_gated`, `futuresight` — each setting both registry knobs to one name, all unbounded.
Scope this ticket inherits from 05: the roster has NO weight grids (scores never blend
hours with days), so the only swept scalars are `INBOUND_URGENCY_HORIZON_DAYS` (the
gate's fee-vs-hours frontier) and `INBOUND_FUTURESIGHT_BATCHES` (the unlawful reference's
depth) — decide their grids HERE as part of the phase-2 cell design (this absorbs the
map's former "weight-knob sweep design" fog item). Two standing exclusions to respect:
`futuresight` never enters the recommendable set, and `lifo` is a control row, not a
candidate.
