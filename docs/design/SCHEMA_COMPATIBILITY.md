# Schema compatibility — keeping a version change from becoming a manual sweep

**Status:** the guardrail described in §4 is implemented. §6 is proposed, not built.

This repo has four schema-ish layers. Three of them are good, and one asymmetry between them is
where the remaining manual work lives. This document says what each owns, where the gap is, and
the pattern that closes it.

## 1. What exists

| Layer | Answers | Identity |
|---|---|---|
| `Schema/` (`shape`, `identity`, `connect`) | *what is inside one file* | sha256 of the canonical SQL shape, 12 hex |
| `Optimization/runschema/` | *where do a run's files live* | sha256 of `schema.py`'s `LEVELS` + `ARTIFACTS` |
| `Visualization/readers/` | *how do I read this vintage* | registry: `schema_id` → reader class |
| `Visualization/cache_schema.py` | *is this derived cache stale* | source pin + own stamp + `cache_freshness()` |

Both identity schemes derive the id from a declared shape rather than letting anyone pick a
number. That is the property everything else rests on: nobody chooses a version, two branches
cannot both call themselves "v2", and any consumer can re-derive an id and check it.

## 2. The run-tree layer is already the target state

`runschema.preflight` is the model this document generalises. Edit `schema.py`, and:

```
DETECT   hash the shape-defining sources against the committed contract; unchanged -> return
CANARY   two tiny REAL runs — mixed catalog AND store-only, because one alone would encode
         "channel always present", the exact assumption behind the store-only bugs
OBSERVE  generalize produced paths back to templates, consuming levels POSITIONALLY
VALIDATE compare observed against declared; produced-but-undeclared is the finding that matters
ADOPT    store the new document, move `head`, regenerate context/artifacts.yml and
         Visualization/static/schema.json
```

Nobody picks an id, nobody hand-edits the three derived files, and an old run stays resolvable
forever because `run_layout.json` records the contract it was written with. **Filepath versioning
is solved.** The one gap is §5.

## 3. The gap: file-granularity vetting, table-granularity reads

`Family.known_ids` vets a whole file. A consumer reads a handful of tables out of a dozen. Those
are different granularities, and the difference is not academic:

```
sim_db vets four ids.  They differ by three whole tables and one column:
  23d0c7f167bc  no bin_eviction / bin_placement, HAS bin_inventory, no simulation_runs.sim_schema_id
  2b7913bcd7e6  + sim_schema_id
  ee5ebabe74fb  + bin_eviction + bin_placement, bin_inventory still present
  6ad0b34af9f1  bin_inventory retired          <- declared
```

`check()` passes all four. **Nothing adapts.** Today that is safe only because no evaluation reads
those tables — an accident, not a guarantee. The first evaluation that reads `bin_placement`
returns silently nothing for the 2026-07-29 run that Experiment 6 is published from, and passes
vetting on the way.

This is the same silent failure `Schema/` was built to stop, displaced one level up. Loaders do
`SELECT *` and guard with `row.keys()`, so a dropped column becomes `0.0` in `common/frames.py`
and a published figure is quietly wrong — and seven of the guarded columns (`sigma_fd`, `W`,
`queue_depth`, `reorder_placements`, `reload_moves`, `lead_queue_depth`, `in_transit_qty`) flow
straight into a figure or CSV. Only `task_makespan` fails safe, because `frames.py` turns its
`0.0` into `NaN`.

## 4. The guardrail (implemented)

**Declare requirements, not versions.** A consumer says what it reads, next to the code that
reads it:

```python
REQUIRES = _compat.Requires(
    family='sim_db',
    label='Performance_Evaluations analysis context',
    tables={'batch_stats': ('duration', 'total_items', 'sigma_fd', ...), ...})
```

Two checks enforce it, at two different times:

| Check | When | Cost | Catches |
|---|---|---|---|
| `compat.validate(req)` | CI, no database | none | a schema change that moves a column a consumer reads out of the guaranteed surface |
| `compat.check_requirements(path, req)` | runtime, one file | one open | a real file that cannot answer *this* consumer, named by column |

`guaranteed_surface(family)` is the intersection of every vetted shape; `conditional_surface` is
the rest. A consumer inside the guaranteed surface is version-free **by construction**. Anything
in the conditional surface may only be reached by negotiation — probe for it, and degrade with a
recorded caveat.

Current sim_db surface:

```
guaranteed  aisle_metrics batch_stats bin_scores picker_events picks
            reorder_queue simulation_runs sku_scores task_stats
conditional bin_eviction bin_inventory bin_placement  +  simulation_runs.sim_schema_id
```

### Committed shape documents

A declared shape is rebuilt from the writer's DDL, so it is always re-derivable. A **historical**
shape is not — `PRE_STAMP_SIM_SCHEMA_ID` is frozen precisely because the source that produced it
is gone. So the intersection cannot be computed from declarations alone, and the shapes are
committed under `Schema/shapes/<family>/<short>.json` — the same reasoning and the same layout as
`Optimization/schemas/run_tree/<short>.json`.

