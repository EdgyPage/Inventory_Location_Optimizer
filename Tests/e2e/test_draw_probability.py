"""test_draw_probability.py — the gate for the DRAW PROBABILITY characterisation.

`Optimization/simdriver/draw_probability.py` exists because the coverage record prices a SKU's
prior lines at its line share `freq / sum freq`, which is the sampler's base WEIGHT and not its
inclusion probability (.scratch/department-calibration, "Close the fulfillment fill-law gap").
Everything downstream — the floor solve, the warehouse it sizes — rests on the counts this module
produces actually being the counts the declared sampler produces, so that is what is proved here:

  * the per-SKU counts are EXACTLY the counts of the batch sequence `batch_precompute` builds, so
    the characterisation stream is the run's own stream and the run's script is a prefix of it;
  * the chunked/parallel driver changes no count, and any chunk PREFIX equals a fresh shorter draw
    (the prefix ladder is the whole reason chunk counts are kept apart rather than summed);
  * the unbiased falling-factorial estimator tracks the truth where the plug-in carries the upward
    Jensen bias that `M` is declared to remove — checked against a synthetic section whose `p_s`
    is known exactly, because on a real one it never is;
  * the fingerprint routes a draw to only its own declaration, and a reuse is a reuse.

Run: python -m pytest Tests/e2e/test_draw_probability.py -q
"""
from __future__ import annotations

import os
import random

import numpy as np
import pytest

from Optimization.simdriver import batch_precompute as BP
from Optimization.simdriver import draw_probability as DP
from Warehouse.catalog.Affinity_Store import AffinityStore
from Warehouse.catalog.Order import Order
from Warehouse.generation.generate_inventory import Inventory, save_inventory_to_db
from Warehouse.picking.Workload_Builder import BatchConfig

SEED_B = 1337
_CATS = [('manual', 'standard'), ('manual', 'fragile'), ('automated', 'standard')]


# ── fixtures: the same shape test_batch_precompute builds, for the same reason ────────────
def _make_inventory_db(path: str, n: int, seed: int = 0) -> str:
    rng = random.Random(seed)
    orders = []
    for s in range(1, n + 1):
        h, c = _CATS[s % len(_CATS)]
        orders.append(Order.build(
            sku=s, handling=h, category=c,
            length=rng.randint(5, 40), width=rng.randint(5, 40),
            height=rng.randint(5, 40), weight=rng.randint(1, 50),
            relative_frequency=rng.uniform(0.01, 1.0), qty_rate=rng.randint(1, 10),
            lead_time_mean=0.0, supply_cv=0.0))
    save_inventory_to_db(Inventory(orders), path, {'test': True})
    return path


def _make_affinity_db(path: str, n: int, seed: int = 0) -> str:
    store = AffinityStore(path)
    rng = random.Random(seed)
    rows = []
    for i in range(1, n + 1):
        for j in range(i + 1, min(i + 4, n + 1)):
            lv = rng.uniform(1.1, 3.0)
            rows += [(i, j, lv), (j, i, lv)]
    store._conn.executemany('INSERT OR REPLACE INTO affinity VALUES (?,?,?)', rows)
    store._conn.commit()
    store._conn.close()
    return path


def _cfg(inv_db: str) -> BatchConfig:
    inv = BP._load_worker_inventory(inv_db, None, None)
    return BatchConfig(inventory_size=len(inv.orders), mean_fraction=0.2, std_fraction=0.05)


def _counts_from_batches(inv_db: str, batches: list) -> np.ndarray:
    """Count the sequence `batch_precompute` produces, positionally over the same inventory."""
    inv = BP._load_worker_inventory(inv_db, None, None)
    pos = {int(c.sku): i for i, c in enumerate(inv.orders)}
    out = np.zeros(len(inv.orders), dtype=np.int64)
    for b in batches:
        for sku in b.items:
            out[pos[int(sku)]] += 1
    return out


