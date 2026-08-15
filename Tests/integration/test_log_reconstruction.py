"""test_log_reconstruction.py — the acceptance test: the VIEWER is exact at every batch.

`Tests/integration/test_bin_log_replay.py` proved the log is sufficient in principle — folding
PLACE + EVICT + PICK reproduces the simulation's own bins. This proves the thing that was actually
wrong: that the reader and its precomputed span index deliver that exactness through the real
persistence path, at batches that are **not** keyframes.

The decisive assertion
----------------------
`state_at(B)` at a NON-keyframe batch equals ground truth, bin for bin, and reports `exact: True`.
That is the property the old contract could not offer: `bin_inventory` recorded picks and never
restocks, so a frame between two keyframes was rebuilt by depletion alone and lost 59% of a real
warehouse within five batches (`RECONSTRUCTION.md` §1).

Ground truth is not another query — it is the warehouse's own `Aisle.Bin` objects, captured per
batch by `Tests/bench/bin_log_harness.py` while a real (small) simulation runs. The DB below is
written from that same run, so what is under test is the round trip:

    live sim -> bin_placement/bin_eviction/picks -> precompute.bin_span -> reader.state_at

All three of the reader's paths are exercised: the cached span index, the live log fold (no
sidecar), and — via the negative control, which deletes the log — the pre-log keyframe path, which
must go back to reporting `exact: false` rather than quietly guessing.

    python -m pytest Tests/integration/test_log_reconstruction.py -q
"""
from __future__ import annotations

import os
import shutil

import pytest

import bin_log_harness as H          # Tests/bench is on sys.path via Tests/conftest.py

from Optimization.persistence.Picking_Data import (
    BatchStats, BinEvictionRecord, BinPlacementRecord, PickRecord,
    create_run, init_keyframe_db, init_run_db, keyframe_db_path,
    save_batch_stats, save_bin_evictions, save_bin_keyframe, save_bin_placements, save_picks,
)
from Optimization.persistence.Warehouse_Data import init_warehouse_db, save_aisle_layout
from Visualization.cache_schema import SPAN_SOURCE_KEYFRAME, SPAN_SOURCE_LOG
from Visualization.db_reader import RunRef
from Visualization.precompute import build_one

N_SKUS = 200
N_BATCHES = 8
#: Keyframes at 0 and 5 only, so six of the eight batches have no snapshot to fall back on.
#: Without a gap this test could pass on the old, keyframe-canonical reader.
KEYFRAME_INTERVAL = 5

ARM = 'uni_fifo_norsl'


# ── writing the harness run into a real run tree ─────────────────────────────────

def _seq_numbered(records):
    """Assign each batch's events a monotone `seq`, in application order.

    The recorders (`bin_log_harness.attach_recorder` and the production
    `Optimization/metrics/bin_recorder.BinRecorder`) both open on batch 0 for the initial fill and
    then reset the counter when the batch LOOP begins on batch 0 — so the initial placements and
    batch 0's reorder placements can share `seq` values. That is harmless in the harness's plain
    list, but `bin_placement`'s primary key is `(run_id, batch_id, seq)`, so persisting them
    verbatim would let one set replace the other. Numbering here keeps the log complete; see the
    note in this test's report.
    """
    seen: dict[int, int] = {}
    for rec in records:
        seq = seen.get(rec.batch, 0)
        seen[rec.batch] = seq + 1
        yield seq, rec


