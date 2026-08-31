# Design the phased funnel

Type: grilling
Status: claimed

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

2026-08-30, from resolving "Define the yard metrics" (07): two items land here. (a) The
threshold calibration probe: `INBOUND_FEE_THRESHOLD_DAYS` ships as a 2.0-day placeholder;
the rule — the default sits where FIFO under the chosen pilot leads (median ≈ one working
day, σ ≈ 0.7) shows nonzero, NON-SATURATED overage. Because overage derives at analysis
from raw stamps with the run's recorded threshold (07's derive-late decision), the probe is
an analysis re-report over one FIFO run, not a sweep. (b) A presentation call: whether the
fee axis (`yard_overage_days`) ALSO earns a `headline`-family slot for the phase-2
comparison surface, or reports only from the `yard` family beside hours and missed share.

2026-08-30, from resolving "Build the futuresight window feed" (13): a cost fact for the
`INBOUND_FUTURESIGHT_BATCHES` grid decision. The window feed re-copies and re-aggregates
the remaining script every batch, so `'all'` on a deep run is O(n²·|batch|) over the run —
deliberately unmitigated (06: no cache machinery) — and the cost lands inside `t_reord`,
so the futuresight arm's deep-tier reorder seconds read inflated, worst at run start.
Take a bench number before putting `'all'` (or a large w) in the grid at depth, and read
that arm's `t_*` with this caveat. Finite small w is cheap.

2026-08-31, from resolving "Build the lead distribution" (15), commit `50149dc` — four
facts the phase design depends on:

(a) **Phase 1's byte-identity holds.** Spread zero is not a degenerate draw, it is the
absence of one: no generator is constructed and the median is returned as the same float,
so an inbound-off (or spread-off) run is bit-identical to the historical shape. Proven by
test three ways, including a drain-by-drain manager lockstep whose target is precisely the
subtle case — a seed leaking entropy while the spread is nominally off. Phase-1 rows are
row-comparable with the pre-inbound archive on this axis. (Only this axis: the sampler-era
and placement-pool boundaries still apply.)

(b) **The domain TAG is `0x1EAD`.** Recorded because it is un-re-derivable: changing it
re-rolls every lead schedule ever run, so any phase-1/phase-2 comparison spanning a TAG
change would be silently incomparable.

(c) **The pilot leads are free to fix, but no seed knob exists.** `lead_seed` is
`seed_world()`, by decision — a lead schedule is a world fact, so "same `--seed-world` =
same warehouse, catalogue and leads" is one sentence and every arm of a cell sees the
identical lead schedule (common random numbers across arms; the funnel gets paired
comparisons for free). A lead-REALIZATION axis would therefore be a new
`INBOUND_LEAD_SEED` knob, not a reuse of `--seed-world`, which also moves the warehouse.

(d) **The knobs are not sweepable from the CLI yet.** `INBOUND_LEAD_MINUTES` (renamed from
`INBOUND_TRAILER_LEAD_MINUTES`) and `INBOUND_LEAD_SPREAD` are declared and threaded into
`CONFIG`/`inbound_spec`, but seams 3 and 4 — the CLI flag and the run-spec record + its two
restore sites — stay DEFERRED to the first sweep, per ticket 09's precedent. That deferral
is now shared by the whole standing-yard family (09, 14, 13, 15), so "the first sweep wires
the flags" is a concrete task this ticket's design should either schedule or graduate: a
phase-2 cell that cannot record the lead shape it ran under is not re-analysable, and 07's
derive-late fee report reads the threshold off the run spec.

Also worth having when the acceptance probe is specified: at median = one working day,
σ = 0.7, seed 42, eight trailers dispatched at one epoch land in yard order
`[1, 2, 6, 5, 0, 4, 7, 3]`. That is criterion (a) of 10's acceptance test — arrival order
is no longer dispatch order — demonstrated at unit scale. Criterion (b), binding cuts,
needs a real run and remains this ticket's to specify.
