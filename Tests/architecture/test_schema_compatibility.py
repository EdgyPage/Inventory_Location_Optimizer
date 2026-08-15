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
    third consumer cannot appear without being checked here.

Everything runs offline from the committed shape store and `tmp_path` fixtures: no archive, no
results drive, no `.env`, no `COMPARISON_OUTPUT_DIR`.

    python -m pytest Tests/architecture/test_schema_compatibility.py -q
"""
from __future__ import annotations

import ast
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
from Schema import compat, identity, shape
from Visualization import cache_schema  # noqa: F401
from Warehouse.generation import generate_affinity, generate_inventory  # noqa: F401

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

def test_the_store_holds_exactly_the_historical_vetted_ids():
    """No uncaptured id, and no orphan document — and the non-vacuity guard for the sweep below.

    Both directions matter. A missing document is the gate above. An ORPHAN — a document for an id
    no family vets any more — is worse than useless: it looks like evidence, `--report` never
    prints it, and the next reader assumes the store is the list of supported vintages.

    This also fixes the count of the parametrized hash check below. An empty store would collect
    ZERO of those tests and report success, which is the exact failure mode `CLAUDE.md` calls out.
    """
    committed, historical = _committed_documents(), _historical_ids()
    assert committed == historical, (
        f'shape store out of step with the registry — uncaptured: '
        f'{sorted(historical - committed)}; orphaned documents: {sorted(committed - historical)}. '
        f'The store must hold one document per vetted HISTORICAL id and nothing else (the '
        f'declared shape is always rebuilt from the writer, so it is never committed).')


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
