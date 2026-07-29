# Formula reference

Every calculation in Experiment 2 in one place: the two channels' pick-time cost models, the
labor decomposition, the per-bin placement primitive, and the scoring objective of each of the 17
assignment functions. The [simulation lifecycle](comparison-overview.md) shows **when** each of
these runs during a simulation; this page is the canonical **what**. Symbols are in the
[Glossary](glossary.md).

!!! note "Notes"
    Space intentionally left blank for user input

## Notation

| Symbol | Meaning |
|--------|---------|
| $t_0$ | fixed pick setup time (`pick_intercept`, s) |
| $w,\ V$ | item weight (lb), volume (in³) |
| $h$ | per-pick **handling term** $= c_w\,g_w(w) + c_v\,g_v(V)$ (weight + volume effort; the forms $g$ differ by channel — see below) |
| $q$ | quantity picked |
| $y,\ M(y)$ | shelf height; its **height-bracket multiplier** |
| $D_b$ | per-bin **travel** cost (entrance-relative, Manhattan) |
| $x_{\text{pace}},\ y_{\text{pace}}$ | per-inch paces $\tfrac{1}{12 v_x},\ \tfrac{1}{12 v_y}$ for speeds $v_x,v_y$ (ft·s⁻¹) |
| $f_s,\ q_s$ | SKU $s$'s relative pick frequency, pick quantity |
| $\text{lift}(s,p)$ | co-occurrence strength of SKUs $s,p$; $\text{co-occur} = \sum_p(\text{lift}-1)f_p$ |
| $\beta,\ \lambda$ | affinity-reward weights |
| $c_{\text{cart}}$ | cart-swap penalty; cart capacity `cap` |
| $\ell(b)$ | per-bin **labor primitive** (placement proxy, below) |

## Pick time

One pick at bin $b$, from [`Warehouse/Pick.py`](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Warehouse/Pick.py). The two channels share the **shape** but differ in the
handling term $h$ and travel speeds. **Store** channel (`store` calibration):

{{ pick_time_formula('lt0', 'store') }}

**Fulfillment** channel (`ful_calibrated` calibration) — a gentler logarithmic weight term and
faster along-aisle travel:

{{ pick_time_formula('lt0', 'ful_calibrated') }}

The four **calibrations** (two per channel) keep this shape and differ only in the weight/volume
term and the height multipliers $M(y)$:

{{ pick_calibration_table('lt0') }}

