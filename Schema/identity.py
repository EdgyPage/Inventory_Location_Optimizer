"""identity.py — which schema is this database, and is it the one we expect?

The registry of DB families and the stamped -> pinned -> derived resolution that turns a file into
a known shape.

Why a database needs an identity at all
---------------------------------------
Nothing in this project raises when a column disappears. Every loader in
`Optimization/persistence/Picking_Data.py` does `SELECT *` and guards with `row.keys()`, and
`Optimization/Performance_Evaluations/` reaches all of them through those loaders — so a renamed
or dropped column does not fail, it silently becomes the dataclass default `0.0`/NaN in
`common/frames.py`, and a published number quietly changes. That is the quietest failure mode in
the codebase. An identity check converts it into a loud one.

Identity is DERIVED FROM SHAPE, never chosen — the same trick
`Optimization/runschema/contract.py` uses for the run tree. Nobody picks a version number, two
branches cannot both call themselves "v3", and any consumer can re-derive it to check.

Resolution order is **stamped -> pinned -> derived**, because deriving costs ~10 PRAGMA round
trips per file and an archive holds hundreds:

    stamped   the writer recorded its own id in the file (families that have a meta table)
    pinned    a previous derivation was cached alongside (e.g. a derived sidecar)
    derived   read out of `sqlite_master` now
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from Schema import connect
from Schema.shape import canonical_shape, describe_diff, diff_shapes, observed_id, shape_id


#: The stamp table every family with `meta_table` set must DECLARE in its own DDL.
#: `stamp()` creates it idempotently, but a family that does not declare it would have an
#: observed shape (with the table) that never matches its declared shape (without) — so the
#: declaration has to include it.  `%s` is the family's chosen table name.
META_TABLE_DDL = 'CREATE TABLE IF NOT EXISTS %s (key TEXT PRIMARY KEY, value TEXT)'


def meta_ddl(table: str = 'schema_meta') -> str:
    """The stamp table's DDL, for a family to include in its own schema."""
    return META_TABLE_DDL % table


class SchemaError(Exception):
    """Base for every schema-identity failure."""


class UnsupportedSchema(SchemaError):
    """This database's shape is not one this build knows how to read."""

    def __init__(self, family: str, observed: str, known: tuple, detail: str = ''):
        self.family, self.observed, self.known, self.detail = family, observed, known, detail
        msg = (f'{family}: schema {observed} is not vetted. '
               f'Known: {", ".join(known) if known else "(none)"}.')
        if detail:
            msg += f' Difference from the current declaration: {detail}'
        super().__init__(msg)


class SchemaDrift(SchemaError):
    """The stamped or pinned id disagrees with the file's actual shape."""


@dataclass(frozen=True)
class Family:
    """One database family: what it is called, how it declares its shape, where it stamps it.

    `declared_shape` is a callable rather than a literal so a family's expectation is always
    produced by BUILDING the schema its writer builds — a hand-listed column set is exactly the
    kind of declaration that drifts.
    """
    name: str
    declared_shape: object                  # () -> shape dict
    meta_table: str | None = None           # key/value table holding the stamp, if any
    meta_key: str = 'schema_id'
    known_ids: tuple = field(default_factory=tuple)   # frozen historical shapes still supported

    def declared_id(self) -> str:
        return shape_id(self.declared_shape())

    def supported_ids(self) -> tuple:
        return tuple(dict.fromkeys((self.declared_id(),) + tuple(self.known_ids)))


_REGISTRY: dict[str, Family] = {}


def register(family: Family) -> Family:
    if family.name in _REGISTRY:
        raise ValueError(f'family already registered: {family.name}')
    _REGISTRY[family.name] = family
    return family


def get(name: str) -> Family:
    return _REGISTRY[name]


def families() -> tuple:
    return tuple(sorted(_REGISTRY))


# ── stamping ─────────────────────────────────────────────────────────────────────

def stamp(con: sqlite3.Connection, family: Family) -> str | None:
    """Record this build's declared id into the file. No-op for a family with no meta table."""
    if family.meta_table is None:
        return None
    sid = family.declared_id()
    con.execute(meta_ddl(family.meta_table))
    con.execute(f'INSERT OR REPLACE INTO {family.meta_table} (key, value) VALUES (?, ?)',
                (family.meta_key, sid))
    return sid


def read_stamp(con: sqlite3.Connection, family: Family) -> str | None:
    """The id a writer recorded, or None.

    None is the expected answer for every file written before its family was stamped — that is
    the normal path into derivation, not an error.
    """
    if family.meta_table is None:
        return None
    try:
        row = con.execute(
            f'SELECT value FROM {family.meta_table} WHERE key=?', (family.meta_key,)).fetchone()
    except sqlite3.Error:                       # table absent on a pre-stamp file
        return None
    return row[0] if row else None


def resolve(con: sqlite3.Connection, family: Family, *, pinned: str | None = None,
            verify: bool = False) -> tuple:
    """Resolve the schema id of `con`. Returns `(schema_id, source)`.

    With `verify=True` the id is always re-derived and checked against whatever was stamped or
    pinned; a disagreement means the file was migrated after it was written, and raises. The
    stamp is provenance, but the OBSERVED shape is what queries must be planned against.
    """
    stamped = read_stamp(con, family)
    claimed = stamped or pinned
    source = 'stamped' if stamped else ('pinned' if pinned else None)

    if claimed is None or verify:
        derived = observed_id(con)
        if claimed is not None and derived != claimed:
            raise SchemaDrift(
                f'{family.name}: {source} id {claimed} but the file is actually {derived}. '
                + describe_diff(diff_shapes(family.declared_shape(), canonical_shape(con))))
        return (derived, 'derived') if claimed is None else (claimed, source)
    return claimed, source


def check(path: str, family_name: str, *, verify: bool = False) -> str:
    """Open `path` read-only, resolve its id, and refuse anything unvetted.

    The one call a consumer needs before trusting a file's columns.
    """
    family = get(family_name)
    con = connect.read_only(path)
    try:
        sid, _source = resolve(con, family, verify=verify)
        if sid not in family.supported_ids():
            detail = describe_diff(diff_shapes(family.declared_shape(), canonical_shape(con)))
            raise UnsupportedSchema(family_name, sid, family.supported_ids(), detail)
        return sid
    finally:
        con.close()
