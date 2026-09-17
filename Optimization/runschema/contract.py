"""runschema.contract — derive, store and diff CONTENT-ADDRESSED run-tree contracts.

A contract document is generated from ``runschema/schema.py``'s LEVELS + ARTIFACTS tables and
committed at ``Optimization/schemas/run_tree/<short>.json``, so consumers that aren't Python (the JS
viewer, notebooks, any future ETL) read the same declaration the resolver enforces.  Nothing here
inspects a run directory — that's ``preflight.observe``; this module only deals with the DECLARED
shape.

IDENTITY IS DERIVED, NOT CHOSEN
-------------------------------
``schema_id = "sha256:" + sha256(canonical_json(shape))`` where `shape` is the projection in
``_shape_only``.  Consequences worth understanding:

  * The id cannot be an INPUT to its own hash, so `schema_id` is excluded from `_shape_only`.
  * The document is IMMUTABLE: its filename is its own content hash.  Mutable state (the
    source-change trigger, the current head, provenance) therefore lives in ``INDEX.json``, never in
    the document.
  * Prose (`note`/`condition`/`writer`) is excluded, so documenting the tree better never mints a
    new schema; anything a path resolver depends on is included, so a real change always does.

Two fingerprints answer different questions:

  * ``schema_id``          — hash of the DECLARED shape. Identity. Changes only on a real change.
  * ``source_fingerprint`` — hash of the shape-DEFINING source files (in INDEX.json). A cheap
    *trigger*, not proof: a docstring edit trips it, which is exactly why the preflight then runs
    canaries before concluding anything.

Run standalone:
    python -m Optimization.runschema.contract --write   # mint/refresh the document for schema.py
    python -m Optimization.runschema.contract --check   # exit 1 if the committed store is stale
"""
from __future__ import annotations

import argparse
import hashlib
from Schema.contractstore import ContractStore as _ContractStore
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SCHEMA_DIR = os.path.join(_REPO_ROOT, 'Optimization', 'schemas')
_TREE_DIR = os.path.join(_SCHEMA_DIR, 'run_tree')
_INDEX = os.path.join(_TREE_DIR, 'INDEX.json')

# Display/filename length for a schema id.  12 hex = 48 bits: short enough to say out loud and
# to fit a UI badge, far beyond collision range for a repo that mints a handful of schemas.
# The full sha256 always lives inside the document; `write` refuses a short-prefix collision.
SHORT_LEN = 12

# ── the shape-defining source set ───────────────────────────────────────────────
# Every file that can move, rename, or add a path in the run tree.  A change here TRIGGERS the
# preflight canaries; it does not by itself mean the tree changed.  Keep this list generous —
# a false trigger costs one canary pair, a missing entry costs a silently-broken downstream tool.
SHAPE_SOURCES = (
    'Optimization/runschema/runlayout.py',
    'Optimization/runschema/sim_manifest.py',
    'Optimization/simdriver/sim_assets.py',
    'Optimization/simdriver/__init__.py',
    'Optimization/simdriver/cells.py',
    'Optimization/simdriver/scenario.py',
    'Optimization/simdriver/supervisor.py',
    'Optimization/simdriver/workunits.py',
    'Optimization/simdriver/strategy_runner.py',
    'Optimization/run_simulation.py',
    'Optimization/analyze_run.py',
    'Optimization/run_analysis.py',
    'Optimization/run_channel_rollup.py',
    'Optimization/run_restock_selection.py',
    'Optimization/run_whatif_delta.py',
    'Optimization/run_whatif_labor.py',
    # run_whatif_volume declares two run-root artifacts and was never listed here, so
    # editing it did not move the source fingerprint.  Fixed while the dossier's own
    # writers were added.
    'Optimization/run_whatif_volume.py',
    'Optimization/run_map_precompute.py',
    'Optimization/persistence/runtime_metrics.py',
    'Optimization/simdriver/batch_precompute.py',
    'Optimization/config/whatif_config.py',
    'Optimization/config/strategies.py',
    'Optimization/config/sim_config.py',
    # The flat authoring surface CONFIG is derived from.  Listed for the same reason
    # sim_config is: editing a VALUE here moves the tree's source fingerprint, so
    # preflight re-proves the shape.  Omitting it would make a config edit invisible --
    # exactly the bug the run_whatif_volume note above records.
    'Optimization/config/settings.py',
    'Optimization/Performance_Evaluations/driver.py',
    'Optimization/Performance_Evaluations/common/io.py',
    'Optimization/Performance_Evaluations/common/series.py',
    'Optimization/simconfig/__init__.py',
    'Optimization/simconfig/constants.py',
    'Optimization/simconfig/core/discovery.py',
    'Optimization/simconfig/core/registry.py',
    # channels decides whether the CONDITIONAL `<channel>/` level exists at all -- it
    # appears only on a mixed catalogue -- so it is as shape-defining as sim_config.
    # It was never listed: the same omission the run_whatif_volume note above records.
    'Optimization/config/channels.py',
    'Optimization/runschema/schema.py',
)

