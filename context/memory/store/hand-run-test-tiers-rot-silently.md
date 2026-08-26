---
name: hand-run-test-tiers-rot-silently
description: "Tests/calltree and Tests/bench are in no CI path, so their oracles die unnoticed — check them before trusting a measurement from either"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-26T02:37:54.300Z
---

`Tests/calltree/` and `Tests/bench/` are **hand-run tiers**. Nothing in the routine suite
(`Tests/unit`, `Tests/integration`, `Tests/architecture`) collects them, so a break there is
reported by nobody.

What that cost, both found 2026-08-25 while building the stress plan:

* **three dead frozen-oracle tests** in `Tests/calltree` — `_WaveAsPool` had lost `prefers_low`,
  confirmed pre-existing by stashing. They had been failing silently for an unknown span.
* the **split put-away configuration had never executed outside a unit test**, so its
  pathologies (the `_admit_held` quadratic) survived every release.

Also: `Tests/bench/coverage_e2e.py` hands every worker a `queue.Queue()` it never drains and
prints DONE regardless — see [[coverage-e2e-swallows-worker-logs]]. A green run through it is
not evidence.

**Why:** these tiers exist to measure, not to gate, so no one added them to a gate. The failure
mode is that a measurement taken through a rotted harness looks perfectly healthy.

**How to apply:** before trusting any number out of either directory, run
`python -m pytest Tests/calltree -q` first and read the result. When touching hot-path names,
expect `SECTION_MAP` in `calltree_scenarios.py` to need the rename too.
