---
name: putaway-break-even-is-half-a-second-per-trip
description: "The placement gain's unmodeled put-away exposure computes to ~0.014 s per unit / ~0.5 s per restock trip — thin, and the number to quote when asked"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-23T15:21:46.282Z
---

The store placement win (~2.6 % of pick-hours) is measured on **pick time only** — restock walk
time is unmodeled for every rule including FIFO. The break-even, computed from the run's own
artifacts rather than asserted:

- one store wave = **1.678 h** of picking (`mean_batch_prod_hours`, `per_run_summary.csv`)
- 2.6 % of that = **~2.7 minutes** saved per wave
- **11,225 units** put away per wave (`mean_reorder_placements`, same table)
- → **0.014 s per unit**, or **~0.5 s per 39-unit restock trip** (the model restocks ≈ 39 units
  at a time per product)

**Why it matters:** that is the entire margin. If the winning rule sends a restocker half a second
farther per trip on average, the pick-hour saving is gone. Two structural reasons to expect it
does not (the rules score slots on the same travel measure picks pay, so they bias toward near/low
bins that FIFO's first-free-slot does not; and each placement amortizes over many later picks) —
but neither is evidence.

**How to apply:** when anyone asks "does put-away eat the gain?", give the 0.014 s/unit figure and
say it must be *measured*, not argued. Any pilot design for the placement lever must track
restock-crew hours as a first-class metric with its own rollback trigger; the published
experiment-8 pages state it that way. Both `mean_reorder_placements` and
`mean_batch_prod_hours` exist in the per-run summary table specifically so this stays checkable
without re-deriving it.

Related: [[stakeholder-site-pilot-frame]], [[fifo-restock-ignores-initial-placement]].
