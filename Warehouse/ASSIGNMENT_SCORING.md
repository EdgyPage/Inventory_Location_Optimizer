# Assignment Scoring Objectives

Reference for the placement (assignment) scoring used in
`Warehouse/Assignment_Functions.py`. An `Inventory_Manager` merely *receives* a
per‑unit `assignment_fn` (and optional `batch_assignment_fn` for ranked drains);
these builders produce them. Every per‑unit fn shares one shape — the scorers differ
only in how they rank aisles.

## Notation

The realised aisle **workload** `W` (the time to clear one aisle) decomposes as:

$$W \;=\; D \;+\; P \;+\; C$$

- **`D`** — *travel* (distance) cost. Per bin: `D = x_speed·x_phys + y_speed·y_phys`
  (entrance‑relative). Per aisle (realised): `x_traversed·x_speed + y_traversed·y_speed`.
- **`P`** — *pick* time: `Σ_stops (intercept + weight_coef·ln(w)·q + volume_coef·ln(v)·q)`.
- **`C`** — *cart* penalty: `cart_swap_coef · max(0, carts−1)`.

`W` is computed by `aisle_workload(...)` (`Optimization/Workload.py`) and recorded
per task as `TaskStats.W`. It is a **measurement**, not a placement score. The
placement scores below are assignment‑time objectives, two of which are built on the
per‑bin travel term `D`.

## How a per‑unit assignment fn works

`_build_aisle_score_fn` (the shared core):

1. Reduce candidate bins to **one representative per aisle** by `D`‑rank
   (`_aisle_extremal_bins`): min‑`D` (front bay) for minimising scorers, max‑`D` for
   travel‑max.
2. Score each aisle (the pluggable part).
3. Place in the extremal‑scoring aisle (`_pick_extremal_aisle`), update aisle state
   (`_commit_aisle`).

Two named scorers plug into step 2.

## Travel score — about distance

`travel` scorer → `travel_min` / `travel_max` (aka trip‑min/max):

$$\text{score} \;=\; f_s\cdot D \;-\; \beta\cdot\text{co\_occur}$$

`f_s` = SKU pick frequency. **Minimising** puts high‑frequency SKUs in low‑`D`
(near‑entrance) bins → less within‑aisle walking. The small `−β·co_occur` is a
co‑location subsidy (see below). Secondary tie‑break = aisle load
(`aisle_demand_sum + f_s·q_s`). Answers: *"how far does the picker walk for this
item, given how often it's picked?"* Inputs: demand + bin geometry.

## Cohesion / cluster score — about co‑occurrence

`cohesion` scorer → `cohesion_min` / `cohesion_max` (the cluster strategies):

$$\text{co\_occur} \;=\; \sum_{\text{partner}\in\text{aisle}} \text{lift}(s,\text{partner})\cdot f_{\text{partner}}$$

(`_demand_weighted_delta_lift`) — the demand‑weighted affinity between the SKU and
the partners already in that aisle. The cluster score is this term **as the sole
primary objective**; `D` is only the tie‑break (front bay). **Maximising** collapses
co‑picked SKUs into the *same aisle* → fewer aisle visits per batch (fewer tasks).
Answers: *"how many of this item's co‑picked partners are already here?"* Inputs:
the affinity matrix + current aisle composition + partner frequencies.

## Load score — balancing (legacy)

`load` scorer (`build_load_*`, currently unused by the registry): minimises/maximises
the L2 norm of predicted aisle loads `L_a = W + λ(W/k)^γ·lift_sum` — a load‑balancing
objective across aisles.

## The contrast

| | Travel (`D`) | Cohesion |
|---|---|---|
| Measures | `f_s·D` — frequency × distance to the bin | `Σ lift(s,p)·f_p` over aisle members |
| Optimises | within‑aisle **walking distance** for hot items | **co‑location** of co‑picked items |
| Cost term targeted | within‑aisle travel (the `D` part of `W`) | the **number of tasks/aisles** per batch |
| Depends on | demand + bin geometry only | affinity graph + who's already in the aisle |
| Bin within aisle | the score **is** the bin choice (`D`) | aisle choice is the point; `D` is tie‑break |

`trip_min` **blends** the two (`f_s·D − β·co_occur`); the cluster scorer is **pure
cohesion**. That blend‑vs‑pure split is exactly what the `travel`/`cohesion` named
scorers make explicit.

## Programmatic names + registries

Composed names: `travel_min`, `travel_max`, `cohesion_min`, `cohesion_max`,
`uniform_min`, `load_min`, `load_max`. Looked up via `ASSIGNMENT_BUILDERS` /
`BATCH_BUILDERS`; `SCORER_NEEDS[name] = (needs_affinity, needs_demand)` says which
aisle state must be prepared before a scorer can run.

## Caveats

- **No cross‑aisle distance** in the cost model: cohesion pays off only via **task
  count** (same‑aisle co‑location), not graded route distance.
- Cohesion only matters when batches are **affinity‑correlated** (the lift‑weighted
  sampler in `Workload_Builder.Batch`); with uniform batches there are no co‑picked
  partners to cluster around.

See `Optimization/PERFORMANCE_MODEL.md` for how the realised `Σ f·D` (layout quality)
evolves over batches and converges to each reorder rule's characteristic level.
