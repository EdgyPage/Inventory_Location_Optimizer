"""profile_tree.py — THE DECLARATION of the generated-catalogue (profiles) tree, and its store.

The run tree got its contract first (``Optimization/runschema/schema.py``); this is the same
treatment for the tree the INVENTORY GENERATORS write — the catalogue every simulation consumes.
Before this module existed the profiles tree had no descriptor, no declaration, and no version
of any kind on disk: four writers hand-composed the layout, "latest catalogue" was a
lexicographic directory-name sort, and a run recorded only WHERE its catalogue was, never WHICH —
a catalogue regenerated in place silently re-pointed every prior run, undetectably.

Three things live here and nowhere else:

  * ``FEATURES`` / ``LEVELS`` / ``ARTIFACTS`` — the declaration (hashed → the schema id);
  * the committed store at ``Schema/schemas/profile_tree/<short>.json`` + ``INDEX.json``
    (immutable content-addressed documents + a mutable head, the run-tree layout exactly);
  * ``write_profile_layout(...)`` — the descriptor every generator stamps at the profile-run
    root, carrying the schema id, repo provenance, and per-profile params digests.

WHY THIS LIVES IN ``Schema/`` AND IS A PATTERN COPY, NOT A SHARED ABSTRACTION
-----------------------------------------------------------------------------
The writers are in ``Warehouse/generation`` and the consumers in ``Optimization/runschema`` —
the only layer both may import is ``Schema/``.  The store machinery deliberately COPIES
``runschema/contract.py`` (~its hashing, store, and adopt shapes) rather than refactoring the two
onto a shared base: ``schema → optimization`` is forbidden, and an abstraction serving two
contracts is exactly the coupling both were built to avoid.  An honesty test pins this module's
hashing against ``runschema.contract`` on shared input so the copy cannot drift
(the ``store_index.source_fingerprint`` precedent).

DESCRIPTORS ARE FORWARD-ONLY (user decision).  Existing catalogues have no descriptor and never
get one fabricated; every consumer keeps a byte-for-byte legacy walk for them.  A descriptor is
evidence a GENERATOR wrote it, or it is nothing.

CLI:
    python -m Schema.profile_tree --write    # mint/refresh the document for this declaration
    python -m Schema.profile_tree --check    # exit 1 if the committed store is stale
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
from Schema.contractstore import ContractStore as _ContractStore
import json
import os
import sys

from Schema.provenance import repo_provenance

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..'))
_TREE_DIR = os.path.join(_HERE, 'schemas', 'profile_tree')
_INDEX = os.path.join(_TREE_DIR, 'INDEX.json')

#: Matches runschema.contract.SHORT_LEN / shape.SHORT_LEN — same namespace-size reasoning.
SHORT_LEN = 12

#: The descriptor a generator stamps at the profile-run root.  The profiles-tree equivalent of
#: run_layout.json: its presence is what switches consumers off the legacy walk.
DESCRIPTOR = 'profile_layout.json'

# ── the declaration ─────────────────────────────────────────────────────────────

#: Template vocabulary this declaration uses (interpreted by Schema.pathtpl).  No
#: `strategy-capture`, no `resolves-via` — the profiles tree needs neither.
FEATURES = ('optional-segments', 'globs')

#: Ordered directory levels under the profiles root.  The `{inventory,affinity}` split is a
#: LITERAL segment in the artifact templates (like `_aggregate` in the run tree), not a level.
LEVELS = [
    {'name': 'profile_run', 'optional': False,
     'note': 'one generation invocation: mixed_<YYYYMMDD_HHMMSS>, or the --name given to it. '
             'The legacy suite generator used profile_<ts>.'},
    {'name': 'profile', 'optional': False,
     'note': 'one catalogue variant: mixed_realistic_[<freq>_]lt<N> / ...ltrand<lo>-<hi> — the '
             'plan flavour + lead-time spec. The run tree pair label is <profile_run>__<profile>.'},
]

AXES = ('profile_run', 'profile')

#: Every artifact a generation produces.  HASHED: path, format, scope, optional.  NOT hashed:
#: writer, note, condition — attribution and prose must never mint a new schema id.
ARTIFACTS = {
    'profile_layout': {
        'path': DESCRIPTOR, 'format': 'json', 'scope': 'run',
        'writer': 'write_profile_layout@Schema/profile_tree.py',
        'note': 'the descriptor; carries schema_id, provenance, and per-profile params digests. '
                'FORWARD-ONLY: pre-contract catalogues legitimately lack it.'},
    'legacy_suite_manifest': {
        'path': 'profile_manifest.json', 'format': 'json', 'scope': 'run', 'optional': True,
        'writer': 'main@Warehouse/generation/generate_profile_suite.py',
        'condition': 'legacy suite generator only; absent from every generate_mixed_profile tree.'},
    'inventory_db': {
        'path': '{profile}/inventory/inventory.db', 'format': 'sqlite', 'scope': 'profile',
        'family': 'inventory_db',
        'writer': '_init_db@Warehouse/generation/generate_inventory.py'},
    'inventory_params': {
        'path': '{profile}/inventory/params.json', 'format': 'json', 'scope': 'profile',
        'writer': 'generate_run@Warehouse/generation/generate_inventory.py',
        'note': 'sha256 of these bytes is the params_digest in the descriptor - the content '
                'identity a run binds to.'},
    'inventory_stats': {
        'path': '{profile}/inventory/stats.json', 'format': 'json', 'scope': 'profile',
        'writer': 'generate_run@Warehouse/generation/generate_inventory.py'},
    'inventory_plots': {
        'path': '{profile}/inventory/plots/**/*.png', 'format': 'png', 'scope': 'profile',
        'writer': 'generate_run@Warehouse/generation/generate_inventory.py',
        'note': 'covers the optional creation_plan/ and fulfillment/ subtrees.'},
    'affinity_db': {
        'path': '{profile}/affinity/affinity.db', 'format': 'sqlite', 'scope': 'profile',
        'family': 'affinity_db',
        'writer': '_init_db@Warehouse/generation/generate_affinity.py'},
    'affinity_params': {
        'path': '{profile}/affinity/params.json', 'format': 'json', 'scope': 'profile',
        'writer': 'generate_run@Warehouse/generation/generate_affinity.py'},
    'affinity_stats': {
        'path': '{profile}/affinity/stats.json', 'format': 'json', 'scope': 'profile',
        'writer': 'generate_run@Warehouse/generation/generate_affinity.py'},
    'affinity_plots': {
        'path': '{profile}/affinity/plots/**/*.png', 'format': 'png', 'scope': 'profile',
        'writer': 'generate_run@Warehouse/generation/generate_affinity.py'},
    'affinity_arrays': {
        'path': '{profile}/affinity/affinity.db.arrays.npz', 'format': 'npz', 'scope': 'profile',
        'optional': True,
        'writer': 'AffinityStore._write_sidecar@Warehouse/catalog/Affinity_Store.py',
        'note': 'derived acceleration cache: the finished CSR arrays + sku index, written '
                'lazily by the first SQL load and keyed on affinity.db\'s (size, change '
                'counter). Bit-identical to a SQL load by construction; safe to delete — '
                'the next open regenerates it.'},
    'cross_profile_dir': {
        'path': 'cross_profile', 'format': 'dir', 'scope': 'run', 'optional': True,
        'writer': 'main@Warehouse/generation/generate_profile_suite.py',
        'condition': 'legacy suite generator only.'},
}

#: Source files whose edit can move this declaration or its writers' layout — the change TRIGGER
#: (a docstring edit trips it; --write settles it; the id only moves on a real shape change).
SHAPE_SOURCES = (
    'Schema/profile_tree.py',
    'Schema/profile_resolver.py',
    'Schema/pathtpl.py',
    'Warehouse/generation/generate_mixed_profile.py',
    'Warehouse/generation/generate_inventory.py',
    'Warehouse/generation/generate_affinity.py',
    'Warehouse/generation/generate_profile_suite.py',
    'Optimization/runschema/runlayout.py',
    'Optimization/runschema/preflight.py',
)


# ── identity ────────────────────────────────────────────────────────────────────

def _sha(blob: bytes) -> str:
    """sha256 of raw bytes, for `params_digest` -- a FILE digest, not a schema id.

    Kept when the store moved out: a schema id is the hash of a canonical PROJECTION and belongs
    to the store; this is the hash of a file exactly as it sits on disk, and the two only look
    alike.
    """
    return 'sha256:' + hashlib.sha256(blob).hexdigest()


def short_id(sid: str) -> str:
    return _STORE.short_id(sid)


def contract_path(sid: str) -> str:
    return _STORE.path_for(sid)


def _shape_only(doc: dict) -> dict:
    """The projection that DEFINES the tree — exactly what `schema_id` hashes.

    Same exclusion rules as `runschema.contract._shape_only`: no schema_id (an identity cannot
    feed its own hash), no note/condition/description/writer (prose and attribution never mint a
    schema), no provenance.  `family` is attribution too — unhashed, per the run-tree precedent.
    """
    return {
        'features': sorted(doc.get('features', [])),
        'descriptor': doc['descriptor'],
        'axes': doc['axes'],
        'levels': [{'name': lv['name'], 'optional': lv['optional']} for lv in doc['levels']],
        'artifacts': {
            k: {'path': v.get('path'), 'format': v['format'], 'scope': v['scope'],
                'optional': v.get('optional', False)}
            for k, v in sorted(doc['artifacts'].items())
        },
    }


#: THE STORE, shared with the run-tree contract (ticket 13).  What stays HERE is the
#: DECLARATION -- `FEATURES`, `LEVELS`, `ARTIFACTS`, `_shape_only`, `build` -- because an
#: abstraction serving two contracts' levels and artifacts would be exactly the coupling both
#: modules were built to avoid.  What moved is the content-addressed JSON directory, which is
#: identical whatever it holds.  `Schema/` may not import `Optimization/`, and does not need to:
#: the shared half lives HERE and `runschema/contract.py` imports down.
_STORE = _ContractStore(
    tree_dir=_TREE_DIR, shape_of=_shape_only, sources=SHAPE_SOURCES, repo_root=_REPO_ROOT,
    short_len=SHORT_LEN, what='profile-tree',
    refresh_cmd='python -m Schema.profile_tree --write')


def schema_id(doc: dict) -> str:
    return _STORE.schema_id(doc)


def build(repo_root: str = _REPO_ROOT) -> dict:
    """Generate the contract document for the CURRENT declaration above."""
    doc = {
        'schema_id': None,
        'generated_by': 'Schema/profile_tree.py',
        'generator_source': 'Schema/profile_tree.py',
        'description': (
            'Machine-readable contract for a generated-catalogue (profiles) directory, identified '
            'by the sha256 of its own shape. Path templates use {name} for a required segment; '
            'a profile_layout.json descriptor at the profile-run root is FORWARD-ONLY — '
            'pre-contract catalogues lack it and are served by the legacy walk.'),
        'features': list(FEATURES),
        'descriptor': DESCRIPTOR,
        'axes': list(AXES),
        'levels': [dict(lv) for lv in LEVELS],
        'artifacts': {k: dict(v) for k, v in sorted(ARTIFACTS.items())},
    }
    doc['schema_id'] = schema_id(doc)
    return doc


# ── the committed store (immutable docs + mutable INDEX; the run-tree layout) ───

def load(sid: str) -> dict | None:
    return _STORE.load(sid)


def load_all() -> dict:
    return _STORE.load_all()


def read_index() -> dict:
    return _STORE.read_index()


def _write_index(index: dict) -> str:
    return _STORE.write_index(index)


def head() -> str | None:
    """The schema id a NEW catalogue stamps.  A named pointer — hashes have no order."""
    return _STORE.head()


def source_fingerprint(repo_root: str = _REPO_ROOT) -> str:
    """Hash of every shape-defining source (path + content, CRLF-normalised, MISSING-marked).

    THE ALGORITHM IS SHARED, not copied (`Schema.fingerprint`, via the store).  It WAS a copy,
    and it had drifted: `runschema.contract` grew a second input class -- auto-discovered
    directories -- and this one did not (`architecture-drift/05`).

    THIS STORE HASHES FILES ONLY, and that is correct rather than an omission: the profiles tree
    has no auto-discovered directory in its shape, so its `source_dirs` is empty.
    """
    return _STORE.source_fingerprint(repo_root)

def adopt(doc: dict, *, label: str = '') -> str:
    """Store `doc`, set it as head, record provenance + the current source fingerprint.

    Returns the PATH (not the id): `main` prints it, and that is this module's long-standing
    contract.  The store returns the id, which is why this is not a bare delegation.
    """
    _STORE.adopt(doc, label=label, source_fp=_STORE.source_fingerprint())
    return contract_path(doc['schema_id'])


def verify_store() -> list:
    """Findings for the committed store, empty when clean.

    FOUR checks now, not two.  This copy checked that each document hashes to its own id and that
    the head resolves; it did NOT check filenames, and it did NOT check the parent chain even
    though its own `adopt` writes a `parent` field.  One store means one set of invariants.
    """
    return _STORE.verify_store()


def stale_reasons() -> list:
    """Cheap staleness findings for the Stop hook — file hashes and stats only."""
    return _STORE.stale_reasons(build)

# ── the descriptor writer (every generator calls this) ──────────────────────────

def write_profile_layout(run_dir: str, profiles: dict, *, generator: str,
                         argv=None, created: str | None = None) -> str:
    """Stamp `profile_layout.json` at a profile-run root.  Atomic (tmp + os.replace).

    `profiles` maps profile name -> its per-side entries, e.g.
        {'mixed_realistic_bell_lt0': {
            'inventory': {'db': 'inventory/inventory.db', 'params_digest': 'sha256:...',
                          'db_schema_id': '...', 'db_bytes': 12345678},
            'affinity':  {..., 'source_inventory': '../inventory/inventory.db'}}}
    Callers REWRITE after every completed profile, so a crashed multi-profile generation still
    leaves a valid partial descriptor covering what finished.
    """
    doc = {
        'version': 1,
        'schema_id': head() or build()['schema_id'],
        'created': created or _dt.datetime.now().replace(microsecond=0).isoformat(),
        'generator': generator,
        'argv': list(argv) if argv is not None else None,
        **repo_provenance(),
        'profiles': profiles,
    }
    path = os.path.join(run_dir, DESCRIPTOR)
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2, sort_keys=False)
        f.write('\n')
    os.replace(tmp, path)
    return path


def entries_from_disk(run_dir: str) -> dict:
    """Descriptor `profiles` entries derived from a run dir the CALLER JUST WROTE.

    For generators whose write path does not thread per-side metadata through one loop (the
    legacy suite; the preflight canary): the generator claims what it wrote by walking its own
    output.  This is NOT backfill — a generator deriving its own just-written tree is the writer
    speaking; deriving someone else's old tree would be fabricated provenance, and stays banned.
    Sides without a params.json record `params_digest: None` (the canary writes none) — absence
    stays honest.
    """
    from Schema import connect as _connect
    from Schema import identity as _identity

    out: dict = {}
    for profile in sorted(os.listdir(run_dir)) if os.path.isdir(run_dir) else []:
        pdir = os.path.join(run_dir, profile)
        if not os.path.isdir(pdir):
            continue
        entry: dict = {}
        for side, db_name, family in (('inventory', 'inventory.db', 'inventory_db'),
                                      ('affinity', 'affinity.db', 'affinity_db')):
            db_path = os.path.join(pdir, side, db_name)
            if not os.path.exists(db_path):
                continue
            params_path = os.path.join(pdir, side, 'params.json')
            con = _connect.read_only(db_path)
            try:
                sid = _identity.read_stamp(con, _identity.get(family))
            finally:
                con.close()
            entry[side] = {
                'db': f'{side}/{db_name}',
                'params_digest': (params_digest(params_path)
                                  if os.path.isfile(params_path) else None),
                'db_schema_id': sid,
                'db_bytes': os.path.getsize(db_path),
            }
            if side == 'affinity':
                entry[side]['source_inventory'] = '../inventory/inventory.db'
        if entry:
            out[profile] = entry
    return out


def params_digest(params_json_path: str) -> str:
    """sha256 over the params.json FILE BYTES as written.

    No canonicalisation, deliberately: the params timestamp participating in the digest is a
    FEATURE — a regeneration mints a new digest even under identical parameters, which is
    exactly what makes silent in-place re-pointing detectable from a run's recorded binding.
    """
    with open(params_json_path, 'rb') as f:
        return _sha(f.read())


def read_profile_layout(run_dir: str) -> dict | None:
    """The descriptor at a profile-run root, or None (a pre-contract catalogue — the normal
    answer for everything generated before this module existed; never an error)."""
    p = os.path.join(run_dir, DESCRIPTOR)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Mint/refresh or check the profile-tree contract.')
    ap.add_argument('--write', action='store_true',
                    help='store the document for the current declaration and set it as head')
    ap.add_argument('--check', action='store_true',
                    help='exit 1 if the committed store is stale or inconsistent')
    args = ap.parse_args(argv)
    if args.write:
        doc = build()
        path = adopt(doc)
        print(f'profile-tree schema {short_id(doc["schema_id"])} stored + set as head: '
              f'{os.path.relpath(path, _REPO_ROOT)}')
        return 0
    reasons = stale_reasons()
    for r in reasons:
        print(f'[profile-tree] {r}')
    if not reasons:
        idx = read_index()
        print(f'profile-tree schema {short_id(idx["head"])} current '
              f'({len(load_all())} stored schema(s)).')
    return 1 if reasons else 0


if __name__ == '__main__':                                  # pragma: no cover
    sys.exit(main())