`store_high_weight` steepens the store weight exponent to $w^{2}$ (heavy items hurt more);
`ful_calibrated_fast_walkers` raises the cross-aisle travel speed (placement matters less when
walking is cheap).

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
[makespan](glossary.md#makespan). The **cart** term is where the two channels diverge most: the
`StoreCart` capacity is large (swaps are rare, $C\approx 0$), while the small `FulfillmentCart`
swaps often — which is exactly the lever `Rank_cartlabor` targets.

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

The suite is 17 restock families. It is built as **brackets**: for each lever there is a maximiser
and a minimiser that bound how much the lever is worth. **The maximising controls (`tmax`, `cmin`,
`expn`, `rank_maxlabor`) are *designed to lose*** — they deliberately place badly, so they read
*worse* than FIFO. This is the general lesson the [Everything else](everything-else.md) page makes
concrete: an "optimization" pointed the wrong way (or at the wrong objective) can make cumulative
task time **worse**, not better.

!!! info "Not every family runs in every channel"
    The **store** channel sweeps only the labor subset it would deploy — `fifo`, `rank_labor`,
    `rank_cartlabor` (`STORE_RESTOCKS`). The **fulfillment** channel sweeps the full suite below.
    So the [Everything else](everything-else.md) full-suite competition is anchored on
    fulfillment.

## Baseline

### FIFO — `fifo` { #fifo }
First-in-first-out: drop each arriving unit into a **uniform-random** bin of its
[BinKey](glossary.md#binkey) pool. No ordering, no affinity, no demand awareness. The
do-nothing control every other family is measured against (`uni_fifo_norsl`).

## Ranked (effort / labor)

These order the wave by the pick-effort priority above, differing in *where* they place it.

### Rank_random — `rank_random` { #rank-random }
Rank by priority, then place each unit in a **uniform-random aisle** at its lowest-`D` (front)
bin. Isolates the *ordering* effect from the *placement* effect.

### Rank_popularity — `rank_popularity` { #rank-popularity }
Rank by expected popularity (`f·q`), place each into the aisle with the **least** Σ popularity.
Spreads demand mass evenly across aisles (a dispersal control).

### Rank_labor — `rank_labor` { #rank-labor }
**Travel-aware LPT (longest-processing-time) labor balance — the store-channel winner.** Aisle
$a$'s total expected labor is $L_a = \sum_{s\in a} f_s\,q_s\,\ell(b_s)$; each unit is placed where
it least raises the busiest aisle, costliest SKU first:

$$\arg\min_{(a,\,b)}\ \bigl(L_a + f_s\,q_s\,\ell(b)\bigr).$$

### Rank_cartlabor — `rank_cartlabor` { #rank-cartlabor }
**Cart-swap-aware `rank_labor` — new in Experiment 2.** Identical to `rank_labor`, but each
aisle's balanced load also carries its **expected cart-swap cost**, so volume that would overflow
a cart disperses across aisles:

$$L_a^{\text{cart}} \;=\; \sum_{s\in a} f_s\,q_s\,\ell(b_s)
\;+\; c_{\text{cart}}\,\max\!\Bigl(0,\ \tfrac{\mathbb{E}[\text{vol}_a]}{\text{cap}} - 1\Bigr),
\qquad \arg\min_{(a,\,b)}\ \bigl(L_a^{\text{cart}} + f_s\,q_s\,\ell(b)\bigr).$$

The extra term is **inert for the large `StoreCart`** (capacity swamps expected aisle volume, so
$C\approx 0$) — which is why it **ties `rank_labor`** on the store channel — but it **bites for
the small `FulfillmentCart`**, where cart swaps are frequent.

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
**Optimal-map score matching.** Each bin has a quantity-free preferred score
$\operatorname{pref}(b) = D_b + M(y_b)(t_0 + \bar h)$; each SKU's $\operatorname{target}(s)$ is the
$\operatorname{pref}$ of its bin in the labor-minimising full linear assignment problem (LAP).
Place at

$$\arg\min_{b}\ \bigl|\operatorname{pref}(b) - \operatorname{target}(s)\bigr|.$$

### Map_rank — `map_rank` { #map-rank }
**The same map, upgrade-capped.** A SKU never reloads into a bin more prime than its optimal rank,
reserving prime spots for higher-ranked SKUs future orders bring:

$$\arg\min_{\,b\,:\,\operatorname{pref}(b)\,\ge\,\operatorname{target}(s)}\
\bigl(\operatorname{pref}(b) - \operatorname{target}(s)\bigr).$$

## Cluster-map (map + cohesion)

### CluMap — `cluster_map` { #cluster-map }
Mix `map` with clustering: choose the aisle **cohesion-first** (most demand-weighted affinity to
existing members), anchor the unit at its favoured map location, and compact it toward the
partners' column centroid.

### CluMapRk — `cluster_map_rank` { #cluster-map-rank }
Upgrade-capped `cluster_map` — same cohesion + compaction, but never settles more prime than its
map target.

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
**Minimise within-aisle span — the fulfillment-channel winner.** Place the SKU in the column
**nearest** the partner centroid, shortening the sweep path:

$$\arg\min_{b}\ \lvert x_b - c_x \rvert.$$

In Experiment 1 `comp` was a bracket *control* for the co-demand lever; in the small-cart
fulfillment channel it emerges as the **best total-task-time strategy** (see
[Highlights](highlights.md)).

### Expand — `expn` { #expn }
Maximise within-aisle span — place it **farthest** from the centroid (counter control):

$$\arg\max_{b}\ \lvert x_b - c_x \rvert.$$

The `comp ↔ expn` gap measures how much the co-demand lever is worth.
