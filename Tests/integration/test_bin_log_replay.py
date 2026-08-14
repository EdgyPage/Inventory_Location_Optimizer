"""test_bin_log_replay.py — PHASE A: can a bin-mutation log replay the simulation exactly?

This is the gate for the whole lossless-recording change, and it runs **before** any production
code moves. If the proposed log is insufficient, that is discovered here, in seconds, instead of
after a schema migration and a 500 GB re-run.

The claim under test
--------------------
The persisted record is lossy because `bin_inventory` logs picks and **never** restocks — measured
on a production arm, rolling it forward from a keyframe loses 59% of the warehouse within five
batches. Every bin mutation in the codebase happens at one of five sites:

    Inventory_Management._execute_placement   `bin_.storage = unit`    -> PLACE    unrecorded today
    inventory_reorder.requeue_bin             `bin_.storage = None`    -> EVICT    unrecorded today
    fast_pick.py / Pick.py                    qty -= / storage = None  -> PICK     already in `picks`
    Storage_Primitive.StorageCart             dead code, zero callers  -> n/a

so PLACE + EVICT + PICK is complete *by construction*. These tests check that construction argument
against a running simulation: fold the log and compare to the warehouse's own bin objects.

Nothing in `Warehouse/` or `Optimization/` is imported-and-patched at module scope — the recorder
rebinds two methods on one manager INSTANCE, the technique `Diagnostics/trace_lifecycle.py` already
uses. Production source is untouched.

    python -m pytest Tests/integration/test_bin_log_replay.py -q
"""
from __future__ import annotations

import pytest

import bin_log_harness as H          # Tests/bench is on sys.path via Tests/conftest.py

N_SKUS = 200
N_BATCHES = 6


@pytest.fixture(scope='module')
def plain():
    """A run with reorders but no reslotting — the shape every production arm has (`norsl`)."""
    inv, wh, mgr = H.build_scenario(n_skus=N_SKUS)
    log = H.attach_recorder(mgr)
    frames = H.run_sim(inv, wh, mgr, log, n_batches=N_BATCHES)
    return inv, wh, mgr, log, frames


@pytest.fixture(scope='module')
def reslot():
    """A run that also evicts. No shipped arm does this, so it has no production data behind it
    and would otherwise go entirely untested."""
    inv, wh, mgr = H.build_scenario(n_skus=N_SKUS, seed=7)
    log = H.attach_recorder(mgr)
    frames = H.run_sim(inv, wh, mgr, log, n_batches=N_BATCHES,
                       reloader=H.make_reloader(move_limit_pct=0.5))
    return inv, wh, mgr, log, frames


# ── the scenarios must actually exercise what they claim ─────────────────────────

def test_the_plain_scenario_actually_reorders(plain):
    """Guards the vacuity trap: `perf_simulation._build_inventory` leaves `reorder_point` unset,
    and without it `check_reorders()` never fires — every assertion below would pass against an
    empty placement log."""
    _inv, _wh, _mgr, log, _frames = plain
    causes = [p.cause for p in log.places]

    assert causes.count('reorder') > 100, 'no restocks fired; the log proves nothing'
    assert causes.count('initial') > 0
    assert len(log.picks) > 100, 'no picks; depletion is untested'


def test_the_reslot_scenario_actually_evicts(reslot):
    """`per_aisle_cap` is `move_limit_pct x XL-aisle bins`, so the production default of 0.005
    floors to ZERO at test scale and `requeue_bin` would never run."""
    _inv, _wh, _mgr, log, _frames = reslot
    assert len(log.evicts) > 100, 'no evictions; the EVICT path is untested'
    assert sum(1 for p in log.places if p.cause == 'reslot') > 100, 'nothing was re-placed'


# ── the proof ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('scenario', ['plain', 'reslot'])
def test_fold_equals_truth_at_every_batch(scenario, request):
    """THE assertion. Folding the log must reproduce the warehouse's own bins, bin for bin, at
    EVERY batch — not merely at keyframes, which is all today's record can manage."""
    _inv, _wh, _mgr, log, frames = request.getfixturevalue(scenario)

    for batch, _after_reorders, truth, _max_t in frames:
        folded = H.fold(log, batch)
        assert folded == truth, (
            f'batch {batch}: fold != truth. '
            f'{len(set(truth) - set(folded))} bins missing from the fold, '
            f'{len(set(folded) - set(truth))} ghosts, '
            f'{len([k for k in set(folded) & set(truth) if folded[k] != truth[k]])} '
            f'differing in sku/qty')


@pytest.mark.parametrize('scenario', ['plain', 'reslot'])
def test_fold_is_exact_at_both_edges_of_a_batch(scenario, request):
    """Intra-batch resolution. Picks carry `sim_time`, so the fold must land on the post-restock
    pre-pick state at t below the first pick, and on the post-pick state at t past the last one.

    That is the frame the viewer animates, and the one that is wrong today.
    """
    _inv, _wh, _mgr, log, frames = request.getfixturevalue(scenario)

    for batch, after_reorders, after_picks, max_t in frames:
        assert H.fold(log, batch, t=-1.0) == after_reorders, f'batch {batch}: start-of-batch frame'
        assert H.fold(log, batch, t=max_t + 1.0) == after_picks, f'batch {batch}: end-of-batch frame'


