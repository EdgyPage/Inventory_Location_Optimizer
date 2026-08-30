# Define the yard metrics and their semantics tags

Type: grilling
Status: resolved
Blocked by: 01

## Question

Define the measurement surface for the yard model, semantics-first (every new column arrives
tagged — the no-remainder gate is already enforced). The charter fixes the fee proxy:
per-trailer overage = max(0, yard_days − threshold), threshold a knob (name it —
`INBOUND_YARD_FEE_THRESHOLD_DAYS` shape), a span-derived reported metric never converted to
dollars and never mixed into labor seconds. Decide the rest: which quantities exist (yard
depth over time — a LEVEL; per-trailer yard span and unload latency — SPANs; overage-days —
derived; door utilization — a SHARE of what stated whole; over-threshold trailer count — a
FLOW), each with kind/unit/grain/clock tags per `Schema/semantics.py`; which table family
carries them and whether a new family is needed (schema changes ride the pipeline —
`schema-maintainer` owns the contract); and which analysis/dossier views report them
(declared quantities, no ad-hoc graphs — the `route-reviewer-finding` discipline).

## Comments

2026-08-29, from resolving "Design the space timeline" (03): a candidate metric surfaced —
ON-SHELF AVAILABILITY (missed orders: the `unmet` / `_shortfall` counts the sim already
produces), floated as the inbound objective in "Define the inbound objective" (10). If 10
adopts it, its columns land here (a FLOW of missed units, plus any share against a stated
demand whole); coordinate with 10 before resolving.

2026-08-29, from resolving "Define the inbound objective" (10): availability was NOT adopted
as the objective (future work won) but IS adopted as a reported axis — its columns land
here. Missed pieces = the `unpicked_unstocked` + `unpicked_unavailable` FLOWs (account
PIECES, per batch; never `unpicked_daycut`, a labor artifact), plus missed share, a SHARE of
the stated whole `items_demanded`. Also wanted here: the regime measurables backing the
lead-distribution acceptance criteria (02) — yard contention (standing trailers vs free
doors at drain start, a LEVEL) and binding cuts (drains ending with unserved standing
trailers or partial unloads, per-drain FLOWs), both derivable from `YardTransit.stamps` and
staged remainders.

2026-08-29, from resolving "Name the policy arms and their knobs" (05): the threshold knob
is named — `INBOUND_FEE_THRESHOLD_DAYS` (float, days; supersedes this question's
`INBOUND_YARD_FEE_THRESHOLD_DAYS` sketch), ONE knob shared by the fee columns here and the
`gain_gated` arm's urgency test (built by ticket 14). Its default value is this ticket's
and 02's business — it should sit where FIFO runs under the chosen lead distribution show
a nonzero but non-saturated overage, or the fee axis reports nothing. Also relevant to the
"which quantities" list: the roster includes `lifo` as an adversarial control, so the fee
columns should read sensibly at its extreme (overage concentrated, not clipped).

## Answer

Resolved 2026-08-30 by grilling (two rounds, all ten decisions user-confirmed).

**1. The fee accrues over the DETENTION SPAN** — arrived→emptied, the whole time the
carrier's trailer is held on site including its time at a door. Three named spans (glossary
updated in root `CONTEXT.md`): *yard wait* (arrived→staged), *door span* (staged→emptied),
*detention span* (their sum, the fee substrate). Detention is monotone under any policy —
staging a trailer early and unloading it slowly sheds nothing.

**2. Edge trailers get rows.** `staged_s` NULL means never staged (a discarded empty-plan
trailer); `emptied_s` NULL means still standing at run end, detention CENSORED at the
run-end clock. Persistence flushes the standing yard and staged remainders into the table at
run end, and a `status` LABEL (`done` / `discarded` / `standing`) rides every row so totals
can include censored detention while the censored share stays visible — under `lifo` the
concentrated overage sits exactly in those rows ("concentrated, not clipped", per 05's
comment).

**3. Materialization: two new sim_db tables, no new family.**

- `yard_trailers` (grain: one finished-or-flushed trailer): `seq` LABEL(id);
  `arrived_s` / `staged_s` / `emptied_s` STAMPs (unit s, sim clock, null-meanings above);
  `status` LABEL. Source: `YardTransit.stamps` plus the run-end flush.