# ── A. the counts ARE the run's batches ──────────────────────────────────────────────────
@pytest.mark.parametrize('use_aff', [False, True])
def test_counts_match_the_batch_sequence(tmp_path, use_aff):
    """The claim the whole form rests on: same seed, same declaration ⇒ the characterisation
    counts the very batches the run draws.  If this fails, `p_s` describes some other demand."""
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 200, seed=11)
    aff_db = _make_affinity_db(str(tmp_path / 'aff.db'), 200, seed=7) if use_aff else None
    cfg = _cfg(inv_db)
    n = 24

    batches = BP.precompute_batches(inv_db, None, None, aff_db, cfg, SEED_B, n, workers=1)
    want = _counts_from_batches(inv_db, batches)

    _bounds, counts = DP.precompute_counts(inv_db, None, None, aff_db, cfg, SEED_B, n,
                                           workers=1, chunks=4)
    assert np.array_equal(counts.sum(axis=0), want)


# ── A'. the chunked driver is not allowed to change a count ──────────────────────────────
def test_serial_equals_parallel(tmp_path):
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 200, seed=3)
    aff_db = _make_affinity_db(str(tmp_path / 'aff.db'), 200, seed=9)
    cfg = _cfg(inv_db)
    n = max(DP._MIN_PARALLEL, 20)

    _b1, ser = DP.precompute_counts(inv_db, None, None, aff_db, cfg, SEED_B, n,
                                    workers=1, chunks=4)
    _b2, par = DP.precompute_counts(inv_db, None, None, aff_db, cfg, SEED_B, n,
                                    workers=4, chunks=4)
    assert np.array_equal(ser.sum(axis=0), par.sum(axis=0))


# ── A''. a chunk PREFIX is a shorter draw ────────────────────────────────────────────────
def test_chunk_prefix_equals_a_shorter_draw(tmp_path):
    """The ladder's premise: `counts[:j].sum(0)` at prefix `M_j` must equal drawing `M_j`
    batches outright, or every rung below the last one is reporting a different sample."""
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 160, seed=4)
    cfg = _cfg(inv_db)
    n, chunks = 24, 4

    bounds, counts = DP.precompute_counts(inv_db, None, None, None, cfg, SEED_B, n,
                                          workers=1, chunks=chunks)
    for j in range(1, len(bounds) + 1):
        m_j = bounds[j - 1][1]
        short = BP.precompute_batches(inv_db, None, None, None, cfg, SEED_B, m_j, workers=1)
        assert np.array_equal(counts[:j].sum(axis=0), _counts_from_batches(inv_db, short)), \
            f'prefix {j} (M={m_j}) is not the same sample as a fresh M={m_j} draw'


# ── B. the estimators, against a section whose p_s is known exactly ──────────────────────
def _true_Q(p: np.ndarray, K: int) -> float:
    return float((p * (1.0 - (1.0 - p) ** K)).sum() / p.sum())


@pytest.mark.parametrize('K', [1, 2, 4])
def test_unbiased_beats_plug_in_at_small_M(K):
    """Counts drawn from a KNOWN `p_s` with the law exactly true, pushed through both
    estimators.  The plug-in must over-state (the functional is convex in `p_s`, which is why
    a finite window inflates it) and the unbiased form must land closer to the truth."""
    rng = np.random.default_rng(20260912)
    p = rng.uniform(0.002, 0.05, size=40_000)
    M = 64                                          # deliberately small: the bias must show
    counts = rng.binomial(M, p)

    truth = _true_Q(p, K)
    plug_in, unbiased = DP.prior_line_probability(counts, M, K)

    assert plug_in > unbiased, 'the plug-in must carry the upward Jensen bias'
    assert abs(unbiased - truth) < abs(plug_in - truth)
    assert abs(unbiased - truth) < 0.02 * truth


