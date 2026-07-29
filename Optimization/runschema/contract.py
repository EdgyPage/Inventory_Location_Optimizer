"""runschema.contract — emit / fingerprint / diff the machine-readable run-tree contract.

``Optimization/schemas/run_tree.v<N>.json`` is GENERATED from ``runschema/v<N>.py``'s LEVELS +
ARTIFACTS tables and committed, so consumers that aren't Python (the JS viewer, notebooks, any
future ETL) read the same contract the resolver enforces.  Nothing here inspects a run directory —
that's ``preflight.observe``; this module only deals with the DECLARED shape.

Two fingerprints live in the emitted file and they answer different questions:

  * ``tree_fingerprint``   — hash of the DECLARED shape (levels + artifact path/format/scope/tables).
    Changes only when the contract itself changes.  This is what a version bump is about.
  * ``source_fingerprint`` — hash of the shape-DEFINING source files.  Changes whenever code that
    could move a file moves.  It is a cheap *trigger*, not proof: a docstring edit trips it, which
    is exactly why the preflight then runs canaries before concluding anything.

Run standalone:
    python -m Optimization.runschema.contract --write     # regenerate the committed JSON
    python -m Optimization.runschema.contract --check     # exit 1 if the JSON is stale
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.normpath(os.path.join(_HERE, '..', '..'))
_SCHEMA_DIR = os.path.join(_REPO_ROOT, 'Optimization', 'schemas')

# ── the shape-defining source set ───────────────────────────────────────────────
# Every file that can move, rename, or add a path in the run tree.  A change here TRIGGERS the
# preflight canaries; it does not by itself mean the tree changed.  Keep this list generous —
# a false trigger costs one canary pair, a missing entry costs a silently-broken downstream tool.
SHAPE_SOURCES = (
    'Optimization/runlayout.py',
    'Optimization/sim_manifest.py',
    'Optimization/sim_assets.py',
    'Optimization/simdriver/__init__.py',
    'Optimization/simdriver/cells.py',
    'Optimization/simdriver/scenario.py',
    'Optimization/simdriver/supervisor.py',
    'Optimization/simdriver/workunits.py',
    'Optimization/strategy_runner.py',
    'Optimization/run_simulation.py',
    'Optimization/analyze_run.py',
    'Optimization/run_analysis.py',
    'Optimization/run_channel_rollup.py',
    'Optimization/run_whatif_delta.py',
    'Optimization/run_whatif_labor.py',
    'Optimization/run_runtime_graphs.py',
    'Optimization/runtime_metrics.py',
    'Optimization/batch_precompute.py',
    'Optimization/whatif_config.py',
    'Optimization/strategies.py',
    'Optimization/sim_config.py',
    'Optimization/Performance_Evaluations/driver.py',
    'Optimization/Performance_Evaluations/common/io.py',
    'Optimization/Performance_Evaluations/common/series.py',
    'Optimization/simconfig/__init__.py',
    'Optimization/simconfig/constants.py',
    'Optimization/simconfig/core/discovery.py',
    'Optimization/simconfig/core/registry.py',
    'Optimization/runschema/v1.py',
)


def _sha(payload: bytes) -> str:
    return 'sha256:' + hashlib.sha256(payload).hexdigest()


def contract_path(version: int) -> str:
    return os.path.join(_SCHEMA_DIR, f'run_tree.v{version}.json')


def source_fingerprint(repo_root: str = _REPO_ROOT) -> str:
    """Hash of every shape-defining source file (path + content), in declared order.

    Line endings are NORMALISED to \\n before hashing.  On Windows a checkout, stash/pop, or
    autocrlf change rewrites CRLF<->LF without touching a single statement; hashing raw bytes made
    that look like a structural change and cost a needless canary pair.  Content changes still
    register, which is the whole point.

    A missing file contributes its path plus a MISSING marker rather than being skipped, so
    DELETING a shape source is detected instead of silently matching.
    """
    h = hashlib.sha256()
    for rel in SHAPE_SOURCES:
        h.update(rel.encode('utf-8'))
        p = os.path.join(repo_root, rel.replace('/', os.sep))
        try:
            with open(p, 'rb') as f:
                h.update(f.read().replace(b'\r\n', b'\n'))
        except OSError:
            h.update(b'\x00MISSING')
    return 'sha256:' + h.hexdigest()


def _shape_only(doc: dict) -> dict:
    """The parts of a contract that DEFINE the tree — prose and fingerprints excluded.

    `note` / `condition` are documentation: editing them must not force a version bump, so they are
    stripped before hashing.  Everything that a downstream path-resolver depends on stays.
    """
    return {
        'schema_version': doc['schema_version'],
        'root_prefixes': doc['root_prefixes'],
        'reserved_prefix': doc['reserved_prefix'],
        'axes': doc['axes'],
        'levels': [{'name': lv['name'], 'optional': lv['optional']} for lv in doc['levels']],
        'artifacts': {
            k: {'path': v['path'], 'format': v['format'], 'scope': v['scope'],
                'optional': v.get('optional', False), 'tables': v.get('tables', [])}
            for k, v in sorted(doc['artifacts'].items())
        },
    }


def tree_fingerprint(doc: dict) -> str:
    return _sha(json.dumps(_shape_only(doc), sort_keys=True, separators=(',', ':')).encode('utf-8'))


def build(version: int | None = None, repo_root: str = _REPO_ROOT) -> dict:
    """Generate the contract document for a version from its vN module's tables."""
    from Optimization import runschema
    version = runschema.RUN_TREE_VERSION if version is None else version
    mod = __import__(f'Optimization.runschema.v{version}', fromlist=['*'])
    doc = {
        'schema_version': mod.SCHEMA_VERSION,
        'generated_by': 'Optimization/runschema/contract.py',
        'generator_source': f'Optimization/runschema/v{version}.py',
        'description': (
            'Machine-readable contract for a simulation run directory. Levels marked optional are '
            'ABSENT under the stated condition — consumers must handle both shapes. Path templates '
            'use {name} for a required segment and {name?} for an optional one (dropped with its '
            'separator when that part is None).'),
        'root_prefixes': list(mod.ROOT_PREFIXES),
        'reserved_prefix': mod.RESERVED_PREFIX,
        'axes': list(mod.AXES),
        'levels': [dict(lv) for lv in mod.LEVELS],
        'artifacts': {k: dict(v) for k, v in sorted(mod.ARTIFACTS.items())},
    }
    doc['tree_fingerprint'] = tree_fingerprint(doc)
    doc['source_fingerprint'] = source_fingerprint(repo_root)
    return doc


