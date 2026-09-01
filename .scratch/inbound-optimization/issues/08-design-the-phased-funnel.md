# Design the phased funnel

Type: grilling
Status: resolved

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
`[1, 2, 6, 5, 0, 4, 7, 3]`, not dispatch order.

[Corrected 2026-08-31, same author, after this ticket's resolution flagged the label: that
demo is a PRECONDITION for the ordering lever having a gradient, not criterion (a) of 10's
acceptance test. Criterion (a) is yard CONTENTION — standing trailers regularly exceeding
free doors at drain start — which a single-epoch unit test cannot show. Both criteria,
contention and binding cuts, need the pilot run. The original wording claimed (a) was
already demonstrated, which would have let the probe skip half its check.]

2026-08-31, read against source while resolving 15 — the futuresight cost fact above
(the 2026-08-30 comment from 13) is imprecise about granularity, and the correction is a
CONSTANT FACTOR, not a change of order. What the code shows:

- `_futuresight_window` slices and copies once per injection, i.e. once per batch.
- `_window_rates` (`Inbound/gain.py:426`) runs inside the `futuresight` @ordering entry,
  so once per ENTRY CALL. `check_reorders` runs `_release_arrivals` + `_receive` once, and
  `_receive_standing` calls `yard_order` and `dock_order` once each — so an arm naming
  `futuresight` on both knobs aggregates TWICE per drain against ONE copy.

So the aggregation is the larger term by roughly 2x, not by an order: the run total stays
O(n²·|batch|) under `'all'`. Ticket 13's own Answer already said "per-injection copies plus
per-drain re-aggregation" and is accurate; it is the summary comment above that collapsed
both into "every batch". If it ever bites, memoizing `_window_rates` on the view's
`demand_v` is legal under 06 (the window rides that same event, no fourth counter) and
collapses the two entry calls into one — again a 2x, so it is not a fix for depth.

## Answer

Resolved 2026-08-31 by grilling (four rounds, twenty questions; every recommendation
confirmed, Q3 answered `(b)` explicitly).

### Phase 1 — a fresh, inbound-off SELECTOR run

**Fresh, not an archive re-read.** The archive cannot price this objective at all. Five
independent boundaries stand between it and today: the placement-pool refactor (`a033aff`
the last byte-identical commit), the over-picking fix (`0b0d7d7`), the seconds-not-
milliseconds correction, the v2 sampler flip, and the one-clock refactor that first made
put-away a TIMED quantity. The last is fatal alone — put hours did not exist as a
measurement in the archive, and no correction factor recovers them.

**Inbound-OFF**, keeping the charter but for a new reason: selecting under inbound-on-
`fifo` would bake ONE policy's contention pattern into the candidate set, biasing the
funnel's mouth toward arms that suit FIFO's yard behaviour specifically. Inbound-off ranks
arms on intrinsic placement quality.

**A PURE SELECTOR: no phase-1 number is ever quoted in the published result.** The whole
claim is made inside phase 2 against the `fifo` inbound cell. This is load-bearing three
ways — it makes a BETWEEN-PHASE build legal (which `(b)` requires), it makes a phase-1
flaw cost a re-selection rather than a retraction, and it means the lead TAG (`0x1EAD`)
and every other un-re-derivable constant need only be stable WITHIN a phase, not across
the campaign.

**Shape:** ONE cell, scheduler fixed at `lpt` — the series' winner; measuring inbound on
the naive scheduler the series retired would answer about a configuration nobody runs.

### The selection

**The unit is a restock RULE, not an arm.** `CHANNEL_RESTOCKS` filters on `s.restock`, so
picking `tmin` necessarily takes both `uni_tmin_norsl` and `opt_tmin_norsl`: 17 rules, each
costing 2 arms. "Top-k arms" is not an expressible unit.

**Metric: TOTAL PRODUCTION HOURS** (per 10) — which does not exist yet and must be BUILT.
Pick is the `production_time` quantity; unload is a `batch_stats.recv_seconds` column with
ZERO quantities declared; put is not in `batch_stats` at all, living only in `work_events`
rows with `role='put'`, which the analysis suite has never read. Selecting on pick hours
alone — all that `channel_rollup` and `whatif_labor` offer today — would systematically
favour arms that buy pick time with put-away time, the exact trade this effort measures.

