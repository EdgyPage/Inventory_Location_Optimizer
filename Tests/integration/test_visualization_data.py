"""test_visualization_data.py — the persistence contract the spatial viewer reads.

Two groups, and the second is the one that matters.

**Round-trips** — every table `Visualization/` reads must survive a save/load cycle with its
values intact: run params + identity, batch stats, reorder queues, bin/SKU/aisle scores, the
warehouse geometry, and the keyframe DB.

**Reconstruction invariants** — the rules `Visualization/RECONSTRUCTION.md` §1 states as *measured
fact* about a production run, pinned here as executable assertions:

  1. `bin_keyframe[B]` is the post-restock, pre-pick state at the start of batch B.
  2. Within a batch, `sum(pre_qty - post_qty)` equals `sum(picks.quantity)` — the delta stream
     accounts for picks and nothing else.
  3. **A restocked-but-not-picked bin produces NO `bin_inventory` row.**  `check_reorders()` runs
     before `build_pre_snapshot`, so the bin is already in `pre_snap` at its post-restock quantity,
     and the writer's `post_qty == pre_qty` skip then drops it.
  4. Therefore rolling `post_qty` deltas forward from a keyframe **loses every restocked bin** —
     on a real 100-batch run that is 59% of the warehouse by the 5th batch.

(2), (3) and (4) describe the **ARCHIVE**, and they are still true of it
------------------------------------------------------------------------
`bin_inventory` is no longer written: `bin_placement` + `bin_eviction` + `picks` reconstruct bin
state exactly at every batch, so the table was pure redundancy and its writer
(`Simulation_Analytics.snapshot_bin_inventory`) is deleted. These three tests are **kept, not
deleted**, because ~500 GB of archived runs carry that table and `load_bin_inventory` still reads
them — so what these properties describe is what a reader must still expect from those files.
They are the reason the log exists; deleting them would delete the argument.

They therefore run against `_archived_snapshot_bin_inventory` below — a frozen transcription of
the retired writer, not a stub. If it ever disagrees with the archive, these tests stop meaning
anything, which is why it is copied verbatim rather than reimplemented.

    python -m pytest Tests/integration/test_visualization_data.py -q
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from Optimization.persistence.Picking_Data import (
    init_run_db, create_run, find_run, run_identity,
    save_batch_stats, load_batch_stats, BatchStats, BinInventoryRecord,
    init_keyframe_db, save_bin_keyframe, keyframe_db_path,
    save_reorder_queue, load_reorder_queue,
    save_bin_scores, load_bin_scores, save_sku_scores, load_sku_scores,
    save_aisle_metrics, load_aisle_metrics, AisleMetricRecord,
)
from Optimization.persistence.Warehouse_Data import (
    init_warehouse_db, save_aisle_layout, compute_warehouse_fingerprint,
)
from Optimization.metrics.Simulation_Analytics import build_pre_snapshot, fused_pre_snapshot
from Warehouse.layout.Storage_Primitive import Singleton

_TOL = 1e-9


# ── the retired writer, frozen ───────────────────────────────────────────────────
#
# Verbatim transcription of `Simulation_Analytics.snapshot_bin_inventory` as it stood when it was
# deleted — the function that wrote every `bin_inventory` table in the archive.  It lives here
# because the properties below are claims about THOSE FILES, and a claim about a file needs the
# code that produced it, not a paraphrase.  It must never be "improved": the archive cannot be
# rewritten, so a fix here would only make the tests describe a run that does not exist.
#
# `build_pre_snapshot` is imported from production, not copied, because it is still live — the
# keyframe writer and the runner's conservation ledger both use it.

def _archived_snapshot_bin_inventory(manager, pre_snap, batch_id, run_id=0, full_snapshot=False):
    """Merge pre-snapshot with post-simulation bin state into BinInventoryRecords.

    Three cases, exactly as the archive's writer handled them:
      - Picked bins    : in pre_snap, post_qty < pre_qty — always recorded.
      - Untouched bins : in pre_snap, post_qty == pre_qty — recorded only on full_snapshot.
      - Reorder bins   : NOT in pre_snap, newly placed by check_reorders() — always recorded.

    The middle case is the whole bug: a bin restocked between batches is ALREADY in pre_snap at
    its post-restock quantity, so it takes the untouched branch and the restock leaves no trace.
    """
    from Warehouse.layout.Storage_Primitive import Singleton

    records = []
    for _bin_id, info in pre_snap.items():
        bin_ = info['bin_ref']
        post_qty = bin_.storage.quantity if bin_.storage is not None else 0
        if not full_snapshot and post_qty == info['pre_qty']:
            continue   # unchanged bin — skipped to minimise write volume
        records.append(BinInventoryRecord(
            run_id=run_id, batch_id=batch_id,
            aisle_id=info['aisle_id'], bayX=info['bayX'], bayY=info['bayY'],
            sku=info['sku'], unit_type=info['unit_type'], storage_size=info['storage_size'],
            pre_qty=info['pre_qty'], post_qty=post_qty))

    # Bins empty before this batch that received a reorder: in _unavailable, not in pre_snap.
    for bin_ in manager._unavailable.values():
        if id(bin_) in pre_snap or bin_.storage is None:
            continue
        records.append(BinInventoryRecord(
            run_id=run_id, batch_id=batch_id,
            aisle_id=bin_.location[0], bayX=bin_.bayX, bayY=bin_.bayY,
            sku=bin_.storage.order.sku,
            unit_type='singleton' if isinstance(bin_.storage, Singleton) else 'pallet',
            storage_size=bin_.storage_size,
            pre_qty=bin_.storage.quantity, post_qty=bin_.storage.quantity))

    return records


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix='vizdata_'), name)


# ── stubs for the snapshot writers ───────────────────────────────────────────────
#
# build_pre_snapshot and the archived writer duck-type over the manager's bins: they read
# `_unavailable.values()`, then `bin_.storage`, `.location[0]`, `.bayX`, `.bayY`,
# `.storage_size`, and `.storage.order.sku` / `.storage.quantity`.  Standing up a real
# Inventory_Manager to exercise ~40 lines of snapshot logic would couple this test to placement;
# these three stubs are the whole surface those two functions touch.

class _Order:
    def __init__(self, sku):
        self.sku = sku


class _Unit:
    """Stands in for a StorageUnit.  Not a Singleton, so the writer tags it 'pallet'."""
    def __init__(self, sku, quantity):
        self.order = _Order(sku)
        self.quantity = quantity


class _Bin:
    def __init__(self, aisle_id, bayX, bayY, storage=None, storage_size='large'):
        self.location = (aisle_id, bayX, bayY)
        self.bayX, self.bayY = bayX, bayY
        self.storage_size = storage_size
        self.storage = storage


class _Manager:
    def __init__(self, bins):
        self._unavailable = {i: b for i, b in enumerate(bins)}


# ── round-trips ──────────────────────────────────────────────────────────────────

def test_run_params_roundtrip():
    db = _tmp('sim.db')
    init_run_db(db)
    params = dict(num_pickers=25, x_speed=1.0, y_speed=0.5, pick_intercept=1.0,
                  pick_weight_coef=1.1, pick_volume_coef=1e-3, cart_swap_coef=10.0,
                  k_pickers=25, n_batches=100, seed_world=42, keyframe_interval=5)
    rid = create_run(db, 'uniform_assignment', params)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM simulation_runs WHERE run_id=?', (rid,)).fetchone()
    con.close()

    assert row['num_pickers'] == 25
    assert row['keyframe_interval'] == 5, 'keyframe_interval drives the whole spatial timeline'
    assert abs(row['x_speed'] - 1.0) < _TOL
    assert abs(row['pick_volume_coef'] - 1e-3) < 1e-12
    assert create_run(db, 'x') > 0, 'a run with no params must still be creatable (NULL columns)'


def test_batch_stats_roundtrip():
    db = _tmp('sim.db')
    init_run_db(db)
    rid = create_run(db, 'uniform_assignment')
    rec = BatchStats(run_id=rid, batch_id=7, duration=120.5, num_tasks=3,
                     total_items=40, avg_concurrent_pickers=2.1,
                     picking_pct=0.6, traveling_pct=0.4,
                     batch_start_time=0.0, batch_end_time=120.5,
                     queue_depth=14395, lead_queue_depth=812, in_transit_qty=88000,
                     reorder_placements=1234)
    save_batch_stats(db, rid, [rec])
    got = {b.batch_id: b for b in load_batch_stats(db, rid)}[7]

    assert abs(got.batch_end_time - 120.5) < _TOL
    assert abs(got.batch_start_time - 0.0) < _TOL
    assert got.queue_depth == 14395
    assert got.lead_queue_depth == 812
    assert got.in_transit_qty == 88000
    # The viewer reads this to report how many restocks a non-keyframe batch is missing.
    assert got.reorder_placements == 1234


def test_reorder_queue_roundtrip():
    db = _tmp('sim_rq.db')
    init_run_db(db)
    rid = create_run(db, 'uniform_assignment')
    # (batch, kind, sku, qty, remaining_lead, unit_type, storage_size, queue).
    # `queue` is NULL on 'lead' rows: still in transit, not yet routed to a stream.
    recs = [(5, 'lead', 101, 30, 2, None, None, None),
            (5, 'lead', 102, 12, 1, None, None, None),
            (5, 'stock', 101, 8, 0, 'pallet', 'large', 'store_pallet'),
            (5, 'held', 103, 4, 0, 'pallet', 'large', 'store_pallet')]
    save_reorder_queue(db, rid, recs)
    got = {(r['kind'], r['sku']): r for r in load_reorder_queue(db, rid, 5)}

    assert len(got) == 4

    # `queue` is NOT in `load_reorder_queue`'s SELECT, deliberately: the guaranteed read
    # surface is the intersection over every vetted vintage, and naming a column added
    # today would make this loader raise on every archived run. It is written and read
    # back here directly, which is the right test of a write-only column anyway.
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        by_kind = dict(con.execute(
            'SELECT kind, queue FROM reorder_queue WHERE run_id=? AND sku IN (101,103) '
            'AND kind IN ("held","lead")', (rid,)).fetchall())
    finally:
        con.close()
    assert by_kind['held'] == 'store_pallet', 'a held item must say which stream refused it'
    assert by_kind['lead'] is None, 'an in-transit item has not been routed to a queue yet'
    assert got[('lead', 101)]['qty'] == 30
    assert got[('lead', 101)]['remaining_lead'] == 2
    assert got[('stock', 101)]['unit_type'] == 'pallet'
    assert got[('stock', 101)]['storage_size'] == 'large'
    assert got[('lead', 101)]['unit_type'] is None, 'lead entries carry no bin tier'
    assert load_reorder_queue(db, rid, 6) == [], 'an unqueued batch is empty, not an error'

    # A DB predating the table must degrade to [] rather than raising.  Note this is
    # INDISTINGUISHABLE from a present-but-empty table at this layer — telling those apart is
    # the reader's capabilities() probe, not this loader's job.
    db2 = _tmp('sim_no_rq.db')
    con = sqlite3.connect(db2)
    con.execute('CREATE TABLE simulation_runs(run_id INTEGER)')
    con.commit()
    con.close()
    assert load_reorder_queue(db2, 1, 0) == []


def test_bin_scores_roundtrip():
    db = _tmp('sim_bs.db')
    init_run_db(db)
    rid = create_run(db, 'uni_map_rank_norsl')
    recs = [(1, 2, 3, 4.5, 1.2, 5.7, 9.1), (1, 2, 4, 6.0, 1.0, 7.0, None)]
    save_bin_scores(db, rid, recs)
    got = {(r['aisle_id'], r['bayX'], r['bayY']): r for r in load_bin_scores(db, rid)}

    assert len(got) == 2
    assert abs(got[(1, 2, 3)]['layout_score'] - 5.7) < _TOL
    assert abs(got[(1, 2, 3)]['map_pref'] - 9.1) < _TOL
    assert got[(1, 2, 4)]['map_pref'] is None, 'non-map bins keep a NULL pref, not a 0.0'


def test_sku_scores_roundtrip():
    db = _tmp('sim_ss.db')
    init_run_db(db)
    rid = create_run(db, 'uni_map_norsl')
    recs = [(101, 3.3, 1.5, 0.5, 0.2, 0.3, 40, 12, 2.0),
            (102, None, 1.1, 0.4, 0.1, 0.11, 20, 6, 0.0)]
    save_sku_scores(db, rid, recs)
    got = {r['sku']: r for r in load_sku_scores(db, rid)}

    assert len(got) == 2
    assert abs(got[101]['map_target'] - 3.3) < _TOL
    assert abs(got[101]['labor_cost'] - 1.5) < _TOL
    assert got[101]['equilibrium_qty'] == 40
    assert got[102]['map_target'] is None


def test_aisle_metrics_roundtrip():
    db = _tmp('sim_am.db')
    init_run_db(db)
    rid = create_run(db, 'uni_rank_labor_norsl')
    rec = AisleMetricRecord(run_id=rid, batch_id=2, aisle_id=7, n_skus=3, n_bins=5,
                            demand_sum=1.5, lift_sum=0.4, pick_load_sum=2.75)
    save_aisle_metrics(db, rid, [rec])
    got = {a.aisle_id: a for a in load_aisle_metrics(db, rid, batch_id=2)}[7]

    assert abs(got.pick_load_sum - 2.75) < _TOL
    assert abs(got.demand_sum - 1.5) < _TOL
    assert got.n_bins == 5


def test_run_identity_and_find_run():
    db = _tmp('sim_id.db')
    init_run_db(db)
    ident = dict(strategy_key='uni_map_rank_norsl', pair_label='mixedA',
                 config_label='calibrated', warehouse_fingerprint='abc123',
                 inventory_label='mixedA')
    rid = create_run(db, 'uni_map_rank_norsl',
                     params=dict(n_batches=100, optimal_work=123.0), identity=ident)

    assert find_run(db, 'uni_map_rank_norsl') == rid
    assert find_run(db, 'nope') == rid, 'an unknown key falls back to the first run'
    meta = run_identity(db, rid)
    assert meta.get('pair_label') == 'mixedA'
    assert meta.get('config_label') == 'calibrated'
    # The viewer matches a run to its warehouse.db by this, so renamed folders still load —
    # and refuses to colour two runs together when it differs.
    assert meta.get('warehouse_fingerprint') == 'abc123'
    assert abs(meta.get('optimal_work') - 123.0) < _TOL


def test_warehouse_fingerprint_is_order_independent():
    rows = [dict(aisle_id=2, handling_type='c', category='food', unit_type='pallet',
                 storage_size='large', bay_x=5, bay_y=4),
            dict(aisle_id=1, handling_type='c', category='food', unit_type='pallet',
                 storage_size='large', bay_x=5, bay_y=4)]
    fp1 = compute_warehouse_fingerprint(rows, 'mixedA')

    assert fp1 == compute_warehouse_fingerprint(list(reversed(rows)), 'mixedA')
    assert fp1 != compute_warehouse_fingerprint(rows, 'other')


def test_aisle_layout_roundtrip():
    db = _tmp('warehouse.db')
    init_warehouse_db(db)
    rows = [
        dict(aisle_id=1, handling_type='conveyable', category='food',
             unit_type='pallet', storage_size='large', bay_x=50, bay_y=13),
        dict(aisle_id=2, handling_type='non-conveyable', category='chemical',
             unit_type='singleton', storage_size='singleton', bay_x=150, bay_y=10),
    ]
    save_aisle_layout(db, rows)
    save_aisle_layout(db, rows)          # rewrite-fresh must not duplicate
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    got = {r['aisle_id']: r for r in con.execute('SELECT * FROM aisle_layout')}
    n = con.execute('SELECT COUNT(*) FROM aisle_layout').fetchone()[0]
    con.close()

    assert n == 2, 'save_aisle_layout rewrites fresh; a second call must not double the rows'
    # bay_x x bay_y is the ONLY source of the bin grid — empty bins exist nowhere else.
    assert got[1]['bay_x'] == 50 and got[1]['bay_y'] == 13
    assert got[2]['unit_type'] == 'singleton'
    assert got[2]['handling_type'] == 'non-conveyable'
    assert got[1]['category'] == 'food'


def test_keyframe_db_path_and_roundtrip():
    run_db = _tmp('sim_A.db')
    kf = keyframe_db_path(run_db)
    assert kf.endswith('sim_A.keyframes.db'), kf
    assert os.path.dirname(kf) == os.path.dirname(run_db), 'keyframes sit beside their sim DB'

    init_keyframe_db(kf)
    recs = [dict(aisle_id=1, bayX=2, bayY=3, sku=10, unit_type='pallet',
                 storage_size='large', qty=16)]
    save_bin_keyframe(kf, run_id=1, batch_id=0, records=recs)
    save_bin_keyframe(kf, run_id=1, batch_id=5, records=recs)
    con = sqlite3.connect(kf)
    batches = sorted(r[0] for r in con.execute(
        'SELECT DISTINCT batch_id FROM bin_keyframe WHERE run_id=1'))
    qty = con.execute('SELECT qty FROM bin_keyframe WHERE run_id=1 AND batch_id=0 '
                      'AND aisle_id=1 AND bayX=2 AND bayY=3').fetchone()[0]
    con.close()

    assert batches == [0, 5]
    assert qty == 16


def test_fused_pre_snapshot_matches_original():
    """The pre-snapshot fusion gate: `fused_pre_snapshot` must reproduce exactly what the
    runner used to derive from `build_pre_snapshot` — the keyframe row list (same values,
    same ORDER: keyframe rowids depend on it, same empty-bin skip) and the conservation
    ledger's occupancy sum.  The row comprehension below is the runner's retired code,
    verbatim."""
    sing = object.__new__(Singleton)          # a REAL Singleton so isinstance says so
    sing.order = _Order(sku=7)
    sing.quantity = 3
    bins = [
        _Bin(1, 2, 3, _Unit(sku=99, quantity=16)),
        _Bin(1, 2, 4, None),                          # empty: both paths skip it
        _Bin(2, 0, 1, sing, storage_size='small'),    # 'singleton', size from the BIN
        _Bin(3, 5, 5, _Unit(sku=98, quantity=0)),     # zero-qty but occupied: kept
    ]
    mgr = _Manager(bins)

    pre_snap = build_pre_snapshot(mgr)
    want_rows = [
        {'aisle_id': v['aisle_id'], 'bayX': v['bayX'], 'bayY': v['bayY'],
         'sku': v['sku'], 'unit_type': v['unit_type'],
         'storage_size': v['storage_size'], 'qty': v['pre_qty']}
        for v in pre_snap.values()
    ]
    want_occ = sum(v['pre_qty'] for v in pre_snap.values())

    occupancy, rows = fused_pre_snapshot(mgr, True)
    assert rows == want_rows, 'fused keyframe rows differ from the build_pre_snapshot derivation'
    assert [r['unit_type'] for r in rows] == ['pallet', 'singleton', 'pallet']
    assert occupancy == want_occ == 19

    occ_only, no_rows = fused_pre_snapshot(mgr, False)
    assert occ_only == want_occ
    assert no_rows is None, 'non-keyframe batches must not pay for row building'


# ── RECONSTRUCTION.md §1 — what an ARCHIVED run's bin_inventory means ────────────
#
# Everything below is a statement about files already on disk, made with the writer that wrote
# them (`_archived_snapshot_bin_inventory`).  Nothing here describes a run this build produces:
# those carry `bin_placement` + `bin_eviction` + `picks` and no `bin_inventory` at all, and their
# reconstruction is exact — see Tests/integration/test_bin_log_replay.py and
# test_log_reconstruction.py.  These four remain because the archive is not going to be re-run,
# and because they are the measured case FOR the log.

def test_archived_pick_depletion_is_fully_accounted():
    """Invariant 2, in the archive: a batch's delta stream accounts for picks and nothing else.

    Two bins start at 10; one is picked down to 4, one is untouched.  The recorded
    depletion must equal exactly the units picked.

    This is the half `bin_inventory` got right, and the reason it could be retired without
    losing information: `picks` already carries the same total, at sim_time resolution.
    """
    picked = _Bin(7, 2, 3, _Unit(sku=99, quantity=10))
    quiet = _Bin(7, 2, 4, _Unit(sku=98, quantity=10))
    mgr = _Manager([picked, quiet])

    pre_snap = build_pre_snapshot(mgr)
    assert len(pre_snap) == 2, 'both bins are non-empty at batch start'

    picked.storage.quantity = 4                       # 6 units picked during the batch
    recs = _archived_snapshot_bin_inventory(mgr, pre_snap, batch_id=1, run_id=1,
                                            full_snapshot=False)

    depletion = sum(r.pre_qty - r.post_qty for r in recs)
    assert depletion == 6, f'recorded depletion {depletion} != 6 units picked'
    assert all(r.post_qty <= r.pre_qty for r in recs), 'a delta row can never show a gain'


def test_archived_untouched_bin_writes_no_row_unless_full_snapshot():
    """The archived delta table's whole reason to exist: unchanged bins were skipped.

    A reader of an archived file must not read "no row" as "no bin".
    """
    quiet = _Bin(7, 2, 4, _Unit(sku=98, quantity=10))
    mgr = _Manager([quiet])
    pre_snap = build_pre_snapshot(mgr)

    assert _archived_snapshot_bin_inventory(mgr, pre_snap, batch_id=1, run_id=1,
                                            full_snapshot=False) == []
    full = _archived_snapshot_bin_inventory(mgr, pre_snap, batch_id=0, run_id=1,
                                            full_snapshot=True)
    assert len(full) == 1 and full[0].pre_qty == full[0].post_qty


def test_archived_restock_writes_no_delta_row():
    """Invariant 3 — the writer gap that made the bin-mutation log necessary, pinned.

    A bin restocked by check_reorders() is ALREADY in pre_snap (reorders run first), at its
    post-restock quantity.  If nothing then picks from it, `post_qty == pre_qty` and the row is
    skipped — so the restock is invisible in `bin_inventory`.  On the production run this was
    exact: 0 rows with post_qty > pre_qty against 20k-42k reorder_placements per batch.

    This is asserted of the ARCHIVE, and it is permanent: the writer is gone and those files are
    never rewritten, so anything reading a `bin_inventory` table must assume this forever.  It
    was NOT "fixed" — it was superseded.  Current runs record `bin_placement`, where a restock
    is an explicit row rather than an inference from a missing one.
    """
    restocked = _Bin(7, 5, 1, _Unit(sku=42, quantity=25))    # refilled before the snapshot
    mgr = _Manager([restocked])
    pre_snap = build_pre_snapshot(mgr)

    recs = _archived_snapshot_bin_inventory(mgr, pre_snap, batch_id=3, run_id=1,
                                            full_snapshot=False)

    assert recs == [], 'a restocked-but-unpicked bin wrote no delta row in the archive'
    assert not any(r.post_qty > r.pre_qty for r in recs), (
        'bin_inventory is a pure depletion log; a row showing a GAIN would mean this frozen '
        'transcription has drifted from the writer that produced the archive')


def test_archived_rolling_deltas_from_a_keyframe_loses_restocked_bins():
    """Invariant 4 — why keyframes were canonical in the archive, in miniature.

    Reproduces the production failure at 2-bin scale: replay the reader's old algorithm
    (keyframe + post_qty deltas) across a batch in which one bin was picked empty and another
    was restocked.  The restocked bin is missing from the rolled state and present in the
    next keyframe.  On the real run this gap is 97,248 of 165,519 bins (59%) after 5 batches.

    For an archived arm this is still the best that record can do, which is why
    `Visualization/readers/base.py` reports `exact: false` off a keyframe.  For a run from this
    build the same frame is exact at every batch, folded from the log instead.
    """
    emptied = _Bin(7, 2, 3, _Unit(sku=99, quantity=4))
    restocked = _Bin(7, 5, 1, None)                          # empty at the batch-0 keyframe
    mgr = _Manager([emptied, restocked])

    def keyframe():
        """The occupied-bin snapshot save_bin_keyframe would write from pre_snap."""
        return {b.location: (b.storage.order.sku, b.storage.quantity)
                for b in (emptied, restocked)
                if b.storage is not None and b.storage.quantity > 0}

    # --- batch 0: keyframe (only `emptied` is occupied), then it is picked to zero
    kf0 = keyframe()
    assert kf0 == {(7, 2, 3): (99, 4)}

    pre_snap = build_pre_snapshot(mgr)
    emptied.storage.quantity = 0
    deltas = _archived_snapshot_bin_inventory(mgr, pre_snap, batch_id=0, run_id=1,
                                              full_snapshot=False)

    # --- between batches: check_reorders() refills the other bin
    restocked.storage = _Unit(sku=42, quantity=25)

    # --- batch 1 keyframe: the truth
    kf1 = keyframe()
    assert kf1 == {(7, 5, 1): (42, 25)}

    # --- the old reader: roll kf0 forward with post_qty deltas
    rolled = dict(kf0)
    for r in deltas:
        key = (r.aisle_id, r.bayX, r.bayY)
        if r.post_qty > 0:
            rolled[key] = (r.sku, r.post_qty)
        else:
            rolled.pop(key, None)

    assert rolled == {}, 'the roll correctly drops the emptied bin'
    missing = set(kf1) - set(rolled)
    assert missing == {(7, 5, 1)}, (
        'the restocked bin is invisible to a delta roll — which is why state_at() must read '
        'the keyframe rather than replaying deltas')


def test_reconstruction_query_is_exact_at_a_keyframe():
    """Invariant 1: keyframe qty minus picks up to t is the exact state at (B, t).

    This is the query `state_at(B, t)` implements for a keyframe batch, run against real SQL.
    """
    db = _tmp('sim_A.db')
    init_run_db(db)
    init_keyframe_db(db)                 # same file so the join is a plain query
    con = sqlite3.connect(db)
    con.execute('INSERT INTO bin_keyframe VALUES (1,5,7,2,3,99,"pallet","large",10)')
    for t in (1.0, 2.0, 3.0):
        con.execute(
            'INSERT INTO picks (run_id,batch_id,picker_id,sim_time,aisle_id,bayX,bayY,sku,quantity) '
            'VALUES (1,5,0,?,7,2,3,99,2)', (t,))
    con.commit()

    def qty_at(t):
        return con.execute("""
            WITH picked AS (
                SELECT aisle_id, bayX, bayY, SUM(quantity) AS n
                FROM   picks
                WHERE  run_id=1 AND batch_id=5 AND sim_time <= ?
                GROUP  BY aisle_id, bayX, bayY)
            SELECT MAX(0, k.qty - COALESCE(p.n, 0))
            FROM   bin_keyframe k LEFT JOIN picked p USING (aisle_id, bayX, bayY)
            WHERE  k.run_id=1 AND k.batch_id=5
        """, (t,)).fetchone()[0]

    try:
        assert qty_at(0.0) == 10, 't=0 is the untouched keyframe quantity'
        assert qty_at(2.0) == 6, 'two picks of 2 applied'
        assert qty_at(9.0) == 4, 'all three picks applied; never negative'
    finally:
        con.close()


def test_batch_list_comes_from_batch_stats_not_a_range():
    """A batch that produced no tasks writes no batch_stats row — the batch list has holes.

    `strategy_runner` `continue`s before appending stats, so iterating range(n_batches) walks
    batches that have no timing, no events and no deltas.
    """
    db = _tmp('sim_holes.db')
    init_run_db(db)
    rid = create_run(db, 'uni_fifo_norsl', params=dict(n_batches=5))
    save_batch_stats(db, rid, [
        BatchStats(run_id=rid, batch_id=b, duration=1.0, num_tasks=1, total_items=1,
                   avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5)
        for b in (0, 1, 3, 4)                                  # batch 2 produced no tasks
    ])
    present = sorted(b.batch_id for b in load_batch_stats(db, rid))

    assert present == [0, 1, 3, 4]
    assert len(present) != 5, 'n_batches over-counts; build the batch list from batch_stats'
