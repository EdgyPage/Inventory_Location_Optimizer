---
name: aisle-ceiling-and-dock-gate
description: "Store picking caps at k* = shift / busiest-aisle work ~ 12.8x demand (one picker per aisle-day; day-cut backlog grows whatever the crew); the unloading order only matters past rho_door = lambda_T*occ/(doors*shift) = 1 (doors held per trailer), 24-27 trailers/day"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-23T17:19:30.233Z
---

Measured 2026-09-23 on the `_churn_probe` 40k grid (aisle-churn S04, S11, S12;
`.scratch/aisle-churn/whiteboard/S04-dock-and-aisle-gates.md`).

- **Aisle ceiling.** The simulator hands each aisle's day to one picker, so work divides only across
  aisles. k* = S / max_a W_a(1) = 28,800 / 2,251 = 12.8 on the store (fulfillment 40). Past it the
  store's day-cut carry grows unboundedly (per picked unit 0.24 at k10 -> 2.0 at k20 -> 3.0 at k30)
  while labour capacity is still 1.4x demand and only 31/56, 42/84 pickers find work. The era's crew
  derivation assumes divisible work and cannot see it.
- **Dock gate.** A trailer holds a door for its whole unload (~1.15 h with a 10-person team, 8% over
  work/team). rho_door = lambda_T E[occ] / (doors S): 0.93 stable (~8 h waits), 1.01 unstable
  (7 -> 25 h over 40 days). A labour-seconds queue (Lindley on 40 x 28,800 s) predicts NO backlog --
  wrong capacity. fifo vs lifo unloading is exchangeable (0/56 significant) below the gate and
  lifo wins 2-3% on fulfillment above it.
- The record's declared inbound load over-states the dock by 11-34% at high k because picking
  falls behind (reorders follow picks); predict rho_door from the REALISED trailer rate.
- **CORRECTED 2026-09-23 at 400k (S16):** the capacity is min(receiving crew, doors x team) x shift.
  The derived receiving crew is sized to ~0.85 utilisation, and while it is smaller than the 40 door
  slots it grows with demand, so realised utilisation sits at ~0.85 whatever k (0.854, 0.846 measured)
  and occupancy per trailer falls (1.55 h -> 1.20 h -> 0.9 h). The yard can only go unstable once the
  load outgrows the door slots: at 400k the realised load is W(k) = 0.140 M + 0.4376 M k s/day, the
  gate k = 2.31. Confirmed: order null 0/8 at rho 0.85 and 0.96, 5/8 significant (all lifo-cheaper,
  both channels) at offered rho 1.07, waits 13.7 -> 49.6 h. Measure the gate on OFFERED load
  (arrivals x occupancy); unloaded seconds cap at the capacity. `models/dock.SITE`.

**Why:** any demand-density sweep past ~13x on this layout measures the aisle ceiling, not churn;
any unloading-order experiment below the dock gate measures noise. **How to apply:**
`Optimization/simconfig/models/dock.py` (DOCK, AISLE, yard_wait, overflow). Raising the ceiling needs
multi-picker aisles or more store aisles -- a model change, the user's decision. Related:
[[breathing-room-frontier-law]], [[fresh-bin-law]], [[site-dock-is-shared-across-channels]].
