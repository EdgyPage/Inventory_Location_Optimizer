---
status: accepted
date: 2026-09-08
---

# Demand is declared and the picking crew is derived from a joint first-time confidence

The calibrated era took the picking crew per channel as its one declared input and derived the
day's demand from it at a declared utilization, pricing the load on the units the shelf would
serve (demand times the first-pass fill rate). A 40-day reference run showed that nothing is lost
under the era: a pick the day cut or the shelf could not fill is re-offered the next day, and a
base-stock top-up with zero lead lands before that re-attempt, so the crew picks all of demand and
the derivation had under-staffed both channels by the fill rate. The same run showed that the day's
line count is one Gaussian draw with a declared coefficient of variation, so the work a day asks
for has an exact quantile and an exact expected overflow. We decided that demand per channel is the
declared input, stored in the sampler's own unit so it scales to any catalogue, and that the
picking crew, the line floor and the picking headroom are derived from one declared scalar: the
confidence that a pick is completed the first time, reached on its day and filled from the shelf.
The two sides multiply and split equally, the floor solved so the stamped fill clears its half,
the crew solved as the smallest integer whose expected cut share of units stays under the other
half, both closed forms over the chosen inventory and the declared day law. The crew is sized on
demanded units, never on served.

## Considered options

- **Keep pickers declared, fix the load** -- price on demanded units and keep utilization as the
  declared scalar. Correct, but the guarantee the user asked for is a statement about the crew,
  not the demand, and under this direction the rule would have shrunk the store's day by a third
  and the warehouse with it. Rejected.
- **Per-day confidence** -- the 95th-percentile day fits the shift. With a day cv of a third that
  is one and a half times the mean load, staffing to the peak by another name, which the charter's
  headroom clause forbids. Rejected: the guarantee is per pick, the expected cut share of units.
- **Labour only** -- leave the shelf at one line (fill 0.922) and put the whole 95% on the crew.
  Rejected by the user: a pick that reaches an empty shelf was not completed the first time.
- **Shelf first or crew first** -- declare one side's confidence and hand the other the
  remainder. Rejected: a second authored knob that silently moves the crew, the pattern that had
  just hidden the fill-rate defect.
- **Extend the rule to put-away and receiving** -- rejected: a receiving backlog on a heavy
  trailer is a legitimate scenario the inbound campaign exists to study; those crews keep their
  declared utilization and their backlog is reported.

## Consequences

- `--store-pickers` / `--ff-pickers` are refused under the era like the legacy crew flags; two
  demand flags replace them, and the record stamps demand `declared`, pickers and the floor
  `derived`, the confidence `declared`.
- The levels no longer depend on the crew, so the coverage fixed point loses its crew leg; the
  reference pair's declaration is chosen equal to the previously derived content so the warehouse
  and script family do not move, and only the crew does (about 32 store pickers at a mean
  utilization near 0.72, against 25 at a nominal 0.85).
- The equilibrium instrument judges labour as a cut share against a stamped expectation with the
  standing carry bounded and not trending, never as "every day drained"; supply is judged on its
  own flow against the solved floor's fill. Absolute results before this change are not
  comparable with results after it (a new era).
- The floor rises to about 1.27 lines on the reference catalogue, roughly 19% more stock and a
  proportionally larger warehouse.
- Two decisions made while building (2026-09-08). The floor is solved per section, since each
  section's fill is its own, so a run records one floor per channel and a rebuild re-declares
  each at its own; and a typed floor under the era is accepted only at or above the solved
  value and refused below it, because a smaller floor is a smaller promise than the confidence
  makes and raising it silently would be a second authored knob that moves the crew. Which keys
  are inputs follows the regime: the era records the picker keys and the picking utilization
  target as absent and derived, the flag-off regime records the demand and the confidence as
  absent, and each refuses the other's flags when typed.

## Amendments

**2026-09-12 -- the law the shelf half is priced through is validated, not corrected.** The
confidence splits equally between a shelf side and a crew side, and the shelf side is the stamped
first-pass fill: the floor is solved so that stamp clears its half. That stamp was under suspicion
for a month -- the fulfillment leaf realized a miss four times what it promised -- and the
suspicion is now resolved against the sampler rather than against the law (ADR-0006). The fill
law, and with it this ADR's pricing of the shelf half, stands unchanged; the reference pair reads
in the supply band on both leaves at +0.0033 fulfillment and +0.0023 store against a 0.020
tolerance. What the episode did show is that the record misprices its own prior-line probability
on the generator by about 18%, in opposite directions on the two channels; that error is recorded
and accepted, so this ADR's guarantee is a promise about the realized result, not about the
intermediate quantity it is computed through.

**The crew half is sampler-invariant, and that is a consequence of this decision.** Because the
picking crew is sized on DEMANDED units and demand is declared, the whole picking derivation --
the floors 1.3078 / 1.4994, the levels, the warehouse geometry and K = 23 -- came back
bit-identical across the v2-to-v3 sampler flip, which moved every batch sequence. Put-away and
receiving, which keep their declared utilization and size on the replenishment each delivered LINE
triggers, moved with it (60 to 64 putters, 22 to 23 receivers). A crew denominated in declared
units does not follow the sampler; one denominated in lines or packs does.

**A standing conditional: if the floor solve cannot clear the confidence, the confidence is
declared per channel.** Decided 2026-09-12 and not yet fired. The floor solve is capped at
`_MAX_FLOOR_LINES` (128 lines per SKU, `Optimization/simconfig/coverage.py`); should a section's
solve fail to clear `sqrt(c)` inside that cap, the answer is not to lower the cap or to soften the
guarantee but to declare the confidence PER CHANNEL -- store and fulfillment each stamping what
their own section can hold, the audit judging each against its own. This is an amendment rather
than a workaround because the finding behind it is that the two channels do not face the same kind
of demand: one section's achievable first-time confidence is not evidence about the other's.
