---
name: per-item-charge-hard-break
description: "fc7a46a5 (2026-09-05) is the per-item-charge HARD BREAK — no pick, put, labor or W* number from before it is comparable with one after; the put crew was also priced at a literal 1.0 (never from the run's pick config) before it"
metadata: 
  node_type: memory
  type: project
  originSessionId: 4f17d2b3-6b5c-4a7e-80c4-be42f33f2749
  modified: 2026-09-05T22:31:34.887Z
---

Commit `fc7a46a5` (2026-09-05, ADR-0001, department-calibration ticket 06) changed the
at-location model to `M(y) · (intercept + qty · per_item + qty · var)` with `per_item = 0.5 s`
defaulted NON-ZERO in `PickConfig`. It is a deliberate hard break, a second and harder one
than [[placement-pools-and-the-audit-point]]'s `a033aff`: every pick time, put time, unload
time, `labor_cost`, W* floor, gain-evaluator price and placement decision from before it
reads LOW against anything after, and no flag reproduces the old model.

Two things about the archive that are easy to get wrong:

- **Put-away before `fc7a46a5` was priced at a literal intercept of 1.0 while the store arms
  pick at 15.** `Inventory_Management` defaulted to `PutawayCost()`; nothing ever built it
  from the run's pick config. Any pre-break put-away or receiving seconds are on a different
  price than the picks beside them. After the break the put crew is picking's × 0.5 (and
  receiving is put-away's × 1.0 with the per-item charge once per PACK).
- **A pre-break leaf `config.json` has no `pick_per_item` key, and that absence is
  meaningful.** `run_map_precompute` and `docs/macros.pick_time_formula` reconstruct such a
  leaf WITHOUT the term on purpose; do not "fix" them to use the dataclass default.

**Why:** the calibrated era ([[working-day-clock-plan-corrections]]'s successor map) derives
crews from this model, and preserving the old model's legitimacy would have meant a dead
0.0 default plus two labour regimes to reconcile forever.

**How to apply:** when a number from an archive predating 2026-09-05 disagrees with a fresh
run, check the era before suspecting a bug; never compare across the break; and know that
the put-away side of a pre-break archive is doubly incomparable (different model AND a
different intercept).
