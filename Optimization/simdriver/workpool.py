"""simdriver.workpool — ONE pool for every cell of a run, and for the analysis stage.

WHY.  Until 2026-09-19 the driver opened one ProcessPoolExecutor PER CELL and waited for the
cell's last unit before the next cell's setup began.  Phase 2's `inbound_unload` spec has four
coupled units a cell on a twelve-worker pool, so eight workers idled for the life of every cell
and the matrix wall was the SUM of the cells' slowest units (~24 h) rather than the
worker-hours over the pool (~4 h, bounded below by the single slowest unit).  The pick side had
the same shape from the start, hidden by two cells of many short units; the analysis stage had
it again one level up (a fresh pool per cell with three stage barriers inside).  User decision
2026-09-19: ONE pool architecture for every run, with the cell carried on the unit.

WHAT.  A `WorkPool` owns one executor for the life of a run and takes jobs tagged with the CELL
that owns them.  It keeps its OWN pending queue and hands a job to the executor only while
fewer than `max_workers` are in flight, so dispatch order is the pool's (weight, then
submission order) and never the executor's FIFO.  Between cell setups the driver calls
`absorb()` (non-blocking) so finished units are booked as they land; `drain()` waits for the
rest.  `when_done(cell, keys, callback)` fires a parent-thread continuation once every named
job of that cell has RESOLVED (an errored job counts), which is how the analysis stage builds
a cell's aggregate jobs only after that cell's config jobs wrote their series docs -- without
waiting for any other cell.

RECOVERY, the old `_supervise` policy over all cells at once.  A hard worker death breaks the
one pool: it is unblocked (`_unblock_broken_pool`, the gh-107219 feeder hang) and shut down, a
fresh executor built, and every cell with jobs not done is asked to REBUILD its jobs through
the caller's `rebuild(cell)` (the driver re-derives from what it kept, with `mid_flight=True`
-- the coupled reconciler's contract) and resubmitted, up to `max_retries` times.  An ordinary
per-job exception is quarantined and never retried.  Jobs still not done at the end are
returned per cell, the shape `run_simulation._refuse_incomplete` reads.

WHAT THIS MODULE DOES NOT KNOW.  Nothing about units, leaves, groups, sim_meta or the run
tree: the driver passes `on_success` / `on_failure` callbacks and the worker callable rides on
each `Job`.  It imports neither `strategy_runner` nor `sim_config`
(`Tests/unit/test_config_reaches_the_worker.py`), and it names no contract path (the run-tree
ratchet in `Tests/architecture/test_runtree_consumption.py` counts them per file).

The Manager + QueueListener that carry worker log records live here for the whole run: one
listener, not one per cell, and the queue proxy survives a pool rebuild.
"""
from __future__ import annotations

import concurrent.futures
import heapq
import logging
import logging.handlers
import multiprocessing
import os
import subprocess
import sys
import time
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass


@dataclass
class Job:
    """One unit of work the pool dispatches: `fn(payload)` in a worker.

    `key` is hashable and unique WITHIN its cell (a work-unit uid, an analysis job name);
    `fn` is module-level and picklable (spawn); `payload` is the single argument; `weight`
    is the dispatch priority (higher first, ties by submission order -- 0 everywhere today,
    the seam for a duration estimate from a reference run)."""
    key: object
    fn: object
    payload: object
    weight: float = 0.0


def _tag_of(cell, key):
    """Standardized per-arm/per-group log tag: [cell/pair/config[/channel][/strategy]]."""
    return '/'.join(x for x in ((cell,) + tuple(key)) if x)


