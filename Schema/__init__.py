"""Schema — the shape and identity of this project's SQLite databases.

`Schema/` owns the shape of each **database**.
`Optimization/runschema/` owns the shape of the run **directory tree**.
They are different questions and neither imports the other. See README.md.

Stdlib only, and importable from every layer — which is the reason it is a top-level package
rather than living under `Optimization/`: `context/architecture.yml` forbids
`warehouse_core -> optimization`, and `Warehouse/generation/` writes two of the DB families.

Nothing is imported eagerly; take what you need:

    from Schema import connect          # read_only / writer / bulk_writer
    from Schema import shape            # canonical_shape / shape_id / diff_shapes
    from Schema import identity         # Family / register / resolve / check
"""
