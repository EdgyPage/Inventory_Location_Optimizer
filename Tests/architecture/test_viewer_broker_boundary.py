"""test_viewer_broker_boundary.py — producer-broker-consumer, ENFORCED in the viewer.

The alignment made the reader layer the viewer's broker: identity through the family, SQL
composed from publisher-side named queries, conditional reads behind probes/surface checks.
This file is what keeps it that way — the raw sites that legitimately remain are an
ALLOWLIST with reasons, shrink-only in spirit and exact in letter:

  R1  every SQL string literal in Visualization/*.py lives in an allowlisted (file, function)
      or the function composes via the broker seam (self._sql / sql_for / ds.query / ds.read);
  R2  `sqlite3.connect` appears nowhere in Visualization/ — every open goes through
      Schema.connect (read_only / bulk_writer), the sanctioned openers;
  R3  server.py contains no SQL at all (routing and argument parsing only).

Run:  python -m pytest Tests/architecture/test_viewer_broker_boundary.py -q
"""
from __future__ import annotations

import ast
import glob
import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_VIZ = os.path.join(_ROOT, 'Visualization')

_SELECT = re.compile(r'\bSELECT\b.*\bFROM\b', re.S | re.I)

#: (file, enclosing function) -> why this raw SQL stays raw.  Every entry must still exist
#: (asserted below), so a refactor that removes one retires its exemption with it.
_ALLOWED_RAW = {
    ('Visualization/readers/base.py', 'run_meta'):
        'shape-following by design: the row IS the payload (ANY_COLUMNS in REQUIRES)',
    ('Visualization/readers/base.py', 'sku_scores'):
        'shape-following: output columns are the physical columns; scope is a json_each param',
    ('Visualization/readers/base.py', '_log_start'):
        'MIN(batch_id) doubles as the bin_log capability probe; memo shared with the fold',
    ('Visualization/readers/base.py', '_state_from_spans'):
        'algorithmic per-bin anchor fold over picks; scope fragment via _int_list',
    ('Visualization/readers/base.py', '_state_from_log'):
        'ordered log replay (EVICT/PLACE/picks); conditional tables gated on _has',
    ('Visualization/readers/base.py', '_apply_picks_upto_t'):
        'the intra-batch clock; shares the callers\' scope fragment',
    ('Visualization/readers/base.py', '_state_from_keyframes'):
        'depletion fold between keyframes',
    ('Visualization/readers/base.py', '_state_without_keyframes'):
        'archive-only bin_inventory last resort; ORDER BY batch_id, id is last-write-wins',
    ('Visualization/readers/base.py', 'sku_series'):
        'cache side is shape-following SELECT *; scope is a json_each param',
    ('Visualization/db_reader.py', '_read_run_meta'):
        'tolerant identity probe over files of ANY vintage, including ones then skipped',
    ('Visualization/cache_schema.py', 'cache_freshness'):
        'must answer on a file too broken to bind — trustworthiness is ITS question',
    ('Visualization/precompute.py', '_log_pass'):
        'streaming 2.2M-row pass, tuple cursor on ds.con (the documented escape hatch)',
    ('Visualization/precompute.py', '_keyframe_pass'):
        'streaming pass, same escape hatch',
    ('Visualization/precompute.py', '_picks_pass'):
        'streaming 3.1M-row fetchmany pass, same escape hatch',
    ('Visualization/precompute.py', 'build_one'):
        'per-arm scalar reads on bound connections (migrating to ds.read is P6)',
    # TRANSIENT — each retired by a named later phase; the staleness test below forces it.
    ('Visualization/precompute.py', 'log_present'):
        'TRANSIENT: becomes capability.has_rows in the precompute rebinding phase',
    ('Visualization/readers/fingerprint.py', 'read_stamped_id'):
        'TRANSIENT: the whole module is deleted in the final phase (identity unified in P1)',
    ('Visualization/readers/__init__.py', '_assert_json1'):
        'the JSON1 import probe: one SELECT against a throwaway :memory: db, no table read',
}

#: Call names that mark COMPOSED (broker-seam) execution inside a function.
_COMPOSED = re.compile(r'self\._sql\(|sql_for\(|\.query\(|\.read\(')


def _py_files():
    return [p for p in glob.glob(os.path.join(_VIZ, '**', '*.py'), recursive=True)
            if '__pycache__' not in p]


def _rel(path):
    return os.path.relpath(path, _ROOT).replace(os.sep, '/')


def _functions_with_raw_sql(path):
    """[(qualname, has_composed_marker)] for every function holding a raw SELECT literal."""
    with open(path, encoding='utf-8') as fh:
        src = fh.read()
    tree = ast.parse(src)
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                    and _SELECT.search(sub.value)):
                out.append(node.name)
                break
    return out


def test_every_raw_sql_site_is_allowlisted_with_a_reason():
    bad = []
    for path in _py_files():
        rel = _rel(path)
        for func in _functions_with_raw_sql(path):
            if (rel, func) not in _ALLOWED_RAW:
                bad.append(f'{rel}:{func}')
    assert not bad, ('raw SQL outside the allowlist — compose it from a publisher-side named '
                     'query (dataset.sql_for / ds.query) or add an allowlist entry WITH its '
                     f'reason: {bad}')


def test_the_allowlist_cannot_go_stale():
    """Every allowlisted (file, function) must still hold raw SQL — a migrated site retires
    its entry, and every entry carries a non-empty reason."""
    live = {(_rel(p), f) for p in _py_files() for f in _functions_with_raw_sql(p)}
    stale = set(_ALLOWED_RAW) - live
    assert not stale, f'allowlist entries whose raw SQL is gone — delete them: {sorted(stale)}'
    empty = [k for k, v in _ALLOWED_RAW.items() if not v.strip()]
    assert not empty, f'allowlist entries need real reasons: {empty}'


def test_no_direct_sqlite_connect_in_the_viewer():
    """Every connection goes through Schema.connect — the WAL-sidecar and threading rules
    live there once, not per call site."""
    bad = []
    for path in _py_files():
        with open(path, encoding='utf-8') as fh:
            src = fh.read()
        for i, line in enumerate(src.splitlines(), 1):
            if ('sqlite3.connect(' in line and not line.lstrip().startswith('#')
                    # The one sanctioned direct connect: the JSON1 probe's throwaway
                    # :memory: db, which mode=ro (read_only) cannot open by definition.
                    and ":memory:" not in line):
                bad.append(f'{_rel(path)}:{i}')
    assert not bad, f'direct sqlite3.connect in the viewer — use Schema.connect: {bad}'


def test_server_contains_no_sql():
    with open(os.path.join(_VIZ, 'server.py'), encoding='utf-8') as fh:
        src = fh.read()
    assert not _SELECT.search(src), 'server.py is routing only — SQL belongs behind the reader'


def test_the_scan_is_not_vacuous():
    assert ('Visualization/readers/base.py', '_state_from_log') in {
        (_rel(p), f) for p in _py_files() for f in _functions_with_raw_sql(p)}, (
        'the sweep no longer sees the log fold — its pattern rotted')
