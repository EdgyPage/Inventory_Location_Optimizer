"""replay_run.py — export a real simulation run's persisted DBs to a dashboard trace.

Reads the SQLite output of a finished `run_simulation.py` run and emits a
Diagnostics/out/replay_<label>.json with per-batch warehouse fill (overall, by
aisle, by bucket) so the SAME dashboard can visualise the actual misbehaving run
— including the warehouses that settle at ~70% instead of the target ~85%.

Per Visualization/RECONSTRUCTION.md the relevant files are (this docstring is also `--help`, so
it stays inside cp1252 — no box drawing here, unlike the section banners below):

    <pair>/warehouse.db            aisle_layout    (geometry -> per-aisle capacity)
    <pair>/<config>/sim_X.db       bin_placement   PLACE  |
                                   bin_eviction    EVICT  |  the bin-mutation log:
                                   picks           PICK   |  fold -> EXACT occupancy
                                   aisle_metrics   (n_bins occupied / aisle / batch)
                                   bin_inventory   (pick deltas - ARCHIVED runs only)
                                   batch_stats     (duration / batch)
                                   simulation_runs (run params)

Where occupancy comes from — and what it is worth
-------------------------------------------------
Three sources, tried best-first.  Only the first is correct, so every export now RECORDS which one
it used (`meta.occupancy_source` / `occupancy_exact` / `occupancy_phase` / `occupancy_note`) and the
CLI prints a warning when the curve is an approximation.  A wrong fill curve presented as fact is
the failure this tool shipped with for its whole life.

1. `bin_placement` + `bin_eviction` + `picks`  ->  **EXACT at every batch.**
   Bin state changes at exactly five sites in the codebase (one of them dead code), and picks are
   already fully recorded, so PLACE + EVICT + PICK is complete *by construction*.  Folding them in
   application order — EVICT, then PLACE, then PICK, mirroring the runner — reproduces the
   simulation's own bin state.  Reference fold: `Tests/bench/bin_log_harness.py::fold`; proof
   against a live simulation: `Tests/integration/test_bin_log_replay.py`.  Checked here against a
   production arm's `sim_X.keyframes.db` (an independent record written by the runner): 9,690/9,690
   bins identical at batch 0 and 9,450/9,450 at batch 3, sku and qty included, zero ghosts.

2. `aisle_metrics.n_bins`  ->  **approximate.**  It is the manager's own occupied-bin counter
   (`sum(_aisle_sku_counts[aid].values())`) sampled after the restock pass and BEFORE the batch's
   picks, so it is a different frame from the other two sources, and it lags a bin emptied by a
   pick.  Only strategies that maintain aisle state write it at all — for a uniform-placement arm
   the table is empty, which is why the third source is what usually ran.

3. `bin_inventory` deltas  ->  **approximate and biased downward**, and no longer written at all:
   only runs from the archive carry this table, and on a current DB source 1 always answers first.
   The table records picks and
   NEVER restocks: `check_reorders()` runs before the pre-batch snapshot, so a restocked bin is
   already in it at its post-restock quantity and the `post_qty == pre_qty` skip drops it.
   Measured on a production arm: **0 rows** with `post_qty > pre_qty` against 20,662-42,832
   `reorder_placements` per batch.  Replaying those deltas can therefore only ever DECAY occupancy
   — 68,271 occupied bins against a true 165,519 five batches past a keyframe.  It draws a
   warehouse that slowly drains, plausibly enough that nothing flagged it for months.

Old runs keep working: the ~500 GB archive predates `bin_placement`, so those DBs fall through to
2/3 and are labelled approximate instead of silently believed.

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
import sys
from collections import defaultdict

# ── path setup: repo root on sys.path so package imports resolve when run as a
#    script (python Diagnostics/replay_run.py ...).  Entry-script bootstrap; see CLAUDE.md §2.
_HERE      = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
_OUT_DIR   = os.path.join(_HERE, 'out')
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from Schema import capability                # the shared TYPE + row probe (stdlib-only leaf)
from Schema import identity as _identity
from Schema.connect import read_only          # the ONE safe way to open a finished artifact
# `Picking_Data` is imported for the capability REGISTRY as well as for its registration side
# effect: `Schema/` imports no writer, so `sim_db`/`warehouse_db` exist in the identity registry
# only once their writer modules have been loaded.  `diagnostics -> optimization` is legal —
# `context/architecture.yml` forbids only warehouse_core -> diagnostics and schema -> diagnostics.
from Optimization.persistence import Picking_Data as _picking_data
from Optimization.persistence import Warehouse_Data as _warehouse_data  # noqa: F401

_GRID_COLS = 6

# ── what each occupancy source is worth ────────────────────────────────────────
# `exact`, `phase` and the caveat BODY now come from `Picking_Data.SIM_CAPABILITIES`, beside the
# DDL that defines these tables; the best-first order comes from `OCCUPANCY_LADDER`.  This file
# used to carry its own `_SOURCES` copy of all of it, which is the duplication the shared
# vocabulary exists to end.
#
# What stays here is only the half the registry cannot own — what a REPLAY, specifically, must add:
#
#   * `bin_log` is exact, so its registry caveat is '' and must stay '' — every exact capability
#     in the registry has an empty caveat, and a consumer that warns on a non-empty one would
#     start warning about the correct source.  But an export still has to record WHY the curve is
#     believable, so the provenance paragraph lives here, where it is not a duplicate of anything.
#   * the re-run advice is about this tool's situation (you are replaying an archived arm and can
#     re-run it), not a property of the table, so it does not belong in a shared registry either.
#
# Text is plain ASCII because it is printed to a console (Windows cp1252) as well as written to
# JSON.  See commit 71a4873.
_RERUN_ADVICE = 'Re-run the arm to get bin_placement and an exact curve.'

_REPLAY_NOTE: dict[str, str] = {
    _picking_data.CAP_BIN_LOG:
        ('EXACT. Folded from the complete bin-mutation log: bin_placement (PLACE) + '
         'bin_eviction (EVICT) + picks (PICK), applied in that order within each batch. '
         'Those are every mutation of Aisle.Bin.storage the simulation can make, so the '
         'fold reproduces its own bin state bin-for-bin instead of approximating it. See '
         'Tests/integration/test_bin_log_replay.py.'),
    _picking_data.CAP_AISLE_METRICS: _RERUN_ADVICE,
    _picking_data.CAP_BIN_INVENTORY: _RERUN_ADVICE,
}


#: Every declared-shape column this tool touches, and HOW (phase-2 of the staged
#: semantics gate).  A PURE LITERAL: Tests/architecture/test_column_semantics.py
#: AST-reads it and validates against Schema/semantics.py without importing this
#: module, so declaring costs no dependency.
#: `bin_inventory` (also read here) is the RETIRED archive table, outside the
#: declared shape by design; its two ordering traps live at its sunset note.
SEMANTIC_USES = {'sim_db': {
    'bin_eviction.batch_id': 'read', 'bin_eviction.aisle_id': 'read',
    'bin_eviction.bayX': 'read', 'bin_eviction.bayY': 'read',
    'bin_placement.batch_id': 'read', 'bin_placement.aisle_id': 'read',
    'bin_placement.bayX': 'read', 'bin_placement.bayY': 'read',
    'bin_placement.qty': 'read',
    'picks.batch_id': 'read', 'picks.aisle_id': 'read', 'picks.bayX': 'read',
    'picks.bayY': 'read', 'picks.quantity': 'read',
    'aisle_metrics.batch_id': 'read', 'aisle_metrics.aisle_id': 'read',
    'aisle_metrics.n_bins': 'read',
    'batch_stats.batch_id': 'read', 'batch_stats.duration': 'read',
    'simulation_runs.run_id': 'read',
}, 'warehouse_db': {
    'aisle_layout.aisle_id': 'read', 'aisle_layout.handling_type': 'read',
    'aisle_layout.category': 'read', 'aisle_layout.unit_type': 'read',
    'aisle_layout.storage_size': 'read',
}}


def _note(cap) -> str:
    """The `occupancy_note` an export carries for `cap`: the registry's caveat, then ours.

    Joined with a single space, skipping either half when it is empty.  The exported key stays
    `occupancy_note` and not `occupancy_caveat` — the dashboard and every replay JSON already on
    disk read `meta.occupancy_note`, and renaming it to match the shared type's field name would
    break the output contract for no gain.
    """
    return ' '.join(part for part in (cap.caveat, _REPLAY_NOTE.get(cap.name, '')) if part)


# ── warehouse geometry ─────────────────────────────────────────────────────────

def read_layout(warehouse_db: str) -> tuple[list[dict], dict[int, int]]:
    """Aisle geometry + per-aisle bin capacity from a run's `warehouse.db`.

    HARD FAIL on an unvetted shape (`identity.check` raises `UnsupportedSchema`), unlike the
    viewer, which warns.  Capacity is the DENOMINATOR of every fill percentage this tool
    exports, and the dashboard renders those percentages as a measurement.  A geometry table
    that is not the one we think it is produces a fill curve that is wrong in a way no reader
    can see — the same class of defect as the `bin_inventory` decay carried in that capability's
    caveat, which is why that entry exists at all.  Better to name the differing column and stop.
    Both archived shapes are vetted (`Warehouse_Data.PRE_STAMP_WAREHOUSE_SCHEMA_ID` and
    `PRE_FINGERPRINT_WAREHOUSE_SCHEMA_ID`), so this refuses nothing that exists today.
    """
    _identity.check(warehouse_db, 'warehouse_db', verify=True)
    conn = read_only(warehouse_db)
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

class _BatchStream:
    """One table's rows, grouped by `batch_id`, consumed strictly forward.

    A cursor with one row of lookahead rather than a list: a single production arm holds millions
    of picks, and the fold only ever needs the batch it is currently applying.  `take()` is a
    generator and MUST be drained before the stream is asked for its next batch.
    """
    __slots__ = ('_cur', '_row')

    def __init__(self, conn: sqlite3.Connection, sql: str, args: tuple):
        self._cur = conn.execute(sql, args)
        self._row = self._cur.fetchone()

    @property
    def batch(self) -> int | None:
        """The batch the next unread row belongs to, or None when exhausted."""
        return None if self._row is None else self._row['batch_id']

    def take(self, batch: int):
        while self._row is not None and self._row['batch_id'] == batch:
            yield self._row
            self._row = self._cur.fetchone()


def occupied_from_bin_log(conn, run_id, max_batches: int = 0) -> dict[int, dict[int, int]]:
    """Fold PLACE + EVICT + PICK into an occupied-bin count per aisle at the end of each batch.

    This is the correct reconstruction, and the only one: the three event streams are every
    mutation of `Aisle.Bin.storage` the simulation can make, so the fold reproduces its own state
    rather than approximating it.  Mirrors `Tests/bench/bin_log_harness.py::fold`, including the
    within-batch order **EVICT -> PLACE -> PICK** — the runner reloads, then calls
    `check_reorders()`, then simulates picks.

    Only the aisle counts are kept, not `{loc: (sku, qty)}` as the harness does: the dashboard
    draws fill per aisle, and a whole warehouse of per-bin tuples per batch is not worth carrying.
    Counts are maintained incrementally as bins enter and leave `qty`, so a batch costs its own
    events and not a rescan of ~165k bins.

    Quantities are integers throughout (`qty`, `picks.quantity`), so the depletion test below is
    exact integer arithmetic — no float tolerance is involved anywhere in this fold.

    `max_batches` stops the fold early (0 = all).  Occupancy at batch k depends on every event
    before it, so this is a cap on output, never a shortcut through the history.
    """
    evicts = _BatchStream(conn, 'SELECT batch_id, aisle_id, bayX, bayY FROM bin_eviction '
                                'WHERE run_id=? ORDER BY batch_id, seq', (run_id,))
    places = _BatchStream(conn, 'SELECT batch_id, aisle_id, bayX, bayY, qty FROM bin_placement '
                                'WHERE run_id=? ORDER BY batch_id, seq', (run_id,))
    # Picks are ordered by batch only — deliberately.  Within a batch the applied order cannot
    # change the end-of-batch state (subtractions commute, and a bin driven to <= 0 is dropped
    # either way), while adding `sim_time` to the ORDER BY would force SQLite to sort millions of
    # rows instead of walking ix_picks_run_batch.
    picks  = _BatchStream(conn, 'SELECT batch_id, aisle_id, bayX, bayY, quantity FROM picks '
                                'WHERE run_id=? ORDER BY batch_id', (run_id,))

    qty: dict[tuple, int] = {}                       # (aisle,bayX,bayY) -> units in that bin
    occ: dict[int, int] = defaultdict(int)           # aisle_id -> occupied bins (kept incrementally)
    out: dict[int, dict[int, int]] = {}

    while True:
        pending = [s.batch for s in (evicts, places, picks) if s.batch is not None]
        if not pending:
            break
        batch = min(pending)

        for r in evicts.take(batch):                 # 1. reloader emptied the bin
            if qty.pop((r['aisle_id'], r['bayX'], r['bayY']), None) is not None:
                occ[r['aisle_id']] -= 1
        for r in places.take(batch):                 # 2. restock / initial fill / re-slot
            loc = (r['aisle_id'], r['bayX'], r['bayY'])
            if loc not in qty:
                occ[r['aisle_id']] += 1
            qty[loc] = r['qty']                      # a fill REPLACES the bin's contents
        for r in picks.take(batch):                  # 3. pickers drew it back down
            loc = (r['aisle_id'], r['bayX'], r['bayY'])
            cur = qty.get(loc)
            if cur is None:                          # already empty; nothing left to remove
                continue
            remaining = cur - r['quantity']
            if remaining > 0:
                qty[loc] = remaining
            else:
                del qty[loc]
                occ[r['aisle_id']] -= 1

        out[batch] = {aid: n for aid, n in occ.items() if n > 0}
        if max_batches and len(out) >= max_batches:
            break
    return out


def occupied_from_aisle_metrics(conn, run_id) -> dict[int, dict[int, int]]:
    """APPROXIMATE fallback — see `Picking_Data.SIM_CAPABILITIES['aisle_metrics']`.

    `n_bins` is the manager's own occupied-bin counter sampled after the restock pass and BEFORE
    the batch's picks, so this is the start-of-batch frame, not the end-of-batch one the log and
    `bin_inventory` produce.
    """
    rows = conn.execute(
        'SELECT batch_id, aisle_id, n_bins FROM aisle_metrics WHERE run_id=? '
        'ORDER BY batch_id', (run_id,)).fetchall()
    out: dict[int, dict[int, int]] = defaultdict(dict)
    for r in rows:
        out[r['batch_id']][r['aisle_id']] = r['n_bins']
    return out


def occupied_from_bin_inventory(conn, run_id) -> dict[int, dict[int, int]]:
    """APPROXIMATE fallback of last resort — see `Picking_Data.SIM_CAPABILITIES['bin_inventory']`.

    ARCHIVE-ONLY: `bin_inventory` is no longer written, so on a current DB this is unreachable —
    `_occupancy` probes `bin_placement` first and the table is not even in the schema.  Reached
    only for the ~500 GB of pre-log runs, which is exactly why it is kept.

    Rolls bin_inventory deltas (full snapshot at batch 0 + changed bins after) into an occupied-bin
    count per aisle at the end of each recorded batch.  The stream contains picks and no restocks,
    so the curve it produces can only ever fall.
    """
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


#: Which fold answers for which capability.  The SELECTION is `Picking_Data.OCCUPANCY_LADDER`'s
#: business — bin_log, then aisle_metrics, then bin_inventory — and this is only the map from the
#: name it chose back to the code that reads that table.
_FOLD = {
    _picking_data.CAP_BIN_LOG:
        lambda conn, run_id, max_batches: occupied_from_bin_log(conn, run_id, max_batches),
    _picking_data.CAP_AISLE_METRICS:
        lambda conn, run_id, max_batches: occupied_from_aisle_metrics(conn, run_id),
    _picking_data.CAP_BIN_INVENTORY:
        lambda conn, run_id, max_batches: occupied_from_bin_inventory(conn, run_id),
}


def _occupancy(conn, run_id, max_batches: int):
    """Fold the best occupancy source this DB carries; return (occ_by_batch, capability).

    Probing rather than assuming is what keeps the ~500 GB archive readable: those DBs predate the
    bin-mutation log entirely, so `bin_placement` is not merely empty — the table does not exist.
    `capability.has_rows` is False for both cases, and the run then falls through to a LABELLED
    approximation.

    The probe stays `run_id`-FILTERED. An arm that was resumed or interrupted can leave a table
    created-but-empty for the run being replayed, and an unfiltered probe would then pick a source
    that folds to nothing — not hypothetical: in the 2026-07-29 archive `aisle_metrics` exists on
    every arm and is empty for the uniform ones, which is how `bin_inventory` gets reached at all.

    Raises `capability.NoSourceAvailable` when the run carries none of the three; the caller turns
    that into a `SystemExit` naming the file.
    """
    have = capability.probe(conn, _picking_data.OCCUPANCY_LADDER, run_id)
    cap = capability.require(have, _picking_data.OCCUPANCY_LADDER,
                             what=f'occupancy of run {run_id}')
    return _FOLD[cap.name](conn, run_id, max_batches), cap


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
    conn = read_only(sim_db)
    run = conn.execute('SELECT * FROM simulation_runs ORDER BY run_id LIMIT 1').fetchone()
    run_id = run['run_id']
    run_type = run['run_type'] if 'run_type' in run.keys() else os.path.basename(sim_db)

    durations = {r['batch_id']: r['duration']
                 for r in conn.execute(
                     'SELECT batch_id, duration FROM batch_stats WHERE run_id=?',
                     (run_id,)).fetchall()} if capability.has_rows(conn, 'batch_stats',
                                                                   run_id) else {}

    try:
        occ, cap = _occupancy(conn, run_id, max_batches)
    except capability.NoSourceAvailable as exc:
        conn.close()                                  # the archive is opened read-only; still close
        # `require` names the ladder it tried by CAPABILITY; a reader goes looking for TABLES, so
        # add those — derived from the ladder, so a new rung cannot leave this message stale.
        raise SystemExit(f'{sim_db}: {exc} Tables tried: '
                         + ', '.join(c.table for c in _picking_data.OCCUPANCY_LADDER)
                         + '.') from None
    conn.close()

    batches = build_batches(occ, capacity, aisles, durations, max_batches)
    if not batches:
        raise SystemExit(f'{sim_db}: no per-batch fill could be derived.')

    # Tagged on EVERY arm line, not just in the JSON.  The defect being fixed here was never a
    # wrong number as such — it was a wrong number that looked like a measurement.  `main` prints
    # the full reason once per source at the end, so 34 arms do not repeat one paragraph 34 times.
    print(f'  {os.path.basename(sim_db)}: run_id={run_id} type={run_type}  '
          f'{len(batches)} batches via {cap.name} '
          f'[{"EXACT" if cap.exact else "APPROXIMATE"}, {cap.phase}]  '
          f'final fill={batches[-1]["fill_overall"]:.1%}')
    prov = capability.provenance(cap)                 # {'source', 'exact', 'phase', 'caveat'}
    return {
        'meta': {'source': 'replay', 'strategy': str(run_type),
                 'label': str(run_type), 'n_skus': None,
                 'total_bins': sum(capacity.values()), 'target_fill': None,
                 'n_batches': len(batches),
                 'occupancy_source': prov['source'],
                 'occupancy_exact': prov['exact'],
                 'occupancy_phase': prov['phase'],
                 # The shared type calls this field `caveat`; the EXPORTED key stays
                 # `occupancy_note` — see `_note`.
                 'occupancy_note': _note(cap)},
        'warehouse': {'aisles': aisles, 'grid_cols': _GRID_COLS},
        'batches': batches,
    }


# ── discovery ───────────────────────────────────────────────────────────────────
#
# Two vintages, one tool.  A run with a `run_layout.json` descriptor resolves through its OWN
# run-tree contract (`runschema.resolver_for`); the ~500 GB of archives that PREDATE the
# descriptor resolve through the raw glob / walk-up below, which is therefore load-bearing and
# kept verbatim — reading old runs is this file's whole point.  `diagnostics -> optimization` is
# a legal import direction (context/architecture.yml forbids only warehouse_core -> diagnostics
# and schema -> diagnostics), so the resolver route needs no layering exemption.

def _contract_resolver(target: str):
    """The RunTree for a contract run root, or None (a single DB / pre-descriptor archive)."""
    if target.endswith('.db') or not os.path.isdir(target):
        return None
    try:
        from Optimization.runschema import resolver_for
        return resolver_for(target)
    except Exception:                            # noqa: BLE001 - no descriptor / unknown schema
        return None


def _nearest_warehouse_db(sim_db: str, rt=None) -> str | None:
    if rt is not None:
        # Positional leaf split + pair-scoped template, via the resolver's own public mirror of
        # `keyframe_db` (never by directory name: `<pair>/store/store/` is real).
        cand = rt.warehouse_db_of(sim_db)
        if os.path.exists(cand):
            return cand
    d = os.path.dirname(os.path.abspath(sim_db))
    for _ in range(4):
        cand = os.path.join(d, 'warehouse.db')
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    return None


def discover_sim_dbs(target: str, rt=None) -> list[str]:
    if target.endswith('.db'):
        return [target]
    if rt is not None:
        # Contract route: the `sim_db` template with every part wildcarded.  Most-specific-template
        # ownership excludes the keyframe sidecars (`sim_{strategy}.keyframes.db` out-literals
        # `sim_{strategy}.db`), which is the same exclusion the endswith() below hand-codes.
        return rt.glob('sim_db')
    # `sim_X.keyframes.db` sits beside `sim_X.db` and matches the same glob, but it is a sidecar
    # holding only `bin_keyframe` — no simulation_runs, so replaying it raised OperationalError
    # and killed the whole sweep at the first arm.
    return sorted(p for p in glob.glob(os.path.join(target, '**', 'sim_*.db'), recursive=True)
                  if not p.endswith('.keyframes.db'))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', help='run base dir, or a single sim_*.db path')
    ap.add_argument('--max', type=int, default=0, help='cap exported batches (0 = all)')
    args = ap.parse_args()

    rt = _contract_resolver(args.target)
    sim_dbs = discover_sim_dbs(args.target, rt=rt)
    if not sim_dbs:
        raise SystemExit(f'No sim_*.db found under {args.target}')

    os.makedirs(_OUT_DIR, exist_ok=True)
    manifest = []
    for sim_db in sim_dbs:
        wh = _nearest_warehouse_db(sim_db, rt=rt)
        if wh is None:
            print(f'  SKIP {sim_db}: no warehouse.db found nearby'); continue
        result = replay_sim_db(sim_db, wh, args.max)
        key = os.path.splitext(os.path.basename(sim_db))[0]   # sim_<key>
        fname = f'replay_{key}.json'
        with open(os.path.join(_OUT_DIR, fname), 'w') as f:
            json.dump(result, f)
        meta = result['meta']
        # The dashboard's run switcher prints the manifest's `strategy` verbatim, so the marker
        # rides along there — an approximate curve must not be indistinguishable from a measured
        # one in the UI either.  meta.strategy stays the clean arm key.
        manifest.append({'file': fname,
                         'strategy': meta['strategy'] + ('' if meta['occupancy_exact']
                                                         else '  [approx]'),
                         'label': meta['label'], 'stock_mode': 'replay',
                         'final_fill': result['batches'][-1]['fill_overall'],
                         'occupancy_source': meta['occupancy_source'],
                         'occupancy_exact': meta['occupancy_exact']})

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

    # The whole point of the change: an approximate curve leaves saying so, once, in full.
    approx = [r for r in manifest if not r['occupancy_exact']]
    if approx:
        print(f'\n{len(approx)} of {len(manifest)} replay(s) are APPROXIMATE - those DBs have no '
              f'bin_placement table, so they predate the bin-mutation log. They are marked '
              f'[approx] in the dashboard run switcher; re-run the arm for an exact curve.')
        for src in sorted({r['occupancy_source'] for r in approx}):
            cap = _picking_data.SIM_CAPABILITIES[src]
            print(f'  {src} ({cap.phase}):\n    {_note(cap)}')


if __name__ == '__main__':
    main()
