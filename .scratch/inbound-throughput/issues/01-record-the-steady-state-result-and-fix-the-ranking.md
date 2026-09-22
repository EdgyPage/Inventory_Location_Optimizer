# 01 - the steady-state campaign is a result: record it, and fix the two things the ranking got wrong

Type: task
Status: resolved

Phase 2 as run (`comparison_whatif_20260920_150203`, 11 cells, 44 units, finished
2026-09-21 06:55) did not fail to rank the unloading policies. It found that at this site's
demand, unloading order cannot move placement, and the numbers say why (map Notes: 91% of the
store's inbound units are never asked for inside the window; the score moves by 1/(N+1) of one
line per inbound pack). The user's decision (Q14 (c)) is that this stands on the record as a
finding, beside the fill trial that asks the other question.

## What to write

1. **The phase-2 campaign map's result section**: the finding in the run's own numbers, the
   availability caveat, and the pointer to this map for what replaced the question. The
   three caveats the predecessor map carried (fulfillment-weighted; `gain_gated`'s H grid is
   fulfillment-only; the rule pairing is one of several defensible draws) ride with it.
2. **A memory** for the mechanism: the score is a mean over bins by count, weighted by planned
   batches per SKU (~1), on stock that is mostly never re-asked inside the window -- so a
   0.1% spread is what the arithmetic allows. Link `pick-owed-s-replaces-flow-totals-for-
   unload-ranking` (which explains why the FLOW total tied; this is why the STATE read nearly
   ties too) and `toy-fixture-cannot-discriminate-unload-policies`.

## What to fix in `Optimization/run_unload_ranking.py`, before the write-up is published (Q27)

- **Price unservable lines at the leaf's mean priced line, not zero.** The census is 0.3% of
  the score (below the 1% material threshold) but ~35% of the fifo-vs-gforecast GAP: gforecast
  strands 170-250 more fulfillment lines per batch and is credited for each at zero. The
  adjustment is `owed * W / (W - unservable)` per leaf (W = planned lines: 24,725 store /
  119,223 fulfillment on this run); the census stays reported beside it as the availability
  verdict, and the material-census refusal is unchanged.
- **Re-declare the floor against the score's own noise.** The 0.1% floor is declared, not
  measured; the store leaf's batch-to-batch sign flips read ~0.05%, the fulfillment leaf
  oscillates -0.98% to +0.03% between fifo and gforecast. Measure it as the paired per-batch
  spread the `percent` view already bootstraps, and write the measured value into the
  artifact where `noise_floor_note` now says DECLARED.
- **The rider is a control** (CONTEXT.md: Rider; Q4). `rank_agreement` compares replications
  only among non-degenerate pairs; with one such pair it reports the rider's verdict as
  "does the policy do anything under FIFO restock" and never as a veto.
- **The metric comes from the spec** (assumption accepted in round 5): the tool reads which
  quantity ranks from the spec's declaration so the same tool ranks the fill trial on pick
  labour (ticket 06) without a second tool.

Re-run the tool on the finished root and record the ranks it now prints in the campaign map.
Then publish through `publish-experiment`.

## Bar

The tool's output on the finished root carries a measured floor, an adjusted score, and the
rider labelled a control; the campaign map states the finding; the memory exists. No
simulation is re-run.

## Answer

Resolved 2026-09-22. No simulation re-run.

**The record.** `.scratch/phase-2-campaign/map.md` gained "The result -- 2026-09-22": the
finding in the run's own numbers (inbound-served picks 8.7% / 23.9%; placed-and-never-picked
91% / 75%; coverage 477 d / 83 d; the score moves 1/(N+1) of one line per pack), the four
caveats, and the corrected ranking table. Memory
`pick-owed-cannot-see-inbound-at-this-demand` carries the mechanism and links the two
memories it completes.

**The tool** (`Optimization/run_unload_ranking.py`, artifact `version: 2`):

