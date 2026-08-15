"""compat.py — what may a consumer READ, given every schema its family still vets?

`identity.py` answers "is this file one we know". That is a whole-file question, and it is the
wrong granularity for a consumer, which reads a handful of tables out of a dozen. The gap between
those two granularities is where this project's remaining schema babysitting lives:

    SIM_DB_FAMILY vets four ids. Three of them exist on the results drive and they differ by
    three whole tables (bin_eviction, bin_placement, bin_inventory) and one column
    (simulation_runs.sim_schema_id). `check()` passes all three. Nothing adapts. Today that is
    safe only because no evaluation happens to read those tables — an accident, not a guarantee.
    An evaluation that read `bin_placement` would be certified against the 2026-07-29 run that
    Experiment 6 is published from, and would then fail on it.

    HOW it fails depends on the loader, and both ways are bad:
      * `load_bin_placements` has NO OperationalError guard, so it RAISES `no such table:
        bin_placement` — loud, but only after a long analysis has already run;
      * a `SELECT *` loader guarded with `row.keys()` (which is most of them) yields the
        `common/frames.py` default 0.0 and PUBLISHES it — silent, and indistinguishable from a
        measurement.
    Declaring the read up front collapses both into one CI failure, before anything runs.

So a consumer declares what it needs (`Requires`), and two checks enforce it:

  STATIC   `validate(req)` — offline, no database. Is every table/column this consumer reads
           present in EVERY id the family vets? Runs in CI with no archive, and is the check
           that turns "safe by accident" into "safe by construction".
  RUNTIME  `check_requirements(path, req)` — this file, right now, at the consumer's own
           granularity. Names the missing COLUMN, not the file's hash.

Anything outside the guaranteed surface is reachable only by NEGOTIATION — probe for it and
degrade with a recorded caveat (`Diagnostics/replay_run._SOURCES` and
`Visualization/readers/protocol.CAP_*` are both this pattern, hand-rolled independently).

WHY HISTORICAL SHAPES ARE COMMITTED, NOT COMPUTED
-------------------------------------------------
A family's declared shape is BUILT from its writer's own DDL, so it is always re-derivable. A
HISTORICAL shape is not: `Picking_Data.PRE_STAMP_SIM_SCHEMA_ID` is frozen precisely because the
source that produced it is gone. The intersection therefore cannot be computed from declarations
alone, and a store of committed documents is the only way to make it computable offline — the
same reasoning, and the same layout, as `Optimization/schemas/run_tree/<short>.json`.

An id in `known_ids` with no committed document is UNRECOVERABLE: its shape is unknown, so the
true intersection is unknowable and any surface computed without it is over-optimistic. Every
function here reports that rather than quietly computing the smaller intersection.

The CLI over this module lives in `scripts/schema_report.py`, NOT here: reporting on every family
means importing every writer, and `context/architecture.yml` forbids the `schema` layer from
importing `opt_persistence`, `generation` or `visualization`. This module takes a family NAME and
reads the committed store; it never learns who writes what.

    python scripts/schema_report.py --report                     # the surface, per family
    python scripts/schema_report.py --capture sim_db <file.db>   # commit a historical shape
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import warnings as _warnings
from dataclasses import dataclass, field

from Schema import connect
from Schema.identity import SchemaError, get as _get_family, stamp as _stamp
from Schema.shape import canonical_shape, observed_id, shape_id

#: Committed historical shapes: Schema/shapes/<family>/<short>.json
SHAPES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shapes')

#: A `Requires` entry asking for a table but naming no particular column.
ANY_COLUMNS: tuple = ()


class UnrecoverableShape(SchemaError):
    """A vetted id has no committed shape document, so the guaranteed surface is unknowable."""


class RequirementUnmet(SchemaError):
    """A consumer's declared tables/columns are not all present."""


class UncommittedShape(SchemaError):
    """A writer is producing files whose declared shape has no committed document.

    The window this names: a DDL edit ships, new files stamp the new id and READ fine (they match
    the declaration) — but the shape exists nowhere except in the live source, so the moment the
    DDL moves again it is unrecoverable, and meanwhile the OUTGOING id fell out of
    `supported_ids()`, quietly orphaning every file already on the archive.  `--sync` closes the
    first half, `--adopt` the second; this exception is how a WRITER finds out at write time
    instead of a reader months later.
    """


# ── the committed store ──────────────────────────────────────────────────────────

def shape_path(family: str, sid: str) -> str:
    return os.path.join(SHAPES_DIR, family, f'{sid}.json')


def load_shape(family: str, sid: str) -> dict | None:
    """The committed shape for one historical id, or None if it was never captured."""
    path = shape_path(family, sid)
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    stored = doc.get('shape')
    if stored is None or shape_id(stored) != sid:
        raise UnrecoverableShape(
            f'{family}: committed document {os.path.relpath(path, SHAPES_DIR)} does not hash to '
            f'{sid} - the shape store is corrupt.')
    return stored


