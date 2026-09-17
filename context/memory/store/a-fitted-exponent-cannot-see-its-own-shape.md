---
name: a-fitted-exponent-cannot-see-its-own-shape
description: "a log-log fit cannot tell a complexity class from a bounded ratio saturating, or from a fixed cost amortizing; read the LOCAL exponents and find the denominator's ceiling"
metadata: 
  node_type: memory
  type: project
  originSessionId: 738f91ee-ad65-4791-a2df-c8daafeefa81
  modified: 2026-09-17T00:27:04.022Z
---

A single fitted `k` with r² > 0.99 says nothing about whether a series is a complexity class.
Three different shapes all fit beautifully, and only one is a finding. `Tests/calltree/calltree_growth.py`
now reports `_trend()` — the local exponents and their direction — beside every fit.

- **saturating** — falling AND heading below the flag threshold. `AffinityStore.delta_lift_idxs`
  led every archived `skus` ladder back to August at k = 1.56–1.61, and is **linear**: it is called
  once per reclaimed bin whose SKU was that aisle's last, against `_index_add` once per reclaimed
  bin unconditionally, so the ratio cannot exceed 1.0. Measured 0.169 → 0.871 with local exponents
  falling 1.92 → 1.30. Extending the fit puts the ratio above 1.0 at ~13,000 SKUs.
- **settling** — falling but settled high. The fit overstates the early rungs; the settled value IS
  the class. A rule that called "saturating" on direction alone demoted a 9.2 M-call quadratic
  (local k 2.41, 1.64, **2.00, 2.02**) to the bottom of the ranking.
- **accelerating** — but on ARM TOTALS this is biased upward and must not gate anything. An arm
  pays ~48 s of fixed cost before its batch loop starts, so early local exponents are depressed and
  any arm reads as accelerating while it amortizes. A flatly linear arm (k = 0.90) still climbed
  0.73 → 1.02. For real seconds, use the fit; for a ratio, use the trend.

**The question that closes a candidate faster than any refactor: what is this a ratio OF, and does
that denominator have a ceiling the code guarantees?**

See [[c-keyed-scans-are-invisible-to-the-offender-table]] for the companion trap — the scans the
fit never sees at all — and [[a-count-is-not-a-claim]] for the denominator rule this specializes.
