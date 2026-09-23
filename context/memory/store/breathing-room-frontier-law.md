---
name: breathing-room-frontier-law
description: "A ranked placement rule spends each height bracket's free bins from the cheap end; LPT aisle choice is water-filling; long-run ground share = occupied ground share (s* = sigma_G) -- a velocity-blind rule cannot beat its own occupancy"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-23T15:35:20.665Z
---

Verified 2026-09-23 on the 400k campaign (aisle-churn S09, `.scratch/aisle-churn/whiteboard/S09-breathing-room.md`).

- The store free pool is at a FLOW EQUILIBRIUM: bins freed/day = placements/day (~1,740), size
  constant; only its quality moves. Under uni_rank_cartlabor free ground bins fell 36,766 -> 25,933
  in 25 days and the ground share of fresh placements 64% -> 32% over 40 days (fifo flat at 20%).
- `_TravelBalancedPool.take` picks the bracket minimising M_b*h + D_b (h = labor_cost), and the
  aisle by LPT on the ledger's expected labour = WATER-FILLING (effective receiving aisles
  predicted within ~10%, e.g. 105/67 vs 105/71). Pooling a class as one aisle over-predicted
  block-1 ground share 80% vs 64%; per-aisle water-filled fronts land within 4 points.
- Steady state: once the good free pool is spent a bracket hands out what it frees, so
  s* = n_b nu_b / sum n nu = sigma_G with equal turnover (opt start sits there: 24% predicted,
  23.7-25.1% measured). Only a TURNOVER differential (fast packs in good bins) beats it; the
  ranked rules are velocity-blind in bracket choice (heavy low, not fast low).
- The store restock gap (uni rank vs fifo, -0.80%) is -0.60% height on fresh picks (M 1.162 vs
  1.263), -0.04% setup, -0.16% travel/tasks. opt_rank vs opt_fifo (-3.63%) is mostly the INITIAL
  layout, since opt_fifo == uni_fifo ([[fifo-restock-ignores-initial-placement]]).
- fifo vs lifo unloading are EXCHANGEABLE pairings: E[gap] = 0, sd 0.012% of pick time at k=1
  (rearrangement bound; an oracle order could win at most 0.65%).

**Why:** this is the physics behind the user's "churn the aisles for breathing room" direction.
More demand spends the good pool k x faster (B = G_free / (m (s - sigma_G))) while raising phi
([[fresh-bin-law]]). **How to apply:** code in `Optimization/simconfig/models/churn.py`
(frontier_shares, water_level, STEADY, rearrangement); scripts `s09_front.py` / `s09_waterfill.py`.
