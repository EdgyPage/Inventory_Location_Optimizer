"""dataset.py — bind a database file to its OWN schema version, and read it by NAME.

The read-side half of runtime versioning.  ``compat.py`` answers "may this consumer read that
family"; this module answers the next question — "read THIS file, whatever vintage it is" —
without the consumer writing SQL, probing PRAGMAs, or branching on a version anywhere.

    ds = dataset.bind(path, 'sim_db', requires=REQUIRES)
    for row in ds.query('batch_frame', run_id=7):
        row['thr_batch']            # a LOGICAL column name — stable across every vintage

Three layers, and where each lives:

  MACHINERY (this module, stdlib + Schema only)
      `bind()` resolves the file's own id (stamped -> pinned -> derived), loads THAT shape from
      the committed store, and returns a `Dataset` whose every read is validated against the
      bound shape.  `Query`/`register_query`/`override` are the registry types.
  VOCABULARY (beside each family's DDL — the PUBLISHER side)
      Named queries with a stable LOGICAL output column set, e.g. sim_db's in
      `Optimization/persistence/Picking_Data.py`.  The publisher dictates shapes, so the
      publisher's module is where physical->logical mapping lives.
  OVERRIDES (small per-vintage modules beside the family)
      A vintage whose physical schema differs (renamed column, absent table) registers a variant
      for its schema id — the same dispatch `Visualization/readers/` proves with `SCHEMA_IDS`.
      Consumers never see any of it.

WHY LOGICAL COLUMNS ARE THE CONTRACT
------------------------------------
A column rename is the quietest schema change: `SELECT *` + `row.keys()` guards turn it into a
default 0.0 on a published figure.  Naming the OUTPUT vocabulary decouples every consumer from
the physical schema: canonical SQL aliases physical->logical (`SELECT completion_rate AS
thr_batch`), an override for an old vintage re-aliases its OWN physical names to the SAME logical
names, and the registry enforces that every variant of a query yields the same logical set.  The
repo already hand-rolled this twice before it was a system (the loaders' legacy-alias branches,
`_bdf`'s completion_rate->thr_batch mirror); those become registrations instead of special cases.

SQL POLICY
----------
Identifier positions (tables, columns) are GENERATED from validated names; predicate/aggregate
text is HUMAN-WRITTEN and parameterized — generating the drift-prone part, never the expressive
part.  `Dataset.read()` builds simple selections; `Query.sql` carries arbitrary hand-written SQL
(joins, aggregates) whose output columns are the declared logical set; `.con` remains the
documented escape hatch, with the rule that escape-hatch reads still appear in a `Requires`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from Schema import capability as _capability
from Schema import compat as _compat
from Schema import connect as _connect
from Schema import identity as _identity
from Schema.shape import canonical_shape, shape_id


class DatasetError(_identity.SchemaError):
    """Base for dataset-binding and named-query failures."""


class UnsupportedQuery(DatasetError):
    """No canonical or override SQL can serve this (schema id, query) pair.

    Raised instead of guessing: a query silently served by SQL written for a different shape is
    the plausible-wrong-number failure this whole layer exists to prevent.
    """

    def __init__(self, query: str, schema: str, detail: str = ''):
        self.query, self.schema = query, schema
        msg = (f'query {query!r} cannot be served for schema {schema}: no override is registered '
               f'for that id and the canonical SQL needs tables/columns it lacks.')
        if detail:
            msg += f' {detail}'
        msg += (' Register a per-vintage override beside the family (see '
                'docs/design/SCHEMA_COMPATIBILITY.md).')
        super().__init__(msg)


# ── the registry ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True, eq=False)
class Query:
    """One named read: hand-written SQL whose OUTPUT columns are a stable logical vocabulary.

    `columns` is the contract consumers bind to — every override must yield exactly the same set.
    `optional` maps logical names that may be absent on some vintages to their default value;
    the canonical/override SQL simply omits them and `Dataset.query` fills the default, which is
    the registry form of the loaders' `row.keys()` guards.  `tables` names what the CANONICAL
    sql touches, so servability can be answered from the bound shape without parsing SQL.
    """
    name: str
    family: str
    sql: str
    columns: tuple                       # logical output names, the cross-vintage contract
    tables: dict = field(default_factory=dict)   # table -> (physical cols the canonical sql reads)
    optional: dict = field(default_factory=dict) # logical name -> default when a vintage lacks it


#: (family, query_name) -> Query
_QUERIES: dict = {}
#: (family, query_name, schema_id) -> sql
_OVERRIDES: dict = {}


def register_query(q: Query) -> Query:
    key = (q.family, q.name)
    if key in _QUERIES:
        raise ValueError(f'query already registered: {q.family}.{q.name}')
    _QUERIES[key] = q
    return q


def override(family: str, query_name: str, schema_ids, sql: str) -> None:
    """Register a per-vintage variant.  `schema_ids` may be one id or an iterable.

    The override's SQL must yield the query's exact logical column set minus any `optional`
    names it cannot supply — `Dataset.query` fills those defaults.  Enforced at call time (the
    row keys are checked against the contract) and by the registry tests.
    """
    ids = (schema_ids,) if isinstance(schema_ids, str) else tuple(schema_ids)
    q = _QUERIES.get((family, query_name))
    if q is None:
        raise ValueError(f'override for unregistered query: {family}.{query_name}')
    for sid in ids:
        key = (family, query_name, sid)
        if key in _OVERRIDES:
            raise ValueError(f'override already registered: {family}.{query_name} @ {sid}')
        _OVERRIDES[key] = sql


def queries_for(family: str) -> tuple:
    """Registered query names for one family, sorted — the discoverable read surface."""
    return tuple(sorted(n for f, n in _QUERIES if f == family))


def sql_for(family: str, name: str, schema_id: str) -> str:
    """The composed SQL that would serve named query `name` on vintage `schema_id` — no file.

    Same resolution as `Dataset.query` (an `override(...)` for the id wins, else the canonical
    SQL if the COMMITTED shape for that id carries the tables/columns it declares), answered
    from the store instead of a bound connection.  For consumers that want the statement itself
    rather than rows — the run_whatif_* trio pattern, where one composed query fans out over
    many files.  Raises `UnsupportedQuery` rather than guessing, exactly like the bound path.
    """
    q = _QUERIES.get((family, name))
    if q is None:
        raise DatasetError(f'no query {name!r} registered for family {family!r} '
                           f'(known: {", ".join(queries_for(family)) or "none"})')
    sql = _OVERRIDES.get((family, name, schema_id))
    if sql is not None:
        return sql
    fam = _identity.get(family)
    shape = (fam.declared_shape() if schema_id == fam.declared_id()
             else _compat.load_shape(family, schema_id))
    if shape is None:
        raise UnsupportedQuery(name, schema_id,
                               'No committed shape document for that id, so canonical '
                               'servability cannot be checked.')
    surface = _compat._surface(shape)
    unmet = [f'{t}.{c}' for t, cs in q.tables.items() for c in cs
             if not (surface.get(t) is not None and c in surface[t])]
    if unmet:
        raise UnsupportedQuery(name, schema_id, f'Missing: {", ".join(sorted(unmet))}.')
    return q.sql


# ── binding ──────────────────────────────────────────────────────────────────────

def bind(path: str, family: str, *, requires=None, pinned: str | None = None,
         verify: bool = True, immutable: bool = False) -> 'Dataset':
    """Open `path` read-only and bind it to ITS OWN schema version.

    Resolution is stamped -> pinned -> derived (`identity.resolve`); an id the family does not
    vet raises `UnsupportedSchema` with the structural diff, exactly like `identity.check`.  The
    bound shape is the file's own — the declared shape when the id is current, else the committed
    document (`UnrecoverableShape` if the store lacks it, which the write-time gates now prevent).

    `requires` (a `compat.Requires`) is validated against the BOUND shape at bind time, so a
    consumer fails at open with the missing column named, not mid-analysis with a default 0.0.
    `immutable=True` is for finished archives only — it skips WAL sidecar creation but the caller
    must promise nothing is writing (see `Schema.connect.read_only`).
    """
    fam = _identity.get(family)
    con = _connect.read_only(path, immutable=immutable)
    try:
        sid, source = _identity.resolve(con, fam, pinned=pinned, verify=verify)
        if sid not in fam.supported_ids():
            from Schema.identity import UnsupportedSchema
            from Schema.shape import describe_diff, diff_shapes
            detail = describe_diff(diff_shapes(fam.declared_shape(), canonical_shape(con)))
            con.close()
            raise UnsupportedSchema(family, sid, fam.supported_ids(), detail)
        if sid == fam.declared_id():
            shape = fam.declared_shape()
        else:
            shape = _compat.load_shape(family, sid)
            if shape is None:
                con.close()
                raise _compat.UnrecoverableShape(
                    f'{family}: vetted id {sid} has no committed shape document, so this file '
                    f'cannot be bound. Capture it: python scripts/schema_report.py --capture '
                    f'{family} <a file of that vintage>')
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        raise
    ds = Dataset(path=path, family=fam, con=con, schema_id=sid, source=source, shape=shape)
    if requires is not None:
        gaps = requires.missing_from(_compat._surface(shape))
        if gaps:
            ds.close()
            raise _compat.RequirementUnmet(
                f'{requires.label}: {path} (schema {sid}) cannot serve this consumer - '
                f'{"; ".join(gaps)}.')
    return ds


class Dataset:
    """One bound file: reads validated against the file's OWN shape, queries served by vintage.

    Not a context manager by accident — it IS one (`with dataset.bind(...) as ds:`), and `.con`
    stays public as the documented escape hatch for hand-written aggregate SQL (whose reads must
    still appear in the consumer's `Requires`, where CI can see them).
    """

    def __init__(self, *, path: str, family, con: sqlite3.Connection,
                 schema_id: str, source: str, shape: dict):
        self.path = path
        self.family = family
        self.con = con
        self.schema_id = schema_id
        self.source = source                       # 'stamped' | 'pinned' | 'derived'
        self.shape = shape
        self._surface = _compat._surface(shape)

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> 'Dataset':
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:                                 # pragma: no cover - debug aid
        import os
        return f'<Dataset {self.family.name}:{self.schema_id} {os.path.basename(self.path)}>'

    # ── shape questions (answered from the BOUND shape, never PRAGMA) ──────────
    def has(self, table: str, cols=()) -> bool:
        """Does THIS file's shape carry `table` (and `cols`, when given)?

        This replaces ad-hoc `PRAGMA table_info` probes: the bound shape already knows, and it
        knows for a committed historical vintage even before the file is touched.
        """
        have = self._surface.get(table)
        return have is not None and set(cols) <= have

    # ── generic selection (identifier-validated) ───────────────────────────────
    def read(self, table: str, cols, where: str = '', params=(), order_by: str = ''):
        """Rows from one table, identifiers validated against the bound shape.

        `table`/`cols`/`order_by` columns must exist in THIS file's shape — a typo or a
        wrong-vintage read fails by name at the call site instead of returning silent defaults.
        `where` stays a caller-written parameterized fragment: the expressive part of SQL is
        never generated here (see the module docstring's SQL POLICY).
        """
        cols = tuple(cols)
        missing = [c for c in cols + ((order_by,) if order_by else ()) if c and
                   not self.has(table, (c,))]
        if not self.has(table):
            raise DatasetError(f'{self.family.name}:{self.schema_id} has no table {table!r}')
        if missing:
            raise DatasetError(
                f'{self.family.name}:{self.schema_id} table {table!r} lacks column(s) '
                f'{", ".join(sorted(set(missing)))}')
        sql = f'SELECT {", ".join(_q(c) for c in cols)} FROM {_q(table)}'
        if where:
            sql += f' WHERE {where}'
        if order_by:
            sql += f' ORDER BY {_q(order_by)}'
        cur = self.con.execute(sql, tuple(params))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur]

    # ── named queries (the versioned layer) ────────────────────────────────────
    def query(self, name: str, **params):
        """Run a NAMED query, served by whichever SQL matches this file's vintage.

        Resolution: an `override(...)` registered for this schema id wins; else the canonical
        SQL runs if the bound shape carries the tables/columns it declares; else
        `UnsupportedQuery` names the (id, query) pair.  Missing OPTIONAL logical columns are
        filled with their declared defaults, so the output contract is identical on every
        vintage that can be served at all.
        """
        q = _QUERIES.get((self.family.name, name))
        if q is None:
            raise DatasetError(f'no query {name!r} registered for family {self.family.name!r} '
                               f'(known: {", ".join(queries_for(self.family.name)) or "none"})')
        sql = _OVERRIDES.get((self.family.name, name, self.schema_id))
        if sql is None:
            unmet = [f'{t}.{c}' for t, cs in q.tables.items() for c in cs
                     if not self.has(t, (c,))]
            if unmet:
                raise UnsupportedQuery(name, self.schema_id,
                                       f'Missing: {", ".join(sorted(unmet))}.')
            sql = q.sql
        cur = self.con.execute(sql, params if params else {})
        names = [d[0] for d in cur.description]
        contract = set(q.columns)
        out = []
        for row in cur:
            rec = dict(zip(names, row))
            extra = set(rec) - contract
            if extra:
                raise DatasetError(
                    f'query {name!r} @ {self.schema_id} emitted column(s) outside its logical '
                    f'contract: {", ".join(sorted(extra))} - fix the SQL alias list, not the '
                    f'consumer.')
            for logical, default in q.optional.items():
                rec.setdefault(logical, default)
            missing = contract - set(rec)
            if missing:
                raise DatasetError(
                    f'query {name!r} @ {self.schema_id} omitted non-optional logical column(s): '
                    f'{", ".join(sorted(missing))}')
            out.append(rec)
        return out

    # ── conditional data (the ONLY route) ──────────────────────────────────────
    def negotiate(self, ladder, run_id: int | None = None, extra=()):
        """Probe-and-pick over a capability ladder — see `Schema.capability`.

        Returns the chosen `Capability` (carry `capability.provenance(cap)` into whatever you
        emit) or raises `NoSourceAvailable` naming everything tried.
        """
        available = _capability.probe(self.con, ladder, run_id=run_id, extra=extra)
        return _capability.require(available, ladder,
                                   what=f'{self.family.name}:{self.schema_id}')


def _q(identifier: str) -> str:
    """Double-quote a VALIDATED identifier.  Validation happened against the bound shape, so the
    only job left is quoting; embedded quotes cannot occur in a name the shape store accepted."""
    return f'"{identifier}"'
