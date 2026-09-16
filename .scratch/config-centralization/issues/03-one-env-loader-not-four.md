# One .env loader, not four

Type: task
Status: resolved

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



## Answer -- three of four, and the fourth is deliberate

`Optimization/config/envfile.py` holds `load_env` and `clean_path`. `sim_config` and both
`Warehouse/generation/` entry scripts import it; the underscore aliases are kept in `sim_config`
because `run_simulation` imports them from there by those names.

### Where it went, and the two places it could not go

* **Not `Warehouse/kernel/`.** Its README declares it "zero-dependency VALUE OBJECTS -- the
  primitives everything else in the DOMAIN is built from". An `.env` reader is harness plumbing
  and would be the first non-domain thing in it.
* **Not the repo root**, which was the plan's first suggestion and is worse than it looks:
  `context/arch/extract.py`'s `GRAPH_ROOTS` are all DIRECTORIES, so a bare top-level `.py` is
  never walked -- no `files.yml` entry, no layer, nothing to notice. That is the same
  structurally-invisible shape this effort has been closing elsewhere, and it would have been
  self-inflicted.
* **`Optimization/config/` works and needs no new layer.** Reading `.env` IS configuration, and
  `generation -> opt_config` and `docs -> opt_config` are both permitted -- only the reverse
  directions are forbidden. Checked against `architecture.yml` before writing a line.

### The fourth copy stays, because a gate says so and the gate is right

`docs/experiments/ingest.py` keeps its own `_load_env`.
`Tests/architecture/test_ingest_env_bootstrap.py` asserts the file CARRIES it, and that is not
pedantry: `--profiles-root`'s default is evaluated when the parser is BUILT, so `.env` must
already be in `os.environ` by then. When it was not, catalogue resolution fell back to a recorded
ABSOLUTE path -- "precisely the route `pair_bindings` exists to replace, and the one that does not
survive a moved drive" -- and it was silent, because that fallback is legitimate for pre-v2 runs.

**I could have rewritten the gate to accept an import. I did not**, because the assertion is a
proxy for an ordering invariant that a late-resolving import could genuinely break, and loosening
a gate to fit a refactor is how gates stop working.

So the duplication is now deliberate, confined to ONE file, and fenced by BEHAVIOUR instead:
`test_ingests_own_loader_unwraps_values_exactly_like_the_shared_one` runs both loaders over an
eleven-case battery (plain, quoted, single-quoted, `r"..."`, empty, unbalanced) and
`..._produce_the_same_environment` compares the whole resulting environment, skip rules included.
Compared through the LOADER, not a helper, because ingest inlines the unwrapping and exposes no
`_clean_path` -- and behaviour is what has to agree anyway.

**Proven non-vacuous** by forking ingest's loader to drop the `r"..."` form: both tests fail, the
first naming the exact input (`'r"raw"': 'r"raw' vs 'raw'`).

### One more fence

`test_envfile_imports_nothing_that_reads_the_environment` parses the module and asserts its
imports are `{os}`. Anything that reads the environment at import time, anywhere in envfile's
import graph, would recreate the ingest ordering hazard somewhere else and just as silently.