- `adjusted_owed(owed, unservable, W)` prices the census at the leaf's mean priced line,
  per batch from the raw `batch_stats` rows; W is rebuilt from the pair's batch pickle by
  `batch_precompute.planned_lines` -- the SAME function the worker now uses for the
  score's weight (`strategy_runner._build_arm` reuses it), matched to the leaf by its
  `batches_fingerprint` (now written into `sim_meta.json` by `_prepare_channel_run`) or,
  on older vintages, by the pickle's SKUs. On the finished root: 24,725 store / 119,223
  fulfillment lines, every unit adjusted. `owed_unadjusted` and `adjusted` ride on every
  unit and entry; a unit that cannot be adjusted ranks raw and says so.
- `measured_floor` -- the widest 95% moving-block-bootstrap half-width of any cell's mean
  paired per-batch gap to the reference, per rule pair (`stats_core._boot_ci`, per
  `per-batch-series-are-autocorrelated`). Winner pair 0.080%, rider 0.017%. The declared
  0.1% is the fallback (`--declared-floor` forces it) and the artifact says which applied,
  with every cell's gap and interval.
- `rank_agreement(rankings, control=rider)` sets the rider aside; `control_verdict` reports
  it as a control: `ggated_h100`, `gmyopic`, `gmyopic_k8` inert (byte-identical to `fifo`),
  six moved. `rank_agreement` answers `None` on one replicating pair instead of the veto.
- `_metric_for` reads `whatif_config.SPECS[spec]['ranking']` (`PHASE2_RANKING` declared on
  the three phase-2 specs); the default is the placement score; the artifact records
  `declared_by`.

**What changed on the finished root.** `chosen` on the winner pair went
`ggated_h025, gforecast, fsight_wall` -> `ggated_h050, ggated_h025, gforecast`; the five
futuresight/forecast cells are cheaper than `fifo` by 0.04-0.12%, outside the measured floor,
and sit in one tie group ordered by overage. `discriminating` is now TRUE on both pairs at
the measured floors; the closed form disagrees with the score on both (it agreed on the
rider at 0.1%). Publishing before this would have shown the old ranks (Q27).

**Tests.** Seven new in `Tests/unit/test_unload_ranking.py` (adjustment arithmetic,
`planned_lines`, the measured floor on seeded series, the control, the declared fallback,
the spec-declared metric, an end-to-end adjust-and-measure through monkeypatched batch
rows); 23 pass. The CLAUDE.md pytest selection: 227 pass. Contract, store, path and docref
gates green.

**The publish (same day).** `publish-experiment` from the finished root produced Experiment 9
(`docs/experiments/experiment-9/`, the first coupled publish) and deprecated Experiment 8. What
the pipeline gained for it, each the declared way: a `site_figure_png` template and a
`_stage_site_figures` stage (the SITE-scope yard figures a coupled run renders once per
cell, through the contract's `figures_site_*` globs), `_stage_ranking` for the ranking
artifact (named, not group-tagged: a tag would mint a new run-tree schema id), a `site_suite`
registry section with the 13 yard figures and the two `layout.pick_owed` figures registered,
`macros.site_suite_section` and `macros.unload_ranking`, and the two-way section-to-scope tie
test for `site`. The stakeholder loop's round 1 routed one finding as R3: both managers asked
for this run's own re-pick share as a sourced number, so `run_unload_ranking` now records
`inbound_repick` per (pair, cell, channel) from a new `pick_bins` named query joined onto
`bin_placement` (9.3% of the store's put-away units and 23.8% of fulfillment's were picked
again inside the window; 8.7% / 22.8% of picks came from a dock-filled bin) and the artifact
also records each cell's fee threshold (`tie_break.threshold_days`, 0.40 d -- the factor
register prints the run-level 2.0 d the cells override, which the WMS reader caught). Later
rounds added, each for a reader's sourced number: `run.site_crews` (the derived receiving
crew of 23 and put crew of 64, copied from the run spec's staffing record), `trailers` per
unit and per ranking row (~640 per site run, 1,284-1,289 per cell on the winner pair), and
`bins_per_picked_sku` in the re-pick block (4.0 store / 7.6 fulfillment). The SME round's
chart-family findings and the factor register's missing speeds are ticket 09.
