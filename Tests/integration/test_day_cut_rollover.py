"""test_day_cut_rollover.py — the cut, end to end, across batches.

The unit tests prove the cut conserves WITHIN a batch. This asks the cross-batch question,
which is the one the requirement is actually about: "if the work from pick or puts or inbound
does not complete, I want the workload to rollover to the next shift."

A NOTE ON WHAT IS AND IS NOT AN INVARIANT, because getting this wrong produced a scary-looking
"LOST 345" the first time it was measured. Summing scheduled work across batches is NOT a
conservation quantity — a batch's scheduled work includes the previous batch's carry, so the
sum double-counts every carried unit. The invariant is per batch:

    picked_i + carried_i == scheduled_i

Measured across day lengths on a 400-SKU arm over ten batches:

    no cut / 100,000s / 20,000s   4,000 picked,  0 cuts, nothing ever carried
    5,000s                        3,983 picked, 20 cuts, backlog spikes to 345 and clears
    2,000s                        3,954 picked, 43 cuts, oscillates and clears
    1,000s                        3,477 picked, 88 cuts, backlog never clears — GROWING

The last row is not a bug. It is a warehouse that cannot keep up with its own demand, and the
point of the carry is that this is now visible instead of silently discarded.

Run:  python -m pytest Tests/integration/test_day_cut_rollover.py -q
"""
from __future__ import annotations

import copy
import pathlib
import random
import sys

import pytest

from Warehouse.kernel.timeline import ReleaseSchedule, WorkDay
from Warehouse.picking.Pick import carry_residue
from Warehouse.picking.Workload_Builder import Batch, Task
from Warehouse.picking.fast_pick import DeferredPickSimulation

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402

N_BATCHES = 8


def _run(day_seconds, n_batches=N_BATCHES):
    """The runner's loop reduced to the parts the cut touches. Returns
    (picked, carried-per-batch, cuts)."""
    a = cs.build_assets(n_skus=300, bins_per_aisle=30, strategy='uni_rank_labor_norsl',
                        seed=42, coverage=2.0, safety=0.4)
    mgr, wh = a.mgr, a.warehouse
    rel = ReleaseSchedule(WorkDay(length=day_seconds)) if day_seconds else None
    arm_clock, picked, cuts = 0.0, 0, 0
    pending: dict = {}
    traj: list = []
    for i in range(n_batches):
        mgr.check_reorders()
        base = Batch(a.batch_cfg, a.inventory, affinity=None, rng=random.Random(1000 + i))
        if pending:
            # The shared batch is NEVER mutated — a shallow copy rebinds `items` only.
            eff = copy.copy(base)
            eff.items = dict(base.items)
            for sku, q in pending.items():
                eff.items[sku] = eff.items.get(sku, 0) + q
        else:
            eff = base
        tasks = Task.from_batch(eff, wh, manager=mgr)
        if not tasks:
            traj.append(sum(pending.values()))
            continue
        scheduled = sum(sum(carry_residue(t, 0, {}).values()) for t in tasks)
        de = rel.day.end_of(rel.day.index_of(arm_clock)) if rel else None
        sim = DeferredPickSimulation(tasks, a.pick_cfg, manager=mgr,
                                     start_times=[arm_clock] * a.pick_cfg.num_pickers,
                                     day_end=de)
        evs = sim.run()
        got = sum(e.quantity or 0 for e in evs if e.event_type == 'pick')
        carried = sum(sim.carried.values())
        # THE invariant, on every batch.
        assert got + carried == scheduled, (
            f'batch {i} (day={day_seconds}): {got} picked + {carried} carried '
            f'!= {scheduled} scheduled')
        picked += got
        cuts += sum(1 for e in evs if e.event_type == 'cut')
        pending = dict(sim.carried)
        traj.append(carried)
        arm_clock = max(e.time for e in evs)
    return picked, traj, cuts


@pytest.mark.parametrize('day', [None, 100_000.0, 5_000.0, 2_000.0])
def test_every_batch_conserves_its_scheduled_work(day):
    """The assert lives inside `_run`, so this both exercises it and pins the shape of the
    result: a long day cuts nothing, a short one cuts and carries."""
    picked, traj, cuts = _run(day)
    assert picked > 0
    if day is None or day >= 100_000.0:
        assert cuts == 0 and not any(traj), f'a long day should not cut: {traj}'
    else:
        assert cuts > 0, f'day={day} never cut; the case is untested'
        assert any(traj), 'cuts happened but nothing was carried'


def test_a_short_day_picks_less_and_cuts_more_than_a_long_one():
    """Direction check. If a shorter day did not reduce throughput, the cut would not be
    doing anything and every conservation assertion above would be vacuous."""
    long_picked, _t1, long_cuts = _run(100_000.0)
    short_picked, _t2, short_cuts = _run(1_000.0)
    assert long_cuts == 0 and short_cuts > 0
    assert short_picked < long_picked, (short_picked, long_picked)


def test_a_backlog_that_cannot_clear_stays_visible():
    """Not a bug — a warehouse that cannot keep up with its own demand. The point of the
    carry is that this is observable rather than silently discarded, so the test asserts the
    backlog is REPORTED, not that it is zero."""
    _p, traj, cuts = _run(1_000.0, n_batches=10)
    assert cuts > 0
    assert sum(traj) > 0, 'a day this short should leave work behind'
    assert traj[-1] > 0, (
        f'the backlog cleared at a day length that cannot sustain the demand: {traj}')


