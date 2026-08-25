"""
run_digest.py — content digests over a run's domain tables; the byte-identical gate.

Two identical-seed runs can never match as FILE bytes (simulation_runs.created is wall-clock,
sqlite page layout varies, manifests carry timestamps/paths). What CAN and must match after a
results-preserving refactor is the CONTENT of the domain tables. This tool defines that
surface precisely and hashes it:

  - table WHITELIST per DB kind (sim / keyframes / warehouse) — never a blacklist, and
    VERIFIED: a table present in the file but named by neither the whitelist nor
    `OUT_OF_SURFACE` raises. A whitelist nobody checks is how three tables went unhashed for
    a whole feature phase while this tool reported IDENTICAL;
  - excluded columns: AUTOINCREMENT `id`, `run_id` (per-file bookkeeping; the distinct-run
    count is recorded in meta instead), `created` (the one wall-clock column), and
    warehouse_stats' `timestamp`/`inventory_db` (clock + absolute path);
  - rows ordered by EVERY included column (a total order needing no schema knowledge —
    immune to rowid/write-cadence artifacts);
  - floats hashed as exact IEEE bytes (struct.pack '<d') — a re-associated sum trips the
    digest, which is the point;
  - DBs opened `mode=ro&immutable=1` so hashing never mints WAL sidecars (archive_cells
    precedent);
  - arms enumerated via the run's own contract (runschema resolver) — no path joins;
  - missing artifacts are symmetric: missing==missing passes, missing vs present fails.

Usage:
    python Tests/bench/run_digest.py <run_root_or_name>              # digest -> stdout + json
    python Tests/bench/run_digest.py <baseline> <candidate>          # compare; exit 1 on diff
    python Tests/bench/run_digest.py --self-test                     # planted-damage control

Run roots may be bare names (resolved under COMPARISON_OUTPUT_DIR) or absolute paths.
Digest JSONs are written next to nothing — stdout only — unless -o is given (keep outputs
out of the repo; they may embed run names).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import sys
import tempfile

_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))   # Tests/<sub>/ -> repo root
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ── the comparable surface ───────────────────────────────────────────────────
SIM_TABLES = ('simulation_runs', 'batch_stats', 'task_stats', 'picker_events', 'picks',
              'aisle_metrics', 'reorder_queue', 'bin_placement', 'bin_eviction',
              'bin_scores', 'sku_scores',
              # The second work stream and the put-away queues, added 2026-08-24/25 and
              # absent here until 2026-08-25 -- so a refactor touching put-away could pass
              # this gate while changing every row it wrote.
              'work_events', 'put_queue_state', 'carryover')
KEYFRAME_TABLES  = ('bin_keyframe',)
WAREHOUSE_TABLES = ('aisle_layout', 'aisle_type_stats', 'warehouse_stats')

#: Tables that exist in a file and are DELIBERATELY not part of the comparable surface.
#: Empty today, and that is the point: the check below turns "I forgot" into an error and
#: leaves "I decided" as a one-line edit with a reason attached.
OUT_OF_SURFACE: dict = {}

#: SQLite objects that are not tables at all. `work_events_merged` is a VIEW over
#: `work_events` and `picker_events`, so hashing it would double-count both and report a
#: difference twice for one cause.
NOT_A_TABLE = ('work_events_merged',)

EXCLUDED_COLS = {
    '*'              : {'id', 'run_id'},
    'simulation_runs': {'id', 'run_id', 'created'},
    'warehouse_stats': {'id', 'timestamp', 'inventory_db'},
}


def _canon(v) -> bytes:
    if v is None:
        return b'\x00N'
    if isinstance(v, float):
        return b'\x00F' + struct.pack('<d', v)
    if isinstance(v, int):
        return b'\x00I' + str(v).encode('ascii')
    if isinstance(v, str):
        return b'\x00S' + v.encode('utf-8')
    if isinstance(v, bytes):
        return b'\x00B' + v
    return b'\x00R' + repr(v).encode('utf-8')


def _table_digest(conn: sqlite3.Connection, table: str) -> dict:
    """{'sha': hex, 'rows': n, 'cols': [...]} for one table's canonical content."""
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    if not info:
        return {'sha': None, 'rows': 0, 'cols': [], 'missing_table': True}
    excluded = EXCLUDED_COLS.get(table, set()) | EXCLUDED_COLS['*']
    cols = [r[1] for r in info if r[1] not in excluded]
    col_list = ', '.join(f'"{c}"' for c in cols)
    order_by = ', '.join(str(i + 1) for i in range(len(cols)))
    h = hashlib.sha256()
    h.update(('|'.join(cols) + '\n').encode('utf-8'))
    n = 0
    cur = conn.execute(f'SELECT {col_list} FROM "{table}" ORDER BY {order_by}')
    for row in cur:
        for v in row:
            h.update(_canon(v))
        h.update(b'\x01')
        n += 1
    return {'sha': h.hexdigest(), 'rows': n, 'cols': cols}


