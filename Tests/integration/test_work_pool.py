"""test_work_pool.py -- every cell of a run shares ONE pool (`simdriver.workpool.WorkPool`).

The pool replaced one-executor-per-cell on 2026-09-19: with four units a cell on a
twelve-worker pool, eight workers idled for the life of every cell and the matrix wall was the
SUM of the cells' slowest units.  A regression here is SILENT in the healthy direction -- a
driver that quietly went back to draining each cell before submitting the next would finish
every run, byte-identical, six times slower -- so the first test is a barrier only a flat pool
can pass:

  - CROSS-CELL CONCURRENCY: jobs of three cells meet at one barrier sized to the whole
    matrix.  A cell-serial pool waits for cell 1's jobs before cell 2's run, so its cell-1
    jobs time out at the barrier and the test FAILS (does not hang).  Its twin proves the
    barrier is not vacuous.
  - DISPATCH IS THE POOL'S: never more than `max_workers` in flight, weight first.
  - CONTINUATIONS: `when_done` fires once, on the parent thread, after exactly the named
    keys (an errored one counts), and one cell's continuation never waits for another cell.
  - RECOVERY: a broken pool rebuilds + resubmits ONLY the cells with jobs left, through the
    caller's `rebuild(cell)`; a job that finished before the break is never re-run; the
    worker-death explanation runs once; retries spent -> the unrecovered jobs are reported
    PER CELL, the shape `run_simulation._refuse_incomplete` reads.
  - QUARANTINE: an ordinary failure is reported, never retried, never rebuilt.
  - SETTLEMENT: `settled_cells` names a cell only once its every job has resolved, and
    `on_settle` ANNOUNCES it -- including from inside the drain, which is where a cell that
    is not the last submitted actually finishes and where the driver runs no code of its
    own. Polling from the setup loop meant the cells that settle late held their shared
    assets (an inventory apiece) for the whole run.
  - THE PROGRESS LINE carries the run PHASE, `unit N/M` (marked provisional while cells
    are still submitting), `cell N/M`, what is running and what failed.
  - A RAISE IN THE DRIVER does not wait for the jobs in flight: what landed is booked, what
    never started is cancelled, and the `with` exits at once.

The executor is a thread pool and the worker a fake, so the control flow is exercised
without a spawn pool; the real spawn pool's break/unblock path is
`test_supervisor_broken_pool.py`.
"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from concurrent.futures.process import BrokenProcessPool

import pytest

from Optimization.simdriver import workpool as wp

_LOG = logging.getLogger('test_work_pool')


def _threads(n):
    return lambda _n: concurrent.futures.ThreadPoolExecutor(max_workers=n)


def _ok(payload):
    return {'ok': payload}


def _jobs(cell, keys, fn=_ok, weight=0.0):
    return [wp.Job(key=k, fn=fn, payload={'cell': cell, 'key': k}, weight=weight) for k in keys]


class _Books:
    """Records the pool's callbacks the way a driver would."""

    def __init__(self):
        self.ok, self.bad = [], []

    def on_success(self, cell, key, payload, res):
        self.ok.append((cell, key))

    def on_failure(self, cell, key, payload, exc):
        self.bad.append((cell, key, type(exc).__name__))


def _pool(n, books=None, **kw):
    books = books or _Books()
    return wp.WorkPool(n, _LOG, executor_factory=_threads(n), on_success=books.on_success,
                       on_failure=books.on_failure, **kw), books


# ── a finished worker must not hold its slot while units wait ─────────────────────────

