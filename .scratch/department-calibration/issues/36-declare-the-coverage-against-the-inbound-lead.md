# Declare the coverage against the inbound lead

Type: grilling
Status: resolved

Graduated 2026-09-10 from the inbound-optimization map's
[Verify the derived receiving crew under arrivals](../../inbound-optimization/issues/23-verify-the-derived-receiving-crew.md).
HITL: `grilling` + `domain-modeling`.

## Question

The era's coverage record sets `reorder_point = d_s * (lead_days + safety_days)` and stamps
`pipeline_qty = d_s * lead_days` (`Optimization/simconfig/coverage.py`), with `lead_days` read
from the catalogue's `lead_time_mean` -- **0.0 on every carton of the reference pair**. That is
right flag-off, where a reorder lands in the batch that placed it (in-transit 0 on every batch
of the reference run). Under the trailer pipeline the realized order-to-shelf lead is
**1.78 days** on fulfillment and 1.75 on store (Little's law over days 20-39: 48,598 units in
transit against 27,294 ordered/day), and the fulfillment leaf's supply level reads 0.148 against
a stamped 0.025, trending up -- the first era run with the yard on failed the supply clause on
every arm while every crew stayed in band. The store passes only because its implied coverage
is ~1,785 days per SKU.

How does the record declare the lead?

- **Derive it**: the trailer lead is a declared law (lognormal, median `INBOUND_LEAD_MINUTES`,
  sigma `INBOUND_LEAD_SPREAD`) plus a drain quantization (one drain per site day) plus a loading
  wait at the ordering site (trailer type, next-fit) -- a closed form over declared inputs, in
  the era's spirit (memory `no-calibration-simulations`), or at least a stamped expectation per
  channel. Which of the three terms the form must carry, and whether `safety_days` still means
  what it did once the lead has its own term.
- **Declare it**: a per-run `lead_days` input on the staffing record (era-only, five seams),
  provenance `assumed`, and the run's own Little lead reported against it by the audit.
- Either way the supply clause's expected level (`1 - fill_rate` under base stock with no
  pipeline, `coverage.py` ~217) must say what it expects WITH a pipeline, or the clause judges
  a lead-aware record against a lead-free expectation.

Out of this ticket: the pipeline's shape (loading, dispatch, lead law) -- the inbound map owns
it; the contention regime (inbound 25). The re-check is inbound 26.

## Answer