def _open_ro(path: str) -> sqlite3.Connection:
    uri = 'file:' + path.replace('\\', '/').replace('?', '%3f') + '?mode=ro&immutable=1'
    return sqlite3.connect(uri, uri=True)


def _surface_check(conn, path: str, tables: tuple[str, ...]) -> None:
    """Every real table in the file must be declared, one way or the other.

    The whole value of a whitelist is that it is a DECISION about what counts. An
    undeclared table is not a decision, it is an omission — and an omission here is
    invisible, because a table nobody hashes can never differ. So it raises.

    Views are excluded by `type='table'` rather than by name, and the one view that exists
    is named in `NOT_A_TABLE` as well, so the two agree.
    """
    have = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    undeclared = have - set(tables) - set(OUT_OF_SURFACE) - set(NOT_A_TABLE)
    if undeclared:
        raise SystemExit(
            f'run_digest: {os.path.basename(path)} holds table(s) this gate does not know '
            f'about: {sorted(undeclared)}.\n'
            f'  A table that is not hashed can never differ, so leaving it out silently '
            f'weakens every IDENTICAL this tool has ever printed.\n'
            f'  Add it to the whitelist for this DB kind, or to OUT_OF_SURFACE with a '
            f'reason if its content is genuinely not comparable between two runs.')


def _digest_db(path: str, tables: tuple[str, ...]) -> dict:
    if not os.path.isfile(path):
        return {'missing': True}
    conn = _open_ro(path)
    try:
        _surface_check(conn, path, tables)
        out = {t: _table_digest(conn, t) for t in tables}
        if 'simulation_runs' in tables:
            out['_distinct_run_ids'] = conn.execute(
                'SELECT COUNT(DISTINCT run_id) FROM simulation_runs').fetchone()[0]
        return out
    finally:
        conn.close()


def _file_sha(path: str) -> dict:
    if not os.path.isfile(path):
        return {'missing': True}
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return {'sha': h.hexdigest(), 'bytes': os.path.getsize(path)}


# ── run-level digest via the contract ────────────────────────────────────────

def digest_run(base: str) -> dict:
    from Optimization.runschema import resolve_base_dir, resolver_for
    root = resolve_base_dir(base)
    rt = resolver_for(root)

    arms: dict[str, dict] = {}
    for cell, cr, sim_db in sorted(rt.sim_dbs(), key=lambda t: (t[0], t[2])):
        strategy = rt.strategy_of(sim_db) or os.path.basename(sim_db)
        key = '/'.join(filter(None, (cell, cr.pair, cr.config, cr.channel or '', strategy)))
        entry = {'sim': _digest_db(sim_db, SIM_TABLES)}
        kf = rt.keyframe_db(sim_db)
        entry['keyframes'] = _digest_db(kf, KEYFRAME_TABLES) if kf and os.path.isfile(kf) \
            else {'missing': True}
        arms[key] = entry

    extras: dict[str, dict] = {}
    for cell, _dir in rt.cells():
        seen_pairs: set[str] = set()
        for c2, cr, _db in rt.sim_dbs(cell=cell):
            if cr.pair in seen_pairs:
                continue
            seen_pairs.add(cr.pair)
            wh = rt.path('warehouse_db', cell=cell, pair=cr.pair)
            extras[f'{cell}/{cr.pair}/warehouse.db'] = _digest_db(wh, WAREHOUSE_TABLES)
            cfg = rt.config_json(cell, cr.pair, cr.config)
            extras[f'{cell}/{cr.pair}/{cr.config}/config.json'] = _file_sha(cfg)

    return {
        'schema': 'run-digest-v1',
        'python': sys.version,
        'run': os.path.basename(root),
        'arms': arms,
        'extras': extras,
    }


# ── compare ──────────────────────────────────────────────────────────────────

