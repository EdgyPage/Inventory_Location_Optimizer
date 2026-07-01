# Assignment functions

The **assignment function** (a.k.a. restock family) is the rule that places each batch's
reorder wave — the one thing the strategies differ on (picking, demand, and the warehouse are
identical across arms). The comparison grid sweeps **32 arms = 2 initial layouts × 16 restock
families** (see [strategies.py](https://github.com/EdgyPage/Inventory_Location_Optimizer/blob/main/Optimization/strategies.py)).
This page catalogues all 16; the top-3 by cumulative task time are on the
[lifecycle page](comparison-overview.md#top-3-assignment-functions), and every arm's measured
performance is on [Full results](full-results.md). Symbols are defined in the
[Glossary](glossary.md).

!!! note "Notes"
    <!-- paste commentary here -->

## How to read the families

Every score is built on the **per-bin labor primitive** — the expected time to make one pick at
bin $b$ (defined with the [pick-time model](comparison-overview.md#3-pick)):

$$\ell(b) \;=\; M(y_b)\,(t_0 + h) + D_b,\qquad h = c_w\,w^{e_w} + c_v\,\log_2 V,\qquad
D_b = x_{\text{pace}}\,x_{\text{phys}} + y_{\text{pace}}\,y_{\text{phys}}$$

where $t_0$ is the pick intercept, $h$ the per-pick [handling term](glossary.md#handle-var)
(weight $w$ + volume $V$), $M(y_b)$ the height multiplier, and $D_b$ the travel cost.

Most families rank the wave by a shared **pick-effort priority** before placing it (highest
first, so it claims its extremal bin):

$$\text{priority} \;=\; f_i\,(t_0 + h) \;+\; \beta\,\text{co\_occur}$$

where $f_i$ is [relative pick frequency](glossary.md#f-s) and $\text{co\_occur}$ is the
[affinity](glossary.md#co-occur) subsidy.

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
Minimise the travel score $f_s\,D - \beta\,\text{co\_occur}$: hot SKUs to low-$D$ (front) bins →
less within-aisle walking.

### TripMax — `tmax` { #tmax }
Maximise the same score (hot items to the **back**). Worst-case travel control; brackets `tmin`.

## Affinity bracket

### MaxClu — `cmax` { #cmax }
Maximise **cohesion** $\text{co\_occur} = \sum_p \bigl(\text{lift}(s,p) - 1\bigr) f_p$: send each
SKU to the aisle where its co-picked partners already sit → fewer aisle visits per batch.

### MinClu — `cmin` { #cmin }
Minimise cohesion (scatter partners across aisles). Anti-affinity control; brackets `cmax`.

## Co-demand bracket

### Compact — `comp` { #comp }
Minimise within-aisle **span** to co-demanded partners — place each SKU in the column nearest
its high-lift partners, shortening the sweep path.

### Expand — `expn` { #expn }
Maximise within-aisle span (partners as far apart as possible). Counter to `comp`; the
`comp ↔ expn` gap measures how much the co-demand lever is worth.