def _unblock_broken_pool(pool, log, *, timeout_s: float = 60.0) -> int:
    """Make the `with ProcessPoolExecutor` exit RETURN after a hard worker death, by reading
    the unit payloads the dead workers never took off the call queue.  Returns the count.

    THE HANG (2026-09-18, the phase-2 launch; `.scratch/phase-2-campaign/issues/01`): every
    child died at import, the driver logged BROKEN and abandoned the pool, and then sat at
    0.00 s CPU for 37 minutes.  Thread dump, taken on the reproduction: the `with` exit is
    `shutdown(wait=True)`, joining the executor's manager thread; that thread, in
    `terminate_broken -> join_executor_internals -> call_queue.join_thread()`, is joining the
    call queue's FEEDER thread; and the feeder is inside `PipeConnection._send_bytes`,
    waiting on an overlapped WriteFile that no live worker will ever read.  A Windows pipe
    buffer is 8 KiB; one 16 KiB unit argument with TWO workers is enough
    (`Tests/integration/_broken_pool_driver.py`), and the real payload -- the CONFIG snapshot
    and the shared paths, times twelve queued units -- is well past it.  With small arguments
    the writes fit the buffer and nothing hangs, which is why `test_crash_recovery.py` (which
    fakes the executor anyway) and every earlier fixture were blind to it.

    This is CPython gh-107219, fixed in 3.11.5 / 3.12 by closing the queue's connections from
    `Queue._terminate_broken`; the machine that hung runs 3.11.4.  That fix is NOT what this
    does, because it was measured not to work here: closing the WRITER from another thread
    leaves the overlapped write pending (probe, 2026-09-18: feeder still alive after 5 s), and
    closing the READER ends the pipe in isolation (`BrokenPipeError` at once) but not under
    the pool -- a child that dies at bootstrap never steals the handle duplicates
    `reduction.DupHandle` made for it in THIS process, so the parent still holds a live
    reader and the pipe is not ended.  What always works is the third thing the probe tried:
    READ the parent's end.  Each `recv_bytes` completes one blocked write, the feeder moves
    to the next item, the manager thread's `close()` appends the sentinel, the feeder exits,
    `join_thread` returns, `shutdown(wait=True)` returns, and the retry runs.  Bounded by
    `timeout_s` so this can never be the thing that hangs.  Private attributes, deliberately:
    there is no public surface for this, and the alternative was a driver that holds the
    machine."""
    cq = getattr(pool, '_call_queue', None)
    reader = getattr(cq, '_reader', None)
    feeder = getattr(cq, '_thread', None)          # None when nothing was ever submitted
    if cq is None or reader is None:
        log.warning('  [supervisor] this executor has no call-queue reader; cannot drain its '
                    f'feeder thread (Python {sys.version.split()[0]}) -- if the pool exit '
                    'hangs, that is why')
        return 0
    drained = 0
    deadline = time.monotonic() + timeout_s
    while feeder is not None and feeder.is_alive():
        if time.monotonic() > deadline:
            log.warning(f'  [supervisor] call-queue feeder still alive after {timeout_s:.0f} s '
                        f'and {drained} payload(s) drained -- the pool exit may hang')
            return drained
        if getattr(reader, 'closed', False):
            break
        try:
            if reader.poll(0.05):
                reader.recv_bytes()
                drained += 1
        except (EOFError, OSError, ValueError, TypeError):
            # THE NORMAL END: once the feeder has sent its last item the manager thread
            # closes the queue, and the reader we are polling goes with it -- sometimes
            # between our `poll` and our `recv_bytes`, which then sees a handle that is
            # already None (TypeError from ReadFile) or a connection that says it is
            # closed (ValueError).  Seen once in the gate on 2026-09-19; all four mean
            # the same thing here.
            break
    log.info(f'  [supervisor] drained {drained} queued unit payload(s) the dead workers never '
             'read; the call queue is closed and the pool can exit')
    return drained


def _explain_worker_death(log, worker_module: str, *, timeout_s: float = 300.0) -> str | None:
    """Say WHY the workers died, in the parent's log, the first time a pool breaks.

    A child that dies at import writes its traceback to a stderr nobody has under a
    scheduled task (memory `launch-long-drivers-detached`); the parent knows only "hard
    death".  So: one fresh interpreter, no multiprocessing, that does what a spawned child
    does first -- re-run the main module as `__mp_main__` (by name under `-m`, by path
    otherwise, mirroring `multiprocessing.spawn.get_preparation_data`), then import
    `worker_module` (the module of the job function that was in flight) -- with its stderr
    CAPTURED.  A failure prints the traceback's tail here, naming the exception a broken
    working tree raises; a success says the death is not an import failure (out of memory, a
    hard crash inside a worker, a kill).

    Returns 'import-failure', 'clean' or None (probe timed out), for a caller that wants the
    verdict; the log lines are the point."""
    main = sys.modules.get('__main__')
    spec = getattr(main, '__spec__', None)
    lines = ['import importlib, runpy, sys']
    if spec is not None and getattr(spec, 'name', None):
        lines.append(f'runpy.run_module({spec.name!r}, run_name="__mp_main__", alter_sys=True)')
    elif getattr(main, '__file__', None):
        lines.append(f'runpy.run_path({os.path.abspath(main.__file__)!r}, run_name="__mp_main__")')
    if worker_module:
        lines.append(f'importlib.import_module({worker_module!r})')
    lines.append('print("WORKER IMPORT OK")')
    try:
        r = subprocess.run([sys.executable, '-c', '\n'.join(lines)], capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=timeout_s,
                           cwd=os.getcwd(), env=os.environ.copy())
    except subprocess.TimeoutExpired:
        log.error(f'  [supervisor] import probe: no verdict within {timeout_s:.0f} s')
        return None
    if r.returncode == 0 and 'WORKER IMPORT OK' in r.stdout:
        log.error('  [supervisor] import probe: a fresh interpreter imports the main module and '
                  f'{worker_module} cleanly -- the death is NOT an import '
                  'failure (out of memory, a hard crash inside a worker, or a kill)')
        return 'clean'
    tail = (r.stderr or r.stdout).strip().splitlines()[-12:]
    log.error(f'  [supervisor] import probe: the worker FAILS to import in a fresh interpreter '
              f'(exit {r.returncode}) -- this is why every worker died:')
    for ln in tail:
        log.error('      ' + ln)
    return 'import-failure'


