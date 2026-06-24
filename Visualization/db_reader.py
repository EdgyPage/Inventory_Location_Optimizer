"""db_reader.py — read-only reconstruction of a finished run for the replay viewer.

No live sim / no warehouse re-plan: everything is reconstructed from the persisted
SQLite DBs per Visualization/RECONSTRUCTION.md.  Sim DBs are large (~550 MB), so every
query is scoped to a single (run, batch) via the existing indexes — never a full scan.

Public API
----------
discover_runs(base_dir)                 -> list[RunRef]
read_geometry(run)                      -> {aisles:[...], grid_cols}
reconstruct_batch(run, batch)           -> {batch, active_aisles:[...], aisle_geom:{id:{...}},
                                            bins:{key:{sku,qty}}, events:[...],
                                            reorder_queue:[...], timing:{...}}
reconstruct_overview(run, batch)        -> {aisles:[per-aisle aggregates], picker_paths:[...]}
reconstruct_aisle(run, batch, aisle)    -> {aisle_id, geom:{...}, bins:{...}, events:[...]}
bin_scores(sim_db, warehouse_db, run_id)-> {key: score}   (cached, static layout cost)
"""
from __future__ import annotations

import math
import os
import sqlite3
import sys
from dataclasses import dataclass, asdict
from functools import lru_cache

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, 'Warehouse'), os.path.join(_ROOT, 'Optimization')):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from Aisle_Dimensions import unit_bin_width, SIZE_HEIGHTS, SINGLETON_BIN_HEIGHT   # noqa: E402
from cost_model import sec_per_inch, height_multiplier, DEFAULT_HEIGHT_BRACKETS   # noqa: E402

from Picking_Data import load_reorder_queue   # noqa: E402

_GRID_COLS = 6


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ── run discovery ────────────────────────────────────────────────────────────────

@dataclass
class RunRef:
    id: str            # stable id: "<pair>/<config>/<strategy>"
    label: str
    pair: str
    config: str
    strategy: str
    sim_db: str
    warehouse_db: str
    keyframe_db: str
    run_id: int
    n_batches: int


def _nearest_warehouse_db(sim_db: str) -> str | None:
    d = os.path.dirname(os.path.abspath(sim_db))
    for _ in range(4):
        cand = os.path.join(d, 'warehouse.db')
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    return None


def discover_runs(base_dir: str) -> list[RunRef]:
    """Walk <base>/<pair>/<config>/sim_*.db and return one RunRef per strategy run."""
    runs: list[RunRef] = []
    if not os.path.isdir(base_dir):
        return runs
    for pair in sorted(os.listdir(base_dir)):
        pair_dir = os.path.join(base_dir, pair)
        if not os.path.isdir(pair_dir) or pair.startswith('_'):
            continue
        wh = os.path.join(pair_dir, 'warehouse.db')
        if not os.path.exists(wh):
            continue
        for config in sorted(os.listdir(pair_dir)):
            cfg_dir = os.path.join(pair_dir, config)
            if not os.path.isdir(cfg_dir):
                continue
            for fn in sorted(os.listdir(cfg_dir)):
                if not (fn.startswith('sim_') and fn.endswith('.db')) or fn.endswith('.keyframes.db'):
                    continue
                sim_db = os.path.join(cfg_dir, fn)
                strategy = fn[4:-3]
                try:
                    conn = _ro(sim_db)
                    row = conn.execute(
                        'SELECT run_id, n_batches FROM simulation_runs ORDER BY run_id LIMIT 1'
                    ).fetchone()
                    conn.close()
                except sqlite3.OperationalError:
                    continue
                if row is None:
                    continue
                kf = os.path.splitext(sim_db)[0] + '.keyframes.db'
                runs.append(RunRef(
                    id=f'{pair}/{config}/{strategy}',
                    label=f'{pair} · {config} · {strategy}',
                    pair=pair, config=config, strategy=strategy,
                    sim_db=sim_db, warehouse_db=wh,
                    keyframe_db=kf if os.path.exists(kf) else '',
                    run_id=int(row['run_id']),
                    n_batches=int(row['n_batches']) if 'n_batches' in row.keys() and row['n_batches'] else 0,
                ))
    return runs


def run_summaries(base_dir: str) -> list[dict]:
    return [{'id': r.id, 'label': r.label, 'pair': r.pair, 'config': r.config,
             'strategy': r.strategy, 'n_batches': r.n_batches} for r in discover_runs(base_dir)]


# ── geometry ─────────────────────────────────────────────────────────────────────

