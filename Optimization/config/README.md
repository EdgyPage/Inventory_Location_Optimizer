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
| `whatif_config.py` | `SPECS` — the cell matrices a run can be launched as |

**Does NOT belong here:** anything that reads or writes a run's output (→ `persistence/`,
`runschema/`), orchestration (→ `simdriver/`), or plotting (→ `Performance_Evaluations/`).

## Two things that will bite you

**`sim_config` and `channels` import each other.** They must stay co-located; splitting them
requires breaking the cycle first.

**`sim_config._REPO_ROOT` is depth-sensitive and fails SILENTLY.** It is used for `.env` loading and
the default profiles dir — not for imports — so a wrong `..` count does not raise. It makes `.env`
stop loading, which makes `COMPARISON_OUTPUT_DIR` fall back to the source tree, which puts a
150–200 GB run output inside the repo. There is an `assert` guarding it; do not remove it.
