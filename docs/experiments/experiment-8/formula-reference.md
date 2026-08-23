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

The **calibrations** keep this shape and differ only in the weight exponent $e_w$ and the
height multipliers $M(y)$:

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

## Baseline

### FIFO — `fifo` { #fifo }
First-in-first-out: drop each arriving unit into a **uniform-random** bin of its
[BinKey](glossary.md#binkey) pool. No ordering, no affinity, no demand awareness. The
do-nothing control every other family is measured against.

## Ranked (effort / labor)

These order the wave by the pick-effort priority above, differing in *where* they place it.

### Rank_random — `rank_random` { #rank-random }
Rank by priority, then place each unit in a **uniform-random aisle** at its lowest-`D` (front)
bin. Isolates the *ordering* effect from the *placement* effect — how much of the win is just
sequencing hot units first.

### Rank_popularity — `rank_popularity` { #rank-popularity }
Rank by expected popularity (`f·q`), place each into the aisle with the **least** Σ popularity.
Spreads demand mass evenly across aisles (a dispersal control).

### Rank_labor — `rank_labor` { #rank-labor }
**Travel-aware LPT (longest-processing-time) labor balance — the best store arm in this run.**
Aisle $a$'s total expected labor is $L_a = \sum_{s\in a} f_s\,q_s\,\ell(b_s)$; each unit is placed
where it least raises the busiest aisle, costliest SKU first:

$$\arg\min_{(a,\,b)}\ \bigl(L_a + f_s\,q_s\,\ell(b)\bigr).$$

### Rank_cartlabor — `rank_cartlabor` { #rank-cartlabor }
**Rank_labor plus a cart-swap term — a top-3 winner, within 0.02 pp of Rank_labor on store.**
Identical to Rank_labor except that the load being balanced also carries each aisle's *expected
cart-swap* cost, so demand mass that would overflow a picker's cart gets dispersed rather than
concentrated. Writing $V_a = \sum_{s\in a} f_s\,q_s\,v_s$ for the aisle's raw expected picked
volume and $\hat{V}$ for the cart capacity rescaled to the same units
($\hat{V} = \text{cart\_capacity}\cdot\sum_s f_s / k$, with $k$ the expected SKUs per batch):

$$C_a = c_{\text{swap}} \cdot \max\!\left(0,\ \frac{V_a}{\hat{V}} - 1\right),
\qquad
\arg\min_{(a,\,b)}\ \bigl(L_a + C_a + f_s\,q_s\,\ell(b)\bigr).$$

With the store's large cart $C_a$ is ≈ 0 — aisles rarely fill it — so store plans barely differ
from Rank_labor, which is why the two sit adjacent at the top of the results table. With the small
fulfillment cart the term bites. Setting the cart tuple to `None` makes this **byte-identical** to
Rank_labor (`build_ranked_cartlabor_fn`, `Warehouse/placement/Assignment_Functions.py`).

### Rank_minlabor — `rank_minlabor` { #rank-minlabor }
**Greedy minimiser of expected total task labor — a top-3 winner in this run.** Fuses golden-zone
height, effort-to-front, and affinity compaction into one marginal-cost score (consolidates rather
than balances):

$$\arg\min_{(a,\,b)}\ \Bigl[\,f_s\bigl(M(y_b)(t_0 + h) + D_b\bigr)
\;-\; \lambda\!\!\sum_{p\,\in\,\text{aisle}}\!\!\bigl(\text{lift}(s,p)-1\bigr) f_p\,\Bigr].$$

### Rank_maxlabor — `rank_maxlabor` { #rank-maxlabor }
The exact **maximiser** mirror of `rank_minlabor` (high/far bins, scattered partners) — a
worst-case control that should land *worst* on task labor. Designed to lose.

## Map (optimal-map score matching)

### Map — `map` { #map }
**Optimal-map score matching.** Each bin has a quantity-free preferred score
$\operatorname{pref}(b) = D_b + M(y_b)(t_0 + \bar h)$; each SKU's $\operatorname{target}(s)$ is
the $\operatorname{pref}$ of its bin in the labor-minimising full linear assignment problem
(LAP). Place at

$$\arg\min_{b}\ \bigl|\operatorname{pref}(b) - \operatorname{target}(s)\bigr|.$$

*Recompute cadence:* the LAP — and with it every $\operatorname{target}(s)$ — is solved **once
per run at setup**, from the demand model; it is **not** re-solved as the run's realized demand
drifts. As a WMS job that means a periodic offline re-slot computation (nightly/weekly), not a
live service — and the variable-lead-time result on the [labor page](full-results.md) was earned
with a map that never refreshed mid-run, so a refreshing implementation starts from at least
this baseline.

### Map_rank — `map_rank` { #map-rank }
**The same map, upgrade-capped.** A SKU never reloads into a bin more prime than
its optimal rank, reserving prime spots for higher-ranked SKUs future orders bring:

$$\arg\min_{\,b\,:\,\operatorname{pref}(b)\,\ge\,\operatorname{target}(s)}\
\bigl(\operatorname{pref}(b) - \operatorname{target}(s)\bigr).$$

## Cluster-map (map + cohesion)

### CluMap — `cluster_map` { #cluster-map }
Mix `map` with clustering: choose the aisle **cohesion-first** (most demand-weighted affinity
to existing members), anchor the unit at its favoured map location, and compact it toward the
partners' column centroid.

### CluMapRk — `cluster_map_rank` { #cluster-map-rank }
Upgrade-capped `cluster_map` — same cohesion + compaction, but never settles more prime than
its map target.

## Travel bracket

### TripMin — `tmin` { #tmin }
Minimise the travel score $f_s\,D - \beta\,\text{co-occur}$: hot SKUs to low-$D$ (front) bins →
less within-aisle walking.

### TripMax — `tmax` { #tmax }
Maximise the same score (hot items to the **back**). Worst-case travel control; brackets `tmin`.

## Affinity bracket

### MaxClu — `cmax` { #cmax }
Maximise **cohesion** $\text{co-occur} = \sum_p \bigl(\text{lift}(s,p) - 1\bigr) f_p$: send each
SKU to the aisle where its co-picked partners already sit → fewer aisle visits per batch.

### MinClu — `cmin` { #cmin }
Minimise cohesion (scatter partners across aisles). Anti-affinity control; brackets `cmax`.

## Co-demand bracket

Both place a SKU in the chosen aisle relative to the **demand-weighted column centroid** of its
co-demanded partners already in that aisle:

$$c_x \;=\; \frac{\sum_p \bigl(\text{lift}(s,p)-1\bigr)\,f_p\,x_p}{\sum_p \bigl(\text{lift}(s,p)-1\bigr)\,f_p}$$

where $x_p$ are the partners' column positions.

### Compact — `comp` { #comp }
Minimise within-aisle **span** — place the SKU in the column **nearest** the partner centroid,
shortening the sweep path:

$$\arg\min_{b}\ \lvert x_b - c_x \rvert.$$

### Expand — `expn` { #expn }
Maximise within-aisle span — place it **farthest** from the centroid (counter control):

$$\arg\max_{b}\ \lvert x_b - c_x \rvert.$$

The `comp ↔ expn` gap measures how much the co-demand lever is worth.

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
| `p_wilcoxon` / `p_holm` | the paired signed-rank test, raw and Holm-corrected. The correction family is **the arms within one metric** — the set a reader scans when looking down a column for a winner. |
| `n_batches` | the batches the two arms actually share. |

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