def write_shape(family: str, sid: str, shape: dict, *, captured_from: str, note: str = '') -> str:
    """Commit one historical shape. `captured_from` is a RUN NAME, never a path.

    A machine-local path here would be written into a tracked file and blocked by
    `context/guards/path_guard.py` — and would be useless to anyone else besides.
    """
    if shape_id(shape) != sid:
        raise ValueError(f'shape hashes to {shape_id(shape)}, not {sid}')
    path = shape_path(family, sid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {
        'family': family,
        'shape_id': sid,
        'captured': _dt.datetime.now().replace(microsecond=0).isoformat(),
        'captured_from': captured_from,
        'note': note,
        'shape': shape,
    }
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, indent=2, sort_keys=False)
        fh.write('\n')
    return path


def known_shapes(family: str) -> dict:
    """`{schema_id: shape}` for every id this family vets, as far as it is recoverable.

    The DECLARED shape is always present (it is built from the writer's DDL); historical ids come
    from the committed store. Missing ones are reported by `unrecoverable_ids`, not silently
    dropped — see the module docstring.
    """
    fam = _get_family(family)
    out = {fam.declared_id(): fam.declared_shape()}
    for sid in fam.known_ids:
        shape = load_shape(family, sid)
        if shape is not None:
            out[sid] = shape
    return out


def unrecoverable_ids(family: str) -> tuple:
    """Vetted ids whose shape was never captured. Non-empty ⇒ the surface is a guess."""
    fam = _get_family(family)
    have = set(known_shapes(family))
    return tuple(sid for sid in fam.supported_ids() if sid not in have)


# ── write-time imposition ────────────────────────────────────────────────────────
#
# The read side of this module answers "may this consumer read that file".  These two answer the
# WRITER's question — "is the shape I am about to produce actually on the record" — at the moment
# the file is created, which is the only moment the answer is cheap to act on.

def verify_family_store(family: str) -> list[str]:
    """Findings for one family's store state, empty when clean.  Stdlib + committed store only.

    Two checks, matching the two halves of the `UncommittedShape` window:

      (a) the DECLARED shape has a committed document — else the shape this writer is producing
          right now becomes unrecoverable at the next DDL move;
      (b) no committed document is orphaned outside `supported_ids()` — an orphan IS the outgoing
          shape of a DDL change that has not been adopted, and every archived file written with
          it is currently unreadable.

    (b) is `adopt()`'s per-family logic from `scripts/schema_report.py`, re-homed where it is
    import-legal — a writer cannot import the CLI (`scripts/` is not a package consumers may
    depend on), but compat owns the store and may read its own directory.
    """
    fam = _get_family(family)
    out = []
    if load_shape(family, fam.declared_id()) is None:
        out.append(
            f'{family}: the CURRENT declared shape {fam.declared_id()} has no committed '
            f'document - files written now become unrecoverable at the next DDL change. '
            f'Run: python scripts/schema_report.py --sync')
    d = os.path.join(SHAPES_DIR, family)
    on_disk = ({fn[:-len('.json')] for fn in os.listdir(d) if fn.endswith('.json')}
               if os.path.isdir(d) else set())
    orphans = sorted(on_disk - set(fam.supported_ids()))
    if orphans:
        out.append(
            f'{family}: committed shape(s) {", ".join(orphans)} are no longer vetted - a DDL '
            f'change shipped without adopting its outgoing shape, so every archived file of '
            f'that vintage is unreadable. Run: python scripts/schema_report.py --adopt')
    return out


#: (family, kind) pairs already warned about, so a 34-arm sweep emits one line per problem
#: rather than one per worker.  Same idiom as `identity._WARNED`.
_STAMP_WARNED: set = set()


def stamp_checked(con: sqlite3.Connection, family_obj, *, strict: bool = False,
                  emit=None) -> str | None:
    """`identity.stamp` + `verify_family_store`, as one call every writer makes at DB creation.

    The stamp is written FIRST and unconditionally: whatever the store's state, an unstamped file
    is strictly worse than a stamped one — the stamp is what lets a future reader identify the
    vintage without deriving.  Then the store is verified:

      strict=False  warn once per (family, finding-kind) per process, via `emit` or
                    `warnings.warn`.  For writers inside a long run: a store gap discovered at
                    hour N must not kill an arm the run-start precheck already blessed.
      strict=True   raise `UncommittedShape`.  For interactive data-gen CLIs, where the fix is
                    one command and stopping costs nothing.

    Returns whatever `identity.stamp` returned (None for a meta_table-less family — the VERIFY
    half still runs for those, which is exactly why this exists for sim_db and keyframes_db too).
    """
    sid = _stamp(con, family_obj)
    problems = verify_family_store(family_obj.name)
    if not problems:
        return sid
    if strict:
        raise UncommittedShape('; '.join(problems))
    for msg in problems:
        key = (family_obj.name, msg.split(' - ')[0])
        if key in _STAMP_WARNED:
            continue
        _STAMP_WARNED.add(key)
        (emit or (lambda m: _warnings.warn(m, RuntimeWarning, stacklevel=3)))(msg)
    return sid


