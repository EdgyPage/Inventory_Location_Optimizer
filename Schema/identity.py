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

import os
import sqlite3
import warnings
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

    **Pass `verify=True` unless you have measured that you cannot afford it.** Without it a
    STAMPED file is taken at its word, and a stamp is a claim the file makes about itself: a
    database altered after the run that wrote it still carries the old id, so `check` returns a
    vetted answer for a shape that is no longer vetted. Every wired consumer in the repo passes
    it. The stamped fast path is for cheaply IDENTIFYING files in bulk (which reader do these
    272 arms need?), not for deciding whether to trust one — and the archive is unstamped
    anyway, so for it the two cost exactly the same.

    Measured on the results drive: ~27 ms per archived sim DB, of which the re-derivation
    `verify=True` adds is ~1 ms. The cost is OPENING the file, not reading `sqlite_master`.

    **Side effect worth knowing**: opening a WAL database `mode=ro` CREATES its `-shm` (and
    `-wal`), and a read-only connection cannot remove them again — so fingerprinting an
    archived run leaves sidecars beside it. That is not this function's doing; it is true of
    every read here, and it is the mechanism behind the sidecars the archive accumulates. Only
    `connect.read_only(..., immutable=True)` avoids it, and only a caller can promise the file
    is not being written. Every current call site opens the same file again immediately
    afterwards, so none of them creates a sidecar that was not coming anyway.
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


def check_tables(path: str, expected: dict, tables, *, label: str) -> None:
    """`check()` for a consumer that can only reach PART of its family's declaration.

    A whole-file id needs the family, and a family lives with its WRITER — which is sometimes a
    module the reader must not import (`Warehouse/catalog/Affinity_Store.py` reads a file whose
    family is registered in `Warehouse/generation/generate_affinity.py`, a data-gen CLI that
    pulls matplotlib and pandas into every simulation worker). Comparing only the tables the
    consumer actually queries, against the DDL it already mirrors, gives that reader the same
    guarantee without the import — and turns "both DDLs must stay in step" from a comment into
    something the file enforces on itself every time it opens a database.

    Raises `UnsupportedSchema` naming the differing columns. `label` goes in the message in
    place of a family name, so it should say which tables were compared.
    """
    con = connect.read_only(path)
    try:
        actual = canonical_shape(con)
    finally:
        con.close()
    want = {'tables': {t: s for t, s in expected['tables'].items() if t in tables}}
    have = {'tables': {t: s for t, s in actual['tables'].items() if t in tables}}
    if shape_id(want) == shape_id(have):
        return
    raise UnsupportedSchema(label, shape_id(have), (shape_id(want),),
                            describe_diff(diff_shapes(want, have)))


#: Paths already reported by `check_or_warn`, so a walk over 272 arms emits one line per bad
#: file rather than one per visit.  Keyed by (family, abspath) — the same file may be checked
#: as two families over a process's life, and both are worth hearing about once.
_WARNED: set = set()


def check_or_warn(path: str, family_name: str, *, verify: bool = False,
                  emit=None) -> str | None:
    """`check()` for consumers that must DEGRADE rather than die.  Returns None if unvetted.

    The split is deliberate and the choice is per-site, not per-family:

      hard `check()`   a simulation about to WRITE from this file, or an analysis about to
                       PUBLISH a number from it.  A wrong column there becomes a plausible
                       `0.0` and a figure nobody can tell is wrong.
      `check_or_warn`  an interactive, read-only, exploratory consumer, and derived caches that
                       can rebuild themselves.  Refusing to open a 2026-06 archive outright is a
                       worse failure than drawing it with a named caveat in the log.

    Either way the `UnsupportedSchema` TEXT — which names the differing tables and columns — is
    what reaches the caller; that message is the whole point of the layer, and a bare `except`
    that drops it leaves the consumer no better off than before this package existed.

    `emit` takes the message (e.g. `log.warning`); the default routes to `warnings.warn`, which
    a viewer's logging config already captures and a test can assert on with `pytest.warns`.
    """
    key = (family_name, os.path.abspath(path))
    try:
        return check(path, family_name, verify=verify)
    except SchemaError as exc:
        msg = str(exc)
    except (sqlite3.Error, OSError) as exc:
        # A truncated / half-copied / locked archive file. Same treatment: say what happened
        # once, then let the caller carry on with its own tolerance for a missing source.
        msg = f'{family_name}: {path} could not be fingerprinted ({type(exc).__name__}: {exc})'
    if key in _WARNED:
        return None
    _WARNED.add(key)
    (emit or (lambda m: warnings.warn(m, RuntimeWarning, stacklevel=3)))(msg)
    return None
