# config — everything a run can be tuned by

`CONFIG` in `sim_config.py` is the single source of truth for every knob. The rest of the package
feeds it: `channels` describes the two independent warehouse sections, `strategies` is the
assignment-function registry, `whatif_config` holds the cell-matrix specs, and `simconfig/` is the
self-registering pick-config registry.

| Module | Owns |
|---|---|
| `sim_config.py` | `CONFIG`, the per-channel sweeps, `.env`-backed paths, config→PickConfig |
| `channels.py` | the Channel abstraction — one operation over a shared warehouse |
| `strategies.py` | `STRATEGIES` — initial × restock × reslot, the arms a run compares |
| `whatif_config.py` | `SPECS` — the cell matrices a run can be launched as, and `ERA_RUN_DEFAULTS`, the calibrated era the campaign specs default to |
| `../simconfig/staffing.py` | the calibrated era's staffing DERIVATION — a pure module: pickers + the calibration record + the script in, batch content and the two site crews out |
| `../simconfig/calibration.py` + `calibration_record.json` | the committed calibration record (seconds per unit with provenance) and its loader; the pass-0 record is the SEED |
| `../simconfig/equilibrium.py` | the pre-registered EQUILIBRIUM CHECK — one pure function of (db, day_lo, day_hi) with four strict clauses; the reference run's window precondition and the throughput audit's report |
| `../simconfig/reference.py` | the reference run's driver: measure a window as ratios of sums, discard a failing one (re-seed, never average), write the candidate record, iterate to the fixed point. Launched by `Optimization/run_reference.py` |

**Does NOT belong here:** anything that reads or writes a run's output (→ `persistence/`,
`runschema/`), orchestration (→ `simdriver/`), or plotting (→ `Performance_Evaluations/`).
The derivation's harness seam — running it after batch precompute and feeding the derived
crews into the worker payload — is `simdriver/workunits.py`, not this package; `staffing.py`
must stay pure (no CONFIG, no files) so its arithmetic is testable without a run.

## Two things that will bite you

**`sim_config` and `channels` import each other.** They must stay co-located; splitting them
requires breaking the cycle first.

**`sim_config._REPO_ROOT` is depth-sensitive and fails SILENTLY.** It is used for `.env` loading and
the default profiles dir — not for imports — so a wrong `..` count does not raise. It makes `.env`
stop loading, which makes `COMPARISON_OUTPUT_DIR` fall back to the source tree, which puts a
several hundred GB of run output inside the repo. There is an `assert` guarding it; do not remove it.
