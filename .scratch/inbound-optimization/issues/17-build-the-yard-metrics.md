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