@pytest.mark.parametrize('K', [1, 3])
def test_estimators_converge_as_M_grows(K):
    """The criterion that DECLARES M: the two estimators agree once M is large enough, and
    the gap shrinks monotonically in M.  A gap that did not close would mean no M is enough."""
    rng = np.random.default_rng(7)
    p = rng.uniform(0.002, 0.05, size=40_000)

    gaps = []
    for M in (32, 256, 2048):
        counts = rng.binomial(M, p)
        a, b = DP.prior_line_probability(counts, M, K)
        gaps.append(abs(a - b))
    assert gaps[0] > gaps[1] > gaps[2]
    # 2% relative, not 1%: measured 1.3% at M = 2,048 on this synthetic section (p in
    # [0.002, 0.05], K = 3).  The bound is deliberately the OBSERVED behaviour rather than an
    # aspiration -- what M a real section needs is declared from its own ladder, and the store
    # sits at the bottom of this p range, so it converges slower than this fixture does.
    assert gaps[-1] < 0.02 * _true_Q(p, K)


def test_zero_count_sku_contributes_nothing():
    """A SKU the window never touched has `p_hat = 0` and must contribute 0 to both the
    numerator and the denominator — never a negative term out of the falling factorial."""
    counts = np.array([0, 0, 5, 3], dtype=np.int64)
    plug_in, unbiased = DP.prior_line_probability(counts, 32, 3)
    assert plug_in > 0.0 and unbiased > 0.0
    assert 0.0 <= unbiased <= 1.0


# ── C. the file: fingerprint routing and reuse ───────────────────────────────────────────
def test_fingerprint_routes_and_reuses(tmp_path):
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 120, seed=5)
    cfg = _cfg(inv_db)
    out = str(tmp_path)

    path, fp = DP.ensure_draw_probability(out, inv_db, None, None, None, cfg, SEED_B, 20,
                                          workers=1, chunks=4)
    assert os.path.exists(path)

    blob = DP.load_counts(path, fp)
    assert blob is not None and blob['M'] == 20 and blob['seed_batches'] == SEED_B
    assert len(blob['sku']) == cfg.inventory_size

    # The wrong declaration must not be served this file.
    assert DP.load_counts(path, 'deadbeef' * 5) is None

    # A second call is a reuse: same path, and the file is not rewritten.
    mtime = os.path.getmtime(path)
    path2, fp2 = DP.ensure_draw_probability(out, inv_db, None, None, None, cfg, SEED_B, 20,
                                            workers=1, chunks=4)
    assert (path2, fp2) == (path, fp)
    assert os.path.getmtime(path2) == mtime


def test_different_M_is_a_different_file(tmp_path):
    """M rides the fingerprint, so a draw of a different length can never be mistaken for
    this one — the same guard `batch_precompute` gives a family of a different size."""
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 120, seed=5)
    cfg = _cfg(inv_db)
    out = str(tmp_path)
    p20, _ = DP.ensure_draw_probability(out, inv_db, None, None, None, cfg, SEED_B, 20,
                                        workers=1, chunks=4)
    p40, _ = DP.ensure_draw_probability(out, inv_db, None, None, None, cfg, SEED_B, 40,
                                        workers=1, chunks=4)
    assert p20 != p40


def test_ladder_is_monotone_in_M(tmp_path):
    """Every rung must be a real prefix: strictly increasing M, and every row present."""
    inv_db = _make_inventory_db(str(tmp_path / 'inv.db'), 160, seed=8)
    cfg = _cfg(inv_db)
    path, fp = DP.ensure_draw_probability(str(tmp_path), inv_db, None, None, None, cfg,
                                          SEED_B, 32, workers=1, chunks=4)
    rows = DP.ladder(DP.load_counts(path, fp), ks=(1, 2))
    ms = sorted({r[0] for r in rows})
    assert ms == [8, 16, 24, 32]
    assert len(rows) == len(ms) * 2