# -- shape-defining DIRECTORIES ---------------------------------------------------
# A tuple of file PATHS cannot notice a file that did not exist when it was written, and
# one package turns exactly that into a tree change.  `simconfig/configs/` is
# auto-discovered -- `core/discovery.py` states it plainly: "dropping a new pick-config
# file into configs/ registers it with zero edits elsewhere" -- and a registered config's
# NAME becomes the `<config>` LEVEL's directory name.  So adding one renames a level of
# the run tree while the fingerprint sees nothing at all.
#
# Listing today's four files would not fix it; the fifth would be just as invisible.
# Hashing the directory's `*.py` in sorted order does, because an addition, a deletion
# and a rename each change the hashed sequence.
SHAPE_SOURCE_DIRS = (
    'Optimization/simconfig/configs',
)


#: THE STORE, shared with `Schema/profile_tree.py` (ticket 13).  What stays HERE is the
#: DECLARATION -- `SHAPE_SOURCES`, `_shape_only`, `build`, `diff_shape` -- because an
#: abstraction serving two contracts' levels and artifacts would be exactly the coupling both
#: modules were built to avoid.  What moved is the content-addressed JSON directory, identical
#: whatever it holds.  `Schema/` may not import `Optimization/`; the shared half therefore lives
#: THERE and this module imports DOWN, which satisfies the boundary by DIRECTION rather than by
#: copying -- the conclusion this module's own docstring used to draw the other way round.
_STORE = _ContractStore(
    tree_dir=_TREE_DIR, shape_of=lambda doc: _shape_only(doc), sources=SHAPE_SOURCES,
    source_dirs=SHAPE_SOURCE_DIRS, repo_root=_REPO_ROOT, short_len=SHORT_LEN,
    what='run-tree', refresh_cmd='python -m Optimization.runschema.contract --write',
    diff=lambda a, b: diff_shape(a, b))


def short_id(schema_id: str) -> str:
    """'sha256:a412c613ba76…' -> 'a412c613ba76' — the display/filename form.

    NB the full id contains a colon, which is an illegal filename character on Windows, so the
    short form is what names files; the full id lives inside the document.
    """
    return _STORE.short_id(schema_id)


def contract_path(schema_id: str) -> str:
    """Where the document for a schema id lives.  Accepts a full id or an already-short form."""
    return _STORE.path_for(schema_id)

def source_fingerprint(repo_root: str = _REPO_ROOT) -> str:
    """Hash of every shape-defining source file (path + content), in declared order, PLUS the
    auto-discovered directories.

    `Schema.fingerprint` owns the algorithm and this is the only store with a directory half:
    a path tuple can only describe files someone already thought of, and `simconfig/configs/` is
    auto-discovered.  That asymmetry is 100% of why this store's fingerprint differs from the two
    file-only ones on shared input (`architecture-drift/05`).

    Line endings are normalised and a missing file contributes a MISSING marker; see the helper
    for why each of those cost someone something.
    """
    return _STORE.source_fingerprint(repo_root)

def _shape_only(doc: dict) -> dict:
    """The projection that DEFINES the tree — the exact material `schema_id` hashes.

    Excluded on purpose:
      * `schema_id` — an identity cannot be an input to its own hash.
      * `note` / `condition` / `description` — documenting the tree better must never mint a new
        schema, so prose is stripped before hashing.
      * `writer` — WHICH function creates a file is attribution, not tree shape; moving a producer
        does not move the file.
      * `source_fingerprint` / provenance — mutable, and lives in INDEX.json.

    Included: everything a path resolver depends on.  Note `axes`, `levels`, `tables` and
    `resolves_via` are LISTS, so their ORDER is significant — reordering them is a real change.
    """
    return {
        'features': sorted(doc.get('features', [])),
        'root_prefixes': doc['root_prefixes'],
        'reserved_prefix': doc['reserved_prefix'],
        'axes': doc['axes'],
        'levels': [{'name': lv['name'], 'optional': lv['optional']} for lv in doc['levels']],
        'artifacts': {
            k: {'path': v.get('path'), 'format': v['format'], 'scope': v['scope'],
                'optional': v.get('optional', False), 'tables': v.get('tables', []),
                'group': v.get('group'), 'resolves_via': v.get('resolves_via', [])}
            for k, v in sorted(doc['artifacts'].items())
        },
    }


