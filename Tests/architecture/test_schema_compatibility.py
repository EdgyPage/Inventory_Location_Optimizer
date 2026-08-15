"""test_schema_compatibility.py — a consumer's columns must survive EVERY schema its family vets.

`test_schema_identity.py` guards the whole-file question ("is this database one we know"). This
file guards the finer one `Schema/compat.py` exists to answer: given that a family vets four
different shapes, may a particular consumer READ what it reads out of all of them?

The two granularities come apart silently, by construction. `SIM_DB_FAMILY` vets four ids that
differ by three whole tables (`bin_eviction`, `bin_placement`, `bin_inventory`) and one column
(`simulation_runs.sim_schema_id`). `identity.check()` passes all four; every loader in
`Picking_Data.py` does `SELECT *` and guards with `row.keys()`; so a column that only some
vintages have does not raise — it arrives as the `common/frames.py` default `0.0` and is published
as a figure nobody can tell is wrong. Vetting the file proves nothing about the read.

What this file locks in
-----------------------
  * every id a family vets has a RECOVERABLE shape — the key gate. Adding a `known_ids` entry
    without capturing its shape fails here, because from that moment the guaranteed surface is a
    guess, and a guess taken over the shapes we happen to hold is always too LARGE;
  * the committed store is self-verifying (each document hashes to its own filename) and holds
    exactly the historical ids — no orphans, nothing uncaptured;
  * `guaranteed_surface` really is the intersection over every vetted shape, `conditional_surface`
    really is the rest, and together they partition the union;
  * `validate` / `check_requirements` name the offending TABLE and COLUMN, not a bare hash;
  * both wired consumers (`Picking_Data.REQUIRES`, the `Performance_Evaluations` context) stay
    inside the guaranteed surface — version-free by construction rather than by accident — and a
    third consumer cannot appear without being checked here;
  * `Schema/capability.py` — the negotiation itself, for everything OUTSIDE that surface. A probe
    answers on ROWS and not on a table's existence (a third of archived arms carry `reorder_queue`
    with nothing in it); `best` follows the LADDER's order rather than whatever happens to be
    available; and an inexact source with no caveat is refused at construction, because an
    approximation whose caveat was never written down is indistinguishable from a measurement;
  * `--sync` / `--adopt` keep the store complete MECHANICALLY. Committing the declared shape while
    it is still current is what stops the next DDL change from losing the outgoing one, and
    `--adopt` is what notices when a change shipped without its `known_ids` entry;
  * **the completeness gate** — a `Requires` must name everything its loaders actually READ.
    `compat.validate()` only proves the declaration stays INSIDE the guaranteed surface, which on
    its own is the wrong direction: an empty `Requires` passes it perfectly. Two real
    under-declarations reached review that way (`reorder_queue.unit_type`/`storage_size`, and ten
    `simulation_runs` columns), and neither was visible to any check in this file. The gate reads
    the loaders' own source with `ast` and fails on the gap.

Everything runs offline from the committed shape store and `tmp_path` fixtures: no archive, no
results drive, no `.env`, no `COMPARISON_OUTPUT_DIR`.

    python -m pytest Tests/architecture/test_schema_compatibility.py -q
"""
from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import sqlite3
import subprocess
import sys

import pytest

# Importing the writers is what populates the registry — Schema/ imports no writer, so a family
# exists only once its own module has been loaded.  Same list, and the same reason, as
# `test_schema_identity`; `Performance_Evaluations.core.context` is here as a CONSUMER, for its
# `REQUIRES` declaration rather than for a family.
from Optimization.Performance_Evaluations.core import context as eval_context
from Optimization.persistence import Picking_Data, Warehouse_Data, runtime_metrics  # noqa: F401
from Schema import capability, compat, identity, shape
from Visualization import cache_schema  # noqa: F401
from Warehouse.generation import generate_affinity, generate_inventory  # noqa: F401

# `scripts/` is a namespace package under the repo root that `Tests/conftest.py` already puts on
# sys.path.  Imported IN-PROCESS because `sync`/`adopt` need only the registry, which this module's
# own imports have already populated; `import_families()` itself is still exercised in a subprocess
# below, where nothing is pre-imported and a missing entry in `FAMILY_MODULES` therefore shows.
from scripts import schema_report

# Deliberately imported rather than re-listed: the identity gate owns the roster of families, and
# two copies of it would drift the moment one is edited.  A sibling helper import inside Tests/ is
# the established idiom (see `Tests/conftest.py` and `test_channel_strategy_subset`).
from test_schema_identity import EXPECTED_FAMILIES

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: Families that genuinely vet MORE THAN ONE shape.  Every surface property below is trivially
#: true for a family with a single vetted id, so this is the non-vacuity anchor for the whole
#: "the surfaces" section — see `test_at_least_one_family_really_has_several_shapes`.
MULTI_VINTAGE = {'sim_db', 'runtime_metrics_db', 'warehouse_db'}

#: Every consumer that DECLARES what it reads, and the file the declaration lives in.  Same idiom
#: as `test_schema_identity.VERIFIED_BY`: an allowlist asserted against the source, so a consumer
#: added WITHOUT a static check fails the suite instead of quietly reading a column that only some
#: vetted vintages have.  A `Requires` that nothing validates is a comment.
#: Keyed by `(file, SYMBOL)`, not by file — see `_requires_declaration_sites`. A file-keyed
#: allowlist cannot see a second declaration added to a module that already appears in it.
DECLARED_CONSUMERS = {
    ('Optimization/persistence/Picking_Data.py', 'REQUIRES'): Picking_Data.REQUIRES,
    ('Optimization/Performance_Evaluations/core/context.py', 'REQUIRES'): eval_context.REQUIRES,
}

#: The declaration is a constructor call, which makes it greppable — and worth keeping that way.
_REQUIRES_CALL = re.compile(r'\bRequires\s*\(')

#: Not scanned for `Requires(`: Tests/ (this file builds throwaway ones), Schema/ (the definition
#: site), and the usual noise.
#: `site/` is the gitignored mkdocs build output — a full copy of docs/, macros.py included — so
#: scanning it would double-count a declaration on any machine that has run `mkdocs build`.
_SKIP_DIRS = {'.git', '.idea', '.venv', '__pycache__', 'Schema', 'Tests', 'docs', 'node_modules',
              'venv', 'site', 'build', 'dist', '.pytest_cache', '.mypy_cache'}

#: The consumer whose reads are swept COLUMN BY COLUMN against its own declaration.  One file, and
#: deliberately this one: it is the shared read layer every evaluation reaches the sim DB through,
#: so an undeclared column here is an undeclared column everywhere downstream.
COMPLETENESS_TARGET = 'Optimization/persistence/Picking_Data.py'

#: `(table, column)` a loader probes for that exists in NO vetted shape — the far side of a rename
#: (`sigma_fd` was `sigma_fw`; `W` was `W_a`), kept as a second `row.keys()` branch so a file older
#: than anything this family still vets keeps loading.  These CANNOT be declared: `compat.validate`
#: would reject a column absent from the guaranteed surface, and the existing consumer test would
#: go red.  The exemption is safe only because it is checked from both ends —
#: `test_a_legacy_column_alias_is_read_but_exists_in_no_vetted_shape` asserts each entry is really
#: absent everywhere AND really still read, so it can neither hide a live column nor rot in place.
LEGACY_COLUMN_ALIASES = {('batch_stats', 'sigma_fw'), ('task_stats', 'W_a')}

#: How many read constructs the sweep is allowed to fail to attribute to a table.  **One today**:
#: `load_picker_events` hoists `cols = set(rows[0].keys())` and then reads through a closure
#: (`_g(row, 'pick_travel_x')`), so the guard's left operand is a PARAMETER and no static pass can
#: say which column it stands for.  The number is asserted rather than the constructs ignored: a
#: sweep that quietly stops attributing things degrades into checking nothing and reports success
#: forever, which is the same silence this whole file exists to remove.  Lower it, never raise it
#: without saying why in the commit.
MAX_UNATTRIBUTED_READS = 1

#: Tables (and single columns) the loaders NEGOTIATE for instead of declaring.  Derived from
#: `Picking_Data.CONDITIONAL_READS` rather than re-listed — a second copy would drift, and the
#: `_still_conditional` tests below already hold that list against the real surface.
_NEGOTIATED_TABLES = frozenset(t for t in Picking_Data.CONDITIONAL_READS.values() if '.' not in t)
_NEGOTIATED_COLUMNS = frozenset(tuple(t.split('.', 1))
                                for t in Picking_Data.CONDITIONAL_READS.values() if '.' in t)


# ── helpers ──────────────────────────────────────────────────────────────────────

def _columns_by_table(shape_doc: dict) -> dict:
    """`{table: {column, ...}}` read straight out of a shape document.

    Deliberately NOT `compat._surface`: a property test that reuses the implementation it is
    checking cannot catch a bug in it — the same reasoning as `test_registry_discovery`, which
    reads its registries out of the source with `ast` rather than importing them.
    """
    return {t: {c['name'] for c in spec['columns']} for t, spec in shape_doc['tables'].items()}


def _committed_documents() -> set:
    """`(family, schema_id)` for every document actually on disk in the committed store."""
    out = set()
    for family in sorted(os.listdir(compat.SHAPES_DIR)):
        d = os.path.join(compat.SHAPES_DIR, family)
        if not os.path.isdir(d):
            continue
        out |= {(family, fn[:-len('.json')]) for fn in sorted(os.listdir(d))
                if fn.endswith('.json')}
    return out