class InlineExecutor:
    """An executor that runs each submit at once, in this process -- the one-worker case of
    a driver (`workers <= 1`) and the executor tests hand a pool.  Same `submit` /
    `shutdown` surface as `ProcessPoolExecutor`, no processes, no threads."""

    def __init__(self, *a, **kw):
        pass

    def submit(self, fn, *args, **kwargs):
        fut = concurrent.futures.Future()
        try:
            fut.set_result(fn(*args, **kwargs))
        except BaseException as exc:                                   # noqa: BLE001
            fut.set_exception(exc)
        return fut

    def shutdown(self, wait=True, *, cancel_futures=False):
        pass


class _CellBook:
    """The pool's books for ONE cell: what was submitted, what resolved, and how."""
    __slots__ = ('jobs', 'done', 'failed', 'order')

    def __init__(self):
        self.jobs: dict = {}        # key -> Job, every job ever submitted for this cell
        self.done: set = set()
        self.failed: set = set()
        self.order: list = []       # submission order of keys, for the per-cell job index

    @property
    def resolved(self) -> set:
        return self.done | self.failed

    def remaining(self) -> list:
        """Keys not DONE -- a quarantined failure is not done and is reported (and, after a
        break, resubmitted) exactly as `_supervise` did."""
        return sorted(k for k in self.jobs if k not in self.done)


