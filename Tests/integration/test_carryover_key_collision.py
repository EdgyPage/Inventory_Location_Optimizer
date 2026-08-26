"""test_carryover_key_collision.py — two producers, one key, and the bigger row lost.

The defect, live from the commit that added the pick carry until 2026-08-25:

`carryover` is `PRIMARY KEY (run_id, batch_id, reason, sku) WITHOUT ROWID` and its insert is
`INSERT OR REPLACE`. Two producers wrote into the same per-batch `cov` list and **both used
`reason='unplaced'`**:

  * `Inventory_Manager.carryover_rows(i)` — one row per SKU standing on a put queue. A
    **LEVEL**: the whole standing backlog, re-emitted every batch.
  * the runner's `_shortfall` — demand for which no bin held the SKU. A **FLOW**: this batch's
    miss.

For any `(batch, sku)` where both were non-zero, the pick row was appended second and the
put-away quantity was destroyed. Measured on a hand-built pair: a 500-unit backlog erased by a
3-unit shortfall, no error, no log.

Nothing could catch it. The row count still looked plausible, `carryover` has no reader
outside tests, and the replace semantics are *correct* for the case they were written for —
a resume re-running a batch must overwrite its own rows. `test_queue_state_and_carryover.py`
even pins that behaviour, without noticing the two producers.

Two things stop it here: the pick shortfall took its own reason (`unpicked_unstocked`, which
also puts all three pick causes under one prefix), and the writer now REFUSES a flush that
contains a duplicate key. The refusal is the durable half — a third producer could make the
same mistake with a different word.

Run:  python -m pytest Tests/integration/test_carryover_key_collision.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from Optimization.persistence import Picking_Data as pd

#: The put-away side's vocabulary — LEVELS, re-emitted every batch.
_PUT_REASONS = ('unplaced', 'held')
#: The pick side's — FLOWS, this batch's unserved demand. All three share a prefix so the
#: two families cannot be confused by eye, which is how the collision survived review.
_PICK_REASONS = ('unpicked_daycut', 'unpicked_unavailable', 'unpicked_unstocked')


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / 'sim.db')
    pd.init_run_db(path)
    return path, pd.create_run(path, 'comparison', {})


# ── the vocabularies are disjoint ─────────────────────────────────────────────────

def test_the_two_reason_families_share_no_word():
    """THE regression, stated as the property rather than as the one word that broke it.
    Any overlap means two producers can write one key."""
    assert not (set(_PUT_REASONS) & set(_PICK_REASONS))


def test_the_producers_use_only_their_own_family():
    """Source-checked, because the failure is a producer reaching for the other family's
    word — which is exactly what happened. Read from the two real emitters."""
    import inspect

    from Optimization.simdriver import strategy_runner as sr
    from Warehouse.inventory import Inventory_Management as im

    put_src = inspect.getsource(im.Inventory_Manager.carryover_rows)
    for r in _PICK_REASONS:
        assert f"'{r}'" not in put_src, f'the put-away side emits {r!r}, a pick reason'
    assert "'unplaced'" in put_src and "'held'" in put_src

    run_src = inspect.getsource(sr._run_strategy_worker_impl)
    # The pick side names all three of its own...
    for r in _PICK_REASONS:
        assert f"'{r}'" in run_src, f'the pick side no longer emits {r!r}'
    # ...and must not build a row with a put-away reason. `cov.extend(mgr.carryover_rows(i))`
    # carries those in, which is correct; what must not appear is a literal in an append.
    assert "cov.append((i, 'unplaced'" not in run_src
    assert "'unplaced', _shortfall" not in run_src, (
        'the pick shortfall is emitting the put-away level\'s reason again — this is the '
        'exact collision, and the put-away row will be destroyed')


# ── the writer refuses to destroy a row ───────────────────────────────────────────

def test_a_duplicate_key_in_one_flush_raises(db):
    """The durable half of the fix. A third producer could make the same mistake with a
    different word; this catches it at the write, where the rows still exist."""
    path, run_id = db
    cov = [(7, 'unplaced', 4242, 500),      # the manager's standing backlog
           (7, 'unplaced', 4242, 3)]        # a pick shortfall wearing the same reason
    with pytest.raises(ValueError, match='two rows for'):
        pd.save_carryover(path, run_id, cov)


def test_the_refusal_names_both_quantities(db):
    """So the reader can see which producer is which without instrumenting the run."""
    path, run_id = db
    with pytest.raises(ValueError) as exc:
        pd.save_carryover(path, run_id, [(2, 'held', 9, 500), (2, 'held', 9, 3)])
    msg = str(exc.value)
    assert '500' in msg and '3' in msg
    assert 'INSERT OR REPLACE' in msg


def test_the_guard_does_not_fire_on_legitimate_rows(db):
    """Non-vacuity, and the thing that would make this fix worse than the bug: the same
    SKU in DIFFERENT batches, and different reasons in the SAME batch, are both normal and
    must write."""
    path, run_id = db
    cov = [(0, 'unplaced', 101, 8), (0, 'held', 101, 14),          # same sku, two reasons
           (0, 'unpicked_unstocked', 101, 2),                      # ...and a pick flow
           (1, 'unplaced', 101, 9), (2, 'unplaced', 101, 11)]      # same key, later batches
    pd.save_carryover(path, run_id, cov)
    con = sqlite3.connect(path)
    try:
        n = con.execute('SELECT COUNT(*) FROM carryover WHERE run_id=?', (run_id,)).fetchone()[0]
    finally:
        con.close()
    assert n == len(cov), 'a legitimate row was refused or collapsed'


def test_a_resume_may_still_replace_its_own_batch(db):
    """The behaviour the OR REPLACE was written for, and which the guard must not break: a
    resumed batch re-writes its own rows. The guard is per FLUSH, not per table."""
    path, run_id = db
    pd.save_carryover(path, run_id, [(4, 'unplaced', 7, 100)])
    pd.save_carryover(path, run_id, [(4, 'unplaced', 7, 250)])     # the batch ran again
    con = sqlite3.connect(path)
    try:
        rows = con.execute('SELECT qty FROM carryover WHERE run_id=? AND batch_id=4',
                           (run_id,)).fetchall()
    finally:
        con.close()
    assert rows == [(250,)], 'a resume can no longer replace its own batch'


# ── what the two families mean, so a reader sums the right things ─────────────────

def test_the_put_side_is_a_level_and_the_pick_side_is_a_flow(db):
    """Recorded as a test because the distinction is the whole reason the two must not
    share a key, and it is not visible in the schema: both are just `qty`.

    A put-away row is the standing queue RE-EMITTED every batch, so summing it across
    batches counts a waiting unit once per batch it waited. A pick row is that batch's
    miss and does sum. Anyone adding a `carryover` reader needs this.
    """
    path, run_id = db
    # one unit stands on the put queue for three batches; one batch misses 5 units of demand
    pd.save_carryover(path, run_id, [
        (0, 'unplaced', 1, 10), (1, 'unplaced', 1, 10), (2, 'unplaced', 1, 10),
        (1, 'unpicked_unstocked', 1, 5),
    ])
    con = sqlite3.connect(path)
    try:
        level = con.execute("SELECT SUM(qty) FROM carryover WHERE reason='unplaced'").fetchone()[0]
        flow = con.execute("SELECT SUM(qty) FROM carryover WHERE reason LIKE 'unpicked_%'"
                           ).fetchone()[0]
    finally:
        con.close()
    assert level == 30, 'the level summed to 30 for a backlog that was never more than 10'
    assert flow == 5, 'the flow sums correctly, because it is a flow'
