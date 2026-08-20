---
name: v2-sampler-era
description: v2 Fenwick batch sampler is the default era since 2026-08-20 (commit 21f3b3c) — v2 runs are not row-comparable with the pre-flip archive
metadata:
  node_type: memory
  type: project
  originSessionId: 36cab001-d78e-4fed-b3d8-2a90d4dde5c3
  modified: 2026-08-20T18:55:11.996Z
---

As of 2026-08-20 (commit 21f3b3c), `CONFIG['global']['sampler'] = 'v2'`: all new runs draw
batches with the Fenwick sampler (`BatchConfig.sampler` in
`Warehouse/picking/Workload_Builder.py`; dataclass default stays `'v1'` — frozen historical
meaning for tests/diagnostics). v2 runs are **NOT row-comparable** with the pre-2026-08-20
archive — v1's draws depend on sequential-cumsum float grouping order, so no O(log n) sampler
can be byte-identical to it; opting into v2 starts a new results era. `--sampler v1` reproduces
the old archive byte-identically at the domain-table level; the only expected digest diff vs a
pre-flip baseline is the ADDITIVE `config.json` `'sampler'` key that old runs predate.

**NEW-ERA baselines** (all 18 workers, identical-seed twins digest IDENTICAL —
`Tests/bench/run_digest.py`):
- tiny keyframes-on: `comparison_20260820_144000` (twin `_144530`)
- 40k: `comparison_20260820_145101` (twin `_150132`)
- instrumented perf reference: `comparison_20260820_151204` (`SIM_GC_DETAIL`: total_s 24.2,
  save 5.22, reord 13.7, gc_pause 2.2, peak RSS 452 MiB)

These SUPERSEDE `comparison_20260818_225336`/`_195808` for future byte-identical gates; the old
pair stays valid only for `--sampler v1` work.

**Measured save** (`ensure_batches` cold, 100 batches, 18 workers): 40k 38.0→27.9s; 80k
94.4→31.7s — v1 is quadratic vs catalogue size (0.83→21.6 s/batch single-threaded, 40k→160k),
v2 is flat (0.05→0.48).

**Bonus visible in the new reference**: peak worker RSS 1205→452 MiB (the C2 affinity sidecar
removed the SQL-load transient that set the old peak, see [[calltree-framework-first-findings]]
Round 2 / C2); 18-worker tree ~8 GiB.

A pre-sampler-field `run_spec.json` means that run used v1 — `run_analysis._apply_run_shape`
restores `'v1'`, never the checkout's current default — pinned by
`test_apply_run_shape_pre_sampler_spec_means_v1` in `Tests/unit/test_run_shaping_params.py`.

**Why:** the sampler is wired through every run-shaping touchpoint (`--sampler {v1,v2}` CLI flag
defaulted from CONFIG, the CONFIG override block, `run_spec.json` + `--resume` reconstruction,
Channel/`make_channel`/`batch_config`, `build_shared_assets`, and `config.json` provenance) so a
result set's era is always recoverable from its own run_spec — never assume the checkout default
when reading an archived run.

**How to apply:** before treating two runs as comparable (digest gate, throughput comparison,
labour-cost comparison), check which sampler produced each — mixing v1 and v2 rows silently
invalidates the comparison with no error. Use the NEW-ERA baselines above for any future
byte-identical gate unless the work is explicitly `--sampler v1`.
