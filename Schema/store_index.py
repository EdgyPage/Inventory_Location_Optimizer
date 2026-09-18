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

THE FINGERPRINT IS SHARED, NOT COPIED (it was a copy until `architecture-drift/05`)
----------------------------------------------------------------------------------
``Optimization/runschema/contract.source_fingerprint`` is the same ~15 lines, and importing IT
here would be `schema -> optimization` — forbidden by `context/architecture.yml` and enforced by
`Tests/architecture/test_schema_compatibility.py`.  The conclusion drawn from that was backwards:
the shared half lives in `Schema.fingerprint`, the stdlib-only leaf, and `runschema/contract.py`
imports DOWN.  Satisfied by DIRECTION rather than by copying.  The copy had already drifted --
`contract` grew an auto-discovered-directory half and neither copy did.

WHY THIS STORE IS NOT A `ContractStore` (ticket 13 expected it to become one)
-----------------------------------------------------------------------------
`Schema/contractstore.py` collapsed the run-tree and profiles-tree stores, and ticket 13 reads
this file as "the third adapter over the family-keyed variant".  It is not the same concept, and
a variant is exactly the wrong way to say so:

  * its documents live per FAMILY (`shapes/<family>/<short>.json`), not in one flat tree;
  * its INDEX is `{source_fingerprint, families}` — there is no `head`, because "which id is
    current" is a question PER FAMILY, and no provenance chain, because a family's history is its
    committed shapes rather than a parent pointer;
  * it has no `adopt`, no `build` and no `schema_id`: it does not MINT anything.
    ``scripts/schema_report.py --sync`` is the only writer and it arrives holding the ids.

A `ContractStore` variant serving both would need a family dimension on every path and a flag
turning off head, adopt, the parent chain and the document hash — an interface as complex as the
two implementations under it, which is the failure this whole effort exists to remove.  What this
file genuinely shares is the fingerprint, and it shares it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from Schema import fingerprint as _fingerprint

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

    THE ALGORITHM IS SHARED NOW (`Schema.fingerprint.of_files`) rather than copied from
    `runschema.contract`.  The copy this replaces is the precedent `profile_tree`'s own test
    cites, and it drifted for the same reason any uncompared copy does.  Files only, like
    `profile_tree` and unlike `contract`: no DDL source is auto-discovered.
    """
    return _fingerprint.of_files(DDL_SOURCES, repo_root)


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


def main(argv=None) -> int:
    """`python -m Schema.store_index --check`: exit 1 if the committed shape store is stale.

    THE GATE THE STOP HOOK IS NOT.  `Schema/hook_check.py` runs this same `stale_reasons()`
    at the end of every turn and ALWAYS exits 0 -- advisory by design, so it can never block.
    Advisory means ignorable, and on 2026-09-18 it was: two DDL-defining sources
    (`Warehouse_Data.py`, `runtime_metrics.py`) were edited and committed with every gate in
    CLAUDE.md section 1 green, and the only check that noticed --
    `test_schema_compatibility.py::test_the_committed_index_is_current_with_the_tree` --
    sits in the slow architecture tier that a routine `Tests/unit Tests/integration` subset
    never reaches.  `Schema.profile_tree --check` already gave the OTHER store in this
    directory a blocking form; this is the DB-shape store's, and it is the hook's own cheap
    read (file hashes and document stats, no writer imports), so it costs well under a second.

    `--check` is accepted for symmetry with the other gates and is also the default: there is
    nothing else this CLI could do, because `scripts/schema_report.py --sync` is the only writer.
    """
    ap = argparse.ArgumentParser(
        description='Check the committed DB-shape store (Schema/shapes/INDEX.json) against the tree.')
    ap.add_argument('--check', action='store_true',
                    help='exit 1 if a DDL-defining source changed since the last --sync, or an '
                         'indexed declared shape has no committed document (the default action)')
    ap.parse_args(argv)
    reasons = stale_reasons()
    for r in reasons:
        print(f'[schema-db] {r}')
    if not reasons:
        idx = read_index() or {}
        fams = idx.get('families') or {}
        print(f'DB-shape store current: {len(fams)} families indexed, '
              f'fingerprint {str(idx.get("source_fingerprint", "")).split(":", 1)[-1][:12]}.')
    return 1 if reasons else 0


if __name__ == '__main__':                                  # pragma: no cover
    sys.exit(main())
