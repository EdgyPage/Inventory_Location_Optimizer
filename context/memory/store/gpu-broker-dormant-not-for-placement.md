---
name: gpu-broker-dormant-not-for-placement
description: GPU broker is kept as dormant validated infra; production placement is deliberately NOT GPU-accelerated
metadata:
  type: project
---

The VRAM-budgeted GPU broker + governor (`Optimization/gpu_broker.py`, `gpu_client.py`,
`Tests/test_gpu_governor.py`, `Tests/bench_gpu_concurrency.py`, committed `b758c4a`) is kept as
**dormant, validated infrastructure** — it has no production consumer by design.

**Why:** We investigated wiring it into production placement and rejected it. The earlier benchmark
(`bench_gpu_placement.py`) measured a *dense U×C cost-matrix argmin* and reported a 27–54x GPU win,
but that operation does not exist in production. The real placement code reduced it away:
`init_travel_costs` precomputes `b._D` and a per-aisle index bisect-sorted by `_D` (maintained
incrementally), so every placement fn in `Warehouse/Assignment_Functions.py` reduces a unit to
**one representative bin per aisle** and scans **O(N_aisles)** with lazy CSR + early termination — not
O(C bins). The dense kernel would be (a) slower (brute force vs the reduction), (b) non-equivalent
(omits height brackets, the λ·affinity aisle reward, and the partner-centroid pull in the real
objective), and (c) unbatchable (affinity term depends on placement state that mutates every unit).

**Auction spike (also NO-GO, commit `ec739f8`, see `docs/gpu_auction_assessment.md`):** We then
prototyped the user's idea — reformulate a placement wave as a linear assignment solved by a parallel
**auction** (prices resolve bin clashes without eviction cascades) on GPU. Measured result killed it:
optimal placement IS ~10–19% cheaper than the greedy (real headroom), but the single-eps auction needs
~1e5 bidding rounds on *structured* costs (near-tied bins) → on GPU each round is a kernel launch → 61s
at 100×800; `scipy.linear_sum_assignment` ALSO collapses at scale on structured costs (~97s at
2000×40000, so "lift the U≤1200 cap" doesn't scale either); and the affinity (QAP) fixed-point
**oscillates**, doesn't converge. The fast greedy (~0.09s at any scale) is fast *because* it's the
reduced per-aisle scan. `Optimization/gpu_auction.py` is a correct-but-slow reference kept for the record.

**How to apply:** Do NOT re-attempt GPU acceleration of placement (neither the dense-argmin nor the
auction/LAP route). The greedy must stay sequential/CPU. Per-batch wall is `reord` ~44% (the sequential
greedy) + `build` ~41% (Batch/Task construction + order sampling — the latter now deduped per-family by
the batch precompute, commit `93ef1fd`; sampling itself resists GPU). The only untested GPU-viable assignment
idea is Sinkhorn/entropic OT (dense matmul, not 1e5 rounds) — a research spike, not a quick win. The
broker stays ready only for a genuinely dense future workload. Don't delete it; don't wire it into the sim.