def load(version: int) -> dict | None:
    """The committed contract for a version, or None when it isn't there yet."""
    p = contract_path(version)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def write(doc: dict) -> str:
    """Write a contract document to its versioned path, atomically.  Returns the path."""
    os.makedirs(_SCHEMA_DIR, exist_ok=True)
    path = contract_path(doc['schema_version'])
    tmp = f'{path}.tmp.{os.getpid()}'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=2, sort_keys=False)
        f.write('\n')
    os.replace(tmp, path)
    return path


def diff_shape(a: dict, b: dict) -> list[str]:
    """Human-readable differences between two contracts' SHAPES (a = committed, b = new).

    Empty list ⇔ identical tree_fingerprint, so this doubles as the bump decision's explanation.
    """
    sa, sb = _shape_only(a), _shape_only(b)
    msgs: list[str] = []

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
            for field in ('path', 'format', 'scope', 'optional'):
                if aa[key][field] != ab[key][field]:
                    msgs.append(f'artifact {key}.{field}: {aa[key][field]!r} -> {ab[key][field]!r}')
            if aa[key]['tables'] != ab[key]['tables']:
                added = sorted(set(ab[key]['tables']) - set(aa[key]['tables']))
                gone = sorted(set(aa[key]['tables']) - set(ab[key]['tables']))
                if added:
                    msgs.append(f'artifact {key}.tables +{added}')
                if gone:
                    msgs.append(f'artifact {key}.tables -{gone}')

    for axis in sorted(set(sa['axes']) ^ set(sb['axes'])):
        msgs.append(f'axis CHANGED: {axis} ({"added" if axis in sb["axes"] else "removed"})')
    return msgs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Generate or verify the run-tree contract JSON.')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--write', action='store_true', help='regenerate the committed contract JSON')
    g.add_argument('--check', action='store_true',
                   help='exit 1 if the committed JSON is stale vs the vN module (default)')
    ap.add_argument('--version', type=int, default=None, help='contract version (default: latest)')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    fresh = build(args.version)
    ver = fresh['schema_version']

    if args.write:
        path = write(fresh)
        if not args.quiet:
            print(f'run-tree contract v{ver} written: {os.path.relpath(path, _REPO_ROOT)}')
            print(f'  tree_fingerprint  : {fresh["tree_fingerprint"]}')
            print(f'  source_fingerprint: {fresh["source_fingerprint"]}')
        return 0

    committed = load(ver)
    if committed is None:
        print(f'run-tree contract v{ver} MISSING: {os.path.relpath(contract_path(ver), _REPO_ROOT)}'
              f'\n  regenerate with: python -m Optimization.runschema.contract --write')
        return 1
    if committed.get('tree_fingerprint') != fresh['tree_fingerprint']:
        print(f'run-tree contract v{ver} STALE — run_tree.v{ver}.json does not match '
              f'runschema/v{ver}.py:')
        for m in diff_shape(committed, fresh):
            print(f'  - {m}')
        print('  regenerate with: python -m Optimization.runschema.contract --write')
        return 1
    if not args.quiet:
        print(f'run-tree contract v{ver} OK — {len(fresh["artifacts"])} artifact(s), '
              f'{len(fresh["levels"])} level(s).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