Resolved 2026-09-10 (HITL, two grilling rounds; every recommendation accepted, the user amending
round 1's Q1: the lead is a SKU attribute, not a site-level law). Two code traces preceded the
rounds; their findings come first because each moved a question.

### Facts established

- Under the pipeline the order-to-shelf lead is the trailer's lognormal transit rounded UP to
  the site-day grid: a reorder fires and dispatches inside one drain (loading costs no time --
  fire and depart are two steps of one `check_reorders`), `arrived_s = dispatched_s + lead_s` is
  observed only at the next drain, and door, unload and put-away cost zero under a slack yard.
  The closed form over declared inputs alone,

      lead_days = E[ceil(L / D)] = 1 + sum_{k>=1} (1 - Phi(ln(k D / m) / sigma))

  with `m = INBOUND_LEAD_MINUTES x 60`, `sigma = INBOUND_LEAD_SPREAD` and `D` the site day, reads
  **1.766 site days** at the pilot regime (480 min, 0.7, 8 h) against the run's Little read of
  1.78 (fulfillment) / 1.75 (store). The continuous mean (1.278 days) is the wrong number: the
  grid is what makes it 1.77. Exactly 1 day at spread 0; 1.51 / 1.60 / 2.17 at 0.3 / 0.5 / 1.0.
- `coverage.py:171` reads the catalogue's `lead_time_mean` -- a count of BATCHES
  (`LEAD_TIME_UNIT`) -- as days. Inert at 0, wrong at any other value. Nothing passes a lead
  into the fixed point; that per-SKU `getattr` is the only source.
- Order-up-to on inventory position is `Q + pipeline_qty`, position counting on-hand, queued and
  in-transit. Fulfillment is 100% floored, so the reorder point's lead term is INERT there and
  only the pipeline stamp and the fill form can act. `fill_rate` stamps `pipeline_units` and
  nothing reads it; the supply clause reads `1 - fill_rate` at a typed 0.02.
- Under the pipeline the SKU's lead is passed to `TrailerTransit.dispatch` and ignored
  (`Inbound/transit.py:125-138`). No Little's-law lead exists anywhere: `mean_in_transit_pieces`
  is stamped per run and never divided by a flow.

### Decisions

1. **The lead is a SKU attribute** (the user's amendment): the catalogue's `lead_time_mean` is
   its SUPPLIER lead, order to ready-to-ship. A site-level lead law declared on the era was
   REJECTED -- the lead belongs on the SKU, and whether per-SKU attributes bias comparisons is a
   question about generation and sampling, not about the record (decision 9).
2. **Two additive stages.** An order waits its SKU's supplier lead at the ordering site, then
   loads onto the next trailer, whose transit is the inbound law: the existing batch transit
   FEEDS the trailer transit instead of the trailer replacing it. Per SKU,
   `lead_days_s = attr_s + E[ceil(L/D)]` with the pipeline on, `attr_s` alone with it off; on
   the reference catalogue (every attribute 0) the stamp is 1.766 on and 0 off. Rejected: the
   attribute as the whole lead with the trailer's arrival derived from its cargo -- a rewrite of
   the pipeline's shape and of the lead-distribution decision (inbound 02/15).
3. **The closed form carries transit and the grid only.** The record stamps the UNCONSTRAINED
   pipeline lead; when the yard binds, the excess over the stamp is the campaign's measured
   effect, as a picking utilization below its band is an arm's travel saving (04 decision 7). A
   queueing term would price the thing the campaign exists to measure.
4. **The attribute converts at the record**: the coverage form reads `attr_s / releases_per_day`
   as days (one batch is one day under the era). The column and the generator stay in batches;
   re-denominating the catalogue belongs to the generation effort (decision 9), not to a
   coverage ticket.
5. **The first-time promise holds AT the lead.** The fill closed form takes each SKU's lead and
   the floor solve absorbs it under the same declared confidence: the shelf a line meets is
   `Q + pipeline - in-transit`, and for a floored SKU the loss is the chance of a second line
   during the first's lead. Rejected: keeping the floor at lead 0 and stamping a degraded
   expectation -- the 0.95 the user declared would silently read ~0.83 on fulfillment.
6. **The lead-aware fill uses the line share** (`freq / sum freq`), consistent with every other
   closed form on the record. Whether the fog's affinity-aware per-SKU rate must graduate is
   decided by 26's residual: this is the first closed form that needs a per-SKU line COUNT
   rather than a section sum, and the fog entry now says so.
7. **`safety_days` keeps its meaning** -- days of demand above the lead on the reorder point --
   and stays `assumed` 2; it bites only above the floor, which on the reference pair is no SKU.
8. **A lead the pipeline would ignore is a refusal.** Under the era with a trailer type, a
   catalogue with a nonzero `lead_time_mean` refuses at setup until inbound 27 chains it
   (decision 2) -- today the pipeline discards it, the silent no-op the config doctrine refuses.
   The non-era path keeps reading it as batches, byte-identically.
9. **Out of scope, recorded on the map:** sampling and inventory generation as an effort of its
   own -- the user's hypothesis that SKUs whose generated attributes let them edge out better
   pick times through an equilibrium of emergent properties may bias every comparison, the lead
   attribute being one more such attribute -- and the catalogue's lead unit with it.
10. **The audit reports three things**: the realized order-to-shelf lead per leaf (mean
    in-transit pieces over mean units ordered per day across the window) as a REPORTED level
    beside the stamped lead, no band; the supply clause's band unchanged, 0.02 around
    `1 - fill(stamped lead)`; and `1 - fill(realized lead)` as the EXPLAINED level, so a binding
    yard reads as supply moved by the yard's detention and a model error as a gap the realized
    lead cannot explain. This is what lets 26 require both "supply in band" and "the yard
    binds".
11. **The funnel's two phases share one record: phase 1 runs with the standing yard on under
    `fifo`.** With decision 2 an inbound-off run stamps lead 0 and an inbound-on run 1.766, so
    their floors and warehouses differ and 24's calibration pin would refuse phase 2. The yard
    is slack under the derived crew, so phase 1 with it on costs little. Recorded as a comment
    on inbound 24 (the pin's owner); a pin identity that excludes the lead-dependent part was
    declined.

### Consequences

- The inbound regime's median and spread now move the record through the closed form (floor,
  levels, warehouse), so inbound 25 must pick the regime BEFORE phase 1 -- and its stock cost is
  readable without a run (comment on 25). Doors do not enter the form (decision 3): they are the
  record-neutral contention knob.
- Solving the floor at the lead raises fulfillment's stock and warehouse above the lead-zero
  reference; the store's ~1,785-day coverage does not notice. The next derivation is a new era
  for every inbound-on number.

**Glossary:** `CONTEXT.md` -- **Supplier lead** and **Order-to-shelf lead** added; **Lead** and
**Stock coverage** amended, 2026-09-10. No ADR: decisions 2 and 11 are reversible builds, and
decision 1 is a scoping choice the Out-of-scope entry records.

### Graduated

- [Build the lead-aware coverage record](37-build-the-lead-aware-coverage-record.md) (task, AFK,
  this map) -- decisions 2-8 and 10, the record side.
- [Chain the supplier lead before the trailer](../../inbound-optimization/issues/27-chain-the-supplier-lead-before-the-trailer.md)
  (task, AFK, the inbound map) -- decision 2's dispatch side.
- Inbound 26's blocking edge on this ticket is replaced by edges on both.
