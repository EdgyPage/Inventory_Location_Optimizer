---
name: figure-views-are-derived
description: since 2026-08-23 an evaluation declares shape+quantities and its views are derived; delta_travel_vs_baseline.png was a percent view and is now percent_travel_per_arm.png
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-24T00:01:42.484Z
---

A figure evaluation no longer declares `views=`. It declares `family`, `shape` (the mark)
and `quantities`, and its view set is derived by `core.quantities.derive_views` from what
the quantity IS and what the mark can structurally show. `FAMILIES` carries only scope and
charter — the per-family view allow-list is gone.

**Why:** the allow-list did not merely permit drift, it manufactured mislabelling. `layout`
allowed no `percent`, so a percent-improvement chart shipped as
**`delta_travel_vs_baseline.png`** under `view='delta'` — it computed
`improvement_pct_series` and labelled its axis with `pct_axis`. Five save-time checks
passed it, because each compared one declaration against another and none ever saw a
number.

**How to apply:**

- Anything citing `delta_travel_vs_baseline.png` predates 2026-08-23; the figure is
  `percent_travel_per_arm.png`.
- To find whether a view exists, ask the registry —
  `EVAL_BY_KEY['<key>'].views` — rather than reading the module. If the view is in that
  tuple the PNG is already on the run drive; if it is missing, check `views_pending`
  (should exist, not written) vs `views_suppressed` (must never be drawn, reason recorded).
- Adding a metric is one `Quantity` entry in `core/quantities.py`; the CSV block, the
  vs_baseline rows, the effect panel and the views follow. Use the
  `route-reviewer-finding` skill to pick the route before writing any renderer.
- A quantity reading a sim-DB column outside `compat.guaranteed_surface('sim_db')` must
  name a `SIM_CAPABILITIES` key or CI fails (`Tests/architecture/test_data_era_gate.py`).
  Related: [[ingest-must-prefer-head-contract]], [[a-grant-is-not-an-output]].
