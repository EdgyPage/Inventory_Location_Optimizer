"""verify_context.py — assert that every anchor in context/ still exists in the code.

The context docs (flows/*.yml + artifacts.yml) describe the pipeline with greppable
anchors: function/class/const names @ files, artifact filename literals, sqlite
CREATE TABLE names, and guard-test files.  This script re-checks every one of them
against the working tree and exits 1 on any drift — making the docs VERIFIABLE
rather than aspirational.  Runs standalone or via Tests/test_context_sync.py.

Usage:  python context/verify_context.py [--quiet]
"""
from __future__ import annotations

import argparse
import os
import re
import sys

try:
    import yaml
except ImportError:                                    # pragma: no cover
    sys.exit('verify_context needs pyyaml (pip install -r requirements-docs.txt)')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

_errors: list[str] = []


def err(msg: str) -> None:
    _errors.append(msg)


def _read(relpath: str) -> str | None:
    path = os.path.join(_ROOT, relpath)
    if not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        return f.read()


_SRC_CACHE: dict[str, str | None] = {}


def src_of(relpath: str) -> str | None:
    if relpath not in _SRC_CACHE:
        _SRC_CACHE[relpath] = _read(relpath)
    return _SRC_CACHE[relpath]


def check_symbol(name: str, file: str, kind: str, where: str) -> None:
    src = src_of(file)
    if src is None:
        err(f'{where}: file missing: {file}')
        return
    if kind == 'const':
        pat = rf'^{re.escape(name)}\s*[:=]'
    elif kind == 'class':
        pat = rf'^\s*class\s+{re.escape(name)}\b'
    else:  # function (also accepts a class implementing a callable anchor)
        pat = rf'^\s*(def|class)\s+{re.escape(name)}\b'
    if not re.search(pat, src, re.M):
        err(f'{where}: `{name}` ({kind}) not found in {file}')


def check_flow(relpath: str, artifact_ids: set) -> None:
    data = yaml.safe_load(_read(relpath) or '')
    if not isinstance(data, dict):
        err(f'{relpath}: not a mapping')
        return
    for key in ('version', 'flow', 'summary', 'entry', 'steps'):
        if key not in data:
            err(f'{relpath}: missing top-level key `{key}`')
    entry = data.get('entry', {})
    if isinstance(entry, dict):
        check_symbol(entry.get('function', ''), entry.get('file', ''), 'function',
                     f'{relpath}:entry')
    step_ids = [s.get('id') for s in data.get('steps', [])]
    if len(step_ids) != len(set(step_ids)):
        err(f'{relpath}: duplicate step ids')
    for step in data.get('steps', []):
        where = f"{relpath}:{step.get('id', '?')}"
        for req in ('id', 'summary', 'functions'):
            if req not in step:
                err(f'{where}: missing `{req}`')
        for fn in step.get('functions', []):
            check_symbol(fn.get('name', ''), fn.get('file', ''),
                         fn.get('kind', 'function'), where)
        for io_key in ('reads', 'writes'):
            for aid in step.get(io_key, []):
                if aid not in artifact_ids:
                    err(f'{where}: {io_key} references unknown artifact `{aid}`')
        for nxt in step.get('next', []):
            if nxt not in step_ids:
                err(f'{where}: next references unknown step `{nxt}`')
        for guard in step.get('guards', []):
            gsrc = src_of(guard)
            if gsrc is None:
                err(f'{where}: guard test missing: {guard}')
            elif os.path.basename(guard).startswith('test_') and 'def test_' not in gsrc:
                err(f'{where}: guard {guard} contains no `def test_`')


def check_artifacts(relpath: str) -> set:
    data = yaml.safe_load(_read(relpath) or '')
    arts = data.get('artifacts', {}) if isinstance(data, dict) else {}
    if not arts:
        err(f'{relpath}: no artifacts defined')
    for aid, art in arts.items():
        where = f'{relpath}:{aid}'
        for req in ('path_pattern', 'format', 'writer'):
            if req not in art:
                err(f'{where}: missing `{req}`')
        writer = art.get('writer', {})
        check_symbol(writer.get('function', ''), writer.get('file', ''), 'function',
                     f'{where}:writer')
        for rd in art.get('readers', []):
            check_symbol(rd.get('function', ''), rd.get('file', ''), 'function',
                         f'{where}:reader')
        # The artifact's filename literal must appear in its writer's source — the
        # cheap tripwire that catches renamed outputs.
        match = art.get('match') or os.path.basename(art['path_pattern'])
        wsrc = src_of(writer.get('file', ''))
        if wsrc is not None and match not in wsrc:
            err(f'{where}: literal `{match}` not found in writer {writer.get("file")}')
        # sqlite: every declared table must have a CREATE TABLE in schema_file (or writer).
        schema_file = art.get('schema_file', writer.get('file', ''))
        ssrc = src_of(schema_file)
        for table in art.get('tables', []):
            if ssrc is None or not re.search(
                    rf'CREATE TABLE(\s+IF NOT EXISTS)?\s+{re.escape(table)}\b', ssrc):
                err(f'{where}: table `{table}` has no CREATE TABLE in {schema_file}')
    return set(arts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--quiet', action='store_true', help='print nothing on success')
    args = ap.parse_args()

    artifact_ids = check_artifacts('context/artifacts.yml')
    flows_dir = os.path.join(_HERE, 'flows')
    flow_files = sorted(f for f in os.listdir(flows_dir) if f.endswith('.yml'))
    if not flow_files:
        err('context/flows/: no flow specs found')
    for f in flow_files:
        check_flow(f'context/flows/{f}', artifact_ids)

    if _errors:
        print(f'context/ DRIFT — {len(_errors)} anchor(s) no longer match the code:')
        for e in _errors:
            print('  -', e)
        return 1
    if not args.quiet:
        print(f'context/ OK — {len(flow_files)} flow(s) + {len(artifact_ids)} artifacts verified.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