def test_a_finished_job_frees_its_slot_only_when_the_pool_is_pumped():
    """`_futures` shrinks ONLY in `absorb`, and `_pump`'s gate is
    `len(_futures) < max_workers` -- so a worker that has finished still occupies its slot
    until someone absorbs.  The driver absorbs once a cell, AFTER building that cell's
    units, so every unit that landed during a multi-minute cell setup left its worker idle
    with units queued.  Measured on the phase-2 campaign 2026-09-20: 18.1 minutes at
    `10 running, 12 queued`.

    `WorkPool.pump` is what a long parent-side step calls.  This pins both halves: the slot
    stays held while nobody pumps (or the fix would be untestable), and one pump refills it.
    """
    pool, books = _pool(2)
    gate = threading.Event()

    def _blocks(payload):
        gate.wait(timeout=20)
        return payload['key']

    with pool:
        # Two blockers fill the pool; a third job waits behind them.
        pool.submit('c1', _jobs('c1', ('a', 'b'), fn=_blocks))
        pool.submit('c1', _jobs('c1', ('c',)))
        assert len(pool._futures) == 2 and len(pool._pending) == 1, (
            f'expected the pool full with one waiting, got in_flight={len(pool._futures)} '
            f'queued={len(pool._pending)}')

        gate.set()                                   # both blockers finish
        for _ in range(200):                         # let the worker threads actually exit
            if all(f.done() for f in list(pool._futures)):
                break
            time.sleep(0.01)
        assert len(pool._futures) == 2 and len(pool._pending) == 1, (
            'the two jobs have FINISHED but their slots must still read as occupied until '
            'something absorbs -- if this fails the pool books completions by itself and '
            'the pump below is testing nothing')

        assert pool.pump() is True
        assert len(pool._pending) == 0, (
            f'after a pump the waiting job must be dispatched; queued={len(pool._pending)}. This '
            f'is the whole defect: a parent busy building the next cell left finished '
            f'workers idle with work queued')
        pool.drain()          # the driver's own tail; `__exit__` deliberately does not wait
    assert sorted(k for _c, k in books.ok) == ['a', 'b', 'c'], (
        'the pumped job must still finish and be booked like any other -- a pump that '
        'dispatched work the pool then lost would be worse than the idleness it fixes')


def test_the_setup_loop_hands_the_pool_its_own_pump():
    """The fix is only real if the DRIVER passes it: `_run_cells` builds each cell's units
    with `pump=pool.pump`, so the long parent-side steps can refill. A signature that
    drifted apart from the call site would leave the pump dead and nothing else would
    notice."""
    import inspect

    from Optimization.simdriver import scenario as _scn
    from Optimization.simdriver import workunits as _wu

    src = inspect.getsource(_scn._run_cells)
    assert 'pump=pool.pump' in src, (
        'the setup loop no longer hands the pool its own pump, so every unit that lands '
        'during a cell setup will sit on a held slot until the next cell is built')
    assert 'pump' in inspect.signature(_wu._build_work_units).parameters, (
        '_build_work_units stopped accepting a pump; the driver above is passing one into '
        'nothing')
    assert 'pump' in inspect.signature(_wu._prepare_site_run).parameters, (
        'the per-leaf prepare -- the longest uninterrupted stretch of a cell setup -- '
        'stopped accepting a pump')


# ── cross-cell concurrency: the property a cell-serial pool cannot have ────────────────

def test_jobs_of_different_cells_run_side_by_side():
    cells = ('c1', 'c2', 'c3')
    barrier = threading.Barrier(2 * len(cells), timeout=20)   # EVERY job of the matrix at once
    seen, lock = [], threading.Lock()

    def worker(payload):
        with lock:
            seen.append(payload['cell'])
        barrier.wait()          # BrokenBarrierError on timeout -> the job fails, the test fails
        return _ok(payload)

    pool, books = _pool(6)
    with pool:
        for cell in cells:
            pool.submit(cell, _jobs(cell, ('a', 'b'), fn=worker))
            pool.absorb()                        # what the driver does between cell setups
        unfinished = pool.finish(rebuild=lambda c: pytest.fail(f'no rebuild expected ({c})'))
    assert unfinished == {}, unfinished
    assert set(seen) == set(cells) and len(books.ok) == 6