def schema_id(doc: dict) -> str:
    """The content-addressed identity of a contract: sha256 over its canonical shape."""
    return _STORE.schema_id(doc)


def build(repo_root: str = _REPO_ROOT) -> dict:
    """Generate the contract document for the CURRENT declaration in runschema/schema.py.

    Its `schema_id` is derived from the tables — there is nothing to choose and nothing to register.
    """
    from Optimization.runschema import schema as decl
    doc = {
        'schema_id': None,                                   # filled below; excluded from the hash
        'generated_by': 'Optimization/runschema/contract.py',
        'generator_source': 'Optimization/runschema/schema.py',
        'description': (
            'Machine-readable contract for a simulation run directory, identified by the sha256 of '
            'its own shape. Levels marked optional are ABSENT under the stated condition — consumers '
            'must handle both shapes. Path templates use {name} for a required segment and {name?} '
            'for an optional one (dropped with its separator when that part is None).'),
        'features': list(decl.FEATURES),
        'root_prefixes': list(decl.ROOT_PREFIXES),
        'reserved_prefix': decl.RESERVED_PREFIX,
        'axes': list(decl.AXES),
        'levels': [dict(lv) for lv in decl.LEVELS],
        'artifacts': {k: dict(v) for k, v in sorted(decl.ARTIFACTS.items())},
    }
    doc['schema_id'] = schema_id(doc)
    return doc


def load(sid: str) -> dict | None:
    """The committed document for a schema id (full or short), or None when absent."""
    return _STORE.load(sid)


def load_all() -> dict[str, dict]:
    """Every committed contract document, keyed by full schema id."""
    return _STORE.load_all()


# ── INDEX.json — the mutable side: head pointer, change trigger, provenance chain ─

def read_index() -> dict:
    """The schema store's index, or an empty skeleton when it doesn't exist yet."""
    return _STORE.read_index()


def write_index(index: dict) -> str:
    return _STORE.write_index(index)


def head() -> str | None:
    """The schema id a NEW run stamps.  A named pointer, because hashes have no natural order."""
    return _STORE.head()


def write(doc: dict) -> str:
    """Write a contract document to its versioned path, atomically.  Returns the path."""
    return _STORE.write(doc)

def diff_shape(a: dict, b: dict) -> list[str]:
    """Human-readable differences between two contracts' SHAPES (a = old, b = new).

    Empty list ⇔ identical schema_id.  Under content addressing this is the ONLY explanation of why
    an id changed, so it must cover every field `_shape_only` hashes — including list ORDER, which
    an earlier set-based comparison silently ignored (reordering axes flipped the hash but produced
    no diff, i.e. "STALE" with no reason given).
    """
    sa, sb = _shape_only(a), _shape_only(b)
    msgs: list[str] = []

    for field in ('features', 'root_prefixes', 'reserved_prefix'):
        if sa[field] != sb[field]:
            msgs.append(f'{field}: {sa[field]!r} -> {sb[field]!r}')

    la = {lv['name']: lv for lv in sa['levels']}
    lb = {lv['name']: lv for lv in sb['levels']}
    for name in sorted(set(la) | set(lb)):
        if name not in la:
            msgs.append(f'level ADDED: {name} (optional={lb[name]["optional"]})')
        elif name not in lb:
            msgs.append(f'level REMOVED: {name}')
        elif la[name]['optional'] != lb[name]['optional']:
            msgs.append(f'level {name}: optional {la[name]["optional"]} -> {lb[name]["optional"]}')
    if [lv['name'] for lv in sa['levels']] != [lv['name'] for lv in sb['levels']]:
        msgs.append(f'level ORDER: {[lv["name"] for lv in sa["levels"]]} -> '
                    f'{[lv["name"] for lv in sb["levels"]]}')

    aa, ab = sa['artifacts'], sb['artifacts']
    for key in sorted(set(aa) | set(ab)):
        if key not in aa:
            msgs.append(f'artifact ADDED: {key} -> {ab[key]["path"]}')
        elif key not in ab:
            msgs.append(f'artifact REMOVED: {key} (was {aa[key]["path"]})')
        else:
            for field in ('path', 'format', 'scope', 'optional', 'group', 'resolves_via'):
                if aa[key][field] != ab[key][field]:
                    msgs.append(f'artifact {key}.{field}: {aa[key][field]!r} -> {ab[key][field]!r}')
            if aa[key]['tables'] != ab[key]['tables']:
                added = sorted(set(ab[key]['tables']) - set(aa[key]['tables']))
                gone = sorted(set(aa[key]['tables']) - set(ab[key]['tables']))
                if added:
                    msgs.append(f'artifact {key}.tables +{added}')
                if gone:
                    msgs.append(f'artifact {key}.tables -{gone}')
                if not added and not gone:
                    msgs.append(f'artifact {key}.tables ORDER: {aa[key]["tables"]} -> '
                                f'{ab[key]["tables"]}')

    for axis in sorted(set(sa['axes']) ^ set(sb['axes'])):
        msgs.append(f'axis CHANGED: {axis} ({"added" if axis in sb["axes"] else "removed"})')
    # `axes` is hashed as an ordered LIST, so a pure reordering IS a new schema and must be
    # explained — reporting only set-difference left the id changing for no stated reason.
    if sa['axes'] != sb['axes'] and set(sa['axes']) == set(sb['axes']):
        msgs.append(f'axis ORDER: {sa["axes"]} -> {sb["axes"]}')
    return msgs