def read_geometry(run: RunRef) -> dict:
    """Full aisle + bin grid from aisle_layout.  Every bin in an aisle shares unit_type/
    storage_size, so the grid (incl. empty bins) is generated from bay_x × bay_y."""
    conn = _ro(run.warehouse_db)
    rows = conn.execute(
        'SELECT aisle_id, handling_type, category, unit_type, storage_size, bay_x, bay_y '
        'FROM aisle_layout ORDER BY aisle_id').fetchall()
    conn.close()
    aisles = []
    for idx, r in enumerate(rows):
        bx, by = int(r['bay_x'] or 0), int(r['bay_y'] or 0)
        bins = [{'x': cx, 'y': cy, 'size': r['storage_size'],
                 'key': f"{r['aisle_id']},{cx},{cy}"}
                for cy in range(1, by + 1) for cx in range(1, bx + 1)]
        aisles.append({
            'aisle_id': int(r['aisle_id']),
            'handling_type': r['handling_type'], 'storage_type': r['category'],
            'unit_type': r['unit_type'], 'storage_size': r['storage_size'],
            'bay_x': bx, 'bay_y': by,
            'grid_col': idx % _GRID_COLS, 'grid_row': idx // _GRID_COLS,
            'bins': bins,
        })
    return {'aisles': aisles, 'grid_cols': _GRID_COLS}


# ── bin state reconstruction ──────────────────────────────────────────────────────

def _keyframe_interval(run: RunRef) -> int:
    conn = _ro(run.sim_db)
    try:
        row = conn.execute(
            'SELECT keyframe_interval FROM simulation_runs WHERE run_id=?', (run.run_id,)
        ).fetchone()
        k = int(row['keyframe_interval']) if row and row['keyframe_interval'] else 0
    except sqlite3.OperationalError:
        k = 0
    finally:
        conn.close()
    return k


