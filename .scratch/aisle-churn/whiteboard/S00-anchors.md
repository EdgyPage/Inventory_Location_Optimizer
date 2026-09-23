# S00 — anchors: what the simulator charges, and where

**Question.**  Before any expectation is taken: exactly what does the simulator do and charge,
term by term?  Every later session cites this table, so a model is never built on a paraphrase.

**Method.**  Read at HEAD (2026-09-23, after `3bcd5228`) by three read-only passes over the
stock, pick and inbound code.  Anchors are `file:line` at that commit.

## Demand

| What | Code | Statement |
|---|---|---|
| lines per day | `Optimization/simdriver/era_coverage.py:85-96` | n_c = DEMAND_c · N_c (declared: STORE 0.00245335, FF 0.0181380) |
| lines per batch | `Warehouse/picking/Workload_Builder.py:364-366` | k = max(1, min(N, round(Normal(mf·N, sf·N)))), mf = n/N, sf = mf·cv; cv = 1/3 store, 1/4 ff |
| which SKUs | `Workload_Builder.py:292-337` (v3 sampler) | k DISTINCT SKUs without replacement, weight f_s · Π lift(a, s) over partners already drawn |
| units per line | `Warehouse/catalog/Demand.py:121-123` | q = max(1, Poisson(λ)), E[q] = λ + e^{-λ}; λ = clamp(round(qty_rate), 1, 20) |
| batches | — | one batch is one site day; independent (`random.Random(seed_batches + i)`) |

## Levels (the declaration)

| What | Code | Statement |
|---|---|---|
| daily units | `Optimization/simconfig/coverage.py:165-179` | d_s = n · (f_s / Σf) · E[q_s] |
| lead | `coverage.py:226-233`, `:113-162` | ℓ = supplier·unit + E[ceil(L/D)]; lognormal trailer lead, median 480 min σ 0.7 → 1.766 d |
| line floor | `coverage.py:182-190` | L = max(1, ceil(f·E[q] − 1e-9)); f solved so first-pass fill ≥ √c, c = 0.95 (`:521-596`) |
| order-up-to / reorder point | `coverage.py:200-223`, `Order.py:125-141` | Q = max(L, round(C·d)), rp = min(Q−1, max(L, round(d(ℓ+safety)))); C = 10, safety 2 |
| pipeline | `coverage.py:193-197` | P = max(0, round(d·ℓ)) |

## Reorders

| What | Code | Statement |
|---|---|---|
| flag | `Warehouse/inventory/inventory_reorder.py:246-274` | after a pick, on_hand + queued + deferred ≤ rp → flagged |
| fire | `inventory_reorder.py:501-569` | ideal = Q + P − position; qty = max(1, round(Normal(ideal, ideal·cv))) if supply_cv > 0 else ideal |
| phases | `inventory_reorder.py:782-839` | tick → reclaim → advance leads → fire → release → receive → put-away |
| lot law (closed form) | `Optimization/simconfig/staffing.py:506-552` `fired_lots` | base stock: q//Q lots of Q plus one of q%Q; lots below P+1 priced at P+1 |

## Pick

| What | Code | Statement |
|---|---|---|
| per line at bin | `Warehouse/picking/Pick.py:144-169`, `kernel/cost_model.py:103-120` | M(y) · (I + q·p + q·v)  — **attached: `PickConfig.closed_form`** |
| height bracket | `cost_model.py:21,55-60` | (96: 1.0), (240: 1.2), (∞: 1.4) |
| tasks | `Workload_Builder.py:608-687` | one task per aisle per batch; bins drained smallest-on-hand first, then location (ADR-0003) |
| path | `Workload_Builder.py:690-714`, `fast_pick.py:99-228` | start (0,0) per task; bayX ascending; travel |Δx|/(12 v_x) + |Δy|/(12 v_y); no return trip; one-way lanes add the exit |
| cart | `cost_model.py:278-290` | next-fit by volume; swap adds `cart_swap_coef` (store 300 s, ff 240 s); cart carried across a picker's tasks |
| labour measure | `Optimization/metrics/Simulation_Analytics.py:239-308` | task_makespan = Σ task time (labour); duration = batch makespan |
| configs | `Optimization/simconfig/configs/store.py`, `ful_calibrated.py` | store I=15, p=0.5, w pow:1.5 ×0.58, vol log:2 ×0.7, 3/2 ft/s, two-way; ff I=10, p=0.5, w log ×0.7, vol log ×0.09, 2/4 ft/s, one-way, all bins < 96 in |

## Inbound

| What | Code | Statement |
|---|---|---|
| trailer | `Inbound/trailer.py:44,79-94,134-148` | 26 positions × 48³ in³; next-fit by volume; departs every drain |
| trailer lead | `Inbound/transit.py:128-151` | median · exp(σ·Z_seq), keyed by trailer seq |
| receive per pack | `Inbound/unload.py:100-125` | p + I + q·v, once per pack, no M, no travel — **attached: `UnloadCost.closed_form`** |
| receiving crew | `Inbound/receiving.py:1093-1203` | split door teams, cap 10 per door × 4 doors; greedy earliest-free; start gate at the whistle |
| put per pack | `Warehouse/operations/putaway.py:131-142` | travel from mouth + M(y)·(I + q·p + q·v) — **attached: `PutawayCost.closed_form`** |
| crew coefficients | `putaway.py:from_pick`, `unload.py:from_putaway` | I_put = I·0.5, p_put = p·0.2; I_recv = I_put·1.0, p_recv = p_put per pack |
| crews | `staffing.py:652-662` | crew = max(1, ceil(load/(S·ρ))), S = 28,800 s, ρ = 0.85 |
| yard fee | `Performance_Evaluations/common/frames.py:249-293` | overage = max(0, (emptied − arrived)/86,400 − threshold) |

## Placement (what decides a bin)

Candidates are always EMPTY bins of the unit's class (`inventory_common` BinKey); a full class
tops up the SKU's own fullest bin (`Inventory_Management.py:995-1018`).

| Rule | Decides by |
|---|---|
| fifo | uniform random free bin in the class (`inventory_common.py:456-464`) |
| rank_random | random live aisle, then its lowest-D bin |
| rank_popularity | aisle with the least Σ f·q, then lowest-D bin |
| rank_cartlabor | LPT aisle-load balancing plus a cart-volume penalty |
| rank_minlabor | marginal f·(M(I+v) + D) with a partner pull |
| map | nearest free bin to the SKU's LAP target |

**Residual known at the outset.**  `expected_travel.PlacementDist.initial` still drains a
SKU's bins forward-pick first (`expected_travel.py:235-236`); the simulator drains
smallest-on-hand first.  S06 corrects it in the research copy.

**Next.**  S01: the sampler's inclusion probability against the line share, because every
per-SKU rate below depends on it.
