---
name: auc-degenerate-on-volume-curves
description: "Raw AUC of a cumulative pick-volume curve carries no information beyond its endpoints — don't ship it as a headline metric"
metadata: 
  node_type: memory
  type: project
  originSessionId: c5c8daf5-483d-42e5-ab6b-5827d8d48e8f
  modified: 2026-08-23T09:21:20.102Z
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

**How to apply:** As of 2026-08-23 the module this was measured against
(`Optimization/Performance_Evaluations/compare/volume_curve.py:curve_metrics`) no longer exists —
the analysis-suite rebuild (commits 0389da8, e73158b, 429a9ee) deleted the whole `compare/`
package. The successor is
`Optimization/Performance_Evaluations/throughput/volume.py`, and it already embodies this finding:
its module docstring cites the same measured shape index (~1.0007) as the reason its PRIMARY view
is `lead_pct_curve` (percent lead over FIFO at matched elapsed hours — the non-degenerate,
area-between-curves idea) rather than a bare AUC number, with the raw cumulative curves
(`cumulative_curve`) kept only as an "honest units" companion. There is no `shape_index` output
left to guard against re-promoting — the rebuild resolved the underlying finding structurally
instead of via a diagnostic field. Don't add a bare AUC metric back to this module.
Related: [[batch-95-flat-spot-is-shared-demand]].