def test_the_shared_batch_is_never_mutated():
    """Every arm of a family reads the same precomputed pickle. Adding the carry by mutating
    it would corrupt every other arm — silently, and only on the arms that ran later."""
    a = cs.build_assets(n_skus=200, bins_per_aisle=20, strategy='uni_fifo_norsl',
                        seed=1, coverage=2.0, safety=0.4)
    base = Batch(a.batch_cfg, a.inventory, affinity=None, rng=random.Random(7))
    before = dict(base.items)
    eff = copy.copy(base)
    eff.items = dict(base.items)
    for sku in list(eff.items)[:5]:
        eff.items[sku] += 100
    assert base.items == before, 'the shared batch was mutated'
    assert eff.items != before


# ── all three causes roll over, and the gate is a true no-op ──────────────────────
#
# "items that are not picked in a batch do not rollover to the next batch. this is a major
# behavior change... items that are not picked on a specific batch must be rolled over."
#
# Three causes, each counted where it is known and none derived as a residual:
#
#   unpicked_daycut       the picker never reached the bin       (Pick.carry_residue)
#   unpicked_unavailable  reached it; the bin held less          (the loops' clamp)
#   unplaced              no bin held the SKU at all             (from_batch's shortfall)

def _run_causes(roll, day=None, n_batches=6):
    """Returns (picked, {reason: units}, final pending)."""
    a = cs.build_assets(n_skus=250, bins_per_aisle=30, strategy='uni_rank_labor_norsl',
                        seed=42, coverage=2.0, safety=0.4)
    mgr, wh = a.mgr, a.warehouse
    rel = ReleaseSchedule(WorkDay(length=day)) if day else None
    arm, picked = 0.0, 0
    pending: dict = {}
    tally = {'unpicked_daycut': 0, 'unpicked_unavailable': 0, 'unplaced': 0}
    for i in range(n_batches):
        mgr.check_reorders()
        base = Batch(a.batch_cfg, a.inventory, affinity=None, rng=random.Random(1000 + i))
        if pending:
            eff = copy.copy(base)
            eff.items = dict(base.items)
            for sku, q in pending.items():
                eff.items[sku] = eff.items.get(sku, 0) + q
        else:
            eff = base
        tasks, short = Task.from_batch_with_shortfall(eff, wh, manager=mgr,
                                                      cart=a.pick_cfg.cart)
        if not tasks:
            continue
        de = rel.day.end_of(rel.day.index_of(arm)) if rel else None
        sim = DeferredPickSimulation(tasks, a.pick_cfg, manager=mgr,
                                     start_times=[arm] * a.pick_cfg.num_pickers,
                                     day_end=de)
        evs = sim.run()
        picked += sum(e.quantity or 0 for e in evs if e.event_type == 'pick')
        now: dict = {}
        for reason, src in (('unpicked_daycut', sim.carried),
                            ('unpicked_unavailable', sim.unmet),
                            ('unplaced', short or {})):
            for sku, q in src.items():
                if q:
                    now[sku] = now.get(sku, 0) + q
                    tally[reason] += q
        pending = now if roll else {}
        arm = max(e.time for e in evs)
    return picked, tally, sum(pending.values())


def test_the_rollover_gate_is_a_true_no_op_when_off():
    """Nothing feeds back, so the arm runs exactly as it did before this machinery existed.
    The carryover rows are still written — observation is free, and it is the feedback that
    is the behaviour change."""
    _p, tally, pending = _run_causes(roll=False, day=2_000.0)
    assert pending == 0, 'demand fed back with the gate off'
    assert sum(tally.values()) > 0, 'nothing was carried at all; the case is untested'


def test_rolling_over_recovers_work_that_would_otherwise_evaporate():
    """The requirement, measured. At a 2,000 s day the cut leaves a lot behind; with the
    rollover on, later batches pick it up instead of it vanishing."""
    off, _t1, _p1 = _run_causes(roll=False, day=2_000.0)
    on, _t2, _p2 = _run_causes(roll=True, day=2_000.0)
    assert on > off, f'rolling over picked no more than discarding: {on} vs {off}'


def test_every_cause_is_counted_separately():
    """`carryover.reason` exists so "ran out of day" and "ran out of stock" are different
    problems with different fixes. Merging them into one number would make either one
    invisible behind the other."""
    _p, tally, _pend = _run_causes(roll=False, day=2_000.0)
    assert tally['unpicked_daycut'] > 0, 'a 2,000 s day should cut'
    assert tally['unplaced'] > 0, 'this catalogue should have unplaceable demand'
    # `unpicked_unavailable` is REAL MACHINERY WITH NO OCCURRENCES in this workload: the
    # live-stock clamp never bites here, which is the same fact `items_realized` reports
    # when it equals `total_items` on all 1,700 task rows of the coverage sweep. Asserted as
    # zero rather than quietly ignored, so the day it becomes non-zero is a visible change.
    assert tally['unpicked_unavailable'] == 0, (
        f"the live-stock clamp now bites ({tally['unpicked_unavailable']} units) — that is "
        f"new, and worth understanding before this assertion is relaxed")


def test_no_cut_still_carries_the_unplaceable_half():
    """Two of the three causes have nothing to do with the day. A run with no cut at all
    still has demand no bin could satisfy, and that is exactly what used to evaporate."""
    _p, tally, _pend = _run_causes(roll=False, day=None)
    assert tally['unpicked_daycut'] == 0, 'no cut, so nothing should be cut'
    assert tally['unplaced'] > 0