def test_the_barrier_is_not_vacuous():
    """A pool of ONE worker cannot satisfy a barrier of two: the job times out, fails, and is
    reported unfinished -- so the concurrency test above passes only because the jobs of
    different cells were genuinely in flight together."""
    barrier = threading.Barrier(2, timeout=1)

    def worker(payload):
        barrier.wait()
        return _ok(payload)

    pool, books = _pool(1, max_retries=0)
    with pool:
        pool.submit('c1', _jobs('c1', ('a', 'b'), fn=worker))
        unfinished = pool.finish(rebuild=lambda c: pytest.fail('no rebuild expected'))
    assert unfinished == {'c1': ['a', 'b']}
    assert [b[2] for b in books.bad] == ['BrokenBarrierError'] * 2


# ── dispatch: bounded in flight, weight first ──────────────────────────────────────────

def test_never_more_than_max_workers_in_flight_and_weight_first():
    inside, peak, order, lock = [0], [0], [], threading.Lock()

    def worker(payload):
        with lock:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
            order.append(payload['key'])
        time.sleep(0.02)
        with lock:
            inside[0] -= 1
        return _ok(payload)

    # a FOUR-thread executor behind a pool that promises TWO in flight: the executor could
    # run four, so a peak of two proves the pool's own queue is what dispatches.
    pool, _books = _pool(2)
    pool._factory = _threads(4)
    with pool:
        light = _jobs('c1', ('l1', 'l2', 'l3', 'l4'), fn=worker, weight=0.0)
        heavy = _jobs('c1', ('h1', 'h2'), fn=worker, weight=10.0)
        pool.submit('c1', light + heavy)
        assert pool.finish(rebuild=lambda c: pytest.fail('no rebuild')) == {}
    assert peak[0] == 2, f'peak in flight {peak[0]}'
    assert set(order[:2]) == {'h1', 'h2'}, f'heavy jobs were not dispatched first: {order}'
    assert order[2:] == ['l1', 'l2', 'l3', 'l4'], f'ties must keep submission order: {order}'


# ── continuations ──────────────────────────────────────────────────────────────────────

def test_a_continuation_fires_once_after_its_keys_including_an_errored_one():
    def worker(payload):
        if payload['key'] == 'bad':
            raise RuntimeError('deterministic')
        return _ok(payload)

    fired, followups = [], []

    pool, books = _pool(2)
    with pool:
        pool.submit('c1', _jobs('c1', ('a', 'bad'), fn=worker))

        def then():
            fired.append(len(books.ok) + len(books.bad))
            pool.submit('c1', _jobs('c1', ('agg',), fn=lambda p: followups.append(p['key']) or _ok(p)))
        pool.when_done('c1', ('a', 'bad'), then)
        unfinished = pool.finish(rebuild=lambda c: pytest.fail('an ordinary failure never rebuilds'))
    assert fired == [2], f'the continuation must fire exactly once, after both keys: {fired}'
    assert followups == ['agg'], 'a job submitted by the continuation must run'
    assert unfinished == {'c1': ['bad']}


def test_one_cells_continuation_does_not_wait_for_another_cell():
    gate = threading.Event()

    def slow(payload):
        gate.wait(20)
        return _ok(payload)

    fired_at = []
    pool, books = _pool(4)
    with pool:
        pool.submit('slow', _jobs('slow', ('s1', 's2'), fn=slow))
        pool.submit('fast', _jobs('fast', ('f1', 'f2')))
        pool.when_done('fast', ('f1', 'f2'),
                       lambda: fired_at.append(('slow', sorted(k for c, k in books.ok if c == 'slow'))))
        deadline = time.monotonic() + 5
        while not fired_at and time.monotonic() < deadline:
            pool.absorb()
            time.sleep(0.01)
        assert fired_at == [('slow', [])], 'fast cell continuation waited on the slow cell'
        gate.set()
        assert pool.finish(rebuild=lambda c: pytest.fail('no rebuild')) == {}


# ── recovery: rebuild + resubmit only what is left, per cell ───────────────────────────

