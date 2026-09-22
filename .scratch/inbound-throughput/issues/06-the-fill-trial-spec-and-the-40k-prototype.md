# 06 - the fill trial as a spec, ranked on pick labour, prototyped at 40k

Type: task
Status: open
Blocked by: 05

The fill trial's question: which unloading policy places stock best when everything must
land. Its answer is read on the pick stage. Decisions: Q17 (pick labour ranks, future work
diagnoses), Q18 (40k first), Q23 (nine cells), Q26 (the bar), Q24 (no hand-off).

## The spec

`inbound_fill` in `whatif_config`: `inbound_unload`'s axis minus `inb_off` and `gmyopic_k8`
-- fifo (reference), lifo, gmyopic, gforecast, ggated at H 0.25/0.50/1.00, fsight w5 and
wall -- nine cells; the winner pair plus the rider as control (Q4); both stock modes. Run
defaults: the fill mode on, span 40 site days, ratio 0.95, pick stage 40 batches, the era.
The prototype binds the 40k perf catalogue already in the profiles tree; the confirmation
(ticket 08) binds the reference catalogue with the same pin discipline as phase 2.

## The score

- **Pick labour over the pick stage ranks.** Phase 1's own quantity; comparable here because
  the placements genuinely differ (in the era run they differed by one pack in N+1). The
  ranking tool takes the metric from the spec (ticket 01's change), so `run_unload_ranking`
  ranks these cells on it with the same tie-break, floor and verdicts.
- **Realised future work diagnoses.** Per placed unit, the at-location cost through
  `cost_model.per_pick` times expected visits from CATALOGUE frequency (not the window's
  planned lines: that basis is what made pick-owed blind), summed over the stock that
  inbound placed. Read once at the fill's end and at each keyframe of the pick stage; a new
  `batch_stats` column through the schema pipeline (`--sync` before, `--accept` after; a new
  column needs `frames._bdf` and `series.py` too -- `a-new-column-needs-two-more-lists-than-
  the-schema`). It is the policy's own objective on the realised placement, so the
  evaluator's estimate and it disagreeing is the informative direction; agreement is weak
  evidence (the closed-form pattern, `expected-travel-closed-form-is-an-asymmetric-check`).
  No floor line, never compared across runs.

## The bar (Q26)

`discriminating` true on the winner pair at a floor re-declared from the pick stage's own
paired per-batch noise, AND the two stock modes order the cells the same way. Either failing
is a result: it says the fill's answer is a property of the stock mode, or that the policies
do not separate even when every bin is theirs to choose -- and the map's fog already names
the second reading (at 40k the pick stage may again be a placement-rule question wearing an
unloading name). Record the numbers either way; the confirmation runs only on a pass.

## What it does not do

Hand anything to phase 3 (Q24). The chosen cells are published as this experiment's result;
`PHASE3_UNLOAD` stays phase 2's.