**Scope: PER CHANNEL**, hours SUMMED across inventory profiles (hours are additive and
physical; ranks are not). Store and fulfillment are independent warehouses, so one global
list would rank two warehouses on one scale. Per-channel arm sets are natively supported —
`CHANNEL_RESTOCKS` is keyed by channel — so the two channels may legitimately carry
different top-5s. Profile DISAGREEMENT is a finding to surface, not to smooth (10's rule).

**k = FIVE rules, plus `fifo`, with bundle extensions capped at THREE families.** `fifo` is
not optional: `run_channel_rollup._baseline_entry` prefers `uni_fifo`, falls back to any
key containing `fifo`, then to `strategies[0]` — so an arm set without it silently
baselines against an arbitrary arm and renders plausible, meaningless `saving_abs` for
everything. That one mandatory row buys two things: the analysis baseline AND the
order-blind negative control (`opt_fifo`/`uni_fifo` are byte-identical runs, so a gradient
on that row would indict the machinery). The cap is what makes `(b)` costable — only
`tmin`, `tmax`, `rank_popularity` and `rank_random` have faithful gain bundles today and
every other family raises by design; if the top five holds more than three unfaithful
families, take the three highest-ranked and backfill from the faithful set.

**Recorded as a POST-ANALYSIS ARTIFACT** in phase 1's run root, shaped like the
channel-rollup writer, carrying the phase-1 run identity, the metric, ALL SEVENTEEN rules
in ranked order, the chosen five, the `fifo` rider, and which chosen rules need an
extension. `run_layout.json`'s `arms` key cannot serve: it records the REQUESTED restock
keys, is written once before simulation, and is resume-guarded. The full ranking rather
than the cut, so a later reader can see how close the decision was.

### Phase 2 — a cell matrix over inbound policy

**The six-policy axis becomes the FIFTH `Cell` FIELD, not six runs.**
`INBOUND_YARD_POLICY`/`INBOUND_DOCK_POLICY` are whole-run settings, so six policies would
otherwise be six single-cell runs — and the entire comparison surface (`whatif_delta`,
`whatif_labor`, `whatif_volume`) fires only when `len(cell_items) > 1`, so six runs would
produce NO comparison artifact and force a hand-rolled contrast. The cell matrix also
guarantees every cell sees the same frozen inventory and batch stream, the apples-to-apples
property a policy comparison needs; the `Cell` docstring already names this axis. Two
traps: `_build_cells` dedupes by name, so the axis MUST add a name suffix or inbound-on and
inbound-off collapse into one cell with no error; and the gain bundle refuses under
velocity zoning, satisfied by default since every committed spec has zoning off.

**Ten cells:** `fifo` (the REFERENCE — every delta then reads "versus FIFO", the campaign's
own question), `lifo`, `gain_myopic`, `gain_forecast`, `gain_gated` × three H points,
`futuresight` × two w points, and an inbound-OFF anchor (the validity check that the
ranking transferred — present as a cell, never the reference). At 12 strategies × 2 pairs
× 2 channels = 48 work units per cell, that is 480.

**H grid: 0.25×, 0.5×, 1.0× the CALIBRATED threshold**, not absolute days — the pilot fixes
the threshold and a horizon authored independently of it would drift out of meaning. Both
poles are predictable and get no cells: H = 0 collapses `gain_gated` to `gain_forecast` (a
seam test), H ≥ threshold collapses it toward FIFO.

**w grid: one small finite window and `'all'`**, the bench taken during the pilot.

### On the O(n²) — a correction to 13's cost claim, verified here

The figure is CORRECT but was single-sourced (13's answer, echoed onward unchecked), and
its two terms are not comparable. `_futuresight_window` re-slices and dict-copies the whole
remaining script once per batch: n(n−1)/2 batch-copies under `'all'` — 2,775 at 75 batches
against 375 for w=5. That is the n², and it is the MINOR term. `_window_rates`
re-aggregates the window "once per entry call" — i.e. once per DRAIN — walking every SKU of
every window dict. **The real cost is the n² multiplied by DRAINS PER BATCH, and that
multiplier is the unmeasured quantity: the bench is measuring drains per batch.**

A legal fix exists if it bites: the window is replaced wholesale by the injection that
bumps `demand_v` (it carries no counter of its own for exactly this reason), so the
aggregate is constant across every drain within a batch, and memoizing it keyed on
`demand_v` is 06's "legal-keyed-not-built" case, not new cache machinery. Prefer the memo
to dropping `'all'` — dropping the oracle costs the ceiling the reference family exists to
establish.

### The pilot gate

ONE deliberately small, THROWAWAY run — not a reusable phase-2 cell (the arm set is not
known until phase 1 ends, so reuse is circular). It runs AFTER the builds land, inbound-on
under `fifo`, on a provisional two-or-three-rule arm set, and answers three things at once:
do the pilot leads produce 10's criterion (a) YARD CONTENTION and (b) BINDING CUTS; where
does `INBOUND_FEE_THRESHOLD_DAYS` sit for nonzero, non-saturated overage (an analysis
re-report over the same run, since the fee derives late); and the `'all'`-window bench.

**Correction carried in:** 15's eight-trailer demonstration (`[1,2,6,5,0,4,7,3]`) shows
arrival order diverging from dispatch order, which is a PRECONDITION for contention, not
contention itself. BOTH of 10's criteria still need a real run.

**On failure: BOUNDED retuning** — at most a couple of attempts moving σ and the door count
— then a DECLARED STOP. If contention will not bind, that is not a knob to keep turning; it
is the finding that this model carries no structural inbound pressure at the series' scale,
invalidating the campaign's premise, and it is reported as such. Naming the exit now is
what stops it becoming an open-ended search. The pilot may also demand more SKUs rather
than more batches — the batches knob saturates every backlog level, so put-away and
receiving pressure show on the SKU knob.

### Depth, decision rule, presentation

**Depth: match the published series.** These results join a series stakeholders read
across; a per-campaign depth makes cross-experiment reading harder, and a depth chosen to
flatter an effect cannot be audited.

**The decision rule, PRE-REGISTERED here rather than chosen after the run.** A policy beats
FIFO when three things hold TOGETHER: its total-production-hours gain over the `fifo` cell
has a CI excluding zero under the MOVING-BLOCK bootstrap (per-batch series are
autocorrelated at lags 1–3, so an iid bootstrap under-reports every interval); its missed
share does not degrade (10: a policy winning on hours while service decays must be
surfaced, not averaged away); and its fee cost is reported BESIDE the result, never netted
into it (05's two-separate-scores rule). Standing exclusions unchanged: `futuresight` never
enters the recommendable set whatever it scores; `lifo` is a control whose job is to lose,
and a `lifo` win is evidence about the machinery, not about LIFO.

**`yard_overage_days` DOES earn a `headline` slot**, beside hours and missed share. The
campaign's question is "does space-aware inbound beat FIFO, AND AT WHAT FEE COST", so the
fee is half the decision and a reader who sees only hours at the decision point concludes
wrongly; three numbers fits `headline`'s charter. It stays a distinct number in its own
unit — never summed with hours, never a ratio against them. Structurally cheap: no new
family glob, and `HEADLINE_ORDER` takes new entries at the end.

### Cost, honestly

Experiment 8 (75 batches, 400k-SKU catalogue, 2 pairs × 2 channels × 34 arms × 2 cells =
272 arm-runs at 20 workers) reconstructs to ~78 arm-hours of batch-loop work — a ~3.9 h
ideal-packing floor — and ~500 GB. Per-arm cost spans 3.4 min (`store`/`fifo`) to 52.3 min
(`fulfillment`/`cluster_map`), and phase 2 selects the top five BY LABOR, which are
inferentially the expensive ones. Phase 1 (136 work units) plus phase 2 (480) therefore
lands near 1.1 TB and well north of twelve hours of simulation floor. Archive-as-you-go is
not optional; the grids are the trimming lever; 20 workers is an operator constraint, not a
tunable.

### Graduated

Three tickets plus the campaign:

- [Build the run-shape layer](18-build-the-run-shape-layer.md) — the fifth `Cell` field,
  the inbound family's deferred seams 3–4, and the selection artifact. Bundled because all
  three touch `SHAPE_SOURCES` and force a preflight canary pair: splitting pays the schema
  event three times and leaves states where the tree shape moved without its writers (the
  reasoning 07 used when it refused to split 17).
- [Build total production hours](19-build-total-production-hours.md) — the unload quantity
  and the put leg's first `work_events` read. A different pipeline; independent; parallel.
- [Extend the gain bundles](20-extend-the-gain-bundles.md) — capped at three families,
  necessarily blocked by phase 1.

The campaign itself stays a map item: pilot → phase 1 → selection → phase 2.