def _historical_ids() -> set:
    """`(family, schema_id)` for every vetted id that is NOT the current declaration.

    The declared shape is re-derivable from the writer's own DDL at any time; a historical one is
    gone with the code that produced it, so exactly these need a committed document.
    """
    out = set()
    for name in sorted(EXPECTED_FAMILIES):
        fam = identity.get(name)
        out |= {(name, sid) for sid in fam.supported_ids() if sid != fam.declared_id()}
    return out


def _probe_shape(columns=('id INTEGER', 'kept TEXT', 'dropped REAL')) -> dict:
    """A tiny throwaway shape, built the way a family builds its own — from DDL, never listed."""
    return shape.shape_of_ddl((f'CREATE TABLE probe ({", ".join(columns)})',))


_PROBE_FAMILY = 'probe_db'
_UNCAPTURED = 'deadbeefcafe'          # a vetted id no document will ever be found for


def _register_probe(monkeypatch, tmp_path, known_ids=()) -> object:
    """Register a synthetic family over an EMPTY shape store, for the duration of one test.

    Both mutations are `monkeypatch`ed, never written: `identity.register` refuses a duplicate
    name, and a family left behind in `_REGISTRY` would leak into every later test in the session
    — including the sweeps above, which walk whatever is registered.
    """
    monkeypatch.setattr(compat, 'SHAPES_DIR', str(tmp_path))
    fam = identity.Family(name=_PROBE_FAMILY, declared_shape=_probe_shape, known_ids=known_ids)
    monkeypatch.setitem(identity._REGISTRY, _PROBE_FAMILY, fam)
    return fam


def _tiny_db(path, statements) -> str:
    """A throwaway SQLite file under tmp_path. Nothing here ever opens the archive."""
    con = sqlite3.connect(str(path))
    try:
        for stmt in statements:
            con.execute(stmt)
        con.commit()
    finally:
        con.close()
    return str(path)


def _requires_declaration_sites() -> set:
    """Every production `Requires` binding, as `(repo-relative posix path, SYMBOL)`.

    Keyed by SYMBOL, not by file, and that distinction is the whole test. Keying by file asks
    "does this module declare a Requires" — which `Picking_Data.py` answers yes to for its first
    declaration and goes on answering for every one after it. A SECOND declaration added to an
    already-listed file is then invisible: it is never validated, and CI stays green while a
    consumer reads whatever it likes. That gap was live, and it took an injected
    `ARCHIVE_REQUIRES` reading two conditional tables to surface it.

    Found with `ast` rather than the regex, because a name is what has to be recovered: the regex
    below still guards the assumption that a declaration IS a constructor call (an alias or a
    factory would slip past the AST walk, and the two disagreeing is itself the signal).
    """
    found = set()
    for dirpath, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in sorted(files):
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding='utf-8') as fh:
                src = fh.read()
            if not _REQUIRES_CALL.search(src):
                continue
            rel = os.path.relpath(path, _ROOT).replace(os.sep, '/')
            tree = ast.parse(src, filename=rel)
            names = {t.id
                     for node in ast.walk(tree) if isinstance(node, ast.Assign)
                     for t in node.targets
                     if isinstance(t, ast.Name)
                     and isinstance(node.value, ast.Call)
                     and _call_name(node.value.func) == 'Requires'}
            assert names, (
                f'{rel} matches {_REQUIRES_CALL.pattern} but no module-level `NAME = Requires(...)` '
                f'assignment was found. A declaration reached by any other route (a factory, an '
                f'alias, a dict entry) is invisible to this sweep and therefore never validated.')
            found |= {(rel, name) for name in names}
    return found


def _call_name(func: ast.expr) -> str:
    """The bare callable name of a call target: `Requires` for both `Requires(...)` and
    `_compat.Requires(...)`, so a consumer's import style does not change what is discovered."""
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', '')


def _functions_selecting(relpath: str, table: str) -> set:
    """Every function in `relpath` holding a SELECT literal that names `table`.

    Read from the SOURCE with `ast` rather than by importing: a structural sweep cannot share a
    bug with the machinery it checks, and it needs no edit when a loader is added — only when one
    is added WITHOUT a decision about the vintages that lack the table.
    """
    with open(os.path.join(_ROOT, *relpath.split('/')), encoding='utf-8') as fh:
        tree = ast.parse(fh.read(), filename=relpath)
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for lit in ast.walk(node):
            if (isinstance(lit, ast.Constant) and isinstance(lit.value, str)
                    and 'SELECT' in lit.value.upper() and table in lit.value):
                out.add(node.name)
    return out


@contextlib.contextmanager
def _open(path: str):
    """A throwaway connection, closed on the way out.

    Closed explicitly rather than left to the collector because an open handle blocks `tmp_path`
    cleanup on Windows — and because `Schema.connect.read_only` would create `-wal`/`-shm`
    sidecars, which is exactly the behaviour a probe test should not depend on.
    """
    con = sqlite3.connect(str(path))
    try:
        yield con
    finally:
        con.close()


def _capability_db(path, *, populated=(), empty=(), run_id: int = 1) -> str:
    """A tmp_path database carrying the named capability tables, with rows only in `populated`.

    The empty/populated split is the whole point of the probe: `has_rows` must not answer "yes" for
    a table that exists and holds nothing, because a consumer would then draw a panel out of it.
    """
    stmts = [f'CREATE TABLE "{t}" (run_id INTEGER, batch_id INTEGER)'
             for t in tuple(empty) + tuple(populated)]
    stmts += [f'INSERT INTO "{t}" (run_id, batch_id) VALUES ({run_id}, 0)' for t in populated]
    return _tiny_db(path, stmts)


def _store_snapshot(root) -> dict:
    """`{'<family>/<sid>.json': bytes}` for a whole shape store — a byte-level fingerprint."""
    out = {}
    for family in sorted(os.listdir(root)):
        d = os.path.join(str(root), family)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            with open(os.path.join(d, fn), 'rb') as fh:
                out[f'{family}/{fn}'] = fh.read()
    return out


# ── the completeness sweep: what a loader READS, recovered from its own source ────
# Everything below reads `COMPLETENESS_TARGET` with `ast` and never imports it for this purpose —
# same reasoning as `_columns_by_table` and `_functions_selecting`: a structural sweep that reuses
# the machinery it is checking cannot catch a bug in it, and one that ran the loaders would need a
# database of every vintage to say anything at all.

_SELECT_FROM = re.compile(r'\bSELECT\b\s+(?P<cols>.+?)\s+\bFROM\b\s+(?P<table>[A-Za-z_]\w*)',
                          re.IGNORECASE | re.DOTALL)
_BARE_NAME = re.compile(r'^[A-Za-z_]\w*$')


def _source_tree(relpath: str) -> ast.Module:
    with open(os.path.join(_ROOT, *relpath.split('/')), encoding='utf-8') as fh:
        return ast.parse(fh.read(), filename=relpath)


def _sql_literals(node) -> list:
    """Every string constant under `node` that holds a `SELECT ... FROM <table>`.

    Adjacent string literals are concatenated by the PARSER, so a query split across source lines
    arrives here as one constant — which is why the column list and its table are still together.
    """
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and _SELECT_FROM.search(n.value)]


def _single_assignments(nodes) -> dict:
    """`{name: value_node}` for names bound EXACTLY ONCE by a plain `NAME = ...`.

    A name assigned twice is dropped rather than guessed at: resolving it to one of its two values
    would be a fabrication, and the sweep counts what it cannot resolve instead.
    """
    count, value = {}, {}
    for node in nodes:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            name = node.targets[0].id
            count[name] = count.get(name, 0) + 1
            value[name] = node.value
    return {n: v for n, v in value.items() if count[n] == 1}


def _comprehension_bindings(func) -> dict:
    """`{loop_var: iterable_node}` for every comprehension target in `func`.

    `{k: row[k] for k in wanted if k in row.keys()}` is the shape `run_identity` uses, and the
    columns it reads are the ELEMENTS of `wanted` — so binding the target to its iterable makes the
    same resolver answer both cases.
    """
    out = {}
    for node in ast.walk(func):
        for gen in getattr(node, 'generators', ()):
            if isinstance(gen.target, ast.Name):
                out[gen.target.id] = gen.iter
    return out


def _resolve_strings(node, scope: dict, seen: tuple = ()) -> frozenset | None:
    """The set of string literals `node` can stand for, or None when that is not decidable.

    Names are followed through `scope`, so `wanted = (...literals...) + _IDENTITY_COLS` resolves —
    and that matters: `run_identity` reaches thirteen `simulation_runs` columns exactly that way,
    and ten of them went undeclared for months precisely because nothing followed the name.
    """
    if isinstance(node, ast.Constant):
        return frozenset({node.value}) if isinstance(node.value, str) else None
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        parts = [_resolve_strings(e, scope, seen) for e in node.elts]
    elif isinstance(node, ast.Dict):
        parts = [_resolve_strings(k, scope, seen) for k in node.keys]
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts = [_resolve_strings(node.left, scope, seen),
                 _resolve_strings(node.right, scope, seen)]
    elif isinstance(node, ast.Name):
        if node.id in seen or node.id not in scope:      # a parameter, or a cycle
            return None
        return _resolve_strings(scope[node.id], scope, seen + (node.id,))
    else:
        return None
    return (frozenset().union(*parts)
            if parts and all(p is not None for p in parts) else None)


def _is_keys_call(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'keys')


def _keys_aliases(func) -> set:
    """Local names holding a `row.keys()` result — `keys = set(row.keys())` and friends.

    Without this, only the inline `'col' in row.keys()` form is visible, and the hoisted form
    (which is what `run_identity` and `load_picker_events` both use) would sweep as nothing.
    """
    return {node.targets[0].id
            for node in ast.walk(func)
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and any(_is_keys_call(c) for c in ast.walk(node.value))}