def _write_run(root, log, frames, warehouse):
    """Persist one harness run as a real arm: warehouse.db + sim.db + keyframes.db."""
    leaf = os.path.join(root, 'k1', 'pairA', 'store', 'store')
    os.makedirs(leaf, exist_ok=True)

    wh_db = os.path.join(root, 'k1', 'pairA', 'warehouse.db')
    init_warehouse_db(wh_db)
    save_aisle_layout(wh_db, [
        dict(aisle_id=a.aisle_id, handling_type=a.handling_type, category=a.storage_type,
             unit_type=a.unit_type, storage_size=a.storage_size,
             bay_x=a.bayXPerAisle, bay_y=a.bayYPerAisle)
        for a in warehouse.aisles])
    tiers = {a.aisle_id: (a.unit_type, a.storage_size) for a in warehouse.aisles}

    sim_db = os.path.join(leaf, f'sim_{ARM}.db')
    init_run_db(sim_db)
    run_id = create_run(sim_db, ARM,
                        params=dict(n_batches=N_BATCHES, keyframe_interval=KEYFRAME_INTERVAL),
                        identity=dict(strategy_key=ARM, pair_label='pairA',
                                      config_label='store', channel='store',
                                      warehouse_fingerprint='fp0'))

    placed = {b: 0 for b, _a, _p, _t in frames}
    for p in log.places:
        placed[p.batch] = placed.get(p.batch, 0) + 1
    save_batch_stats(sim_db, run_id, [
        BatchStats(run_id=run_id, batch_id=b, duration=10.0 + b,
                   num_tasks=1, total_items=1, avg_concurrent_pickers=1.0,
                   picking_pct=0.5, traveling_pct=0.5, reorder_placements=placed.get(b, 0))
        for b, _after, _final, _max_t in frames])

    save_picks(sim_db, run_id, [
        PickRecord(run_id=run_id, batch_id=k.batch, picker_id=0, sim_time=k.t,
                   aisle_id=k.loc[0], bayX=k.loc[1], bayY=k.loc[2],
                   sku=k.sku, quantity=k.qty)
        for k in log.picks])
    save_bin_placements(sim_db, run_id, [
        BinPlacementRecord(run_id=run_id, batch_id=p.batch, seq=seq, aisle_id=p.loc[0],
                           bayX=p.loc[1], bayY=p.loc[2], sku=p.sku, qty=p.qty, cause=p.cause)
        for seq, p in _seq_numbered(log.places)])
    save_bin_evictions(sim_db, run_id, [
        BinEvictionRecord(run_id=run_id, batch_id=e.batch, seq=seq, aisle_id=e.loc[0],
                          bayX=e.loc[1], bayY=e.loc[2], sku=e.sku, qty=e.qty)
        for seq, e in _seq_numbered(log.evicts)])

    # Keyframes exactly as `strategy_runner` writes them: the post-restock, pre-pick state at the
    # start of every KEYFRAME_INTERVAL-th batch.  They are the audit and the qty anchor now, not
    # the reconstruction mechanism.
    kf_db = keyframe_db_path(sim_db)
    init_keyframe_db(kf_db)
    for batch, after_reorders, _final, _max_t in frames:
        if batch % KEYFRAME_INTERVAL:
            continue
        save_bin_keyframe(kf_db, run_id, batch, [
            dict(aisle_id=loc[0], bayX=loc[1], bayY=loc[2], sku=sku,
                 unit_type=tiers[loc[0]][0], storage_size=tiers[loc[0]][1], qty=qty)
            for loc, (sku, qty) in after_reorders.items()])

    return sim_db, wh_db, kf_db, run_id


def _ref(root, sim_db, wh_db, kf_db, run_id, tag='log'):
    return RunRef(
        id=f'k1/pairA/store/store/{ARM}', label='log-backed mini run',
        cell='k1', pair='pairA', config='store', channel='store', strategy=ARM,
        sim_db=sim_db, warehouse_db=wh_db, keyframe_db=kf_db, run_id=run_id,
        n_batches=N_BATCHES, warehouse_fingerprint='fp0',
        viz_cache=os.path.join(root, '_viz', tag, f'{ARM}.viz.db'))


