"""test_free_index_by_bucket.py — the tier spill, the free index per bucket, and the rework
clause that judges three events at zero (department-calibration 32 / 33).

`batch_stats.free_bins` is the WHOLE geometry, so a channel leaf reads the other section's
untouched bins as free (57-59% where the section's real headroom was 15-18%).  Ticket 33
records the depth PER BUCKET (`free_index`, keyed by BinKey), counts the TIER SPILL
(`put_spills`: the candidate chain moved up a size tier because the unit's own bucket was
dry -- the first deviation from the put pricer's per-class assumption), stamps the tier
pair on `bin_placement`, and makes the rework clause judge the spill, the top-up and the
repack at exactly zero with no knob.

What a failure here means:

  * **A spill is not counted, or a fit is.**  `_execute_placement` increments `_put_spills`
    exactly when the landing bin's size differs from the unit's own.  The sabotage fixture
    fills every `small` bin so a 6-item (small) unit MUST spill into a `medium` bin; the
    control fixture offers the same unit with its own tier free.
  * **A dry bucket vanishes.**  `free_bin_depth_by_bucket` writes a bucket at ZERO, and the
    rows partition `free_bin_depth` exactly.
  * **The clause cannot fail, or fails on unknowns.**  A spill or a top-up in the window
    FAILS the clause naming the bucket that read dry; a `None` spill count (a vintage before
    the column) passes as unrecorded, never as zero.  The per-bucket reading names the dry
    bucket while the leaf total stays high -- the reading the total could not give.
  * **The audit is vacuous.**  `throughput/audit.py` reaches the clause through `_bdf` and
    `_fidf`; a column missing from either dict would make the clause read defaults on
    exactly the run it exists to flag (the trap ticket 24 fell into).  The last block forces
    a spill THROUGH those frames.
  * **A bundle argument is accepted and never written.**  `save_checkpoint_bundle`'s
    characteristic failure; the persistence block reads the FILE back.

Run:  python -m pytest Tests/unit/test_free_index_by_bucket.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from Optimization.metrics.bin_recorder import BinRecorder
from Optimization.Performance_Evaluations.common.frames import _bdf, _fidf
from Optimization.Performance_Evaluations.throughput import audit
from Optimization.simconfig import equilibrium as eq
from Tests.unit.test_empty_first_topup import (
    _medium_bins, _offer, _order, _small_bins, _stocked, _warehouse,
)
from Tests.unit.test_equilibrium_check import (
    S, _bs, _check, _expectations, _fresh_db, _staffing, _window,
)
from Warehouse.inventory.Inventory_Management import Inventory_Manager
from Warehouse.layout.Storage_Primitive import Pallet

SMALL = ('conveyable', 'food', 'small', 'pallet')
MEDIUM = ('conveyable', 'food', 'medium', 'pallet')


def _label(key) -> str:
    return eq.bucket_label(*key)


# ══════════════════════════════════════════════════════════════════════════════
# The domain: the spill is counted at the commit point; the depth is read per bucket
# ══════════════════════════════════════════════════════════════════════════════

def _small_tier_dry():
    """A two-tier warehouse whose every SMALL bin is full of SKU 1, medium bins free.

    A 6-item unit of the fixture item is a `small` pallet (asserted, so the premise cannot
    rot silently), and `_candidates_raw` spills UP only -- so offering one is the exact
    configuration a tier spill is: the unit's own bucket dry, a larger tier free.
    """
    wh = _warehouse(small_aisle=True)
    mgr = Inventory_Manager(wh)
    order = _order()
    assert Pallet(order, 6).storage_size == 'small', 'the fixture premise: 6 items is small'
    for bin_ in _small_bins(wh):
        mgr._execute_placement(Pallet(order, 6), bin_, source='intake')
    assert mgr._index.get(SMALL) == [], 'the small tier must be dry for a spill to fire'
    assert mgr._put_spills == 0, 'stocking a unit into its OWN tier counted as a spill'
    return wh, mgr, order


def test_a_unit_whose_own_tier_is_dry_spills_up_and_is_counted():
    wh, mgr, order = _small_tier_dry()
    free_medium = len(mgr._index[MEDIUM])
    _offer(mgr, order, 6)
    assert mgr._put_spills == 1, f'{mgr._put_spills} spill(s) counted for one spilled unit'
    assert mgr._put_topups == 0 and mgr._recv_repacks == 0, (
        'the spill was recorded as a top-up or a rescue; it is neither -- an empty bin fit')
    assert len(mgr._index[MEDIUM]) == free_medium - 1, 'the unit did not land in a medium bin'
    landed = [b for b in _medium_bins(wh) if b.storage is not None]
    assert len(landed) == 1 and landed[0].storage.quantity == 6


def test_a_placement_into_the_units_own_tier_is_not_a_spill():
    """The control: the same unit with its own tier free.  Without this the test above could
    pass on a counter that fires on every placement."""
    wh, mgr, order, _med = _stocked([12, 12])          # medium units into medium bins
    assert mgr._put_spills == 0
    _offer(mgr, order, 6)                               # a small unit, small bins free
    assert mgr._put_spills == 0, 'a placement into the unit\'s own tier counted as a spill'
    assert any(b.storage is not None for b in _small_bins(wh)), 'the unit was not placed'


def test_free_bin_depth_by_bucket_partitions_the_index_and_keeps_a_dry_bucket_at_zero():
    wh, mgr, order = _small_tier_dry()
    rows = mgr.free_bin_depth_by_bucket()
    assert rows == sorted(rows), 'the rows are not in key order'
    assert dict(rows)[SMALL] == 0, 'the dry bucket vanished instead of reading 0'
    assert dict(rows)[MEDIUM] == len(_medium_bins(wh))
    assert sum(n for _k, n in rows) == mgr.free_bin_depth(), (
        'the per-bucket rows do not partition the whole-geometry total')
    _offer(mgr, order, 6)
    assert dict(mgr.free_bin_depth_by_bucket())[MEDIUM] == len(_medium_bins(wh)) - 1


def test_snapshot_resets_the_spill_flow_and_the_bucket_depth_does_not():
    wh, mgr, order = _small_tier_dry()
    _offer(mgr, order, 6)
    before = mgr.free_bin_depth_by_bucket()
    assert mgr.snapshot_putaway_rework() == (0, 1, 0, 0)
    assert mgr.snapshot_putaway_rework() == (0, 0, 0, 0), 'the spill survived a snapshot'
    assert mgr.free_bin_depth_by_bucket() == before, 'a level reset -- a level is not a flow'


# ══════════════════════════════════════════════════════════════════════════════
# The recorder: the tier pair rides every PLACE row
# ══════════════════════════════════════════════════════════════════════════════

def test_the_recorder_stamps_the_tier_pair_and_a_spill_row_is_the_one_where_they_differ():
    wh, mgr, order = _small_tier_dry()
    rec = BinRecorder(run_id=1)
    rec.attach(mgr)
    rec.begin_batch(3)
    _offer(mgr, order, 6)                               # spills into medium
    _offer(mgr, order, 12)                              # a medium unit, medium bins free
    placements, _ = rec.drain()
    pairs = sorted((r.unit_size, r.bin_size, r.bin_state) for r in placements)
    assert pairs == [('medium', 'medium', 'empty'), ('small', 'medium', 'empty')], pairs
    spilled = [r for r in placements if r.unit_size != r.bin_size]
    assert len(spilled) == 1 and spilled[0].qty == 6


def test_a_top_up_row_carries_the_bin_tier_and_no_unit_tier():
    wh, mgr, order, med = _stocked([1, 5, 5, 12])
    rec = BinRecorder(run_id=1)
    rec.attach(mgr)
    rec.begin_batch(4)
    _offer(mgr, order, 7)                               # the own-bin rung, one top-up
    placements, _ = rec.drain()
    assert len(placements) == 1, f'one top-up, {len(placements)} rows: {placements}'
    row = placements[0]
    assert row.bin_state == 'occupied'
    assert row.unit_size is None and row.bin_size == 'medium', (row.unit_size, row.bin_size)


# ══════════════════════════════════════════════════════════════════════════════
# Persistence: the bundle writes the FILE, the loaders negotiate the vintage
# ══════════════════════════════════════════════════════════════════════════════

def _rows(batch, small, medium):
    return [(batch, *SMALL, small), (batch, *MEDIUM, medium)]


def test_free_index_rows_ride_the_bundle_and_read_back_from_the_file(tmp_path):
    from Optimization.persistence.Picking_Data import (
        load_batch_stats, load_free_index, save_checkpoint_bundle)
    db, run_id = _fresh_db(tmp_path)
    assert load_free_index(db, run_id) == [], 'a fresh file has the table and no rows'
    bs = _bs(run_id, 0, day=0, makespan=S, items=100, demanded=100)
    bs.put_spills = 3
    save_checkpoint_bundle(db, run_id, batch_stats=[bs], task_stats=[], picker_events=[],
                           picks=[], bin_placements=[], bin_evictions=[], aisle_metrics=[],
                           reorder_queue=[], free_index=_rows(0, 0, 40) + _rows(1, 2, 39))
    rows = load_free_index(db, run_id)
    assert [(r['batch_id'], r['size'], r['free']) for r in rows] == [
        (0, 'medium', 40), (0, 'small', 0), (1, 'medium', 39), (1, 'small', 2)]
    assert set(rows[0]) == {'batch_id', 'handling', 'category', 'size', 'unit', 'free'}
    (back,) = load_batch_stats(db, run_id)
    assert back.put_spills == 3, 'put_spills did not round-trip through batch_stats'


def test_the_tier_pair_round_trips_through_bin_placement(tmp_path):
    from Optimization.persistence.Picking_Data import (
        BinPlacementRecord, load_bin_placements, save_bin_placements)
    db, run_id = _fresh_db(tmp_path)
    save_bin_placements(db, run_id, [
        BinPlacementRecord(run_id, 0, 0, 1, 0, 0, 1, 6, 'reorder', unit_size='small',
                           bin_size='medium'),
        BinPlacementRecord(run_id, 0, 1, 1, 0, 1, 1, 7, 'reorder', bin_state='occupied',
                           bin_size='medium')])
    rows = load_bin_placements(db, run_id)
    assert [(r['unit_size'], r['bin_size']) for r in rows] == [('small', 'medium'),
                                                               (None, 'medium')]


@pytest.mark.parametrize('vintage', ['free_index', 'rework'])
def test_the_vintages_before_the_table_read_unknown_never_zero(tmp_path, vintage):
    """Fake an outgoing vintage in place (the `test_era_wiring` recipe: drop every column
    added since, restamp, checkpoint the WAL) and read it through the loaders.

    Two vintages, because the optional fill only answers THROUGH AN OVERRIDE -- the canonical
    SQL names every column and is unsupported on a file lacking one, which drops the loader
    to its legacy body and the dataclass default.  Until this build the 798778f4fae1 vintage
    had no override, so `free_bins` read 0 there while every docstring promised None.
    """
    from Optimization.persistence.Picking_Data import (
        PRE_FREE_INDEX_SIM_SCHEMA_ID, PRE_REWORK_SIM_SCHEMA_ID, BinPlacementRecord,
        load_batch_stats, load_bin_placements, load_free_index, save_bin_placements,
        save_checkpoint_bundle)
    from Schema import dataset
    db, run_id = _fresh_db(tmp_path)
    bs = _bs(run_id, 0, day=0, makespan=S, items=100, demanded=100)
    bs.put_spills, bs.free_bins, bs.put_topups = 3, 40, 2
    save_checkpoint_bundle(db, run_id, batch_stats=[bs], task_stats=[], picker_events=[],
                           picks=[], bin_placements=[], bin_evictions=[], aisle_metrics=[],
                           reorder_queue=[], free_index=_rows(0, 0, 40))
    save_bin_placements(db, run_id, [BinPlacementRecord(run_id, 0, 0, 1, 0, 0, 1, 6,
                                                        'reorder', unit_size='small',
                                                        bin_size='medium')])
    con = sqlite3.connect(db)
    con.execute('DROP TABLE free_index')
    # And the mirror case, which the paragraph above did not anticipate: a column REMOVED
    # since that vintage has to be put BACK, or the fake is missing something the real file
    # had.  `aisle_metrics.lift_sum` was dropped on 2026-09-16 as write-only.  It has to be
    # RECREATED rather than ALTERed in, because the observed shape records column ORDER and
    # `ADD COLUMN` can only append -- which reads as a different shape, not a restored one.
    con.execute('DROP TABLE aisle_metrics')
    con.execute('''CREATE TABLE aisle_metrics (
        run_id        INTEGER NOT NULL REFERENCES simulation_runs(run_id),
        batch_id      INTEGER NOT NULL,
        aisle_id      INTEGER NOT NULL,
        n_skus        INTEGER NOT NULL DEFAULT 0,
        n_bins        INTEGER NOT NULL DEFAULT 0,
        demand_sum    REAL    NOT NULL DEFAULT 0.0,
        lift_sum      REAL    NOT NULL DEFAULT 0.0,
        pick_load_sum REAL    NOT NULL DEFAULT 0.0,
        PRIMARY KEY (run_id, batch_id, aisle_id)
    )''')
    # DROP TABLE took the indexes with it, and the observed shape records those too.
    con.execute('CREATE INDEX ix_am_run_batch ON aisle_metrics (run_id, batch_id)')
    con.execute('CREATE INDEX ix_am_run_aisle ON aisle_metrics (run_id, aisle_id)')
    # `site_receiving` postdates BOTH vintages faked here (site-dock 25), so a fake that
    # left it behind is not that vintage: `dataset.bind(verify=True)` re-derives the shape
    # and raises rather than trusting the stamp, which is what the recipe's checkpoint-and-
    # assert-the-bound-id step exists to make loud (memory
    # `immutable-readers-see-only-the-checkpointed-file`).
    con.execute('DROP TABLE site_receiving')
    con.execute('ALTER TABLE batch_stats DROP COLUMN put_spills')
    con.execute('ALTER TABLE bin_placement DROP COLUMN unit_size')
    con.execute('ALTER TABLE bin_placement DROP COLUMN bin_size')
    stamp = PRE_FREE_INDEX_SIM_SCHEMA_ID
    if vintage == 'rework':
        con.execute('ALTER TABLE bin_placement DROP COLUMN bin_state')
        for c in ('put_topups', 'recv_repacks', 'recv_repacked_packs', 'free_bins'):
            con.execute(f'ALTER TABLE batch_stats DROP COLUMN {c}')
        stamp = PRE_REWORK_SIM_SCHEMA_ID
    con.execute('UPDATE simulation_runs SET sim_schema_id = ?', (stamp,))
    con.commit()
    con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    con.close()
    with dataset.bind(db, 'sim_db', immutable=True) as ds:
        assert (ds.schema_id, ds.source) == (stamp, 'stamped')
    assert load_free_index(db, run_id) == []
    (back,) = load_batch_stats(db, run_id)
    assert back.put_spills is None, f'put_spills read {back.put_spills!r}; never counted there'
    if vintage == 'free_index':
        assert (back.put_topups, back.free_bins) == (2, 40), 'the columns it HAS read as written'
    else:
        assert back.put_topups == 0, 'put_topups IS zero by construction on that vintage'
        assert back.free_bins is None, f'free_bins read {back.free_bins!r}; unknown, never 0'
    (row,) = load_bin_placements(db, run_id)
    assert (row['unit_size'], row['bin_size']) == (None, None)


# ══════════════════════════════════════════════════════════════════════════════
# The clause: three events judged at zero, the depth reported per bucket
# ══════════════════════════════════════════════════════════════════════════════

A, B, C = _label(SMALL), _label(MEDIUM), _label(('conveyable', 'food', 'ff_medium',
                                                 'fulfillment'))


def _free_rows(days=range(0, 6), *, a=(100,) * 6, b=(50,) * 6, c=(1000,) * 6):
    """One `free_index` row per bucket per batch (batch_id == day in `_window`)."""
    rows = []
    for k, d in enumerate(days):
        for key, series in ((SMALL, a), (MEDIUM, b),
                            (('conveyable', 'food', 'ff_medium', 'fulfillment'), c)):
            rows.append({'batch_id': d, 'handling': key[0], 'category': key[1],
                         'size': key[2], 'unit': key[3], 'free': series[k]})
    return rows


def _exp(**over):
    base = _expectations(expected_repacked_packs=0.0, setup_free={A: 100, B: 50})
    base.update(over)
    return base


def _with(batch, **cols):
    for b in batch:
        b.update(cols)
    return batch


def test_the_reference_window_passes_and_reports_the_depth_per_bucket_of_the_record():
    shift, batch, work, carry = _window()
    _with(batch, put_topups=0, put_spills=0, free_bins=1150)
    v = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                      day_lo=0, day_hi=5, expectations=_exp(),
                      free_rows=_free_rows(a=(100, 95, 90, 85, 80, 75)))
    assert v.passed, v.reasons
    r = v.clauses['rework'].reading
    assert r['topups'] == 0 and r['spills'] == 0 and r['buckets_recorded']
    # only the RECORD's buckets: the fulfillment row is the other section, the artefact
    assert list(r['buckets']) == sorted([A, B]) == [B, A], 'label order: medium < small'
    assert r['buckets'][A] == {'setup_free': 100, 'n': 6, 'first': 100, 'last': 75, 'min': 75,
                               'mean': pytest.approx(87.5), 'drawdown': 25, 'dry_batches': []}
    assert r['buckets'][B]['drawdown'] == 0 and r['dry_buckets'] == []
    assert 'driest' in eq.summarize(v) and 'section drawdown +25' in eq.summarize(v)


def test_a_tier_spill_fails_the_clause_naming_the_dry_bucket():
    shift, batch, work, carry = _window()
    _with(batch, put_topups=0, put_spills=0, free_bins=1150)
    batch[3]['put_spills'] = 2
    v = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                      day_lo=0, day_hi=5, expectations=_exp(),
                      free_rows=_free_rows(b=(50, 40, 20, 0, 0, 3)))
    c = v.clauses['rework']
    assert not c.passed and not v.passed
    assert '2 tier spill(s)' in c.reason and B in c.reason, c.reason
    assert c.reading['spills'] == 2 and c.reading['dry_buckets'] == [B]
    assert c.reading['buckets'][B]['dry_batches'] == [3, 4]
    assert c.reading['days'][3]['spills'] == 2


def test_a_top_up_fails_the_clause_with_no_knob_and_without_a_repack_expectation():
    """Decision 4: the zero is the ADR's own claim.  A record with no `f_repack` still judges
    the top-up -- only the REPACK is reported-not-judged there."""
    shift, batch, work, carry = _window()
    _with(batch, put_topups=0, put_spills=0, free_bins=1150)
    batch[1]['put_topups'] = 1
    v = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                      day_lo=0, day_hi=5, expectations=_exp(expected_repacked_packs=None),
                      free_rows=_free_rows(a=(100, 0, 0, 0, 0, 0), b=(50, 0, 0, 0, 0, 0)))
    c = v.clauses['rework']
    assert not c.passed
    assert '1 top-up(s)' in c.reason and 'no f_repack' in c.reason, c.reason
    assert 'bucket(s) read dry: ' + B + ', ' + A in c.reason, c.reason
    # and with nothing fired, the same record passes: the top-up is what failed it
    batch[1]['put_topups'] = 0
    v2 = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                       day_lo=0, day_hi=5, expectations=_exp(expected_repacked_packs=None),
                       free_rows=_free_rows())
    assert v2.clauses['rework'].passed and 'not judged' in v2.clauses['rework'].reason


def test_an_unrecorded_spill_count_is_unknown_and_cannot_fail_a_zero():
    shift, batch, work, carry = _window()
    _with(batch, put_topups=0, put_spills=None, free_bins=None)
    v = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                      day_lo=0, day_hi=5, expectations=_exp(), free_rows=[])
    c = v.clauses['rework']
    assert c.passed, c.reason
    assert c.reading['spills'] is None and c.reading['days'][0]['spills'] is None
    assert not c.reading['buckets_recorded']
    assert all(b['min'] is None and b['setup_free'] is not None
               for b in c.reading['buckets'].values()), 'the record\'s buckets still list'
    assert 'unrecorded spill(s)' in eq.summarize(v) and 'depth unrecorded' in eq.summarize(v)
    # a NaN off a pandas frame is the same unknown
    _with(batch, put_spills=float('nan'), free_bins=float('nan'))
    v = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                      day_lo=0, day_hi=5, expectations=_exp(), free_rows=[])
    assert v.clauses['rework'].passed and v.clauses['rework'].reading['spills'] is None


def test_a_dry_bucket_shows_in_the_per_bucket_reading_while_the_leaf_total_stays_high():
    """THE READING THE TOTAL COULD NOT GIVE: the whole-geometry `free_bins` sits at 1,050 on
    every batch (the other section's thousand), and bucket B is dry from day 2."""
    shift, batch, work, carry = _window()
    _with(batch, put_topups=0, put_spills=0, free_bins=1050)
    v = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                      day_lo=0, day_hi=5, expectations=_exp(),
                      free_rows=_free_rows(b=(50, 20, 0, 0, 0, 0)))
    r = v.clauses['rework'].reading
    assert all(d['free_bins_min'] == 1050 for d in r['days'].values())
    assert r['dry_buckets'] == [B] and r['buckets'][B]['min'] == 0
    assert r['buckets'][B]['drawdown'] == 50
    assert v.clauses['rework'].passed, 'the depth is REPORTED, never judged (decision 6)'
    assert 'driest ' + B + ' at 0 of 50 at setup' in eq.summarize(v)


