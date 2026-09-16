# One .env loader, not four

Type: task
Status: ready-for-agent
Blocked by: none

`_load_env` + `_clean_path` are copy-pasted at four sites:

| site | note |
|---|---|
| `Optimization/config/sim_config.py:39-58, 85-94` | the one `run_simulation` imports |
| `Warehouse/generation/generate_mixed_profile.py:55-74, 88-94` | |
| `Warehouse/generation/generate_profile_suite.py:75-94, 100-107` | |
| `docs/experiments/ingest.py:51-81` | guarded by `Tests/architecture/test_ingest_env_bootstrap.py` |

**Confirmed semantically identical** (read side by side): same skip rules for blanks, comments and
lines without `=`, same `r"..."` raw-string stripping, same shell-wins precedence. Only whitespace
and docstrings differ. So this is a genuine duplicate, not four things that look alike.

## Where it can live -- the part that needs deciding

The shared module must be importable by `Warehouse/generation/` AND `Optimization/config/` AND
`docs/experiments/`. `Warehouse/generation/` imports nothing from `Optimization/` today, and
`warehouse_core -> optimization` is a forbidden boundary, so it cannot simply import
`sim_config`'s copy. Candidates:

1. **A new dependency-free top-level leaf** (the `Warehouse/physical.py` pattern), plus one
   `layers` entry in `context/architecture.yml`. Cleanest dependency-wise; costs an architecture
   edit and a `files.yml` entry.
2. **`Warehouse/kernel/`** -- already zero-dependency and imported by everything. But the kernel is
   the DOMAIN's cost/regime/timeline vocabulary; an `.env` reader is harness plumbing and would be
   the first non-domain thing in it. Check `Warehouse/kernel/README.md` before choosing this.

## The constraint that must not be broken

`docs/experiments/ingest.py` loads `.env` **before importing anything that reads it** -- that
ordering is what `Tests/architecture/test_ingest_env_bootstrap.py` pins. A shared module is only
safe if importing it does not itself pull in anything that reads the environment at import time.
A dependency-free leaf satisfies that; a module that transitively imports `sim_config` does not.

## Explicitly NOT in scope

The **36 `_REPO_ROOT` bootstraps**. Most are entry-script `sys.path` bootstraps, which CLAUDE.md
section 2 expressly permits, and a shared helper cannot be imported before the path that makes it
importable exists. The honest improvement there is propagating the **depth `assert`**
`sim_config.py:29` already carries to the ones that load `.env` -- the trap CLAUDE.md section 3
names, where a wrong `..` count silently stops `.env` loading and a run writes hundreds of GB into
the repo.

## Why it is not done yet

Deprioritised against `complexity-round`, which is the larger half of the request. Recorded here
with the analysis complete so it can be picked up cold.
