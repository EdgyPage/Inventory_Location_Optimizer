# Config centralization

Label: wayfinder:map

## Destination

Every simulation-configuration value is **declared exactly once**, in the layer that owns its
meaning, and every other layer imports it. A value that two places need cannot drift, because
there is only one of it; a value that shapes a run is recorded in that run's spec and moves the
run-tree fingerprint when it is edited.

Concretely, the destination is reached when:

1. No domain-side literal restates a `settings.py` value. Where both exist today, the domain
   DECLARES and `settings.py` IMPORTS -- the direction `context/architecture.yml` forces, and the
   one `settings.py:423` already uses for the three crew-price scalars.
2. A test proves each unified pair resolves to ONE object, so a future divergence fails at import
   rather than in a run.
3. The `.env` loader exists once, not four times.
4. Editing a catalogue-generation parameter is visible: recorded in the profile's metadata, and
   inside `SHAPE_SOURCES` so preflight re-runs.
5. The two values with no seam at all (aisle geometry, `_OVERSTOCK_MIN_HEADROOM`) have one.

## Notes

- **This is a NO-OP effort by construction.** Every pair unified in ticket 01 holds the same value
  today; the unification is provably value-preserving NOW, and the point is that it stays one
  tomorrow. Anything that would move a number belongs in `complexity-round`, not here.
- **Direction is not negotiable.** `warehouse_core -> optimization`, `wh_* -> optimization` and
  `inbound -> optimization` are all forbidden boundaries. The domain declares; `settings.py`
  imports. `verify_architecture.py` must exit 0 before any commit that adds an import edge.
- **Runs before `complexity-round`**, to give that effort a clean baseline to digest against.
- Line endings are MIXED: `settings.py` is LF, every other target is CRLF. Multi-line exact-string
  patches must preserve the file's own ending (see `.scratch/config-centralization/assets/`).

## Decisions so far

- **[01] Five pairs unified; three "duplicates" were not duplicates; the worst offender was a
  worker fallback nobody had listed.** The domain now declares `DEFAULT_TARGET_FILL`,
  `DEFAULT_ZONING_BANDS`, `DEFAULT_DOCK_DOORS`, `DEFAULT_FEE_THRESHOLD_DAYS` and
  `DEFAULT_URGENCY_HORIZON_DAYS`, and `settings.py` imports them -- the direction
  `verify_architecture.py` confirms is legal (`optimization -> inbound` is unforbidden; the fog
  item is closed). `n_bands` turned out to have FOUR literals: the fourth,
  `strategy_runner.py:1454`'s `_zcfg.get('n_bands', 3)`, sits INSIDE the worker, where a stale
  fallback is invisible to every seam and no run records it.
  Three excluded after inspection -- `PutQueueSpec.swap_coef` (a sentinel, not a copy),
  `BatchConfig.sampler` (a recorded frozen-meaning decision) and `BatchConfig.mean_fraction`
  (equals FF's by coincidence, differs from store's). The test for each site is whether changing
  one without the other is a BUG or a CHOICE.
  Guarded by `Tests/unit/test_declared_once.py`, which asserts IDENTITY rather than equality and
  was proven to fail under both sabotages before being believed.
  -> [01-ten-restated-literals.md](issues/01-ten-restated-literals.md)

- **[02] The fingerprint was structurally blind to a file that did not exist yet.**
  `SHAPE_SOURCES` is a tuple of PATHS, and `simconfig/configs/` is auto-discovered -- a new
  pick-config's NAME becomes the `<config>` level's directory name, so adding one renames a level
  of the run tree with no fingerprint movement at all. Listing the four current files would have
  left the fifth just as invisible, so this needed a mechanism: `SHAPE_SOURCE_DIRS`, hashing the
  sorted NAME LIST before any content -- the name is the directory, so a rename must register even
  when no byte moves. `config/channels.py` was also missing outright (it decides whether the
  conditional `<channel>/` level exists). Four tests, proven to fail with `SHAPE_SOURCE_DIRS`
  forced back to `()`.
  -> [02-the-fingerprint-cannot-see-a-new-file.md](issues/02-the-fingerprint-cannot-see-a-new-file.md)

- **[03] One `.env` reader, three of four sites -- and the fourth stays because a gate says so.**
  `Optimization/config/envfile.py` (imports `os` and nothing else) now serves `sim_config` and
  both `Warehouse/generation/` entry scripts. NOT the kernel (declared "zero-dependency VALUE
  OBJECTS... of the DOMAIN"), and NOT the repo root, which is worse than it looks: `GRAPH_ROOTS`
  are all DIRECTORIES, so a bare top-level `.py` is never walked -- no `files.yml` entry, no
  layer, nothing to notice. `docs/experiments/ingest.py` keeps its copy because
  `test_ingest_env_bootstrap.py` requires the loader to RUN at module level before the argparse
  default that depends on it, and loosening a gate to fit a refactor is how gates stop working.
  That copy is fenced by BEHAVIOUR instead -- an eleven-case battery plus a whole-environment
  comparison, proven to fail when the copy is forked to drop the `r"..."` form.
  -> [03-one-env-loader-not-four.md](issues/03-one-env-loader-not-four.md)

- **[04] One of the two "values with no seam" was not a value at all.**
  `_OVERSTOCK_MIN_HEADROOM` appears ONCE in the entire repo: its own declaration. Commit
  `8a20cdc9` (2026-06-03) says in its own message that it removed the constant -- it removed the
  two USES and left the declaration behind a comment describing a loop that no longer exists.
  DELETED. Threading it would have been the worst outcome available: a flag, a run_spec field and
  a payload entry for a knob controlling nothing, recorded in every future run as if it meant
  something.
  The aisle geometry WAS real and carried a second defect the ticket did not name:
  `_AISLE_W = aisle_width_for(50)` was a module scalar evaluated at IMPORT, which is the exact
  `_INITIAL_FILL` shape `test_config_reaches_the_worker.py` calls a SHIPPED defect. Adding a flag
  without converting it would have produced a flag that silently did nothing -- the sixth
  instance. Now `AISLE_COLUMNS`/`AISLE_LEVELS` through seams 1-4 with `aisle_geometry()` read at
  CALL time; seam 5 deliberately absent (parent-side) and registered in `PARENT_ONLY`.
  -> [04-two-values-with-no-seam.md](issues/04-two-values-with-no-seam.md)

## Fog

- ~~Does `settings.py` importing `Inbound` pass the boundary checker?~~ **CLOSED by ticket 01:
  yes.** `verify_architecture.py` exits 0 with the new edges in the graph.
- ~~Where does the single `.env` loader live?~~ **CLOSED by ticket 03: `Optimization/config/`,
  and it needed no new layer at all.** `generation -> opt_config` and `docs -> opt_config` are
  both permitted; only the reverse directions are forbidden. The plan's "new top-level leaf"
  suggestion would have been structurally invisible to the architecture graph.