def test_without_a_record_every_recorded_bucket_is_reported_and_a_window_is_respected():
    shift, batch, work, carry = _window()
    _with(batch, put_topups=0, put_spills=0, free_bins=1150)
    v = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                      day_lo=2, day_hi=4, expectations=_exp(setup_free=None),
                      free_rows=_free_rows(a=(100, 90, 80, 70, 60, 50)))
    r = v.clauses['rework'].reading
    assert list(r['buckets']) == sorted([A, B, C])
    assert r['buckets'][A] == {'setup_free': None, 'n': 3, 'first': 80, 'last': 60, 'min': 60,
                               'mean': pytest.approx(70.0), 'drawdown': 20, 'dry_batches': []}


def test_expectations_for_carries_the_setup_free_per_bucket_off_the_fielded_block():
    rec = _staffing()
    assert eq.expectations_for(rec, pair='pairA', channel='store')['setup_free'] is None
    rec['calibration']['pairA']['coverage'] = {'final': {'store': {
        'fill': {'fill_rate': 0.93},
        'fielded': {'buckets': [
            {'handling': 'conveyable', 'category': 'food', 'size': 'small', 'unit': 'pallet',
             'requirement': 60, 'capacity': 72, 'free': 12},
            {'handling': 'conveyable', 'category': 'food', 'size': 'medium',
             'unit': 'pallet', 'requirement': 20, 'capacity': 24, 'free': 4}]}}}}
    e = eq.expectations_for(rec, pair='pairA', channel='store')
    assert e['setup_free'] == {A: 12, B: 4}
    # the per-arm re-centring carries it through unchanged
    assert eq.arm_expectations({**e, 'departments': {'pick': {'crew': 2, 'expected': 0.5,
                                                              's_pick': 100.0}}},
                               {'s_pick': 90.0})['setup_free'] == {A: 12, B: 4}


