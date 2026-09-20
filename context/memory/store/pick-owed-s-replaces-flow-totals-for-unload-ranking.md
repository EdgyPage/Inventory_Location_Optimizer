---
name: pick-owed-s-replaces-flow-totals-for-unload-ranking
description: "Measured 2026-09-19: ss_prod_total cannot rank an inbound ORDERING policy (its 50-batch steady-state window against a 40-deep campaign makes it a mean over every batch, invariant to unload order once everything unloads); Optimization/Performance_Evaluations/layout/pick_owed.py's pick_owed_s replaces it, priced from where stock stands, and is comparable only within one run"
metadata: 
  node_type: memory
  type: project
  originSessionId: 54a9e9be-5880-4d00-9c09-172dafbe4eb1
  modified: 2026-09-20T04:24:41.399Z
---

**The old metric asked a question ordering cannot answer.** `ss_prod_total`'s steady-state
window is 50 batches; a phase-2 campaign cell is 40 batches deep. So the "steady-state" total is
a MEAN OVER EVERY BATCH IN THE RUN — a total in disguise. Over a window long enough to unload
everything a trailer carries, total unload seconds are per-pack and order-independent, so the
quantity cannot move with unload ORDER at all. Phase 2's exact-tie ranking
([[unload-key-does-not-rank-like-gain]], [[inbound-campaign-is-a-three-phase-funnel]]) was the
honest answer to a question `ss_prod_total` cannot be asked.

Measured 2026-09-19, two unload cells sharing one restocking pair: `fifo` restock arms were
BIT-IDENTICAL across the cells (FIFO ignores rank when placing, so arrival order cannot change
placement — consistent with [[fifo-restock-ignores-initial-placement]]); `rank_*` arms differed
in 31–35 of 40 batches and moved `ss_prod_total` by only 0.18–0.24%.

**The replacement: `pick_owed_s` (`Optimization/Performance_Evaluations/layout/pick_owed.py`,
persisted via `Optimization/persistence/Picking_Data.py`, read by
`Optimization/run_unload_ranking.py`).** It prices what the run's PLANNED batches would cost
served from where the stock stands RIGHT NOW: per SKU, the MEAN at-location cost over that SKU's
own bins, weighted by the planned lines. It shares `cost_model.per_pick` with `optimal_work`
(`Optimization/Performance_Evaluations/core/quantities.py`) and does NOT divide by it — the
weight bases differ (every catalogue SKU by relative frequency for `optimal_work`, this run's
planned lines for `pick_owed_s`) — measured 206x apart on the priced toy fixture
(11,301,662 s vs 54,971 s). **Comparable across the arms and cells of ONE run, which share a
script by construction; never across runs.** There is deliberately no floor line drawn on its
figure, for this reason.

**Why:** a flow total measured over a window as long as the campaign restates a conserved
quantity (see [[a-count-is-not-a-claim]], [[cut-is-a-level-not-a-flow]] for the same shape of
mistake elsewhere in this codebase); `pick_owed_s` is a STOCK-conditioned price, so it moves
with placement even when the flow total cannot.

**How to apply:** never cite `ss_prod_total` (or any full-window steady-state total) to rank an
inbound unloading/ordering policy — cite `pick_owed_s` instead, and only within one run's arms
and cells. Related: [[expected-travel-closed-form-is-an-asymmetric-check]],
[[toy-fixture-cannot-discriminate-unload-policies]].
