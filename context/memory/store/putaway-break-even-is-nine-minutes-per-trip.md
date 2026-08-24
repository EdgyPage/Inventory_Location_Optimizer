---
name: putaway-break-even-is-nine-minutes-per-trip
description: "The placement gain's unmodeled put-away exposure is ~14 s per unit / ~9 min per 39-unit restock trip — roomy, not thin; the old 0.5 s figure was the 1000x unit bug"
metadata: 
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-24T20:51:11.011Z
---

The store placement win (~2.6 % of pick-hours) is measured on **pick time only** — restock walk
time is unmodeled for every rule including FIFO. The break-even, recomputed from the run's own
artifacts after the seconds-vs-milliseconds fix:

- one store wave = **1,677.8 h** of picking (`mean_batch_prod_hours`, `per_run_summary.csv`,
  `total_production_time` 452,998,965 s over 75 waves ÷ 3600)
- 2.6 % of that = **43.6 h** = 157,042 s saved per wave
- **11,225 units** put away per wave (`mean_reorder_placements`, same table)
- → **~14 s per unit**, or **~9.1 minutes per 39-unit restock trip**

**Why it matters — and this replaces the opposite conclusion.** This memory previously read
**0.014 s per unit / ~0.5 s per trip** and concluded the margin was so thin that half a second of
extra restock walk would erase the whole pick-hour saving. That was the 1000× divisor bug:
`mean_batch_prod_hours` was `seconds / 3.6e6` when the sim emits seconds, so the wave's pick-hours
read 1.678 instead of 1,677.8. Corrected, the exposure is three orders of magnitude roomier — a
restocker would have to walk **nine extra minutes per trip** to eat the gain, which is not a
plausible placement side-effect. The lever is far more robust than the old note claimed.

**How to apply:** quote **~14 s/unit** and **~9 min/trip**, and still say it must be *measured*
rather than argued — a roomy margin is not a measured one. Any pilot for the placement lever
should still track restock-crew hours as a first-class metric. **Do not quote the old 0.5 s
figure**, and treat any page or note still carrying it as pre-fix: the published experiment-8
pages were written against the old divisor and have not been re-analysed. Related:
[[sim-time-unit-is-seconds-not-ms]], [[stakeholder-site-pilot-frame]],
[[fifo-restock-ignores-initial-placement]].