# ── the surfaces ─────────────────────────────────────────────────────────────────

def _surface(shape: dict) -> dict:
    return {t: frozenset(c['name'] for c in s['columns']) for t, s in shape['tables'].items()}


def guaranteed_surface(family: str, *, strict: bool = True) -> dict:
    """`{table: frozenset(columns)}` present in EVERY vetted shape — the version-free surface.

    A consumer that stays inside this reads every vetted vintage identically and needs no
    per-version code at all. That is the whole point: bespoke compatibility should be required
    only for what genuinely varies.

    With `strict` (the default) an unrecoverable id raises, because an intersection taken over
    the ids we happen to have is larger than the truth and would bless reads that are not
    actually safe.
    """
    missing = unrecoverable_ids(family)
    if missing and strict:
        raise UnrecoverableShape(
            f'{family}: no committed shape for vetted id(s) {", ".join(missing)}, so the '
            f'guaranteed surface cannot be computed. Capture one with '
            f'`python scripts/schema_report.py --capture {family} <a file of that vintage>`, or drop the '
            f'id from the family if no such file survives.')
    surfaces = [_surface(s) for s in known_shapes(family).values()]
    common = set.intersection(*[set(s) for s in surfaces])
    return {t: frozenset.intersection(*[s[t] for s in surfaces]) for t in sorted(common)}


def conditional_surface(family: str, *, strict: bool = True) -> dict:
    """`{table: frozenset(columns)}` present in SOME vetted shape but not all.

    Everything here must be negotiated at runtime — probed for, and degraded from with a caveat
    the consumer records. Nothing may read it unconditionally.
    """
    if strict:
        guaranteed_surface(family)          # raises on an unrecoverable id
    surfaces = [_surface(s) for s in known_shapes(family).values()]
    union: dict = {}
    for s in surfaces:
        for t, cols in s.items():
            union[t] = union.get(t, frozenset()) | cols
    guaranteed = guaranteed_surface(family, strict=False)
    out = {}
    for t in sorted(union):
        extra = union[t] - guaranteed.get(t, frozenset())
        if t not in guaranteed or extra:
            out[t] = union[t] if t not in guaranteed else extra
    return out


# ── what a consumer declares ─────────────────────────────────────────────────────

# eq=False: with the default eq=True, frozen=True synthesizes __hash__ over the fields — and
# `tables` is a dict, so hashing any Requires would raise TypeError the first time one landed in a
# set. These are module-level singletons; identity equality is the right semantics, and it keeps
# them usable as dict keys and set members.
@dataclass(frozen=True, eq=False)
class Requires:
    """The tables and columns ONE consumer reads out of ONE family.

    Declared next to the code that does the reading, so the declaration and the query move
    together. `label` names the consumer and goes in every message — "Performance_Evaluations
    context" is what a reader needs to see, not a stack trace.
    """
    family: str
    label: str
    tables: dict = field(default_factory=dict)      # table -> (columns,) | ANY_COLUMNS

    def missing_from(self, surface: dict) -> list:
        """Which of this consumer's needs `surface` does not satisfy, as readable clauses."""
        out = []
        for table in sorted(self.tables):
            if table not in surface:
                out.append(f'table {table} is absent')
                continue
            gap = sorted(set(self.tables[table]) - surface[table])
            if gap:
                out.append(f'{table} is missing column(s): {", ".join(gap)}')
        return out


def validate(req: Requires) -> list:
    """STATIC check, no database: is `req` inside its family's guaranteed surface?

    Empty list ⇒ this consumer is version-free by construction. Anything returned is a read that
    only some vetted vintages can serve, and must move behind a runtime capability probe.
    """
    return req.missing_from(guaranteed_surface(req.family))


def check_requirements(path: str, req: Requires, *, verify: bool = True) -> str:
    """RUNTIME check against one real file. Returns its schema id; raises `RequirementUnmet`.

    Complements `identity.check()` rather than replacing it: that one asks whether the FILE is
    vetted, this one asks whether it can answer THIS consumer's questions. A file can be vetted
    and still lack a table a particular caller needs — which is exactly the case `known_ids`
    creates and the reason this function exists.
    """
    con = connect.read_only(path)
    try:
        sid = observed_id(con) if verify else None
        actual = canonical_shape(con)
    finally:
        con.close()
    gaps = req.missing_from(_surface(actual))
    if gaps:
        # Plain ASCII: this reaches a Windows cp1252 console as well as a log file.
        raise RequirementUnmet(
            f'{req.label}: {os.path.basename(path)} (schema {sid or "unverified"}) cannot serve '
            f'this consumer - {"; ".join(gaps)}.')
    return sid or ''
