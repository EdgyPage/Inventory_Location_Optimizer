# Re-take the reference run under v3 and re-establish the gap

Type: task
Status: open

Graduated 2026-09-12 from
[Re-measure the fill-law targets under v3](45-remeasure-the-fill-targets-under-v3.md), by user
decision to hoist the run in front of the form work. AFK. Execution override is ON.

## Question

**Does a fill-law gap survive the sampler fix at all?**

Everything 38, 39 and the 40-43 chain were built to explain was measured on
`comparison_20260912_055947`, a **v2** run. 45 has since shown that v2's defect manufactured the
evidence: 335 fulfillment SKUs were drawn on at least half of the 20 measured days (one on 17 of
20) because they sat on Fenwick boundaries with `p_s` ~0.75-0.80, and under v3 no SKU reaches
half and the maximum is 5. On the generator side every symptom is gone -- the touched-SKU
shortfall (-25.2% -> +3.3%), the lag-1 suppression (0.838x -> 1.014x), and the repeat statistic
itself (3.74x the record's Poisson -> 0.87x).

**The realized missed share is the one number 45 could not re-measure**, because it takes a run:
store 0.0302 and fulfillment 0.1044 are v2 outcomes. And the same artifact SKUs, drawn 15-17 days
out of 20 against levels sized for ~1.5 lines per SKU, would have been missing almost constantly
-- so the defect plausibly inflated the realized miss as well. That is a PREDICTION and this
ticket measures it.

### What to run

One era run on the reference `lt0` pair, the same shape as `comparison_20260912_055947` (40
coupled days, days 20-39 measured), under the declared v3 sampler. Note that the run is not
merely a re-take: **the geometry moves**. v3 delivers 9.5% more fulfillment lines and 1.0% more
store lines, the coverage fixed point reads `n` off the sampler's unit, so the levels, the solved
line floor and the derived picking crew all move with it. Report what the solve asked for --
floors per leaf against 1.2668 / 1.4994 and the aisle count against 2,774 -- as a measurement,
not a surprise.

### What the answer must state

1. **The realized first-pass fill per leaf**, read against the equilibrium instrument's `supply`
   band, and the realized missed share against the stamped, on both leaves. This is the verdict:
   in band means there is no gap left for the 40-43 chain to close.
2. **The realized order-to-shelf lead** under v3, re-measured the way 39 measured it
   (`TrailerTransit.lead_for` reconstructed from `yard_trailers.seq`, never inferred from a
   level). The DRAWN law is untouched by the sampler; the realized leg is not.
3. **A verdict on the chain.** If the gap is closed, say so and rule
   [Characterise the draw probability](40-characterise-the-draw-probability.md),
   [Gate the form on the generator](41-gate-the-form-on-the-generator.md) and
   [Land the draw probability through every closed form](42-land-the-draw-probability.md) out of
   scope or re-scope them; if a gap survives, give it its v3 SHAPE (which leaf, how big, which
   direction per channel) so 41 can be aimed at a live target. 45 found the record under-prices
   the store by 19% and over-prices fulfillment by 16% -- opposite directions -- so a surviving
   gap is not the single-multiplier shape 39 fitted.
4. **Whether [Re-run the reference pair and record the form](43-rerun-and-record-the-form.md)
   still needs its own run**, or collapses into this one plus its paper trail. Do not re-run an
   era for a second time without saying why.

Done when the run is clean, the fill and lead are read, the geometry move is reported, and the
verdict on 40/41/42/43 is stated plainly.

**Method warnings:**
- Check `run.log` for `Traceback`, `produced no data` and `Config stage: 0 job(s)` before
  trusting the run -- every worker can die and `run_simulation` still exits 0 (memory
  `pool-run-swallows-dead-arms`).
- Launch the driver DETACHED, not through Bash background mode, which dies at the 10-minute cap
  and orphans `run_simulation` (memory `launch-long-drivers-detached`).
- The whole-run entry point is `python -m Optimization.analyze_run <run_root>`; `run_analysis.py`
  takes a CELL directory and exits 0 doing nothing when handed a run root (CLAUDE.md 3).
- Denominate crew shares on distinct `work_day` values, never on calendar span (memory
  `calendar-span-is-not-work-days`).
- The v2 run's batch caches and its two `_drawp_*.npz` artifacts are v2 artifacts; a v3 run
  fingerprints its own apart and can never be served them (memory `v3-sampler-era`).
- A line is a distinct `(batch_id, sku)`, never a row of `picks`.
