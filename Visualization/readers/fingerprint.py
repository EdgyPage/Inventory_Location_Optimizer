"""fingerprint.py — resolving WHICH sim-DB schema a file has, and explaining a mismatch.

The normalizer and the structural diff live in `Schema/shape.py`, shared by every DB family;
the sim DB's DECLARED shape comes from `Picking_Data.declared_sim_schema_shape`, which builds the
schema its own writer builds, so the declaration cannot drift from reality.

This module is the thin sim-specific layer over both, and adds the one thing only a *reader*
needs: :func:`resolve_schema_id`, the stamped -> pinned -> derived precedence.  Everything else
here is a re-export, kept so the reader package has a single import surface.

Why the diff matters: ``init_run_db`` is all ``CREATE TABLE IF NOT EXISTS``, so a DB written by
old code and reopened by new code gains the missing TABLES but never the missing COLUMNS — it
matches neither declaration.  "hash 4f2a… is unknown" is useless for that; "picks is missing
column quantity; index ix_pe_run_batch_time absent" is actionable.
"""
from __future__ import annotations

import sqlite3

from Optimization.persistence.Picking_Data import declared_sim_schema_shape, sim_schema_id
from Schema.shape import (                                # the single normalizer, one per repo
    canonical_shape as canonical_schema_shape,
    describe_diff, diff_shapes,
    observed_id as observed_sim_schema_id,
    shape_id as schema_shape_id,
)

__all__ = [
    'canonical_schema_shape', 'declared_sim_schema_shape', 'observed_sim_schema_id',
    'schema_shape_id', 'sim_schema_id',
    'resolve_schema_id', 'read_stamped_id', 'diff_shapes', 'describe_diff',
]


def read_stamped_id(con: sqlite3.Connection) -> str | None:
    """The `simulation_runs.sim_schema_id` a run stamped, or None.

    None for every run written before the column existed — which is every run in the current
    archive.  That is the expected path, not an error.
    """
    try:
        row = con.execute(
            'SELECT sim_schema_id FROM simulation_runs '
            'WHERE sim_schema_id IS NOT NULL ORDER BY run_id LIMIT 1').fetchone()
    except sqlite3.OperationalError:                  # pre-stamp schema: no such column
        return None
    return row[0] if row else None


def resolve_schema_id(con: sqlite3.Connection, pinned: str | None = None,
                      verify: bool = False) -> tuple[str, str]:
    """Resolve this DB's schema id.  Returns ``(schema_id, source)``.

    Precedence is **stamped -> pinned -> derived**, because deriving costs ~10 PRAGMA round trips
    per file and the archive holds hundreds of them:

      ``'stamped'``  the run recorded its own id (runs written from this build onward)
      ``'pinned'``   a previous derivation was cached in the sidecar's `cache_meta`
      ``'derived'``  read out of `sqlite_master` now; callers that can write a sidecar should
                     pin the result so it is derived once, not once per request

    With ``verify=True`` the id is always re-derived and checked against whatever was stamped or
    pinned; a disagreement raises `SimSchemaDrift` (a file migrated after the run wrote it, or
    a corrupted pin).  This is the ``--verify`` escape hatch, not the default: the stamp is
    provenance, but the OBSERVED shape is what queries must be planned against.
    """
    from Visualization.readers.protocol import SimSchemaDrift

    claimed = read_stamped_id(con) or pinned
    claimed_source = 'stamped' if read_stamped_id(con) else ('pinned' if pinned else None)

    if claimed is None or verify:
        derived = observed_sim_schema_id(con)
        if claimed is not None and derived != claimed:
            raise SimSchemaDrift(
                f'{claimed_source} sim schema id {claimed} but the file is actually {derived}; '
                f'it was migrated after the run wrote it. '
                f'{describe_diff(diff_shapes(declared_sim_schema_shape(), canonical_schema_shape(con)))}')
        return (derived, 'derived') if claimed is None else (claimed, claimed_source)
    return claimed, claimed_source


# Structural diffing now lives in Schema/shape.py and is re-exported above — it was never
# sim-specific, and every DB family needs it.
