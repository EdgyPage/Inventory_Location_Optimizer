"""test_viz_cache.py — the derived viewer sidecar (`_viz/**/<arm>.viz.db`).

The cache exists to make four things cheap that are otherwise 0.25 s-24 s per request. It is only
worth having if it is *exactly* equivalent to reading the source, so the load-bearing assertion
here is the one that would have caught the reconstruction bug documented in RECONSTRUCTION.md §1:

    at EVERY keyframe, the set of (bin -> sku) rebuilt from `bin_span` must equal the keyframe,
    bin for bin.

Plus: rollup occupancy matches a live `state_at`, staleness is detected on both size and mtime,
a build that dies leaves nothing behind, and an arm with no keyframes is refused rather than
cached as a quietly depletion-only approximation.

    python -m pytest Tests/integration/test_viz_cache.py -q
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from Optimization.persistence.Picking_Data import (
    create_run, init_keyframe_db, init_run_db, keyframe_db_path,
    save_batch_stats, save_bin_keyframe, save_picks, BatchStats, PickRecord,
)
from Optimization.persistence.Warehouse_Data import init_warehouse_db, save_aisle_layout
from Visualization.cache_schema import CACHE_VERSION, TABLES
from Visualization.db_reader import RunRef
from Visualization.precompute import build_one, cache_state

# A 2-aisle, 2x3-bin warehouse over 6 batches with keyframes every 2 — small enough to reason
# about by hand, big enough to exercise span opening/closing, a restock, and an emptied bin.
KEYFRAME_INTERVAL = 2
N_BATCHES = 6
AISLES = [
    dict(aisle_id=1, handling_type='conveyable', category='food',
         unit_type='pallet', storage_size='large', bay_x=2, bay_y=3),
    dict(aisle_id=2, handling_type='non-conveyable', category='chemical',
         unit_type='singleton', storage_size='singleton', bay_x=2, bay_y=3),
]

# (batch, aisle, bayX, bayY, sku, qty) at each keyframe.  Deliberately includes:
#   (1,1,1) holds sku 10 across kf 0-2 then switches to 11   -> a span that closes on sku change
#   (1,2,1) holds sku 20 at kf 0 then vanishes               -> a span that closes on emptying
#   (2,1,1) is empty until kf 2, then appears                -> a span that opens late (a RESTOCK,
#                                                               invisible to the delta stream)
KEYFRAMES = {
    0: [(1, 1, 1, 10, 5), (1, 2, 1, 20, 4), (1, 1, 2, 12, 9)],
    2: [(1, 1, 1, 10, 3), (1, 1, 2, 12, 7), (2, 1, 1, 30, 6)],
    4: [(1, 1, 1, 11, 8), (1, 1, 2, 12, 5), (2, 1, 1, 30, 2)],
}


@pytest.fixture
def arm(tmp_path):
    """A complete miniature arm: warehouse.db + sim.db + keyframes.db, wrapped in a RunRef."""
    leaf = tmp_path / 'k1' / 'pairA' / 'store' / 'store'
    leaf.mkdir(parents=True)
    wh = str(tmp_path / 'k1' / 'pairA' / 'warehouse.db')
    init_warehouse_db(wh)
    save_aisle_layout(wh, AISLES)

    sim = str(leaf / 'sim_uni_fifo_norsl.db')
    init_run_db(sim)
    run_id = create_run(sim, 'uni_fifo_norsl',
                        params=dict(n_batches=N_BATCHES, keyframe_interval=KEYFRAME_INTERVAL),
                        identity=dict(strategy_key='uni_fifo_norsl', pair_label='pairA',
                                      config_label='store', channel='store',
                                      warehouse_fingerprint='fp0'))
    save_batch_stats(sim, run_id, [
        BatchStats(run_id=run_id, batch_id=b, duration=10.0 + b, num_tasks=1, total_items=2,
                   avg_concurrent_pickers=1.0, picking_pct=0.5, traveling_pct=0.5,
                   reorder_placements=3 if b else 0)
        for b in range(N_BATCHES)])
    save_picks(sim, run_id, [
        PickRecord(run_id=run_id, batch_id=b, picker_id=0, sim_time=1.0 + b,
                   aisle_id=1, bayX=1, bayY=1, sku=10 if b < 4 else 11, quantity=1)
        for b in range(N_BATCHES)])

    kf = keyframe_db_path(sim)
    init_keyframe_db(kf)
    for batch, rows in KEYFRAMES.items():
        save_bin_keyframe(kf, run_id, batch, [
            dict(aisle_id=a, bayX=x, bayY=y, sku=s, unit_type='pallet',
                 storage_size='large', qty=q) for a, x, y, s, q in rows])

    return RunRef(
        id='k1/pairA/store/store/uni_fifo_norsl', label='mini',
        cell='k1', pair='pairA', config='store', channel='store', strategy='uni_fifo_norsl',
        sim_db=sim, warehouse_db=wh, keyframe_db=kf, run_id=run_id, n_batches=N_BATCHES,
        warehouse_fingerprint='fp0',
        viz_cache=str(tmp_path / '_viz' / 'k1' / 'pairA' / 'store' / 'store'
                      / 'uni_fifo_norsl.viz.db'),
    )


def _cache(arm):
    return sqlite3.connect(f'file:{arm.viz_cache.replace(os.sep, "/")}?mode=ro', uri=True)


# ── the load-bearing invariant ───────────────────────────────────────────────────

def test_bin_span_reproduces_every_keyframe_bin_for_bin(arm):
    """The assertion that would have caught the 59% reconstruction gap.

    Verified the same way against the production run: 20/20 keyframes, zero mismatches.
    """
    build_one(arm)
    con = _cache(arm)
    try:
        for batch, rows in KEYFRAMES.items():
            truth = {(a, x, y): s for a, x, y, s, _q in rows}
            spanned = {(r[0], r[1], r[2]): r[3] for r in con.execute(
                'SELECT aisle_id, bayX, bayY, sku FROM bin_span '
                'WHERE run_id=? AND t_from<=? AND t_to>=?', (arm.run_id, batch, batch))}
            assert spanned == truth, f'keyframe {batch} does not round-trip through bin_span'
    finally:
        con.close()


def test_spans_close_on_sku_change_and_on_emptying(arm):
    build_one(arm)
    con = _cache(arm)
    try:
        spans = {(r[0], r[1], r[2]): [] for r in con.execute(
            'SELECT aisle_id, bayX, bayY FROM bin_span WHERE run_id=?', (arm.run_id,))}
        for r in con.execute('SELECT aisle_id, bayX, bayY, t_from, t_to, sku FROM bin_span '
                             'WHERE run_id=? ORDER BY aisle_id, bayX, bayY, t_from',
                             (arm.run_id,)):
            spans[(r[0], r[1], r[2])].append((r[3], r[4], r[5]))
    finally:
        con.close()

    # sku 10 spans keyframes 0..2, then sku 11 opens a new span at 4
    assert spans[(1, 1, 1)] == [(0, 2, 10), (4, 4, 11)]
    # emptied after keyframe 0 — the span must not run on
    assert spans[(1, 2, 1)] == [(0, 0, 20)]
    # a RESTOCK: the bin is empty at keyframe 0 and occupied from 2.  Invisible in the delta
    # stream (no post_qty > pre_qty row is ever written), which is why spans come from keyframes.
    assert spans[(2, 1, 1)] == [(2, 4, 30)]


def test_rollup_occupancy_matches_a_live_state_read(arm):
    build_one(arm)
    reader = arm.reader()
    for batch in KEYFRAMES:
        live = {}
        for key in reader.state_at(batch)['bins']:
            aisle = int(key.split(',', 1)[0])
            live[aisle] = live.get(aisle, 0) + 1
        cached = {r['aisle_id']: r['occupied'] for r in reader.aisle_rollup(batch)}
        for aisle in (1, 2):
            assert cached[aisle] == live.get(aisle, 0), f'batch {batch} aisle {aisle}'


def test_rollup_covers_every_batch_including_non_keyframe_ones(arm):
    build_one(arm)
    con = _cache(arm)
    try:
        batches = sorted({r[0] for r in con.execute(
            'SELECT DISTINCT batch_id FROM aisle_batch_rollup WHERE run_id=?', (arm.run_id,))})
    finally:
        con.close()
    assert batches == list(range(N_BATCHES))


# ── final_home: the colour authority ─────────────────────────────────────────────

def test_final_home_records_the_aisle_SET_not_just_one_bin(arm):
    """64.3% of SKUs on the real run hold several bins, and only 9.7% of those keep every
    replica in one aisle — so `home_aisles` is what "is this item home?" must test against."""
    build_one(arm)
    homes = arm.reader().final_home()

    assert homes['batch'] == 4, 'final means the LAST keyframe, and it is recorded explicitly'
    assert set(homes['homes']) == {'11', '12', '30'}
    assert homes['homes']['30']['home_aisles'] == [2]
    assert homes['homes']['11']['n_homes'] == 1
    for home in homes['homes'].values():
        assert home['aisle_id'] in home['home_aisles'], 'the primary home must be in the set'


def test_final_home_primary_is_the_largest_qty_bin(tmp_path, arm):
    """Ties break on the lowest coordinate so the choice is deterministic across rebuilds."""
    save_bin_keyframe(arm.keyframe_db, arm.run_id, 4, [
        dict(aisle_id=2, bayX=2, bayY=1, sku=11, unit_type='pallet',
             storage_size='large', qty=99)])
    build_one(arm, force=True)
    home = arm.reader().final_home()['homes']['11']

    assert (home['aisle_id'], home['bayX'], home['bayY']) == (2, 2, 1), 'largest qty wins'
    assert home['n_homes'] == 2
    assert home['home_aisles'] == [1, 2]


# ── ranking ──────────────────────────────────────────────────────────────────────

def test_sku_rank_covers_every_sku_while_series_is_capped(arm):
    """`sku_rank` holds ALL SKUs so the reader can say "rank 3,201, outside the cached window"
    rather than returning an empty series that reads as "never picked"."""
    build_one(arm, top_n=1)
    con = _cache(arm)
    try:
        ranked = {r[0] for r in con.execute('SELECT sku FROM sku_rank WHERE run_id=?',
                                            (arm.run_id,))}
        series = {r[0] for r in con.execute('SELECT DISTINCT sku FROM sku_series WHERE run_id=?',
                                            (arm.run_id,))}
    finally:
        con.close()
    assert ranked == {10, 11}, 'every picked SKU is ranked'
    assert len(series) == 1, 'the series window honours --top-n'
    assert series < ranked


def test_sku_series_does_not_drop_skus_outside_the_cached_window(arm):
    """A partial cache hit must not read as "this SKU was never picked".

    `sku_series` in the sidecar holds only the top-N window, so a request mixing an in-window
    and an out-of-window SKU gets rows for one of them. Treating that as the complete answer is
    exactly the failure `sku_rank` exists to prevent — the out-of-window SKU would plot as a
    flat zero line rather than falling through to the (indexed, cheap) live query.
    """
    build_one(arm, top_n=1)                      # only sku 10 is in the series window
    series = arm.reader().sku_series([10, 11])

    assert set(series) == {'10', '11'}, 'sku 11 is outside the window but WAS picked'
    assert series['11'], 'sku 11 came back with no points — it would plot as never picked'
    assert sum(p['units'] for p in series['11']) == 2
    assert sum(p['units'] for p in series['10']) == 4


def test_a_stale_cache_is_not_used_or_advertised(arm):
    """Freshness is checked on the READ path, not only at build time.

    A strategy-granularity resume rewrites `sim_<arm>.db` from scratch and its run_id restarts
    at 1, so a stale sidecar's rows collide by key: without this the viewer would keep serving
    the PREVIOUS run's final_home (the colour authority) with nothing to signal it.
    """
    build_one(arm)
    reader = arm.reader()
    assert reader.cache_status() == 'fresh'
    assert 'viz_cache' in reader.capabilities()

    save_picks(arm.sim_db, arm.run_id, [                     # the sim DB grows -> cache is stale
        PickRecord(run_id=arm.run_id, batch_id=5, picker_id=0, sim_time=9.0,
                   aisle_id=1, bayX=1, bayY=2, sku=12, quantity=1)])

    assert reader.cache_status() == 'stale'
    assert 'viz_cache' not in reader.capabilities(), 'a stale cache is a hazard, not a capability'
    # ...and the reader must fall through to live data rather than serving the stale rows.
    assert 12 in {t['sku'] for t in reader.top_skus(10)}


def test_a_cache_built_after_binding_is_picked_up(arm):
    """Opening the viewer and THEN precomputing is the normal order.

    Existence must not be pinned at bind time, or the arm stays on the slow path until restart
    while /api/runs advertises a cache.
    """
    reader = arm.reader()                        # bound while the sidecar does not exist
    assert reader.cache_status() == 'absent'
    build_one(arm)
    assert reader.cache_status() == 'fresh'
    assert 'viz_cache' in reader.capabilities()


def test_top_skus_orders_by_units(arm):
    build_one(arm)
    top = arm.reader().top_skus(10)
    assert [t['sku'] for t in top] == [10, 11], 'sku 10 has 4 picks, sku 11 has 2'
    assert [t['rank'] for t in top] == [1, 2]
    assert top[0]['units'] == 4 and top[0]['first_batch'] == 0


# ── freshness, atomicity, refusal ────────────────────────────────────────────────

def test_cache_state_transitions(arm):
    assert cache_state(arm) == 'absent'
    build_one(arm)
    assert cache_state(arm) == 'fresh'
    assert build_one(arm)['status'] == 'fresh', 'a fresh cache is not rebuilt'


def test_a_changed_source_makes_the_cache_stale(arm):
    build_one(arm)
    assert cache_state(arm) == 'fresh'
    save_picks(arm.sim_db, arm.run_id, [
        PickRecord(run_id=arm.run_id, batch_id=5, picker_id=0, sim_time=9.0,
                   aisle_id=1, bayX=1, bayY=2, sku=12, quantity=1)])
    assert cache_state(arm) == 'stale', 'a grown sim DB must invalidate the cache'


def test_a_version_bump_makes_the_cache_stale(arm):
    build_one(arm)
    con = sqlite3.connect(arm.viz_cache)
    try:
        con.execute("UPDATE cache_meta SET value=? WHERE key='cache_version'",
                    (str(CACHE_VERSION + 1),))
        con.commit()
    finally:
        con.close()
    assert cache_state(arm) == 'stale'


def test_an_unfinished_build_is_partial_not_fresh(arm):
    """The build writes `built_utc` last and renames into place atomically, so a crash can only
    leave a `.tmp` — but a cache whose meta lacks it must never be trusted."""
    build_one(arm)
    con = sqlite3.connect(arm.viz_cache)
    try:
        con.execute("DELETE FROM cache_meta WHERE key='built_utc'")
        con.commit()
    finally:
        con.close()
    assert cache_state(arm) == 'partial'


def test_build_is_atomic(arm):
    build_one(arm)
    assert not os.path.exists(arm.viz_cache + '.tmp'), 'the temp build file must be renamed away'
    assert os.path.exists(arm.viz_cache)


def test_an_arm_without_keyframes_is_refused(arm):
    """Better a loud skip than a cache whose spatial tables silently cannot see restocks."""
    arm.keyframe_db = ''
    res = build_one(arm)
    assert res['status'] == 'skipped'
    assert 'keyframe' in res['error']
    assert not os.path.exists(arm.viz_cache)


def test_the_schema_id_is_pinned_so_readers_stop_deriving(arm):
    """Every archived DB predates the stamp; deriving once and recording it here is what keeps
    the reader from re-deriving on every request."""
    build_one(arm)
    con = _cache(arm)
    try:
        meta = {r[0]: r[1] for r in con.execute('SELECT key, value FROM cache_meta')}
        present = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    assert meta['sim_schema_id'] == arm.reader().schema_id()
    assert meta['keyframe_interval'] == str(KEYFRAME_INTERVAL)
    assert int(meta['n_spans']) > 0
    assert set(TABLES) <= present, sorted(set(TABLES) - present)
