"""fingerprint.py — resolving WHICH sim-DB schema a file has, and explaining a mismatch.

The normalizer itself lives in `Optimization/persistence/Picking_Data` beside the DDL it
describes — `canonical_schema_shape` / `schema_shape_id` / `observed_sim_schema_id` /
`sim_schema_id`.  There is exactly one implementation and the declared id is produced by running
it over a schema the writer actually builds, so declaration cannot drift from reality.

This module adds the two things only a *reader* needs:

  * :func:`resolve_schema_id` — the stamped -> pinned -> derived precedence.
  * :func:`diff_shapes` / :func:`describe_diff` — what an ``UnsupportedSimSchema`` message says.

Why the diff matters: ``init_run_db`` is all ``CREATE TABLE IF NOT EXISTS``, so a DB written by
old code and reopened by new code gains the missing TABLES but never the missing COLUMNS — it
matches neither declaration.  "hash 4f2a… is unknown" is useless for that; "picks is missing
column quantity; index ix_pe_run_batch_time absent" is actionable.
"""
from __future__ import annotations

import sqlite3

from Optimization.persistence.Picking_Data import (       # the single normalizer
    canonical_schema_shape, declared_sim_schema_shape,
    observed_sim_schema_id, schema_shape_id, sim_schema_id,
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


# ── structural diffing ───────────────────────────────────────────────────────────

def diff_shapes(expected: dict, actual: dict) -> dict:
    """Structural difference `expected` -> `actual`, as plain data.

    ``{'tables_missing': [...], 'tables_extra': [...],
       'columns': {table: {'missing': [...], 'extra': [...], 'changed': [(col, exp, act)]}},
       'indexes': {table: {'missing': [...], 'extra': [...]}}}``
    """
    exp_t, act_t = expected['tables'], actual['tables']
    out = {
        'tables_missing': sorted(set(exp_t) - set(act_t)),
        'tables_extra': sorted(set(act_t) - set(exp_t)),
        'columns': {},
        'indexes': {},
    }
    for name in sorted(set(exp_t) & set(act_t)):
        e_cols = {c['name']: c for c in exp_t[name]['columns']}
        a_cols = {c['name']: c for c in act_t[name]['columns']}
        changed = [(c, e_cols[c], a_cols[c])
                   for c in sorted(set(e_cols) & set(a_cols)) if e_cols[c] != a_cols[c]]
        missing = sorted(set(e_cols) - set(a_cols))
        extra = sorted(set(a_cols) - set(e_cols))
        if missing or extra or changed:
            out['columns'][name] = {'missing': missing, 'extra': extra, 'changed': changed}

        e_idx = {i['name'] for i in exp_t[name]['indexes']}
        a_idx = {i['name'] for i in act_t[name]['indexes']}
        if e_idx - a_idx or a_idx - e_idx:
            out['indexes'][name] = {'missing': sorted(e_idx - a_idx),
                                    'extra': sorted(a_idx - e_idx)}
    return out


def describe_diff(d: dict, limit: int = 6) -> str:
    """One finding per clause, for an exception message."""
    lines = []
    if d['tables_missing']:
        lines.append(f"tables missing: {', '.join(d['tables_missing'])}")
    if d['tables_extra']:
        lines.append(f"tables extra: {', '.join(d['tables_extra'])}")
    for table, c in d['columns'].items():
        if c['missing']:
            lines.append(f"{table} is missing column(s): {', '.join(c['missing'])}")
        if c['extra']:
            lines.append(f"{table} has extra column(s): {', '.join(c['extra'])}")
        for col, exp, act in c['changed']:
            lines.append(f"{table}.{col} type changed: "
                         f"{exp['type'] or '<untyped>'} -> {act['type'] or '<untyped>'}")
    for table, i in d['indexes'].items():
        if i['missing']:
            lines.append(f"{table} is missing index(es): {', '.join(i['missing'])}")
        if i['extra']:
            lines.append(f"{table} has extra index(es): {', '.join(i['extra'])}")
    if not lines:
        return 'no structural difference found (the ids differ for another reason)'
    if len(lines) > limit:
        lines = lines[:limit] + [f'... and {len(lines) - limit} more']
    return '; '.join(lines)