@pytest.fixture(scope='module')
def run(tmp_path_factory):
    """A real (small) simulation, its ground truth per batch, and its arm on disk."""
    root = str(tmp_path_factory.mktemp('logrecon'))
    inv, warehouse, mgr = H.build_scenario(n_skus=N_SKUS)
    log = H.attach_recorder(mgr)
    frames = H.run_sim(inv, warehouse, mgr, log, n_batches=N_BATCHES)
    sim_db, wh_db, kf_db, run_id = _write_run(root, log, frames, warehouse)
    arm = _ref(root, sim_db, wh_db, kf_db, run_id)
    build_one(arm)
    return {'root': root, 'arm': arm, 'frames': frames, 'log': log,
            'sim_db': sim_db, 'wh_db': wh_db, 'kf_db': kf_db, 'run_id': run_id}


def _bins(state) -> dict:
    """A `state_at` payload as the harness's `{(aisle, bayX, bayY): (sku, qty)}` ground truth."""
    return {tuple(int(p) for p in key.split(',')): (int(v['sku']), int(v['qty']))
            for key, v in state['bins'].items()}


def _diff(got, truth) -> str:
    missing = set(truth) - set(got)
    ghosts = set(got) - set(truth)
    wrong = [k for k in set(got) & set(truth) if got[k] != truth[k]]
    return (f'{len(missing)} bins missing, {len(ghosts)} ghosts, '
            f'{len(wrong)} differing in sku/qty')


def _non_keyframe_batches(frames):
    return [b for b, _a, _f, _t in frames if b % KEYFRAME_INTERVAL]


# ── the scenario must actually put the claim at risk ─────────────────────────────

def test_the_run_has_batches_no_keyframe_covers(run):
    """Vacuity guard. With a keyframe on every batch, the OLD reader would pass every assertion
    below and the test would prove nothing."""
    gaps = _non_keyframe_batches(run['frames'])
    assert len(gaps) >= 5, f'only {len(gaps)} non-keyframe batches; the gap is what is under test'
    assert run['arm'].reader().keyframe_batches() == [0, 5]


def test_the_run_actually_restocks_between_keyframes(run):
    """The restock is the term the old record dropped; if none fired, nothing is being tested."""
    between = [p for p in run['log'].places if p.batch in _non_keyframe_batches(run['frames'])]
    assert len(between) > 50, f'only {len(between)} placements off the keyframe grid'


def test_the_cache_was_built_from_the_log(run):
    """`span_source` is what licenses the reader to call an off-keyframe frame exact."""
    meta = _cache_meta(run['arm'])
    assert meta['span_source'] == SPAN_SOURCE_LOG
    assert int(meta['n_spans']) > 0
    assert meta['final_home_batch'] == str(N_BATCHES - 1), 'final means the last BATCH now'


def _cache_meta(arm) -> dict:
    import sqlite3
    con = sqlite3.connect(f'file:{arm.viz_cache.replace(os.sep, "/")}?mode=ro', uri=True)
    try:
        return {k: v for k, v in con.execute('SELECT key, value FROM cache_meta')}
    finally:
        con.close()


# ── the acceptance assertion ─────────────────────────────────────────────────────

def test_state_at_a_non_keyframe_batch_equals_ground_truth(run):
    """THE assertion this whole change exists for.

    Every batch with no keyframe under it, rebuilt through the span index, must equal the
    warehouse's own bin objects — sku AND qty, bin for bin.
    """
    reader = run['arm'].reader()
    for batch, after_reorders, _final, _max_t in run['frames']:
        if not batch % KEYFRAME_INTERVAL:
            continue
        state = reader.state_at(batch)
        got = _bins(state)
        assert got == after_reorders, f'batch {batch} (no keyframe): {_diff(got, after_reorders)}'
        assert state['exact'] is True, f'batch {batch} was rebuilt but not reported exact'
        assert state['restocks_pending'] == 0


def test_state_at_is_exact_at_every_batch(run):
    """Keyframe batches included — the new path must not regress what already worked."""
    reader = run['arm'].reader()
    for batch, after_reorders, _final, _max_t in run['frames']:
        state = reader.state_at(batch)
        assert state['exact'] is True, f'batch {batch}'
        assert _bins(state) == after_reorders, f'batch {batch}: {_diff(_bins(state), after_reorders)}'


