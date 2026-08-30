# Define the yard metrics and their semantics tags

Type: grilling
Status: open
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
