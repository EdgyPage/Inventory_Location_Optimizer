"""test_work_events_reconciliation.py — the merged timeline, checked against a real DB.

`work_events` is a SECOND record of instants that `picker_events` and `batch_stats` already
describe. The whole risk of a second table is that it drifts from the first and nobody
notices, so the reconciliation has to be a test, and it has to run SQL against a real
database — the in-memory checks in `Tests/unit/test_work_events.py` compare rows to the
events they were built from, which cannot catch a writer that drops, doubles or misorders
them on the way to disk.

This existed only as a throwaway script until an adversarial review pointed out that the
suite's own docstring called an in-memory equality "THE query". It is committed now, and it
carries the two invariants that review's findings turned into failures:

  * a put row never starts before its wave is released, and one worker is never in two
    places at once — the put crew's clock was cumulative across the arm while `put_rows`
    added the batch epoch on top, and then, once reset, made a single putter work two
    batches simultaneously;
  * the merged view is totally ordered — `seq` restarts each batch and `batch_id` was
    missing from the key, so at every batch boundary the next batch's first event sorted
    before the previous batch's last.

Run:  python -m pytest Tests/integration/test_work_events_reconciliation.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from Optimization.metrics.Simulation_Analytics import extract_batch_stats
from Optimization.metrics.work_events import pick_rows, put_rows
from Optimization.persistence.Picking_Data import (
    create_run, init_run_db, save_checkpoint_bundle,
)
from Optimization.metrics.Simulation_Analytics import extract_picker_events
from Warehouse.kernel.cost_model import SpeedProfile
from Warehouse.operations import Crew, Mode, Role
from Warehouse.picking.Pick import PickEvent

K = 2
PICKERS = Crew(Role.PICK, Mode.MACHINE, SpeedProfile(3.0, 2.0), size=K)
PUTTERS = Crew(Role.PUT, Mode.FOOT, SpeedProfile(2.0, 4.0), size=1)
PW = PICKERS.workers(0)
TW = PUTTERS.workers(PICKERS.next_uid(0))

# Per-batch (picker span, picker span) and the put durations that follow that batch.
# Batch 2's put-away is deliberately LONGER than its pick makespan, which is the case that
# forces the put crew to carry across the wave boundary.
BATCHES = [([12.0, 8.0], [3.0, 4.0]),
           ([6.0, 11.0], [40.0, 35.0]),
           ([9.0, 9.0], [2.0, 2.0])]


def _events(t0: float, spans: list[float]) -> list[PickEvent]:
    evs = []
    for pid, span in enumerate(spans):
        evs.append(PickEvent(time=t0, picker_id=pid, event_type='task_start',
                             aisle_id=pid + 1, total_bins=1))
        evs.append(PickEvent(time=t0 + span * 0.4, picker_id=pid, event_type='arrive',
                             aisle_id=pid + 1))
        evs.append(PickEvent(time=t0 + span * 0.8, picker_id=pid, event_type='pick',
                             aisle_id=pid + 1, sku=10 + pid, quantity=2, items_picked=2))
        evs.append(PickEvent(time=t0 + span, picker_id=pid, event_type='task_end',
                             aisle_id=pid + 1))
        evs.append(PickEvent(time=t0 + span, picker_id=pid, event_type='done',
                             items_picked=2))
    return evs


@pytest.fixture(scope='module')
def db(tmp_path_factory):
    """A real sim DB written the way the runner writes one: an arm clock, a carrying put
    crew, and everything flushed through save_checkpoint_bundle."""
    path = str(tmp_path_factory.mktemp('we') / 'sim_recon.db')
    init_run_db(path)
    run_id = create_run(path, 'test')

    arm_clock = 0.0
    put_clock = 0.0
    pb, pe, we = [], [], []

    for i, (spans, put_durs) in enumerate(BATCHES):
        evs = _events(arm_clock, spans)
        bs = extract_batch_stats(evs, batch_id=i, k_pickers=K, run_id=run_id)
        pb.append(bs)
        pe.extend(extract_picker_events(evs, batch_id=i, run_id=run_id))
        we.extend(pick_rows(evs, batch_id=i, batch_start=bs.batch_start_time, crew=PW))

        # Records are batch-relative from 0 (the drain restarts the manager's clock).
        recs, t = [], 0.0
        for j, d in enumerate(put_durs):
            recs.append((t, d, 50 + j, 4, 1, 10.0, 20.0, 'reorder', 0, 'all'))
            t += d
        base = max(bs.batch_start_time, put_clock)
        we.extend(put_rows(recs, batch_id=i, batch_start=bs.batch_start_time,
                           crew=TW, crew_start=base))
        put_clock = base + recs[-1][0] + recs[-1][1]

        arm_clock = bs.batch_start_time + bs.duration

    save_checkpoint_bundle(path, run_id, batch_stats=pb, task_stats=[], picker_events=pe,
                           picks=[], bin_placements=[], bin_evictions=[], aisle_metrics=[],
                           reorder_queue=[], work_events=we)
    return path


@pytest.fixture
def con(db):
    c = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    yield c
    c.close()


# ── the fixture is not vacuous ────────────────────────────────────────────────────

def test_the_database_actually_has_both_streams(con):
    """Every assertion below is trivially true over zero rows. This is the guard: the
    first version of this reconciliation passed over 68 databases containing NO rows,
    because the bundle took a `work_events` argument and never inserted it."""
    n_pick, n_put = con.execute(
        "SELECT SUM(role='pick'), SUM(role='put') FROM work_events").fetchone()
    assert n_pick == sum(len(_events(0, s)) for s, _ in BATCHES) > 0
    assert n_put == sum(len(p) for _, p in BATCHES) > 0
    assert con.execute('SELECT COUNT(*) FROM batch_stats').fetchone()[0] == len(BATCHES)


# ── the pick stream reconciles against the table that predates it ─────────────────

def test_one_work_event_per_picker_event(con):
    a = con.execute("SELECT COUNT(*) FROM work_events WHERE role='pick'").fetchone()[0]
    b = con.execute('SELECT COUNT(*) FROM picker_events').fetchone()[0]
    assert a == b


def test_every_pick_instant_equals_picker_events_time(con):
    """THE reconciliation, as SQL over a real file: the absolute axis and the table every
    published figure is built on describe the same instants."""
    a = [r[0] for r in con.execute(
        "SELECT t_abs FROM work_events WHERE role='pick' "
        'ORDER BY batch_id, actor_local, t_abs, seq')]
    b = [r[0] for r in con.execute(
        'SELECT time FROM picker_events ORDER BY batch_id, picker_id, time, id')]
    assert a == pytest.approx(b)


def test_t_local_is_t_abs_minus_the_batch_epoch(con):
    off = con.execute("""
        SELECT COUNT(*) FROM work_events w
        JOIN batch_stats b ON b.run_id = w.run_id AND b.batch_id = w.batch_id
        WHERE ABS((w.t_abs - b.batch_start_time) - w.t_local) > 1e-6
    """).fetchone()[0]
    assert off == 0


# ── the put stream: continuous, and never in two places ───────────────────────────

def test_no_put_starts_before_its_wave_is_released(con):
    assert con.execute(
        "SELECT COUNT(*) FROM work_events WHERE role='put' AND t_local < -1e-6"
    ).fetchone()[0] == 0


def test_one_worker_is_never_in_two_places_at_once(con):
    """The invariant that replaced "a put fits inside its batch". It does not have to: a
    single putter placing a wave's restock takes longer than the parallel pick crew takes
    to pick it, so its work legitimately overruns the next wave. What it may never do is
    overlap ITSELF."""
    prev_end, overlaps = {}, []
    for uid, t, dur in con.execute(
            'SELECT actor_uid, t_abs, duration FROM work_events '
            "WHERE role='put' ORDER BY actor_uid, t_abs"):
        end = prev_end.get(uid)
        if end is not None and t < end - 1e-6:
            overlaps.append((uid, t, end))
        prev_end[uid] = t + dur
    assert not overlaps, f'a putter overlaps itself: {overlaps}'


def test_the_put_crew_does_overrun_a_wave_here(con):
    """Non-vacuity for the test above: the fixture is built so batch 1's put-away is
    longer than its pick makespan, so the carry is actually exercised. Without this the
    overlap check could pass on data that never stressed it."""
    over = con.execute("""
        SELECT COUNT(*) FROM work_events w
        JOIN batch_stats b ON b.run_id = w.run_id AND b.batch_id = w.batch_id
        WHERE w.role='put' AND w.t_local > b.duration + 1e-6
    """).fetchone()[0]
    assert over > 0, 'the fixture no longer exercises a put crew running behind its wave'


# ── the merged view ───────────────────────────────────────────────────────────────

def test_the_merged_view_is_totally_ordered(con):
    rows = list(con.execute('SELECT t_abs, batch_id, role, mode, actor_uid, seq '
                            'FROM work_events_merged'))
    assert rows == sorted(rows)


def test_a_batch_boundary_is_ordered_by_emission(con):
    """The tie that occurs in every run: the arm advances to the last `done` instant, so
    batch i's `done` and batch i+1's `task_start` for one picker share a t_abs, a role, a
    mode and an actor. `seq` restarts each batch, so without `batch_id` in the key the
    next batch sorted first."""
    seen = [(r[0], r[1], r[2]) for r in con.execute(
        'SELECT t_abs, batch_id, event_type FROM work_events_merged '
        "WHERE role='pick'")]
    ties = {}
    for t, b, kind in seen:
        ties.setdefault(t, []).append((b, kind))
    boundaries = [v for v in ties.values()
                  if len({b for b, _ in v}) > 1]
    assert boundaries, 'no batch boundary tie in the fixture — the check is vacuous'
    for group in boundaries:
        batches = [b for b, _ in group]
        assert batches == sorted(batches), f'batch boundary ordered backwards: {group}'


def test_the_view_and_the_table_hold_the_same_rows(con):
    assert (con.execute('SELECT COUNT(*) FROM work_events_merged').fetchone()[0]
            == con.execute('SELECT COUNT(*) FROM work_events').fetchone()[0])


# ── the signed quantity, end to end ───────────────────────────────────────────────

def test_picks_are_negative_and_puts_positive_in_the_file(con):
    lo, hi = con.execute(
        "SELECT MAX(qty), MIN(qty) FROM work_events WHERE role='pick' AND qty IS NOT NULL"
    ).fetchone()
    assert lo < 0 and hi < 0
    assert con.execute(
        "SELECT MIN(qty) FROM work_events WHERE role='put'").fetchone()[0] > 0


def test_a_state_change_stores_NULL_not_zero(con):
    assert con.execute(
        "SELECT COUNT(*) FROM work_events WHERE event_type IN ('task_start','done') "
        'AND qty IS NOT NULL').fetchone()[0] == 0