def test_check_reads_the_free_index_off_a_sim_db_and_a_spill_fails_it(tmp_path):
    from Optimization.persistence.Picking_Data import save_checkpoint_bundle, save_shift_days
    db, run_id = _fresh_db(tmp_path)
    batches = [_bs(run_id, d, day=d, makespan=0.50 * 2 * S, items=1000, demanded=1000)
               for d in range(2)]
    batches[1].put_spills = 1
    save_checkpoint_bundle(db, run_id, batch_stats=batches, task_stats=[], picker_events=[],
                           picks=[], bin_placements=[], bin_evictions=[], aisle_metrics=[],
                           reorder_queue=[], free_index=_rows(0, 3, 40) + _rows(1, 0, 40))
    save_shift_days(db, run_id, [(0, S, S - 500, True, 0, 0, 0, 0, 0, 0, S - 500),
                                 (1, 2 * S, 2 * S - 500, True, 0, 0, 0, 0, 0, 0, 2 * S - 500)])
    v = eq.check(db, run_id, 0, 1, expectations=_exp(setup_free={A: 3, B: 40}))
    c = v.clauses['rework']
    assert not c.passed and A in c.reason and c.reading['buckets'][A]['dry_batches'] == [1]
    assert c.reading['buckets'][B] == {'setup_free': 40, 'n': 2, 'first': 40, 'last': 40,
                                       'min': 40, 'mean': pytest.approx(40.0), 'drawdown': 0,
                                       'dry_batches': []}


