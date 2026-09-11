# Chain the supplier lead before the trailer

Type: task
Status: claimed

Graduated 2026-09-10 from department-calibration's
[Declare the coverage against the inbound lead](../../department-calibration/issues/36-declare-the-coverage-against-the-inbound-lead.md),
decision 2. AFK build. Skills: `codebase-design`; `test-developer`. Blocks
[Re-verify the gate under the lead-aware record](26-reverify-the-gate-under-the-lead-aware-record.md)
together with department-calibration 37 (the record side).

## Question

Not a decision: the pipeline side of 36's decision 2. Today `TrailerTransit.dispatch` receives
the SKU's `lead` (batches, from the catalogue's `lead_time_mean`) and discards it
(`Inbound/transit.py:125-138`): under the pipeline every reorder loads the instant it fires. 36
made the SKU's lead its SUPPLIER lead -- order to ready-to-ship, at the ordering site -- and the
trailer's draw the shared transport. The two are additive stages, and the record (37) prices
them as `attr_s + transit_days`.

What lands: an order fired with a nonzero supplier lead waits that many batches at the ordering
site and loads onto the open trailer only when it releases -- the batch-denominated queue the
flag-off `BatchTransit` already runs (advance one per `check_reorders`, release at zero) is the
model, sitting in front of the trailer's next-fit loading. Lead 0 loads the instant it fires, as
today. The trailer's own lead law is untouched. The phase order inside `check_reorders` (tick,
reclaim, advance, fire, release, receive, put-drain) stays fixed.

Constraints:

- **Byte-identical at lead 0.** On `catalogue_reference_lt0` every trailer, every stamp and
  every drain is identical to today's -- proven drain by drain, the way 15 proved the
  spread-zero path, never as aggregates (memory `lockstep-tests-compare-aggregates-only`).
- The non-era flag-off path is untouched.
- Loading stays FIFO next-fit over RELEASED orders; two orders releasing in the same batch load
  in their fire order (a deterministic tiebreak, recorded in the docstring).
- When this lands, department-calibration 37's interim refusal (a nonzero catalogue lead under
  the era with a trailer type) is lifted; the record then reads `attr_s + transit_days` as 37
  built it.
- The `positive_lead_skus` census the fragmentation chain records (priced at lead 0) gains a
  real population on any `lt>0` catalogue; report it, do not change it here.

Acceptance: a two-SKU scenario with supplier leads 0 and 2 batches dispatches the first at fire
time and the second two drains later on a trailer of its own or the open one; the lockstep proof
above; and the reference pair's gate run (26) is unchanged by this ticket, since every attribute
there is 0.

## Comments

2026-09-10, from department-calibration
[Build the lead-aware coverage record](../../department-calibration/issues/37-build-the-lead-aware-coverage-record.md):
the record side is built. The guard this ticket lifts is `era_coverage.refuse_discarded_lead`
(`Optimization/simdriver/era_coverage.py`), which refuses `round(lead_time_mean) >= 1` under the
era with a trailer type and names this ticket; delete it (and its tests in
`Tests/unit/test_lead_aware_coverage.py`: `test_a_supplier_lead_the_pipeline_would_discard_is_refused_under_the_era_only`
and the `lt1` build test's first half) when the supplier lead queues before the trailer. The
record already prices `attr_s + transit_days` per SKU (`coverage.sku_lead_days`) and the fill's
grid day is `round(attr x lead_unit_days) + k` (`coverage._served_under_lead`) -- one batch a day
under the era -- so the chained pipeline must realize exactly that: `round(lead_time_mean)`
batches at the ordering site, then the trailer's own draw. No record change is owed here.

2026-09-10, from resolving
[Decide the contention regime under the derived crew](25-decide-the-contention-regime-under-the-derived-crew.md):
still on the frontier and still worth doing first. The supplier-lead queue sits in front of the
trailer's loading regardless of whose orders share the trailer, so the build is the same under
the site-dock coupling (map, Out of scope); the one seam the coupling later rewrites is WHOSE
released orders `TrailerTransit.dispatch` loads (both channels' instead of one leaf's). Keep
the queue's contract free of the manager so it survives that move.
