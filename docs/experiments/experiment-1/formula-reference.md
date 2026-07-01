# Formula reference

Every calculation in Experiment 1 in one place: the pick-time cost model, the labor
decomposition, the per-bin placement primitive, and the scoring objective of each of the 16
assignment functions. The [simulation lifecycle](comparison-overview.md) shows **when** each of
these runs during a simulation; this page is the canonical **what**. Symbols are in the
[Glossary](glossary.md).

!!! note "Notes"
    <!-- paste commentary here -->

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

One pick at bin $b$, from [`Warehouse/Pick.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/Pick.py):

{{ pick_time_formula('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0', 'calibrated') }}

The four **calibrations** keep this shape and differ only in the weight exponent $e_w$ and the
height multipliers $M(y)$:

{{ pick_calibration_table('comparison_20260627_054619', 'mixed_20260624_083549__mixed_realistic_lt0') }}

## Task labor — handling + travel + cart { #task-labor }

The **realised** time to clear one aisle (the simulation's measurement, from
[`Optimization/Workload.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/Workload.py))
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
than FIFO. This is the general lesson the [Full results](full-results.md) make concrete: an
"optimization" pointed the wrong way (or at the wrong objective) can make cumulative task time
**worse**, not better.

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
**Travel-aware LPT (longest-processing-time) labor balance — a top-3 winner.** Aisle $a$'s total
expected labor is $L_a = \sum_{s\in a} f_s\,q_s\,\ell(b_s)$; each unit is placed where it least
raises the busiest aisle, costliest SKU first:

$$\arg\min_{(a,\,b)}\ \bigl(L_a + f_s\,q_s\,\ell(b)\bigr).$$

### Rank_minlabor — `rank_minlabor` { #rank-minlabor }
Greedy **minimiser** of expected total task labor — fuses golden-zone height, effort-to-front,
and affinity compaction into one marginal-cost score (consolidates rather than balances):

$$\arg\min_{(a,\,b)}\ \Bigl[\,f_s\bigl(M(y_b)(t_0 + h) + D_b\bigr)
\;-\; \lambda\!\!\sum_{p\,\in\,\text{aisle}}\!\!\bigl(\text{lift}(s,p)-1\bigr) f_p\,\Bigr].$$

### Rank_maxlabor — `rank_maxlabor` { #rank-maxlabor }
The exact **maximiser** mirror of `rank_minlabor` (high/far bins, scattered partners) — a
worst-case control that should land *worst* on task labor. Designed to lose.

## Map (optimal-map score matching)

### Map — `map` { #map }
**Optimal-map score matching — a top-3 winner.** Each bin has a quantity-free preferred score
$\operatorname{pref}(b) = D_b + M(y_b)(t_0 + \bar h)$; each SKU's $\operatorname{target}(s)$ is
the $\operatorname{pref}$ of its bin in the labor-minimising full linear assignment problem
(LAP). Place at

$$\arg\min_{b}\ \bigl|\operatorname{pref}(b) - \operatorname{target}(s)\bigr|.$$

### Map_rank — `map_rank` { #map-rank }
**The same map, upgrade-capped — a top-3 winner.** A SKU never reloads into a bin more prime than
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
