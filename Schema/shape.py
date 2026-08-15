"""shape.py — the canonical SQL shape of a SQLite database, and the diff between two.

Stdlib only, and deliberately family-agnostic: every function here takes a bare
`sqlite3.Connection` and knows nothing about which of the project's databases it is looking at.
That is what lets one implementation serve all eight families.

What the shape covers, per table (excluding `sqlite_%`):
  * `PRAGMA table_info`  -> (name, normalized type, notnull, pk) in cid order
  * `PRAGMA index_list` + `index_info` -> each index's name, uniqueness, and ORDERED columns
  * AUTOINCREMENT / WITHOUT ROWID, regexed out of `sqlite_master.sql`

Deliberately EXCLUDED — do not "fix" these without reading why:
  `sqlite_sequence`     a side effect of AUTOINCREMENT carrying no shape of its own, and not
                        reliably present on a database rebuilt by another path.  The bit that
                        matters is recovered by the AUTOINCREMENT flag instead.
  `sqlite_stat1..4`     created by ANALYZE.  Hashing them would mint a new id for a statistics
                        refresh and orphan every vetted reader — and ANALYZE is a plausible
                        thing to run here, since one viewer query is an unindexed 24 s scan.
  foreign keys          declared but never enforced (nothing sets `PRAGMA foreign_keys=ON`),
                        so a read-only consumer cannot observe them.
  views, triggers       none exist; excluding them makes adding one a conscious act.

Everything is sorted, so the ORDER of CREATE statements is not a schema change. Column types are
upper-cased and whitespace-collapsed but NOT resolved to SQLite affinities: renaming `INTEGER` to
`INT` is harmless but real, and should be visible rather than silently absorbed.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

#: Truncation length for a shape id.  Matches `Optimization/runschema/contract.SHORT_LEN`, which
#: truncates for the same reason: a full `sha256:<64 hex>` contains a ':', illegal in a Windows
#: filename, and 12 hex is ample for a namespace of a few dozen shapes.
SHORT_LEN = 12

_AUTOINCREMENT_RE = re.compile(r'\bAUTOINCREMENT\b', re.IGNORECASE)
_WITHOUT_ROWID_RE = re.compile(r'\bWITHOUT\s+ROWID\b', re.IGNORECASE)
_WHITESPACE_RE = re.compile(r'\s+')


def canonical_shape(con: sqlite3.Connection) -> dict:
    """The hashable SQL shape of every user table on this connection."""
    tables = {}
    master = {
        name: (sql or '')
        for name, sql in con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")
    }
    for name, sql in master.items():
        cols = [
            {'name': r[1], 'type': _WHITESPACE_RE.sub(' ', (r[2] or '').strip()).upper(),
             'notnull': int(r[3]), 'pk': int(r[5])}
            for r in con.execute(f'PRAGMA table_info("{name}")')
        ]
        indexes = [
            {'name': idx[1], 'unique': int(idx[2]),
             'cols': [r[2] for r in con.execute(f'PRAGMA index_info("{idx[1]}")')]}
            for idx in con.execute(f'PRAGMA index_list("{name}")')
        ]
        tables[name] = {
            'columns': cols,
            'indexes': sorted(indexes, key=lambda i: i['name']),
            # Invisible to table_info: INTEGER PRIMARY KEY and INTEGER PRIMARY KEY AUTOINCREMENT
            # look identical there, and six of the ten sim tables use the latter.
            'autoincrement': bool(_AUTOINCREMENT_RE.search(sql)),
            'without_rowid': bool(_WITHOUT_ROWID_RE.search(sql)),
        }
    return {'tables': dict(sorted(tables.items()))}


def shape_id(shape: dict) -> str:
    """12-hex id of a canonical shape."""
    blob = json.dumps(shape, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()[:SHORT_LEN]


def observed_id(con: sqlite3.Connection) -> str:
    """The id of the schema the database behind *con* actually has."""
    return shape_id(canonical_shape(con))


def shape_of_ddl(statements) -> dict:
    """Canonical shape of a schema built in memory from DDL statements.

    This is how a family declares its expected shape: by BUILDING it, never by hand-listing
    columns. A declaration derived from the same DDL the writer executes cannot drift from it.
    """
    con = sqlite3.connect(':memory:')
    try:
        for stmt in statements:
            con.executescript(stmt) if ';' in stmt.strip()[:-1] else con.execute(stmt)
        return canonical_shape(con)
    finally:
        con.close()


# ── diffing: what an "unsupported schema" message is built from ─────────────────

def diff_shapes(expected: dict, actual: dict) -> dict:
    """Structural difference `expected` -> `actual`, as plain data.

    Exists because every family's DDL is `CREATE TABLE IF NOT EXISTS`: a database written by old
    code and reopened by new code gains the missing TABLES but never the missing COLUMNS, so it
    matches neither declaration. "hash 4f2a… is unknown" is useless for that; "picks is missing
    column quantity" is actionable.
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
        # An empty diff means the two shapes are structurally IDENTICAL — with content-addressed
        # ids that means the ids are equal too, so a caller seeing this while holding two
        # different id strings has a bug upstream (e.g. comparing a full sha256:… form against a
        # 12-hex short form), not a schema mystery.  The old text ("the ids differ for another
        # reason") implied hashing could disagree with structure; it cannot.
        return 'the shapes are structurally identical'
    if len(lines) > limit:
        lines = lines[:limit] + [f'... and {len(lines) - limit} more']
    return '; '.join(lines)


def is_same_shape(a: dict, b: dict) -> bool:
    return shape_id(a) == shape_id(b)
