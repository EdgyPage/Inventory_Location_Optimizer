# Characterise the draw probability

Type: task
Status: open
Blocked by: 38

Graduated 2026-09-12 from
[Close the fulfillment fill-law gap](38-close-the-fulfillment-fill-law-gap.md), decisions 2, 3, 6
and 7. AFK: nothing to decide, and no warehouse is built or simulated at any point.

## Question

Build the characterisation stage that produces the **draw probability** `p_s` -- the probability
the declared sampler puts SKU `s` in a batch -- as a fingerprinted artifact per (pair, channel),
and declare `M` from a measurement rather than a guess.

**The draw.** `M` batches from `Warehouse.picking.Workload_Builder._lift_weighted_sample` alone,
using the run's own `seed_batches` so the run's actual script is a PREFIX of the sample (38
decision 7). Inputs are exactly the ones `batch_precompute.precompute_batches`
(`Optimization/simdriver/batch_precompute.py:111`) already takes -- `(inv_db, aff_db, batch_cfg,
seed, channel_regime)` -- and it already parallelises chunks over a spawn pool
(`:123-139`), so reuse that rather than writing a second pool. No warehouse, no simulation.
Note both existing generator-only benches (`Tests/bench/perf_simulation.py:288-312`,
`Tests/calltree/calltree_scenarios.py:229`) pass `affinity=None` and therefore do NOT exercise
the lift path -- they are a starting shape, not a control.

**The stage.** `ensure_p(...)` on the `ensure_batches` pattern
(`batch_precompute.py:169,185-186`): a fingerprint over `(inv_db, aff_db, batch_cfg, allowlist,
M, seed)`, an artifact in the pair directory, a file-exists reuse on the second channel or a
re-run. It runs inside `build_shared_assets` AHEAD of `era_coverage.fixed_point`
(`Optimization/simdriver/sim_assets.py:190`). It must NOT be called from inside the fixed-point
loop: under the era the two `units_per_line` cancel so `mean_fraction == STORE_DEMAND /
FF_DEMAND` (`era_coverage.py:195,230-232` against `:96`; `settings.py:270-271`), making `k` a
declared fraction times the section size with no dependence on `n`. If that cancellation is ever
found not to hold, STOP and re-open 38 -- the whole affordability argument rests on it.

**Declaring M.** `M` is fixed by requiring the plug-in `p_hat_s = c_s / M` and the unbiased
factorial-moment estimate `c_s (c_s - 1) / M (M - 1)` to agree, on the line-weighted
prior-line functional, within a stated tolerance. The plug-in is what the record will carry;
the moment estimate is a diagnostic, never a second rate (38 decision 7). This matters because
that functional is CONVEX in `p_s`, so estimator noise inflates it -- the identical Jensen
artifact that was worth two thirds of 38's apparent movement at M = 20. Expect the STORE to bind
`M`, not fulfillment: at M = 10,000 the mean per-SKU count is ~25 store against ~181 fulfillment
(`k/N` 0.00245 vs 0.01814).

Done when the artifact exists for the reference pair's two channels, `M` is declared with the
agreement measurement that fixed it, and the achieved relative standard error is reported per
channel. Report the realized cost per batch and in total; the estimate this ticket inherits is
~0.02-0.04 s/batch at era batch sizes, scaled from `Workload_Builder.py:68`, and it is an
estimate rather than a measurement.

**Do not** touch `Optimization/simconfig/coverage.py` in this ticket. It "imports no CONFIG and
touches no file" (`coverage.py:68`) and that purity is load-bearing -- `p_s` reaches it as an
ARGUMENT in [Land the draw probability through every closed form](42-land-the-draw-probability.md),
exactly as the stamped line law does.

**Method warnings:**
- A line is a distinct `(batch_id, sku)`, never a row of `picks`.
- Spawn, not fork: pool entry points and their arguments stay module-level and picklable
  (CLAUDE.md), and a pool launched from a heredoc hangs silently
  (memory `heredoc-python-breaks-the-spawn-pool`) -- run it as a `-m` module.
- Draw from the seeded `random.Random`, never the bare global.
