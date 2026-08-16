"""readers — the versioned data layer.

`reader_for(...)` is the ONE entry point.  It resolves a sim DB's schema id, looks up the vetted
reader registered for it, and binds it to the arm's files.  Everything above this line — routes,
views, the front end — sees only the `SimReader` protocol and never learns which schema it read.

Adding support for a new schema is one module: subclass `SqliteSimReader` (or implement the
protocol outright), set `SCHEMA_IDS`, and register it here.  No route and no view changes.
"""
from __future__ import annotations

import os
import sqlite3

from Schema import identity as _identity
from Schema.shape import canonical_shape, describe_diff, diff_shapes

from Optimization.persistence.Picking_Data import SIM_DB_FAMILY

from Visualization.readers.base import SqliteSimReader, _ro
from Visualization.readers.protocol import (
    SimReader, SimSchemaDrift, SimSchemaError, UnsupportedSimSchema,
)
from Visualization.readers.sim_2026_07 import Sim2026_07Reader

__all__ = ['reader_for', 'register', 'registered_schema_ids', 'SimReader', 'SimSchemaError',
           'UnsupportedSimSchema', 'SimSchemaDrift', 'SqliteSimReader']

#: schema id -> reader class.
_REGISTRY: dict[str, type] = {}


def register(cls: type) -> type:
    """Register a reader class under every id in its `SCHEMA_IDS`."""
    for schema in cls.SCHEMA_IDS:
        existing = _REGISTRY.get(schema)
        if existing is not None and existing is not cls:
            raise ValueError(f'schema {schema} is already handled by {existing.__name__}')
        _REGISTRY[schema] = cls
    return cls


def registered_schema_ids() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


register(Sim2026_07Reader)


def reader_for(sim_db: str, warehouse_db: str, run_id: int, *,
               keyframe_db: str = '', viz_cache: str = '',
               pinned_schema_id: str | None = None, verify: bool = False) -> SimReader:
    """Bind the vetted reader for `sim_db`.

    `pinned_schema_id` short-circuits derivation — pass the value the sidecar cached, so a DB
    with no stamp is inspected once at precompute time rather than on every request.

    Raises `UnsupportedSimSchema` (no vetted reader) or `SimSchemaDrift` (`verify=True` and the
    file disagrees with what was stamped or pinned).  Neither is recoverable by guessing, which
    is the point: a viewer that reads an unknown schema on a best-effort basis produces a
    warehouse picture that looks right and is not.
    """
    con = _ro(sim_db)
    try:
        # THE shared resolution: stamped -> pinned -> derived, via the family's own
        # stamp_reader — one stamp read, one implementation, `identity.SchemaDrift` on a
        # lying stamp/pin.  (This replaced the viewer's private fingerprint copy.)
        schema, source = _identity.resolve(con, SIM_DB_FAMILY,
                                           pinned=pinned_schema_id, verify=verify)
        cls = _REGISTRY.get(schema)
        if cls is None:
            detail = describe_diff(diff_shapes(SIM_DB_FAMILY.declared_shape(),
                                               canonical_shape(con)))
            raise UnsupportedSimSchema(schema, registered_schema_ids(), detail)
    finally:
        con.close()

    if not keyframe_db:
        # Deliberately NOT resolved through the run-tree contract: `reader_for` takes bare file
        # paths and has no run root to bind a resolver to — and threading one through the reader
        # protocol would leak tree-schema knowledge into the layer whose whole point is knowing
        # only DB shapes.  Callers that DO hold a resolver (db_reader.discover_runs) resolve the
        # sidecar with `rt.keyframe_db(...)` and pass it in, so this splitext derivation is only
        # the last resort for a caller binding a bare sim DB with no discovery step.
        candidate = os.path.splitext(sim_db)[0] + '.keyframes.db'
        keyframe_db = candidate if os.path.exists(candidate) else ''
    return cls(sim_db=sim_db, warehouse_db=warehouse_db, run_id=run_id,
               keyframe_db=keyframe_db, viz_cache=viz_cache,
               schema_id=schema, schema_source=source)