# ══════════════════════════════════════════════════════════════════════════════
# Through the audit's own frames -- the path that was vacuous once
# ══════════════════════════════════════════════════════════════════════════════

def test_a_forced_spill_fails_the_clause_through_the_audits_own_frames():
    shift, batch, work, carry = _window()
    stats = []
    for b in batch:
        s = _bs(1, b['batch_id'], day=b['work_day'], makespan=b['task_makespan'],
                items=b['total_items'], demanded=b['items_demanded'])
        s.free_bins = 1150
        stats.append(s)
    stats[2].put_spills = 1
    bdf, fdf = _bdf(stats), _fidf(_free_rows(a=(100, 100, 0, 0, 0, 0)))
    assert 'put_spills' in bdf.columns and list(fdf.columns) == [
        'batch_id', 'handling', 'category', 'size', 'unit', 'free']
    v = eq.check_rows(shift_rows=shift, batch_rows=bdf.to_dict('records'), work_rows=work,
                      carry_rows=carry, day_lo=0, day_hi=5, expectations=_exp(),
                      free_rows=fdf.to_dict('records'))
    c = v.clauses['rework']
    assert not c.passed and '1 tier spill(s)' in c.reason and A in c.reason, c.reason
    # the frames' unknowns are NaN, and the clause reads them as unknown
    for s in stats:
        s.put_spills = None
    v = eq.check_rows(shift_rows=shift, batch_rows=_bdf(stats).to_dict('records'),
                      work_rows=work, carry_rows=carry, day_lo=0, day_hi=5,
                      expectations=_exp(), free_rows=_fidf([]).to_dict('records'))
    assert v.clauses['rework'].passed and v.clauses['rework'].reading['spills'] is None