def _guard_records(func, scope: dict, aliases: set) -> list:
    """`(lineno, source_text, columns|None)` for every `<x> in <row.keys()>` test in `func`."""
    out = []
    for node in ast.walk(func):
        if not (isinstance(node, ast.Compare) and len(node.ops) == 1
                and isinstance(node.ops[0], ast.In)):
            continue
        probe = node.comparators[0]
        if not (_is_keys_call(probe) or (isinstance(probe, ast.Name) and probe.id in aliases)):
            continue                                     # an unrelated membership test
        out.append((node.lineno, ast.unparse(node), _resolve_strings(node.left, scope)))
    return out


def _sweep_reads(relpath: str = COMPLETENESS_TARGET) -> dict:
    """What every top-level function in `relpath` reads, as far as it can be attributed.

    `{function: {'tables', 'selected', 'guarded', 'unattributed'}}` where `selected`/`guarded` are
    `{table: {column, ...}}`.  A guarded read is attributed only when the function's SELECTs name
    exactly ONE table; anything else — an unresolvable column expression, an aggregate or aliased
    SELECT term, a guard in a function that touches two tables — lands in `unattributed` and is
    counted, never dropped.
    """
    tree = _source_tree(relpath)
    module_scope = _single_assignments(tree.body)
    out = {}
    for func in [n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        scope = dict(module_scope)
        scope.update(_single_assignments(list(ast.walk(func))))       # locals shadow module names
        scope.update(_comprehension_bindings(func))
        tables, selected, guarded, unattributed = set(), {}, {}, []

        for lit in _sql_literals(func):
            for m in _SELECT_FROM.finditer(lit.value):
                table = m.group('table')
                tables.add(table)
                terms = [t.strip() for t in m.group('cols').split(',')]
                if terms == ['*']:                       # a `SELECT *` names no column at all
                    continue
                for term in terms:
                    if _BARE_NAME.match(term):
                        selected.setdefault(table, set()).add(term)
                    else:                                # `COUNT(*)`, `am.batch_id`, an alias …
                        unattributed.append(
                            (func.name, lit.lineno, f'SELECT term {term!r} from {table}'))

        for lineno, text, columns in _guard_records(func, scope, _keys_aliases(func)):
            if columns is None or len(tables) != 1:
                unattributed.append((func.name, lineno, text))
            else:
                guarded.setdefault(next(iter(tables)), set()).update(columns)

        if tables or unattributed:
            out[func.name] = {'tables': tables, 'selected': selected, 'guarded': guarded,
                              'unattributed': unattributed}
    return out


def _classify(table: str, column: str) -> str:
    """`'negotiated' | 'declared' | 'legacy-alias' | 'undeclared'` for one read."""
    if table in _NEGOTIATED_TABLES or (table, column) in _NEGOTIATED_COLUMNS:
        return 'negotiated'                              # probed for, with a recorded decision
    if column in set(Picking_Data.REQUIRES.tables.get(table, ())):
        return 'declared'
    if (table, column) in LEGACY_COLUMN_ALIASES:
        return 'legacy-alias'
    return 'undeclared'


# ── the committed store must be COMPLETE ─────────────────────────────────────────
# Everything else in this file computes an intersection.  An intersection over an incomplete set
# of shapes is not a smaller answer, it is a wrong one — larger than the truth, and therefore
# permissive in exactly the direction that turns this layer into a rubber stamp.

@pytest.mark.parametrize('name', sorted(EXPECTED_FAMILIES))
def test_every_vetted_id_has_a_recoverable_shape(name):
    """THE gate. A `known_ids` entry with no committed document makes the surface a guess.

    A family's DECLARED shape is rebuilt from the writer's own DDL whenever it is asked for, so it
    is never at risk. A historical one is: `Picking_Data.PRE_STAMP_SIM_SCHEMA_ID` is frozen
    precisely because the source that produced it is gone, and no file of that vintage can be
    re-derived from today's tree. Vetting such an id without capturing its shape means every
    surface computed afterwards silently omits it — and omitting a shape can only ADD columns to
    the intersection, which blesses reads that vintage cannot serve.

    Fix by capturing (`python scripts/schema_report.py --capture <family> <a file of that vintage>`) or by
    dropping the id from the family if no such file survives. Do not "fix" it by making the
    surface non-strict.
    """
    missing = compat.unrecoverable_ids(name)
    assert not missing, (
        f'{name}: vetted id(s) {", ".join(missing)} have no committed shape document under '
        f'Schema/shapes/{name}/. Until one exists, guaranteed_surface({name!r}) cannot be '
        f'computed and every consumer that validates against it is validating against a guess. '
        f'Capture it with `python scripts/schema_report.py --capture {name} <file>`, or drop the id.')


def test_the_gate_sweeps_every_registered_family():
    """The sweep above is a literal roster, so a NEW family would be guarded by nothing at all.

    `EXPECTED_FAMILIES` is what the identity gate expects to EXIST; this asserts the reverse
    direction — that nothing is registered which the compatibility sweep never visits. A family
    registered but unswept has all the appearance of coverage and none of it.
    """
    unswept = sorted(set(identity.families()) - EXPECTED_FAMILIES)
    assert not unswept, (
        f'registered but never swept for compatibility: {unswept}. Add them to '
        f'`test_schema_identity.EXPECTED_FAMILIES` so every gate in both files visits them.')


def test_the_report_cli_imports_every_family_it_reports_on():
    """`FAMILY_MODULES` is a hand-kept list, and what it misses, `--report` never mentions.

    In-process this module's own imports register everything, so the sweeps above pass no matter
    what the CLI imports. The CLI has no such help: a family whose module is absent from that list
    is simply not in the registry when `--report` walks it, so the report prints nothing about it
    and still exits 0 — the same silence this whole package exists to remove.

    The list lives in `scripts/schema_report.py`, NOT in `Schema/` — see the boundary test below.
    """
    code = ('import scripts.schema_report as sr, Schema.identity as identity; '
            'sr.import_families(); print(",".join(identity.families()))')
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, cwd=_ROOT)
    assert r.returncode == 0, f'import_families() raised:\n{r.stdout}{r.stderr}'

    registered = {n for n in r.stdout.strip().split(',') if n}
    missing = sorted(EXPECTED_FAMILIES - registered)
    assert not missing, (
        f'`python scripts/schema_report.py --report` would not mention {missing}: no module in '
        f'`schema_report.FAMILY_MODULES` registers them, so they are invisible to the CLI even '
        f'though the in-process suite sees them.')


