---
name: a-count-is-not-a-claim
description: "an absolute count exponent conflates \"work per unit rose\" with \"there are more units\"; divide by a denominator before concluding anything"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T19:25:10.255Z
---

A growth-ladder exponent on a raw call count cannot tell you **which** of two different problems
you have, and they have different fixes:

* the work per unit got more expensive, or
* there are simply more units.

Measured case (2026-08-26): under a put-away staging floor, candidate-bin scans fitted
**k = 1.74** — which says nothing. The ratios said it outright: bins-scanned **per placement**
went 2.47 → 71.7 while takes **per placement** stayed flat. So per-placement work was unchanged
and a prologue was merely being re-paid 17.8x more often. The fix followed immediately from the
ratio and not at all from the exponent.

**Why:** a denominator turns a number into a claim. `calltree_growth.py` now emits
`flows_per_placement` and flags ratio exponents at a much lower threshold (0.30), because a
ratio above zero is already work-per-unit growth.

**How to apply:** when a count grows super-linearly, divide by the thing it should be
proportional to *before* forming a hypothesis. And when adding a counter, check what it
actually counts — my own `bins_scanned` anchor silently reported pool OPENS (2,084 against
2,084) because the optimisation had deleted the per-candidate call it was meant to count.
Related: [[knees-hide-from-r-squared]], [[hand-run-test-tiers-rot-silently]].
