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
  - SETTLEMENT: `settled_cells` names a cell only once its every job has resolved.
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