class WorkPool:
    """ONE executor across every cell; see the module docstring.

    `on_success(cell, key, payload, result)` and `on_failure(cell, key, payload, exc)` are the
    driver's bookkeeping.  A raise inside `on_success` is a FAILURE of that job (a malformed
    result is the driver's `strategy FAILED`, as before), never a raise out of the pool.
    `executor_factory(max_workers)` builds the executor; the sim driver passes the spawn pool
    with recycling pinned (`supervisor._sim_executor`), the analysis driver one without, and
    tests a thread pool.  `resume_hint` is the command the UNRECOVERED block names.
    `worker_logging` False skips the Manager and the QueueListener (`log_queue` stays None):
    the analysis workers log to their own stdout logger and never take the queue, and a
    Manager is a process nobody should start for nothing."""

    def __init__(self, max_workers, log, *, executor_factory, run_root=None, max_retries=2,
                 on_success=None, on_failure=None, resume_hint=None, worker_logging=True):
        self.max_workers = int(max_workers or 1)
        self.log = log
        self.run_root = run_root
        self.max_retries = max_retries
        self._factory = executor_factory
        self._on_success = on_success or (lambda cell, key, payload, res: None)
        self._on_failure = on_failure or (lambda cell, key, payload, exc: None)
        self._resume_hint = resume_hint
        self._worker_logging = bool(worker_logging)
        self.pool = None
        self.broke = False
        self.log_queue = None
        self._manager = None
        self._listener = None
        self._futures: dict = {}                # Future -> (cell, Job)
        self._pending: list = []                # heap of (-weight, seq, cell, Job)
        self._seq = 0
        self._cells: dict = {}                  # cell -> _CellBook
        self._continuations: list = []          # [(cell, frozenset(keys), callback)]
        self._explained = False
        self._broken_module = None
        self._n_done = 0

    # ── lifetime ────────────────────────────────────────────────────────────────────────
    def __enter__(self):
        if self._worker_logging:
            self._manager = multiprocessing.Manager()
            self.log_queue = self._manager.Queue(-1)
            self._listener = logging.handlers.QueueListener(
                self.log_queue, *self.log.handlers, respect_handler_level=True)
            self._listener.start()
            self.log.info('  Log listener started')
        self.pool = self._factory(self.max_workers)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.pool is not None:
                if exc_type is not None:
                    # A raise in the driver (a cell's setup, a continuation) must not wait
                    # hours for the units in flight: book what has landed, cancel what has
                    # not started, and let the exception out.  The units that were running
                    # keep their resume state and a `--resume` re-plans them.
                    self.absorb()
                    self.pool.shutdown(wait=False, cancel_futures=True)
                else:
                    self.pool.shutdown(wait=True)
        finally:
            if self._listener is not None:
                self._listener.stop()
                self._manager.shutdown()
                self.log.info('  Log listener stopped')
        return False

    # ── submission and dispatch ─────────────────────────────────────────────────────────
    def _book(self, cell) -> _CellBook:
        book = self._cells.get(cell)
        if book is None:
            book = self._cells[cell] = _CellBook()
        return book

    def submit(self, cell, jobs) -> int:
        """Queue one cell's jobs (any iterable of `Job`), dispatch as far as the pool allows,
        and return how many were queued.  A key already DONE for this cell is skipped -- a
        rebuild after a break hands back every job of the cell, finished ones included."""
        book = self._book(cell)
        n = 0
        for job in jobs:
            if job.key in book.done:
                continue
            if job.key not in book.jobs:
                book.order.append(job.key)
            book.jobs[job.key] = job
            self._seq += 1
            heapq.heappush(self._pending, (-float(job.weight), self._seq, cell, job))
            n += 1
        self._pump()
        self.log.info(f'  [pool] cell {cell}: {n} job(s) queued; {len(self._futures)} in flight, '
                      f'{len(self._pending)} pending across {len(self._cells)} cell(s)')
        return n

    def _pump(self) -> None:
        """Hand pending jobs to the executor while a worker slot is free."""
        while (self.pool is not None and not self.broke and self._pending
               and len(self._futures) < self.max_workers):
            _w, _s, cell, job = heapq.heappop(self._pending)
            fut = self.pool.submit(job.fn, job.payload)
            self._futures[fut] = (cell, job)

    def job_index(self, cell, key) -> tuple:
        """`(index, total)` of a key within its cell's submission order -- the per-cell
        progress the worker's log line shows."""
        book = self._book(cell)
        return book.order.index(key) + 1, len(book.order)

    # ── absorption ──────────────────────────────────────────────────────────────────────
    def _absorb_future(self, fut) -> None:
        cell, job = self._futures.pop(fut)
        book = self._cells[cell]
        try:
            res = fut.result()
        except BrokenProcessPool:
            self.broke = True
            self._broken_module = getattr(job.fn, '__module__', None)
            self.log.error('  [supervisor] worker pool BROKEN (hard worker death) -- abandoning '
                           'this pool; unfinished units will be rebuilt + resubmitted')
            return
        except Exception as exc:                        # noqa: BLE001 -- quarantined, reported
            book.failed.add(job.key)
            self._on_failure(cell, job.key, job.payload, exc)
            return
        try:
            self._on_success(cell, job.key, job.payload, res)
        except Exception as exc:                        # noqa: BLE001 -- a malformed result
            book.failed.add(job.key)
            self._on_failure(cell, job.key, job.payload, exc)
            return
        book.failed.discard(job.key)
        book.done.add(job.key)
        self._n_done += 1

    def _after_absorb(self) -> None:
        self._pump()
        self._fire_continuations()

    def absorb(self) -> bool:
        """Absorb every job that has completed WITHOUT waiting, refill the executor, fire
        any continuation that became ready.  Returns True while the pool is healthy."""
        if self._futures and not self.broke:
            done, _ = concurrent.futures.wait(list(self._futures), timeout=0)
            for fut in done:
                if self.broke:
                    break
                self._absorb_future(fut)
            if done:
                self.log.info(f'  [pool] {self._n_done} unit(s) done; {len(self._futures)} in '
                              f'flight, {len(self._pending)} pending')
        if not self.broke:
            self._after_absorb()
        return not self.broke

    def drain(self) -> bool:
        """Absorb until nothing is pending or in flight, or the pool breaks."""
        while not self.broke and (self._futures or self._pending):
            if not self._futures:
                self._pump()
                if not self._futures:
                    break                  # nothing could be dispatched (no executor)
                continue
            done, _ = concurrent.futures.wait(
                list(self._futures), return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                if self.broke:
                    break
                self._absorb_future(fut)
            if not self.broke:
                self._after_absorb()
        return not self.broke

    # ── continuations ───────────────────────────────────────────────────────────────────
    def when_done(self, cell, keys, callback) -> None:
        """Run `callback()` on the parent thread once every `(cell, key)` in `keys` has
        RESOLVED -- succeeded OR failed, because "the config stage is over" is true either
        way and the aggregate stage must not wait forever on one errored leaf.  Fires at
        most once, from inside `absorb`/`drain` (or immediately if already satisfied).  The
        callback may submit more jobs."""
        self._continuations.append((cell, frozenset(keys), callback))
        self._fire_continuations()

    def _fire_continuations(self) -> None:
        while True:
            ready = [c for c in self._continuations
                     if c[1] <= self._book(c[0]).resolved]
            if not ready:
                return
            for c in ready:
                self._continuations.remove(c)
                c[2]()
            self._pump()

    # ── settlement and recovery ─────────────────────────────────────────────────────────
    def settled_cells(self) -> list:
        """Cells whose EVERY submitted job has resolved and nothing is queued -- the
        driver drops a cell's shared assets on this (the assets a retry rebuilds from)."""
        queued = {cell for _w, _s, cell, _j in self._pending}
        flying = {cell for cell, _j in self._futures.values()}
        return [cell for cell, book in self._cells.items()
                if cell not in queued and cell not in flying
                and set(book.jobs) <= book.resolved]

    def remaining(self) -> dict:
        """{cell: sorted keys not done} for the cells that have any."""
        return {cell: book.remaining() for cell, book in self._cells.items()
                if book.remaining()}

    def _teardown_broken(self) -> None:
        pool, self.pool = self.pool, None
        if hasattr(pool, '_call_queue'):          # the real spawn pool: free its feeder first
            _unblock_broken_pool(pool, self.log)
        pool.shutdown(wait=True)
        self._futures.clear()
        self._pending.clear()

    def finish(self, rebuild) -> dict:
        """Drain; on a break, rebuild + resubmit through `rebuild(cell) -> [Job]` up to
        `max_retries` times; return the jobs still not done, per cell (empty when clean).

        The order is the old `_supervise`'s: drain, and only on a break tear down, explain
        the death ONCE, build a fresh executor, and ask every cell with jobs left for its
        jobs again -- the driver's `rebuild` is where `mid_flight=True` is passed to the
        work-unit builder, so a torn coupled pair seen during a live retry is reported and
        not repaired (`workunits._reconcile_coupled_unit`)."""
        for attempt in range(self.max_retries + 1):
            if attempt:
                left = self.remaining()
                self.log.warning(f'  [supervisor] retry {attempt}/{self.max_retries}: rebuild pool + '
                                 f'resubmit {sum(len(v) for v in left.values())} unit(s) across '
                                 f'{len(left)} cell(s)')
                self.broke = False
                self.pool = self._factory(self.max_workers)
                for cell in left:
                    self.submit(cell, list(rebuild(cell)))
            self.drain()
            if not self.broke:
                break
            self._teardown_broken()
            if not self._explained:               # once: the WHY (see _explain_worker_death)
                self._explained = True
                _explain_worker_death(self.log, getattr(self, '_broken_module', None) or '')
        unfinished = self.remaining()
        if unfinished:
            bar = '!' * 72
            self.log.error(bar)
            self.log.error(f'  {sum(len(v) for v in unfinished.values())} unit(s) UNRECOVERED across '
                           f'{len(unfinished)} cell(s) after {self.max_retries} '
                           f'retr{"y" if self.max_retries == 1 else "ies"}. Resume state left intact.')
            for cell, keys in unfinished.items():
                for key in keys:
                    self.log.error('    ' + _tag_of(cell, key if isinstance(key, tuple) else (str(key),)))
            if self._resume_hint:
                self.log.error(f'  Resume with:  {self._resume_hint}')
            self.log.error(bar)
        return unfinished
