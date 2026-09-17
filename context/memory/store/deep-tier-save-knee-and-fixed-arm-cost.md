---
name: deep-tier-save-knee-and-fixed-arm-cost
description: "two deep-tier confounds that make every arm look superlinear — save_s is ~half the run with a knee at the top rung, and each arm pays ~48s before its batch clock starts"
metadata: 
  node_type: memory
  type: project
  originSessionId: 738f91ee-ad65-4791-a2df-c8daafeefa81
  modified: 2026-09-17T00:27:14.614Z
---

Measured on the 2026-09-16 deep ladder (10k → 80k SKUs, 136 arms, 5 rungs). Both confound any
per-arm growth reading and neither is a placement cost.

**`save_s` is the largest growing term in the deep tier and has no owner.** It runs 35.9 % → **48.6 %**
of `total_s` across the ladder with local exponents `1.27, 1.06, 0.97, 2.30` — a knee at the top
rung. On `total_s`, 33 of 34 arms read as accelerating (median last local k 1.68); on
`total_s − save_s`, 22 of 34 and **1.09**. Always read arm growth with save excluded; the ladder now
carries `per_arm_ex_save_s` beside `per_arm_total_s` for this.

**Each arm pays ~48 s of fixed cost before its batch clock starts.** That is why the
commensurability check (`Σtotal_s / workers` vs the wall) reads 0.26–0.57 rather than ~1: the gap
divided by arm-slots is 48.4, 50.0, 77.0, 107.2, 139.0 s — flat across the first doubling, then
catalogue-proportional. `max_tasks_per_child` is pinned at 1 (see [[worker-recycling-pinned-at-one]]),
so every arm spawns a fresh interpreter, re-imports, and reloads the catalogue.

Consequences: the section exponents are **not** contaminated (they come from per-arm sums, and this
is per-arm *startup*); the deep **wall** is the number not to fit (k = 0.72, sub-linear, because a
fixed term amortizes — fit it and you conclude the simulation gets cheaper per SKU); and early local
exponents are depressed, so an amortizing linear arm reads as "accelerating".

With both removed, exactly four of 34 arms are superlinear — `uni_cmin`, `uni_cmax`, `opt_cmin`,
`opt_cmax` at k = 1.26–1.29. See [[a-fitted-exponent-cannot-see-its-own-shape]].
