"""test_skipped_batch_demand_carry.py — a skipped batch's demand must survive the `continue`.

`_pending` is reassigned at the BOTTOM of the runner's batch loop. The `if not tasks: continue`
sits above it, so a skipped batch used to jump straight past the reassignment: its own sampled
demand ceased to exist, and the PREVIOUS batch's carry was re-offered in its place.

Measured before the fix, forcing one skip in a 6-batch run with rollover on: the skipped batch
wanted 993 units and the next batch was handed 898 IN TOTAL — fewer than the skipped batch alone
had asked for. `cons_breaks` stayed 0 the whole time, and it would: the conservation ledger is a
STOCK ledger over bins, so demand that never reached a bin is invisible to it. Nothing in the
repo could see this.

WHY THIS FILE EXISTS AT ALL, given `test_skipped_batch_counters.py` already covers the branch:
that file is a SOURCE SCAN (`inspect.getsource` + a regex over the loop body). It pins call
ordering, which is real, but it cannot observe a value — so it passed throughout. A defect in
what the loop COMPUTES needs a test that runs it.

The observation point is `Task.from_batch_with_shortfall`, which the runner hands `_eff_batch` on
every batch. That is the effective demand, carry included, so recording its argument answers the
question directly without reaching into runner locals.

Run:  python -m pytest Tests/integration/test_skipped_batch_demand_carry.py -q
"""
from __future__ import annotations

import logging
import os
import queue

import pytest

from Optimization import run_simulation as rs
from Optimization.simdriver import strategy_runner as sr
from Warehouse.generation import generate_affinity as ga
from Warehouse.generation.generate_inventory import (
    Family, build_inventory_from_plan, save_inventory_to_db,
)

_DIM = {'dist': 'uniform', 'low': 20, 'high': 44}
_WT = {'dist': 'volume_poisson'}
_SKIP_AT = 2
_N_BATCHES = 5


def _run(tmp_path, monkeypatch, *, roll_over, skip_at=_SKIP_AT, n_skus=150):
    """One store arm through the real worker, with batch `skip_at` starved of tasks.

    Returns the list of effective-demand dicts, one per batch, in order.
    """
    seen: list = []
    orig = sr.Task.from_batch_with_shortfall

    def spy(batch, warehouse, manager=None, cart=None):
        idx = len(seen)
        seen.append(dict(batch.items))
        tasks, short = orig(batch, warehouse, manager=manager, cart=cart)
        if idx == skip_at:
            # Everything this batch wants is unpickable -> the runner's `if not tasks` branch.
            return [], dict(batch.items)
        return tasks, short

    log = logging.getLogger('skip-carry')
    log.setLevel(logging.ERROR)
    g = rs.CONFIG['global']
    monkeypatch.setitem(g, 'n_batches', _N_BATCHES)
    monkeypatch.setitem(g, 'roll_over_unpicked', roll_over)
    monkeypatch.setitem(g, 'cut_at_day_end', True)
    monkeypatch.setitem(g, 'work_day_seconds', 2_000.0)
    monkeypatch.setitem(rs.CONFIG['channels']['store'], 'configs', [rs.REGRESSION_CONFIGS[0]])

    plan = [Family('food', 0.6, (0.5, 0.5), _DIM, _DIM, _DIM, _WT),
            Family('clothing', 0.4, (0.5, 0.5), _DIM, _DIM, _DIM, _WT)]
    inv = build_inventory_from_plan(num_skus=n_skus, plan=plan, seed=3)
    inv_db = str(tmp_path / 'inv.db')
    save_inventory_to_db(inv, inv_db, {'name': 'skipcarry', 'num_skus': n_skus})
    aff_db = str(tmp_path / 'aff.db')
    conn = ga._init_db(aff_db)
    skus = sorted(c.sku for c in inv.orders)
    conn.executemany('INSERT OR REPLACE INTO affinity (sku_i,sku_j,lift) VALUES (?,?,?)',
                     [r for a, b in zip(skus, skus[1:]) for r in ((a, b, 1.5), (b, a, 1.5))])
    conn.commit()
    conn.close()

    build = str(tmp_path / 'build'); os.makedirs(build, exist_ok=True)
    shared = rs.build_shared_assets(
        # No `max_bins`: a bin cap that binds below what the run's DECLARED levels need
        # now refuses the plan rather than fielding less (department-calibration, "Field
        # the floor", decision 3), and this fixture's cap sat below the 60-bucket
        # structural floor anyway -- it was already being warned past, not honoured.
        inv_db, aff_db, log, max_skus=n_skus, min_bins=3000,
        keyframe_interval=0, warehouse_db_path=os.path.join(build, 'warehouse.db'))
    pair = str(tmp_path / 'run'); os.makedirs(pair, exist_ok=True)
    mixed, runs = rs._channel_runs_for(shared['inventory'])
    ch, cfg = runs[0]
    args, _ = rs._prepare_channel_run(ch, cfg, mixed, shared, pair, log, workers=1)
    a = args[0]
    a['log_queue'] = queue.Queue()

    monkeypatch.setattr(sr.Task, 'from_batch_with_shortfall', staticmethod(spy))
    res = sr._run_strategy_worker(a)
    return seen, res


