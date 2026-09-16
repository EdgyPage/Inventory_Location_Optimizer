# The analysis side is fixed cost, and that is the finding

Type: research
Status: resolved

`Optimization/Performance_Evaluations` is **78 of Optimization's 147 files** and no ladder touches
it: `calltree_tracer.SECTIONS` is `t_reord, t_sample, t_task, t_pre, t_inv, t_sim, t_extract,
t_save` -- every one a `strategy_runner` batch-loop section. The analysis half of the package this
effort was pointed at has never been measured.

The ticket was written expecting "found nothing" and says so; this records the NUMBER rather than
the expectation.

## Measured

Two real runs, identical but for the batch count -- the dimension the analysis actually scales on,
since every series it builds is per batch. Both re-analysed back to back on a quiet host,
`--workers 1` so the pool cannot hide work:

| batches | analysis wall |
|---|---|
| 5 | **69.1 s** |
| 20 | **71.8 s** |

**A 4x increase in batches costs 3.9% more analysis wall: k = 0.028.**

Against `FLAG_TIME_EXP = 1.50`, that is not merely sub-linear -- it is flat. **The analysis pass
is fixed cost** at this scale: imports, figure rendering, DB opens, the catalogue model and the
factor register, none of which care how many batches the run had.

## What it rules out, and what it does not

RULED OUT: a superlinear term in the analysis half, on the batch axis, at this scale. That was the
plausible worry and it is answered.

NOT ruled out, and stated so nobody reads this as a clean bill:

* **The other axes.** Cells x pairs x configs x arms are all bounded and small here (one cell, one
  pair, two configs). A campaign runs 10 cells; the fixed cost is paid PER CELL, so the run-level
  cost is `cells x ~70 s` and the lever is parallelism, not complexity. That matches the memory
  `analyze-run-granularity-worker-saturation`: the default emits ~4 jobs per cell, so `--workers
  24` idles ~20, and at ~70 s of largely serial fixed cost per cell that idling IS the cost.
* **Catalogue size.** Held at 500 SKUs. Some analysis work is per SKU (the inventory model reports
  "500 of 500 SKUs declared"), so a 400k catalogue would move terms this ladder holds still.
* **An `ast` sweep found only 3 triple-nested loop sites** in the whole package, all over bounded
  axes, which is consistent with the measurement and was the reason to expect it.

## The comparison worth keeping

The simulation that produced the 5-batch tree took ~64 s wall; its analysis took 69.1 s. **At this
scale the analysis costs about as much as the simulation it describes**, and essentially all of it
is fixed. Anyone tempted to optimise the analysis half should attack the per-cell fixed cost or
the job granularity -- not an algorithm.