def test_the_inspection_table_carries_the_three_events_and_the_depth():
    head = ['arm', '6', '6', '0', '0']
    shift, batch, work, carry = _window()
    _with(batch, put_topups=0, put_spills=2, free_bins=1150)
    v = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                      day_lo=0, day_hi=5, expectations=_exp(),
                      free_rows=_free_rows(a=(100, 90, 80, 70, 60, 0)))
    rows = audit._rework_rows(head, v)
    assert [r[5] for r in rows] == ['tier spills', 'own-bin top-ups', 'repacked packs',
                                    'free index', 'driest bucket']
    assert rows[0][7:] == ['0', '12', '0', 'ABOVE zero · dry: ' + A]
    assert rows[1][7:] == ['0', '0', '0', 'none']
    assert rows[2][7:] == ['0', '0', '0', 'none']
    assert rows[3][7:] == ['150 at setup', '50', '-', 'drawdown +100 over 2 bucket(s)']
    # the driest bucket by its window minimum, its label shortened for the cell only
    assert rows[4][7:] == ['100 at setup', '0', '-', 'conveyable/food/small']
    assert all(r[:5] == [''] * 5 for r in rows)
    assert [r[10] for r in audit._rework_rows(head, None)] == ['n/a'] * 5


def test_the_diagnostic_prints_a_per_bucket_table():
    from Diagnostics.equilibrium_report import bucket_table
    shift, batch, work, carry = _window()
    _with(batch, put_topups=0, put_spills=0, free_bins=1150)
    v = eq.check_rows(shift_rows=shift, batch_rows=batch, work_rows=work, carry_rows=carry,
                      day_lo=0, day_hi=5, expectations=_exp(),
                      free_rows=_free_rows(b=(50, 40, 30, 20, 10, 0)))
    lines = bucket_table(v)
    assert lines[0].startswith('bucket') and len(lines) == 3
    assert lines[1].startswith(B) and '+50' in lines[1] and lines[1].rstrip().endswith('1')
    assert bucket_table(_check(*_window())) == ['free index per bucket: unrecorded on this vintage']


