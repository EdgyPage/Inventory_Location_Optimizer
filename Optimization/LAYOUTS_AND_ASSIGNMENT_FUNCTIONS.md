# Layouts & assignment functions — summary

A one-page reference to the two **initial layouts** and the eight **assignment functions**
(restock/reorder rules) that the comparison grid sweeps. Every *strategy* is one cell of

```
initial layout  ×  assignment function  ×  re-slot
   {uni, opt}    ×   {8 families below}  ×  {noRSL, …}   → run-id e.g. opt_tmin_norsl
```

defined data-driven in [strategies.py](strategies.py). For the line-by-line code trace see
[ASSIGNMENT_FUNCTIONS_TRACE.md](ASSIGNMENT_FUNCTIONS_TRACE.md); for the decay/efficiency math see
[PERFORMANCE_MODEL.md](PERFORMANCE_MODEL.md). The metric that actually drives makespan is
**ΣW = total within-aisle picker work per batch** ("productivity hours", r≈0.95 with makespan);
layout depth Σf·D is the long-run lever that *feeds* W.

---

## Initial layouts (how the warehouse is stocked once, before batch 1)

| Layout | What it does | Rank key | Result |
|--------|--------------|----------|--------|
| **uni** (Uniform) | `enqueue_all` → uniform-random placement. The restock rule has not been wired yet, so the start is **always random** regardless of which assignment function follows. | none (RNG) | Starts ~62% efficient; the assignment function has to *climb* from there. |
| **opt** (Optimal) | `place_optimal`: per BinKey class, the **hottest SKU → lowest-D bin** (rearrangement-inequality optimum for Σf·D). | **frequency only** | Starts ~100% efficient, then **decays** toward whatever steady state the assignment function maintains. |

The key dynamic: **the initial layout sets the starting point; the assignment function sets the
attractor.** `opt` only stays good if the reorder rule keeps re-optimizing the churn (~11% of
bins/batch); under FIFO an optimal start collapses to ~62%.

---

## Assignment functions (how reorder waves are placed every batch)

| Key | Label | Drain | Objective (what it minimizes/maximizes) | Role |
|-----|-------|-------|------------------------------------------|------|
| **fifo** | FIFO | per-unit | none — uniform-random aisle/bin | **Baseline.** The do-nothing control. |
| **rank** | Rank | ranked | rank units by pick-effort, place each in a **uniform-random aisle** at its min-D bin | Isolates the *ordering* effect from the *placement* effect. |
| **tmin** | TripMin | ranked | **min Σ(priority·D)** — highest-effort units → globally lowest-D (front) bins | **Travel winner.** Reproduces the `opt` rule on churn → sustains the layout. |
| **tmax** | TripMax | ranked | max Σ(priority·D) — push hot items to the **back** | Worst-case travel control; brackets tmin. |
| **cmax** | MaxClu | per-unit | **max cohesion** — SKU → aisle where demand-weighted lift to existing members is highest (co-locate partners) | Tests aisle-level affinity clustering. |
| **cmin** | MinClu | per-unit | min cohesion — SKU → aisle where lift is lowest (scatter partners) | Anti-affinity control; brackets cmax. |
| **comp** | Compact | ranked | **min within-aisle span** — place each SKU in the **column nearest** its high-lift partners (short sweep path → low W) | Co-demand adjacency lever. |
| **expn** | Expand | ranked | max within-aisle span — place each SKU **farthest** from partners | Counter to comp; the **comp↔expn gap = size of the co-demand lever**. |

### The shared pick-effort priority (ranked families)

`tmin`, `tmax`, `rank`, `comp`, `expn` all order units by the same priority before placing:

```
priority = f_i · (pick_intercept + pick_weight_coef·ln(weight) + pick_volume_coef·ln(volume)) + β·co_occur
           └ freq ┘ └────────────────── per-pick handling effort ──────────────────┘   └ affinity ┘
```

Highest-priority unit claims its extremal bin first (lowest-D for tmin, highest-D for tmax,
nearest-partner column for comp, etc.), committing incrementally so later units in the wave see it.

---

## Three brackets the grid measures

- **Travel:** `tmin` ↔ `tmax` — how much hot→front vs hot→back placement is worth.
- **Affinity (aisle):** `cmax` ↔ `cmin` — whether co-locating partners across aisles helps.
- **Co-demand (column):** `comp` ↔ `expn` — whether tightening the within-aisle sweep path helps
  *beyond* frequency clustering. Wide gap ⇒ real headroom past TripMin; tie ⇒ the round-robin
  dispatcher, not placement, is the bottleneck.

## Sweep verdict (latest full sweep)

1. **`opt | tmin`** wins (+~14.6% throughput, −~19% productivity hours vs uni|FIFO). TripMin both
   reaches and *holds* the optimal layout because its reorders reproduce hottest→lowest-D.
2. **`tmin` ≈ `rank`** on travel — ordering by effort gets most of the win; the precise aisle
   choice is secondary once items are ranked.
3. **`comp` beats `expn` by ~5pp throughput / ~7.5pp prod-hours** — the co-demand lever is *real*
   but **secondary** to frequency/travel placement; it does not beat TripMin/Rank.
4. **`tmax` / `cmin` / `expn`** are the worst, confirming each bracket's direction.

### Why `opt|tmin` settles at ~82%, not 100%

`place_optimal` ranks by **frequency only**; `tmin` ranks by **frequency × effort** (it folds in
`ln(weight)`/`ln(volume)`). So tmin maintains a slightly *different* optimum than the pure-Σf·D
yardstick — arguably better for real makespan, but it reads as ~82% on the frequency-only metric.
That weight/volume term is the entire gap.