- `yard_drains` (grain: one drain — 1:1 with batches, so inbound-off runs have zero rows
  rather than NULL-padded `batch_stats` columns): `batch` LABEL(id, pairs with
  `batch_stats`); `yard_start` / `free_doors_start` LEVELs (the contention pair, read from
  `freeze_ctx` — 02's acceptance measurable); `yard_end` / `staged_remainder_end` LEVELs
  (the binding-cut pair). *Binding cut* itself is a derived per-drain indicator (either
  end-level > 0), a COUNT when summed across drains — the LEVELs never sum.

Both tables are always created (the declared shape stays flag-independent); rows exist only
when the standing yard is on; they ride the existing sim_db family through the schema
pipeline (`--sync`/`--accept`, semantics declared in `sim_semantics.py` in the same commit —
the completeness ratchet enforces it). Rejected alternative: deriving the drain rows at
analysis from stamps + batch epochs — fails on censoring (standing trailers have no stamps
until run end) and re-derives what `freeze_ctx` already knows exactly.

**4. Derive late — raw stamps only.** No derived span/overage columns in the DB. Spans,
detention days and overage are declared analysis quantities computed via named queries with
the run's RECORDED threshold (run-spec restored; analysis reads resolve HEAD-first per the
ingest rule). Because only `gain_gated`'s urgency gate consumes the threshold in-sim, a
finished run's fee axis is re-reportable under any other threshold without re-simulating.
Seconds→days conversion happens at the analysis units seam only (the one-divisor rule).

**5. Door utilization** = Σ door spans ÷ (doors × the arm's recorded makespan) — a SHARE
whose stated whole is door-seconds available over the arm's span, computed at analysis from
`yard_trailers`. The drain-sampled variant stays undeclared (recoverable from the contention
LEVELs at no cost).

**6. `INBOUND_FEE_THRESHOLD_DAYS` defaults to 2.0** — a placeholder (median lead ≈ one
working day, σ ≈ 0.7 → past ~2 days is genuinely late), inert until the standing yard is on.
The calibration RULE rides the knob's docstring: the default sits where FIFO under the
chosen leads shows nonzero, non-saturated overage. The calibration probe itself belongs to
the funnel (comment posted on 08) — and by point 4 it is an analysis re-report over one FIFO
run, not a re-run.

**7. The availability axis is analysis-only.** *Missed pieces* = `carryover.qty` under
`reason ∈ {unpicked_unstocked, unpicked_unavailable}` (FLOW, pieces, direction lower);
*missed share* = SHARE of `batch_stats.items_demanded`. Never `unpicked_daycut` (a labor
artifact). No schema change; both quantities land in the `throughput` figure family,
ungated (they are about demand service, meaningful inbound-off).

**8. A new leaf figure family: `yard`** — charter "did the yard bind, and what did the
inbound policy cost in trailer-days". The tenth family, accepting the `figures_yard_pngs`
run-tree contract glob (a schema change riding the pipeline); the folder is legitimately
empty inbound-off. Whether the fee number ALSO earns a `headline` slot is the funnel's
presentation call (comment on 08). Fact surfaced while resolving: the receiving surface
(`batch_stats.recv_*`) is carried into frames and one diagnostics report but is declared by
ZERO quantities today — this family is inbound's first figure/view coverage.

**9. The honesty split.** Declared Quantities (capability key `'yard'` — forced anyway,
since new tables sit outside the guaranteed surface; all stance `level`):
`yard_overage_days` (days, lower — THE fee axis), `yard_over_threshold_trailers` (count,
lower), `yard_detention_days` (mean, days, lower; plus a fixed-mark distribution figure so
`lifo`'s concentration stays visible), `yard_depth` (the LEVEL series over drains,
trailers, lower — rendered in the `yard` family via the series-elsewhere mechanism, not
`trajectories`), `binding_cuts` (count, lower — 02's acceptance criterion reads the FIFO
arm's ABSOLUTE value, not the ranking). Inspection read-outs carry NO Quantity and no fake
direction (`DIRECTIONS` admits only lower/higher): door utilization, the contention pair,
and the censored-trailer share land in a yard scorecard/table evaluation with
`SEMANTIC_USES`, the per-run-tables precedent. New frame kinds (trailer, drain) extend
`FRAME_TABLE` at build time.

**10. Graduation.** One build ticket —
[Build the yard metrics](17-build-the-yard-metrics.md) — carries the whole surface in one
session against one schema change: the seven pipeline steps (DDL + declared shape, family
vintage, writer stamp, semantics tags, `Requires` optional-fill, named queries,
quantities/family/evaluations) plus the run-end flush and the threshold default. Splitting
it would leave the DB shape moved with its readers unbuilt. The knob itself is shared with
the arms build (14) — whichever lands first creates it at 2.0; CLI flag and run-spec
recording stay deferred to the first sweep, per the family precedent.