# ── the carry survives ────────────────────────────────────────────────────────────

def test_a_skipped_batch_hands_its_whole_demand_to_the_next_one(tmp_path, monkeypatch):
    """THE regression. Nothing was picked, so every unit the skipped batch wanted must appear
    in the next batch's effective demand — on top of that batch's own fresh sampling."""
    seen, res = _run(tmp_path, monkeypatch, roll_over=True)
    assert res.get('skipped') == 1, f'the fixture did not skip exactly one batch: {res}'
    assert len(seen) > _SKIP_AT + 1, 'the run ended before the batch after the skip'

    skipped, after = seen[_SKIP_AT], seen[_SKIP_AT + 1]
    assert sum(skipped.values()) > 0, 'the skipped batch wanted nothing; nothing is proven'

    missing = {s: q for s, q in skipped.items() if after.get(s, 0) < q}
    assert not missing, (
        f'{sum(skipped.values()) - sum(min(q, after.get(s, 0)) for s, q in skipped.items())} '
        f'units across {len(missing)} skus did not carry past the skip')
    # ...and the total must EXCEED the skipped batch's, because the next batch samples too
    assert sum(after.values()) > sum(skipped.values()), (
        f'the next batch was handed {sum(after.values())} units against the skipped batch\'s '
        f'{sum(skipped.values())} — it cannot contain both the carry and fresh demand')


def test_the_conservation_ledger_cannot_see_this_which_is_why_it_needs_a_test(tmp_path,
                                                                              monkeypatch):
    """Pins the REASON the defect was invisible, so nobody deletes this file believing the
    ledger already covers it. The ledger is a stock ledger over bins; lost demand never
    reached a bin, so it balances perfectly while the carry leaks."""
    _seen, res = _run(tmp_path, monkeypatch, roll_over=True)
    assert res.get('cons_breaks') == 0, (
        'the conservation ledger DID break here — if it can see this, the claim in this '
        'file and in the runner comment is wrong and both need rewriting')


# ── and the flag-off path is untouched ────────────────────────────────────────────

def test_with_rollover_off_a_skipped_batch_carries_nothing(tmp_path, monkeypatch):
    """The fix assigns `{}` on the path that already held `{}`, so the store-only default is
    byte-identical. Asserted as behaviour: no batch may inherit anything."""
    seen, res = _run(tmp_path, monkeypatch, roll_over=False)
    assert res.get('skipped') == 1, f'the fixture did not skip: {res}'

    skipped, after = seen[_SKIP_AT], seen[_SKIP_AT + 1]
    assert sum(skipped.values()) > 0
    carried = {s: q for s, q in skipped.items() if after.get(s, 0) >= q}
    assert len(carried) < len(skipped), (
        'with rollover OFF every sku of the skipped batch reappeared intact — the carry is '
        'firing on a path that must not have one')


def test_the_skip_branch_is_what_is_being_exercised(tmp_path, monkeypatch):
    """Non-vacuity for the whole file: without the forced skip there is no skipped batch, so
    every assertion above would be about an ordinary batch."""
    seen, res = _run(tmp_path, monkeypatch, roll_over=True, skip_at=10 ** 6)
    assert res.get('skipped') == 0, 'a batch skipped on its own; the fixture is not in control'
    assert len(seen) == _N_BATCHES