# ══════════════════════════════════════════════════════════════════════════════
# The two seams a hand-built frame cannot reach: the runner, and the audit's call site
# ══════════════════════════════════════════════════════════════════════════════

def test_the_runner_snapshots_the_bucket_depth_per_batch_and_flushes_it_with_the_bundle():
    """Source inspection, the `test_era_wiring` ledger precedent: the per-bucket rows are
    taken beside the whole-geometry level (above the skip guard), the 4-tuple is unpacked
    positionally at BOTH batch-stats sites, and `fi` rides both bundle flushes and the
    checkpoint clear."""
    import inspect
    import Optimization.simdriver.strategy_runner as sr
    src = inspect.getsource(sr._build_leaf)
    assert 'fi.extend((i, *_k, _n) for _k, _n in mgr.free_bin_depth_by_bucket())' in src
    assert src.index('_free = mgr.free_bin_depth()') < src.index('fi.extend((i, *_k, _n)')
    assert src.count('free_index=fi)') == 2, 'both bundle flushes carry the per-bucket rows'
    assert 'fi.clear()' in src
    assert src.count('_bs.put_spills,') == 1 and src.count('bs.put_spills, bs.recv_repacks') == 1, (
        'the 4-tuple must be unpacked at the skipped-batch site AND the normal site')


