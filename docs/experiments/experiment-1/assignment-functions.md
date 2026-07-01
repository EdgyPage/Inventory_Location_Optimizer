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

Most families rank the wave by a shared **pick-effort priority** before placing it, then place
the highest-priority unit first so it claims its extremal bin:

`priority = f_i·(pick_intercept + pick_weight_coef·ln(w) + pick_volume_coef·ln(v)) + β·co_occur`

where *f_i* is [relative pick frequency](glossary.md#f-s), *w*/*v* are weight/volume, and
`co_occur` is the [affinity](glossary.md#co-occur) subsidy.

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
**Travel-aware LPT (longest-processing-time) labor balance — a top-3 winner.** Each unit goes
to the `(aisle, bin)` that least raises the busiest aisle's total labor
`L_a = Σ f_s·q_s·ℓ(b)`, costliest SKU first. Equation on the
[lifecycle page](comparison-overview.md#top-3-assignment-functions).

### Rank_minlabor — `rank_minlabor` { #rank-minlabor }
Greedy **minimiser** of expected total task labor: fuses golden-zone height (`M(y)`),
effort-to-front (`D`), and affinity compaction into one marginal-cost score. Consolidates
rather than balances.

### Rank_maxlabor — `rank_maxlabor` { #rank-maxlabor }
The exact **maximiser** mirror of `rank_minlabor` (high/far bins, scattered partners) — a
worst-case control that should land *worst* on task labor. Designed to lose.

## Map (optimal-map score matching)

### Map — `map` { #map }
**Optimal-map score matching — a top-3 winner.** Each bin has a quantity-free preferred score
`pref(b)`; each SKU a target from the labor-minimising full linear assignment problem (LAP);
place at `argmin_b |pref(b) − target(s)|`. Equation on the
[lifecycle page](comparison-overview.md#top-3-assignment-functions).

### Map_rank — `map_rank` { #map-rank }
**The same map, upgrade-capped — a top-3 winner.** A SKU never reloads into a bin more prime
than its optimal rank, reserving prime spots for higher-ranked SKUs future orders bring.

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
Minimise `f_s·D − β·co_occur`: hot SKUs to low-`D` (front) bins → less within-aisle walking.

### TripMax — `tmax` { #tmax }
Maximise the same score (hot items to the **back**). Worst-case travel control; brackets `tmin`.

## Affinity bracket

### MaxClu — `cmax` { #cmax }
Maximise **cohesion** `Σ (lift(s,p) − 1)·f_p`: send each SKU to the aisle where its
co-picked partners already sit → fewer aisle visits per batch.

### MinClu — `cmin` { #cmin }
Minimise cohesion (scatter partners across aisles). Anti-affinity control; brackets `cmax`.

## Co-demand bracket

### Compact — `comp` { #comp }
Minimise within-aisle **span** to co-demanded partners — place each SKU in the column nearest
its high-lift partners, shortening the sweep path.

### Expand — `expn` { #expn }
Maximise within-aisle span (partners as far apart as possible). Counter to `comp`; the
`comp ↔ expn` gap measures how much the co-demand lever is worth.
