"""test_diagnostics_read_boundary.py — Diagnostics joins the ratchet Visualization lives under.

The read seam is half-adopted BY PACKAGE. `Performance_Evaluations/` has zero raw SQL;
`Visualization/` has its raw sites under a shrink-only allowlist with a reason each
(`test_viewer_broker_boundary.py`); **`Diagnostics/` was unmanaged** -- twenty-four raw SELECT
literals across ten functions, a hand-rolled read-only URI instead of `Schema.connect.read_only`,
and a hand-rolled `sqlite_master` probe instead of `Schema.capability`, while its own prose
documents the `dataset.override` pattern and binds nothing.

This file is the ratchet, in the shape the viewer's has: the sites that exist are listed, the
list may only SHRINK, and an entry whose SQL is gone must be deleted with it.

## WHY ONE SHARED REASON AND NOT TEN

The viewer's allowlist carries a specific justification per site, because each was argued
individually as that package was migrated. These ten were not: they are what the package held on
the day the ratchet was installed. Writing ten invented justifications would be worse than none
-- it would read as ten decisions when it is one fact -- so the entries share the honest one, and
the two STRUCTURAL defects the ticket found get named assertions of their own below.

A site that earns a real reason gets it when someone migrates the rest and argues for keeping it.

Run:  python -m pytest Tests/architecture/test_diagnostics_read_boundary.py -q
"""
from __future__ import annotations

import ast
import glob
import os
import re

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_PKG = os.path.join(_ROOT, 'Diagnostics')

_SELECT = re.compile(r'\bSELECT\b.*\bFROM\b', re.S | re.I)

_UNMIGRATED = 'unmigrated when the ratchet was installed (ticket 15); shrink-only'

#: (file, enclosing function) -> why this raw SQL is still raw. Ten entries, one fact.
_ALLOWED_RAW = {
    ('Diagnostics/receiving_report.py', '_has'): _UNMIGRATED,
    ('Diagnostics/receiving_report.py', '_leaf_state'): _UNMIGRATED,
    ('Diagnostics/receiving_report.py', '_site_totals'): _UNMIGRATED,
    ('Diagnostics/receiving_report.py', '_unload_price'): _UNMIGRATED,
    ('Diagnostics/receiving_report.py', 'reconcile'): _UNMIGRATED,
    ('Diagnostics/replay_run.py', 'occupied_from_aisle_metrics'): _UNMIGRATED,
    ('Diagnostics/replay_run.py', 'occupied_from_bin_inventory'): _UNMIGRATED,
    ('Diagnostics/replay_run.py', 'occupied_from_bin_log'): _UNMIGRATED,
    ('Diagnostics/replay_run.py', 'read_layout'): _UNMIGRATED,
    ('Diagnostics/replay_run.py', 'replay_sim_db'): _UNMIGRATED,
}


def _py_files():
    return [p for p in glob.glob(os.path.join(_PKG, '**', '*.py'), recursive=True)
            if '__pycache__' not in p]


def _rel(path):
    return os.path.relpath(path, _ROOT).replace(os.sep, '/')


def _functions_with_raw_sql(path):
    tree = ast.parse(open(path, encoding='utf-8').read())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(isinstance(s, ast.Constant) and isinstance(s.value, str)
               and _SELECT.search(s.value) for s in ast.walk(node)):
            out.append(node.name)
    return out


def _live_sites():
    return {(_rel(p), f) for p in _py_files() for f in _functions_with_raw_sql(p)}


# ── the ratchet ───────────────────────────────────────────────────────────────────

def test_no_new_raw_sql_site_appears_in_diagnostics():
    """The whole point: the set may shrink, never grow.

    A new raw read here escapes the family's declared surface AND the compatibility sweep --
    it is the shape that let `task_stats.items_realized` be written-but-unreadable for months
    (ticket 11) without anything saying so.
    """
    new = sorted(_live_sites() - set(_ALLOWED_RAW))
    assert not new, (
        f'new raw SQL in Diagnostics: {new}. Compose it from a publisher-side named query '
        f'(`dataset.sql_for` / `ds.query`) or bind the family with a `Requires`; the allowlist '
        f'is shrink-only and a new entry needs the argument made, not assumed.')


def test_the_allowlist_cannot_go_stale():
    """A migrated site retires its exemption with it -- otherwise the list records the past."""
    stale = sorted(set(_ALLOWED_RAW) - _live_sites())
    assert not stale, f'allowlist entries whose raw SQL is gone -- delete them: {stale}'
    empty = [k for k, v in _ALLOWED_RAW.items() if not v.strip()]
    assert not empty, f'allowlist entries need reasons: {empty}'


def test_the_ratchet_is_not_vacuous():
    """NON-VACUITY. An empty package would make every assertion above compare empty sets, and a
    ratchet installed over nothing reads exactly like a ratchet holding."""
    assert len(_live_sites()) >= 8, (
        f'only {len(_live_sites())} raw site(s) found -- either the package migrated (delete the '
        f'entries and lower this floor deliberately) or the detector stopped detecting')


# ── the two structural defects, named ─────────────────────────────────────────────

def test_the_hand_rolled_read_only_uri_is_recorded_not_forgotten():
    """`Diagnostics/receiving_report.py` assembles `file:...?mode=ro&immutable=1` by hand
    instead of calling `Schema.connect.read_only`.

    Recorded rather than silently fixed, because the two are NOT trivially interchangeable here
    and the difference matters: memory `wal-sidecars-come-from-readers` -- a `mode=ro` open
    creates `-wal`/`-shm` beside an archived DB and cannot remove them, so which opener is used
    decides what an archive looks like afterwards. This asserts the site is where the ratchet
    says it is, so a migration is a deliberate edit here rather than a diff someone skims.
    """
    src = open(os.path.join(_PKG, 'receiving_report.py'), encoding='utf-8').read()
    hand_rolled = 'mode=ro&immutable=1' in src
    sanctioned = 'connect.read_only' in src
    assert hand_rolled != sanctioned, (
        'receiving_report.py now both hand-rolls a read-only URI and calls connect.read_only, '
        'or neither -- one opener, and if it migrated, delete this test with the URI')


def test_the_hand_rolled_capability_probes_are_recorded():
    """Two probes that duplicate `Schema.capability`: a `sqlite_master` lookup in
    `receiving_report.py` and a `PRAGMA table_info` in `replay_run.py`.

    Both answer "does this vintage carry that?", which is exactly what the capability layer is
    for -- and a probe outside it is one the compatibility sweep cannot see.
    """
    rr = open(os.path.join(_PKG, 'receiving_report.py'), encoding='utf-8').read()
    rp = open(os.path.join(_PKG, 'replay_run.py'), encoding='utf-8').read()
    assert 'sqlite_master' in rr or 'capability' in rr, (
        'receiving_report.py lost its sqlite_master probe -- if it moved to Schema.capability, '
        'delete this assertion rather than leaving it describing the past')
    assert 'PRAGMA table_info' in rp or 'capability' in rp, (
        'replay_run.py lost its PRAGMA probe -- same: retire the assertion with the code')
