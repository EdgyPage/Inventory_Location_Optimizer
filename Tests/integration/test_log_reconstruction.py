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
    BatchStats, BinEvictionRecord, BinPlacementRecord, PickRecord, create_run,
    init_keyframe_db, init_run_db, keyframe_db_path, save_bin_keyframe)
from Optimization.persistence.checkpoint_buffer import write_rows
from Optimization.persistence.Warehouse_Data import init_warehouse_db, save_aisle_layout
from Visualization.cache_schema import SPAN_SOURCE_KEYFRAME, SPAN_SOURCE_LOG
from Visualization.db_reader import RunRef
from Visualization.readers.state_sources import (
    SOURCES, ArchiveSource, KeyframeSource, LogFoldSource, SpanIndexSource)
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
    write_rows(sim_db, run_id, batch_stats=[
        BatchStats(run_id=run_id, batch_id=b, duration=10.0 + b,
                   num_tasks=1, total_items=1, avg_concurrent_pickers=1.0,
                   picking_pct=0.5, traveling_pct=0.5, reorder_placements=placed.get(b, 0))
        for b, _after, _final, _max_t in frames])

    write_rows(sim_db, run_id, picks=[
        PickRecord(run_id=run_id, batch_id=k.batch, picker_id=0, sim_time=k.t,
                   aisle_id=k.loc[0], bayX=k.loc[1], bayY=k.loc[2],
                   sku=k.sku, quantity=k.qty)
        for k in log.picks])
    write_rows(sim_db, run_id, bin_placements=[
        # `bin_state` rides through: a top-up (ADR-0003) ADDS to a bin rather than filling an
        # empty one, and the span fold cannot tell the two apart without it — dropping the
        # field here made the rollup under-count by exactly the topped-up units.
        BinPlacementRecord(run_id=run_id, batch_id=p.batch, seq=seq, aisle_id=p.loc[0],
                           bayX=p.loc[1], bayY=p.loc[2], sku=p.sku, qty=p.qty, cause=p.cause,
                           bin_state=p.bin_state)
        for seq, p in _seq_numbered(log.places)])
    write_rows(sim_db, run_id, bin_evictions=[
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


# ── the four records, each driven DIRECTLY ────────────────────────────────────────
#
# Until ticket 15 only the COMPOSITE was tested, and only indirectly: `state_at` picked a record
# by an if-chain and the tests asserted the frame, so which record answered was never named. Each
# one is now a class with one method, and these drive them one at a time against the same real
# run -- so "the log fold is exact" is an assertion about the log fold rather than about whatever
# `state_at` happened to reach.

def test_the_span_index_answers_only_when_the_sidecar_holds_the_batch(run):
    """The record that made `available()` impossible to write: the index can EXIST and still not
    answer for a batch, which is why a source's one question is `state() is None`."""
    reader = run['arm'].reader()
    assert reader.cache_status() != 'absent', 'the fixture has no sidecar; this proves nothing'
    src = SpanIndexSource(reader)
    for batch, after_reorders, _f, _m in run['frames']:
        state = src.state(batch, None, None)
        assert state is not None, f'the sidecar holds batch {batch} and the record declined it'
        assert state['exact'] is True
        assert _bins(state) == after_reorders, f'span index, batch {batch}'

    # Past the end of the cached run there is no row, and the record says so rather than
    # asserting an exact empty warehouse.
    assert src.state(N_BATCHES + 50, None, None) is None


def test_the_log_fold_is_exact_at_every_batch_with_no_sidecar_at_all(run):
    """The second record, alone. `_ref(tag='absent')` gives a reader whose sidecar does not
    exist, so `SpanIndexSource` would decline and this is what carries the frame."""
    bare = _ref(run['root'], run['sim_db'], run['wh_db'], run['kf_db'], run['run_id'],
                tag='absent')
    reader = bare.reader()
    assert SpanIndexSource(reader).state(0, None, None) is None, 'the sidecar is not absent'
    src = LogFoldSource(reader)
    for batch, after_reorders, _f, _m in run['frames']:
        state = src.state(batch, None, None)
        assert state is not None, f'the log fold declined batch {batch}'
        assert state['exact'] is True and _bins(state) == after_reorders, f'log fold, batch {batch}'


def test_the_keyframe_record_is_exact_AT_a_keyframe_and_says_so_between(run):
    """The third record's whole contract: exact at a keyframe batch, depletion-exact but
    restock-blind between -- reported in `exact` and `note` rather than hidden."""
    reader = run['arm'].reader()
    kfs = reader.keyframe_batches()
    assert kfs == [0, 5], kfs
    src = KeyframeSource(reader)

    at_kf = src.state(5, None, None)
    assert at_kf['exact'] is True and at_kf['keyframe'] == 5 and at_kf['note'] == ''

    between = src.state(7, None, None)
    assert between['exact'] is False, 'a non-keyframe batch reported itself as exact'
    assert between['keyframe'] == 5
    assert 'not represented' in between['note'], between['note']


def test_the_archive_record_answers_unconditionally(run):
    """THE CONTRACT `state_at`'s LOOP RESTS ON. The terminal source must never return None --
    a run with no record at all gets a frame whose `note` says so, because that is renderable
    and a None falling out of the loop is not."""
    reader = run['arm'].reader()
    state = ArchiveSource(reader).state(3, None, None)
    assert state is not None
    # This fixture carries no `bin_inventory` (it is archive-only and no longer written), so the
    # record takes its own absent branch -- which is the branch that must not raise.
    assert state['exact'] is False
    assert 'no spatial record' in state['note'] or 'no keyframes' in state['note'], state['note']


def test_state_at_returns_the_FIRST_record_that_answers(run):
    """The ORDER is the policy, and this is the only test that can see it.

    Driven directly, the sources are asked in `SOURCES` order; `state_at` must return what the
    first non-None one returns. An if-chain made this a property of a method body.
    """
    reader = run['arm'].reader()
    for batch, _a, _f, _m in run['frames']:
        first = next(s for s in (src(reader).state(batch, None, None) for src in SOURCES)
                     if s is not None)
        assert _bins(reader.state_at(batch)) == _bins(first), f'batch {batch}'


def test_the_source_order_is_pinned(run):
    """Reordering `SOURCES` changes which record a run with more than one gets -- a behaviour
    change, not tidying. Pinned as a literal so it is a deliberate edit with a reason."""
    assert [s.__name__ for s in SOURCES] == [
        'SpanIndexSource', 'LogFoldSource', 'KeyframeSource', 'ArchiveSource']


def test_the_records_are_not_all_the_same_record(run):
    """NON-VACUITY for everything above: if two sources returned identical frames for every
    batch, every assertion here would hold with the seam removed."""
    reader = run['arm'].reader()
    spans = SpanIndexSource(reader).state(7, None, None)
    keyfr = KeyframeSource(reader).state(7, None, None)
    assert spans is not None and keyfr is not None
    assert spans['exact'] != keyfr['exact'], (
        'the span index and the keyframe record agree on exactness at a non-keyframe batch; '
        'the fixture no longer distinguishes them')


# ── the public reader methods that had no behavioural test ────────────────────────
#
# `test_viewer_protocol_conformance.py` is the only file importing `SqliteSimReader` directly
# and it never opens a database -- reflection only. These six reached coverage indirectly or not
# at all; they ride the fixture that is already here.

def test_run_meta_returns_the_row_as_the_payload(run):
    meta = run['arm'].reader().run_meta()
    assert isinstance(meta, dict) and meta, 'run_meta returned nothing'
    assert int(meta.get('run_id', run['run_id'])) == run['run_id']


def test_batch_index_covers_the_run(run):
    idx = run['arm'].reader().batch_index()
    assert idx, 'batch_index is empty'
    assert len(idx) == N_BATCHES, f'{len(idx)} entries for {N_BATCHES} batches'


def test_aisle_geometry_describes_the_warehouse_the_frames_sit_in(run):
    geo = run['arm'].reader().aisle_geometry()
    assert geo, 'aisle_geometry is empty'
    aisles = {int(a) for b in run['frames'][0][1] for a in (b[0],)}
    known = {int(g['aisle_id']) if isinstance(g, dict) else int(g[0]) for g in geo}
    assert aisles <= known, f'frames reference aisles the geometry does not describe: {aisles - known}'


def test_aisle_state_is_the_scoped_frame(run):
    """`aisle_state` is `state_at` scoped to one aisle -- the scoped and full frames must agree
    on that aisle, which is the property a scope bug breaks silently."""
    reader = run['arm'].reader()
    batch, truth, _f, _m = run['frames'][-1]
    aisle = next(iter({k[0] for k in truth}))
    scoped = reader.aisle_state(batch, aisle)
    full = {k: v for k, v in _bins(reader.state_at(batch)).items() if k[0] == aisle}
    assert _bins(scoped) == full, _diff(_bins(scoped), full)


def test_sku_scores_and_bin_scores_do_not_raise_on_a_run_without_them(run):
    """Both are shape-following reads over tables this fixture never writes. The contract is
    that an absent optional table is an empty answer, not `no such table` three layers up."""
    reader = run['arm'].reader()
    # The shapes differ (`sku_scores` is keyed, `bin_scores` is a sequence) and that is not what
    # is under test: what matters is that an absent optional table is an EMPTY answer rather
    # than `no such table` surfacing at a caller three layers up.
    for got in (reader.sku_scores(0), reader.bin_scores(0)):
        assert isinstance(got, (dict, list, tuple)), type(got)
        # `bin_scores` answers with a structured envelope (`layout` / `map_pref` / `has_map`)
        # and `sku_scores` with a bare mapping, so "empty" is about the PAYLOADS rather than the
        # container: no score data, and no exception reaching the caller.
        payloads = list(got.values()) if isinstance(got, dict) else [got]
        assert not any(p for p in payloads if isinstance(p, (dict, list, tuple))), (
            f'this fixture writes no scores; got {got!r}')
