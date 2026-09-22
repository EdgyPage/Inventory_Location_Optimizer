---
name: gain-evaluator-to-be-replaced-approximate
description: "Decided 2026-09-22: the exact plan_order gain evaluator will be REPLACED, not sped up further, by an approximate evaluator gated on order agreement with the exact plan (Kendall tau >= 0.9 median, top-1 >= 0.8), accepted at cell level on overage/pick-owed within the ranking's own noise floor"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-22T22:59:55.053Z
---

**The decision.** Opened 2026-09-22 in `.scratch/inbound-throughput/map.md` (destination 1),
from the same grilling session that produced [[pick-owed-cannot-see-inbound-at-this-demand]].
The exact `plan_order` evaluator is not going to be optimized further — [[the-gain-sweep-cannot-be-made-incremental]]
showed its T(T+1) cost is irreducible by memoization (reuse across candidates measured at ZERO,
2026-09-20), and [[the-trailer-bound-buys-wall-with-discrimination]] showed the one available
lever (`INBOUND_TRAILER_BOUND`, k(k+1) instead of T(T+1)) is REFUTED as a substitute because
bounding the candidate set is mechanically a step toward arrival order and keeps only 12% of a
gain policy's separation from `fifo`. With both the incremental and the bounded routes closed,
the plan is to replace the exact evaluator outright with a cheaper approximate one, gated so the
replacement is only trusted where it agrees with the exact plan it replaces: Kendall tau >= 0.9
median and top-1 agreement >= 0.8 against `plan_order`, accepted at cell level when overage and
`pick_owed_s` both land within the ranking's own noise floor of the exact evaluator's numbers
([[pick-owed-s-replaces-flow-totals-for-unload-ranking]] for that floor).

**Why:** an order-of-magnitude cost cut is the stated destination (at least 10x cheaper than
`plan_order` at the campaign's yard depth) and neither of the two levers already tried
(memoization, bounding) can deliver it without destroying the thing the metric measures.
Gating on rank agreement rather than on the priced score directly is deliberate: the priced
score itself barely separates cells at this site's demand
([[pick-owed-cannot-see-inbound-at-this-demand]]), so a replacement judged only on score
agreement could pass by being equally uninformative, not by being equally right.

**How to apply:** before trusting any new fast unload evaluator's ranking, check it against
`plan_order` on tau/top-1 first, not on `pick_owed_s`/overage alone. Read the map's destination 1
in `.scratch/inbound-throughput/map.md` for the current status of this work; do not assume it has
landed — as of 2026-09-22 this is a decision and a gate, not yet a shipped evaluator.