def _state_at_batch_start(run: RunRef, batch: int, aisles: set[int] | None = None) -> dict[str, dict]:
    """Occupied-bin {key: {sku, qty}} at the START of `batch`.  Nearest keyframe then roll
    bin_inventory post_qty deltas for batches [kf, batch-1].  Optionally scope to `aisles`
    (the active pick aisles) so the payload stays small on 157-aisle warehouses."""
    state: dict[str, dict] = {}
    k = _keyframe_interval(run)
    kf = (batch // k) * k if k else 0
    in_clause = ''
    if aisles:
        ids = ','.join(str(int(a)) for a in aisles)
        in_clause = f' AND aisle_id IN ({ids})'
    if run.keyframe_db and kf >= 0:
        try:
            kconn = _ro(run.keyframe_db)
            for r in kconn.execute(
                f'SELECT aisle_id, bayX, bayY, sku, qty FROM bin_keyframe '
                f'WHERE run_id=? AND batch_id=?{in_clause}', (run.run_id, kf)).fetchall():
                if r['qty'] > 0:
                    state[f"{r['aisle_id']},{r['bayX']},{r['bayY']}"] = {'sku': r['sku'], 'qty': r['qty']}
            kconn.close()
        except sqlite3.OperationalError:
            kf = 0
            state = {}
    lo = kf if state or run.keyframe_db else 0
    conn = _ro(run.sim_db)
    for r in conn.execute(
        f'SELECT aisle_id, bayX, bayY, sku, post_qty FROM bin_inventory '
        f'WHERE run_id=? AND batch_id>=? AND batch_id<?{in_clause} ORDER BY batch_id',
        (run.run_id, lo, batch)).fetchall():
        key = f"{r['aisle_id']},{r['bayX']},{r['bayY']}"
        if r['post_qty'] > 0:
            state[key] = {'sku': r['sku'], 'qty': r['post_qty']}
        else:
            state.pop(key, None)
    conn.close()
    return state


def _aisle_geom(run: RunRef, aisle_ids) -> dict:
    """{aisle_id: {bay_x, bay_y, unit_type, storage_size, ...}} for the given aisles — used
    so the frontend can draw the full bin grid (including empty bins) for active aisles."""
    ids = [int(a) for a in aisle_ids]
    if not ids:
        return {}
    in_clause = ','.join(str(a) for a in ids)
    conn = _ro(run.warehouse_db)
    rows = conn.execute(
        f'SELECT aisle_id, unit_type, storage_size, bay_x, bay_y, handling_type, category '
        f'FROM aisle_layout WHERE aisle_id IN ({in_clause})').fetchall()
    conn.close()
    return {int(r['aisle_id']): {
        'bay_x': int(r['bay_x'] or 0), 'bay_y': int(r['bay_y'] or 0),
        'unit_type': r['unit_type'], 'storage_size': r['storage_size'],
        'handling_type': r['handling_type'], 'storage_type': r['category'],
    } for r in rows}


def reconstruct_batch(run: RunRef, batch: int) -> dict:
    """Everything the viewer needs to play one batch, scoped to the ACTIVE pick aisles:
    bin state at batch start, the timed picker events, per-aisle geometry, the reorder-queue
    snapshot, timing.  (The frontend highlights restock by diffing consecutive batches; the
    reorder_queue table carries the queue contents — no expensive per-bin restock here.)"""
    conn = _ro(run.sim_db)
    active = sorted({int(r['aisle_id']) for r in conn.execute(
        'SELECT DISTINCT aisle_id FROM picker_events '
        'WHERE run_id=? AND batch_id=? AND aisle_id IS NOT NULL',
        (run.run_id, batch)).fetchall()})

    events = [
        {'time': round(r['time'], 4), 'picker_id': r['picker_id'],
         'event_type': r['event_type'], 'aisle_id': r['aisle_id'],
         'location': ([r['aisle_id'], r['bayX'], r['bayY']]
                      if r['aisle_id'] is not None and r['bayX'] is not None else None),
         'sku': r['sku'], 'quantity': r['quantity'],
         'bins_completed': r['bins_completed'], 'total_bins': r['total_bins'],
         'items_picked': r['items_picked'], 'total_items': r['total_items']}
        for r in conn.execute(
            'SELECT * FROM picker_events WHERE run_id=? AND batch_id=? ORDER BY time, id',
            (run.run_id, batch)).fetchall()
    ]
    n_pickers = (max((e['picker_id'] for e in events), default=-1) + 1) if events else 0
    max_time = max((e['time'] for e in events), default=0.0)

    brow = conn.execute(
        'SELECT duration, batch_start_time, batch_end_time, queue_depth, '
        'lead_queue_depth, in_transit_qty FROM batch_stats WHERE run_id=? AND batch_id=?',
        (run.run_id, batch)).fetchone()
    timing = (dict(brow) if brow else {})
    conn.close()

    start_bins = _state_at_batch_start(run, batch, aisles=set(active))
    queue = load_reorder_queue(run.sim_db, run.run_id, batch)
    return {
        'batch': batch, 'n_pickers': n_pickers, 'max_time': round(max_time, 4),
        'active_aisles': active, 'aisle_geom': _aisle_geom(run, active),
        'bins': start_bins, 'events': events,
        'reorder_queue': queue, 'timing': timing,
    }


# ── downsampled views (canvas: overview heatmap + per-aisle drill-in) ─────────────

def _picker_paths(events: list[dict]) -> list[dict]:
    """Aisle-level picker trajectories for the overview: each picker's ordered
    (aisle_id, t) waypoints from 'arrive' events — cheap (~one per task)."""
    paths: dict[int, list] = {}
    for e in events:
        if e['event_type'] == 'arrive' and e['aisle_id'] is not None:
            paths.setdefault(e['picker_id'], []).append(
                {'aisle_id': e['aisle_id'], 'bayX': e['location'][1], 'bayY': e['location'][2],
                 't': e['time']})
    return [{'picker_id': pid, 'waypoints': wp} for pid, wp in sorted(paths.items())]


def reconstruct_overview(run: RunRef, batch: int) -> dict:
    """Per-aisle aggregates for the zoomed-out heatmap (157 cells = cheap to render),
    plus aisle-level picker paths.  Full bin state is reconstructed once to count
    occupancy per aisle; detail bins are fetched lazily per aisle (reconstruct_aisle)."""
    geom = read_geometry(run)
    cap = {a['aisle_id']: a['bay_x'] * a['bay_y'] for a in geom['aisles']}
    state = _state_at_batch_start(run, batch)          # whole warehouse, occupied bins
    occ: dict[int, int] = {}
    for key, info in state.items():
        aid = int(key.split(',', 1)[0])
        occ[aid] = occ.get(aid, 0) + 1

    conn = _ro(run.sim_db)
    active = sorted({int(r['aisle_id']) for r in conn.execute(
        'SELECT DISTINCT aisle_id FROM picker_events '
        'WHERE run_id=? AND batch_id=? AND aisle_id IS NOT NULL', (run.run_id, batch)).fetchall()})
    picks = {int(r['aisle_id']): r['n'] for r in conn.execute(
        "SELECT aisle_id, COUNT(*) n FROM picker_events "
        "WHERE run_id=? AND batch_id=? AND event_type='pick' GROUP BY aisle_id",
        (run.run_id, batch)).fetchall()}
    events = [
        {'time': round(r['time'], 4), 'picker_id': r['picker_id'], 'event_type': r['event_type'],
         'aisle_id': r['aisle_id'],
         'location': ([r['aisle_id'], r['bayX'], r['bayY']] if r['bayX'] is not None else None)}
        for r in conn.execute(
            "SELECT picker_id, time, event_type, aisle_id, bayX, bayY FROM picker_events "
            "WHERE run_id=? AND batch_id=? AND event_type='arrive' ORDER BY time",
            (run.run_id, batch)).fetchall()
    ]
    brow = conn.execute(
        'SELECT duration, queue_depth, lead_queue_depth, in_transit_qty FROM batch_stats '
        'WHERE run_id=? AND batch_id=?', (run.run_id, batch)).fetchone()
    max_time = conn.execute(
        'SELECT MAX(time) m FROM picker_events WHERE run_id=? AND batch_id=?',
        (run.run_id, batch)).fetchone()['m'] or 0.0
    conn.close()

    active_set = set(active)
    aisles = [{
        'aisle_id': a['aisle_id'], 'grid_col': a['grid_col'], 'grid_row': a['grid_row'],
        'handling_type': a['handling_type'], 'storage_type': a['storage_type'],
        'unit_type': a['unit_type'], 'bay_x': a['bay_x'], 'bay_y': a['bay_y'],
        'capacity': cap[a['aisle_id']],
        'occupied': occ.get(a['aisle_id'], 0),
        'fill': round(occ.get(a['aisle_id'], 0) / (cap[a['aisle_id']] or 1), 4),
        'picks': picks.get(a['aisle_id'], 0),
        'active': a['aisle_id'] in active_set,
    } for a in geom['aisles']]
    return {
        'batch': batch, 'grid_cols': geom['grid_cols'], 'aisles': aisles,
        'picker_paths': _picker_paths(events), 'max_time': round(max_time, 4),
        'reorder_queue': load_reorder_queue(run.sim_db, run.run_id, batch),
        'timing': (dict(brow) if brow else {}),
    }


def reconstruct_aisle(run: RunRef, batch: int, aisle_id: int) -> dict:
    """Full bin detail + bin-level timed events for ONE aisle (the drill-in canvas).  Includes
    the aisle geometry so the grid shows every bin (incl. empty) at exact extents."""
    bins = {k: v for k, v in _state_at_batch_start(run, batch, aisles={aisle_id}).items()}
    conn = _ro(run.sim_db)
    events = [
        {'time': round(r['time'], 4), 'picker_id': r['picker_id'], 'event_type': r['event_type'],
         'location': [r['aisle_id'], r['bayX'], r['bayY']] if r['bayX'] is not None else None,
         'sku': r['sku'], 'quantity': r['quantity']}
        for r in conn.execute(
            'SELECT * FROM picker_events WHERE run_id=? AND batch_id=? AND aisle_id=? ORDER BY time, id',
            (run.run_id, batch, aisle_id)).fetchall()
    ]
    conn.close()
    geom = _aisle_geom(run, {aisle_id}).get(aisle_id, {})
    return {'batch': batch, 'aisle_id': aisle_id, 'bins': bins, 'events': events, 'geom': geom}


# ── static per-bin layout score (cached) ──────────────────────────────────────────

@lru_cache(maxsize=64)
def bin_scores(sim_db: str, warehouse_db: str, run_id: int) -> dict:
    """Per-bin static layout cost = travel D + golden-zone height penalty, from geometry +
    run speeds.  Lower = cheaper to pick.  Computed once per run (O(bins)), cached."""
    conn = _ro(sim_db)
    row = conn.execute(
        'SELECT x_speed, y_speed, pick_intercept FROM simulation_runs WHERE run_id=?',
        (run_id,)).fetchone()
    conn.close()
    x_speed = float(row['x_speed']) if row and 'x_speed' in row.keys() else 4.0
    y_speed = float(row['y_speed']) if row and 'y_speed' in row.keys() else 2.0
    xp, yp = sec_per_inch(x_speed), sec_per_inch(y_speed)

    wconn = _ro(warehouse_db)
    rows = wconn.execute(
        'SELECT aisle_id, unit_type, storage_size, bay_x, bay_y FROM aisle_layout').fetchall()
    wconn.close()
    scores: dict[str, float] = {}
    for r in rows:
        ut, ss = r['unit_type'], r['storage_size']
        x_step = unit_bin_width(ut)
        y_step = SINGLETON_BIN_HEIGHT if ut == 'singleton' else SIZE_HEIGHTS.get(ss, SINGLETON_BIN_HEIGHT)
        for cy in range(1, int(r['bay_y'] or 0) + 1):
            y_phys = (cy - 1) * y_step + y_step // 2
            m = height_multiplier(DEFAULT_HEIGHT_BRACKETS, y_phys)
            for cx in range(1, int(r['bay_x'] or 0) + 1):
                x_phys = (cx - 1) * x_step + x_step // 2
                d = xp * x_phys + yp * y_phys
                scores[f"{r['aisle_id']},{cx},{cy}"] = round(m * 1.0 + d, 4)
    return scores
