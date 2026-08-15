"""store_index.py — the mutable head of the committed shape store, and its change trigger.

`Schema/shapes/<family>/<short>.json` documents are IMMUTABLE (filename = content hash).  Mutable
state — which declared id each family currently has, and a cheap trigger for "did a DDL source
change since the store was last synced" — needs somewhere else to live.  This is that somewhere:
``Schema/shapes/INDEX.json``, the exact division of labour ``Optimization/schemas/run_tree/``
uses (immutable documents + a mutable INDEX carrying `head` and `source_fingerprint`).

Two consumers, with opposite budgets:

  * ``scripts/schema_report.py --sync`` — the ONLY writer.  It has every family imported, so it
    can compute declared ids; it records them plus the fingerprint after committing shapes.
  * ``Schema/hook_check.py`` — a Stop hook that must NOT import the writers (importing them pulls
    ~1350 modules, matplotlib and pandas included).  It compares the fingerprint and stats the
    per-family documents, nothing more.  The fingerprint is a cheap TRIGGER, not proof: a
    docstring edit trips it, and the nag says "run --sync", which is idempotent and settles it.

WHY THE FINGERPRINT IS COPIED, NOT IMPORTED
-------------------------------------------
``Optimization/runschema/contract.source_fingerprint`` is the same ~15 lines, but importing it
here is `schema -> optimization` — forbidden by `context/architecture.yml`, and enforced by
`Tests/architecture/test_schema_compatibility.py::test_the_schema_package_imports_nothing_above_its_own_layer`.
Paths-as-strings are data, not imports (the precedent is `contract.SHAPE_SOURCES` itself, which
lists files across four layers).  A test keeps the copy honest by comparing the two algorithms on
identical input.
"""
from __future__ import annotations

import hashlib
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))

#: Where INDEX.json lives — beside the family directories it indexes.
INDEX_PATH = os.path.join(_HERE, 'shapes', 'INDEX.json')

#: Every source file whose edit can move a family's declared shape — the DB analog of
#: `contract.SHAPE_SOURCES`.  The six writer modules mirror `schema_report.FAMILY_MODULES`
#: (a test asserts the correspondence, so one cannot rot without the other), plus the two
#: Schema modules whose behaviour defines what a "shape" even is.
#:
#: A file is listed when editing it CAN mint a new declared id; over-listing costs one spurious
#: nag (the fix, --sync, is idempotent), under-listing costs the silent window this module
#: exists to close — so when in doubt, list it.
DDL_SOURCES = (
    'Optimization/persistence/Picking_Data.py',        # sim_db, keyframes_db
    'Optimization/persistence/Warehouse_Data.py',      # warehouse_db
    'Optimization/persistence/runtime_metrics.py',     # runtime_metrics_db
    'Visualization/cache_schema.py',                   # viz_cache_db
    'Warehouse/generation/generate_inventory.py',      # inventory_db
    'Warehouse/generation/generate_affinity.py',       # affinity_db
    'Schema/shape.py',                                 # what a canonical shape IS
    'Schema/identity.py',                              # what a family declares
)


def source_fingerprint(repo_root: str = _REPO_ROOT) -> str:
    """Hash of every DDL-defining source file (path + content), in declared order.

    Same algorithm as ``runschema.contract.source_fingerprint`` (copied — see the module
    docstring for why importing it is illegal): line endings normalised to \\n so a CRLF<->LF
    rewrite is not a structural change, and a missing file contributes a MISSING marker so a
    DELETED source is detected instead of silently matching.
    """
    h = hashlib.sha256()
    for rel in DDL_SOURCES:
        h.update(rel.encode('utf-8'))
        p = os.path.join(repo_root, rel.replace('/', os.sep))
        try:
            with open(p, 'rb') as f:
                h.update(f.read().replace(b'\r\n', b'\n'))
        except OSError:
            h.update(b'\x00MISSING')
    return 'sha256:' + h.hexdigest()


def read_index() -> dict | None:
    """The committed INDEX, or None when the store has never been synced."""
    if not os.path.isfile(INDEX_PATH):
        return None
    with open(INDEX_PATH, encoding='utf-8') as fh:
        return json.load(fh)


def write_index(families: dict) -> str:
    """Record `{family: declared_id}` plus the current fingerprint.  Atomic (tmp + os.replace).

    Only ``scripts/schema_report.py --sync`` calls this — it is the one place that has every
    family imported and every declared shape freshly committed.  Everything else treats INDEX as
    read-only evidence.
    """
    doc = {
        'source_fingerprint': source_fingerprint(),
        'families': dict(sorted(families.items())),
    }
    tmp = INDEX_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(doc, fh, indent=2, sort_keys=False)
        fh.write('\n')
    os.replace(tmp, INDEX_PATH)
    return INDEX_PATH


def stale_reasons() -> list[str]:
    """Cheap staleness findings for the hook — NO writer imports, file hashes and stats only.

    Returns human-readable reasons (empty = quiet).  Each names the remedy, which is always the
    same idempotent command; deciding whether the trip was a real shape change or a docstring
    edit is --sync's job, not the hook's.
    """
    idx = read_index()
    if idx is None:
        return ['Schema/shapes/INDEX.json does not exist - run: python scripts/schema_report.py --sync']
    out = []
    if idx.get('source_fingerprint') != source_fingerprint():
        out.append('a DDL-defining source changed since the shape store was last synced - '
                   'run: python scripts/schema_report.py --sync')
    for family, sid in (idx.get('families') or {}).items():
        doc = os.path.join(_HERE, 'shapes', family, f'{sid}.json')
        if not os.path.isfile(doc):
            out.append(f'{family}: indexed declared shape {sid} has no committed document - '
                       f'run: python scripts/schema_report.py --sync')
    return out
