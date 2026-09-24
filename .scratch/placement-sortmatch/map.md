Status: in-progress

# Sort-match placement -- a faster inbound algorithm, researched in a feedback loop

## Destination

Replace the quadratic inbound placement work with algorithms that walk two sorted lists --
packs by expected pick cost (highest first), empty bins by location cost (lowest first) --
matched by a shared index (the rearrangement inequality), and extend every idea that
survives to the other placement assignment functions.

Opened 2026-09-24 from the user's request: "I feel there is a better way to go through
essentially two sorted lists (the trailer volume and the empty bins) and place them in
optimal ranked slots. Perhaps they can share indexes ie the packed freight with the highest
expected pick cost must go in the lowest cost spot in a warehouse bin." Then: "Extend any
successful optimization idea to any of the placement assignment functions as well."

Plan: `~/.claude/plans/tender-roaming-dream.md` (S00-S09).

## The loop

Every session is a whiteboard file with fixed sections: Question, Hypothesis, Derivation,
**Prediction (committed before measuring)**, Measurement, Residual, Diagnosis, Revision,
Next.  The diagnosis chooses the next design.  Three revisions without convergence ->
stop and ask the user.  Never fit on the data that verifies.

## What is already known (the starting facts)

- Billing cost per (SKU, bin): C = alpha_s D_b + beta_s M_b; one sort is exact inside a
  (BinKey, height bracket) cell and everywhere in fulfillment (M = 1).
- Existing sort-match code: the gain merge rung (`Inbound/gain.py` `_place_merge`, used by
  `tmin`/`tmax`), `inventory_optimal._optimal_assign`, exact LAP `_optimal_work_assign`
  (capped 1,200 units), `map` (ideal rank -> nearest free bin).
- Whole-trailer order is worth ~0 (aisle-churn S12/S16/S17); the order prize lives inside
  trailers and in pairing velocity with bin cost.
- Real puts run FIFO +- a few: an index match must work online.

## Sessions

| S | topic | status |
|---|---|---|
| S00 | lab anchors | done -- recorded fifo = uniform draw; occupancy model 0 violations (S02-S05) |
| S01 | exact replay evaluator for merge bundles | done -- bit-identical, 15x at campaign shape (c6e3ec54) |
| S02 | A1 billing-consistent keys | done -- 99% of the exact optimum's saving (static) |
| S03 | A2 exact per-group LAP | survives the rule, not wired: +0.16 pts static at 3,500x cost |
| S04 | A3a online quantile | refuted as registered; scaled quantile rescues it (S02-S05) |
| S05 | aisle ceiling | refuted in sortmatch's favour: lowers the busiest aisle |
| S06 | wiring `rank_sortmatch` | done -- `_toy_priced` IDENTICAL; prepare() for FIFO queues |
| S07 | 40k simulation | done -- store 0.8-1.0% below rank_cartlabor at k10; ff ties rank_minlabor; put-away 2.2x faster |
| S08 | 400k wall | open |
| S09 | transfers to the other assignment functions | early-stop refuted for both balance pools (S09) |

## Decisions so far

- **Two sorted lists, as an arm** (S06-S07): `rank_sortmatch` -- packs by lifetime pick
  work onto bins by D + hbar M, the pairing fixed per group so FIFO service keeps it.
  Store pick labour 0.8-1.0% below the phase-1 winner at 10x demand (significant), a tie
  in fulfillment, the put-away drain 2.2x faster and the ranking work 10-15x cheaper.
- **Frontier replay** (S01, c6e3ec54): merge plans priced 15x faster, bit-identical.
- **The lab overstates magnitudes, not rankings**: routing keeps ~25% of the separable
  store gain and co-location cancels the fulfillment one (S07 residual).
- **Early stopping does not transfer** to the balance pools: their objectives keep every
  aisle competitive (S09).  The only lever left there is vectorising the scan -- the
  user's call.
- Replacing rank_cartlabor / rank_minlabor, or retiring `plan_order`, remains the user's
  decision.

## Fog

- Whether the n-lowest-per-bracket reduction is exact once put travel chooses WHICH bins.
- Whether sort-match concentrates hot packs into few aisles (the one-picker-per-aisle
  ceiling, k* = S / max W_a).
- Whether the production `_candidates` index can carry a live sorted order without moving
  a tie-break.