def test_a_broken_pool_rebuilds_only_the_cells_with_jobs_left(monkeypatch):
    generation = {'n': 0}
    calls: dict = {}
    lock = threading.Lock()

    def worker(payload):
        with lock:
            calls[(payload['cell'], payload['key'])] = calls.get((payload['cell'], payload['key']), 0) + 1
        if generation['n'] == 0 and not (payload['cell'] == 'c1' and payload['key'] == 'a'):
            raise BrokenProcessPool('hard worker death')     # what fut.result() raises
        return _ok(payload)

    explained = []
    monkeypatch.setattr(wp, '_explain_worker_death', lambda log, mod: explained.append(mod))
    rebuilt = []

    def rebuild(cell):
        generation['n'] = 1
        rebuilt.append(cell)
        return _jobs(cell, ('a', 'b'), fn=worker)     # EVERY job of the cell, finished ones too

    pool, books = _pool(2, max_retries=2)
    with pool:
        for cell in ('c1', 'c2'):
            pool.submit(cell, _jobs(cell, ('a', 'b'), fn=worker))
        unfinished = pool.finish(rebuild=rebuild)
    assert unfinished == {}, unfinished
    assert explained == [worker.__module__], 'the worker-death explanation must run exactly once'
    assert sorted(rebuilt) == ['c1', 'c2']
    assert calls[('c1', 'a')] == 1, 'a job that finished before the break was re-run'
    # The pool holds its own queue, so a job never dispatched before the break (two in
    # flight of four) runs once, in the new generation; one that broke runs again.
    assert calls[('c1', 'b')] >= 1 and calls[('c2', 'a')] >= 1 and calls[('c2', 'b')] >= 1
    assert sum(calls.values()) == 1 + 3 + sum(1 for k, n in calls.items() if k != ('c1', 'a') and n == 2)
    assert sorted(books.ok) == [('c1', 'a'), ('c1', 'b'), ('c2', 'a'), ('c2', 'b')]


def test_a_cell_with_nothing_left_is_not_rebuilt(monkeypatch):
    generation = {'n': 0}

    def worker(payload):
        if generation['n'] == 0 and payload['cell'] == 'c2':
            raise BrokenProcessPool('hard worker death')
        return _ok(payload)
    monkeypatch.setattr(wp, '_explain_worker_death', lambda log, mod: None)
    rebuilt = []

    def rebuild(cell):
        generation['n'] = 1
        rebuilt.append(cell)
        return _jobs(cell, ('a', 'b'), fn=worker)

    pool, _books = _pool(1)
    with pool:
        pool.submit('c1', _jobs('c1', ('a', 'b'), fn=worker))
        pool.drain()                                  # c1 finishes before c2 is reached
        pool.submit('c2', _jobs('c2', ('a', 'b'), fn=worker))
        unfinished = pool.finish(rebuild=rebuild)
    assert unfinished == {} and rebuilt == ['c2'], (unfinished, rebuilt)


def test_retries_spent_reports_the_unrecovered_jobs_per_cell(monkeypatch):
    def worker(payload):
        raise BrokenProcessPool('always')
    monkeypatch.setattr(wp, '_explain_worker_death', lambda log, mod: None)
    rebuilds = []

    def rebuild(cell):
        rebuilds.append(cell)
        return _jobs(cell, ('a', 'b'), fn=worker)

    pool, books = _pool(2, max_retries=1, resume_hint='resume me')
    with pool:
        for cell in ('c1', 'c2'):
            pool.submit(cell, _jobs(cell, ('a', 'b'), fn=worker))
        unfinished = pool.finish(rebuild=rebuild)
    assert unfinished == {'c1': ['a', 'b'], 'c2': ['a', 'b']}
    assert sorted(rebuilds) == ['c1', 'c2'], 'one retry -> each cell rebuilt once'
    assert books.ok == []


def test_an_ordinary_failure_is_quarantined_not_retried():
    def worker(payload):
        if payload['key'] == 'b':
            raise RuntimeError('deterministic bad config')
        return _ok(payload)

    pool, books = _pool(2)
    with pool:
        pool.submit('c1', _jobs('c1', ('a', 'b'), fn=worker))
        unfinished = pool.finish(rebuild=lambda c: pytest.fail('an ordinary failure never rebuilds'))
    assert unfinished == {'c1': ['b']}
    assert books.bad == [('c1', 'b', 'RuntimeError')] and books.ok == [('c1', 'a')]


