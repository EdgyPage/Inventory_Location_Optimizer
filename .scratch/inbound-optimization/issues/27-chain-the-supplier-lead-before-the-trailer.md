# Chain the supplier lead before the trailer

Type: task
Status: resolved

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

## Answer

BUILT 2026-09-10 (AFK), commit `8b6796b4`. Decision 2's dispatch side is code: the flag-off batch countdown now
sits in FRONT of the trailer's loading, inside `TrailerTransit` itself, so the manager's phase
wrappers, its phase order and its ledger needed zero edits.

**What landed** (`Inbound/transit.py`):

- `TrailerTransit.dispatch(sku, qty, lead, ...)`: a positive `lead` (batches, as
  `_fire_reorders` rounds `lead_time_mean`) joins `_at_site` -- `[sku, qty, remaining,
  unit_volume]` in fire order; lead 0 loads this instant through `_load`, which is the old
  `dispatch` body verbatim.
- `advance()` ticks every site entry one batch (it used to be a no-op; the trailer stage is
  still untouched by a tick).
- `_release_site(now_s)` loads every entry with `remaining <= 0`, in queue order, and is called
  at the top of `dispatch` and at the top of both `release()` bodies (`TrailerTransit` and
  `YardTransit`). A due order therefore loads whether or not anything fired that batch, and
  always AHEAD of that batch's lead-0 newcomer -- loading order is fire order, the tiebreak
  `BatchTransit.release` has by construction (checked against it in the tests).
- `dispatched_s` is the epoch of the drain the order LOADED in, so the trailer's lead starts
  after the supplier's and the two add -- `attr_s + transit_days`, exactly as 37 prices it.
- `depth`, `merchandise()` and `snapshot()` count the site queue in both classes (rows
  `(sku, qty, remaining_batches)` first): the ledger credited the order at fire, so the level
  must include it. A negative remainder is a backlog, never clamped, as `BatchTransit` leaves it.
- The interim guard `era_coverage.refuse_discarded_lead` and its call are deleted (the flow
  anchor and catalog entry with them); the record's `attr_s + transit_days` is now what the run
  realizes. `positive_lead_skus` is untouched, as the ticket said.

**Byte-identical at lead 0, proven drain by drain** (`Tests/unit/test_supplier_lead_queue.py`,
13 tests): a lockstep of a manager over the chained transit against one over a stand-in that
dispatches the pre-chain way (`_load` IS the old body), for v1 and for the standing yard with a
0.7 spread so arrivals cross drains and every stamp is exercised, comparing the put-queue
stream, the ledgers, the censuses, the snapshot rows and the finished + censored trailer stamps
per drain; AND a SHA-256 digest of that whole per-drain record computed on `6eaf30fc` (the last
commit without the queue) and pinned -- so the claim is against the archive's code, not only a
stand-in. Also pinned: the two-SKU acceptance through the REAL `check_reorders` (supplier leads
0 and 2 read off `lead_time_mean`: lands on drain 0 and drain 2, identical per drain to the
`BatchTransit` manager), the fire-order tiebreak, dispatched-at-the-load-drain, the census, the
`-1` backlog, and that lead 0 never enters the queue. `test_lead_aware_coverage.py`: the refusal
test is gone and the `lt1` sibling now BUILDS under the era with a trailer, declaring at
`1.0 + 1.766` with a floor above the attribute-alone build. Neighbouring suites (trailer
pipeline, lead distribution, standing yard, space timeline, yard metrics, gain plan, reorder
phases/queue/accounting, fragmentation, receiving and equilibrium params): 237 passed;
lead-aware coverage: 20 passed.

**Deviations from the ticket: none.** One choice worth recording: the flush lives in the
transit, not in a manager phase -- the seam contract (`dispatch` / `advance` / `release`) was
already wide enough, so `check_reorders` is untouched and the site-dock coupling's later rewrite
of WHOSE released orders load (comment above) touches `_load`'s caller only; the queue's
contract knows nothing of the manager. The reference pair's gate (26) is unchanged by
construction: every attribute there is 0, and the digest says so.

**What this lifts:** department-calibration 37's interim refusal. **What it does not change:**
the fragmentation chain still prices a lead-carrying SKU's transient at lead 0 (the
`positive_lead_skus` census now reports a real population on any `lt>0` catalogue); that is
the department-calibration map's affinity/lead fog, not this ticket's.

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
