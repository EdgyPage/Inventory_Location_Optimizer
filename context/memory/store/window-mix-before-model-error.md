---
name: window-mix-before-model-error
description: "A closed form checked against a finite run window can read 1-2% off on every per-unit term at once; weight it by the run's realized per-SKU units before calling the gap model error"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f4a2defe-0615-4f15-b8aa-784772e62445
  modified: 2026-09-09T01:42:52.926Z
---

When a closed-form expectation is compared against a finite run window (the 40-day era check),
a residual that is UNIFORM across every per-unit term -- including one that is exact per pack,
like receiving -- is the window's SKU mix, not the model. Weight each SKU's closed-form price by
the units the run actually processed before judging the fit.

**Why:** On department-calibration 28 (2026-09-08) the store's put price read +1.8% high after the
lot fix while packs per unit read +0.4%; receiving (exact per pack) read +1.9% too. The run had put
away 249,640 of the script's 255,817 units; the 9,763 standing at day 40 (day-40 lots, pending
re-offers) carried a mean handling term of 91 against the script's 64 -- the heavy SKUs are what
stands. Mix-weighted, the same closed form read +0.21% put / +0.01% receiving.

**How to apply:** Re-price per SKU and weight by realized units (the harness pattern was a
per-SKU `ScriptTotals` through `implied_reorders`, then `Σ realized_units[sku] × price[sku]`).
Compare per-pack and per-unit ratios, never per-day rates, against a window that ends with work
standing. See [[a-count-is-not-a-claim]] and [[a-right-site-total-hides-two-wrong-shares]].