def adopt(doc: dict, *, label: str | None = None, source_fp: str | None = None,
          commit: str | None = None, created: str | None = None) -> str:
    """Store `doc` and make it the head, recording its provenance.  Returns its schema id.

    Idempotent: adopting the current head only refreshes the mutable `source_fingerprint`.  The
    previous head becomes the new entry's `parent`, which is what restores the ordering that
    hashes inherently lack, and `diff_shape` fills the entry's `changes`.
    """
    return _STORE.adopt(doc, label=label or '', source_fp=source_fp,
                        commit=commit, created=created)


def verify_store() -> list[str]:
    """Integrity of the whole committed store.  Empty list = sound.

    Four properties, and they apply to the profiles tree too now that both stores are one object.
    Doing so immediately found two DELETED documents there whose INDEX entries survived --
    exactly what the two-check copy could not see.
    """
    return _STORE.verify_store()


def stale_reasons() -> list[str]:
    """Cheap staleness findings, for the Stop hook.

    NEW HERE (ticket 13): this module had none, so `runschema/hook_check.py` re-implemented it
    inline -- a fourth partial copy, one level up from the three this ticket collapsed.
    """
    return _STORE.stale_reasons(build)

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='Derive, store or verify the content-addressed run-tree contract.')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--write', action='store_true',
                   help="store the current schema.py declaration and make it the head")
    g.add_argument('--check', action='store_true',
                   help='exit 1 if the committed store is stale vs schema.py (default)')
    ap.add_argument('--label', default=None, help='human label recorded in INDEX.json')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    fresh = build()
    sid = fresh['schema_id']

    if args.write:
        adopt(fresh, label=args.label, source_fp=source_fingerprint())
        if not args.quiet:
            print(f'run-tree schema {short_id(sid)} stored + set as head: '
                  f'{os.path.relpath(contract_path(sid), _REPO_ROOT)}')
            print(f'  schema_id         : {sid}')
            print(f'  source_fingerprint: {source_fingerprint()}')
        return 0

    hd = head()
    if hd is None:
        print('run-tree schema store is EMPTY — mint it: '
              'python -m Optimization.runschema.contract --write')
        return 1
    if hd != sid:
        committed = load(hd)
        print(f'run-tree schema STALE — schema.py now hashes to {short_id(sid)}, '
              f'but the head is {short_id(hd)}:')
        for m in (diff_shape(committed, fresh) if committed else ['head document missing']):
            print(f'  - {m}')
        print('  adopt it with: python -m Optimization.runschema.contract --write')
        return 1
    problems = verify_store()
    if problems:
        print('run-tree schema store INTEGRITY failure:')
        for m in problems:
            print(f'  - {m}')
        return 1
    if not args.quiet:
        print(f'run-tree schema {short_id(sid)} OK — {len(fresh["artifacts"])} artifact(s), '
              f'{len(fresh["levels"])} level(s), {len(load_all())} stored schema(s).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
