# 06 - the fill trial as a spec, ranked on pick labour, prototyped at 40k

Type: task
Status: claimed
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

## Built 2026-09-22 -- everything but the launch

**Blocked, not built:** the 40k prototype run itself.  Its gain cells need ticket 04's
cheaper evaluator (a fill at ratio 0.95 is the deepest queue the exact plan has been asked
to price), and the user is re-researching the evaluator's problem statement.  The bar's
second half (the two stock modes agree) waits on the user's decision on ticket 05 about what
the uniform stock mode means in a fill.  Opt arms run until then.

**The pick stage starts at ONE declared batch in every cell** (ticket 05 left this open).
`fill_batches` is now the fill's length cap -- twice the dispatch span plus ten days, rounded
up to a keyframe -- and every cell idles from the day its fill settled (`fill_settled`, which
IS policy-dependent and is recorded) to that batch.  So pick-stage batch ids align across the
cells of a run at one door count, and no reader that pairs arms by `batch_id` needs a re-base.

**The analysis reads the pick stage.**  `core.requests.pick_stage` drops batches before
`fill_batches` wherever an arm's rows are loaded (`frame`, `site_batch_frame`,
`SiteContext.leaf_batch_df`; `run_analysis` threads the start onto the site leaves); the three
what-if writers take it from the arm's sim_meta (`run_whatif_delta.fill_start`); the ranking
tool's batch rows drop it too.  Trailers, yard drains and shift days pass through: the yard
family reads the whole run, which on a fill IS the fill.  Every other run: start 0, the same
rows.  Verified on the toy fill: series windows cover batches 25-30 only.

**The spec.**  `inbound_fill` (nine cells, winner pair + rider, `FILL_RUN_DEFAULTS`: span 40,
ratio 0.95, 40-batch pick stage) ranked by `FILL_RANKING` -- pick labour, `ss_prod_hours`,
per-batch `task_makespan` for the measured floor, no census.  `_probe_fill_depth` (fifo +
lifo, no evaluator) measures how deep a 40k fill's yard stands and how long it runs, before
anything priced is launched into one.  Both declare `unpinned` (a new validator exemption:
a sentence, never a flag), because Q18 puts the prototype on the 40k catalogue by design;
ticket 08 carries the pin.

**The future-work diagnostic is the keyframe closed form, not a new column** -- an assumption
the user can overturn.  `pick_owed_exact_s` (`expected_pick_over`) is the arm's expected pick
seconds per unit over its REALISED placement, weighted by catalogue frequency, taken at every
keyframe; on a fill every placement is inbound-placed, so it is the quantity this ticket
describes.  The declared pick start lands on a keyframe, so a reading exists at the fill's
end (toy: batch 25, 105.9 s/unit) and at each pick-stage keyframe.  It prices height and one
sweep per aisle, not aisle choice (memory `expected-travel-closed-form-is-an-asymmetric-
check`), which is the asymmetry the ticket already names.  No schema move.

**Labour's own census, open.**  A policy that strands lines unservable picks less and reads
cheaper.  The write-up reads served units beside the rank; `FILL_RANKING` adjusts nothing.
