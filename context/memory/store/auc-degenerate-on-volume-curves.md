---
name: auc-degenerate-on-volume-curves
description: "Raw AUC of a cumulative pick-volume curve carries no information beyond its endpoints — don't ship it as a headline metric"
metadata: 
  node_type: memory
  type: project
  originSessionId: c5c8daf5-483d-42e5-ab6b-5827d8d48e8f
  modified: 2026-07-30T21:04:32.587Z
---

The cumulative-items-vs-elapsed-hours curve is very nearly a straight line from the origin. Measured
on the 272 arm-rows of the 2026-07-29 scheduler sweep, the **shape index** (trapezoid area ÷ the
triangle its own endpoints define) sits between **0.9867 and 1.0254**. A perfectly straight line
scores exactly 1.000, and its area is then fixed by its endpoints alone.

So a raw "AUC" headline is a dressed-up restatement of `items × hours ÷ 2`.

**Why:** this was measured, not assumed — the first probe on a real store arm returned 48,951,439
item-hours against a straight-line 48,915,513, a shape index of 1.0007. Reporting that as though it
captured curve *shape* would have been exactly the kind of unsupported claim the analysis is meant
to avoid.

**How to apply:** `Optimization/Performance_Evaluations/comparison/volume_curve.py:curve_metrics`
reports three numbers and only the first two are performance scores — `mean_thr_items_hr` (chord
slope, higher better) and `auc_gain_vs_ref_pct` (area *between* two curves, which is non-degenerate
because two straight lines of different slope diverge steadily). `shape_index` is a **stability
diagnostic**: its staying near 1.000 is the evidence that the pick rate did not sag or ramp, and it
is the reason not to claim a warm-up effect. Don't promote it to a score, and don't add a bare AUC
metric back. Related: [[batch-95-flat-spot-is-shared-demand]].
