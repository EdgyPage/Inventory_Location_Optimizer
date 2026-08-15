---
name: results-drive-location
description: Where completed simulation run outputs and generated catalogues actually live on disk
metadata: 
  node_type: memory
  type: reference
  originSessionId: f5db1bb2-a822-4280-9584-0d8a4c78e873
  modified: 2026-08-15T13:29:10.159Z
---

Run outputs are NOT under the repo and NOT on the C: drive — they live on an external data drive
whose location is recorded only in the untracked `.env`. Never hardcode the drive; read the keys:

- `COMPARISON_OUTPUT_DIR` — new comparison run outputs land here, one `comparison_<ts>/` per sweep
- `PROFILE_INPUT_DIR` — generated catalogues/batches, `<batch>/<profile>/inventory/`
  (`params.json` + `plots/`)
- `COLD_DRIVE` — as of 2026-08-15, the **archive**: the user moved the accumulated run history off
  the hot output drive here (same relative layout under `.../Optimization_Outputs/...`). New runs
  presumably keep landing on `COMPARISON_OUTPUT_DIR` until the next archive sweep.

`Optimization/config/sim_config.py` parses `.env` into the environment, so importing it is enough
to resolve all three. `Tests/bench/bench_sections.py:_roots` is the worked example (written before
the archive split — check which key(s) it reads before trusting it to find historical runs).

**As of 2026-08-15, `COMPARISON_OUTPUT_DIR` has no `sim_*.db`** — every `comparison_*` tree there
is either gone or stripped to stubs. The full run trees, including `comparison_whatif_20260729_125755`
(store-config subtree) and the 2026-08-13 runs, now live under `COLD_DRIVE`. A scan of
`COMPARISON_OUTPUT_DIR` finding nothing is expected, not a bug — check `COLD_DRIVE` before
concluding a run is lost. [[wal-sidecars-come-from-readers]] still applies to `COLD_DRIVE` reads:
a read-only open (even `mode=ro`) strands `-wal`/`-shm` sidecars that open can't remove, so treat
cold-drive reads as archival access (open with care, don't casually browse).

Caveat: each run's `sim_meta.json` `inv_db` field points at a **stale path on a drive that is no
longer the output drive** — don't trust it to locate the inventory; derive the catalogue path from
the batch name under `PROFILE_INPUT_DIR` instead.

**Why no literal path here:** machine-local paths are forbidden in memories and tracked files
(this store is mirrored into git). See [[no-machine-local-paths]].

The latest run when Experiment 2 was authored (2026-07-08) was `comparison_20260706_174353`
(store + fulfillment channels) — that run, too, is now archived under `COLD_DRIVE` rather than
`COMPARISON_OUTPUT_DIR`. See [[channel-experiment-independent-warehouses]].