def test_the_schema_package_imports_nothing_above_its_own_layer():
    """`Schema/` is the stdlib-only leaf every layer may import. Keep it that way, statically.

    `context/architecture.yml` forbids `schema` from importing warehouse_core, generation,
    optimization, opt_persistence, evaluations and visualization — but `context/arch/extract.py`
    only visits `ast.Import`/`ast.ImportFrom`, so a dynamic `importlib.import_module("...")` on a
    string literal crosses a boundary while the gate still reports OK. That happened here: the
    family-import list lived in `Schema/compat.py` and pulled ~1350 modules — matplotlib, pandas,
    scipy — into the leaf, invisibly, and created a real `Picking_Data -> compat -> Picking_Data`
    cycle. This asserts what the extractor structurally cannot see.
    """
    offenders = {}
    schema_dir = os.path.join(_ROOT, 'Schema')
    for fn in sorted(os.listdir(schema_dir)):
        if not fn.endswith('.py'):
            continue
        rel = f'Schema/{fn}'
        with open(os.path.join(schema_dir, fn), encoding='utf-8') as fh:
            tree = ast.parse(fh.read(), filename=rel)
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or '']
            elif isinstance(node, ast.Call) and _call_name(node.func) in {'import_module',
                                                                         '__import__'}:
                mods = ([a.value for a in node.args
                         if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                        or ['<dynamic, non-literal>'])
            for m in mods:
                if m.split('.')[0] in {'Optimization', 'Warehouse', 'Visualization',
                                       'Diagnostics'}:
                    offenders.setdefault(rel, []).append(f'{m} (line {node.lineno})')

    assert not offenders, (
        f'Schema/ imports above its own layer: {offenders}. `Schema/` is the stdlib-only leaf; a '
        f'module that needs the writers belongs in scripts/ (see scripts/schema_report.py). Note '
        f'that a dynamic import PASSES verify_architecture.py, which is why this test exists.')


# ── the committed store must be TRUSTWORTHY ──────────────────────────────────────

def test_the_store_holds_exactly_the_vetted_ids():
    """One document per vetted id — DECLARED included — and no orphans.

    Both directions matter, and they fail for opposite reasons.

    **Uncaptured** is the gate above: a vetted id with no shape makes the guaranteed surface a
    guess, and a guess taken over the shapes we happen to hold is always too LARGE.

    **Orphaned** — a document for an id no family vets any more — is not a mess to tidy. It is the
    OUTGOING shape of a DDL change that has not been adopted yet, which is precisely what
    `scripts/schema_report.py --adopt` reports and why it exits 1. Left unadopted, every file
    written with that shape stops being readable.

    The declared shape is committed too, even though it is always rebuildable from the writer's
    DDL. That is the whole mechanism behind `--sync`: capturing it WHILE IT IS CURRENT is what
    means the next DDL change cannot lose it. The first version of this store held only historical
    shapes, and recovering one then required finding a real file of that exact vintage —
    `sim_db/2b7913bcd7e6` had none, and had to be reconstructed and hash-verified.

    This also fixes the count of the parametrized hash check below. An empty store would collect
    ZERO of those tests and report success, the exact failure mode `CLAUDE.md` calls out.
    """
    committed = _committed_documents()
    vetted = {(name, sid) for name in sorted(EXPECTED_FAMILIES)
              for sid in identity.get(name).supported_ids()}
    assert committed == vetted, (
        f'shape store out of step with the registry — uncaptured: '
        f'{sorted(vetted - committed)} (run `python scripts/schema_report.py --sync`); '
        f'orphaned: {sorted(committed - vetted)} (these are OUTGOING shapes a DDL change left '
        f'behind — run `python scripts/schema_report.py --adopt` and add them to `known_ids`).')


@pytest.mark.parametrize('family, sid', sorted(_committed_documents()))
def test_a_committed_document_hashes_to_its_own_filename(family, sid):
    """A shape store is only evidence if it is self-verifying.

    The filename IS the hash of the content, so a document that does not hash to its own name has
    been hand-edited, half-merged, or captured from the wrong file — and it would be believed
    silently, changing the intersection every consumer is validated against.
    """
    doc_shape = compat.load_shape(family, sid)        # raises UnrecoverableShape if it disagrees
    assert doc_shape is not None, f'{family}/{sid}.json vanished between collection and the test'
    assert shape.shape_id(doc_shape) == sid, (
        f'{family}/{sid}.json holds a shape that hashes to {shape.shape_id(doc_shape)}')
    assert doc_shape['tables'], f'{family}/{sid}.json declares no tables'

    with open(compat.shape_path(family, sid), encoding='utf-8') as fh:
        doc = json.load(fh)
    assert doc['family'] == family, f'{family}/{sid}.json says family={doc["family"]!r}'
    assert doc['shape_id'] == sid, f'{family}/{sid}.json says shape_id={doc["shape_id"]!r}'
    assert doc.get('captured_from'), (
        f'{family}/{sid}.json records no provenance; a shape with no story is a guess someone '
        f'will have to re-derive from an archive that may no longer exist.')


def test_a_corrupted_document_is_refused_rather_than_believed(tmp_path, monkeypatch):
    """Committed data gets hand-edited; the load has to notice. Uses a throwaway store only.

    Deleting a column from a historical shape makes the intersection LARGER, which is the one
    direction that silently converts a compatibility gate into a rubber stamp: the consumer
    validates clean and still reads nothing on that vintage.
    """
    monkeypatch.setattr(compat, 'SHAPES_DIR', str(tmp_path))
    shp = _probe_shape()
    sid = shape.shape_id(shp)
    path = compat.write_shape(_PROBE_FAMILY, sid, shp, captured_from='comparison_probe')

    # NON-VACUITY: the store must round-trip before its refusal proves anything.
    assert compat.load_shape(_PROBE_FAMILY, sid) == shp, 'a freshly written document must load'
    assert compat.load_shape(_PROBE_FAMILY, 'ffffffffffff') is None, (
        'an id that was never captured reads as None — absence is not corruption')

    with open(path, encoding='utf-8') as fh:
        doc = json.load(fh)
    removed = doc['shape']['tables']['probe']['columns'].pop()      # a hand-edit; filename kept
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, indent=2)

    with pytest.raises(compat.UnrecoverableShape, match=sid) as exc:
        compat.load_shape(_PROBE_FAMILY, sid)
    text = str(exc.value)
    assert f'{sid}.json' in text, f'the message must name the offending document: {text}'
    assert 'corrupt' in text, (
        f'dropping column {removed["name"]!r} must be reported as corruption, not as a shape: '
        f'{text}')


def test_write_shape_refuses_a_document_that_could_never_load(tmp_path, monkeypatch):
    """The filename is the hash, so a mismatched pair would poison the store at the source."""
    monkeypatch.setattr(compat, 'SHAPES_DIR', str(tmp_path))
    with pytest.raises(ValueError, match='hashes to'):
        compat.write_shape(_PROBE_FAMILY, _UNCAPTURED, _probe_shape(),
                           captured_from='comparison_probe')
    assert not os.path.exists(compat.shape_path(_PROBE_FAMILY, _UNCAPTURED)), (
        'a rejected capture must leave nothing behind — a half-written document is indistinguish'
        'able from a corrupt one')


# ── the surfaces ─────────────────────────────────────────────────────────────────

def test_at_least_one_family_really_has_several_shapes():
    """Non-vacuity for everything below: an intersection over ONE shape proves nothing.

    Every surface property here is trivially true for a family with a single vetted id. If the
    multi-vintage families ever collapse to one shape each, the tests below keep passing while
    testing nothing — so the fact that they do not is asserted, not assumed.
    """
    counts = {n: len(compat.known_shapes(n)) for n in sorted(EXPECTED_FAMILIES)}
    thin = sorted(n for n in MULTI_VINTAGE if counts[n] < 2)
    assert not thin, (
        f'{thin} no longer vet more than one shape ({counts}), so the intersection tests below '
        f'became vacuous. If an id was deliberately retired, update MULTI_VINTAGE and say why.')
    assert counts['sim_db'] >= 3, (
        f'sim_db is the family this package was written for — three whole tables and a column '
        f'apart across its vintages — and it now vets only {counts["sim_db"]} shape(s).')


@pytest.mark.parametrize('name', sorted(EXPECTED_FAMILIES))
def test_the_guaranteed_surface_is_present_in_every_vetted_shape(name):
    """The defining property: "guaranteed" means every vetted vintage can serve it.

    If the surface named one table or column that a single vintage lacks, a consumer could
    `validate()` clean and still read nothing at all from a file the family happily vets — which
    is precisely the accident `Schema/compat.py` was written to remove.
    """
    guaranteed = compat.guaranteed_surface(name)
    shapes = compat.known_shapes(name)
    assert shapes, f'{name} has no recoverable shape at all'
    assert guaranteed, f'{name}: the guaranteed surface is empty, so this test asserts nothing'

    for sid, shp in sorted(shapes.items()):
        present = _columns_by_table(shp)
        for table, wanted in guaranteed.items():
            assert table in present, (
                f'{name}: guaranteed_surface promises table {table!r}, but vetted shape {sid} '
                f'has only {sorted(present)}')
            absent = sorted(wanted - present[table])
            assert not absent, (
                f'{name}: guaranteed_surface promises {table}.{{{", ".join(absent)}}}, absent '
                f'from vetted shape {sid} — the intersection is not an intersection.')


@pytest.mark.parametrize('name', sorted(EXPECTED_FAMILIES))
def test_the_two_surfaces_partition_the_union_of_every_vetted_shape(name):
    """Guaranteed and conditional must be disjoint AND exhaustive.

    Overlap would let a column be read unconditionally and probed for at the same time — two
    answers to one question. A gap is worse: a column present in some vintage but in NEITHER
    surface is invisible to `validate()`, so a consumer reading it gets no warning from the static
    check and no probe at runtime. Nothing may fall between the two.
    """
    guaranteed = compat.guaranteed_surface(name)
    conditional = compat.conditional_surface(name)

    union: dict = {}
    for shp in compat.known_shapes(name).values():
        for table, cols in _columns_by_table(shp).items():
            union[table] = union.get(table, set()) | cols
    assert union, f'{name}: no vetted shape declares any table'

    for table, cols in conditional.items():
        overlap = sorted(cols & guaranteed.get(table, frozenset()))
        assert not overlap, (
            f'{name}.{table}: {overlap} is reported as BOTH guaranteed and conditional — a '
            f'consumer cannot both rely on it and have to probe for it.')

    for table, cols in sorted(union.items()):
        covered = set(guaranteed.get(table, frozenset())) | set(conditional.get(table,
                                                                                frozenset()))
        assert cols == covered, (
            f'{name}.{table}: columns in a vetted shape but in neither surface: '
            f'{sorted(cols - covered)}; columns in a surface but in no vetted shape: '
            f'{sorted(covered - cols)}')


def test_an_uncaptured_vetted_id_refuses_to_produce_a_surface(tmp_path, monkeypatch):
    """`strict` is the difference between "we checked" and "we checked what we happened to have".

    The permissive answer is not conservative — it is the OPTIMISTIC one, because omitting a shape
    can only add columns to an intersection. So the strict path must raise, and must name the id
    that has to be captured; a caller told only "cannot compute" learns nothing actionable.

    Built on a synthetic family over an empty store: the real registry is restored by monkeypatch,
    and the real committed store is never opened.
    """
    _register_probe(monkeypatch, tmp_path, known_ids=(_UNCAPTURED,))
    assert compat.unrecoverable_ids(_PROBE_FAMILY) == (_UNCAPTURED,), (
        'precondition: the synthetic family must vet exactly one uncaptured id')

    with pytest.raises(compat.UnrecoverableShape, match=_UNCAPTURED) as exc:
        compat.guaranteed_surface(_PROBE_FAMILY)
    assert '--capture' in str(exc.value), (
        f'the message must say how to fix it, not just that it failed: {exc.value}')

    with pytest.raises(compat.UnrecoverableShape, match=_UNCAPTURED):
        compat.conditional_surface(_PROBE_FAMILY)

    # NON-VACUITY: it is `strict` doing the work, not a function that always raises — and the
    # lenient answer really is the over-optimistic one (every declared column, none withheld).
    lenient = compat.guaranteed_surface(_PROBE_FAMILY, strict=False)
    assert lenient == {'probe': frozenset({'id', 'kept', 'dropped'})}, (
        f'strict=False must still compute the (larger, unsafe) surface: {lenient}')


