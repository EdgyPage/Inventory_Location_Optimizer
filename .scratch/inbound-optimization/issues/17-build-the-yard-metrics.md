# Build the yard metrics

Type: task
Status: open
Blocked by: 07, 09

## Question

Build everything [Define the yard metrics](07-define-the-yard-metrics.md) decided — one
session, one schema change. The raw material (`YardTransit.stamps`, `freeze_ctx`) exists
since the standing-yard build (09).

- **The two sim_db tables.** `yard_trailers` (grain: trailer): `seq` LABEL(id),
  `arrived_s`/`staged_s`/`emptied_s` STAMPs (s, sim clock), `status` LABEL
  (`done`/`discarded`/`standing`); null-meanings — `staged_s` NULL = never staged,
  `emptied_s` NULL = standing at run end (censored detention). Includes the run-end flush
  of the standing yard and staged remainders so censored trailers get rows.
  `yard_drains` (grain: drain, 1:1 with batches): `batch` LABEL(id, pairs with
  `batch_stats`), `yard_start`/`free_doors_start`/`yard_end`/`staged_remainder_end` LEVELs.
  Both always created; rows only when the standing yard is on.
- **The seven pipeline steps**: DDL + declared shape; family vintage (append the outgoing
  id, `--sync`/`--accept` — `schema-maintainer` owns the contract); the writer stamp
  already rides `create_run`; semantics tags in `sim_semantics.py` in the SAME commit (the
  completeness ratchet fails otherwise); `Requires` optional-fill (new columns are outside
  the guaranteed surface by definition); named queries beside the family (no consumer SQL);
  the `'yard'` `SIM_CAPABILITIES` key.
- **The report surface**: the tenth figure family `yard` (leaf scope, charter "did the yard
  bind, and what did the inbound policy cost in trailer-days") plus its run-tree contract
  glob; `FRAME_TABLE` extensions (trailer, drain); the five declared Quantities —
  `yard_overage_days`, `yard_over_threshold_trailers`, `yard_detention_days` (+ its
  fixed-mark distribution figure), `yard_depth` (series-elsewhere), `binding_cuts` — all
  capability-gated `'yard'`, stance level, direction lower; the no-direction scorecard
  evaluation (door utilization, the contention pair, the censored share) via
  `SEMANTIC_USES`; and the two ungated availability quantities in `throughput` —
  `missed_pieces` (carryover reasons `unpicked_unstocked` + `unpicked_unavailable`, never
  `unpicked_daycut`) and `missed_share` (of `items_demanded`).
- **Derivation altitude is fixed**: raw stamps only in the DB; spans, detention days,
  overage, and utilization derive at analysis with the run's recorded
  `INBOUND_FEE_THRESHOLD_DAYS` (HEAD-default fallback when a spec predates recording);
  seconds→days at the analysis units seam only.
- **The knob**: `INBOUND_FEE_THRESHOLD_DAYS = 2.0` (shared with the arms build, 14 —
  whichever lands first creates it), calibration rule in its docstring; CLI flag and
  run-spec recording deferred to the first sweep, per the family precedent.

## Comments

2026-08-31, from resolving "Design the phased funnel" (08): the deferred run-spec recording
above now has an owner and a deadline. 08 decided the first sweep, and the wiring graduated
as [Build the run-shape layer](18-build-the-run-shape-layer.md), which records
`INBOUND_FEE_THRESHOLD_DAYS` and restores it at BOTH sites (`_apply_run_spec` and
`run_analysis._apply_run_shape` — separate functions, and today neither touches inbound).

Worth stating plainly because this ticket's derive-late decision depends on it: until 18
lands, a fee re-report resolves the threshold through the HEAD-default fallback named
above, which means it silently uses **this checkout's** default rather than the run's. The
fallback is correct for runs that predate recording; the campaign must never exercise it,
so 18 is a prerequisite of phase 1 rather than of phase 2.

Two items 08 settled that land on this build's surface: `yard_overage_days` DOES earn a
`headline` slot beside hours and missed share (the campaign's question is "and at what fee
cost", so the fee is half the decision) — a distinct number in its own unit, never summed
with hours and never a ratio against them, added at the END of `HEADLINE_ORDER` alongside
the entries from [Build total production hours](19-build-total-production-hours.md). And
`binding_cuts` is load-bearing earlier than expected: it is one of the two acceptance
criteria for the pilot gate, read as an ABSOLUTE value under `fifo`, so a campaign cannot
start until this build reports it.