@pytest.mark.parametrize('scenario', ['plain', 'reslot'])
def test_units_are_conserved_every_batch(scenario, request):
    """`Delta occupancy == placed - picked - evicted`, per batch, in units.

    A single integer per batch that ANY unrecorded mutation would break — cheap enough to run as
    a live invariant in the simulation itself, which is the point of checking it here.
    """
    _inv, _wh, _mgr, log, frames = request.getfixturevalue(scenario)

    prev_units = 0
    for batch, _after_reorders, truth, _max_t in frames:
        placed = sum(p.qty for p in log.places if p.batch == batch)
        picked = sum(k.qty for k in log.picks if k.batch == batch)
        evicted = sum(e.qty for e in log.evicts if e.batch == batch)
        units = sum(q for _sku, q in truth.values())

        assert units - prev_units == placed - picked - evicted, (
            f'batch {batch}: conservation broken — occupancy moved by {units - prev_units} '
            f'but the log accounts for {placed - picked - evicted} '
            f'(placed {placed}, picked {picked}, evicted {evicted})')
        prev_units = units


def test_recorder_agrees_with_the_managers_own_counters(plain):
    """Two independent mechanisms must give the same count, or one of them is wrong.

    `_reorder_placements` is bumped inside `_execute_placement` itself
    (`Inventory_Management.py:677`) and is what `batch_stats.reorder_placements` is built from —
    so this also pins the recorder against the number already shipped in every run.
    """
    _inv, _wh, mgr, log, _frames = plain
    assert len(log.places) == mgr._reorder_placements


def test_recorder_agrees_with_the_reload_counter(reslot):
    _inv, _wh, mgr, log, _frames = reslot
    assert len(log.evicts) == mgr._reload_moves


# ── negative controls: the tests must be capable of failing ──────────────────────

def test_dropping_placements_breaks_the_fold(plain):
    """The control for the whole exercise.

    If the fold still matched without PLACE events, placements would not be the missing term and
    the entire premise would be wrong. This is also a miniature of the production bug: today's
    record is exactly this log minus its placements.
    """
    _inv, _wh, _mgr, log, frames = plain
    crippled = H.BinLog(places=[p for p in log.places if p.cause == 'initial'],
                        evicts=list(log.evicts), picks=list(log.picks))
    crippled._state = log._state

    last_batch, _after, truth, _max_t = frames[-1]
    folded = H.fold(crippled, last_batch)

    assert folded != truth, 'dropping restocks changed nothing — the premise is wrong'
    missing = len(set(truth) - set(folded))
    assert missing > 50, f'only {missing} bins lost; restocks are not load-bearing here'


def test_dropping_evictions_breaks_the_fold(reslot):
    """Ghost bins: without EVICT the fold keeps a unit in its old bin after it has moved — the
    documented failure mode the old reconstruction had between keyframes."""
    _inv, _wh, _mgr, log, frames = reslot
    crippled = H.BinLog(places=list(log.places), evicts=[], picks=list(log.picks))
    crippled._state = log._state

    last_batch, _after, truth, _max_t = frames[-1]
    folded = H.fold(crippled, last_batch)

    assert folded != truth
    assert set(folded) - set(truth), 'expected ghost bins with evictions dropped'


# ── regression: the PK must be collision-proof ───────────────────────────────────

def test_placement_seq_never_collides_within_a_batch():
    """`(run_id, batch_id, seq)` is the primary key, and `save_bin_placements` uses
    INSERT OR REPLACE — so a repeated seq inside one batch does not error, it silently DROPS a
    row.

    That was a real bug: initial stocking records at batch 0 before the loop starts, and the
    loop's first `begin_batch(0)` used to restart `seq` at 0, so any batch-0 reorder overwrote an
    initial fill. It went unnoticed because no shipped arm reorders at batch 0. The counter is
    now run-scoped and monotonic.
    """
    from Optimization.metrics.bin_recorder import BinRecorder

    inv, wh, mgr = H.build_scenario(n_skus=120)
    rec = BinRecorder(run_id=1)
    rec.attach(mgr)
    log = H.BinLog()
    log._state = {'batch': 0, 'place_seq': 0, 'evict_seq': 0, 'evicted_units': set()}
    original = H.begin_batch
    H.begin_batch = lambda l, b: (original(l, b), rec.begin_batch(b))
    try:
        H.run_sim(inv, wh, mgr, log, n_batches=3)
    finally:
        H.begin_batch = original

    keys = [(r.batch_id, r.seq) for r in rec.placements]
    assert keys, 'no placements recorded — the scenario is vacuous'
    assert len(keys) == len(set(keys)), (
        'duplicate (batch_id, seq): INSERT OR REPLACE would silently drop a placement')
    # Ordering must survive the change: seq ascending within a batch is what makes
    # `ORDER BY batch_id, seq` the application order.
    for batch in {b for b, _ in keys}:
        seqs = [s for b, s in keys if b == batch]
        assert seqs == sorted(seqs), f'batch {batch}: seq not ascending'
