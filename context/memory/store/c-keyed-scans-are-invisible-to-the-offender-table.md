---
name: c-keyed-scans-are-invisible-to-the-offender-table
description: "calltree counts exclude C leaves, so an O(A) scan whose key is a C callable (dict.__getitem__) never reaches the offender table while an identical scan keyed on a Python lambda is convicted"
metadata:
  node_type: memory
  type: reference
---

`calltree_tracer` records every `c_call` as `kind='ext'`, and `calltree_growth._flat_counts`
skips those. So the call-count offender table **cannot see a scan whose key function is a C
callable**.

Measured 2026-09-16 on two cells that run the SAME pool with identical `take` counts:

- `ranked_popularity` — key is a Python lambda → convicted at **k = 1.963, 8,819,328 calls**
- `ranked_tmin` — key is `head_D.__getitem__` → **absent from the offender table entirely**

`tmin` is not cheaper. Its scan is the same width (115.3 aisles per take at 8,000 SKUs); only its
key is written in C.

**Why:** the exclusion is correct on its own terms — C leaves vary with OS thread scheduling and
would make `counts_fingerprint` non-deterministic. It is the *consequence* that is unstated.

**How to apply:** never read "absent from the offender table" as "cheap". When checking a
selection for an O(A) scan, fit the SCAN WIDTH — `inner_calls / takes`, or a per-placement ratio
over a counted parent — which is immune to what the key is written in. Related:
[[calltree-framework-first-findings]], [[growth-ladder-saturates-silently]].
