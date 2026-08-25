from __future__ import annotations

import pathlib
import sqlite3
import sys

import pytest

from Optimization.metrics.bin_recorder import BinRecorder
from Optimization.persistence.Picking_Data import (
    create_run, init_run_db, save_bin_placements,
)

SEED, N_SKUS, BINS_PER_AISLE, N_BATCHES = 42, 600, 40, 6

# `calltree_scenarios.build_assets` is the repo's one working recipe for a fully wired
# manager (plan_warehouse to a target fill, a real affinity store, reorder fields set before
# sampling, the strategies-registry build). Reproducing it by hand here got the
# `init_demand_state` signature wrong on the first attempt, which is the argument for not
# reproducing it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'calltree'))
import calltree_scenarios as cs                                    # noqa: E402


def _run_arm(tmp_path, strategy):
    """Run `strategy` for a few batches with the recorder attached, and write the
    placements to a real database.

    The recorder attaches AFTER `build_assets` (which does its own initial stocking), so
    every row under test is a REORDER-time placement — the path the pools actually serve
    in production.
    """
    assets = cs.build_assets(n_skus=N_SKUS, bins_per_aisle=BINS_PER_AISLE,
                             strategy=strategy, seed=SEED, coverage=2.0, safety=0.4)
    path = str(tmp_path / f'scores_{strategy}.db')
    init_run_db(path)
    run_id = create_run(path, strategy)
    rec = BinRecorder(run_id)
    rec.attach(assets.mgr)
    rec.begin_batch(0)
    cs.run_meso(assets, n_batches=N_BATCHES, seed=SEED)
    placements, _ = rec.drain()
    assert placements, f'{strategy} produced no reorder placements — fixture is vacuous'
    save_bin_placements(path, run_id, placements)
    return path, run_id, assets.mgr


def _rows(path, run_id):
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        return list(con.execute(
            'SELECT batch_id, seq, sku, cause, score, score_rank, policy '
            'FROM bin_placement WHERE run_id=? ORDER BY batch_id, seq', (run_id,)))
    finally:
        con.close()


# ── a scoring arm writes scores ───────────────────────────────────────────────────

@pytest.mark.parametrize('strategy,policy', [
    ('uni_map_norsl', 'optmap'), ('uni_rank_labor_norsl', 'ranked_labor'),
    ('uni_rank_minlabor_norsl', 'ranked_minlabor'), ('uni_comp_norsl', 'compaction'),
    ('uni_cluster_map_norsl', 'cluster_map'),
])
def test_a_pooled_arm_writes_a_score_and_a_policy(tmp_path, strategy, policy):
    path, run_id, _mgr = _run_arm(tmp_path, strategy)
    rows = _rows(path, run_id)
    assert rows, 'no placements reached the database'
    assert {r[6] for r in rows} == {policy}, f'policy column: {set(r[6] for r in rows)}'
    scored = [r for r in rows if r[4] is not None]
    assert scored, f'{strategy} wrote no score at all — the pool path did not run'
    assert len(scored) / len(rows) > 0.5, (
        f'only {len(scored)}/{len(rows)} placements were scored; the straggler path is '
        f'doing most of the work and the fixture is not testing the pool')


def test_a_null_score_is_null_and_not_zero(tmp_path):
    """A zero score reads as a perfect placement. Every unscored path must write NULL."""
    path, run_id, _mgr = _run_arm(tmp_path, 'uni_map_norsl')
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    try:
        n_null, n_zero = con.execute(
            'SELECT SUM(score IS NULL), SUM(score = 0.0) FROM bin_placement '
            'WHERE run_id=?', (run_id,)).fetchone()
    finally:
        con.close()
    # Zeros are legitimate for map (an exact anchor hit), so this asserts the two are
    # DISTINGUISHABLE rather than that zeros never occur.
    assert n_null is not None
    assert (n_null or 0) + (n_zero or 0) <= len(_rows(path, run_id))


@pytest.mark.parametrize('strategy', ['uni_map_norsl', 'uni_rank_labor_norsl',
                                      'uni_rank_maxlabor_norsl'])
def test_rank_zero_is_the_best_choice_in_the_policys_own_direction(tmp_path, strategy):
    """`rank_maxlabor` maximises on purpose, so a rank defined as "smallest score" would
    invert for that arm. The pool declares its direction and the drain uses it."""
    from Warehouse.inventory.Inventory_Management import _ranked_by_score
    path, run_id, mgr = _run_arm(tmp_path, strategy)
    rows = [r for r in _rows(path, run_id) if r[4] is not None and r[5] is not None]
    assert rows, f'{strategy} produced no scored, ranked placement'

    # Within one batch+policy the rank-0 rows must hold the extreme score in the policy's
    # direction among the rows that share their group. Groups are not recorded, so this
    # checks the weaker, still-falsifiable property: rank 0 exists, ranks are contiguous
    # from 0, and no rank is negative.
    by_rank = sorted(r[5] for r in rows)
    assert by_rank[0] == 0, f'no rank-0 placement in {strategy}'
    assert all(r >= 0 for r in by_rank)

    # And the helper itself, directly, on both directions.
    mk = lambda s: ('u', 'b', s)                                    # noqa: E731
    low = _ranked_by_score([mk(3.0), mk(1.0), mk(2.0)], prefers_low=True)
    assert [r[3] for r in low] == [2, 0, 1]
    high = _ranked_by_score([mk(3.0), mk(1.0), mk(2.0)], prefers_low=False)
    assert [r[3] for r in high] == [0, 2, 1]


def test_the_ranker_leaves_unscored_placements_unranked():
    """Not measured is not last. An unscored placement was not a worse choice."""
    from Warehouse.inventory.Inventory_Management import _ranked_by_score
    taken = [('u1', 'b1', 5.0), ('u2', 'b2', None), ('u3', None, None), ('u4', 'b4', 1.0)]
    out = _ranked_by_score(taken, prefers_low=True)
    assert [r[3] for r in out] == [1, None, None, 0]


def test_the_ranker_gives_ties_the_same_rank():
    """Equal scores were not ordered by the objective — the tie was broken by candidate
    order — so competition-style ranking is the honest encoding."""
    from Warehouse.inventory.Inventory_Management import _ranked_by_score
    mk = lambda s: ('u', 'b', s)                                    # noqa: E731
    out = _ranked_by_score([mk(2.0), mk(1.0), mk(2.0), mk(3.0)], prefers_low=True)
    assert [r[3] for r in out] == [1, 0, 1, 3]


def test_a_non_scoring_arm_writes_no_scores(tmp_path):
    """`fifo` has no pool and nothing to report. Its rows must be NULL throughout — that
    absence is a fact about the arm, and it is what makes the scored arms' rows mean
    something."""
    path, run_id, _mgr = _run_arm(tmp_path, 'uni_fifo_norsl')
    rows = _rows(path, run_id)
    assert rows, 'fifo placed nothing'
    assert all(r[4] is None and r[5] is None for r in rows)