def compare(a: dict, b: dict) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    def diff_tree(pa: dict, pb: dict, prefix: str) -> None:
        nonlocal ok
        for k in sorted(set(pa) | set(pb)):
            va, vb = pa.get(k), pb.get(k)
            if isinstance(va, dict) or isinstance(vb, dict):
                ma = bool((va or {}).get('missing') or (va or {}).get('missing_table')) \
                    or va is None
                mb = bool((vb or {}).get('missing') or (vb or {}).get('missing_table')) \
                    or vb is None
                if ma and mb:
                    continue                          # symmetric absence passes
                if ma != mb:
                    ok = False
                    lines.append(f'  MISSING-MISMATCH {prefix}{k}: '
                                 f'{"absent" if ma else "present"} vs '
                                 f'{"absent" if mb else "present"}')
                    continue
                sa, sb = va.get('sha'), vb.get('sha')
                if sa is not None or sb is not None:
                    if sa != sb:
                        ok = False
                        ra, rb = va.get('rows'), vb.get('rows')
                        detail = f' (rows {ra} vs {rb})' if ra != rb else ''
                        lines.append(f'  DIFF {prefix}{k}{detail}')
                    continue
                diff_tree(va, vb, f'{prefix}{k}/')

    diff_tree(a['arms'], b['arms'], '')
    diff_tree(a['extras'], b['extras'], '')
    if a['python'].split()[0] != b['python'].split()[0]:
        lines.append(f"  NOTE python versions differ: {a['python'].split()[0]} vs "
                     f"{b['python'].split()[0]} — calibration only valid per interpreter")
    return ok, lines


# ── self-test: planted damage must be detected ───────────────────────────────

def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix='rundigest_') as td:
        p1 = os.path.join(td, 'a.db')
        p2 = os.path.join(td, 'b.db')
        p3 = os.path.join(td, 'c.db')
        for p in (p1, p2, p3):
            conn = sqlite3.connect(p)
            conn.executescript('''
                CREATE TABLE batch_stats (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER, batch_id INTEGER, duration REAL);''')
            rows = [(1, i, 100.0 + i * 0.5) for i in range(50)]
            if p is p3:
                rows[37] = (1, 37, rows[37][2] + 1e-9)     # the plant: one float, one ulp-ish
            conn.executemany('INSERT INTO batch_stats(run_id,batch_id,duration) '
                             'VALUES (?,?,?)', rows)
            conn.commit()
            conn.close()
        d1 = _digest_db(p1, ('batch_stats',))
        d2 = _digest_db(p2, ('batch_stats',))
        d3 = _digest_db(p3, ('batch_stats',))
        same = d1['batch_stats']['sha'] == d2['batch_stats']['sha']
        caught = d1['batch_stats']['sha'] != d3['batch_stats']['sha']
        missing_sym = _digest_db(os.path.join(td, 'nope.db'), ('x',)) == {'missing': True}
        print(f'identical copies match: {same}')
        print(f'planted 1e-9 float damage detected: {caught}')
        print(f'missing-file symmetric: {missing_sym}')
        if not (same and caught and missing_sym):
            print('SELF-TEST FAILED — a pass from this tool means nothing; fix it first')
            return 1
        print('self-test OK')
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='domain-table content digests for run trees')
    ap.add_argument('runs', nargs='*', help='one run (digest) or two (compare)')
    ap.add_argument('--self-test', action='store_true')
    ap.add_argument('-o', '--out', default=None, help='write digest/compare JSON here')
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.runs:
        ap.error('give a run root (digest) or two (compare), or --self-test')

    if len(args.runs) == 1:
        doc = digest_run(args.runs[0])
        n_tables = sum(1 for a in doc['arms'].values()
                       for t in a['sim'] if not t.startswith('_'))
        print(f"{doc['run']}: {len(doc['arms'])} arms, {n_tables} table digests")
        if args.out:
            with open(args.out, 'w', encoding='utf-8') as fh:
                json.dump(doc, fh, indent=1)
            print(f'wrote {args.out}')
        else:
            json.dump(doc, sys.stdout, indent=1)
            print()
        return 0

    a = digest_run(args.runs[0])
    b = digest_run(args.runs[1])
    ok, lines = compare(a, b)
    print(f"baseline : {a['run']} ({len(a['arms'])} arms)")
    print(f"candidate: {b['run']} ({len(b['arms'])} arms)")
    for ln in lines:
        print(ln)
    print(f'result: {"IDENTICAL on the comparable surface" if ok else "DIFFERS"}')
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            json.dump({'baseline': a, 'candidate': b, 'identical': ok}, fh, indent=1)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