def test_the_end_of_batch_frame_is_exact_too(run):
    """`state_at(B)` is the START of batch B; past the last pick it must be the END of it.

    This is the intra-batch clock the animation scrubs, and it is served by `picks.sim_time`
    on top of the same span index.
    """
    reader = run['arm'].reader()
    for batch, _after, after_picks, max_t in run['frames']:
        state = reader.state_at(batch, t=max_t + 1.0)
        assert _bins(state) == after_picks, f'batch {batch}: {_diff(_bins(state), after_picks)}'
        assert state['exact'] is True


def test_a_scoped_frame_matches_the_same_aisle_of_the_full_frame(run):
    """The aisle view asks for one aisle; scoping must not change the answer."""
    reader = run['arm'].reader()
    batch = _non_keyframe_batches(run['frames'])[-1]
    full = _bins(reader.state_at(batch))
    aisle = sorted({loc[0] for loc in full})[0]
    scoped = _bins(reader.state_at(batch, aisles=[aisle]))

    assert scoped == {loc: v for loc, v in full.items() if loc[0] == aisle}
    assert scoped, 'the scoped frame is empty; the comparison would be vacuous'


# ── the same exactness without a sidecar ─────────────────────────────────────────

def test_the_live_fold_is_exact_without_any_cache(run):
    """No sidecar: the reader folds the log forward from the nearest keyframe instead.

    Deleting `_viz/` must cost time, never correctness — that is the contract the cache is built
    on, and it is only true if the live path is exact as well.
    """
    bare = _ref(run['root'], run['sim_db'], run['wh_db'], run['kf_db'], run['run_id'],
                tag='absent')
    reader = bare.reader()
    assert reader.cache_status() == 'absent'

    for batch, after_reorders, _final, _max_t in run['frames']:
        state = reader.state_at(batch)
        assert state['exact'] is True, f'batch {batch} on the live path'
        assert _bins(state) == after_reorders, f'batch {batch}: {_diff(_bins(state), after_reorders)}'


def test_the_run_advertises_the_log_as_a_capability(run):
    """The UI decides whether to label a frame exact from this, so it must be probed, not assumed."""
    assert 'bin_log' in run['arm'].reader().capabilities()


# ── eviction: the one mutation with no production data behind it ─────────────────

@pytest.fixture(scope='module')
def reslot(tmp_path_factory):
    """A run that RE-SLOTS, so `bin_eviction` is non-empty.

    Every shipped arm is `norsl`, so eviction is the only bin mutation with no production data
    behind it — a span index that mishandled it would look perfect on the archive and be wrong
    the first time re-slotting shipped. It has to be exercised synthetically or not at all.
    """
    root = str(tmp_path_factory.mktemp('reslot'))
    inv, warehouse, mgr = H.build_scenario(n_skus=N_SKUS, seed=7)
    log = H.attach_recorder(mgr)
    frames = H.run_sim(inv, warehouse, mgr, log, n_batches=N_BATCHES,
                       reloader=H.make_reloader(move_limit_pct=0.5))
    sim_db, wh_db, kf_db, run_id = _write_run(root, log, frames, warehouse)
    arm = _ref(root, sim_db, wh_db, kf_db, run_id)
    build_one(arm)
    return arm, frames, log


def test_the_reslot_scenario_actually_evicts(reslot):
    _arm, _frames, log = reslot
    assert len(log.evicts) > 50, 'no evictions; the EVICT path is untested'
    assert sum(1 for p in log.places if p.cause == 'reslot') > 50, 'nothing was re-placed'


def test_state_at_is_exact_at_every_batch_with_reslotting(reslot):
    """A bin the reloader emptied must not linger as a ghost, and its unit must appear in the
    bin it moved TO — at the batch it moved, not at the next keyframe."""
    arm, frames, _log = reslot
    reader = arm.reader()
    for batch, after_reorders, _final, _max_t in frames:
        state = reader.state_at(batch)
        assert state['exact'] is True, f'batch {batch}'
        got = _bins(state)
        assert got == after_reorders, f'batch {batch}: {_diff(got, after_reorders)}'


