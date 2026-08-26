---
name: knees-hide-from-r-squared
description: an r-squared gate systematically drops step changes — the biggest jump in the deep artifact scored r2=0.77 and was reported as nothing
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T19:25:22.349Z
---

`calltree_growth.py` flags a fitted exponent only when `r2 >= MIN_R2` (0.90), which is sound
for rejecting garbage fits and **exactly backwards for a knee**. A smooth power law fits well
and gets flagged; a threshold crossed between two rungs fits badly and is dropped.

The real instance (2026-08-26): `save_s` on the deep ladder ran 784 / 1,014 / 1,497 / **18,343**
seconds over 10k→80k SKUs. A single OLS fit gives k = 1.42 at **r2 = 0.77**, so the largest
super-linear jump anywhere in that artifact was reported as no offender at all. Local per-step
exponents show it instantly: **+0.37, +0.56, +3.62**.

`_knee()` now runs over every fitted series (sections, counts, flows, arm totals) and reports
when the last step's local exponent exceeds the median of the earlier steps by
`FLAG_KNEE_JUMP = 1.00`. On the real ladder it finds exactly three series and stays silent on
the linear ones.

**Why:** a global fit answers "what shape is this curve"; a knee is the claim that the shape
CHANGED. One statistic cannot serve both, and the gate that protects the first suppresses the
second.

**How to apply:** when a ladder reports "no offenders", check the knee block before believing
it — and if you add a new fitted series anywhere, add it to the knee sweep too. A two-rung
ladder cannot show a knee at all, which is why `_DEEP_LADDER` gained a 60k rung.
Related: [[a-count-is-not-a-claim]], [[calltree-framework-first-findings]].
