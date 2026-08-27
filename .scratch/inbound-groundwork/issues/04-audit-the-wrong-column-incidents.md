# Audit the wrong-column incidents

Type: research
Status: resolved

## Question

Catalog every wrong-column-for-the-thing incident class this repo has hit, and every current
analysis-path read at risk of one. Sources: memories (`sim-time-unit-is-seconds-not-ms`,
`cut-is-a-level-not-a-flow`, `carryover-two-producers-one-key`, `a-count-is-not-a-claim`,
`one-clock-one-speed-one-config` on timestamps-read-as-spans, `runtime-metrics-is-the-deep-instrument`),
git history, and a sweep of `Optimization/metrics/`, `Optimization/Performance_Evaluations/`,
`Optimization/analyze_run.py` + the DDL constants in `Schema/`. Output: a table of columns x
semantic kind (stamp/span, level/flow, unit) with the reads that confuse them, written to
`.scratch/inbound-groundwork/assets/column-audit.md`.

## Answer

Full audit: [assets/column-audit.md](../assets/column-audit.md).

- **12 incident classes** cataloged, every one a silent failure: wrong unit/scale (the 1000x
  ms divisor, `5434605`), stamp-read-as-span (`399de2a`), level-summed-as-flow (the 101x
  `recv_cut` headline, `36f494f`), two-producers-one-key (500 units destroyed, `3a8b4ab`),
  zero-where-NULL-belongs (`5cfdfbe`), written-but-unreadable columns (`20571a1`), mixed
  units-of-account in one row (packs vs pieces, `de6a66e`), rate-vs-wrong-denominator
  (`thr_batch`, `857fc21`), count-without-denominator, plan-read-as-actuals (`de1ee6e`),
  incommensurable "seconds" across instruments, and id-space/label confusion — plus three
  near-classes (ledger scope, drained counters, declaration rot).
- **Column census** of all six DB families tagged stamp/span, level/flow, count/rate, unit,
  per-what. `batch_stats` is the epicenter (5 of 12 classes); `carryover.qty`'s kind depends
  on the `reason` VALUE in the same row — the strongest single argument for the layer.
- **8 at-risk read sites** ranked: `Diagnostics/receiving_report.py` (raw SQL at the 101x
  site, rule enforced by comment), `catalog/inventory.py` (no bind at all), `common/frames.py`
  getattr-zero defaults (absent column = plausible zero on published figures), and five more.
- **Key surprise:** the semantics that would have prevented every incident already exist —
  as DDL comment prose that provably rots within days (`ebe0c46`). The machinery to attach
  them to (`Requires` tuples, named-query logical columns, `Quantity.Source`/`era.QUANTITY_READS`)
  is already built and bridged in one direction; the layer is per-column tags on those hooks.