# ── the rollup the sidecar serves is exact per batch too ─────────────────────────

def test_aisle_rollup_occupancy_is_exact_at_every_batch(run):
    """`aisle_batch_rollup` used to inherit the nearest keyframe, so occupancy STEPPED between
    them. Built from the log it is a per-batch prefix sum, and must equal the truth exactly."""
    reader = run['arm'].reader()
    for batch, after_reorders, _final, _max_t in run['frames']:
        want_occ, want_qty = {}, {}
        for (aisle, _bx, _by), (_sku, qty) in after_reorders.items():
            want_occ[aisle] = want_occ.get(aisle, 0) + 1
            want_qty[aisle] = want_qty.get(aisle, 0) + qty
        for row in reader.aisle_rollup(batch):
            aisle = row['aisle_id']
            assert row['occupied'] == want_occ.get(aisle, 0), f'batch {batch} aisle {aisle} occupied'
            assert row['qty'] == want_qty.get(aisle, 0), f'batch {batch} aisle {aisle} qty'


# ── negative control: the assertions above must be capable of failing ────────────

@pytest.fixture(scope='module')
def crippled(run, tmp_path_factory):
    """The same run with its placement log deleted — i.e. what every archived arm looks like."""
    root = str(tmp_path_factory.mktemp('nolog'))
    leaf = os.path.join(root, 'k1', 'pairA', 'store', 'store')
    os.makedirs(leaf)
    sim_db = os.path.join(leaf, os.path.basename(run['sim_db']))
    shutil.copy2(run['sim_db'], sim_db)
    kf_db = keyframe_db_path(sim_db)
    shutil.copy2(run['kf_db'], kf_db)
    wh_db = os.path.join(root, 'k1', 'pairA', 'warehouse.db')
    os.makedirs(os.path.dirname(wh_db), exist_ok=True)
    shutil.copy2(run['wh_db'], wh_db)

    import sqlite3
    con = sqlite3.connect(sim_db)
    try:
        con.execute('DELETE FROM bin_placement')
        con.execute('DELETE FROM bin_eviction')
        con.commit()
    finally:
        con.close()

    arm = _ref(root, sim_db, wh_db, kf_db, run['run_id'], tag='nolog')
    report = build_one(arm)
    return arm, report


def test_without_the_log_the_cache_falls_back_to_keyframes(crippled):
    arm, report = crippled
    assert report['status'] == 'built'
    assert report['span_source'] == SPAN_SOURCE_KEYFRAME
    assert _cache_meta(arm)['span_source'] == SPAN_SOURCE_KEYFRAME
    assert 'bin_log' not in arm.reader().capabilities()


def test_without_the_log_a_non_keyframe_batch_is_wrong_AND_says_so(run, crippled):
    """The control for the whole exercise.

    If the frames still matched with the placement log deleted, the log would not be the missing
    term and every assertion above would be passing for the wrong reason. This is the production
    bug in miniature: the archive is exactly this DB.
    """
    arm, _report = crippled
    reader = arm.reader()
    batch = _non_keyframe_batches(run['frames'])[-1]
    truth = dict(run['frames'][batch][1])
    got = _bins(reader.state_at(batch))

    assert got != truth, 'deleting the restock log changed nothing — the premise is wrong'
    assert len(set(truth) - set(got)) > 10, (
        f'only {len(set(truth) - set(got))} bins lost without the log; '
        f'restocks are not load-bearing in this scenario')
    state = reader.state_at(batch)
    assert state['exact'] is False, 'an inexact frame must never claim to be exact'
    assert state['restocks_pending'] > 0
    assert 'not represented' in state['note']


def test_without_the_log_a_keyframe_batch_is_still_exact(crippled, run):
    """The fallback must stay as good as it always was — old runs keep working."""
    arm, _report = crippled
    state = arm.reader().state_at(KEYFRAME_INTERVAL)
    assert state['exact'] is True
    assert _bins(state) == run['frames'][KEYFRAME_INTERVAL][1]