def test_capturing_the_missing_shape_shrinks_the_surface_to_the_intersection(tmp_path,
                                                                             monkeypatch):
    """The arithmetic on values a reader can check by eye, end to end through the store.

    Two vintages, one column apart: capture the historical one and the guaranteed surface must
    LOSE the column the old shape never had, while the conditional surface gains exactly it. This
    is the whole mechanism the sim_db numbers rest on, at a scale small enough to verify by hand.
    """
    old = _probe_shape(('id INTEGER', 'kept TEXT'))          # the vintage without `dropped`
    old_id = shape.shape_id(old)
    _register_probe(monkeypatch, tmp_path, known_ids=(old_id,))

    assert compat.unrecoverable_ids(_PROBE_FAMILY) == (old_id,), 'precondition: not yet captured'
    compat.write_shape(_PROBE_FAMILY, old_id, old, captured_from='comparison_probe')
    assert compat.unrecoverable_ids(_PROBE_FAMILY) == (), 'capturing must clear the gate'

    assert compat.guaranteed_surface(_PROBE_FAMILY) == {'probe': frozenset({'id', 'kept'})}, (
        f'the intersection must drop a column the older vintage lacks: '
        f'{compat.guaranteed_surface(_PROBE_FAMILY)}')
    assert compat.conditional_surface(_PROBE_FAMILY) == {'probe': frozenset({'dropped'})}, (
        f'and the union-minus-intersection must be exactly that column: '
        f'{compat.conditional_surface(_PROBE_FAMILY)}')


# ── what a consumer declares ─────────────────────────────────────────────────────

def test_missing_from_names_the_absent_table_and_the_absent_column():
    """"Something is wrong with sim_db" costs an afternoon; naming the column costs a minute.

    The clause text is the entire product of this layer — it is what reaches a human through
    `validate()`, `check_requirements()` and every message built from them — so it is asserted
    verbatim rather than merely being non-empty.
    """
    surface = {'picks': frozenset({'run_id', 'sku'}), 'batch_stats': frozenset({'run_id'})}
    req = compat.Requires(family='sim_db', label='probe consumer', tables={
        'picks': ('run_id',),                       # satisfied
        'batch_stats': ('run_id', 'sigma_fd'),      # one column short
        'bin_placement': ('sku',),                  # whole table absent
    })

    assert req.missing_from(surface) == [
        'batch_stats is missing column(s): sigma_fd',
        'table bin_placement is absent',
    ], req.missing_from(surface)

    # The other direction: a satisfiable requirement must report NOTHING, or the check is noise.
    ok = compat.Requires('sim_db', 'probe consumer', {'picks': ('run_id', 'sku')})
    assert ok.missing_from(surface) == [], ok.missing_from(surface)
    any_cols = compat.Requires('sim_db', 'probe consumer', {'picks': compat.ANY_COLUMNS})
    assert any_cols.missing_from(surface) == [], 'ANY_COLUMNS asks for the table, nothing more'


@pytest.mark.parametrize('tables, clause', [
    # A whole table only SOME vintages have.  `bin_placement` arrived 2026-08-13, so this read
    # returns nothing at all against the 2026-07-29 vintage — while `identity.check()` passes.
    ({'bin_placement': ('sku', 'cause')}, 'table bin_placement is absent'),
    # And the finer case: one column, added to a table every vintage has.
    ({'simulation_runs': ('run_id', 'sim_schema_id')},
     'simulation_runs is missing column(s): sim_schema_id'),
])
def test_validate_reports_a_read_only_some_vintages_can_serve(tables, clause):
    """Sensitivity, against the REAL sim_db surface — if this passed, `validate` is decorative.

    Both cases are the failure the module docstring describes: the read works perfectly against a
    file written by today's build, and silently returns nothing against a vetted archive vintage.
    A static check that cannot see that is worth nothing.
    """
    req = compat.Requires('sim_db', 'probe consumer', tables)
    assert compat.validate(req) == [clause], compat.validate(req)

    # NON-VACUITY: this must be a genuine VERSION split, not a typo'd name that would "fail" for
    # a boring reason — every column above exists in the shape the current writer produces.
    declared = _columns_by_table(identity.get('sim_db').declared_shape())
    for table, cols in tables.items():
        assert table in declared, f'{table} is not even in the current declaration'
        assert set(cols) <= declared[table], (
            f'{table}: {sorted(set(cols) - declared[table])} exists in no shape at all, so this '
            f'test would pass without any version split to detect')


def test_check_requirements_accepts_a_file_that_can_answer_the_consumer(tmp_path):
    """The runtime half, on a throwaway file: a database is judged by what it CONTAINS.

    Deliberately not the archive and deliberately not a vetted shape — `check_requirements` and
    `identity.check` answer different questions, and folding vetting into this one would make
    every consumer check twice and disagree with itself.
    """
    path = _tiny_db(tmp_path / 'tiny.db', ('CREATE TABLE picks (run_id INTEGER, sku INTEGER)',))
    req = compat.Requires('sim_db', 'probe consumer', {'picks': ('run_id', 'sku')})

    sid = compat.check_requirements(path, req)
    assert re.fullmatch(r'[0-9a-f]{%d}' % shape.SHORT_LEN, sid), (
        f'verify=True must return the observed schema id, got {sid!r}')
    assert sid not in identity.get('sim_db').supported_ids(), (
        f'{sid} is unvetted as a FILE and still serves this consumer — that separation is the '
        f'point of the function, so a vetting step added here has to fail this test.')
    assert compat.check_requirements(path, req, verify=False) == '', (
        'verify=False must skip the derivation, not silently do it anyway')


def test_check_requirements_names_the_column_the_file_cannot_serve(tmp_path):
    """A refusal that says only "unsupported" sends someone to the archive with a hex string.

    The message has to carry the consumer's own label, the file, the missing table AND the missing
    column — that is the whole improvement over `identity.check`'s whole-file answer.
    """
    path = _tiny_db(tmp_path / 'tiny.db', ('CREATE TABLE picks (run_id INTEGER, sku INTEGER)',))
    req = compat.Requires('sim_db', 'probe consumer',
                          {'picks': ('run_id', 'quantity'),
                           'bin_placement': compat.ANY_COLUMNS})

    with pytest.raises(compat.RequirementUnmet, match='quantity') as exc:
        compat.check_requirements(path, req)
    text = str(exc.value)
    assert 'probe consumer' in text, f'the message must name the consumer: {text}'
    assert 'tiny.db' in text, f'the message must name the file: {text}'
    assert 'picks is missing column(s): quantity' in text, text
    assert 'table bin_placement is absent' in text, (
        f'every gap must be reported, not just the first: {text}')


# ── the layer must stay WIRED ────────────────────────────────────────────────────
# A surface nothing is validated against is a report, not a guarantee.  Everything below exists
# so that a consumer cannot start reading the conditional surface unnoticed.

@pytest.mark.parametrize('key', sorted(DECLARED_CONSUMERS), ids=lambda k: f'{k[0]}::{k[1]}')
def test_a_declared_consumer_stays_inside_the_guaranteed_surface(key):
    """Both wired consumers must be version-free BY CONSTRUCTION, offline, with no archive.

    This is the check that turns "safe by accident" into "safe by design". Today nothing breaks
    when `Performance_Evaluations` opens a 2026-07-29 run, but only because no evaluation happens
    to read `bin_placement`; the first one that does returns silently empty and publishes it. If
    this fails, the fix is a runtime capability probe with a recorded caveat — NOT a wider
    surface.
    """
    relpath = '::'.join(key)
    req = DECLARED_CONSUMERS[key]
    assert req.tables, (
        f'{relpath} declares an empty Requires, which validates clean and proves nothing')
    assert req.family in EXPECTED_FAMILIES, (
        f'{relpath} declares the unregistered family {req.family!r}')

    gaps = compat.validate(req)
    assert not gaps, (
        f'{relpath} ({req.label}) reads outside the guaranteed surface of {req.family}: '
        f'{"; ".join(gaps)}. Every vetted vintage must serve every declared read — move the read '
        f'behind a runtime probe that degrades with a recorded caveat, or capture/retire the '
        f'vintage that lacks it.')


def test_every_consumer_that_declares_requirements_is_validated_here():
    """A `Requires` that nothing validates in CI is a comment with a dataclass around it."""
    declared = _requires_declaration_sites()
    assert declared == set(DECLARED_CONSUMERS), (
        f'unvalidated declarations: {sorted(declared - set(DECLARED_CONSUMERS))}; stale entries: '
        f'{sorted(set(DECLARED_CONSUMERS) - declared)}. Every production `Requires` belongs in '
        f'DECLARED_CONSUMERS so `validate()` runs against it on every suite.')


def test_every_loader_that_reads_a_conditional_table_is_declared():
    """`no such table: bin_placement`, months later on the archive — or a CI failure now.

    `Picking_Data.CONDITIONAL_READS` is kept as DATA precisely so this can be asserted. The
    loaders are found structurally (a SELECT naming a table that only some vetted vintages have),
    so adding one that negotiates properly needs no edit here; adding one WITHOUT a decision about
    the vintages that lack the table fails.
    """
    guaranteed = compat.guaranteed_surface('sim_db')
    conditional = [t for t in compat.conditional_surface('sim_db') if t not in guaranteed]
    assert conditional, (
        'sim_db no longer has a table-level conditional surface, so this test cannot detect an '
        'undeclared conditional reader. If the vintages converged, retire CONDITIONAL_READS.')

    found = set()
    for table in conditional:
        found |= _functions_selecting('Optimization/persistence/Picking_Data.py', table)
    declared = {fn for fn, target in Picking_Data.CONDITIONAL_READS.items() if '.' not in target}

    assert found == declared, (
        f'undeclared readers of a conditional table: {sorted(found - declared)}; declared but no '
        f'longer reading one: {sorted(declared - found)}. A loader that reads a table only some '
        f'vetted vintages have must be listed in `Picking_Data.CONDITIONAL_READS` with the '
        f'decision about callers that cannot negotiate.')


