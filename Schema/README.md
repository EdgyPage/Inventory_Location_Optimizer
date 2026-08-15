# Schema — what shape is this database, and is it the one we expect?

These things live here, and nothing else:

| Module | Owns |
|---|---|
| `shape.py` | the canonical SQL shape of a SQLite file, its 12-hex id, and the diff between two shapes |
| `identity.py` | the registry of DB families, and the stamped → pinned → derived resolution |
| `connect.py` | the three sanctioned ways to open a SQLite file |
| `compat.py` | what a CONSUMER may read given every shape its family still vets, and the write-time gate (`stamp_checked` / `verify_family_store`) |
| `capability.py` | negotiating for data only SOME vetted shapes carry — probe for rows, degrade with a recorded caveat |
| `dataset.py` | `bind()` a file to its OWN version + the named-query registry with LOGICAL output columns (+ `sql_for`, the statement without the file) |
| `pathtpl.py` | the contract path-template vocabulary (`{name}`, `{name?}`, globs) — render + invert, shared by every tree contract |
| `provenance.py` | `repo_provenance()` — the git commit/dirty stamp every descriptor and run spec carries (never raises) |
| `profile_tree.py` | the PROFILES-tree contract: what a generated catalogue contains, its content-addressed id, and the `profile_layout.json` descriptor writers stamp |
| `profile_resolver.py` | `ProfileTree` — descriptor-first navigation of a profiles root (`pairs`, `latest`, `binding_of`), legacy walk byte-for-byte otherwise |
| `store_index.py` | `shapes/INDEX.json` — the store's mutable head + the DDL source fingerprint the Stop hook compares |
| `shapes/` | committed shape documents, one JSON per vetted id — declared ids included, since `--sync` |
| `schemas/profile_tree/` | the profiles-tree contract's own committed store (documents + INDEX), second of the two stores here |
| `hook_check.py` | the advisory Stop hook, watching BOTH stores (fingerprint + document stats; never imports writers; always exit 0) |

`identity.py` asks whether a FILE is one we know; `compat.py` asks whether it can answer a
particular caller's questions; `dataset.py` answers them by NAME, whatever the file's vintage.
Those are different granularities, and the gaps between them are where a dropped or renamed
column used to go unnoticed — see `docs/design/SCHEMA_COMPATIBILITY.md`.

**Not to be confused with `Optimization/runschema/`.** That package answers *where do a run's
files live* — it content-addresses the directory **tree**. This one answers *what is inside one
file* — plus, since `profile_tree.py`, *where do a CATALOGUE's files live*: the profiles tree is
declared here rather than in runschema because its writers are `Warehouse/generation/`, which
must not import the run harness. All three contracts derive an id from a declared shape rather
than letting anyone choose a version number, and all follow the same store layout (immutable
content-addressed documents + a mutable INDEX); they are not layered on each other, and
runschema and Schema share only the template vocabulary (`pathtpl.py`, which runschema
re-exports).

## Why it is top-level

`context/architecture.yml` forbids `warehouse_core → optimization`, and `Warehouse/generation/`
writes `inventory.db` and `affinity.db`. A module serving every family therefore cannot live under
`Optimization/`. It is stdlib-only and imports nothing else in the repo, so every layer may use it.

`Warehouse/kernel/` and `Warehouse/physical.py` may **not** — they carry `forbid: [X, "*"]` and must
stay dependency-free. Neither touches a database.

## The publisher-consumer rule

**Publishers dictate; consumers bind.** Simulation runs and catalogue generation define the newest
shapes — so the vocabulary that adapts versions lives BESIDE the DDL (sim_db's named queries and
capabilities in `Optimization/persistence/Picking_Data.py`), while the machinery that dispatches
them lives here, domain-blind. A consumer calls `ds.query('batch_frame')` and binds to LOGICAL
column names; a vintage whose physical schema differs gets a per-id `dataset.override(...)`
registered on the publisher side. **A schema change is never a consumer edit.**

## The rule that makes an id trustworthy

**A family never hand-lists its columns.** `Family.declared_shape` is a callable that BUILDS the
schema in memory using the same DDL the writer executes, and the id is hashed from the result. A
declaration produced from the writer's own DDL cannot drift from it; a hand-maintained list can and
will.

## Changing a schema

Versioning is imposed at runtime now — every writer stamps AND verifies the store at DB creation
(`compat.stamp_checked`), `run_simulation` blocks at startup on an inconsistent store, and the
Stop hook nags on DDL drift. The manual loop is three commands:

```bash
python scripts/schema_report.py --sync      # BEFORE the DDL edit (idempotent; run it always)
# ... edit the DDL ...
python scripts/schema_report.py --accept    # adopts the outgoing shape + re-syncs; leaves a
                                            # TODO(schema-adopt) comment naming what YOU write:
                                            # the commit window the old id covers
```

If the change renames or removes a column a named query reads, register a per-vintage
`dataset.override(...)` for the OUTGOING id beside the family — never edit a consumer.
The `schema-maintainer` agent owns the whole procedure.

The profiles-tree contract has the same two commands in one CLI: `python -m Schema.profile_tree
--check` (the gate) and `--write` (mint/refresh; the id only moves on a real shape change —
descriptors are forward-only, never fabricated for an old catalogue).

## Why anyone should care

Nothing in this project raises when a column disappears. Every loader in `Picking_Data.py` does
`SELECT *` and guards with `row.keys()`, and `Performance_Evaluations/` reaches all of them through
those loaders — so a dropped or renamed column does not fail. It silently becomes the dataclass
default `0.0`/NaN in `common/frames.py`, and a published number quietly changes.

That is the quietest failure mode in the codebase. This package exists to make it loud — at CI
time (`validate`), at write time (`stamp_checked`), at bind time (`bind(requires=...)`), and at
read time (`UnsupportedQuery`), in that order of preference.