def test_a_raise_inside_on_success_is_that_jobs_failure():
    """A malformed result is the driver's `strategy FAILED`, never a raise out of the pool."""
    class _Strict(_Books):
        def on_success(self, cell, key, payload, res):
            raise ValueError('leaf count mismatch')
    books = _Strict()
    pool, _ = _pool(1, books=books)
    with pool:
        pool.submit('c1', _jobs('c1', ('a',)))
        unfinished = pool.finish(rebuild=lambda c: pytest.fail('no rebuild'))
    assert unfinished == {'c1': ['a']} and books.bad == [('c1', 'a', 'ValueError')]


# ── settlement ─────────────────────────────────────────────────────────────────────────

def test_settled_cells_names_a_cell_only_once_every_job_resolved():
    gate = threading.Event()

    def slow(payload):
        gate.wait(20)
        return _ok(payload)

    pool, _books = _pool(4)
    with pool:
        pool.submit('slow', _jobs('slow', ('s1',), fn=slow))
        pool.submit('fast', _jobs('fast', ('f1', 'f2')))
        deadline = time.monotonic() + 5
        while 'fast' not in pool.settled_cells() and time.monotonic() < deadline:
            pool.absorb()
            time.sleep(0.01)
        assert pool.settled_cells() == ['fast']
        gate.set()
        assert pool.finish(rebuild=lambda c: pytest.fail('no rebuild')) == {}
        assert sorted(pool.settled_cells()) == ['fast', 'slow']


def test_on_settle_fires_from_inside_the_drain_not_only_from_the_setup_loop():
    """THE LEAK THIS HOOK CLOSES.  A cell that settles while `finish` is draining must be
    announced THEN, not at the end of the run.

    The driver drops a cell's shared assets on this hook -- an inventory, an affinity map,
    a batch script per pair. It used to poll `settled_cells()` inside its SETUP loop, so
    once the last cell was submitted nothing asked again: on a wide, shallow matrix that
    left almost every cell's assets alive for the whole drain, which is the longest stretch
    of the run and the one whose peak decides how wide a matrix can go.
    """
    gate = threading.Event()
    seen = []

    def slow(payload):
        gate.wait(20)
        return _ok(payload)

    pool, _books = _pool(4, on_settle=seen.append)
    with pool:
        pool.submit('early', _jobs('early', ('e1',)))
        pool.submit('late', _jobs('late', ('l1',), fn=slow))
        deadline = time.monotonic() + 5
        while 'early' not in seen and time.monotonic() < deadline:
            pool.absorb()
            time.sleep(0.01)
        assert seen == ['early'], seen

        # `late` settles INSIDE finish(), with the driver running no code of its own.
        gate.set()
        assert pool.finish(rebuild=lambda c: pytest.fail('no rebuild')) == {}
    assert seen == ['early', 'late'], seen


def test_on_settle_fires_once_per_cell():
    """The driver's handler pops a dict; a second announcement would log a release that did
    not happen and make the held-cell count a fiction."""
    seen = []
    pool, _books = _pool(4, on_settle=seen.append)
    with pool:
        pool.submit('c1', _jobs('c1', ('a', 'b')))
        assert pool.finish(rebuild=lambda c: pytest.fail('no rebuild')) == {}
        pool.absorb()
        pool.absorb()
    assert seen == ['c1'], seen


def test_a_cell_with_a_continuation_still_to_fire_is_not_settled():
    """A continuation may SUBMIT MORE JOBS for its own cell -- the analysis stage does.
    Announcing settlement before it fires would drop the very assets the resubmission
    needs, and the failure would land in the retry path, hours later."""
    seen = []
    pool, _books = _pool(4, on_settle=seen.append)
    with pool:
        pool.submit('c1', _jobs('c1', ('a',)))
        fired = []

        def more():
            fired.append(True)
            pool.submit('c1', _jobs('c1', ('b',)))

        pool.when_done('c1', ('a',), more)
        assert pool.finish(rebuild=lambda c: pytest.fail('no rebuild')) == {}
    assert fired == [True], 'the continuation never ran, so this proves nothing'
    assert seen == ['c1'], seen
    assert pool.job_index('c1', 'b') is not None, (
        'the job the continuation submitted never reached the pool, so settlement was not '
        'deferred past the continuation')


