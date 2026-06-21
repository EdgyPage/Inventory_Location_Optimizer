"""replay_run.py — export a real simulation run's persisted DBs to a dashboard trace.

Reads the SQLite output of a finished `run_simulation.py` run and emits a
Diagnostics/out/replay_<label>.json with per-batch warehouse fill (overall, by
aisle, by bucket) so the SAME dashboard can visualise the actual misbehaving run
— including the warehouses that settle at ~70% instead of the target ~85%.

Per Visualization/RECONSTRUCTION.md the relevant files are:
    <pair>/warehouse.db            aisle_layout   (geometry -> per-aisle capacity)
    <pair>/<config>/sim_X.db       aisle_metrics  (n_bins occupied / aisle / batch)
                                   bin_inventory  (universal fallback)
                                   batch_stats    (duration / batch)
                                   simulation_runs (run params)

Occupied bins come from `aisle_metrics.n_bins` when present (affinity strategies);
otherwise they are reconstructed from `bin_inventory` deltas (works for any run).

Usage
-----
    python Diagnostics/replay_run.py <run_base_dir>
    python Diagnostics/replay_run.py <run_base_dir>/<pair>/<config>/sim_uni_fifo_norsl.db
    python Diagnostics/replay_run.py <run_base_dir> --max 200     # cap exported batches

Then view with the dashboard:
    cd Diagnostics && python -m http.server 8009  -> http://localhost:8009/static/
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
from collections import defaultdict

_HERE    = os.path.dirname(os.path.abspath(__file__))
_OUT_DIR = os.path.join(_HERE, 'out')
_GRID_COLS = 6


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has_rows(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return conn.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone() is not None
    except sqlite3.OperationalError:
        return False


# ── warehouse geometry ─────────────────────────────────────────────────────────

def read_layout(warehouse_db: str) -> tuple[list[dict], dict[int, int]]:
    conn = _connect(warehouse_db)
    rows = conn.execute(
        'SELECT aisle_id, handling_type, category, unit_type, storage_size, '
        'bay_x, bay_y FROM aisle_layout ORDER BY aisle_id').fetchall()
    conn.close()
    aisles, capacity = [], {}
    for idx, r in enumerate(rows):
        cap = (r['bay_x'] or 0) * (r['bay_y'] or 0)
        capacity[r['aisle_id']] = cap
        aisles.append({
            'aisle_id': r['aisle_id'],
            'handling': r['handling_type'],
            'category': r['category'],
            'unit_type': r['unit_type'],
            'bucket': f"{r['handling_type']}|{r['category']}|{r['storage_size']}|{r['unit_type']}",
            'capacity': cap,
            'grid_col': idx % _GRID_COLS,
            'grid_row': idx // _GRID_COLS,
        })
    return aisles, capacity


# ── occupied bins per (batch, aisle) ───────────────────────────────────────────

def occupied_from_aisle_metrics(conn, run_id) -> dict[int, dict[int, int]]:
    rows = conn.execute(
        'SELECT batch_id, aisle_id, n_bins FROM aisle_metrics WHERE run_id=? '
        'ORDER BY batch_id', (run_id,)).fetchall()
    out: dict[int, dict[int, int]] = defaultdict(dict)
    for r in rows:
        out[r['batch_id']][r['aisle_id']] = r['n_bins']
    return out


def occupied_from_bin_inventory(conn, run_id) -> dict[int, dict[int, int]]:
    """Roll bin_inventory deltas (full snapshot at batch 0 + changed bins after) into
    an occupied-bin count per aisle at the end of each recorded batch."""
    rows = conn.execute(
        'SELECT batch_id, aisle_id, bayX, bayY, post_qty FROM bin_inventory '
        'WHERE run_id=? ORDER BY batch_id', (run_id,)).fetchall()
    state: dict[tuple, tuple[int, int]] = {}     # (aisle,bayX,bayY) -> (aisle_id, qty)
    out: dict[int, dict[int, int]] = {}
    batch_rows: dict[int, list] = defaultdict(list)
    for r in rows:
        batch_rows[r['batch_id']].append(r)
    for batch in sorted(batch_rows):
        for r in batch_rows[batch]:
            state[(r['aisle_id'], r['bayX'], r['bayY'])] = (r['aisle_id'], r['post_qty'])
        occ: dict[int, int] = defaultdict(int)
        for aid, qty in state.values():
            if qty and qty > 0:
                occ[aid] += 1
        out[batch] = dict(occ)
    return out


# ── per-batch records ───────────────────────────────────────────────────────────

def build_batches(occ_by_batch, capacity, aisles, durations, max_batches) -> list[dict]:
    bucket_of = {a['aisle_id']: a['bucket'] for a in aisles}
    total_cap = sum(capacity.values()) or 1
    batches = []
    for batch in sorted(occ_by_batch)[: max_batches or None]:
        occ = occ_by_batch[batch]
        fill_by_aisle, b_occ, b_cap = {}, defaultdict(int), defaultdict(int)
        total_occ = 0
        for a in aisles:
            aid = a['aisle_id']; cap = capacity.get(aid, 0) or 1
            o = occ.get(aid, 0); total_occ += o
            fill_by_aisle[aid] = round(o / cap, 4)
            b_occ[bucket_of[aid]] += o
            b_cap[bucket_of[aid]] += capacity.get(aid, 0)
        rec = {
            'batch': batch,
            'fill_overall': round(total_occ / total_cap, 4),
            'fill_by_aisle': fill_by_aisle,
            'fill_by_bucket': {k: round(b_occ[k] / (b_cap[k] or 1), 4) for k in b_cap},
        }
        if batch in durations:
            rec['duration'] = durations[batch]
        batches.append(rec)
    return batches


def replay_sim_db(sim_db: str, warehouse_db: str, max_batches: int) -> dict:
    aisles, capacity = read_layout(warehouse_db)
    conn = _connect(sim_db)
    run = conn.execute('SELECT * FROM simulation_runs ORDER BY run_id LIMIT 1').fetchone()
    run_id = run['run_id']
    run_type = run['run_type'] if 'run_type' in run.keys() else os.path.basename(sim_db)

    durations = {r['batch_id']: r['duration']
                 for r in conn.execute(
                     'SELECT batch_id, duration FROM batch_stats WHERE run_id=?',
                     (run_id,)).fetchall()} if _has_rows(conn, 'batch_stats') else {}

    if _has_rows(conn, 'aisle_metrics'):
        occ = occupied_from_aisle_metrics(conn, run_id)
        src = 'aisle_metrics'
    elif _has_rows(conn, 'bin_inventory'):
        occ = occupied_from_bin_inventory(conn, run_id)
        src = 'bin_inventory'
    else:
        conn.close()
        raise SystemExit(f'{sim_db}: neither aisle_metrics nor bin_inventory has rows.')
    conn.close()

    batches = build_batches(occ, capacity, aisles, durations, max_batches)
    if not batches:
        raise SystemExit(f'{sim_db}: no per-batch fill could be derived.')
    print(f'  {os.path.basename(sim_db)}: run_id={run_id} type={run_type}  '
          f'{len(batches)} batches via {src}  final fill={batches[-1]["fill_overall"]:.1%}')
    return {
        'meta': {'source': 'replay', 'strategy': str(run_type),
                 'label': str(run_type), 'n_skus': None,
                 'total_bins': sum(capacity.values()), 'target_fill': None,
                 'n_batches': len(batches), 'occupancy_source': src},
        'warehouse': {'aisles': aisles, 'grid_cols': _GRID_COLS},
        'batches': batches,
    }


# ── discovery ───────────────────────────────────────────────────────────────────

def _nearest_warehouse_db(sim_db: str) -> str | None:
    d = os.path.dirname(os.path.abspath(sim_db))
    for _ in range(4):
        cand = os.path.join(d, 'warehouse.db')
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    return None


def discover_sim_dbs(target: str) -> list[str]:
    if target.endswith('.db'):
        return [target]
    return sorted(glob.glob(os.path.join(target, '**', 'sim_*.db'), recursive=True))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', help='run base dir, or a single sim_*.db path')
    ap.add_argument('--max', type=int, default=0, help='cap exported batches (0 = all)')
    args = ap.parse_args()

    sim_dbs = discover_sim_dbs(args.target)
    if not sim_dbs:
        raise SystemExit(f'No sim_*.db found under {args.target}')

    os.makedirs(_OUT_DIR, exist_ok=True)
    manifest = []
    for sim_db in sim_dbs:
        wh = _nearest_warehouse_db(sim_db)
        if wh is None:
            print(f'  SKIP {sim_db}: no warehouse.db found nearby'); continue
        result = replay_sim_db(sim_db, wh, args.max)
        key = os.path.splitext(os.path.basename(sim_db))[0]   # sim_<key>
        fname = f'replay_{key}.json'
        with open(os.path.join(_OUT_DIR, fname), 'w') as f:
            json.dump(result, f)
        manifest.append({'file': fname, 'strategy': result['meta']['strategy'],
                         'label': result['meta']['label'], 'stock_mode': 'replay',
                         'final_fill': result['batches'][-1]['fill_overall']})

    # Merge into any existing manifest (so traces + replays share the run switcher).
    mpath = os.path.join(_OUT_DIR, 'manifest.json')
    existing = []
    if os.path.exists(mpath):
        try:
            existing = json.load(open(mpath)).get('runs', [])
        except (OSError, ValueError):
            existing = []
    files = {r['file'] for r in manifest}
    merged = manifest + [r for r in existing if r['file'] not in files]
    with open(mpath, 'w') as f:
        json.dump({'runs': merged}, f, indent=2)
    print(f'\nWrote {len(manifest)} replay(s) to {_OUT_DIR}; manifest has {len(merged)} run(s).')


if __name__ == '__main__':
    main()
