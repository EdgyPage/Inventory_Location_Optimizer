# Characterise the draw probability

Type: task
Status: resolved  <!-- closed OUT OF SCOPE -->
Blocked by: 46

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

## Comments

2026-09-12, a wayfinder session. **The module is BUILT and green; M is NOT declared, because the
session found the sampler it characterises to be defective.** Re-blocked on
[Re-measure the fill-law targets under v3](45-remeasure-the-fill-targets-under-v3.md) by user
decision -- the draw probability IS the sampler's output, so characterising a sampler about to
change means re-drawing everything.

### What landed (stands regardless of the sampler version)

- **`Optimization/simdriver/draw_probability.py`** -- `ensure_draw_probability` on the
  `ensure_batches` contract (fingerprint cache, atomic write, loader that re-verifies), reusing
  `batch_precompute.batch_fingerprint` unchanged with `M` in the `n_batches` slot and a `_drawp_`
  prefix so a draw can never be served from a `_batches_` file. Returns per-SKU COUNTS, not
  probabilities, because the consumer needs both estimators and only counts give the unbiased one.
  Chunk counts are stored separately and in range order, so every prefix is a partial sum and ONE
  draw answers the whole ladder.
- **`Tests/e2e/test_draw_probability.py`** -- 13 tests, green. Two of them failed for real during
  development (a tolerance set tighter than the estimator achieves), which is the non-vacuity
  evidence `Tests/README.md` asks for.

### The prefix property is PROVEN, not assumed

Drawing `M = 40` with the run's own seeds reproduces `comparison_20260912_055947`'s precomputed
batch files **count-for-count on both channels** (store `seed_batches` 1337; fulfillment
1,001,337 via `FF_BATCH_SEED_OFFSET`). Decision 7 rests on exactly this.

### Cost, measured rather than scaled

The estimate this ticket inherited (~0.02-0.04 s/batch, scaled from `Workload_Builder.py:68`) was
**wrong by 5x**. Realized, 3 workers, reference pair:

| section | M | wall | per batch | mean p | touched | mean rse of `p_hat` |
|---|---|---|---|---|---|---|
| store | 8,192 | 537 s | 65.6 ms | 0.002427 | 229,862/239,938 | 34.4% |
| fulfillment | 8,192 | 748 s | 91.3 ms | 0.016687 | **160,062/160,062** | 10.5% |

**MEMORY, not compute, sets the worker count.** The affinity partner map is ~14M entries (400,000
keys) and over a gigabyte resident PER PROCESS; a first attempt at 16 workers on a 15.7 GB machine
thrashed and produced nothing in 10 minutes. Three workers is the shape that fits. A defect in the
first draft is worth recording because it is invisible: `_get_partner_map` caches by
`id(affinity)`, so constructing a fresh `AffinityStore` per chunk silently rebuilds all 14M
entries EVERY chunk. `batch_precompute._sample_range` never hits this because it is called exactly
once per worker; this module deliberately uses more chunks than workers, so it must memoize.

### The ladder works, and it says the criterion should be declared per section

The plug-in's Jensen bias falls as a clean `1/M` (store ratios 2.10, 2.04, 1.88, 2.02, 2.01) while
the UNBIASED estimate is stable from M = 1,024 onward. Relative gap, worst over K in 1..4:

| M | store | fulfillment |
|---|---|---|
| 256 | 53.9% | 4.5% |
| 1,024 | 12.7% | 1.1% |
| 2,048 | 6.7% | **0.57%** |
| 8,192 | 1.66% | 0.14% |

So `M` is set by `M * p`, not by `M`: fulfillment clears 1% relative at **2,048**, the store not
even at 8,192 (a 16,384 draw was running when this ticket was stopped, extrapolating to ~0.80%).
**The contract this suggests -- declare the CRITERION, derive M per section -- was not ratified
and is left to whoever resumes**, along with the actual declaration.

### The finding that stopped the ticket

`sum_s p_s` is exactly the expected distinct-SKU count per batch, and it came back 8% under the
declaration. That was not noise:
[Fix the sampler's duplicate draws as v3](44-fix-the-sampler-duplicate-draws.md) has the isolation
(v2 duplicates on 3/3 batches, v1 zero on the same three) and the cost to the era. Two consequences
for THIS ticket when it resumes:

- **The artifacts on disk are v2 and must be re-drawn.** `_drawp_3eb36d9adc936cd9.npz` (store) and
  `_drawp_6f8cdf1669cd815b.npz` (fulfillment) sit in the reference pair's directory. They are
  correct characterisations of a sampler the era should stop declaring.
- **`max p` = 0.8044 is NOT evidence for the Binomial.** It looked like the strongest possible
  vindication of 38 decision 5 -- a SKU in 80% of batches, where a Poisson rate breaks outright --
  and it is an artifact: that SKU sits at index 131,071 = 2^17 - 1, a Fenwick boundary. The
  argument for `Binomial(K, p_s)` stands on the sampler drawing DISTINCT SKUs once a day, which is
  structural; do not re-use this number to support it.

2026-09-12, a wayfinder session (resolving 45). **Re-blocked on
[Re-take the reference run under v3 and re-establish the gap](46-retake-the-reference-run-under-v3.md)
instead of 45, by user decision to hoist the run in front of the form work.** 45 found that
v2's defect manufactured the evidence this chain exists to explain, so whether `p_s` is worth
characterising at all now depends on whether a gap survives a v3 run. Two facts carry forward
regardless: the two `_drawp_*.npz` artifacts in the reference pair are v2 characterisations and
must be re-drawn, and `max p` = 0.8044 is confirmed an artifact -- under v3 no fulfillment SKU
is drawn on even half the days, against 335 that were under v2.

## Closed: OUT OF SCOPE, 2026-09-12

Ruled out of scope by user decision while resolving
[Re-take the reference run under v3 and re-establish the gap](46-retake-the-reference-run-under-v3.md), which measured the fill-law gap CLOSED under the
era's declared sampler: 12 arms judged, **0 failed**, fulfillment supply 0.1044 -> 0.0284
against an expected 0.0251 at tol 0.020. The v2 sampler's duplicate draws were 95.5-97.1% of
the gap this chain existed to explain.

The draw probability had exactly one consumer -- correcting the fill law -- and the law
now reads in band with no correction. `Optimization/simdriver/draw_probability.py` and its
13 green tests STAY in the tree, unused: they are the sampler effort's starting point, and
the module's contract (fingerprint cache, per-SKU counts, both estimators) is sound. Its
two `_drawp_*.npz` artifacts in the reference pair are v2 characterisations of a sampler
the era no longer declares; treat them as archive, not input.

A scope boundary, not a step on the route: this ticket is NOT in the map's Decisions-so-far.
It returns only if the destination is redrawn.
