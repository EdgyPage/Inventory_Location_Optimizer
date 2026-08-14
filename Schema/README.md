# Schema — what shape is this database, and is it the one we expect?

Two things live here, and nothing else:

| Module | Owns |
|---|---|
| `shape.py` | the canonical SQL shape of a SQLite file, its 12-hex id, and the diff between two shapes |
| `identity.py` | the registry of DB families, and the stamped → pinned → derived resolution |
| `connect.py` | the three sanctioned ways to open a SQLite file |

**Not to be confused with `Optimization/runschema/`.** That package answers *where do a run's
files live* — it content-addresses the directory **tree**. This one answers *what is inside one
file*. Both derive an id from a declared shape rather than letting anyone choose a version number,
which is why they look similar; they are not layered on each other and neither imports the other.

## Why it is top-level

`context/architecture.yml` forbids `warehouse_core → optimization`, and `Warehouse/generation/`
writes `inventory.db` and `affinity.db`. A module serving every family therefore cannot live under
`Optimization/`. It is stdlib-only and imports nothing else in the repo, so every layer may use it.

`Warehouse/kernel/` and `Warehouse/physical.py` may **not** — they carry `forbid: [X, "*"]` and must
stay dependency-free. Neither touches a database.

## The rule that makes an id trustworthy

**A family never hand-lists its columns.** `Family.declared_shape` is a callable that BUILDS the
schema in memory using the same DDL the writer executes, and the id is hashed from the result. A
declaration produced from the writer's own DDL cannot drift from it; a hand-maintained list can and
will.

## What belongs here

Anything that answers "what shape is this artifact, and can I trust it". Adding a DB family is one
`Family(...)` registration.

**Does NOT belong here:** DDL for any specific database (that lives with its writer —
`Optimization/persistence/`, `Warehouse/generation/`, `Visualization/cache_schema.py`), path
construction (→ `Optimization/runschema/`), or anything that reads rows rather than structure.

## Why anyone should care

Nothing in this project raises when a column disappears. Every loader in `Picking_Data.py` does
`SELECT *` and guards with `row.keys()`, and `Performance_Evaluations/` reaches all of them through
those loaders — so a dropped or renamed column does not fail. It silently becomes the dataclass
default `0.0`/NaN in `common/frames.py`, and a published number quietly changes.

That is the quietest failure mode in the codebase. This package exists to make it loud.
