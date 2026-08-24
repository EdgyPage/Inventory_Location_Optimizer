---
name: by-initial-aggregate-needs-three-profiles
description: "the cross-profile by-initial significance suite needs 3+ inventory profiles and the standard two-inventory sweep has 2, so it legitimately renders nothing"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-24T00:01:28.144Z
---

`agg.sig` / `agg.tables`' **by-initial** fork (the default preset's branch) pairs uni vs
opt **across profiles** and needs `MIN_PAIRED_PROFILES = 3`. A standard sweep has one
profile per inventory variant, so an aggregate group holds **two** — below the floor. The
suite therefore produces no cross-profile heatmap and no forest plots, and that is
correct, not a bug.

Measured on the Experiment-8 sweep (2026-08-23): 17 of 17 assignment functions have both a
uni and an opt arm; 2 profiles per group.

**Why:** the retired fallback message said "no uni/opt pairs", which is false here and
sends the next reader looking for a bug in the arm naming. It was also invisible for the
whole life of the module, behind the `NameError` in [[a-grant-is-not-an-output]].

**How to apply:** the log now names which of three reasons applies
(`paired_profile_diagnosis`). If you actually want cross-profile by-initial significance,
add inventory variants to the sweep — nothing about the code will fix it. The **per-leaf**
significance suite (`sig.by_initial`) is unaffected and pairs by batch, so use that for a
single sweep.
