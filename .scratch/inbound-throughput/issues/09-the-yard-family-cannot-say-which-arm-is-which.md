# 09 - the yard family labels rows `fifo`/`rank`, and ships no effect view

Type: task
Status: resolved

Filed 2026-09-22 from the analysis-SME round that `publish-experiment` runs when the figure
set changes, taken against the full-scale coupled leaf `k1_off_gforecast` of
`comparison_whatif_20260920_150203` (the first coupled publish, Experiment 9). Twelve
findings; the ones below are the suite's, not the experiment's, and Experiment 9's captions
work around them in prose. None blocks the publish; each is a chart-family fix in
`Optimization/Performance_Evaluations/yard/` (and two in the pick-side families) that
re-renders on the next analysis pass, never a hand-edited PNG.

## Legibility

1. **Every site yard figure labels its rows `fifo` / `rank`** -- the placement pair's rule
   family with no starting-layout qualifier -- where every other family labels arms
   `Opt|Rank_cartlabor|noRSL`. A reader cannot tell `opt_rank_cartlabor` from
   `uni_rank_cartlabor` in `absolute_yard_depth.png`, `absolute_detention_mean.png`,
   `absolute_yard_overage_days.png`, `absolute_yard_over_threshold_trailers.png`,
   `absolute_binding_cuts.png` or their `percent_`/`delta_` siblings, nor which `fifo` is
   which. Label the arm PAIR as the ranking tool spells it (`opt_rank_cartlabor /
   opt_rank_minlabor`) or as `Opt|Rank` at least.
2. `absolute_yard_depth.png`'s legend carries `FIFO baseline` (solid black) and `fifo`
   (blue dash-dot) as two entries for lines that coincide for the whole run.
3. `absolute_yard_scorecard.png`'s caption overruns the canvas on the right, and its
   `per channel` column reads `s 61% / f 18%` with no legend for `s` / `f`.
4. `percent_yard_overage_days.png` / `percent_yard_over_threshold_trailers.png` plot the
   baseline at 0 % while the subtitle says an arm is absent where the baseline accrued no
   overage -- ambiguous whether 0 % means "none" or "is the baseline".
5. `absolute_detention_distribution.png` labels its rows `0,1,2,3` rather than by arm.

## Grammar

6. **The yard family ships no `effect_` view and no `table_` view.** Overage is Experiment
   9's headline differentiator and the only artifact for it is a point estimate. Overage
   accrues per TRAILER, not per batch, so the paired-per-batch effect machinery does not
   apply directly; the honest shape is a per-trailer distribution comparison (the detention
   distribution already exists) with a bootstrap interval on the total, or an explicit
   `views_suppressed` entry stating why not. Decide, then declare.
7. Pre-existing and suite-wide, recorded here because the SME saw them on this leaf:
   `significance/effect_heatmap.png` and `effect_<arm>.png` test opt-vs-uniform start, not
   arm-vs-FIFO, while `tables/vs_baseline.csv` holds the arm-vs-FIFO effect sizes and no
   PNG draws them; `headline/percent_all_arms_vs_baseline.png` carries no interval and no
   stars. Both predate Experiment 9 (Experiment 8 shipped them) and belong to the analysis
   suite's own map, not this one -- noted so the next SME round does not rediscover them.

## Also, from the WMS reader (round 2)

8. The factor register (`held_fixed.json`, the dossier's writer) does not list `x_speed` /
   `y_speed` among its varied factors although the two channels run different, reversed
   pairs (store 3/2, fulfillment 2/4 ft/s). Its docstring claims the fixed list cannot be
   incomplete; the speeds are the counter-example. Add them to the factor walk.

## Bar

The site yard figures name the arm pair on every row; the scorecard fits its canvas and
legends its abbreviations; the yard family either ships an effect view or declares why it
cannot; `Tests/architecture/test_view_coverage.py` and `test_figure_registry.py` green;
Experiment 9's captions lose the workaround sentence.

## Answer

Resolved 2026-09-22, verified by re-rendering the coupled priced toy
(`comparison_whatif_20260922_190917`) and viewing the site yard PNGs.

1. **Rows name the arm pair.** Root cause: `run_analysis._assignment_of` split the arm key on
   underscores and took the second token, so `uni_rank_cartlabor_norsl` became `rank` and both
   starting layouts of both placement pairs printed alike. `_pair_fields` now reads the store
   half's display label from the strategy grid (as every leaf does), keeps its
   initial/assignment/reslot (baseline selection unchanged: the rider still reads `FIFO`), and
   adds a `title` naming both halves -- `Uni|Rank_cartlabor+Rank_minlabor|noRSL` -- which
   `style._stitle` prefers. Every yard figure picks it up.
2. **The serial mark's baseline says which member it is**: `FIFO baseline (Opt|FIFO+FIFO|noRSL)`,
   so the other start's rider line no longer reads as the baseline twice.
3. **The scorecard fits.** The definitions moved from the subtitle into a wrapped note band under
   the table; the arm column is sized to its longest label; headers wrap onto two lines; the
   per-channel column's `s` / `f` are legended.
4. **The percent views say what zero is** (the baseline, by definition) and why there is no
   interval.
5. **Detention rows are named**: `boxplot` re-labelled the axis with its positions after
   `category_axis` set the names; `manage_ticks=False` keeps them.
6. **No effect view, by decision, now stated on the figure**: overage accrues per TRAILER, not
   per batch, and trailers are not paired across arms (`yard.fee`'s `_entries` docstring
   records why pairing on seq would be an unchecked assumption), so the paired-batch effect
   machinery has nothing honest to draw. The percent view's subtitle carries that sentence.
7. Suite-wide items (the significance family's contrast, the headline percent chart's missing
   interval) stay with the analysis suite, as recorded above.
8. **The factor register walks `x_speed` / `y_speed`**; the toy's `held_fixed.json` now lists
   them.

Experiment 9's captions carry a workaround sentence about the unlabelled rows; they lose it at
the next re-analysis and re-stage of the campaign root, which this ticket does not run (a full
analysis of the 400k run is hours). Recorded on the map.