class _Ctx:
    """The slice of `EvalContext` the audit's verdict reads: the memo caches, the strategy
    map and the four frame accessors, delegating to the real request broker so the test
    walks `audit._verdict_for -> ctx.free_index_df -> requests.free_index_frame -> _fidf ->
    load_free_index` on a real file."""

    def __init__(self, db, run_id):
        from Optimization.Performance_Evaluations.core import requests as rq
        self._rq = rq
        self._by_key = {'fifo': {'db_path': db, 'run_id': run_id}}
        self._bcache, self._tcache, self._wcache = {}, {}, {}
        self._ccache, self._ficache = {}, {}
        self.aisle_unittype_map, self.aisle_handling_map = {}, {}

    def batch_df(self, key):
        return self._rq.batch_frame(self, key)

    def work_df(self, key):
        return self._rq.work_frame(self, key)

    def carry_df(self, key):
        return self._rq.carry_frame(self, key)

    def free_index_df(self, key):
        return self._rq.free_index_frame(self, key)


def test_a_forced_spill_fails_the_clause_through_the_audits_call_site(tmp_path):
    from Optimization.Performance_Evaluations.common.frames import _sdf
    from Optimization.persistence.Picking_Data import (
        load_shift_days, save_checkpoint_bundle, save_shift_days)
    db, run_id = _fresh_db(tmp_path)
    batches = [_bs(run_id, d, day=d, makespan=0.50 * 2 * S, items=1000, demanded=1000)
               for d in range(2)]
    batches[1].put_spills = 1
    save_checkpoint_bundle(db, run_id, batch_stats=batches, task_stats=[], picker_events=[],
                           picks=[], bin_placements=[], bin_evictions=[], aisle_metrics=[],
                           reorder_queue=[], free_index=_rows(0, 3, 40) + _rows(1, 0, 40))
    save_shift_days(db, run_id, [(0, S, S - 500, True, 0, 0, 0, 0, 0, 0, S - 500),
                                 (1, 2 * S, 2 * S - 500, True, 0, 0, 0, 0, 0, 0, 2 * S - 500)])
    ctx = _Ctx(db, run_id)
    exp = _exp(setup_free={A: 3, B: 40})
    sdf = _sdf(load_shift_days(db, run_id), ctx.batch_df('fifo'), ctx.work_df('fifo'), exp)
    v = audit._verdict_for(ctx, 'fifo', sdf, exp)
    c = v.clauses['rework']
    assert not c.passed and '1 tier spill(s)' in c.reason and A in c.reason, c.reason
    assert c.reading['buckets'][A]['dry_batches'] == [1]
    assert ctx._ficache['fifo'] is ctx.free_index_df('fifo'), 'memoised like every frame'