@pytest.mark.parametrize('reader, target', sorted(Picking_Data.CONDITIONAL_READS.items()))
def test_a_declared_conditional_read_is_still_conditional(reader, target):
    """The list must not rot in the other direction either.

    If a target becomes guaranteed — every vetted vintage grew the table, or the vintage that
    lacked it was retired — the entry is stale, and its reader is negotiating for something it
    could now simply read. Stale caveats are how a conditional read becomes permanent.
    """
    table, _, column = target.partition('.')
    guaranteed = compat.guaranteed_surface('sim_db')
    conditional = compat.conditional_surface('sim_db')

    if column:
        assert table in guaranteed, (
            f'{reader}: {table} is not guaranteed either, so {target} is mis-declared')
        assert column not in guaranteed[table], (
            f'{reader}: {target} is now in the guaranteed surface — every vetted vintage has it, '
            f'so the conditional handling can go.')
        assert column in conditional.get(table, frozenset()), (
            f'{reader}: {target} is in no vetted shape at all; the entry names a column that '
            f'does not exist.')
    else:
        assert table not in guaranteed, (
            f'{reader}: {table} is now in the guaranteed surface — every vetted vintage has it, '
            f'so the conditional handling can go.')
        assert table in conditional, (
            f'{reader}: {table} is in no vetted shape at all; the entry names a table that does '
            f'not exist.')


# ── negotiating for what is NOT guaranteed ───────────────────────────────────────
# `Schema/capability.py` is how the conditional surface is reached at all: probe, then degrade
# with a recorded caveat.  Everything here runs on throwaway `tmp_path` databases; the archived
# arms that motivated each rule are named in the docstrings, not opened.

def test_has_rows_separates_absent_from_empty_from_populated(tmp_path):
    """Three cases, and the MIDDLE one is why the probe exists.

    "The table is there" is not the question a consumer is asking — it is asking whether it can
    draw a panel. `aisle_metrics` and `reorder_queue` are in every vetted shape and are written
    only by strategies that maintain that state: measured across two archived what-if cells,
    `reorder_queue` carries rows in 68 of 166 arms. A probe that checked for the TABLE would report
    a capability on all 166, and the other 98 would render 0.0 as though it were a measurement.
    """
    path = _capability_db(tmp_path / 'probe.db', populated=('full_t',), empty=('empty_t',))
    with _open(path) as con:
        assert capability.has_rows(con, 'full_t') is True, (
            'a table with a row is the only case that may answer True')
        assert capability.has_rows(con, 'empty_t') is False, (
            'a table that EXISTS but holds no row must answer False — this is the whole reason '
            'the probe counts rows rather than asking sqlite_master, and the case that turns an '
            'unwritten table into a published 0.0')
        assert capability.has_rows(con, 'never_created_t') is False, (
            'a missing table answers False rather than raising: to a consumer deciding whether it '
            'can draw something, "the schema predates this" and "this arm wrote none" are one fact')


def test_has_rows_answers_per_run_not_per_file(tmp_path):
    """A resumed or interrupted arm leaves rows for one run and none for the next.

    Without the filter the probe would select a source that folds to nothing for the run actually
    being replayed — the same silent zero, arrived at from the other direction.
    """
    path = _capability_db(tmp_path / 'runs.db', populated=('aisle_metrics',), run_id=2)
    with _open(path) as con:
        assert capability.has_rows(con, 'aisle_metrics') is True, (
            'precondition: the table does carry a row when nothing is filtered')
        assert capability.has_rows(con, 'aisle_metrics', run_id=2) is True, (
            'the run that wrote the rows must still see them')
        assert capability.has_rows(con, 'aisle_metrics', run_id=1) is False, (
            'run 1 wrote nothing here, so the capability is NOT available to run 1 however full '
            'the file looks')
        assert capability.has_rows(con, 'no_such_table', run_id=2) is False, (
            'the filtered path must swallow a missing table exactly like the unfiltered one')


def test_probe_reports_only_what_a_row_proves_and_honours_extra(tmp_path):
    """The registry sweep, against the real `SIM_CAPABILITIES` and a file with one live table.

    `table=None` marks a capability no row can settle — a sibling keyframe DB, a fresh derived
    cache. Those are the CALLER's evidence, supplied through `extra=`, and the probe must neither
    invent them nor drop them; the negotiation then stays uniform even when the evidence is not a
    table.
    """
    caps = Picking_Data.SIM_CAPABILITIES
    path = _capability_db(tmp_path / 'sim.db', populated=('bin_placement',),
                          empty=('aisle_metrics',), run_id=7)
    with _open(path) as con:
        found = capability.probe(con, caps.values(), run_id=7)
        assert found == {Picking_Data.CAP_BIN_LOG}, (
            f'only the populated table may be reported: {sorted(found)} — `aisle_metrics` exists '
            f'and is empty, and the keyframe/viz-cache capabilities carry no table at all')

        with_extra = capability.probe(con, caps.values(), run_id=7,
                                      extra=(Picking_Data.CAP_KEYFRAMES,))
        assert with_extra == {Picking_Data.CAP_BIN_LOG, Picking_Data.CAP_KEYFRAMES}, (
            f'`extra` must be carried through untouched: {sorted(with_extra)}')

    # NON-VACUITY: the capability `extra` supplied is genuinely unprobeable, so the assertion
    # above cannot have been satisfied by the row probe finding a `keyframes` table.
    assert caps[Picking_Data.CAP_KEYFRAMES].table is None, (
        'CAP_KEYFRAMES grew a table, so `extra=` is no longer the only way it can be reported '
        'and this test stopped covering the table=None path')


def test_best_follows_the_ladder_not_what_happens_to_be_available():
    """"Best" is a question about what is being ASKED, so the order is the answer.

    The case that matters is an arm where the exact source is missing and two approximate ones
    remain. `bin_inventory` is available, later in the ladder, and would be WRONG to pick: it
    records picks and never restocks, so rolling its deltas forward can only decay occupancy —
    68,271 occupied bins against a true 165,519 five batches past a keyframe. A "use whatever is
    available" implementation has no way to express that.
    """
    ladder = Picking_Data.OCCUPANCY_LADDER
    assert len(ladder) >= 3, f'the occupancy ladder collapsed to {len(ladder)} source(s)'

    available = frozenset({Picking_Data.CAP_AISLE_METRICS, Picking_Data.CAP_BIN_INVENTORY})
    chosen = capability.best(available, ladder)
    assert chosen.name == Picking_Data.CAP_AISLE_METRICS, (
        f'with the exact source absent the ladder must fall to the start-of-batch counter, not to '
        f'the biased delta roll: got {chosen.name!r}')

    passed_over = Picking_Data.SIM_CAPABILITIES[Picking_Data.CAP_BIN_INVENTORY]
    assert not passed_over.exact and 'BIASED' in passed_over.caveat, (
        f'the source that was passed over is supposed to be the recorded-as-wrong one; its caveat '
        f'now reads {passed_over.caveat!r}, so this test no longer demonstrates anything')

    # NON-VACUITY: it is the ORDER doing the work, not membership. Same available set, reversed
    # ladder, different answer — and an implementation that iterated `available` (a set) would
    # not agree with either.
    assert capability.best(available, tuple(reversed(ladder))).name == \
        Picking_Data.CAP_BIN_INVENTORY, 'reversing the ladder must reverse the choice'
    every = frozenset(c.name for c in ladder)
    assert capability.best(every, ladder).name == Picking_Data.CAP_BIN_LOG, (
        'with everything available the ladder must still pick its own first entry')
    assert sorted(every)[0] != Picking_Data.CAP_BIN_LOG, (
        'the ladder now happens to agree with alphabetical order, so the assertion above no '
        'longer distinguishes it from an arbitrary pick')
    assert capability.best(frozenset(), ladder) is None, 'nothing available -> no capability'


def test_require_names_every_source_it_tried():
    """A consumer that cannot degrade any further must fail LOUDLY, and say what it looked for.

    "No occupancy data" sends someone to the archive with nothing; naming the three tables that
    were tried says which arm they are looking at and why it cannot answer.
    """
    ladder = Picking_Data.OCCUPANCY_LADDER
    with pytest.raises(capability.NoSourceAvailable, match='occupancy at batch 40') as exc:
        capability.require(frozenset(), ladder, what='occupancy at batch 40')

    text = str(exc.value)
    for cap in ladder:
        assert cap.name in text, f'the message must name every source it tried ({cap.name}): {text}'

    # NON-VACUITY: with one source present it RETURNS rather than raising, so the raise above is
    # about availability and not about the function always failing.
    got = capability.require(frozenset({Picking_Data.CAP_BIN_INVENTORY}), ladder, what='occupancy')
    assert got.name == Picking_Data.CAP_BIN_INVENTORY, got


