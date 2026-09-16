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

## Fog

- ~~Does `settings.py` importing `Inbound` pass the boundary checker?~~ **CLOSED by ticket 01:
  yes.** `verify_architecture.py` exits 0 with the new edges in the graph.
- Where does the single `.env` loader live? It must be importable by `Warehouse/generation/`
  (which imports nothing from `Optimization/`) AND by `Optimization/config/`. Likely a new
  dependency-free leaf plus one `layers` entry in `context/architecture.yml`.
