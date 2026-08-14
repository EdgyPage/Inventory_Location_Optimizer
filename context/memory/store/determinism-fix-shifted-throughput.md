---
name: determinism-fix-shifted-throughput
description: "Commit 753d01e raised absolute throughput ~1.3-1.4% while leaving labor unchanged, so figures published before it read low"
metadata: 
  node_type: memory
  type: project
  originSessionId: f8316019-3a49-404c-ad0d-e5f670ed885b
  modified: 2026-08-14T09:18:34.472Z
---

Commit `753d01e` (2026-08-13) made bin selection deterministic — `Warehouse/picking/Workload_Builder.py`
now sorts by `operator.attrgetter('location')` where it previously iterated a `set[Aisle.Bin]`, whose
order varied per process under spawn + ASLR.

Measured effect: **absolute `total_items` / throughput rises ~1.3–1.4%.** Labor cost and makespan are
unaffected beyond a ±2.8% sampling band they should never have had. Throughput *percentages* are
second-order — the shift largely cancels in a ratio.

Everything under `docs/experiments/` predates this commit. Experiment 6's absolute throughput figures
read ~1.4% low; its deltas are largely fine.

**Why:** nothing in the repo tied a published figure to the code that produced it. The one
`schema_id` under `docs/` identifies the DB *table contract*, and `753d01e` touches no table — so it
is byte-identical before and after and cannot distinguish the two runs. The change could therefore
invalidate figures with no test failure, no build failure and no reader-visible signal.

**How to apply:** the profile does **not** need regenerating — `753d01e` touches only the picking
workload builder. Only a re-simulation (~500 GB) would refresh the figures; annotation was the agreed
interim. Do not silently edit a published number to "correct" it: it is what that run produced.
Related: [[results-drive-location]].