def test_an_inexact_capability_without_a_caveat_is_refused():
    """The invariant that stops a silent approximation: no caveat, no capability.

    An approximate source whose caveat was never written down is indistinguishable from a
    measurement by the time it reaches a figure. Refusing it at CONSTRUCTION is what makes
    `provenance()` worth attaching — the payload can never be empty for an inexact source.
    """
    with pytest.raises(ValueError, match='inexact but carries no caveat'):
        capability.Capability(name='probe_cap', table='probe', exact=False,
                              phase='end-of-batch', caveat='')

    # NON-VACUITY: the same construction is accepted the moment the caveat is there, and an EXACT
    # source is allowed to carry none — so it is the pairing being enforced, not `caveat` alone.
    with_caveat = capability.Capability(name='probe_cap', table='probe', exact=False,
                                        phase='end-of-batch', caveat='APPROXIMATE: sampled early.')
    assert with_caveat.caveat, 'an inexact capability WITH a caveat must construct'
    exact = capability.Capability(name='probe_cap', table='probe', exact=True,
                                  phase='static (per run)', caveat='')
    assert exact.exact and exact.caveat == '', 'an exact capability needs no caveat'


def test_the_capability_registry_is_not_thin():
    """Non-vacuity for both parametrized sweeps below, and for the `extra=` path above.

    Every property asserted per capability is trivially true of an empty registry, and a
    parametrize over an empty source collects zero tests and reports success.
    """
    caps = Picking_Data.SIM_CAPABILITIES
    assert len(caps) >= 8, f'SIM_CAPABILITIES has shrunk to {len(caps)}: {sorted(caps)}'
    assert all(name == cap.name for name, cap in caps.items()), (
        f'the registry must be keyed by each capability\'s own name: {sorted(caps)}')
    assert any(not c.exact for c in caps.values()), (
        'no capability is inexact any more, so the caveat sweep below asserts nothing')
    assert any(c.table is None for c in caps.values()), (
        'every capability is table-backed now, so the `extra=` path is no longer covered')
    assert set(Picking_Data.OCCUPANCY_LADDER) <= set(caps.values()), (
        'the occupancy ladder must be built FROM the registry, not beside it')


@pytest.mark.parametrize('name', sorted(Picking_Data.SIM_CAPABILITIES))
def test_a_sim_capability_names_a_table_some_vetted_shape_really_has(name):
    """A capability pointing at a table (or column) no vintage carries is a probe that never fires.

    It fails in the quietest possible way: `has_rows` swallows `no such table`, so the capability
    is simply never available, every consumer degrades forever, and nothing anywhere says why.
    """
    cap = Picking_Data.SIM_CAPABILITIES[name]
    assert cap.phase, f'{name} records no phase; a source with no instant cannot be compared'
    if not cap.exact:
        assert cap.caveat.strip(), (
            f'{name} is inexact and its caveat is blank — the constructor forbids that, so this '
            f'has been mutated after construction')
    if cap.table is None:
        assert not cap.columns, f'{name} has no table but names columns {cap.columns}'
        return

    shapes = compat.known_shapes('sim_db')
    holders = sorted(sid for sid, shp in shapes.items() if cap.table in shp['tables'])
    assert holders, (
        f'{name} probes for table {cap.table!r}, which is in none of the vetted sim_db shapes '
        f'{sorted(shapes)}. The probe can never succeed; either the table name is wrong or the '
        f'vintage that had it was retired without retiring the capability.')

    common = set.intersection(*[{c['name'] for c in shapes[sid]['tables'][cap.table]['columns']}
                                for sid in holders])
    absent = sorted(set(cap.columns) - common)
    assert not absent, (
        f'{name} names column(s) {absent} that {cap.table} does not carry in every shape that has '
        f'the table ({holders}) — a consumer selecting this capability would read them as absent.')


@pytest.mark.parametrize('name', sorted(Picking_Data.SIM_CAPABILITIES))
def test_provenance_is_json_serializable_payload(name):
    """The caveat has to be able to reach a figure caption, an API response and an export.

    `Diagnostics/replay_run.py` copies this dict verbatim into exported JSON; anything that needed
    a converter would be dropped by the first consumer that forgot one — and dropping the caveat
    leaves the number looking exactly like a measurement.
    """
    cap = Picking_Data.SIM_CAPABILITIES[name]
    payload = capability.provenance(cap)

    assert set(payload) == {'source', 'exact', 'phase', 'caveat'}, sorted(payload)
    assert json.loads(json.dumps(payload)) == payload, (
        f'{name}: provenance does not survive a JSON round trip: {payload}')
    assert payload['source'] == name and payload['phase'] == cap.phase, payload
    assert payload['exact'] is cap.exact, payload
    assert payload['caveat'] == cap.caveat, (
        f'{name}: the caveat must be carried VERBATIM, not summarised: {payload["caveat"]!r}')


# ── keeping the store complete: --sync / --adopt ─────────────────────────────────
# `--sync` commits the DECLARED shape while it is still current, which is what stops the NEXT DDL
# change from losing the outgoing one; `--adopt` names what a change already left behind.  Neither
# test writes into the real `Schema/shapes/`: the store is `monkeypatch`ed onto tmp_path, exactly
# as `_register_probe` does.

def test_sync_commits_every_declared_shape_and_then_writes_nothing(tmp_path, monkeypatch):
    """Idempotence is the property that makes "run it always" safe advice.

    If a second run rewrote the documents, `--sync` would churn the store on every invocation and
    nobody would run it before a DDL change — which is the one moment it has to have been run.
    Proved by spying on the WRITE, not by comparing bytes: `write_shape` stamps `captured` to the
    second, so two writes inside the same second would be byte-identical and prove nothing.
    """
    monkeypatch.setattr(compat, 'SHAPES_DIR', str(tmp_path))
    writes = []
    real_write = compat.write_shape
    monkeypatch.setattr(compat, 'write_shape',
                        lambda family, sid, shp, **kw: (writes.append(f'{family}/{sid}'),
                                                        real_write(family, sid, shp, **kw))[1])

    first: list = []
    assert schema_report.sync(out=first.append) == 0, first
    assert set(writes) == {f'{n}/{identity.get(n).declared_id()}' for n in EXPECTED_FAMILIES}, (
        f'the first sync must commit exactly one declared shape per family: {sorted(writes)}')

    # Every family's declared id now HAS a document, and it is the shape the writer's DDL builds.
    for name in sorted(EXPECTED_FAMILIES):
        fam = identity.get(name)
        assert compat.load_shape(name, fam.declared_id()) == fam.declared_shape(), (
            f'{name}: the committed declared shape is not what the writer declares')
    assert compat.unrecoverable_ids('keyframes_db') == (), 'a synced family must be recoverable'

    snapshot = _store_snapshot(tmp_path)
    assert len(snapshot) == len(EXPECTED_FAMILIES), sorted(snapshot)

    writes.clear()
    second: list = []
    assert schema_report.sync(out=second.append) == 0, second
    assert writes == [], f'the second sync rewrote {writes}; --sync must be idempotent'
    assert _store_snapshot(tmp_path) == snapshot, 'the store changed on a no-op sync'
    assert 'already current' in ' '.join(second), (
        f'a no-op sync must say so rather than reporting a count: {second}')


def test_adopt_finds_nothing_outstanding_in_the_committed_store():
    """The real store, read-only: a green `--adopt` is the claim every consumer rests on.

    An orphaned document is the OUTGOING shape of a DDL change that has not been adopted — and
    until its id is back in `known_ids`, every file already written with that shape is unreadable
    by `identity.check`. Exiting non-zero here is how a hook catches that on the commit that
    caused it rather than months later, on the archive.
    """
    lines: list = []
    rc = schema_report.adopt(out=lines.append)
    assert rc == 0, (
        'shapes are waiting to be adopted:\n' + '\n'.join(lines) +
        '\nAdd each id to its family\'s `known_ids` (entries are ADDED, never replaced).')
    assert 'nothing to adopt' in ' '.join(lines), f'expected the clean message, got {lines}'


def test_adopt_reports_the_outgoing_shape_a_ddl_change_left_behind(tmp_path, monkeypatch):
    """The failing direction, on a synthetic family — the real store is never touched.

    A committed document whose id no family vets any more IS the outgoing shape, and it names
    itself. The report has to carry that id and the `known_ids=` line to paste, because the whole
    point of `--sync`/`--adopt` is that adopting a shape stopped being archaeology.
    """
    fam = _register_probe(monkeypatch, tmp_path)              # declares the 3-column probe shape
    outgoing = _probe_shape(('id INTEGER', 'kept TEXT'))      # what the DDL looked like before
    outgoing_id = shape.shape_id(outgoing)
    compat.write_shape(_PROBE_FAMILY, outgoing_id, outgoing, captured_from='comparison_probe')

    # NON-VACUITY: the document really is committed, and the family really has moved past it.
    assert compat.load_shape(_PROBE_FAMILY, outgoing_id) == outgoing, 'the document must load'
    assert outgoing_id not in fam.supported_ids(), (
        f'{outgoing_id} is still vetted, so there is nothing to adopt and this test is vacuous')

    lines: list = []
    rc = schema_report.adopt(out=lines.append)
    text = '\n'.join(lines)

    assert rc == 1, f'an unadopted shape must exit non-zero so a hook can catch it:\n{text}'
    assert outgoing_id in text, f'the report must name the orphaned id: {text}'
    assert fam.declared_id() in text, f'and the id that replaced it: {text}'
    assert 'known_ids' in text, f'and the line to paste into the family: {text}'
    assert 'nothing to adopt' not in text, text
    assert not any(name in text for name in EXPECTED_FAMILIES), (
        f'only the synthetic family may be reported — the real store is monkeypatched away and '
        f'must not appear at all:\n{text}')


