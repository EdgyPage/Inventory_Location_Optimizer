---
name: results-drive-location
description: Where completed simulation run outputs and generated catalogues actually live on disk
metadata: 
  node_type: memory
  type: reference
  originSessionId: f5db1bb2-a822-4280-9584-0d8a4c78e873
  modified: 2026-07-29T03:48:50.649Z
---

Run outputs are NOT under the repo and NOT on the C: drive — they live on an external data drive
whose location is recorded only in the untracked `.env`. Never hardcode the drive; read the keys:

- `COMPARISON_OUTPUT_DIR` → comparison run outputs, one `comparison_<ts>/` per sweep
- `PROFILE_INPUT_DIR` → generated catalogues/batches, `<batch>/<profile>/inventory/`
  (`params.json` + `plots/`)

`Optimization/config/sim_config.py` parses `.env` into the environment, so importing it is enough
to resolve both. `Tests/bench/bench_sections.py:_roots` is the worked example.

Caveat: each run's `sim_meta.json` `inv_db` field points at a **stale path on a drive that is no
longer the output drive** — don't trust it to locate the inventory; derive the catalogue path from
the batch name under `PROFILE_INPUT_DIR` instead.

**Why no literal path here:** machine-local paths are forbidden in memories and tracked files
(this store is mirrored into git). See [[no-machine-local-paths]].

The latest run when Experiment 2 was authored (2026-07-08) was `comparison_20260706_174353`
(store + fulfillment channels). See [[channel-experiment-independent-warehouses]].
