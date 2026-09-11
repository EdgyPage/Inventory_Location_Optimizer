# Chain the supplier lead before the trailer

Type: task
Status: open

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
