---
name: confidence-lever-saturates-at-one-line
description: "--first-time-confidence below ~0.80 declares the same stock as 0.80 (the one-line floor, f = 1.0); stock depth can shorten cover only ~1.2x, while demand density k cuts it ~1/k and at c=0.95 RAISES the solved floor"
metadata:
  node_type: memory
  type: project
  originSessionId: 31cf3b4f-6a24-4bd3-9118-cfbd0ff52dc6
  modified: 2026-09-23T15:35:34.789Z
---

Measured 2026-09-23 through the record's own solver (`coverage.solve_floor_lines`) on the 40k
perf catalogue (aisle-churn S10, `.scratch/aisle-churn/assets/s10_knobs.py`):

- store k=1: c=0.95 -> f 1.308, sum Q 306,816, cover 637 d; c=0.80 and 0.60 -> f 1.000,
  249,554, 526 d. Identical at 0.80 and 0.60 everywhere except store k=30 and fulfillment k=3.
- Demand density: store cover 637 -> 223 -> 78 -> 33 d at k = 1/3/10/30; fulfillment leaves the
  floor at k~10 (68% on floor at c=0.95).
- At c=0.95 a higher k raises the solved floor (store f 1.31 -> 2.0 at k=30; stock x1.55):
  more lines arrive inside the lead, so the same confidence needs deeper shelves.

**Why:** a grid over c wastes runs past 0.80, and "stock depth" is not a strong churn lever.
**How to apply:** sweep c in {0.95, 0.80} only; use demand density (`--store-demand`,
`--ff-demand`, era-only) for churn. Related: [[fresh-bin-law]], [[breathing-room-frontier-law]].