An id in `known_ids` with no committed document is **unrecoverable**: the true intersection is
unknowable, and a surface computed without it is over-optimistic. `guaranteed_surface` raises
rather than quietly returning the larger surface.

When no file of a vintage survives, a **hash-verified reconstruction** is legitimate: rebuild the
shape from an adjacent committed one plus the documented delta, and accept it only if it hashes to
the target id. `sim_db/2b7913bcd7e6` was recovered this way after a full scan of both drives found
no surviving file — inserting `sim_schema_id TEXT` at ordinal 9 of the pre-stamp `simulation_runs`
reproduces the id exactly, which a wrong shape could not do. A shape document you cannot make hash
correctly is a guess, and must not be written.

### Negotiation, where it already happens twice

Two consumers already do this by hand, independently:

- `Visualization/readers/protocol.py` — `CAP_BIN_LOG` etc., probed for **rows**, not just columns,
  because `aisle_metrics` and `reorder_queue` exist everywhere and are empty for most arms.
- `Diagnostics/replay_run.py` — `_SOURCES`, a best-first fidelity ladder carrying `exact`, `phase`
  and a caveat that is copied into the exported JSON so the number cannot be read without it.

Both are the right pattern. Neither is shared, and `context/architecture.yml` forbids
`optimization -> visualization`, so the analysis layer cannot reuse either.

## 5. Where filepath versioning is still missing

Run trees are versioned; **derived** trees are not. `docs/experiments/<exp>/images/{run}/{inv}/{cfg}/`
is built by `docs/experiments/ingest.py` and consumed by `docs/macros.py` through hand-joined
path strings. `experiment.yml` records a `schema_id` that `macros.py` never reads. A tree-shape
change therefore breaks the site at `mkdocs build --strict` with a `FileNotFoundError` — loud, but
not negotiated, and the failure names a missing file rather than a schema delta.

### Why the CLI is not in `Schema/`

`--report` and `--capture` live in `scripts/schema_report.py`. A family exists only once its
writer's module has been imported, so anything reporting on *all* families must import all of
them — which is exactly what the `schema` layer may not do.

That list sat in `Schema/compat.py` briefly, behind `importlib.import_module` on string literals.
It was wrong twice, and the second reason is the general lesson:

- **The gate could not see it.** `context/arch/extract.py` only visits `ast.Import` /
  `ast.ImportFrom`, so a string-literal dynamic import records no edge.
  `verify_architecture.py` reported OK on a real crossing of three declared boundaries — green
  because it was blind, not because the import was legal. A plain
  `from Optimization.persistence import Picking_Data` in the same function *would* have failed it.
- **It made the leaf heavy in fact.** The call pulls ~1350 modules — matplotlib, pandas, scipy —
  into the package whose README advertises "stdlib-only … so every layer may use it", and creates
  a real `Picking_Data → compat → Picking_Data` cycle that only deferred execution survives.

`Tests/architecture/test_schema_compatibility.py::test_the_schema_package_imports_nothing_above_its_own_layer`
now asserts this statically, including the dynamic forms, because the extractor structurally
cannot.

## 6. Proposed, not built

1. **Move the capability vocabulary to `Schema/capability.py`.** `Schema/` is stdlib-only,
   top-level, and importable by every layer — the only legal home. `readers/protocol.CAP_*` and
   `replay_run._SOURCES` become registrations against it; analysis gains the ability to negotiate
   at all.
2. **`Schema/preflight.py --adopt`,** mirroring the run-tree ADOPT stage: canary-write a DB from
   the family's own DDL, derive the outgoing id, capture its shape, and append to `known_ids` with
   a generated comment. Today that step is manual archaeology.
3. **Teach `docs/macros.py` the `experiment.yml` `schema_id`,** so a staged snapshot resolves
   through a contract instead of a joined string.
4. **`describe_diff` reports "no structural difference found (the ids differ for another reason)"
   when the shapes are identical** — misleading; it should say the shapes match.
5. **Teach `context/arch/extract.py` the dynamic-import forms** (`importlib.import_module`,
   `__import__`) on string literals. Until then every layer boundary in `architecture.yml` is
   enforced only against static imports, and the local test above is the only thing covering
   `Schema/`. Other layers have no equivalent.
6. **Nothing checks a `Requires` for COMPLETENESS.** `validate()` proves a declaration is *inside*
   the guaranteed surface; no test proves it names everything its loaders actually read. Both
   current declarations were found under-declared by inspection (`reorder_queue.unit_type`,
   `storage_size`, and ten `simulation_runs` columns), not by a gate. A structural sweep pairing
   each loader's SELECT and `row.keys()` guards against its declaration would close it.
7. **`_functions_selecting` only matches single-literal SQL,** so a loader that interpolates its
   table name is invisible to the conditional-reader exhaustiveness test.

## 7. The rule

> A change to a `*_DDL`, a `declared_*_shape`, or `runschema/schema.py` is not finished until the
> outgoing shape is captured, the surface is re-derived, and every `Requires` still validates.

`.claude/agents/schema-maintainer.md` owns that procedure; `code-reviewer` rejects a DDL change
that ships without it. Neither is a substitute for
`Tests/architecture/test_schema_compatibility.py`, which is what actually fails.
