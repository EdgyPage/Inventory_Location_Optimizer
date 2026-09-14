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

**2026-09-14, `inbound-performance` effort — `run_fullfid` was dead on three separate axes,**
each silent: it hardcoded `max_bins=20000`, which no value would have fixed (the coverage fixed
point's seed round sizes ~11x the plan it converges on — 898,700 bins before settling at 77,500);
it skipped `_derive_staffing_for_pair` (stage B), so it never reached the era at all even though
stage A (coverage) ran and made it *look* reachable; and it always takes `_channel_runs[0]`,
which is always the STORE leaf — binding 14-17 of 75 drains against fulfillment's 46-60, so any
number from it is store-biased unless both leaves are checked. All three fixed
(`Tests/calltree/calltree_capture.py`), see `docs/design/INBOUND_PERF_FINDINGS.md` §0.
`Tests/calltree/test_calltree_anchors.py` gained a relationship class of gate, not just an
existence class — see [[symbol-table-relationship-not-verified-by-symbols]] for why the existing
anchor test couldn't have caught the `yard_plans`/`dock_plans` break found the same session.
