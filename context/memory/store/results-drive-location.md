---
name: results-drive-location
description: Where completed simulation run outputs and generated catalogues actually live on disk
metadata: 
  node_type: memory
  type: reference
  originSessionId: f5db1bb2-a822-4280-9584-0d8a4c78e873
  modified: 2026-09-13T04:43:05.977Z
---

Run outputs are NOT under the repo and NOT on the system drive — they live on an external data
drive whose location is recorded only in the untracked `.env`. Never hardcode the drive; read the
keys:

- `COMPARISON_OUTPUT_DIR` — new comparison run outputs land here, one `comparison_<ts>/` per sweep
- `PROFILE_INPUT_DIR` — generated catalogues/batches, `<batch>/<profile>/inventory/`
  (`params.json` + `plots/`)
- `COLD_DRIVE` — introduced 2026-08-15 as the **archive**, when the user moved the accumulated run
  history off the hot output drive (same relative layout). **See the dated correction below
  before relying on this.**

`Optimization/config/sim_config.py` parses `.env` into the environment, so importing it is enough
to resolve all three. `Tests/bench/bench_sections.py:_roots` is the worked example (written before
the archive split — check which key(s) it reads before trusting it to find historical runs).

**DRIVE LETTERS MOVE, AND A RUN'S OWN RECORD DOES NOT FOLLOW THEM.** Measured 2026-09-12: the
September gate run's `run_spec.json` records its `argv`, `profiles_dir` and `pairs` on a drive
letter that no longer holds any of it, while `.env` now names a different letter for the same
data. So **resolve every path through the `.env` keys and verify it exists** — never a letter
remembered from a transcript, and never a path recorded inside an older run.

**Correction, 2026-09-12 — the 2026-08-15 archive claim has inverted.** That snapshot said
`COMPARISON_OUTPUT_DIR` held no `sim_*.db` and the trees lived on `COLD_DRIVE`. Today it is the
other way round: `COMPARISON_OUTPUT_DIR` holds 26 roots carrying 1,408 `sim_*.db` (the whole
calibrated-era and inbound sequence, including the September gate run), and `COLD_DRIVE` resolves
to an **existing but EMPTY** directory. Following the old wording sends you to an empty drive to
conclude a run is lost. Check `COMPARISON_OUTPUT_DIR` first; treat `COLD_DRIVE` as "may or may
not be pointed at the archive right now" and look before concluding anything.

**THE ONE-PAIR REFERENCE CATALOGUE IS A VIEW MADE OF DIRECTORY JUNCTIONS, AND A DRIVE-LETTER
CHANGE BREAKS IT WITH A MISLEADING ERROR.** The campaign's declared input (the
`catalogue_reference_lt0` view beside `PROFILE_INPUT_DIR`) is not a copy: it is a directory
holding one junction per selected pair, pointing into the real catalogue — that is how the funnel
gets ONE inventory pair out of a profile that carries two views. When the letters moved, the
junction kept pointing at the old one and `run_simulation` exits with
`No inventory+affinity DB pairs found in: <the view>` — which names the VIEW, not the dead
junction inside it, so it reads as "the catalogue is missing" rather than "one link is stale".
Cost ~15 min on 2026-09-12. Diagnose with `os.path.realpath` on the view's pair directory: if it
resolves onto a letter that is not the one `PROFILE_INPUT_DIR` names, the junction is the fault.
Rebuilding the view (`mklink /J <view>/<profile>/<pair>` at the live target, no admin rights
needed) is a minute's work; **phase 1 of the inbound funnel launches through this view, so it
will hit this.**

[[wal-sidecars-come-from-readers]] applies to archival reads wherever they live: a read-only open
(even `mode=ro`) strands `-wal`/`-shm` sidecars that open can't remove, so treat them as archival
access (open with care, don't casually browse).

Caveat: each run's `sim_meta.json` `inv_db` field points at a **stale path**, for the same reason
the `run_spec.json` paths are stale — don't trust it to locate the inventory; derive the catalogue
path from the batch name under `PROFILE_INPUT_DIR` instead.

**Why no literal path here:** machine-local paths are forbidden in memories and tracked files
(this store is mirrored into git). See [[no-machine-local-paths]].

The latest run when Experiment 2 was authored (2026-07-08) was `comparison_20260706_174353`
(store + fulfillment channels). See [[channel-experiment-independent-warehouses]].
