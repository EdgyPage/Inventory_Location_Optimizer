# Formula reference

Every calculation in this experiment in one place: the pick-time cost model, the labor
decomposition, the per-bin placement primitive, and the scoring objective of each of the 17
assignment functions. The [simulation lifecycle](comparison-overview.md) shows **when** each of
these runs during a simulation; this page is the canonical **what**. Symbols are in the
[Glossary](glossary.md).

{% set inv0 = (experiment().inventories.keys() | list) | first %}

!!! note "Notes"
    One distinction on this page carries the whole experiment: **task labor** is a property of the
    work, and it is what every formula below computes. None of them says anything about *when* a
    task runs or *which picker* runs it.

    That is why swapping round-robin for LPT can move throughput by double digits while leaving
    every quantity defined here unchanged to within ±0.06 %. The scheduler does not appear in the
    cost model at all — it only decides the order in which the model's outputs are consumed. If a
    formula on this page changed between the two schedulers, that would be a bug, not a finding.

    The model itself is **stated, not fitted**: coefficients are calibrated to plausible pick
    ergonomics (setup time, weight/volume effort, shelf-height penalty, travel pace), not
    regressed on a particular building's time studies. Comparisons under the stated model are the
    deliverable; validating the coefficients against a real floor is a pilot's job, not this
    page's claim. The reason an unfitted model can still *rank* rules: every rule pays the same
    costs, and the ranking has been stable under perturbation —
    [Experiment 1](../experiment-1/index.md) varied the calibration itself across four settings
    and the placement win survived all four (scaling from −1.7 % to −9.5 % as picks got
    costlier); the scheduler winner has survived every catalogue and demand-stream change since
    [Experiment 6](../experiment-6/index.md), while this sweep showed the placement winner
    tracks the supply model ([labor page](full-results.md#the-headline-and-where-it-holds)).

## Notation

| Symbol | Meaning |
|--------|---------|
| $t_0$ | fixed pick setup time (`pick_intercept`, s) |
| $w,\ V$ | item weight (lb), volume (in³) |
| $h$ | per-pick **handling term** $= c_w w^{e_w} + c_v \log_2 V$ (weight + volume effort) |
| $q$ | quantity picked |
| $y,\ M(y)$ | shelf height; its **height-bracket multiplier** |
| $D_b$ | per-bin **travel** cost (entrance-relative, Manhattan) |
| $x_{\text{pace}},\ y_{\text{pace}}$ | per-inch paces $\tfrac{1}{12 v_x},\ \tfrac{1}{12 v_y}$ for speeds $v_x,v_y$ (ft·s⁻¹) |
| $f_s,\ q_s$ | SKU $s$'s relative pick frequency, pick quantity |
| $\text{lift}(s,p)$ | co-occurrence strength of SKUs $s,p$; $\text{co-occur} = \sum_p(\text{lift}-1)f_p$ |
| $\beta,\ \lambda$ | affinity-reward weights |
| $\ell(b)$ | per-bin **labor primitive** (placement proxy, below) |

## Pick time

One pick at bin $b$, from [`Warehouse/picking/Pick.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/picking/Pick.py):

{{ pick_time_formula(inv0) }}

The **calibrations** differ by more than coefficients, and the table below is authoritative
where this sentence is not: the two channels use different **function families**, not one shape
with different constants. Store weights by a power law and takes volume in $\log_2$; fulfillment
weights logarithmically and takes volume in natural $\log$. The fixed per-pick intercept differs
too (15 store, 10 fulfillment), as do the height multipliers $M(y)$. Each leaf's committed
`config.json` carries the exact pair — `pick_weight_fn` / `pick_volume_fn` name the family,
`pick_weight_coef` / `pick_volume_coef` the coefficient:

{{ pick_calibration_table(inv0) }}

Three precision notes for anyone reconstructing the exact per-channel formula (each verifiable
against the committed leaf `config.json` files under `images/…/store/` and
`images/…/ful_calibrated/`):

- **The constants are per-channel, not universal.** Where the rendered equation shows numeric
  values, read them as this channel's $t_0$ and $c_{\text{cart}}$: the store config runs
  $t_0=15$ s, $c_{\text{cart}}=300$ s; the fulfillment config runs $t_0=10$ s,
  $c_{\text{cart}}=240$ s.
- **Fulfillment's height multiplier is 1 by geometry, not by configuration.** The run's
  fulfillment config carries the same $M(y)$ brackets as store (`[[96,1],[240,1.2],[∞,1.4]]`),
  but every fulfillment bin sits on shelving below the first 96-inch step, so $M(y)=1$ for
  every fulfillment pick — the brackets exist and simply never bite.
- **The batch-size fraction differs by channel too:** store samples waves at mean 15 % of its
  SKU pool, fulfillment at 20 % (`batch_mean_frac` in each leaf's config) — a per-channel
  demand-shape setting, identical across all arms *within* a channel, which is the invariance
  the comparisons need.

## Task labor — handling + travel + cart { #task-labor }

The **realised** time to clear one aisle (the simulation's measurement, from
[`Optimization/metrics/Workload.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/metrics/Workload.py))
splits into three parts:

$$W \;=\; \underbrace{\sum_{\text{stops}} M(y)\,(t_0 + q\,h)}_{H\ \text{— handling}}
\;+\; \underbrace{x_{\text{trav}}\,x_{\text{pace}} + y_{\text{trav}}\,y_{\text{pace}}}_{T\ \text{— travel}}
\;+\; \underbrace{c_{\text{cart}}\,\max(0,\ \text{carts}-1)}_{C\ \text{— cart}}$$

**Travel is Manhattan (L1):** $x_{\text{trav}} = \sum_i |x_{i+1}-x_i|$ and
$y_{\text{trav}} = \sum_i |y_{i+1}-y_i|$ over the aisle's ordered pick path — the summed
horizontal + vertical distance walked, not straight-line. $H\!+\!T\!+\!C$ is what drives
[makespan](glossary.md#makespan). (In code these are named `P`, `D`, `C`.)

## Placement primitive $\ell(b)$ { #placement-primitive-ellb }

Placement scorers do **not** optimise $W$ directly — they rank bins by a per-bin **proxy**:

$$\ell(b) \;=\; M(y_b)\,(t_0 + h) + D_b,\qquad
D_b = x_{\text{pace}}\,x_{\text{phys}} + y_{\text{pace}}\,y_{\text{phys}}$$

This shares the handling and travel terms with $W$ **but is not the same calculation**: it is
per-bin, evaluated at $q=1$, and omits the cart penalty — a cheap ranking signal, not the
realised labor. The families below combine $\ell(b)$ (or its parts) with demand $f_s$ and
affinity.

Most **ranked** families order the wave by a shared **pick-effort priority** (highest first, so
it claims its extremal bin):

$$\text{priority} \;=\; f_i\,(t_0 + h) \;+\; \beta\,\text{co-occur}$$

## The families

The suite is built as **brackets**: for each lever there is a maximiser and a minimiser that
bound how much the lever is worth. **The maximising controls (`tmax`, `cmin`, `expn`,
`rank_maxlabor`) are *designed to lose*** — they deliberately place badly, so they read *worse*
than FIFO. This is the general lesson the [labor page](full-results.md) makes concrete: an
"optimization" pointed the wrong way (or at the wrong objective) can make cumulative task time
**worse**, not better.

*The scheduler's own mechanics* (it is not a placement family, but the same precision is owed):
LPT runs **once per batch** as a static assignment — tasks are visited heaviest-first by a
modeled estimate $\operatorname{est}(t) = (\text{travel}+\text{handling}) + c_{swap}\cdot
\text{vol}(t)/\text{cap}$ (a smooth cart proxy, not the realized step-function makespan) and
each is appended to the **least-loaded** picker; ties break to lower aisle id, then lower picker
id, so the assignment is deterministic. Cart constants for this run (from each leaf's committed
`config.json`): store cart 125,000 in³ capacity / 300 s swap; fulfillment cart 25,000 in³ /
240 s — the size gap is why `Rank_cartlabor` ≈ `Rank_labor` on store but separates on
fulfillment.

*Mechanics shared by every family* (so each formula below can state only its objective): every
$\arg\min$/$\arg\max$ ranges over the **free bins that fit the unit** — a full aisle simply has
no candidates and drops out; scoring **ties resolve deterministically**: candidate bins are
scanned in a fixed pre-sorted order (travel-distance ascending within each height band, aisles
in id order) and the first best score wins, so identical runs place identically; and a unit no rule can place is repacked to a smaller tier,
then singleton bins, else it waits in the restock queue and retries next batch. The operational
scope notes (manual holds, damaged slots, cold-start SKUs) are on the
[labor page](full-results.md#every-arm).

!!! note "The rule sections below are generated, not written"
    Every heading, objective and implementation note from here to the statistics section
    is rendered from `rule_catalog.json` — the catalogue **this run emitted**, listing what
    each rule optimises, which Python symbol implements it, and whether the sweep actually
    swept it. It used to be prose maintained in three places at once (this page, a macro,
    and the code), and it had drifted. A test now ties each objective to the symbol its
    builder really calls, so a renamed function fails CI instead of quietly making this
    page wrong.

{{ rule_catalog() }}

## How the comparison statistics are computed { #comparison-statistics }

Every arm-versus-baseline number the pages quote comes from one per-leaf table,
`vs_baseline.csv`, staged beside each experiment's figures. One row per (arm, metric). The
columns, and what each is:

| column | what it is |
|---|---|
| `pct_median` | the **median** of the per-batch improvements against the baseline arm, oriented so **positive = better** whichever direction the metric runs. Batches where the baseline value is zero are dropped, not counted as ties — the comparison is undefined there. |
| `ci_lo` / `ci_hi` | a 95 % **moving-block bootstrap** interval *of that same median*, so the interval always brackets the number printed beside it. |
| `hedges_g` | the paired standardised effect size on the raw per-batch values, oriented the same way as `pct_median`. |
| `rank_biserial` | its distribution-free companion, in [−1, 1]; ±1 means the arm won (or lost) on every batch. |
| `p_wilcoxon` / `p_holm` | the paired signed-rank test, raw and Holm-corrected. The correction family is **every non-baseline arm in this leaf, for this one metric** — 33 comparisons on a 34-arm run — because that is the set a reader scans when looking down a column for a winner. Nothing is pooled across metrics, channels, inventories or cells: each leaf's file corrects within itself. |
| `n_batches` | the batches the two arms actually share. |

**Reproducing an interval exactly.** 2,000 resamples, overlapping moving blocks of length
round(n^⅓) (4 batches at n = 75), drawn from a Generator seeded to 0 — so re-running the
analysis on the same databases reproduces every printed bound to the digit, and a bound that
moves means the data moved. Blocks do not wrap the series end; the last block is truncated to
fill exactly n observations.

**Why the interval is block-based.** The 75 batches are one continuous run: each batch inherits
the previous batch's layout, so the per-batch differences are not independent draws. Measured on
this run, lag-1 through lag-3 autocorrelation sits outside the ±2/√n white-noise band for some
arms. Resampling batches independently would treat correlated observations as independent and
report an interval narrower than the data earns, so the bootstrap resamples contiguous **blocks**
of batches (length ≈ n^⅓) and preserves the local dependence. Where there is no correlation this
costs nothing.

**Why two labor-ish metrics can disagree.** `production_time` and `task_mean_duration` are
*measured outcomes* — time the simulated pickers actually spent. `objective_task_labor` and
`objective_total_labor` are the **scorer's own objective**, the analytical quantity an assignment
function minimises when it chooses a slot. A rule can move its objective slightly while the
realised time moves more (or the reverse): the objective is a model of the work, the duration is
the work. When the two disagree for one arm, the measured outcome is the one the findings on
these pages are stated in; the objective column is there to show what the rule was *trying* to do.


**When the p-value and the interval seem to disagree.** They can, and one row here does:
`opt_rank_minlabor` on throughput reports a corrected p of 2 × 10⁻⁴ beside a 95 % interval of
[−6.8 %, +0.4 %] that includes zero. Both are honest, because they answer different questions.
The signed-rank test asks *how consistently* one arm beat the other wave by wave — it is a
question about direction, and 75 waves leaning the same way answer it decisively. The interval
asks *how large* the effect is, and a heavy-tailed spread of per-wave ratios leaves that range
wide enough to touch zero. Read it as: the arm is reliably worse, and how much worse is not
pinned down. Where the two disagree, believe the interval about the magnitude and the test about
the direction — and treat a magnitude whose interval crosses zero as unproven, whatever the p.
