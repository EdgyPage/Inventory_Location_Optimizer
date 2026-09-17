---
name: toy-run-noise-floor-is-three-percent
description: "the toy run has THREE resolutions — wall cv 3.1%, per-arm save_s cv 2.65%, per-arm total_s cv 1.25% — so A/B against runtime_metrics per-arm, never the wall"
metadata: 
  node_type: memory
  type: project
  originSessionId: a4c74e51-996b-422d-b1f0-8a05cf2f5ade
  modified: 2026-09-17T21:38:07.506Z
---

Measured across **twelve** byte-identical `smoketest.py --profile tiny --stages simulate
--workers 12` runs on one day (136 arms each, same HEAD, same host):

| instrument | mean | sd | cv | a win must clear (2 sd) |
|---|---|---|---|---|
| end-to-end **wall** | 174.5 s | 5.4 s | **3.1%** | ~6% |
| **`save_s` per arm** (`runtime_metrics`) | 1.2747 s | 0.0338 s | **2.65%** | ~5.3% |
| **`total_s` per arm** (`runtime_metrics`) | 3.833 s | 0.048 s | **1.25%** | ~2.5% |

**A/B against the per-arm rows, never the wall.** The per-arm sections exclude pool scheduling
and interpreter startup, so they are two to three times tighter — and they are per-SECTION, so a
change to one part of the run is not diluted by the rest. `rm.load_rows(run_root)` is the reader.

**THE WALL IS DESTROYED BY ANY CONCURRENT WORK, and the per-arm rows survive it.** Demonstrated
2026-09-17: a toy run that finished in 344.9 s against a 174.5 s baseline — a 2x wall — had
`save_s`/arm of 1.2951 against a 1.2747 twelve-run mean, i.e. **1.016x, z=+0.60 sd**. The whole
2x was other Python work running beside it. Had the wall been the instrument, a free change
would have been reported as catastrophic.

`save_s` holds a **32.8-34.3%** share of `total_s` across all twelve runs, which is stable enough
to use as a check that a run is comparable at all.

**Why:** the toy run is the instrument that can actually FAIL a refactor
([[toy-run-is-the-byte-identity-instrument]]), so it is tempting to read its wall as a
performance verdict too. The digest half is exact; the timing half has a resolution, and the
wall's is both the coarsest available and the one most easily wrecked by a quiet-host violation.

**How to apply:** quote `save_s`/arm or `total_s`/arm from `runtime_metrics`, with the 12-run sd
above as the bar. If you must quote a wall, prove the host was quiet first. Related:
[[runtime-metrics-is-the-deep-instrument]], [[deep-tier-save-knee-and-fixed-arm-cost]],
[[per-batch-series-are-autocorrelated]], [[the-instrument-is-what-is-wrong]].