# ── the progress line a person actually reads ──────────────────────────────

def test_the_progress_line_carries_the_phase_the_unit_count_and_the_cell():
    """The four things someone watching a twelve-hour run is asking, in one line.

    It is only a log line, so a regression here is invisible to every other test and
    perfectly silent to the run itself -- which is exactly why it is pinned. The specific
    way it goes wrong is a part quietly disappearing: a phase that stops being set, a cell
    count that stays at zero, a denominator that never becomes final.
    """
    pool, _books = _pool(4)
    with pool:
        pool.set_phase('simulate', cells_expected=2)
        pool.submit('c1', _jobs('c1', ('a', 'b')), pos=(1, 2))
        line = pool.progress()
        assert 'simulate' in line
        assert 'unit 0/2+' in line, f'a provisional total must be marked: {line}'
        assert '%' not in line, (
            f'a percentage against a growing denominator goes DOWN as cells are added, '
            f'which reads as work being undone: {line}')
        assert 'cell 1/2' in line, line

        pool.submit('c2', _jobs('c2', ('c',)), pos=(2, 2))
        assert pool.finish(rebuild=lambda c: pytest.fail('no rebuild')) == {}
        done = pool.progress()
    assert 'unit 3/3 (100%)' in done, (
        f'with every cell submitted the total is final, the `+` is dropped and the '
        f'percentage appears: {done}')
    assert 'cell 2/2' in done, done
    assert 'FAILED' not in done, done


def test_the_progress_line_says_how_many_failed():
    """A count that is absent when it is zero and loud when it is not -- the run's exit
    status is decided by it, and a reader should not have to scroll for it."""
    def worker(payload):
        if payload['key'] == 'bad':
            raise RuntimeError('deterministic')
        return _ok(payload)

    pool, _books = _pool(2)
    with pool:
        pool.set_phase('simulate', cells_expected=1)
        pool.submit('c1', _jobs('c1', ('ok', 'bad'), fn=worker), pos=(1, 1))
        pool.finish(rebuild=lambda c: pytest.fail('an ordinary failure never rebuilds'))
        line = pool.progress()
    assert '1 FAILED' in line, line
    assert 'unit 1/2' in line, f'a failed unit is not a done one: {line}'


def test_a_pool_nobody_told_the_phase_still_prints_a_line():
    """Both drivers set it, and a harness that does not must not crash on the log line."""
    pool, _books = _pool(2)
    with pool:
        pool.submit('c1', _jobs('c1', ('a',)))
        assert pool.finish(rebuild=lambda c: pytest.fail('no rebuild')) == {}
        line = pool.progress()
    assert 'run' in line and 'unit 1/1' in line, line


# ── a raise in the driver ──────────────────────────────────────────────────────────────

def test_a_raise_in_the_driver_books_what_landed_and_does_not_wait():
    gate = threading.Event()

    def slow(payload):
        gate.wait(30)
        return _ok(payload)

    pool, books = _pool(2)
    t0 = time.monotonic()
    try:
        with pool:
            pool.submit('c1', _jobs('c1', ('fast',)))
            pool.drain()
            pool.submit('c2', _jobs('c2', ('slow1', 'slow2', 'slow3'), fn=slow))
            raise RuntimeError('cell 3 setup failed')
    except RuntimeError as exc:
        assert 'cell 3' in str(exc)
    finally:
        gate.set()
    assert time.monotonic() - t0 < 5, 'the exit waited for the jobs in flight'
    assert books.ok == [('c1', 'fast')], 'what landed before the raise was not booked'