# ── the completeness gate: a declaration must name what its loaders READ ─────────
# `compat.validate()` proves a `Requires` stays INSIDE the guaranteed surface.  That is only half
# the question, and on its own it is the wrong half: an EMPTY declaration passes it perfectly.
# Nothing proved that a declaration names everything its loaders actually read, and two real
# under-declarations reached review through that gap — `reorder_queue.unit_type`/`storage_size`,
# named in a primary SELECT, and ten `simulation_runs` columns reached through `row.keys()`.
#
# The sweep reads `COMPLETENESS_TARGET`'s own source with `ast`.  Not by importing it: a structural
# check must not share a bug with the thing it checks, and a runtime check would need a database of
# every vintage before it could say anything at all.

def test_every_explicitly_selected_column_is_declared():
    """`SELECT a, b, c FROM t` — the loud half, and still worth catching statically.

    An explicit column list against a vintage that lacks one of them raises `OperationalError`,
    which is better than a silent zero and still arrives only after a long analysis has run. The
    columns `load_reorder_queue` asks for are exactly this case: `unit_type` and `storage_size`
    were in the SELECT and not in the declaration, so nothing in CI knew they were being read.
    """
    sweep = _sweep_reads()
    undeclared: dict = {}
    checked = set()
    for func, rec in sorted(sweep.items()):
        for table, columns in sorted(rec['selected'].items()):
            for column in sorted(columns):
                verdict = _classify(table, column)
                if verdict == 'undeclared':
                    undeclared.setdefault(f'{func} -> {table}', []).append(column)
                elif verdict == 'declared':
                    checked.add((func, table, column))

    assert not undeclared, (
        'columns read by a SELECT but absent from `Picking_Data.REQUIRES`: '
        + '; '.join(f'{where}: {", ".join(cols)}' for where, cols in sorted(undeclared.items()))
        + '. Add them to the declaration (and if `compat.validate` then fails, the read is '
          'genuinely not guaranteed and belongs behind a capability probe with a caveat).')

    # NON-VACUITY: name the reads this gate was written for. A sweep that found nothing would
    # pass the assertion above without ever looking at a column.
    assert len(checked) >= 12, (
        f'the SELECT sweep only attributed {len(checked)} declared column(s) across '
        f'{len(sweep)} function(s) — it has stopped finding the queries: {sorted(checked)}')
    for anchor in (('load_reorder_queue', 'reorder_queue', 'unit_type'),
                   ('load_reorder_queue', 'reorder_queue', 'storage_size'),
                   ('load_bin_scores', 'bin_scores', 'travel_d')):
        assert anchor in checked, (
            f'{anchor} is no longer swept, and it is one of the reads this gate exists for; '
            f'found: {sorted(checked)}')


def test_every_row_keys_guarded_column_is_declared():
    """The DANGEROUS half: a missing guarded column becomes 0.0 and gets published.

    `row['x'] if 'x' in row.keys() else 0.0` cannot raise. On a vintage without `x` the loader
    returns the dataclass default, `common/frames.py` carries it into a frame, and a figure comes
    out with a number nobody can tell is wrong — seven of `load_batch_stats`' guarded columns flow
    straight into a published figure or CSV. Declaring them turns that into a CI failure.

    `run_identity` is the reason the resolver follows NAMES: its columns come from
    `('run_id', ...) + _IDENTITY_COLS`, and ten of them went undeclared for months because nothing
    followed the concatenation.
    """
    sweep = _sweep_reads()
    undeclared: dict = {}
    checked = set()
    for func, rec in sorted(sweep.items()):
        for table, columns in sorted(rec['guarded'].items()):
            for column in sorted(columns):
                verdict = _classify(table, column)
                if verdict == 'undeclared':
                    undeclared.setdefault(f'{func} -> {table}', []).append(column)
                elif verdict == 'declared':
                    checked.add((func, table, column))

    assert not undeclared, (
        'columns read behind a `row.keys()` guard but absent from `Picking_Data.REQUIRES`: '
        + '; '.join(f'{where}: {", ".join(cols)}' for where, cols in sorted(undeclared.items()))
        + '. A guard means the failure is SILENT, not that it is safe: on a vintage without the '
          'column the loader returns its default and the value is published as a measurement.')

    assert len(checked) >= 24, (
        f'the guard sweep only attributed {len(checked)} declared column(s) — it has stopped '
        f'finding the `row.keys()` reads: {sorted(checked)}')
    for anchor in (('run_identity', 'simulation_runs', 'channel'),
                   ('run_identity', 'simulation_runs', 'warehouse_fingerprint'),
                   ('load_batch_stats', 'batch_stats', 'queue_depth'),
                   ('load_task_stats', 'task_stats', 'W')):
        assert anchor in checked, (
            f'{anchor} is no longer swept, and it is one of the reads this gate exists for; '
            f'found: {sorted(checked)}')


def test_every_table_a_loader_reads_is_declared_or_negotiated():
    """Table-level completeness, which also covers the `SELECT *` loaders.

    A `SELECT *` names no column, so the two sweeps above see nothing in it — but the TABLE is
    still a read, and a table in neither `REQUIRES` nor `CONDITIONAL_READS` is one nobody decided
    about. It is also where a cross-family read would show up: `REQUIRES` speaks for `sim_db`
    only, and a loader reaching into a keyframe sidecar's tables cannot be validated by it.
    """
    sweep = _sweep_reads()
    read = {(func, table) for func, rec in sweep.items() for table in rec['tables']}
    assert len(read) >= 10, f'the sweep found only {len(read)} loader/table pair(s): {sorted(read)}'

    undecided = sorted({t for _f, t in read
                        if t not in Picking_Data.REQUIRES.tables and t not in _NEGOTIATED_TABLES})
    assert not undecided, (
        f'tables read by a loader but in neither `REQUIRES` nor `CONDITIONAL_READS`: {undecided}. '
        f'Declare the table (every vetted vintage has it) or list it in CONDITIONAL_READS with '
        f'the decision about callers that cannot negotiate.')

    surface = compat.guaranteed_surface('sim_db')
    surface = set(surface) | set(compat.conditional_surface('sim_db'))
    foreign = sorted({t for _f, t in read if t not in surface})
    assert not foreign, (
        f'tables read here that no vetted sim_db shape has at all: {foreign}. `REQUIRES` speaks '
        f'for sim_db, so either the name is a typo or this loader reads another family\'s file '
        f'and needs its own declaration.')


def test_the_read_sweep_attributes_all_but_a_recorded_number_of_constructs():
    """The gate's own honesty check: a sweep that stops finding things reports success forever.

    Some constructs genuinely cannot be attributed to a table by reading the source, and skipping
    them is correct — pretending otherwise would mean inventing an answer. Counting them is what
    keeps the skip honest: if a refactor moved every loader behind a closure like
    `load_picker_events._g`, the two gates above would pass while checking nothing at all.
    """
    sweep = _sweep_reads()
    skipped = [rec for func in sorted(sweep) for rec in sweep[func]['unattributed']]
    assert len(skipped) <= MAX_UNATTRIBUTED_READS, (
        f'{len(skipped)} read construct(s) could not be attributed to a table, above the recorded '
        f'MAX_UNATTRIBUTED_READS={MAX_UNATTRIBUTED_READS}: '
        + '; '.join(f'{f}:{ln} {txt}' for f, ln, txt in skipped)
        + '. Each one is a column nobody is checking. Make the read static (a literal column name '
          'at the guard) or raise the constant and say in the commit what stopped being checked.')

    # NON-VACUITY, the other direction: no loader may escape the sweep by moving into a class or a
    # nested helper. Every SELECT literal in the file must be reachable from a TOP-LEVEL function,
    # because that is the only thing `_sweep_reads` walks.
    tree = _source_tree(COMPLETENESS_TARGET)
    everywhere = {id(n) for n in _sql_literals(tree)}
    swept = {id(n) for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             for n in _sql_literals(node)}
    assert everywhere and everywhere == swept, (
        f'{len(everywhere - swept)} SELECT literal(s) in {COMPLETENESS_TARGET} live outside a '
        f'top-level function (a method, a module-level constant), where `_sweep_reads` never '
        f'looks. Every loader must stay a module-level function or the sweep must learn to walk '
        f'classes.')


def test_a_legacy_column_alias_is_read_but_exists_in_no_vetted_shape():
    """The exemption must be unable to hide a live column — checked from BOTH ends.

    `LEGACY_COLUMN_ALIASES` lets `sigma_fw`/`W_a` past the gates above, and an exemption list is
    exactly how a real gap gets waved through. So each entry must be (a) absent from every vetted
    shape, which is what makes it undeclarable — `compat.validate` would reject it — and (b) still
    actually read, so a stale entry cannot sit there blessing a name that has come back into use.
    """
    assert LEGACY_COLUMN_ALIASES, 'the alias list is empty, so this test asserts nothing'
    union = {t: set(c) for t, c in compat.guaranteed_surface('sim_db').items()}
    for table, columns in compat.conditional_surface('sim_db').items():
        union[table] = union.get(table, set()) | set(columns)

    for table, column in sorted(LEGACY_COLUMN_ALIASES):
        assert column not in union.get(table, set()), (
            f'{table}.{column} IS present in a vetted shape, so it is not a legacy alias — it is '
            f'a real column being read while exempted from the completeness gate. Declare it in '
            f'`Picking_Data.REQUIRES` and drop the entry.')

    sweep = _sweep_reads()
    read = {(table, column)
            for rec in sweep.values()
            for source in ('selected', 'guarded')
            for table, columns in rec[source].items()
            for column in columns}
    stale = sorted(LEGACY_COLUMN_ALIASES - read)
    assert not stale, (
        f'{stale} is exempted but no loader reads it any more. Drop the entry: an exemption for a '
        f'read that no longer exists is a hole waiting for a column of the same name.')
